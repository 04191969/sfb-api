import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
with patch.dict(sys.modules):
 spec=importlib.util.spec_from_file_location('native_e2e_probe',ROOT/'diagnostics/native_end_to_end.py')
 probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
class Tests(unittest.TestCase):
 def test_missing_credential_stops_before_retrieval_or_models(self):
  with tempfile.TemporaryDirectory() as t,patch.object(probe,'load',side_effect=AssertionError('must not load')):
   r=probe.execute(Path(t)/'out',None)
   self.assertEqual(r['calls'],[]);self.assertEqual(r['database_writes'],0)
   self.assertIn('API credential not available',r['error'])
 def test_local_call_allowance_stops_eighteenth_reservation(self):
  def cycle(scope,*args):
   for i in range(18):scope['_reserve_ai'](None,False)
  with tempfile.TemporaryDirectory() as t,patch.object(probe,'load',return_value={}),patch.object(probe,'cycle',side_effect=cycle):
   r=probe.execute(Path(t)/'out','unused-test-value')
   self.assertEqual(len(r['calls']),17)
   self.assertIn('17-call test allowance',r['error']);self.assertEqual(r['database_writes'],0)
 def test_review_saved_locally_only(self):
  def cycle(scope,client,seen,report,save,publish,queries):
   identity,created=publish({'text':'buyer'},{'text':'seller'},{'match':True})
   report['state']='completed'
   self.assertEqual(identity,'isolated-review-1')
  with tempfile.TemporaryDirectory() as t,patch.object(probe,'load',return_value={}),patch.object(probe,'cycle',side_effect=cycle):
   r=probe.execute(Path(t)/'out','unused-test-value')
   self.assertEqual(len(r['local_reviews']),1);self.assertFalse(r['production_changed'])
   self.assertEqual(r['database_writes'],0)
if __name__=='__main__':unittest.main()
