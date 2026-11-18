import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]/'diagnostics'))
import recent_buyer_proof as m
class Tests(unittest.TestCase):
 def test_date_boundaries(self):
  now=datetime(2026,11,18,tzinfo=timezone.utc)
  for days,label in [(0,'within_90_days'),(90,'within_90_days'),(91,'older_than_90_days'),(-1,'future_date_unconfirmed')]:
   self.assertEqual(m.recent({'posted_at':(now-timedelta(days=days)).isoformat()},now),label)
  for v in [None,'wrong','2026-11-18']:
   self.assertEqual(m.recent({'posted_at':v},now),'date_unconfirmed')
 def check(self,fail=False):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out'
   def discover(folder,state):
    folder.mkdir();return {'buyers':[{'posted_at':'2014-01-29T00:00:00Z'},{}]+[{'posted_at':datetime.now(timezone.utc).isoformat()} for _ in range(5)]}
   def read(home,src,output):
    output.mkdir();(output/'RESPONSE.json').write_text('{}');return {'state':'failed_or_uncertain' if fail else 'reader_handoff_validated','paid_calls_dispatched':1}
   with patch.object(m.discovery,'execute',side_effect=discover),patch.object(m,'read_one',side_effect=read) as reader:
    r=m.execute(Path(d),out)
    self.assertEqual(reader.call_count,1 if fail else 3)
    self.assertEqual(r['paid_calls_dispatched'],reader.call_count)
    self.assertTrue((out/'SEND_THIS_REPORT.zip').exists())
 def test_three_call_bound_and_old_post_exclusion(self):self.check()
 def test_failure_stops_no_retry(self):self.check(True)
if __name__=='__main__':unittest.main()
