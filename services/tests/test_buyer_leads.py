import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent))
import test_broad_discovery as fixture
from test_broad_discovery import PublicBrowser,QUERIES,BURL,page
from lead_navigation import classify_buyer
from broad_discovery import source_record
class Tests(unittest.TestCase):
    def test_report_shopping_urls_rejected_even_with_query_words(self):
        urls=['https://www.ebay.com/itm/227511888368','https://www.ironheart.co.uk/products/ih-666s-142od','https://www.ebay.com/shop/iron-heart-666s?_nkw=iron+heart+666s','https://indigoandasphalt.com/fit-finder/denim/iron-heart','https://www.ironshopprovisions.com/products/14oz-selvedge-denim','https://www.reversible.com/shopping/men/item/iron-heart','https://www.ebay.com/sch/i.html?_nkw=iron+heart']
        for url in urls:self.assertEqual(classify_buyer(dict(url=url,title='Iron Heart jeans',query='WTB Iron Heart'))[0],'skip',url)
    def test_actual_wanted_post_preserved_on_unknown_site(self):
        for title in ['WTB Iron Heart size 33','Looking to buy a machine','In search of vintage bicycle','Needed: watch back']:
            self.assertEqual(classify_buyer(dict(url='https://unknown.example/thread/123',title=title))[0],'ad')
    def test_search_query_is_not_buyer_evidence(self):
        self.assertEqual(classify_buyer(dict(url='https://example.com/guide/123',title='Jeans sizing guide',query='WTB jeans'))[0],'skip')
    def test_group_index_and_individual_post_are_distinct(self):
        self.assertEqual(classify_buyer(dict(url='https://www.facebook.com/groups/realestatetransparancy',title='Looking to buy'))[0],'directory')
        self.assertEqual(classify_buyer(dict(url='https://www.facebook.com/groups/123/posts/456',title='WTB bicycle'))[0],'ad')
        p=page(BURL);p['url']='https://www.facebook.com/groups/123'
        with self.assertRaises(ValueError):source_record(p,'buyer')
    def test_shopping_pages_never_open_or_consume_buyer_slots(self):
        harness=fixture.Tests();self.addCleanup(harness.doCleanups)
        original=PublicBrowser.search;visit=PublicBrowser.visit;opened=[]
        def search(browser,query):
            if query==QUERIES[0][1]:return [dict(url='https://www.ebay.com/itm/227511888368',title='Iron Heart jeans'),dict(url='https://shop.example/products/jeans',title='Jeans'),dict(url=BURL,title='WTB Example Q2 guitar')]
            return original(browser,query)
        def capture(browser,url,purpose,parent=None):opened.append(url);return visit(browser,url,purpose,parent)
        with patch.object(PublicBrowser,'search',search),patch.object(PublicBrowser,'visit',capture):
            _,r,_=harness.run_broad()
        self.assertEqual(r['state'],'completed',r.get('reason'))
        self.assertEqual(opened,[BURL]);self.assertEqual(r['public_page_counts']['buyer'],1)
if __name__=='__main__':unittest.main()
