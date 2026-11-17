"""Five-step adapter. Invoked only for blank public discovery after host claim.
Locked files are loaded in a child process; existing consumer/market handlers remain.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


def config():
    folder = os.environ.get('SFB_LOCKED_SEEKER_DIR', '').strip()
    if not folder:
        folder = str(Path.home() / 'Documents' / 'test it' / 'Seeker_v0_9_1_QQ2_handoff_fix' / 'Seeker_locked_v0_9_1_plus_QQ2')
    return Path(folder).resolve()


def source_id(table, url):
    return str(uuid5(NAMESPACE_URL, table + ':' + url))


def persist_assessment(sb, run_id, envelope):
    """Evidence is stored intact; no invented product, price, or confidence.

    Locked discovery reports availability unconfirmed. A Send is preserved in
    evidence, but never becomes a verified-available introduction by importing it.
    """
    handoff = envelope['handoff']
    ids = {}
    for side, table, field in [('buyer', 'buyer_posts', 'post_text'),
                                ('seller', 'seller_listings', 'listing_text')]:
        source = handoff['pair'][side]
        url = source['url']
        existing = sb.table(table).select('id').eq('source_url', url).execute().data
        if existing:
            ids[side] = existing[0]['id']
            # Never replace original public wording with later observations.
            continue
        identifier = source_id(table, url)
        row = {'id': identifier, 'username': urlsplit(url).hostname or 'Public source',
               field: source['author_text'], 'source_url': url,
               'source_platform': urlsplit(url).hostname or 'web',
               'scraped_at': source['captured_at'], 'discovered_at': source['captured_at'],
               'observed_at': source['captured_at'], 'last_checked_at': source['captured_at']}
        if side == 'seller':
            row.update(availability='unconfirmed', price=None, shipping=None)
        sb.table(table).upsert(row, on_conflict='id', ignore_duplicates=True).execute()
        ids[side] = identifier
    existing = (sb.table('matches').select('id,status').eq('buyer_post_id', ids['buyer'])
                .eq('seller_listing_id', ids['seller']).execute().data)
    match_id = existing[0]['id'] if existing else str(uuid5(NAMESPACE_URL, ids['buyer'] + ':' + ids['seller']))
    reason = ('Availability unconfirmed. Five-step assessment requires administrator review.\n'
              'Locked matcher decision: ' + handoff['matcher_answer']['decision'] + '\n'
              + json.dumps(envelope, ensure_ascii=False))
    if not existing:
        sb.table('matches').upsert({'id': match_id, 'buyer_post_id': ids['buyer'],
            'seller_listing_id': ids['seller'], 'ai_reasoning': reason, 'confidence': None,
            'match_decision': 'no_send', 'status': 'pending_review'},
            on_conflict='id', ignore_duplicates=True).execute()
    elif existing[0]['status'] == 'pending_review':
        # Existing decisions are not silently replaced by an unconfirmed import.
        # The complete new observation remains attached to its run on disk.
        pass
    sb.table('seeker_runs').update({'match_id': match_id, 'new_matches': 0}).eq('id', run_id).execute()
    return match_id


def run_claimed(sb, heartbeat, job):
    """Keep the host's heartbeat alive and finish failures without changing searches."""
    run_id = str(UUID(str(job['id'])))
    base = Path(os.environ.get('SFB_SEEKER_RESULTS', str(Path.home() / 'SFB_Seeker_Results'))).resolve()
    base.mkdir(parents=True, exist_ok=True)
    control = base / (run_id + '.job.json')
    result = base / (run_id + '.result.json')
    proc = None
    try:
        package = config()
        if control.exists() or result.exists() or (base / run_id).exists():
            raise ValueError('Existing run artifacts require reconciliation; not rerunning')
        control.write_text(json.dumps({'job': job, 'package': str(package), 'output': str(base)}), encoding='utf-8')
        with (base / (run_id + '.log')).open('w', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'five_step_runtime.py'), str(control)],
                                    stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 1200
            while proc.poll() is None:
                heartbeat(sb)
                if time.monotonic() > deadline:
                    proc.kill(); proc.wait()
                    raise TimeoutError('Five-step process deadline reached')
                time.sleep(5)
        if proc.returncode != 0 or not result.is_file():
            raise RuntimeError('Five-step runtime failed; inspect run log')
        payload = json.loads(result.read_text(encoding='utf-8'))
        if payload['run_id'] != run_id:
            raise ValueError('Result run ID mismatch')
        if payload.get('envelope'):
            persist_assessment(sb, run_id, payload['envelope'])
        sb.table('seeker_runs').update({'status': payload['status'], 'completed_at': now(),
            'result_summary': payload['summary'], 'error_message': payload.get('error')}).eq('id', run_id).execute()
    except BaseException as exc:
        if proc is not None and proc.poll() is None:
            proc.kill(); proc.wait()
        sb.table('seeker_runs').update({'status': 'failed', 'completed_at': now(),
            'result_summary': 'Search incomplete — five-step connector stopped',
            'error_message': type(exc).__name__ + ': ' + (str(exc) if isinstance(exc, (ValueError, TimeoutError)) else 'Inspect local run log')}).eq('id', run_id).execute()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
