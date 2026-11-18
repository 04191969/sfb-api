"""Offline adapter wiring test. No provider, browser, or database calls."""
import hashlib,json,os,sys,tempfile,types
from pathlib import Path
from unittest.mock import MagicMock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'seeker'))
sys.path.insert(1,str(ROOT.parent/'worker'))
import test_live_matches_v2 as fixtures
import seeker_connector
import v2_bridge

class Factory:
 def __init__(self,folder,**kwargs):
  self.folder=Path(folder);self.folder.mkdir(parents=True)
  self.handoff=kwargs['handoff'];self.r={'ebay_api':{}}
  self.buyer_screen=MagicMock()
  self.route=types.SimpleNamespace(model_transport=lambda *a: fixtures.reading())
  self.route.model=lambda *a:self.route.model_transport(*a)
 def persist(self):pass
 def model(self,kind,body,key,reservation):return self.route.model(kind,body)
 def execute(self,key,ceiling):
  pair={'buyer_text':fixtures.BUYER,'seller_text':fixtures.PAGE}
  body=json.dumps(v2_bridge.payload(pair)).encode()
  answer=self.model('matcher',body,key,'offline')
  valid=v2_bridge.validate_answer(answer,pair)
  sources={side:{'url':'https://example.com/'+side,'author_text':pair[side+'_text'],
    'text_sha256':hashlib.sha256(pair[side+'_text'].encode()).hexdigest(),'captured_at':'2026-11-17T00:00:00Z'} for side in ('buyer','seller')}
  handoff={'pair':sources,'matcher_answer':answer,'validated':valid}
  path=self.folder/'HANDOFF_INPUT.json';path.write_text(json.dumps(handoff))
  self.handoff(path,self.folder/'admin_review')
  return {'state':'completed','pair':sources,'matcher_answer':answer}

seeker_connector.load_runner=lambda *a,**kw:Factory
sys.modules['automatic_run']=types.ModuleType('automatic_run')
continuation=types.ModuleType('continuation');continuation.ContinuationMixin=type('ContinuationMixin',(),{});sys.modules['continuation']=continuation
broad=types.ModuleType('broad_discovery');broad.BroadDiscoveryMixin=type('BroadDiscoveryMixin',(),{});sys.modules['broad_discovery']=broad
public=types.ModuleType('public_search_adapter');public.PublicSearchBrowser=object;sys.modules['public_search_adapter']=public
relay=types.ModuleType('railway_ebay_transport');relay.relay_options=lambda e:{};sys.modules['railway_ebay_transport']=relay
sb=MagicMock()
def rpc(name,args):return types.SimpleNamespace(execute=lambda:types.SimpleNamespace(data='00000000-0000-0000-0000-000000000001' if name=='reserve_seeker_ai' else True))
sb.rpc.side_effect=rpc
supabase=types.ModuleType('supabase');supabase.create_client=lambda *a:sb;sys.modules['supabase']=supabase
os.environ.update(SUPABASE_KEY='offline',OPENAI_KEY='offline')
import five_step_runtime
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);os.environ['SFB_COMPONENT_STATE']=tmp
 run_id='00000000-0000-0000-0000-000000000002'
 job={'id':run_id,'status':'running','job_kind':'consumer_search','search_query':None}
 control=root/'job.json';control.write_text(json.dumps({'job':job,'package':'offline','output':tmp}))
 five_step_runtime.execute(str(control))
 result=json.loads((root/(run_id+'.result.json')).read_text())
 assert result['status']=='completed',result
 assert result['envelope']['handoff']['matcher_answer']['decision']=='Send'
 assert sb.rpc.call_count==2
 assert sb.table.call_args.args==('sfb_component_ai',)
 assert 'reading' in result['envelope']['handoff']['matcher_answer']
 print('Seeker-owned V2 five-step handoff and usage attribution passed (offline).')
