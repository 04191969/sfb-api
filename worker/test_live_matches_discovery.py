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

    def test_bicycle_fallback_and_craigslist_reaches_reader(self):
        request = 'Item: LL Bean Sport Cruiser 7 Bicycle\nBudget: 135\nBudget currency: USD'
        ad = 'https://boston.craigslist.org/gbs/bik/d/arlington-ll-bean-sport-cruiser/1234567890.html'
        car = {'href': 'https://bringatrailer.com/listing/2007-subaru-outback-8/', 'title': '2007 Subaru Outback wagon', 'body': 'Used automobile for sale'}
        bike = {'href': ad, 'title': 'LL Bean Sport Cruiser 7 Bicycle', 'body': '$135 Arlington'}
        self.s['ddg_text'] = MagicMock(return_value=[car])
        engine = MagicMock()
        engine.text.return_value = [bike]
        self.s['DDGS'] = MagicMock(return_value=engine)
        self.s['read_listing_html'] = MagicMock(return_value='<html><body>LL Bean Sport Cruiser 7 Bicycle. For sale. Asking price: $135. Contact seller in Arlington. Seven speed bicycle with brakes and tires in good condition. Local pickup available by arrangement. Please reply to this ad to arrange viewing.</body></html>')
        result = self.s['search_listings'](request, {}, buyer_text=request)
        self.assertTrue(any(r['url'] == ad and r['qualifying'] for r in result['listings']))
        self.assertEqual([c.args[0] for c in self.s['read_listing_html'].call_args_list], [ad])
        self.assertTrue(any('not opened' in r['reason'] for r in result['skipped']))
        texts = [c.args[0] for c in self.s['ddg_text'].call_args_list]
        self.assertIn('LL Bean Sport Cruiser 7 Bicycle site:craigslist.org', texts)
        self.assertTrue(all(c.kwargs['backend'] == 'google' for c in engine.text.call_args_list))

    def test_fallback_failure_is_bounded_and_preserves_original_queries(self):
        self.s['ddg_text'] = MagicMock(return_value=[])
        engine = MagicMock()
        engine.text.side_effect = RuntimeError('provider unavailable')
        self.s['DDGS'] = MagicMock(return_value=engine)
        self.s['expand_candidates'] = MagicMock(return_value=([], ''))
        self.s['search_listings']('Item: LL Bean Sport Cruiser 7 Bicycle', {})
        self.assertEqual(engine.text.call_count, 2 * self.s['ddg_text'].call_count)
        self.assertEqual({c.kwargs['backend'] for c in engine.text.call_args_list}, {'google', 'bing'})

    def test_partial_product_and_sparse_profile_are_not_discarded(self):
        from live_matches_discovery import unrelated
        for row in [
            {'title': 'Schwinn Sport Cruiser 7', 'body': 'Used bike good condition'},
            {'title': 'Seller', 'body': '', 'href': 'https://www.facebook.com/marketplace/profile/123'},
            {'title': 'LL Bean Bicycle', 'body': 'Collection available locally'},
        ]:
            self.assertFalse(unrelated(row, 'LL Bean Sport Cruiser 7 Bicycle'))

    def craigslist_html(self, url, category='bicycles - by owner', body='LL Bean Hunter Green Sports Cruiser 7 Bicycle. Great condition, couple scratches.'):
        import json
        crumb = {'@type': 'BreadcrumbList', 'itemListElement': [
            {'name': 'for sale', 'item': 'https://www.craigslist.org/search?cat=sss'},
            {'name': category, 'item': 'https://www.craigslist.org/search?cat=bia'},
            {'name': 'LL Bean Sport Cruiser 7 Bicycle', 'item': url}]}
        return (f'<html><head><link rel="canonical" href="{url}">'
                + '<script type="application/ld+json">' + json.dumps(crumb) + '</script>'
                + '<script type="application/ld+json">{"@type":"Product","offers":{"price":"135.00"}}</script></head><body>'
                + '<h1><span id="titletextonly">LL Bean Sport Cruiser 7 Bicycle</span> - $135</h1>'
                + f'<section id="postingbody">{body}</section></body></html>')

    def test_craigslist_own_sale_structure_without_sale_words(self):
        url = 'https://www.craigslist.org/view/d/arlington-ll-bean-sport-cruiser-bicycle/fuoAb8TXm1iswswPpAZku3'
        result = self.s['inspect_listing_html'](self.craigslist_html(url), 'LL Bean Sport Cruiser 7 Bicycle', url)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['evidence'], 'classified_ad')
        self.assertTrue(result['checked_at'])
        self.assertEqual(result['price'], 135)
        self.assertIsNone(result['shipping'])

    def test_craigslist_closed_wanted_other_page_and_missing_fields(self):
        url = 'https://www.craigslist.org/view/d/arlington-bike/abc123'
        base = self.craigslist_html(url)
        for html in [self.craigslist_html(url, body='This bicycle has been sold.'),
                     self.craigslist_html(url, body='This posting has expired.')]:
            self.assertEqual(self.s['inspect_listing_html'](html, 'LL Bean Sport Cruiser 7 Bicycle', url)['status'], 'ended')
        for html, link in [
            (self.craigslist_html(url, category='wanted - by owner'), url),
            (base.replace('id="postingbody"', 'id="related"'), url),
            (base.replace('id="titletextonly"', 'id="other"'), url),
            (base.replace('$135', '').replace('"price":"135.00"', '"price":null'), url),
            (base, url + 'other'),
            (base, 'https://example.com/view/d/arlington-bike/abc123'),
            (base, 'https://www.craigslist.org/search/bia'),
            ('<html>Access denied</html>', url),
        ]:
            self.assertNotEqual(self.s['inspect_listing_html'](html, 'LL Bean Sport Cruiser 7 Bicycle', link)['status'], 'available')

    def test_explicit_price_beyond_old_excerpt_limit(self):
        text = '<html><body>Fender ST62 Buy It Now. ' + ('description ' * 80) + '. Asking price: $925.00</body></html>'
        result = self.s['inspect_listing_html'](text, 'Fender ST62', 'https://reverb.com/item/123-example')
        self.assertEqual(result['price'], 925)

    def test_price_absence_shipping_and_original_price_not_filled(self):
        for tail in ('Shipping: $80', 'Original price: $950', 'No stated price', 'Asking price: $925. Asking price: $900.'):
            text = '<html><body>Fender ST62 Buy It Now. ' + ('description ' * 80) + '. ' + tail + '</body></html>'
            self.assertIsNone(self.s['inspect_listing_html'](text, 'Fender ST62', 'https://reverb.com/item/123-example')['price'])


if __name__ == '__main__':unittest.main()
