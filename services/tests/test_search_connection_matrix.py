import importlib.util,json,tempfile,unittest,subprocess,types,sys
from pathlib import Path
from unittest.mock import patch
p=Path(__file__).resolve().parents[1]/'diagnostics/search_connection_matrix.py'
s=importlib.util.spec_from_file_location('matrix',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def test_matrix_has_50_distinct_slots(self):
  self.assertEqual(len([(r,e,q) for r in range(2) for e in m.ENGINES for q in m.QUERIES]),50)
 def test_timeout_is_explicit_and_keeps_trace(self):
  with tempfile.TemporaryDirectory() as f:
   root=Path(f);(root/'01-requests.jsonl').write_text(json.dumps({'host':'search.yahoo.com','state':'started'})+'\n')
   with patch.object(m.subprocess,'run',side_effect=subprocess.TimeoutExpired('probe',25)):
    r=m.attempt('yahoo','q',root,1)
   self.assertEqual(r['state'],'deadline');self.assertEqual(r['request_events'][0]['host'],'search.yahoo.com')
 def test_unavailable_engine_does_not_search_fallback(self):
  dd=types.ModuleType('ddgs');http=types.ModuleType('ddgs.http_client')
  class Client:
   def request(self,*a,**k):raise AssertionError('No network expected')
  http.HttpClient=Client
  dd.DDGS=lambda **k:types.SimpleNamespace(_get_engines=lambda *a:[types.SimpleNamespace(name='duckduckgo')],text=lambda *a,**k:self.fail('fallback called'))
  with patch.dict(sys.modules,{'ddgs':dd,'ddgs.http_client':http}):
   r=m.child('yahoo','q','unused')
  self.assertEqual(r['state'],'unsupported')
 def test_complete_runner_saves_all_slots_without_paid_calls(self):
  with tempfile.TemporaryDirectory() as f:
   def attempt(engine,query,*args):return dict(engine=engine,query=query,state='results',results=[{'title':'WTB example','href':'https://example.com/1'}],result_count=1,seconds=.01)
   with patch.object(m,'attempt',side_effect=attempt),patch.object(m.time,'sleep'),patch.object(m.importlib.metadata,'version',return_value='test'),patch('builtins.print'):
    r=m.run(Path(f)/'results')
   self.assertEqual(len(r['attempts']),50);self.assertEqual(r['state'],'finished');self.assertEqual(r['paid_model_calls'],0)
   self.assertTrue((Path(f)/'results/SEND_THIS_REPORT.zip').exists())
if __name__=='__main__':unittest.main()
