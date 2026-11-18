import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'diagnostics'))
import browser_buyer_proof as m
class Tests(unittest.TestCase):
 def test_result_headings_not_navigation(self):
  rows=m.result_links('<a href="https://ads.example">WTB ad</a><h2><a href="https://forum.example/wtb">WTB forum</a></h2><h2><a href="https://forum.example/wtb">duplicate</a></h2>','https://www.qwant.com/')
  self.assertEqual(rows,[{'url':'https://forum.example/wtb','title':'WTB forum'}])
 def test_profile_and_related_not_accepted_as_original(self):
  from buyer_discovery_steps12 import original_post
  r=original_post('<h1>WTB speakers</h1><div class="message-body">Looking for speakers</div><div class="message-body">Reply wants camera</div><aside>Selling car</aside>','https://forum.example/thread')
  self.assertEqual(r['text'],'WTB speakers\nLooking for speakers')
 def test_query_has_no_product(self):
  self.assertEqual(m.QUERIES,['"WTB" forum','"want to buy" classifieds','"looking to buy" forum'])
 def test_failure_kept_in_report_without_model_or_db(self):
  import tempfile
  with tempfile.TemporaryDirectory() as d:
   r=m.run(Path(d)/'out',channel='nonexistent-test-channel')
   self.assertEqual(r['state'],'incomplete');self.assertEqual(r['model_calls'],0)
   self.assertTrue((Path(d)/'out'/'SEND_THIS_REPORT.zip').exists())
if __name__=='__main__':unittest.main()
