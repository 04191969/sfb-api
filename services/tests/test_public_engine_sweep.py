import importlib.util, pathlib, unittest
p=pathlib.Path(__file__).parents[1]/'diagnostics/public_engine_sweep.py'
s=importlib.util.spec_from_file_location('sweep',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class SweepTests(unittest.TestCase):
 def test_broad_queries(self):
  self.assertEqual(len(m.QUERIES),5)
  self.assertTrue(all('Fender' not in q and 'camera' not in q for q in m.QUERIES))
 def test_google_bing_direct(self):
  self.assertEqual(m.ENGINES['Google'][0],'https://www.google.com/search')
  self.assertEqual(m.ENGINES['Bing'][0],'https://www.bing.com/search')
 def test_navigation_not_counted_as_verified_result(self):
  r=m.extract('<title>Challenge</title><a href="https://help.test/">Help</a>','https://search.test/')
  self.assertEqual(r['title'],'Challenge');self.assertNotIn('results',r)
 def test_search_query_is_not_destination(self):
  r=m.extract('<a href="/search?q=hello">Search</a>','https://search.test/')
  self.assertEqual(r['external_links'],[])
 def test_explicit_result_redirect(self):
  r=m.extract('<a href="/url?q=https%3A%2F%2Fforum.test%2Fthread">WTB</a>','https://search.test/')
  self.assertEqual(r['external_links'][0]['url'],'https://forum.test/thread')
if __name__=='__main__':unittest.main()
