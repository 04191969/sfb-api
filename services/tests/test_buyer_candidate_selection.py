import sys,tempfile,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'diagnostics'))
from buyer_candidate_selection import selection,order,bootstrap
class Tests(unittest.TestCase):
 def test_meta_and_article(self):
  for u,t in [('https://x/forums/topic/bumping-a-wtb-post/','Bumping a WTB post?'),('https://x/article/want-to-buy-a-house.html','Want to buy a house')]:self.assertEqual(selection(dict(url=u,title=t))[0],'exclude')
 def test_cross_category_ads_retained(self):
  for title in ['WTB screwdriver','Wanted tractor','Looking to buy a bicycle','WTB radio']:
   self.assertEqual(selection(dict(url='https://x/threads/ad.123/',title=title))[0],'wanted_thread')
 def test_ambiguous_retained(self):self.assertEqual(selection(dict(url='https://x/threads/request.123/',title='Some advice needed'))[0],'thread')
 def test_rank_stable(self):
  s={'pending':[dict(url='https://x/forum'),dict(url='https://x/threads/1/',title='WTB camera'),dict(url='https://x/article/a')]};r={};order(s,r)
  self.assertEqual(len(s['pending']),2);self.assertIn('threads',s['pending'][0]['url']);self.assertEqual(len(r['selection_skips']),1)
 def test_restore_and_keep_existing(self):
  with tempfile.TemporaryDirectory() as d:
   h=Path(d);folder=h/'SFB_Recent_Buyer_Proof_1';folder.mkdir()
   (folder/'STATE.json').write_text(json.dumps({'boards':['board'],'broad_route':{'visited':['old'],'pending':[dict(url='old'),dict(url='new')]}}))
   (folder/'REPORT.json').write_text(json.dumps({'searches':[{'extracted_results':[dict(url='new',title='WTB radio')]}]}))
   p=h/'state/state.json';bootstrap(h,p);s=json.loads(p.read_text());self.assertEqual(s['broad_route']['pending'],[dict(url='new',title='WTB radio')]);self.assertEqual(s['broad_route']['visited'],['old'])
   p.write_text('{"existing":true}');bootstrap(h,p);self.assertEqual(json.loads(p.read_text()),{'existing':True})
if __name__=='__main__':unittest.main()
