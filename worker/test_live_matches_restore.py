import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock, patch
import live_matches_restore as restore


def dependencies():
    # No network or dependency installation in offline regression tests.
    result = {}
    for name, attr in [('supabase', 'create_client'), ('openai', 'OpenAI'), ('ddgs', 'DDGS'), ('httpx', 'Client')]:
        module = ModuleType(name)
        setattr(module, attr, MagicMock())
        result[name] = module
    return patch.dict('sys.modules', result)


def current_worker():
    p = Path(__file__).with_name('SFB_Seeker_v2_env.bat')
    scope = {'__name__': 'worker_test', '__file__': str(p)}
    with dependencies():
        exec(compile(p.read_text().split('\n', 1)[1], str(p), 'exec'), scope)
    return scope


class RestorationTests(unittest.TestCase):
    def test_exact_snapshot_and_historical_rules(self):
        with dependencies():
            baseline = restore.load_baseline()
        baseline['check_rules']()

    def test_original_model_payload_and_reservation_settlement(self):
        with dependencies():
            baseline = restore.load_baseline()
        baseline['_reserve_ai'] = MagicMock(return_value=('db', 'reservation'))
        baseline['_settle_ai'] = MagicMock()
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'match': True, 'confidence': 100})))])
        result = baseline['ai_evaluate_match']('Looking for: Barudan Profit 902T', 'Barudan Profit 902T for sale', client, object())
        self.assertTrue(result['match'])
        args = client.chat.completions.create.call_args.kwargs
        self.assertEqual(args['model'], 'gpt-4o-mini')
        self.assertIn('Barudan Profit 902T', args['messages'][0]['content'])
        baseline['_settle_ai'].assert_called_once()
        self.assertEqual(baseline['_settle_ai'].call_args.args[1:], ('reservation', 'succeeded'))

    def test_consumer_runs_entire_baseline_once_without_second_claim(self):
        with dependencies():
            baseline = restore.load_baseline()
        sb = MagicMock()
        seller = {'title': 'Barudan Profit 902T', 'text': 'Barudan Profit 902T for sale', 'url': 'https://www.facebook.com/marketplace/item/1368287185279175/', 'platform': 'facebook', 'qualifying': True, 'availability': 'available', 'price': None}
        baseline['search_wtb_posts'] = MagicMock(return_value=[])
        baseline['search_listings'] = MagicMock(return_value={'listings': [seller], 'incomplete': ''})
        baseline['ai_evaluate_match'] = MagicMock(return_value={'match': True, 'confidence': 100})
        baseline['push_match'] = MagicMock(return_value=('match', True))
        baseline['mark_seen'] = MagicMock()
        run = {'id': 'test', 'search_query': 'Barudan Profit 902T', 'job_kind': 'consumer_search'}
        with patch.object(restore, 'load_baseline', return_value=baseline):
            self.assertTrue(restore.run_claimed(sb, object(), {}, run))
        baseline['push_match'].assert_called_once()
        sb.rpc.assert_not_called()
        sb.table.return_value.update.assert_called_once()
        self.assertEqual(sb.table.return_value.update.call_args.args[0]['status'], 'completed')

    def test_no_blank_or_market_dispatch_to_historical_path(self):
        for run in ({'search_query': ''}, {'search_query': 'Barudan', 'job_kind': 'market_discovery'}, {'search_query': 'Barudan', 'job_kind': 'recheck'}):
            with self.assertRaises(ValueError):
                restore.run_claimed(None, None, {}, run)

    def test_worker_routing_and_no_double_claim(self):
        for kind, query, target in [('consumer_search', 'Barudan', 'restore'), ('consumer_search', '', 'five'), ('market_discovery', 'Barudan', 'market'), ('recheck', '', 'recheck')]:
            worker = current_worker()
            run = {'id': 'test', 'job_kind': kind, 'search_query': query}
            worker['claim_next_run'] = MagicMock(return_value=run)
            worker['run_market_discovery'] = MagicMock()
            worker['run_market_recheck'] = MagicMock()
            five = ModuleType('five_step_host')
            five.run_claimed = MagicMock()
            with patch.dict('sys.modules', {'five_step_host': five}), patch.object(restore, 'run_claimed', return_value=True) as old:
                self.assertTrue(worker['check_manual_requests'](MagicMock(), MagicMock(), {}))
                self.assertEqual(old.call_count, int(target == 'restore'))
                self.assertEqual(five.run_claimed.call_count, int(target == 'five'))
                self.assertEqual(worker['run_market_discovery'].call_count, int(target == 'market'))
                self.assertEqual(worker['run_market_recheck'].call_count, int(target == 'recheck'))
            worker['claim_next_run'].assert_called_once()

    def test_restoration_failure_is_recorded(self):
        worker = current_worker()
        worker['claim_next_run'] = MagicMock(return_value={'id': 'test', 'search_query': 'Barudan', 'job_kind': 'consumer_search'})
        sb = MagicMock()
        with patch.object(restore, 'run_claimed', side_effect=RuntimeError('baseline failed')):
            self.assertTrue(worker['check_manual_requests'](sb, MagicMock(), {}))
        self.assertEqual(sb.table.return_value.update.call_args.args[0]['status'], 'failed')

if __name__ == '__main__':
    unittest.main()
