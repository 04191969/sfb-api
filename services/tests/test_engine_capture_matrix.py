import importlib.util,json,tempfile,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
p=Path(__file__).parents[1]/'diagnostics'/'engine_capture_matrix.py'
s=importlib.util.spec_from_file_location('matrix',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Page:
 def __init__(self,fail):self.fail=fail;self.url='https://example.org/search'
 def set_default_timeout(self,*a):pass
 def set_default_navigation_timeout(self,*a):pass
 def on(self,*a):pass
 def goto(self,*a,**k):
  if self.fail:raise TimeoutError('navigation timeout')
  return SimpleNamespace(status=200)
 def locator(self,*a):return self
 def inner_text(self,**k):return 'Search page'
 def title(self):return 'Test page'
 def content(self):return '<html>Search page</html>'
 def screenshot(self,**k):raise TimeoutError('screenshot failure')
 def evaluate(self,*a):return [] if self.fail else [{'title':'WTB','url':'https://example.org/thread'}]
 def wait_for_function(self,*a,**k):pass
class Context:
 def __init__(self,fail):self.page=Page(fail)
 def new_page(self):return self.page
 def close(self):pass
class Browser:
 def __init__(self):self.n=0
 def new_context(self):self.n+=1;return Context(self.n==1)
 def close(self):pass
class Factory:
 def __enter__(self):return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **k:Browser()))
 def __exit__(self,*a):pass
class Tests(unittest.TestCase):
 def test_continues_and_retains_captures(self):
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out';r=m.run(out,[('One','https://one.example','q'),('Two','https://two.example','q')],Factory)
   self.assertEqual(len(r['attempts']),2)
   self.assertIn('navigation',r['attempts'][0]['errors'])
   self.assertIn('screenshot',r['attempts'][1]['errors'])
   self.assertEqual(r['attempts'][1]['state'],'candidate_links_need_review')
   self.assertEqual(r['state'],'captures_complete_not_qualification')
   with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip') as z:
    for n in ['01/page.html','01/CAPTURE.json','02/page.txt','02/CAPTURE.json','REPORT.json']:self.assertIn(n,z.namelist())
 def test_verification_not_success(self):self.assertEqual(m.classify('Verify you are human',200,[{}]),'verification_present_not_bypassed')
 def test_backend_failure_visible(self):self.assertEqual(m.classify('Qwant temporarily unavailable HTTP 403',200,[]),'page_reports_error')
 def test_http_error(self):self.assertEqual(m.classify('',403,[{}]),'http_error')
 def test_sanitize(self):self.assertEqual(m.safe_network_url('https://user:secret@a.test/path?token=secret'),'https://a.test/path')
 def test_engine_scope(self):
  self.assertEqual(len(m.ENGINES),20)
  self.assertEqual(len({x[0] for x in m.ENGINES}),20)
  self.assertFalse(any('api.' in x[1] for x in m.ENGINES))
if __name__=='__main__':unittest.main()
