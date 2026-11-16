import unittest
from types import SimpleNamespace
from unittest.mock import patch
try:
    from .test_seeker_worker_rules import load_worker
except ImportError:
    from test_seeker_worker_rules import load_worker


class DB:
    def __init__(self):
        self.rows = {'sections': [{'id': str(i), 'name': 'Market ' + str(i)} for i in range(4)],
                     'seeker_check_history': [], 'seeker_runs': [{'id': 'run'}]}
    def table(self, name):
        return Query(self, name)


class Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.payload, self.operation = db, name, [], None, 'select'
    def select(self, *args): return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args): return self
    def insert(self, payload): self.payload, self.operation = payload, 'insert'; return self
    def update(self, payload): self.payload, self.operation = payload, 'update'; return self
    def execute(self):
        rows = self.db.rows.setdefault(self.name, [])
        if self.operation == 'insert': rows.append(dict(self.payload)); return SimpleNamespace(data=[self.payload])
        chosen = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
        if self.operation == 'update':
            for row in chosen: row.update(self.payload)
        return SimpleNamespace(data=chosen)


class PublicCycleTests(unittest.TestCase):
    def setUp(self): self.w = load_worker(); self.db = DB()

    def test_blank_dispatch_zero_and_cursor_survives_next_run(self):
        visited = []
        def discover(sb, client, seen, market, report): visited.append(market['id'])
        with patch.dict(self.w, {'claim_next_run': lambda sb: {'id': 'run', 'search_query': None},
                                 'discover_public_market': discover}):
            self.w['check_manual_requests'](self.db, None, {})
            self.assertEqual(visited, ['0', '1', '2'])
            self.assertIn('0 new match(es)', self.db.rows['seeker_runs'][0]['result_summary'])
            self.assertIn('Cycle incomplete', self.db.rows['seeker_runs'][0]['result_summary'])
            self.assertEqual(len(self.db.rows['seeker_check_history']), 3)
            visited.clear()
            self.w['run_public_discovery_cycle'](self.db, None, {}, {'id': 'run'})
            self.assertEqual(visited[0], '3')

    def test_failure_is_recorded_and_other_markets_continue(self):
        def discover(sb, client, seen, market, report):
            if market['id'] == '0':
                report['new_matches'] = 1
                raise TimeoutError('source timed out after first match')
        with patch.dict(self.w, {'discover_public_market': discover}):
            self.w['run_public_discovery_cycle'](self.db, None, {}, {'id': 'run'})
        saved = self.db.rows['seeker_runs'][0]
        self.assertEqual(saved['new_matches'], 1)
        self.assertIn('source timed out', saved['result_summary'])
        self.assertEqual(len(self.db.rows['seeker_check_history']), 3)

    def test_aliases_share_cursor(self):
        rows = [{'id': 'a', 'make': 'Epiphone', 'model': 'USA Map'},
                {'id': 'b', 'make': 'Epiphone', 'model': 'USA Map guitar'},
                {'id': 'c', 'make': 'Fender', 'model': 'ST62G-SPL'}]
        result = self.w['public_cycle_markets'](rows, [{'market_id': 'b', 'checked_at': '2026-11-16T12:00:00Z'}])
        self.assertEqual([r['id'] for r in result], ['c', 'a'])

    def test_seller_first_finds_public_buyer_without_initial_wtb(self):
        buyer = {'url': 'https://forum.example/wtb', 'platform': 'forum',
                 'title': 'WTB Epiphone USA Map', 'text': 'WTB Epiphone USA Map'}
        offer = {'url': 'https://reverb.com/item/123', 'platform': 'reverb', 'title': 'Epiphone USA Map',
                 'text': 'Epiphone USA Map for sale', 'availability': 'available', 'qualifying': True,
                 'price': 850, 'shipping': 150}
        queries, saved = [], []
        def wtb(query, seen, diagnostics=None):
            queries.append(query)
            return [] if len(queries) == 1 else [buyer]
        def take(sb, mid, post, buyers, offers, seen, name): buyers.append(post)
        changes = {'load_market_records': lambda *a: ([], []), 'search_wtb_posts': wtb,
                   'search_listings': lambda *a, **kw: {'listings': [offer], 'incomplete': ''},
                   'buyer_queries_for_offers': lambda *a: (['Epiphone USA Map guitar'], False),
                   '_take_public_post': take, '_save_source': lambda *a: None,
                   'record_offer_observation': lambda *a: None,
                   'ai_evaluate_match': lambda *a: {'match': True, 'confidence': 100},
                   'push_match': lambda *a, **kw: saved.append(a[1:3]) or ('match', True)}
        with patch.dict(self.w, changes):
            result = self.w['discover_public_market'](self.db, None, {}, {'id': 'market', 'name': 'Epiphone USA Map'})
        self.assertEqual(result['new_matches'], 1)
        self.assertEqual(len(queries), 2)
        self.assertEqual(saved[0][0]['url'], buyer['url'])

    def test_sfb_buyer_not_counted_as_public(self):
        self.assertFalse(self.w['_public_cycle_buyer']({'url': 'https://searchfindbuy.local/requests/id', 'platform': 'sfb'}))
        self.assertTrue(self.w['_public_cycle_buyer']({'url': 'https://forum.example/wtb', 'platform': 'forum'}))

    def test_provider_error_is_not_silently_empty(self):
        class Broken:
            def text(self, *args, **kwargs): raise RuntimeError('provider blocked')
        errors = []
        with patch.dict(self.w, {'DDGS': Broken, 'SEARCH_DELAY': 0}):
            self.assertEqual(self.w['search_wtb_posts']('WTB', {}, diagnostics=errors), [])
        self.assertEqual(errors[0]['error'], 'provider blocked')

    def test_explicit_consumer_query_does_not_enter_public_cycle(self):
        def forbidden(*a): raise AssertionError('consumer entered public cycle')
        with patch.dict(self.w, {'claim_next_run': lambda sb: {'id': 'run', 'search_query': 'Nikon F3'},
                                 'run_public_discovery_cycle': forbidden,
                                 'search_wtb_posts': lambda *a: [],
                                 'search_listings': lambda *a, **kw: {'listings': [], 'incomplete': ''}}):
            self.assertTrue(self.w['check_manual_requests'](self.db, None, {}))
        self.assertIn('No qualifying match', self.db.rows['seeker_runs'][0]['result_summary'])
