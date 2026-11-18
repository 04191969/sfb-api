"""Offline broad discovery, actual reader contract, V2 and ledger; no paid calls."""
import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
from test_continuation import Runner, Browser, BUYER, SELLER, readout
from broad_discovery import BroadDiscoveryMixin, DiscoveryState, QUERIES, source_record
from automatic_run import RunLimitReached, SearchExtractionError

OLD_BUYER=BUYER
BUYER += ' Please contact me with any suitable offers for this requested pair.'

BURL='https://example.com/thread/wtb-example-q2'
SURL='https://example.com/thread/wts-example-q2'
def page(url):
    buyer=url==BURL
    return dict(url=url,capture_id=url,source_text=BUYER if buyer else SELLER,
        title='WTB Example Q2 guitar' if buyer else 'For sale Example Q2 guitar',
        extraction='first_original_post:article',single_listing=True,links=[])
class PublicBrowser(Browser):
    def search(self,query):
        if query==QUERIES[0][1] or query=='Example Q2 guitar wanted':return [dict(url=BURL,title='WTB Example Q2 guitar')]
        if query==QUERIES[1][1]:return [dict(url=SURL,title='WTS Example Q2 guitar')]
        return []
    def visit(self,url,purpose,parent=None):return page(url)
class BroadRunner(BroadDiscoveryMixin,Runner):
    def __init__(self,folder,hold=False):
        super().__init__(folder);self.browser_factory=PublicBrowser;self.hold=hold
    def transport_call(self,kind,body):
        self.calls.append(('model',kind));request=json.loads(body)
        if kind=='discovery':
            p=json.loads(request['input'])
            answer=dict(state='hold' if self.hold else 'ready',direction=p['direction'],interpretation='Original author request/offer',
                quotations=[dict(id='q1',text=p['author_text'],supports='Item and intent')],
                queries=[] if self.hold else [dict(text='Example Q2 guitar for sale' if p['direction']=='find_sellers' else 'Example Q2 guitar wanted',rationale='Opposite party',quotation_ids=['q1'])],
                unresolved=[],source_limitations=p['source_limitations'],source=p['source'])
        else:answer=json.loads(json.dumps(readout(2)).replace(OLD_BUYER,BUYER))
        return json.dumps(dict(model=request['model'],status='completed',usage=dict(input_tokens=10,output_tokens=10),
            output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(answer))])])).encode()
    def iter_sellers(self,queries,buyer):
        self.calls.append(('ebay',queries))
        yield source_record(page(SURL),'seller')
