"""Native Seeker routing and source isolation; no network/model/database calls."""
import hashlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'seeker'))
import native_discovery as n
import native_reader

class Tests(unittest.TestCase):
 def test_original_post_not_reply_or_snippet(self):
  html='<h1>WTB Example Q2 guitar</h1><div class="message-body">Need a blue guitar.<blockquote>Someone wants a red one</blockquote></div><div class="message-body">Reply says black</div>'
  buyer=n.buyer_page(html,'https://forum.example/threads/10000')
  self.assertEqual(buyer['text'],'WTB Example Q2 guitar\nNeed a blue guitar.')
  self.assertEqual(buyer['query'],'Example Q2 guitar')
 def test_index_and_closed_do_not_become_buyer(self):
  for html in ['<h1>WTB guitars</h1><nav>Posts</nav>','<h1>WTB guitar</h1><div class="message-body">Found one, no longer needed.</div>']:
   with self.assertRaises(ValueError):n.buyer_page(html,'https://forum.example/threads/10000')
 def test_native_dispatch_replaces_five_step(self):
  text=(ROOT/'seeker/SFB_Seeker_v2_env.bat').read_text()
  self.assertNotIn('import five_step_host',text)
  self.assertIn('native_discovery.run_claimed(sb, openai_client, seen, run)',text)
 def test_reader_copies_are_independent_and_provenance_preserved(self):
  for original,owned in [('live_matches_f972a58.bat','reader_baseline.bat'),('live_matches_discovery.py','reader_discovery.py')]:
   a=ROOT/'live_matches'/original;b=ROOT/'seeker'/owned
   self.assertEqual(a.read_bytes(),b.read_bytes())
   self.assertNotEqual(a.stat().st_ino,b.stat().st_ino)
  text=(ROOT/'seeker/reader_currency.py').read_text()
  self.assertNotIn('from live_matches',text)
  self.assertIn('from reader_discovery',text)
 def test_cycle_buyer_seller_v2_persistence_and_state_restore(self):
  original=lambda text:text
  calls=[]
  buyerurl='https://forum.example/threads/10000-wtb-guitar'
  seller=dict(url='https://seller.example/item/1',title='Example guitar',text='seller excerpt',page_text='full seller page')
  scope={'ddg_text':lambda *a,**k:[dict(href=buyerurl,title='WTB Example guitar',body='not source')],
   'is_seen':lambda *a:False,'read_listing_html':lambda url:'<h1>WTB Example guitar</h1><div class="message-body">Must be blue.</div>',
   'requested_item':original,'search_listings':lambda *a,**k:dict(listings=[seller],incomplete=''),
   'qualifies':lambda *a:True,'ai_evaluate_match':lambda b,s,c,sb,allow:calls.append((b,s,allow)) or dict(match=True),
   'mark_seen':lambda *a:None}
  report=dict(searches=[],buyers=[],seller_searches=[],assessments=[],skipped=[],incomplete=[],state='running')
  n.cycle(scope,None,{},report,lambda:None,lambda *a:('match-1',True),['"WTB" forum'])
  self.assertEqual(report['state'],'completed');self.assertEqual(report['match_id'],'match-1')
  self.assertEqual(calls,[('WTB Example guitar\nMust be blue.','seller excerpt',False)])
  self.assertIs(scope['requested_item'],original)
 def test_owned_v2_receives_opened_page_not_search_excerpt(self):
  from seeker_v2 import adapter
  calls=[]
  page='Original seller page with product details and price.'
  scope={'search_listings':lambda *a,**k:dict(listings=[dict(text='snippet',page_text=page)]),
   'search_outcome':lambda *a:'done','_reserve_ai':lambda *a:('db','token'),
   '_settle_ai':lambda *a:True,'requested_item':lambda text:'Example guitar','ReservationClosed':RuntimeError}
  transport=types.SimpleNamespace(responses=types.SimpleNamespace(create=lambda **body:calls.append(body) or types.SimpleNamespace(model_dump=lambda **kw:{'status':'completed'})))
  client=types.SimpleNamespace(with_options=lambda **kw:transport)
  adapter.install(scope);scope['search_listings']('guitar',{})
  qualification=dict(decision='No Send',reason='wrong product',disclosures=[],product='conflict')
  with patch.object(adapter,'assess',return_value=({'product':{'explanation':'wrong product'}},qualification)):
   result=scope['ai_evaluate_match']('Original buyer text','snippet',client,object(),False)
  self.assertEqual(json.loads(calls[0]['input']),{'buyer_text':'Original buyer text','seller_text':page})
  self.assertEqual(calls[0]['model'],'gpt-5.4-2026-03-05')
  self.assertFalse(result['match'])
 def test_real_owned_reader_loads_without_five_step_or_live_imports(self):
  fake_sb=types.ModuleType('supabase');fake_sb.create_client=lambda *a:None
  fake_oa=types.ModuleType('openai');fake_oa.OpenAI=object
  fake_dd=types.ModuleType('ddgs');fake_dd.DDGS=object
  with patch.dict(sys.modules,{'supabase':fake_sb,'openai':fake_oa,'ddgs':fake_dd}),tempfile.TemporaryDirectory() as folder:
   scope=native_reader.load(folder)
   self.assertTrue(callable(scope['ai_evaluate_match']))
   self.assertEqual(scope['SEEN_FILE'],str(Path(folder)/'seen.json'))
   self.assertFalse(scope['AUTO_SEARCH'])
   self.assertTrue(scope['inspect_listing_html']('<h1>Item</h1>','guitar','https://example.com/item/1') is not None)
if __name__=='__main__':unittest.main()
