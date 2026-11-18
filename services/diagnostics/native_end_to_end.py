"""Isolated live Seeker cycle. Local accounting/review only; no live DB client."""
import argparse,hashlib,json,os,sys,types,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]/'seeker'
sys.path.insert(0,str(ROOT))
# Database and model SDK construction is forbidden; the explicit transport below
# is the only API route. Database reservation callbacks are replaced by a local
# audit ledger for this test, never by calls into the production database.
def forbidden(*a,**k):raise RuntimeError('Production client forbidden in isolated test')
for name,attribute in [('supabase','create_client'),('openai','OpenAI')]:
 module=types.ModuleType(name);setattr(module,attribute,forbidden);sys.modules[name]=module
from native_reader import load
from native_discovery import cycle,QUERIES

def execute(out,key):
 out.mkdir(parents=True,exist_ok=False)
 report=dict(state='setup',searches=[],buyers=[],seller_searches=[],assessments=[],skipped=[],incomplete=[],calls=[],local_reviews=[],database_writes=0,introductions=0,production_changed=False,started_at=datetime.now(timezone.utc).isoformat())
 def save():
  tmp=out/'REPORT.tmp';tmp.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');tmp.replace(out/'REPORT.json')
 save()
 try:
  if not key:raise RuntimeError('API credential not available to this process; no calls made')
  manifest=json.loads((ROOT/'release.json').read_text(encoding='utf-8'))
  for name,digest in manifest['files'].items():
   if hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=digest:raise RuntimeError('Release integrity mismatch: '+name)
  report['release_id']=manifest['release_id']
  state=out/'state';state.mkdir();scope=load(state)
  def reserve(sb,allow):
   if len(report['calls'])>=17:raise RuntimeError('17-call test allowance reached')
   token=str(len(report['calls'])+1);report['calls'].append(dict(id=token,state='reserved'));save()
   return 'db',token  # Adapter protocol label; this test stores only local records.
  def settle(sb,token,status):
   report['calls'][int(token)-1]['state']=status;save();return True
  scope['_reserve_ai']=reserve;scope['_settle_ai']=settle
  class Client:
   def with_options(self,**kw):return types.SimpleNamespace(responses=types.SimpleNamespace(create=self.create))
   def create(self,**body):
    row=report['calls'][-1];row['request']=body;row['state']='dispatched';save()
    if body.get('model')!='gpt-5.4-2026-03-05' or body.get('max_output_tokens')!=6000:raise RuntimeError('Unexpected test model/allowance')
    request=urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
     with urllib.request.urlopen(request,timeout=300) as response:raw=json.load(response)
    except Exception as exc:
     row.update(state='uncertain',error=type(exc).__name__);save();raise
    row['response']=raw;save();return types.SimpleNamespace(model_dump=lambda **kw:raw)
  def search(query,max_results=10):
   rows=list(scope['DDGS'](timeout=15).text(query,max_results=max_results))
   report.setdefault('all_queries',[]).append(dict(query=query,results=rows));save();return rows
  scope['ddg_text']=search
  def publish(buyer,seller,result):
   review=dict(buyer=buyer,seller=seller,result=result)
   report['local_reviews'].append(review);save();return 'isolated-review-1',True
  report['state']='running';save()
  cycle(scope,Client(),{},report,save,publish,QUERIES)
  if report['state']=='running':report['state']='incomplete'
 except Exception as exc:report.update(state='incomplete',error=type(exc).__name__+': '+str(exc))
 finally:
  report['finished_at']=datetime.now(timezone.utc).isoformat();save()
  with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
   for p in out.rglob('*'):
    if p.is_file() and p.suffix!='.zip':z.write(p,p.relative_to(out))
 print('Result:',report['state']);print('Report:',out/'SEND_THIS_REPORT.zip')
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
 execute(Path(args.output),os.getenv('OPENAI_KEY') or os.getenv('OPENAI_API_KEY'))
