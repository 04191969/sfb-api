"""Child execution for an unchanged, pinned five-step package. No polling."""
import json
import os
import sys
from pathlib import Path
from seeker_connector import load_runner, connect_run


def execute(control):
    cfg = json.loads(Path(control).read_text(encoding='utf-8'))
    manifest = json.loads(Path(__file__).with_name('five_step_package_hashes.json').read_text())
    factory = load_runner(cfg['package'], manifest)
    from automatic_run import locked
    from public_search_adapter import PublicSearchBrowser
    from supabase import create_client
    sb = create_client('https://fyxglozykvhrfejrcrqz.supabase.co', os.environ['SUPABASE_KEY'])
    key = os.environ['OPENAI_KEY']
    # Subclass the execution boundary only. Locked payloads, queries, transports,
    # validation, local ledger and live provenance stay on the inherited path.
    class ReservedRun(factory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.browser_factory = lambda: PublicSearchBrowser(self.folder / 'browser', stop=self.cancelled)

        def model(self, kind, body, key, reservation):
            baseline = 0
            usage = Path.home() / 'sfb_ai_usage.json'
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
            original = self.route.model_transport
            received = False
            def observe(k, b):
                nonlocal received
                raw = original(k, b)
                received = True
                return raw
            self.route.model_transport = observe
            try:
                answer = super().model(kind, body, key, reservation)
            finally:
                self.route.model_transport = original
                if received:
                    sb.rpc('settle_seeker_ai', {'reservation_id': token, 'outcome': 'succeeded'}).execute()
                # A transport exception can have incurred a charge. Leave its
                # reservation open for the existing uncertain-call recovery.
            return answer
    result = {'run_id': cfg['job']['id'], 'envelope': None}
    def publish(run_id, envelope):
        result['envelope'] = envelope
    def finish(run_id, status, summary):
        result.update(status=status, summary=summary)
    try:
        connect_run(cfg['job'], cfg['output'], ReservedRun, key,
                    os.environ.get('SFB_SEEKER_RUN_ESTIMATE', '1.00'), publish, finish, locked)
    finally:
        result.setdefault('status', 'failed')
        result.setdefault('summary', 'Search incomplete — five-step runtime stopped')
        target = Path(cfg['output']) / (cfg['job']['id'] + '.result.json')
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        tmp.replace(target)

if __name__ == '__main__':
    execute(sys.argv[1])
