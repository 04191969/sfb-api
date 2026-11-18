import sys,unittest,types
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'seeker'))
from native_discovery import public_search,cycle
class Tests(unittest.TestCase):
 def test_failure_then_success_retains_evidence(self):
  report={'incomplete':[]};calls=[]
  def search(query,**kw):
   calls.append(query)
   if query=='bad':raise ConnectionResetError(10054,'Remote host closed connection')
   return [{'href':'https://example.com/item/1','title':'WTB item','body':'original lead'}]
  scope={'DDGS':lambda **kw:types.SimpleNamespace(text=search)}
  before=public_search(scope,'good',10,report,lambda:None)
  self.assertEqual(public_search(scope,'bad',10,report,lambda:None),[])
  after=public_search(scope,'next',10,report,lambda:None)
  self.assertEqual(before,after);self.assertEqual(calls,['good','bad','next'])
  self.assertEqual([r['state'] for r in report['all_queries']],['completed','failed','completed'])
  self.assertIn('10054',report['all_queries'][1]['error'])
 def test_all_failed_queries_remain_incomplete_and_no_model_called(self):
  report=dict(state='running',searches=[],incomplete=[],skipped=[])
  def fail(*a,**k):raise ConnectionResetError(10054,'closed')
  scope={'DDGS':lambda **kw:types.SimpleNamespace(text=fail)}
  scope['ddg_text']=lambda q,max_results:public_search(scope,q,max_results,report,lambda:None)
  cycle(scope,None,{},report,lambda:None,lambda *a:self.fail('must not publish'),['one','two'])
  self.assertEqual(report['state'],'incomplete');self.assertEqual(len(report['all_queries']),2)
 def test_empty_success_is_not_network_failure(self):
  report={'incomplete':[]};scope={'DDGS':lambda **kw:types.SimpleNamespace(text=lambda *a,**k:[])}
  self.assertEqual(public_search(scope,'empty',10,report,lambda:None),[])
  self.assertEqual(report['incomplete'],[])
  self.assertEqual(report['all_queries'][0]['state'],'completed')
if __name__=='__main__':unittest.main()