class Tests(unittest.TestCase):
    def run_broad(self,start=0,hold=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        state=DiscoveryState(root/'state'/'broad_discovery.sqlite3')
        for _ in range(start):state.start()
        state.close()
        run=BroadRunner(root/'results'/'run',hold=hold)
        with patch.dict(os.environ,SFB_COMPONENT_STATE=str(root/'state')):report=run.execute('offline')
        return run,report,root
    def test_buyer_first_reaches_v2_with_original_sources(self):
        run,r,_=self.run_broad()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['decision'],'Send')
        self.assertEqual(r['pair']['buyer']['author_text'],BUYER)
        self.assertEqual(r['pair']['seller']['author_text'],SELLER)
        self.assertTrue(any(c[0]=='ebay' for c in run.calls))
        self.assertEqual(r['discovery_readings'][0]['output']['direction'],'find_sellers')
    def test_seller_first_finds_public_buyer_and_reaches_same_v2(self):
        run,r,_=self.run_broad(start=1)
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['decision'],'Send')
        self.assertEqual([x['output']['direction'] for x in r['discovery_readings']],['find_buyers','find_sellers'])
        self.assertEqual(r['pair']['buyer']['author_text'],BUYER)
        self.assertEqual(r['pair']['seller']['author_text'],SELLER)
        self.assertFalse(any(c[0]=='ebay' for c in run.calls))
    def test_reader_hold_never_becomes_pair(self):
        run,r,_=self.run_broad(hold=True)
        self.assertEqual(r['state'],'no_qualifying_match',r.get('reason'))
        self.assertEqual(r['assessments'],[])
        self.assertFalse(any(c==('model','matcher') for c in run.calls))
    def test_rotation_survives_reopen_and_covers_all_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite3';starts=[]
            for _ in range(11):
                state=DiscoveryState(path);starts.append(state.start());state.close()
            self.assertEqual(starts,list(range(10))+[0])
            self.assertEqual([QUERIES[i][0] for i in starts[:4]],['buyer','seller','buyer','seller'])
    def test_checked_sources_expire_and_roles_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=DiscoveryState(Path(tmp)/'state.sqlite3')
            with patch('broad_discovery.time.time',return_value=100):state.mark('buyer',BURL,'hash')
            with patch('broad_discovery.time.time',return_value=200):
                self.assertTrue(state.recent('buyer',BURL));self.assertFalse(state.recent('seller',BURL))
            with patch('broad_discovery.time.time',return_value=86501):self.assertFalse(state.recent('buyer',BURL))
            state.close()
    def test_nonoriginal_category_ad_admitted_but_not_qualified(self):
        p=page(BURL);p.update(title='WTB embroidery machine',source_text='WTB Barudan embroidery machine. Looking to buy a working two head machine for my workshop. Please contact me with offers.')
        self.assertEqual(source_record(p,'buyer')['author_text'],p['source_text'])
    def test_profile_never_opened(self):
        with patch.object(PublicBrowser,'search',return_value=[dict(url='https://example.com/profile/123',title='WTB Example Q2')]),patch.object(PublicBrowser,'visit',side_effect=AssertionError('profile opened')):
            _,r,_=self.run_broad()
        self.assertEqual(r['state'],'no_qualifying_match')
    def test_search_failure_is_incomplete_not_no_sellers(self):
        with patch.object(PublicBrowser,'search',side_effect=SearchExtractionError('blocked')):
            _,r,_=self.run_broad()
        self.assertEqual(r['state'],'search_extraction_failed')
    def test_directory_not_saved_as_seller(self):
        p=page(SURL);p.update(title='All listings classifieds',single_listing=False)
        with self.assertRaises(ValueError):source_record(p,'seller')
    def test_page_allowance_and_cooldown_are_recorded(self):
        _,r,root=self.run_broad()
        self.assertEqual(r['public_page_counts']['buyer'],1)
        state=DiscoveryState(root/'state'/'broad_discovery.sqlite3')
        self.assertTrue(state.recent('buyer',BURL));state.close()
    def test_shared_public_seller_allowance_includes_api_visits(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        run=BroadRunner(Path(tmp.name)/'run')
        run.browser=PublicBrowser();run.r['public_page_counts']={'buyer':0,'seller':1}
        run.r['seller_counts']['page_visits_attempted']=8
        with self.assertRaises(RunLimitReached):list(run.public_sources(QUERIES[1][1],'seller'))
    def test_wanted_directory_is_not_individual_buyer(self):
        p=page(BURL);p.update(extraction='main_region',links=[{'url':BURL+str(i),'title':'WTB Example'} for i in range(6)])
        with self.assertRaises(ValueError):source_record(p,'buyer')
    def test_directory_is_only_traversed_never_passed_to_reader(self):
        original=PublicBrowser.search
        def search(browser,query):
            if query==QUERIES[0][1]:return [dict(url='https://example.com/categories/wanted',title='Wanted classifieds')]
            return original(browser,query)
        def directory(browser,url,parent=None):
            return dict(url=url,title='Wanted classifieds',source_text='',links=[dict(url=BURL,title='WTB Example Q2 guitar')])
        with patch.object(PublicBrowser,'search',search),patch.object(PublicBrowser,'visit_directory',directory,create=True):
            _,r,_=self.run_broad()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['discovery_readings'][0]['source']['url'],BURL)
    def test_failed_directory_continues_to_next_real_ad(self):
        from browser_driver import Held
        original=PublicBrowser.search
        def search(browser,query):
            if query==QUERIES[0][1]:return [dict(url='http://www.bimmerboard.com/forums/forsale',title='For Sale Forum'),dict(url=BURL,title='WTB Example Q2 guitar')]
            return original(browser,query)
        with patch.object(PublicBrowser,'search',search),patch.object(PublicBrowser,'visit_directory',side_effect=Held('Directory navigation failed: net::ERR_BLOCKED_BY_CLIENT'),create=True):
            _,r,_=self.run_broad()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(r['pair']['buyer']['url'],BURL)
    def test_current_reader_receives_full_original_and_correct_direction(self):
        _,r,_=self.run_broad(start=1)
        for entry in r['discovery_readings']:
            self.assertEqual(entry['source']['text_sha256'],entry['output']['source']['text_sha256'])
            self.assertEqual(entry['source']['author_text'],entry['output']['quotations'][0]['text'])
if __name__=='__main__':unittest.main()
