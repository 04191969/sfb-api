import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Run with the pinned package on PYTHONPATH, never a modified copy.
from browser_driver import Browser, Held, SearchExtractionError
from public_search_adapter import PublicSearchBrowser

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.browser = PublicSearchBrowser(Path(self.tmp.name))

    def test_merge_query_and_capture_unchanged(self):
        urls = []
        def fetch(url):
            urls.append(url)
            if '/html/' in url:
                return '<div class="result"><a class="result__a" href="https://example.com/a">same</a></div><div class="result"><a class="result__a" href="https://example.com/b">other</a></div>'
            if 'mojeek.com' in url:
                return json.dumps({'response': {'results': [{'url':'https://example.com/c','title':'third','desc':'asking'}]}})
            return json.dumps({'results': [{'url':'https://example.com/a','title':'first','content':'wanted'}]})
        with patch.object(self.browser, 'fetch', side_effect=fetch), patch.object(self.browser, 'visit', side_effect=AssertionError('Search must not render')):
            rows = self.browser.search('"USA Map" $850 plus shipping')
        from urllib.parse import parse_qs, urlsplit
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(parse_qs(urlsplit(u).query)['q'] == ['"USA Map" $850 plus shipping'] for u in urls))
        self.assertIs(PublicSearchBrowser.visit, Browser.visit)
        self.assertTrue(all(r['query'] == '"USA Map" $850 plus shipping' for r in rows))

    def test_failure_keeps_other_engine_results(self):
        def fetch(url):
            if 'mojeek.com' in url:
                return json.dumps({'response':{'results':[{'url':'https://example.com/item','title':'item'}]}})
            raise Held('HTTP 429')
        with patch.object(self.browser, 'fetch', side_effect=fetch):
            self.assertEqual(len(self.browser.search('same query')), 1)
        self.assertEqual(len(self.browser.trace), 5)

    def test_all_fail_is_not_no_matches(self):
        with patch.object(self.browser, 'fetch', side_effect=Held('HTTP 403')):
            with self.assertRaises(SearchExtractionError):
                self.browser.search('WTB')

    def test_mixed_empty_and_failure_is_incomplete(self):
        def fetch(url):
            if 'mojeek.com' in url: return '{"response":{"results":[]}}'
            raise Held('Timeout')
        with patch.object(self.browser, 'fetch', side_effect=fetch):
            with self.assertRaises(SearchExtractionError): self.browser.search('WTB')

    def test_budget_not_raised(self):
        self.browser.dispatched = 1200
        from browser_driver import RunLimitReached
        with self.assertRaises(RunLimitReached): self.browser.search('WTB')

if __name__ == '__main__': unittest.main()
