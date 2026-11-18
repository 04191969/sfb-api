"""Child execution for an unchanged, pinned five-step package. No polling."""
import json
import os
import sys
from pathlib import Path
from seeker_connector import load_runner, connect_run


def execute(control):
    cfg = json.loads(Path(control).read_text(encoding='utf-8'))
    manifest = json.loads(Path(__file__).with_name('five_step_package_hashes.json').read_text())
    factory = load_runner(cfg['package'], manifest, mode='ebay_api')
    import automatic_run
    import v2_bridge as locked
    automatic_run.locked = locked
    from public_search_adapter import PublicSearchBrowser
    from owner_api_bridge import OwnerApiBridge, observe_completion
    from railway_ebay_transport import relay_options
    from supabase import create_client
    sb = create_client('https://fyxglozykvhrfejrcrqz.supabase.co', os.environ['SUPABASE_KEY'])
    from runtime_support import tracked_client
    sb = tracked_client(sb, cfg['job']['id'])
    key = os.environ['OPENAI_KEY']
    # Subclass the execution boundary only. Locked payloads, queries, transports,
    # validation, local ledger and live provenance stay on the inherited path.
    from continuation import ContinuationMixin
    class ReservedRun(ContinuationMixin, OwnerApiBridge, factory):
        def __init__(self, *args, **kwargs):
            # Supply the browser before ApiRun wraps it for API seller details.
            super().__init__(*args, public_browser=PublicSearchBrowser, handoff=locked.handoff, **kwargs)
            self.r['ebay_api']['mode'] = 'live'
            self.r['ebay_api']['transport'] = 'Railway authenticated relay'
            self.persist()

        def model(self, kind, body, key, reservation):
            baseline = 0
            usage = Path(os.environ['SFB_COMPONENT_STATE']) / 'usage.json'
            if usage.exists():
                from datetime import datetime, timezone
                old = json.loads(usage.read_text())
                if old.get('date') == datetime.now(timezone.utc).date().isoformat():
                    baseline = int(old.get('calls', 0))
            response = sb.rpc('reserve_seeker_ai', {'cap': 2147483647, 'baseline': baseline}).execute().data
            if isinstance(response, list): response = response[0] if response else None
            if not response: raise RuntimeError('Database reservation refused')
            token = str(response)
            # Wrap this call's raw transport, so a received completion is counted
            # even if later schema/semantic validation rejects it.
            received = [False]
            try:
                # ApiRun installs its diagnostic transport inside model(). Observe
                # the actual Route.model dispatch after that installation.
                with observe_completion(self.route, received):
                    answer = super().model(kind, body, key, reservation)
            finally:
                if received[0]:
                    sb.rpc('settle_seeker_ai', {'reservation_id': token, 'outcome': 'succeeded'}).execute()
                # Unreceived responses remain open for uncertain-call recovery.
            if kind == 'matcher':
                pair = json.loads(json.loads(body)['input'])
                answer = locked.wrap(answer, pair)
            return answer
    result = {'run_id': cfg['job']['id'], 'envelope': None}
    def publish(run_id, envelope):
        result['envelope'] = envelope
    def finish(run_id, status, summary):
        result.update(status=status, summary=summary)
    try:
        connect_run(cfg['job'], cfg['output'], ReservedRun, key,
                    os.environ.get('SFB_SEEKER_RUN_ESTIMATE', '1.00'), publish, finish, locked, runner_options=relay_options(os.environ))
    finally:
        result.setdefault('status', 'failed')
        result.setdefault('summary', 'Search incomplete — five-step runtime stopped')
        target = Path(cfg['output']) / (cfg['job']['id'] + '.result.json')
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        tmp.replace(target)

if __name__ == '__main__':
    execute(sys.argv[1])
