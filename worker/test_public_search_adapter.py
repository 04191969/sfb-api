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
            rows = self.browser.public_engine_search('"USA Map" $850 plus shipping')
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
            self.assertEqual(len(self.browser.public_engine_search('same query')), 1)
        self.assertEqual(len(self.browser.trace), 5)

    def test_all_fail_is_not_no_matches(self):
        with patch.object(self.browser, 'fetch', side_effect=Held('HTTP 403')):
            with self.assertRaises(SearchExtractionError):
                self.browser.public_engine_search('WTB')

    def test_mixed_empty_and_failure_is_incomplete(self):
        def fetch(url):
            if 'mojeek.com' in url: return '{"response":{"results":[]}}'
            raise Held('Timeout')
        with patch.object(self.browser, 'fetch', side_effect=fetch):
            with self.assertRaises(SearchExtractionError): self.browser.public_engine_search('WTB')

    def test_budget_not_raised(self):
        self.browser.dispatched = 1200
        from browser_driver import RunLimitReached
        with self.assertRaises(RunLimitReached): self.browser.public_engine_search('WTB')


class BingRepairTests(AdapterTests):
    def test_bing_exact_query_and_source(self):
        html = '<ol id="b_results"><li class="b_algo"><h2><a href="https://forum.example.com/wtb">WTB speakers</a></h2><div class="b_caption"><p>Original snippet</p></div></li></ol>'
        rows = self.browser.parse_bing(html, 'wanted WTB camera photography classifieds', 'https://www.bing.com/search?q=x')
        self.assertEqual(rows[0]['query'], 'wanted WTB camera photography classifieds')
        self.assertEqual(rows[0]['url'], 'https://forum.example.com/wtb')
        self.assertEqual(rows[0]['snippet'], 'Original snippet')

    def test_bing_is_primary_not_failed_engine_roster(self):
        with patch.object(self.browser, 'bing_document_search', return_value=[{'url':'https://example.com/ad'}]) as bing, patch.object(self.browser, 'public_engine_search') as other:
            self.assertEqual(len(self.browser.search('unchanged query')), 1)
            bing.assert_called_once_with('unchanged query')
            other.assert_not_called()

    def test_bing_failure_reaches_other_engines(self):
        with patch.object(self.browser, 'bing_document_search', side_effect=Held('HTTP 429')), patch.object(self.browser, 'public_engine_search', return_value=[]) as other:
            self.browser.search('unchanged query')
            other.assert_called_once_with('unchanged query')

    def test_subresources_never_dispatch(self):
        from unittest.mock import Mock
        for kind in ('script', 'stylesheet', 'xhr', 'fetch', 'image', 'font'):
            route = Mock()
            route.request.resource_type = kind
            self.browser.search_guard(route)
            route.abort.assert_called_once()
            route.continue_.assert_not_called()
        self.assertEqual(self.browser.dispatched, 0)
        self.assertEqual(self.browser.blocked_reasons['search_subresource'], 6)

    def test_document_consumes_existing_allowance(self):
        from unittest.mock import Mock
        route = Mock()
        route.request.resource_type = 'document'
        route.request.method = 'GET'
        route.request.url = 'https://www.bing.com/search?q=WTB'
        with patch.object(self.browser, 'address'):
            self.browser.search_guard(route)
        self.assertEqual(self.browser.dispatched, 1)
        route.continue_.assert_called_once()

    def test_listing_capture_unchanged(self):
        self.assertIs(PublicSearchBrowser.visit, Browser.visit)
        self.assertIs(PublicSearchBrowser.guard, Browser.guard)

    def test_search_context_closed_without_touching_listing_context(self):
        from unittest.mock import Mock
        browser = Mock()
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = 'https://www.bing.com/search?q=WTB'
        page.goto.return_value.status = 200
        page.title.return_value = 'WTB - Bing'
        page.content.return_value = '<ol id="b_results"><li class="b_algo"><h2><a href="https://example.com/ad">Wanted</a></h2></li></ol>'
        self.browser.browser = browser
        listing_page = Mock()
        self.browser.page = listing_page
        self.assertEqual(len(self.browser.search('WTB')), 1)
        browser.new_context.assert_called_once_with(java_script_enabled=False, accept_downloads=False, service_workers='block')
        context.close.assert_called_once()
        listing_page.goto.assert_not_called()
        self.assertEqual(self.browser.trace[0]['state'], 'captured')

if __name__ == '__main__': unittest.main()
