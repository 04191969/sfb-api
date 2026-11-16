import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from sfb_api.api.listing_sources import facebook_results, facebook_search_url
from sfb_api.api.listing_find import _facebook, keep_source_facts, find_listings
from sfb_api.api.ebay_client import EbayNotConfigured
from sfb_api.api.evidence_store import MemoryEvidence
from sfb_api.api.catalog_fetch import CatalogBlocked

PAGE = '''<div class="snippet"><a href="https://www.facebook.com/marketplace/item/123/?tracking=x">Porsche 911 brake caliper</a><div class="snippet-description">Front left caliper, asking $300.</div></div>
<a href="https://m.facebook.com/marketplace/item/123/">Duplicate</a>
<a href="https://facebook.com.evil.test/marketplace/item/234/">brake caliper</a>
<a href="https://www.facebook.com/groups/234/">brake caliper</a>
<a href="https://www.facebook.com/marketplace/atlanta/">brake caliper</a>'''

class FacebookDiscoveryTests(unittest.TestCase):
    def test_find_returns_indexed_item_with_assessment_and_provenance(self):
        class Response:
            status_code=200
            headers={}
            text=PAGE
        def get(url, **kwargs):
            return Response()
        found=find_listings('brake caliper', make='Porsche',model='911',year=2015,
            ebay=EbayNotConfigured('not configured'),old_cars=lambda _:[],
            get=get,pause=lambda _:None,read=lambda *_:None,evidence=MemoryEvidence())
        rows=[x for x in found['listings'] if x['source']=='facebook']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['assessment']['market']['status'],'uncertain')
        shown=[x for x in found['reader'] if x['source']=='facebook']
        self.assertEqual(shown[0]['availability'],'unconfirmed')
        self.assertEqual(shown[0]['oem_number'],'Not listed')

    def test_item_scope_dedupe_and_provenance(self):
        rows = facebook_results(PAGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['item_id'], '123')
        self.assertEqual(rows[0]['url'], 'https://www.facebook.com/marketplace/item/123/')
        self.assertIn('$300', rows[0]['snippet'])
        self.assertEqual(rows[0]['availability'], 'unconfirmed')
        self.assertEqual(rows[0]['sale_status'], 'asking')
        self.assertEqual(rows[0]['identifiers'], [])
        self.assertNotIn('price_currency', rows[0])

    def test_query_carries_vehicle(self):
        q=parse_qs(urlparse(facebook_search_url('brake caliper','Porsche','911',2015)).query)['q'][0]
        self.assertIn('site:facebook.com/marketplace/item/', q)
        self.assertIn('2015 Porsche 911', q)

    def test_deadline_makes_no_request(self):
        with patch('sfb_api.api.listing_find.fetch_catalog') as fetch:
            self.assertEqual(_facebook('caliper','Porsche','911',2015,None,lambda _:None,[],lambda:0), [])
            fetch.assert_not_called()

    def test_refusal_recorded_without_retry_or_direct_facebook_fetch(self):
        skipped=[]
        with patch('sfb_api.api.listing_find.fetch_catalog', side_effect=CatalogBlocked(403,'https://search.brave.com/search')) as fetch:
            self.assertEqual(_facebook('caliper','Porsche','911',2015,None,lambda _:None,skipped,lambda:40), [])
            self.assertEqual(fetch.call_count,1)
            self.assertFalse(fetch.call_args.kwargs['retry_rate_limit'])
        self.assertEqual([x['source'] for x in skipped],['brave','facebook'])

    def test_reader_cannot_upgrade_excerpt_to_verified_oem(self):
        raw=facebook_results(PAGE)
        rows=keep_source_facts(raw,[{'link':raw[0]['url'],'oem_number':'invented','price':'300'}])
        self.assertEqual(rows[0]['oem_number'],'Not listed')
        self.assertEqual(rows[0]['availability'],'unconfirmed')
        self.assertEqual(rows[0]['price'],'300')

    def test_a_breadcrumb_title_becomes_the_excerpt(self):
        page = (
            '<a href="https://www.facebook.com/marketplace/item/1436030267521548/">'
            "Facebook Marketplace facebook.com › marketplace › item › "
            "1436030267521548 Paragon PA015 brake caliper</a>"
        )
        row = facebook_results(page)[0]
        self.assertEqual(row["snippet"], "Paragon PA015 brake caliper")
        self.assertNotIn("facebook.com", row["snippet"].casefold())

    def test_irrelevant_item_filtered(self):
        with patch('sfb_api.api.listing_find.fetch_catalog',return_value=PAGE):
            self.assertEqual(_facebook('water pump','Porsche','911',2015,None,lambda _:None,[],lambda:40),[])
