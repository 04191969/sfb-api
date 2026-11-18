"""Offline regression for the supplied cap trace; no network/model calls."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,os.environ['SFB_TEST_PACKAGE'])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'seeker'))
import automatic_run
from lead_navigation import classify
from public_search_adapter import PublicSearchBrowser
from browser_driver import Browser,Held,RunLimitReached
class Tests(unittest.TestCase):
    def browser(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        b=PublicSearchBrowser(Path(tmp.name));b.page=MagicMock();return b
    def test_trace_non_ads_rejected_before_navigation(self):
        for url,title in [('https://www.merriam-webster.com/dictionary/want','want'),('https://www.merriam-webster.com/dictionary/wanted','wanted'),('https://www.wtsinternational.org/events/ai-open-forum','AI Open Forum')]:
            self.assertEqual(classify(dict(url=url,title=title))[0],'skip')
    def test_directories_have_navigation_only_role(self):
        for url,title in [('https://www.classifiedads.com/','Classifieds - Free Classified Ads Online'),('https://www.facebook.com/marketplace/category/classifieds','Classifieds'),('https://forums.golfwrx.com/forum/94-wanted-to-buy-forum','Wanted to Buy Forum')]:
            self.assertEqual(classify(dict(url=url,title=title))[0],'directory')
    def test_real_ads_across_categories_remain_eligible(self):
        for url,title in [('https://forums.golfwrx.com/topic/2114992-wtb-putter','WTB: 2025 Makino Halloween Putter'),('https://example.com/thread/machine','WTS embroidery machine'),('https://www.facebook.com/marketplace/item/123','Bicycle'),('https://boston.craigslist.org/bik/d/bike/123456.html','Bike'),('https://example.com/product/book','English dictionary')]:
            self.assertEqual(classify(dict(url=url,title=title))[0],'ad')
    def test_content_unloaded_after_success(self):
        b=self.browser();capture={'source_text':'original'}
        with patch.object(Browser,'visit',return_value=capture):self.assertIs(b.visit('https://example.com','buyer'),capture)
        b.page.goto.assert_called_once_with('about:blank',wait_until='commit',timeout=5000)
    def test_content_unloaded_after_read_failure(self):
        b=self.browser()
        with patch.object(Browser,'visit',side_effect=Held('unreadable')):
            with self.assertRaises(Held):b.visit('https://example.com','buyer')
        b.page.goto.assert_called_once()
    def test_cleanup_failure_stops_not_background_leak(self):
        b=self.browser();b.page.goto.side_effect=RuntimeError('closed')
        with patch.object(Browser,'visit',return_value={}):
            with self.assertRaises(RunLimitReached):b.visit('https://example.com','buyer')
    def test_directory_documents_have_no_author_text_and_close_context(self):
        b=self.browser();b.browser=MagicMock();b.address=MagicMock()
        context=b.browser.new_context.return_value;p=context.new_page.return_value
        p.goto.return_value.status=200;p.url='https://example.com/classifieds';p.title.return_value='Classifieds'
        p.content.return_value='<a href="/item/123">Bicycle</a>'
        data=b.visit_directory(p.url)
        self.assertEqual(data['source_text'],'');self.assertFalse(data['single_listing'])
        self.assertEqual(data['links'][0]['url'],'https://example.com/item/123')
        self.assertFalse(b.browser.new_context.call_args.kwargs['java_script_enabled'])
        context.close.assert_called_once()
    def test_directory_context_closed_on_failure(self):
        b=self.browser();b.browser=MagicMock();b.address=MagicMock()
        c=b.browser.new_context.return_value;c.new_page.return_value.goto.side_effect=Held('blocked')
        with self.assertRaises(Held):b.visit_directory('https://example.com')
        c.close.assert_called_once()
    def test_navigation_subresources_blocked_without_spending(self):
        b=self.browser();route=MagicMock();route.request.resource_type='script'
        b.search_guard(route)
        self.assertEqual(b.dispatched,0);route.abort.assert_called_once()
    def test_cap_still_1200(self):
        b=self.browser();b.dispatched=1200
        with self.assertRaises(RunLimitReached):b.visit_directory('https://example.com')
if __name__=='__main__':unittest.main()
