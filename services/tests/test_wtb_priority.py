import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import httpx
import test_broad_discovery as f
from broad_discovery import prioritized_queries,source_record,QUERIES
from lead_navigation import classify
from public_search_adapter import PublicSearchBrowser
from browser_driver import Held
class Tests(unittest.TestCase):
    def test_all_buyer_queries_first_at_every_rotation(self):
        for start in range(10):
            queries=prioritized_queries(start)
            self.assertEqual([s for s,q in queries],['buyer']*5+['seller']*5)
            self.assertEqual(set(queries),set(QUERIES))
        self.assertEqual(len({prioritized_queries(i)[0] for i in range(5)}),5)
    def test_opportunity_worked_before_next_broad_search(self):
        fixture=f.Tests();self.addCleanup(fixture.doCleanups)
        _,r,_=fixture.run_broad(start=9)
        self.assertEqual(r['decision'],'Send')
        self.assertTrue(all(x['purpose']=='buyer' for x in r['searches']))
    def test_broad_dispatch_uses_seeker_allowance_and_quote_instruction(self):
        fixture=f.Tests();self.addCleanup(fixture.doCleanups)
        original=f.BroadRunner.transport_call;requests=[]
        def transport(run,kind,body):
            if kind=='discovery':requests.append(json.loads(body))
            return original(run,kind,body)
        with patch.object(f.BroadRunner,'transport_call',transport):run,r,_=fixture.run_broad()
        self.assertEqual(r['state'],'completed')
        self.assertEqual(requests[0]['max_output_tokens'],6000)
        self.assertIn('exact contiguous substring',requests[0]['instructions'])
        reader=[o for o in run.ledger.summary()['operations'] if o['purpose']=='discovery'][0]
        self.assertEqual(reader['plan']['max_output_tokens'],6000)
    def test_short_original_post_preserved_unmodified(self):
        p=f.page(f.BURL);p['source_text']='WTB irons with TPT 14 or 16 LO shafts.'
        self.assertEqual(source_record(p,'buyer')['author_text'],p['source_text'])
        p['extraction']='main_region'
        with self.assertRaisesRegex(ValueError,'chars='):source_record(p,'buyer')
        p['extraction']='first_original_post:article';p['source_text']=' '
        with self.assertRaises(ValueError):source_record(p,'buyer')
        p['source_text']='WTB '+'x'*20001
        with self.assertRaises(ValueError):source_record(p,'buyer')
    def test_bad_sources_and_indexes_from_trace(self):
        for url in ['https://simplified.com/social-media-glossary/iso-in-search-of','https://idioms.thefreedictionary.com/in+search+of','https://www.wtsinternational.org/','https://www.wtsinternational.org/about/awards']:
            self.assertEqual(classify(dict(url=url,title='WTB WTS'))[0],'skip')
        for url in ['https://www.reddit.com/r/WantToBuy','https://www.facebook.com/marketplace']:
            self.assertEqual(classify(dict(url=url,title='Wanted marketplace'))[0],'directory')
    def browser(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        b=PublicSearchBrowser(Path(tmp.name));b.address=MagicMock();return b
    def test_normal_redirect_followed_and_counted(self):
        b=self.browser();urls=[];client=httpx.Client
        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(307,headers={'location':'/results'}) if len(urls)==1 else httpx.Response(200,text='<html>search results</html>')
        with patch('public_search_adapter.httpx.Client',lambda **kw:client(transport=httpx.MockTransport(handler),trust_env=False,**kw)):
            self.assertIn('search results',b.fetch('https://search.yahoo.com/search?q=WTB'))
        self.assertEqual(b.dispatched,2);self.assertEqual(urls[-1],'https://search.yahoo.com/results')
    def test_challenge_redirect_not_followed(self):
        b=self.browser();client=httpx.Client
        with patch('public_search_adapter.httpx.Client',lambda **kw:client(transport=httpx.MockTransport(lambda r:httpx.Response(307,headers={'location':'/captcha'})),trust_env=False,**kw)):
            with self.assertRaises(Held):b.fetch('https://search.yahoo.com/search')
        self.assertEqual(b.dispatched,1)
    def test_failed_engines_not_dispatched_again_same_run(self):
        b=self.browser()
        with patch.object(b,'fetch',side_effect=Held('HTTP 429')) as fetch,patch('public_search_adapter.random.shuffle',lambda x:None):
            with self.assertRaises(Exception):b.public_engine_search('WTB guitar')
            calls=fetch.call_count
            with self.assertRaises(Exception):b.public_engine_search('WTB bike')
            self.assertEqual(fetch.call_count,calls)
if __name__=='__main__':unittest.main()
