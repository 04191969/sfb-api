import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from sfb_api.api.test_seeker_worker_rules import load_worker


class LockedQQ2Integration(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker()
        self.rows = json.loads((Path(__file__).parent / "fixtures/locked_qq2_replay.json").read_text())
        self.worker['_reserve_ai'] = Mock(return_value=('db', 'reservation'))
        self.worker['_settle_ai'] = Mock()
        self.client = Mock()
        self.client.with_options.return_value = self.client

    def evaluate(self, row, answer=None, status='completed'):
        self.client.responses.create.return_value = SimpleNamespace(
            status=status, output_text=json.dumps(answer or row['answer']))
        return self.worker['ai_evaluate_match'](
            row['input']['buyer_text'], row['input']['seller_text'], self.client, object(), False)

    def test_all_33_archived_responses_through_worker(self):
        for row in self.rows:
            with self.subTest(case=row['id']):
                result = self.evaluate(row)
                self.assertEqual(result['assessment'], row['answer'])
                self.assertEqual(self.worker['matcher_accepts'](result), row['answer']['decision'] == 'Send')
                self.assertIsNone(result['confidence'])
                payload = self.client.responses.create.call_args.kwargs
                self.assertEqual(json.loads(payload['input']), row['input'])
                self.assertEqual(payload['model'], 'gpt-5.4-2026-03-05')
                self.assertEqual(payload['max_output_tokens'], 6000)
                self.assertEqual(payload['reasoning'], {'effort': 'medium'})
                self.assertEqual(payload['tools'], [])
        self.assertEqual(self.client.responses.create.call_count, 33)
        self.client.chat.completions.create.assert_not_called()
        self.client.with_options.assert_called_with(max_retries=0)

    def test_full_source_after_1500_characters(self):
        row = copy.deepcopy(self.rows[0])
        row['input']['seller_text'] += '\n' + 'x' * 2000 + '\nSOLD. No longer available.'
        self.evaluate(row)
        sent = json.loads(self.client.responses.create.call_args.kwargs['input'])
        self.assertEqual(sent, row['input'])

    def test_invalid_quote_withheld_without_retry(self):
        row = self.rows[0]
        answer = copy.deepcopy(row['answer'])
        answer['evidence'][0]['quote'] = 'This quotation does not occur in the original source.'
        self.assertIsNone(self.evaluate(row, answer))
        self.assertEqual(self.client.responses.create.call_count, 1)
        self.worker['_settle_ai'].assert_called_once_with(unittest.mock.ANY, 'reservation', 'succeeded')

    def test_truncated_response_is_not_accepted(self):
        self.assertIsNone(self.evaluate(self.rows[0], status='incomplete'))
        self.assertEqual(self.client.responses.create.call_count, 1)

    def test_transport_failure_has_no_legacy_fallback(self):
        self.client.responses.create.side_effect = RuntimeError('transport failed')
        self.assertIsNone(self.evaluate(self.rows[0]))
        self.client.chat.completions.create.assert_not_called()
        self.worker['_settle_ai'].assert_called_once_with(unittest.mock.ANY, 'reservation', 'failed')

    def test_existing_availability_and_budget_gates_still_apply(self):
        assess = self.worker['assessment_text']
        args = dict(confidence=None, product_reason='QQ2 Send', location='', matcher_decision='Send')
        self.assertEqual(assess('ended', **args)[0], 'no_send')
        self.assertEqual(assess('unconfirmed', **args)[0], 'no_send')
        self.assertEqual(assess('available', item_price=850, buyer_text='Budget: 850 plus shipping — firm maximum', **args)[0], 'send')
        self.assertEqual(assess('available', item_price=875, buyer_text='Budget: 850 plus shipping — firm maximum', **args)[0], 'no_send')
        self.assertEqual(assess('available', **dict(args, matcher_decision='Reject'))[0], 'no_send')

    def test_match_storage_preserves_decision_evidence_and_existing_market(self):
        stored = []
        class Query:
            def __init__(self, table):
                self.table, self.row = table, None
            def select(self, *_args): return self
            def eq(self, *_args): return self
            def insert(self, row):
                self.row = row
                return self
            def execute(self):
                if self.row is not None:
                    stored.append((self.table, self.row))
                    return SimpleNamespace(data=[dict(self.row, id=self.table + '-id')])
                if self.table == 'sections':
                    return SimpleNamespace(data=[dict(id='existing-market', name='Polk RT35i', make='Polk', model='RT35i')])
                return SimpleNamespace(data=[])
        db = SimpleNamespace(table=Query)
        result = self.evaluate(self.rows[0])
        buyer = dict(title='Polk RT35i', text=self.rows[0]['input']['buyer_text'], url='https://example.test/wtb', platform='forum')
        seller = dict(title='Polk RT35i', text=self.rows[0]['input']['seller_text'], url='https://example.test/item/1', availability='available', price=159.99, shipping=75)
        self.assertEqual(self.worker['push_match'](db, buyer, seller, result), ('matches-id', True))
        match = next(row for table, row in stored if table == 'matches')
        self.assertEqual(match['section_id'], 'existing-market')
        self.assertEqual(match['match_decision'], 'send')
        self.assertIsNone(match['confidence'])
        self.assertIn('Locked QQ2 assessment:', match['ai_reasoning'])
        self.assertIn('Validated disclosure:', match['ai_reasoning'])
        self.assertFalse(any(table == 'sections' for table, _ in stored))
