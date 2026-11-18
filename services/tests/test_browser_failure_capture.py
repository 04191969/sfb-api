import sys, unittest, tempfile, json, zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'diagnostics'))
import browser_buyer_proof as m
class Page:
    url='https://www.qwant.com/?q=test'
    def __init__(self, screenshot_error=False): self.visits=[];self.screenshot_error=screenshot_error;self.first=self
    def set_default_timeout(self,*a):pass
    def set_default_navigation_timeout(self,*a):pass
    def on(self,*a):pass
    def goto(self,url,**kw):self.visits.append(url);return SimpleNamespace(status=200)
    def title(self):return 'Search shell'
    def content(self):return '<html><body>Loading search results…</body></html>'
    def locator(self,*a):return self
    def inner_text(self,**kw):return 'Loading search results…'
    def get_by_role(self,*a,**kw):return self
    def click(self):pass
    def wait_for(self,**kw):raise TimeoutError('No result heading')
    def screenshot(self,path,**kw):
        if self.screenshot_error: raise RuntimeError('Screenshot unavailable')
        Path(path).write_bytes(b'test screenshot')
class Context:
    def __init__(self,page):self.page=page
    def __enter__(self):return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw:SimpleNamespace(new_context=lambda:SimpleNamespace(new_page=lambda:self.page))))
    def __exit__(self,*a):pass
class Tests(unittest.TestCase):
    def test_first_failure_captured_and_other_searches_not_run(self):
        page=Page()
        with tempfile.TemporaryDirectory() as d, patch('playwright.sync_api.sync_playwright',return_value=Context(page)):
            out=Path(d)/'out';report=m.run(out,diagnose_first=True)
            self.assertEqual(len(page.visits),1);self.assertEqual(len(report['attempts']),1)
            self.assertEqual(report['pages'],[]);self.assertEqual(report['state'],'incomplete')
            cap=json.loads((out/'first-failure/CAPTURE.json').read_text())
            self.assertEqual(cap['navigation_status'],200);self.assertEqual(cap['title'],'Search shell')
            self.assertEqual(cap['url'],page.url)
            with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip') as z:
                self.assertTrue({'REPORT.json','STATE.json','first-failure/page.html','first-failure/page.txt','first-failure/page.png','first-failure/CAPTURE.json'} <= set(z.namelist()))
    def test_screenshot_failure_does_not_lose_html_status_or_error(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);r=m.capture_page(Page(True),out,{'navigation_status':429},[])
            self.assertIn('screenshot',r['capture_errors']);self.assertEqual(r['navigation_status'],429)
            self.assertTrue((out/'first-failure/page.html').exists());self.assertTrue((out/'first-failure/page.txt').exists())
    def test_network_excludes_query_credentials_and_headers(self):
        r=m.network_response(SimpleNamespace(url='https://search.example/results?token=secret',status=503,request=SimpleNamespace(resource_type='fetch')))
        self.assertEqual(r,{'url':'https://search.example/results','status':503,'resource_type':'fetch'})
if __name__=='__main__':unittest.main()
