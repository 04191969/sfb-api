"""Isolated bridge to an explicitly selected locked Seeker. No queue or search rewrite.

The existing host owns claiming, pause checks, heartbeat, and reservations.
Call connect_run only for a claimed broad public-discovery job. This module is
not installed in the running worker and performs no work on import.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


class UnsupportedRequest(ValueError):
    pass


def validate_job(job):
    UUID(str(job['id']))
    if job.get('status') != 'running':
        raise ValueError('Job must already be claimed by the host')
    if job.get('market_id') or str(job.get('search_query') or '').strip():
        raise UnsupportedRequest('Locked broad discovery does not accept a market or consumer query')
    if job.get('job_kind') not in (None, 'consumer_search', 'public_discovery'):
        raise UnsupportedRequest('Unsupported job kind')


def verify_package(root, manifest):
    """Require a separately approved complete file-hash manifest, not a guessed path."""
    root = Path(root).resolve()
    if not manifest or 'automatic_run.py' not in manifest:
        raise ValueError('Approved package manifest is required')
    for name, digest in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Invalid package file')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Approved package differs: ' + name)
    # Check all Python and prompt/schema/settings files; do not accept a partial lock.
    required = {p.relative_to(root).as_posix() for p in root.rglob('*')
                if p.is_file() and (p.suffix == '.py' or p.name in
                ('instruction.txt', 'schema.json', 'settings.json'))}
    if required - set(manifest):
        raise ValueError('Approved manifest does not cover the executable package')
    return root


def load_runner(root, manifest, mode='browser'):
    root = verify_package(root, manifest)
    if mode not in ('browser', 'ebay_api'):
        raise ValueError('Unknown source mode')
    # Use a dedicated connector process: never silently reuse another loaded Seeker.
    for name in ('automatic_run', 'ebay_api_run'):
        loaded = sys.modules.get(name)
        if loaded and Path(loaded.__file__).resolve().parent != root:
            raise ValueError('Another Seeker package is already loaded')
    sys.path.insert(0, str(root))
    if mode == 'ebay_api':
        from ebay_api_run import ApiRun
        return ApiRun
    from automatic_run import Run
    return Run


def validate_handoff(data, locked):
    pair = data['pair']
    facts = {}
    for side in ('buyer', 'seller'):
        source = pair[side]
        text = source['author_text']
        if not text or not source.get('url'):
            raise ValueError('Missing original source')
        if hashlib.sha256(text.encode()).hexdigest() != source['text_sha256']:
            raise ValueError('Source hash mismatch')
        facts[side + '_text'] = text
    validated = locked.validate_answer(data['matcher_answer'], facts)
    if validated != data['validated']:
        raise ValueError('Stored validation differs from locked validation')
    return data


def connect_run(job, output_root, factory, key, ceiling, publish, finish,
                locked, runner_options=None):
    """Publish only this run's validated handoff; finish every handled outcome.

    publish(run_id, envelope) must be an idempotent host storage adapter preserving
    the complete evidence. It must not invent confidence, availability, or prices.
    finish(run_id, status, summary) writes the host job outcome.
    Factory/options are injected so existing reservation/transport policy is kept.
    """
    validate_job(job)  # unsupported jobs stay with their existing handler
    run_id = str(UUID(str(job['id'])))
    folder = Path(output_root).resolve() / run_id
    if folder.exists():
        raise ValueError('Run directory already exists; reconcile instead of rerunning')
    try:
        runner = factory(folder, **(runner_options or {}))
        report = runner.execute(key=key, ceiling=ceiling)
        handoff = folder / 'HANDOFF_INPUT.json'
        if report.get('state') != 'completed':
            # A stale or partially written handoff never overrides a stopped run.
            summary = f"{report.get('state', 'stopped')}: {report.get('reason', 'No completed assessment')}"
            normal_outcomes = {'no_wtb_leads', 'no_readable_wtb',
                               'no_seller_selected', 'reader_hold'}
            status = 'completed' if report.get('state') in normal_outcomes else 'failed'
            finish(run_id, status, summary)
            return {'published': False, 'report': report}
        if not handoff.is_file():
            raise ValueError('Completed run has no handoff')
        data = validate_handoff(json.loads(handoff.read_text(encoding='utf-8')), locked)
        if report.get('pair') != data['pair'] or report.get('matcher_answer') != data['matcher_answer']:
            raise ValueError('Handoff does not match this run report')
        envelope = {'run_id': run_id, 'handoff': data, 'report': report,
                    'confidence': None, 'recorded_at': datetime.now(timezone.utc).isoformat()}
        publish(run_id, envelope)
        finish(run_id, 'completed', 'Assessment saved for review: ' + data['matcher_answer']['decision'])
        return {'published': True, 'report': report}
    except Exception as exc:
        # Do not persist arbitrary exception text which may contain credentials.
        finish(run_id, 'failed', 'Connector stopped: ' + type(exc).__name__)
        raise
