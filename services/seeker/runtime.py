"""Independent worker entry point; no imports from the other component."""
import hashlib
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from runtime_support import belongs, tracked_client


def load_release():
    release=json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
    for name, expected in release['files'].items():
        path=ROOT/name
        if hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=expected:
            raise RuntimeError('Component release differs: '+name)
    return release


def main():
    release=load_release();component=release['component']
    # Process-scoped single-instance guard; automatically released after a crash.
    guard=socket.socket();guard.bind(('127.0.0.1',47601 if component=='live_matches' else 47602));guard.listen(1)
    state=Path(os.environ.get('SFB_COMPONENT_STATE',str(Path.home()/'SFB'/component/'state')))
    state.mkdir(parents=True,exist_ok=True);os.environ['SFB_COMPONENT_STATE']=str(state)
    os.environ['SFB_SEEKER_RESULTS']=str(state/'results')
    os.environ['SEEKER_AUTO_SEARCH']='0'
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    source=ROOT/'SFB_Seeker_v2_env.bat'
    scope={'__name__':'component_host','__file__':str(source)}
    exec(compile(source.read_text(encoding='utf-8').split('\n',1)[1],str(source),'exec'),scope)
    from supabase import create_client
    from openai import OpenAI
    url=os.environ.get('SUPABASE_URL','https://fyxglozykvhrfejrcrqz.supabase.co')
    key=os.environ['SUPABASE_KEY'];sb=create_client(url,key)
    worker=component+'-'+str(os.getpid())
    scope.update(SEEN_FILE=str(state/'seen.json'),USAGE_FILE=str(state/'usage.json'),AUTO_SEARCH=False,WORKER_ID=worker)
    scope['write_heartbeat']=lambda _sb: True  # dedicated thread owns this component's lease
    scope['code_version']=lambda: release['release_id']
    client=OpenAI(api_key=os.environ['OPENAI_KEY'],max_retries=0)
    stop=threading.Event()
    def heartbeat():
        heartbeat_sb=create_client(url,key)
        while not stop.is_set():
            try:
                now=datetime.now(timezone.utc).isoformat()
                heartbeat_sb.table('sfb_component_workers').upsert({'worker_id':worker,'component':component,
                    'seen_at':now,'code_version':release['release_id'],'v2_version':release['v2_version']}).execute()
                if component=='seeker':
                    heartbeat_sb.table('seeker_worker_heartbeat').upsert({'worker_id':worker,'seen_at':now,
                        'revision':'reservation-v2','code_version':release['release_id'],
                        'detail':'Independent Seeker; V2 '+release['v2_version']}).execute()
            except Exception as exc:print('Heartbeat failed: '+type(exc).__name__,flush=True)
            stop.wait(10)
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    if component=='live_matches':
        import live_matches_restore
        original=live_matches_restore.load_baseline
        def load():
            baseline=original()
            baseline.update(SEEN_FILE=str(state/'seen.json'),USAGE_FILE=str(state/'usage.json'))
            return baseline
        live_matches_restore.load_baseline=load
    else:
        import v2_bridge
        v2_bridge.install_market(scope)
    seen=scope['load_seen']()
    print(component+' ready; release '+release['release_id']+'; V2 '+release['v2_version'],flush=True)
    try:
        while not (state/'stop').exists():
            try:
                control=sb.table('sfb_component_control').select('enabled').eq('component',component).single().execute().data
                if control and control['enabled']:
                    sb.rpc('hold_stale_sfb_component_jobs',{'component_name':component}).execute()
                    if component=='seeker':scope['enqueue_due_markets'](sb)
                    run=sb.rpc('claim_sfb_component_job',{'component_name':component,'worker':worker,
                        'release_id':release['release_id'],'matcher_id':release['v2_version']}).execute().data
                    if isinstance(run,str):run=json.loads(run)
                    if run:
                        if not belongs(run):raise RuntimeError('Wrong component queue assignment')
                        tracked=tracked_client(sb,run['id'])
                        print('Claimed '+run['id'],flush=True)
                        try:
                            if component=='live_matches':live_matches_restore.run_claimed(tracked,client,seen,run)
                            else:
                                scope['claim_next_run']=lambda _sb:run
                                scope['check_manual_requests'](tracked,client,seen)
                        except Exception as exc:
                            sb.table('seeker_runs').update({'status':'failed','completed_at':datetime.now(timezone.utc).isoformat(),
                              'result_summary':'Search incomplete — '+component+' worker error: '+type(exc).__name__}).eq('id',run['id']).execute()
                            print('Run failed: '+type(exc).__name__,flush=True)
            except Exception as exc:print('Polling stopped this cycle: '+type(exc).__name__,flush=True)
            if (state/'stop').exists():break
            time.sleep(5)
    finally:
        stop.set();thread.join(timeout=15);guard.close()

if __name__=='__main__':main()
