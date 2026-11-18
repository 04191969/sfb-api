"""Offline public/API routing, real continuation and V2 qualification."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_continuation as f
from owner_api_bridge import MultiSourceSellerMixin, MixedSellerBrowser, diverse_leads
from browser_driver import Held

URLS=['https://boston.craigslist.org/bik/d/example/1234567890.html',
      'https://www.facebook.com/marketplace/item/12345678901/',
      'https://dealer.example/products/example-q2',
      'https://forums.example.com/topic/123-for-sale']
class Public(f.Browser):
    def search(self,q):return [dict(url=u,title='Example Q2 guitar for sale',snippet='Example Q2 guitar') for u in URLS]
class Base(f.Runner):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.api=SimpleNamespace(token='token',ids={},report={'searches':0})
        def search(q):
            self.api.report['searches']+=1
            return [f.row(1)]
        self.api.search=search
        self.browser_factory=lambda:SimpleWrapped(Public(),self.api)
class SimpleWrapped:
    def __init__(self,original,client):self.original=original;self.client=client
    def __getattr__(self,n):return getattr(self.original,n)
    def __enter__(self):return self
    def __exit__(self,*args):pass
class Runner(MultiSourceSellerMixin,Base):pass
class Tests(unittest.TestCase):
    def test_all_public_sources_reach_v2_after_rejections(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Runner(Path(tmp)/'run',stocks=[1,1,1,2])
            with patch.object(f.c,'validate_output',lambda o,p:o):r=run.execute('offline')
            self.assertEqual(r['state'],'completed',r.get('reason'))
            self.assertEqual([a['pair']['seller']['url'] for a in r['assessments']],URLS)
            self.assertEqual(r['assessments'][-1]['validated']['Decision'],'Send')
            self.assertNotIn('eBay',r['assessments'][-1]['pair']['seller']['source_coverage'])
            self.assertEqual({s['provider'] for s in r['searches']},{'public','ebay'})
    def test_api_item_only_uses_api_detail(self):
        calls=[]
        api=SimpleNamespace(ids={'https://www.ebay.com/itm/1':'id'},detail=lambda u:calls.append(('api',u)))
        public=SimpleNamespace(visit=lambda u,p,parent:calls.append(('public',u)))
        browser=MixedSellerBrowser(SimpleWrapped(public,api))
        for u in URLS+['https://www.ebay.com/itm/1','https://www.ebay.com/itm/2']:browser.visit(u,'seller')
        self.assertEqual([x[0] for x in calls],['public']*4+['api','public'])
    def test_public_block_does_not_hide_api_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Runner(Path(tmp)/'run');run.browser=SimpleNamespace(trace=[], requests=0, search=lambda q:(_ for _ in ()).throw(Held('blocked')))
            rows=run.search('Example Q2 guitar','seller')
            self.assertEqual(rows[0]['url'],f.row(1)['url'])
            self.assertEqual(run.r['seller_search_failures'][0]['provider'],'public')
    def test_api_failure_does_not_hide_public_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            run=Runner(Path(tmp)/'run');run.browser=Public()
            run.api.search=lambda q:(_ for _ in ()).throw(Held('HTTP 429'))
            self.assertEqual([r['url'] for r in run.search('Example Q2','seller')],URLS)
    def test_round_robin_prevents_ebay_monopoly(self):
        leads=[(10,f.row(i)) for i in range(12)]+[(1,dict(url=u)) for u in URLS]
        self.assertEqual([r['url'] for _,r in diverse_leads(leads)[:4]],URLS)
if __name__=='__main__':unittest.main()
