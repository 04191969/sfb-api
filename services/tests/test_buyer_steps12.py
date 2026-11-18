import importlib.util,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'diagnostics/buyer_discovery_steps12.py'
s=importlib.util.spec_from_file_location('steps12',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Tests(unittest.TestCase):
 def directory(self):return '''<h1>Wanted to Buy Forum</h1><main><div class="structItemContainer"><div class="structItem-title"><a href="/threads/a.1/">Speakers</a></div><div class="structItem-title"><a href="/threads/b.2/">WTB camera</a></div><div class="structItem-title"><a href="/members/me/">WTB profile</a></div><div class="structItem-title"><a href="https://evil.example/threads/1">WTB elsewhere</a></div></div><a href="/threads/sidebar.9/">WTB unrelated sidebar</a><a rel="next" href="/forums/wtb/page-2">Next</a></main>'''
 def report(self):return dict(pages=[],buyers=[],failures=[])
 def test_title_links_only_and_same_host(self):
  r=m.directory_links(self.directory(),'https://example.com/forums/wtb/')
  self.assertEqual([x['url'] for x in r['posts']],['https://example.com/threads/a.1/','https://example.com/threads/b.2/'])
  self.assertEqual(r['next_pages'],['https://example.com/forums/wtb/page-2'])
 def test_first_post_not_reply_quote_or_signature(self):
  r=m.original_post('<h1>Speakers</h1><article><time datetime="2026-11-18"></time><div class="message-body">I want to buy speakers.<blockquote>Want a camera</blockquote><div class="signature">Selling a car</div></div></article><div class="message-body">Found one</div>','https://example.com/threads/1')
  self.assertEqual(r['text'],'Speakers\nI want to buy speakers.');self.assertEqual(r['posted_at'],'2026-11-18')
  self.assertEqual(r['availability'],'unconfirmed')
 def test_wanted_directory_context_retained_not_invented_as_author_text(self):
  r=m.original_post('<h1>Ping G440 4 hybrid</h1><div class="message-body">Stiff or head only please. Thanks.</div>','https://example.com/threads/1','Wanted to Buy Forum')
  self.assertEqual(r['status'],'role_needs_review');self.assertNotIn('Wanted to Buy Forum',r['text'])
 def test_closed_original_rejected(self):
  with self.assertRaises(ValueError):m.original_post('<h1>WTB camera</h1><div class="message-body">Found one</div>','https://example.com/1')
 def test_directory_does_not_become_buyer(self):
  with self.assertRaises(ValueError):m.original_post(self.directory(),'https://example.com/forums/wtb')
 def test_unknown_layout_not_replaced_with_snippet(self):
  with self.assertRaises(ValueError):m.original_post('<h1>WTB camera</h1><article>Looking for camera</article>','https://example.com/1')
 def test_blocked_page_does_not_stop_other_sources(self):
  report=self.report();state={}
  def fetch(u):return dict(status=202 if u.endswith('blocked') else 200,url=u,html='<h1>WTB camera</h1><div class="message-body">Looking for a camera.</div>')
  m.walk(['https://example.com/blocked','https://example.com/threads/2'],fetch,state,report,lambda:None)
  self.assertEqual(len(report['failures']),1);self.assertEqual(len(report['buyers']),1)
 def test_immediate_read_and_resume_preserve_queue(self):
  report=self.report();state={};calls=[]
  def fetch(u):
   calls.append(u)
   return dict(status=200,url=u,html=self.directory() if '/forums/' in u else '<h1>WTB speakers</h1><div class="message-body">Looking for speakers.</div>')
  m.walk(['https://example.com/forums/wtb/'],fetch,state,report,lambda:None,max_pages=2)
  self.assertEqual(len(report['buyers']),1);self.assertEqual(len(state['pending']),2)
  first=list(calls);second=self.report()
  m.walk([],fetch,state,second,lambda:None,max_pages=1)
  self.assertEqual(len(second['buyers']),1);self.assertEqual(len(calls),3);self.assertNotIn(calls[-1],first)
 def test_pagination_cannot_leave_directory(self):
  r=m.directory_links(self.directory().replace('/forums/wtb/page-2','/forums/sales/page-2'),'https://example.com/forums/wtb/')
  self.assertEqual(r['next_pages'],[])
if __name__=='__main__':unittest.main()
