import json
import unittest
from unittest.mock import MagicMock
import live_matches_restore
from live_matches_currency import install, offer_currency
from test_live_matches_restore import dependencies
import test_live_matches_v2 as v2

URL = 'https://reverb.com/item/123-example'


class CurrencyTests(unittest.TestCase):
    def setUp(self):
        with dependencies():self.scope = live_matches_restore.load_baseline()

    def html(self, currency='USD', url=URL, price=900):
        node = {'@type': 'Product', 'offers': {'@type': 'Offer', 'url': url, 'price': price, 'priceCurrency': currency}}
        return '<html><body>Example guitar for sale. Buy It Now.' + (' Description.' * 20) + '<script type="application/ld+json">' + json.dumps(node) + '</script></body></html>'

    def test_current_item_currency_survives_script_removal(self):
        install(self.scope)
        result = self.scope['inspect_listing_html'](self.html(), 'Example guitar', URL)
        self.assertIn('priceCurrency USD', result['text'])
        self.assertEqual(result['price'], 900)

    def test_other_offer_conflicting_or_missing_currency_not_used(self):
        self.assertIsNone(offer_currency(self.html(url='https://reverb.com/item/124-other'), URL, 900, self.scope))
        self.assertIsNone(offer_currency(self.html(price=1000), URL, 900, self.scope))
        self.assertIsNone(offer_currency(self.html(currency=None), URL, 900, self.scope))
        self.assertIsNone(offer_currency(self.html() + self.html(currency='CAD'), URL, 900, self.scope))
        self.assertIsNone(offer_currency('Price $900; United States', URL, 900, self.scope))

    def craigslist(self, currency='USD', **changes):
        url = 'https://www.craigslist.org/view/d/arlington-bike/abc123'
        name = 'LL Bean Sport Cruiser 7 Bicycle'
        description = 'Hunter Green bicycle, great condition, couple scratches.'
        product = {'@type': 'Product', 'name': name, 'description': description,
                   'offers': {'price': '135.00', 'priceCurrency': currency}}
        product.update(changes)
        crumbs = {'@type': 'BreadcrumbList', 'itemListElement': [
            {'name': 'for sale', 'item': '/search?cat=sss'},
            {'name': 'bicycles - by owner', 'item': '/search?cat=bia'},
            {'name': name, 'item': url}]}
        html = (f'<html><head><link rel="canonical" href="{url}"></head><body>'
                f'<h1 id="titletextonly">{name}</h1><span class="price">$135</span>'
                f'<section id="postingbody">QR Code Link to This Post {description}</section>'
                '<script type="application/ld+json">' + json.dumps(crumbs) + '</script>'
                '<script type="application/ld+json">' + json.dumps(product) + '</script></body></html>')
        return url, html

    def test_craigslist_missing_offer_url_uses_own_ad_identity(self):
        from live_matches_discovery import install as discovery
        url, html = self.craigslist()
        discovery(self.scope)
        install(self.scope)
        result = self.scope['inspect_listing_html'](html, 'LL Bean Sport Cruiser 7 Bicycle', url)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['price'], 135)
        self.assertIsNone(result['shipping'])
        self.assertIn('priceCurrency USD', result['text'])
        self.assertIn('item URL ' + url, result['text'])

    def test_craigslist_binding_rejects_ambiguous_or_other_product(self):
        url, good = self.craigslist()
        variants = [self.craigslist(name='Different bike')[1],
                    self.craigslist(description='Different description')[1],
                    good.replace('rel="canonical"', 'rel="other"'),
                    good.replace('bicycles - by owner', 'wanted - by owner'),
                    good + '<script type="application/ld+json">{"@type":"Product","name":"Related bike"}</script>',
                    self.craigslist(url='https://www.craigslist.org/view/d/other/xyz')[1],
                    self.craigslist(currency=None)[1]]
        for html in variants:
            self.assertIsNone(offer_currency(html, url, 135, self.scope))
        self.assertIsNone(offer_currency(good, url, 136, self.scope))
        self.assertIsNone(offer_currency(good, 'https://example.com/view/d/arlington-bike/abc123', 135, self.scope))
        self.assertIsNone(offer_currency(self.html(url=None), URL, 900, self.scope))



class CurrencySummaryTests(unittest.TestCase):
    def test_product_match_currency_hold_in_run_summary(self):
        harness = v2.V2Tests()
        harness.setUp()
        r = v2.reading()
        r['budget'].update(mode='firm', limit='990', currency=None, basis='item', buyer_quote=v2.BUYER)
        harness.client.with_options.return_value.responses.create.return_value.model_dump.return_value = v2.response(r)
        result = harness.evaluate()
        self.assertFalse(result['match'])
        summary = harness.scope['search_outcome']([], 'the page budget was used before every candidate was opened')
        self.assertIn('1 product match(es) held', summary)
        self.assertIn('currency', summary)
        self.assertIn('page budget', summary)


if __name__ == '__main__':unittest.main()
