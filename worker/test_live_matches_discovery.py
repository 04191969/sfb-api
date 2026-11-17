import copy
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import live_matches_restore
from live_matches_discovery import install, item_identity
from test_live_matches_restore import dependencies

REQUEST = 'Item: Fender Japan ST62\nRequirements: Ocean Turquoise Metallic\nBudget: 990 plus shipping'


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        with dependencies():self.s = live_matches_restore.load_baseline()
        self.s['time'] = MagicMock()
        self.s['is_seen'] = lambda *args: False
        install(self.s)

    def offer(self, **changes):
        row = {'url': 'https://reverb.com/item/123-example', 'title': 'Fender Japan ST62', 'text': '', 'page_text': '', 'availability': 'available', 'price': 743, 'shipping': None, 'location': 'Dover, MA'}
        row.update(changes);return row

    def test_color_is_not_a_location_and_full_request_preserved(self):
        self.assertEqual(self.s['required_location'](REQUEST), '')
        self.assertTrue(self.s['qualifies'](self.offer(), REQUEST))
        self.assertFalse(self.s['qualifies'](self.offer(), REQUEST + '\nLocation: Ohio'))

    def test_budget_and_availability_rules_stay(self):
        for price, expected in [(990, True), (991, False), (None, False)]:
            self.assertEqual(self.s['qualifies'](self.offer(price=price), REQUEST), expected)
        self.assertFalse(self.s['qualifies'](self.offer(availability='unconfirmed'), REQUEST))
        self.assertTrue(self.s['qualifies'](self.offer(price=None), 'Item: Fender Japan ST62'))
        self.assertFalse(self.s['qualifies'](self.offer(), REQUEST.replace('plus shipping', 'including shipping')))

    def test_rejection_log_names_cost_reason_and_item(self):
        log = io.StringIO()
        with redirect_stdout(log):self.s['qualifies'](self.offer(price=1099), REQUEST)
        self.assertIn('1099.00 exceeds limit 990.00', log.getvalue())
        self.assertIn('/item/123-example', log.getvalue())

    def test_same_id_dedup_not_same_title_or_cross_platform(self):
        self.assertEqual(item_identity('https://reverb.com/item/123-old'), item_identity('https://reverb.com/en-nl/item/123-new?x=1'))
        self.assertNotEqual(item_identity('https://reverb.com/item/123-a'), item_identity('https://reverb.com/item/124-a'))
        self.assertNotEqual(item_identity('https://reverb.com/item/123-a'), item_identity('https://www.facebook.com/marketplace/item/123/'))
        first = self.s['canonical_item_url']('https://www.ebay.com/itm/123456789012')
        self.assertEqual(self.s['canonical_item_url']('https://www.ebay.co.uk/itm/123456789012?x=1'), first)

    def test_targeted_plus_all_original_queries_direct_color_first(self):
        rows = [
            {'href': 'https://www.ebay.com/sch/i.html?_nkw=st62', 'title': 'Fender ST62', 'body': ''},
            {'href': 'https://reverb.com/item/123-other', 'title': 'Fender ST62 white', 'body': ''},
            {'href': 'https://reverb.com/item/124-color', 'title': 'Fender ST62 Ocean Turquoise Metallic', 'body': ''},
            {'href': 'https://reverb.com/item/124-newslug', 'title': 'Same item', 'body': ''},
        ]
        self.s['ddg_text'] = MagicMock(return_value=rows)
        self.s['expand_candidates'] = MagicMock(return_value=([], ''))
        self.s['search_listings'](REQUEST, {}, buyer_text=REQUEST)
        texts = [c.args[0] for c in self.s['ddg_text'].call_args_list]
        self.assertIn('Ocean Turquoise Metallic', texts[0])
        for q in self.s['LISTING_QUERIES']:
            self.assertIn(q.format(query='Fender Japan ST62'), texts)
        args = self.s['expand_candidates'].call_args.args
        self.assertEqual(args[0][0]['url'], 'https://reverb.com/item/124-color')
        self.assertEqual(len(args[0]), 3)
        self.assertEqual(args[3], 8)
        self.assertEqual(args[4], REQUEST)

    def test_explicit_price_beyond_old_excerpt_limit(self):
        text = '<html><body>Fender ST62 Buy It Now. ' + ('description ' * 80) + '. Asking price: $925.00</body></html>'
        result = self.s['inspect_listing_html'](text, 'Fender ST62', 'https://reverb.com/item/123-example')
        self.assertEqual(result['price'], 925)

    def test_price_absence_shipping_and_original_price_not_filled(self):
        for tail in ('Shipping: $80', 'Original price: $950', 'No stated price', 'Asking price: $925. Asking price: $900.'):
            text = '<html><body>Fender ST62 Buy It Now. ' + ('description ' * 80) + '. ' + tail + '</body></html>'
            self.assertIsNone(self.s['inspect_listing_html'](text, 'Fender ST62', 'https://reverb.com/item/123-example')['price'])


if __name__ == '__main__':unittest.main()
