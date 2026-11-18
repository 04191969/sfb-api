import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'diagnostics'))
from buyer_discovery_steps12 import original_post,walk
from combined_buyer_proof import prioritize_direct
YABB='<table><tr><td><a name="0"></a></td><td><div class="dividerbot"><b>WTB cables</b><span>03/26/22 at 17:13:33</span></div><div class="message">Need cables, ten feet.<blockquote>Quoted stranger</blockquote></div><div class="signature">Signature</div></td></tr><tr><td><a name="1"></a></td><td><div class="message">A reply</div></td></tr></table>'
class Tests(unittest.TestCase):
 def test_yabb_original_only(self):
  r=original_post(YABB,'https://example.org/cgi-bin/yabb22/YaBB.pl?num=123/1')
  self.assertEqual(r['original_post'],'Need cables, ten feet.')
  self.assertEqual(r['posted_at_raw'],'03/26/22 at 17:13:33')
  self.assertEqual(r['availability'],'unconfirmed')
 def test_reply_only_rejected(self):
  with self.assertRaisesRegex(ValueError,'opening post'):original_post(YABB.replace('name="0"','name="8"'),'https://example.org/yabb/YaBB.pl')
 def test_navigation_priority_not_qualification(self):
  state={'pending':[{'url':'https://x/forum/12/'},{'url':'https://x/threads/wtb-camera.22/'},{'url':'https://x/forums/'}]};prioritize_direct(state)
  self.assertIn('/threads/',state['pending'][0]['url']);self.assertEqual(len(state['pending']),3)
 def test_every_attempt_counted(self):
  def fetch(u):
   if u.endswith('timeout'):raise TimeoutError('test timeout')
   if u.endswith('blocked'):return dict(status=403,html='blocked')
   return dict(status=200,html='<h1>Unsupported</h1>')
  r={'pages':[],'buyers':[],'failures':[]};walk(['https://x/timeout','https://x/blocked','https://x/unsupported'],fetch,{},r,lambda:None)
  self.assertEqual(r['opened_this_run'],3);self.assertEqual(len(r['pages']),3)
  self.assertEqual([e['outcome'] for e in r['pages']],['timed_out','blocked','read_but_unsupported'])
if __name__=='__main__':unittest.main()
