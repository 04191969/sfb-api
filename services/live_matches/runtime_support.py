"""Component-local queue and usage plumbing. Each release has its own copy."""
import json
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
COMPONENT = json.loads((ROOT/'release.json').read_text(encoding='utf-8'))['component']


def belongs(run):
    consumer = (not run.get('market_id') and (run.get('job_kind') or 'consumer_search')=='consumer_search'
                and bool((run.get('search_query') or '').strip()))
    return consumer if COMPONENT=='live_matches' else not consumer


def tracked_client(sb, run_id):
    class Query:
        def __init__(self, query, name):self.query, self.name = query, name
        def execute(self):
            result = self.query.execute()
            if self.name=='reserve_seeker_ai' and result.data:
                token=result.data
                if isinstance(token,list):token=token[0] if token else None
                if token:
                    sb.table('sfb_component_ai').insert({'reservation_id':str(token),'run_id':run_id,'component':COMPONENT}).execute()
            return result
    class Client:
        def __getattr__(self,name):return getattr(sb,name)
        def rpc(self,name,args):return Query(sb.rpc(name,args),name)
    return Client()
