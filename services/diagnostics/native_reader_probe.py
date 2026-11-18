"""Read-only native Seeker retrieval probe; no model calls or database writes."""
import argparse,json,sys,tempfile,types
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'seeker'))
# This probe never constructs either client. Make absence explicit without
# requiring database/model SDK installation merely to test retrieval.
for name,attribute in [('supabase','create_client'),('openai','OpenAI')]:
    module=types.ModuleType(name)
    def forbidden(*a,**k):raise RuntimeError('Client access forbidden in retrieval-only probe')
    setattr(module,attribute,forbidden);sys.modules[name]=module
from native_reader import load

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    report=dict(started_at=datetime.now(timezone.utc).isoformat(),paid_model_calls=0,database_writes=0,production_changed=False,cases=[])
    def save(): (out/'REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    with tempfile.TemporaryDirectory() as state:
        scope=load(state)
        def search(query,max_results=10):
            rows=list(scope['DDGS'](timeout=15).text(query,max_results=max_results))
            case['searches'].append(dict(query=query,results=rows));save();return rows
        scope['ddg_text']=search
        for item in ['Titleist GTS 4 9* head','OG Ventus Velocore Blue 6s driver shaft']:
            case=dict(item=item,searches=[]);report['cases'].append(case);save()
            print('Testing native search and reader:',item,flush=True)
            try:case.update(state='retrieval_finished',result=scope['search_listings'](item,{},buyer_text='Item: '+item))
            except Exception as exc:case.update(state='incomplete',error=type(exc).__name__+': '+str(exc))
            save();print(case['state'],flush=True)
    report['finished_at']=datetime.now(timezone.utc).isoformat();save()
    print('Report:',out/'REPORT.json',flush=True)
if __name__=='__main__':main()
