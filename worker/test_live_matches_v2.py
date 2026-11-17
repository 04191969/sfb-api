import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import live_matches_restore as restore
from live_matches_v2.adapter import assess, install, template
from test_live_matches_restore import dependencies

BUYER = 'Looking for: Example Q2 guitar'
PAGE = 'Example Q2 guitar. One guitar for sale. Buy It Now.'


def reading():
    b = {'source': 'buyer', 'quote': BUYER}
    s = {'source': 'seller', 'quote': PAGE}
    return {
        'buyer_role': {'value': 'buy', 'evidence': [b]},
        'selected_request': {'description': 'Example Q2 guitar', 'buyer_quote': BUYER},
        'offer': {'kind': 'individual', 'description': 'One guitar', 'evidence': [s]},
        'product': {'status': 'matches', 'explanation': 'Same named model.', 'evidence': [b, s]},
        'availability': {'value': 'available', 'basis': 'listing_text', 'evidence': [s]},
        'budget': {'mode': 'none', 'limit': None, 'currency': None, 'basis': 'unknown', 'buyer_quote': ''},
        'quantity': {'requested': 1, 'items_per_unit': 1, 'units_available': None, 'max_units_per_buyer': None, 'buyer_quote': BUYER, 'evidence': [s]},
        'cost': {'unit_item_price': None, 'shipping_for_required_purchase': None, 'delivered_total_for_required_purchase': None, 'currency': None, 'evidence': []},
        'checks': [], 'disclosures': [], 'assumptions': [],
    }


def response(r=None, status='completed'):
    return {'status': status, 'output': [{'content': [{'type': 'output_text', 'text': json.dumps(r or reading())}]}]}


class V2Tests(unittest.TestCase):
    def setUp(self):
        with dependencies():
            self.scope = restore.load_baseline()
        self.seller = {'title': 'Example Q2 guitar', 'text': 'Search excerpt without price',
                       'page_text': PAGE, 'url': 'https://www.facebook.com/marketplace/item/123/',
                       'discovery_url': 'https://www.facebook.com/marketplace/profile/456/',
                       'platform': 'facebook', 'qualifying': True, 'availability': 'available', 'price': None}
        self.result = {'listings': [self.seller], 'incomplete': '', 'skipped': []}
        self.search = MagicMock(return_value=self.result)
        self.scope['search_listings'] = self.search
        self.scope['_reserve_ai'] = MagicMock(return_value=('db', 'token'))
        self.scope['_settle_ai'] = MagicMock(return_value=True)
        self.scope['push_match'] = MagicMock(return_value=('match', True))
        self.scope['search_wtb_posts'] = MagicMock(return_value=[])
        self.scope['mark_seen'] = MagicMock()
        self.client = MagicMock()
        self.client.with_options.return_value.responses.create.return_value.model_dump.return_value = response()
        self.sb = MagicMock()
        self.sb.table.return_value.select.return_value.execute.return_value.data = [
            {'id': 'market', 'name': 'Example Q2 guitar', 'make': 'Example', 'model': 'Q2 guitar'}]
        install(self.scope)
        self.scope['search_listings']('Example Q2 guitar', {}, max_keep=6, buyer_text=BUYER)

    def evaluate(self):
        return self.scope['ai_evaluate_match'](BUYER, self.seller['text'], self.client, self.sb)

    def test_exact_template_and_full_page_no_extra_search(self):
        result = self.evaluate()
        self.assertTrue(result['match'])
        self.assertEqual(result['make'], 'Example')
        payload = self.client.with_options.return_value.responses.create.call_args.kwargs
        self.assertEqual(json.loads(payload['input']), {'buyer_text': BUYER, 'seller_text': PAGE})
        self.assertEqual({k: v for k, v in payload.items() if k != 'input'}, template())
        self.client.with_options.assert_called_once_with(max_retries=0, timeout=300.0)
        self.client.chat.completions.create.assert_not_called()
        self.scope['_settle_ai'].assert_called_once_with(self.sb, 'token', 'succeeded')
        self.search.assert_called_once()

    def test_search_objects_links_and_unknown_costs_are_unchanged(self):
        before = copy.deepcopy(self.result)
        returned = self.scope['search_listings']('same', {})
        self.assertIs(returned, self.result)
        self.evaluate()
        self.assertEqual(before, self.result)

    def test_no_budget_missing_price_qualifies(self):
        self.assertTrue(self.evaluate()['match'])

    def test_trade_ended_conflict_and_unconfirmed_never_send(self):
        for field, value in [('availability', 'trade_only'), ('availability', 'ended'), ('availability', 'unknown'), ('product', 'conflict')]:
            r = reading();r[field]['status' if field == 'product' else 'value'] = value
            self.client.with_options.return_value.responses.create.return_value.model_dump.return_value = response(r)
            self.assertFalse(self.evaluate()['match'])

    def test_invalid_quote_or_incomplete_output_stops_without_save(self):
        r = reading();r['quantity']['evidence'][0]['quote'] = 'invented'
        for raw in (response(r), response(status='incomplete')):
            self.client.with_options.return_value.responses.create.return_value.model_dump.return_value = raw
            with self.assertRaises(self.scope['ReservationClosed']):self.evaluate()
        self.scope['push_match'].assert_not_called()

    def test_missing_page_and_ambiguous_identity_do_not_call_model(self):
        self.seller['page_text'] = ''
        with self.assertRaises(self.scope['ReservationClosed']):self.evaluate()
        self.result['listings'].append(dict(self.seller))
        self.scope['search_listings']('same', {})
        with self.assertRaises(self.scope['ReservationClosed']):self.evaluate()
        self.scope['_reserve_ai'].assert_not_called()

    def test_timeout_no_retry_no_false_failed_settlement(self):
        self.client.with_options.return_value.responses.create.side_effect = TimeoutError('secret')
        with self.assertRaises(self.scope['ReservationClosed']) as caught:self.evaluate()
        self.assertNotIn('secret', str(caught.exception))
        self.client.with_options.return_value.responses.create.assert_called_once()
        self.scope['_settle_ai'].assert_not_called()

    def test_reservation_failure_no_model_call(self):
        self.scope['_reserve_ai'].side_effect = self.scope['ReservationClosed']('database unavailable')
        with self.assertRaises(self.scope['ReservationClosed']):self.evaluate()
        self.client.with_options.return_value.responses.create.assert_not_called()

    def test_full_consumer_flow_saves_only_v2_send(self):
        self.scope['claim_next_run'] = lambda _: {'id': 'test', 'search_query': 'Example Q2 guitar'}
        self.assertTrue(self.scope['check_manual_requests'](self.sb, self.client, {}))
        self.scope['push_match'].assert_called_once()
        self.assertIs(self.scope['push_match'].call_args.args[2], self.seller)
        self.assertEqual(self.scope['push_match'].call_args.args[3]['model'], 'Q2 guitar')

    def test_full_flow_v2_reject_does_not_reach_legacy_send(self):
        r = reading();r['availability']['value'] = 'trade_only'
        self.client.with_options.return_value.responses.create.return_value.model_dump.return_value = response(r)
        self.scope['claim_next_run'] = lambda _: {'id': 'test', 'search_query': 'Example Q2 guitar'}
        self.assertTrue(self.scope['check_manual_requests'](self.sb, self.client, {}))
        self.scope['push_match'].assert_not_called()


if __name__ == '__main__':unittest.main()
