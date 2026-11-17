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
