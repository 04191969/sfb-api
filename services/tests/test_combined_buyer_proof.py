import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'diagnostics'))
from combined_buyer_proof import links,rotate,prepare
from buyer_discovery_steps12 import walk
class Tests(unittest.TestCase):
 def test_daum_custom_heading(self):
  html='<c-title><strong><a href="https://example.org/topic/1">WTB camera</a></strong></c-title><a href="https://example.org/profile/1">Author</a>'
  self.assertEqual([r['url'] for r in links(html,'Daum')],['https://example.org/topic/1'])
 def test_ocean(self):
  self.assertEqual(len(links('<a data-ph-result href="https://example.org/forum"><h3>WTB</h3></a>','OceanHero')),1)
 def test_rotation(self):
  s={'boards':['a','b','c']};self.assertEqual(rotate(s),['a','b']);self.assertEqual(rotate(s),['c','a'])
 def test_new_seeds_join_pending(self):
  s={'visited':['https://x.org/old'],'pending':[{'url':'https://x.org/left','kind':'post'}]}
  prepare(s,['https://x.org/new','https://x.org/old'],[])
  self.assertEqual([e['url'] for e in s['pending']],['https://x.org/left','https://x.org/new'])
 def test_board_revisit_reads_only_new_original(self):
  s={};board='https://x.org/forum';requests=[]
  pages={board:'<h1>Wanted to buy</h1><div class="structItemContainer"><div class="structItem-title"><a href="/one">WTB one</a></div></div>', 'https://x.org/one':'<h1>WTB one</h1><div class="message-body">Looking to buy one</div><div class="message-body">Reply not buyer text</div>'}
  def fetch(u):requests.append(u);return dict(status=200,url=u,html=pages[u])
  def report():return {'pages':[],'buyers':[],'failures':[]}
  prepare(s,[],[board]);r=report();walk([],fetch,s,r,lambda:None,max_pages=6)
  self.assertEqual(len(r['buyers']),1);self.assertNotIn('Reply',r['buyers'][0]['original_post'])
  pages[board]=pages[board].replace('</div></div>','</div><div class="structItem-title"><a href="/two">WTB two</a></div></div>')
  pages['https://x.org/two']='<h1>WTB two</h1><div class="message-body">Looking to buy two</div>'
  prepare(s,[],[board]);r=report();walk([],fetch,s,r,lambda:None,max_pages=6)
  self.assertEqual([b['url'] for b in r['buyers']],['https://x.org/two']);self.assertEqual(requests.count('https://x.org/one'),1)
if __name__=='__main__':unittest.main()
