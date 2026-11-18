import importlib.util
import re
import hashlib
import os
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'seeker'))
spec=importlib.util.spec_from_file_location('probe',Path(__file__).with_name('seller_discovery_probe.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class RunLimitReached(Exception):pass
class ProbeTests(unittest.TestCase):
    def test_component_hash_accepts_lf_and_windows_crlf(self):
        source=b"x = 1\nprint(x)\n"
        manifest={'files':{'example.py':hashlib.sha256(source).hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for content in (source,source.replace(b'\n',b'\r\n')):
                (root/'example.py').write_bytes(content)
                p.verify_component(root,manifest)
    def test_component_hash_rejects_real_edit(self):
        source=b"x = 1\n"
        manifest={'files':{'example.py':hashlib.sha256(source).hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'example.py').write_bytes(b"x = 2\r\n")
            with self.assertRaisesRegex(ValueError,'Component integrity mismatch'):
                p.verify_component(root,manifest)

    def test_ddgs_adapter_preserves_each_result_url(self):
        raw=[dict(href='https://seller.example/item/1',title='guitar',body='search excerpt')]
        calls=[]
        def factory(**kwargs):
            self.assertEqual(kwargs,{'timeout':15})
            return SimpleNamespace(text=lambda query,**kw:calls.append((query,kw)) or raw)
        saved,rows=p.ddgs_search('guitar',factory)
        self.assertEqual(saved,raw)
        self.assertEqual(rows[0]['url'],raw[0]['href'])
        self.assertEqual(calls,[('guitar',{'max_results':10})])
    def test_ddgs_handoff_uses_page_text_not_snippet(self):
        rows=[dict(url='https://seller.example/item/1',title='guitar',snippet='combined search text')]
        calls=[]
        def visit(url,purpose,parent):
            calls.append((url,purpose,parent))
            return dict(author_text='guitar original seller text')
        browser=p.SearchRowsBrowser(SimpleNamespace(visit=visit),'guitar',rows)
        r=p.run_case(browser,'guitar',lambda r:('ad','test'),lambda s:set(s.split()),lambda page,side:page,re.compile('WTB'))
        self.assertEqual(r['opened'][0]['source']['author_text'],'guitar original seller text')
        self.assertEqual(calls,[('https://seller.example/item/1','seller',None)])
    def test_ddgs_handoff_rejects_wrong_query(self):
        browser=p.SearchRowsBrowser(None,'guitar',[])
        with self.assertRaises(ValueError):browser.search('different')
    @unittest.skipUnless(os.environ.get('SFB_TEST_PACKAGE'), 'Pinned package required')
    def test_ddgs_to_real_source_validator(self):
        sys.path.insert(0,os.environ['SFB_TEST_PACKAGE'])
        from automatic_run import record,tokens,WANTED
        from lead_navigation import classify
        url='https://seller.example/products/ventus-6s'
        rows=[dict(url=url,title='Ventus Blue Velocore 6S shaft for sale',snippet='Snippet must not become evidence')]
        text='Ventus Blue Velocore 6S shaft for sale. Seller offers this used golf shaft with a Titleist adapter. Asking $150.'
        page=dict(url=url,title=rows[0]['title'],source_text=text,capture_id='fixture',extraction='single_article',single_listing=True,product=True,links=[])
        browser=p.SearchRowsBrowser(SimpleNamespace(visit=lambda *a:page),'Ventus Blue Velocore 6S shaft for sale',rows)
        result=p.run_case(browser,browser.query,classify,tokens,record,WANTED)
        self.assertEqual(result['opened'][0]['source']['author_text'],text)
        self.assertEqual(result['opened'][0]['source']['availability'],'unconfirmed')
        page['single_listing']=False
        result=p.run_case(browser,browser.query,classify,tokens,record,WANTED)
        self.assertEqual(result['state'],'no_readable_source')
        self.assertIn('Unbound source',result['opened'][0]['error'])
    def run_probe(self, rows, visit):
        browser=SimpleNamespace(search=lambda q:rows,visit=visit)
        return p.run_case(browser,'guitar',lambda r:('ad','test'),lambda s:set(s.split()),lambda page,side:page,re.compile('WTB'))
    def test_empty_is_not_success(self):
        r=self.run_probe([],lambda *a:None)
        self.assertEqual(r['state'],'no_readable_source')
    def test_retrieved_source_is_not_a_match(self):
        r=self.run_probe([dict(url='https://a.example/item/1',title='guitar')],lambda *a:dict(author_text='guitar'))
        self.assertEqual(r['state'],'evidence_ready_for_review')
        self.assertEqual(r['opened'][0]['state'],'readable_source_requires_review')
        self.assertNotIn('decision',r)
    def test_limit_preserved(self):
        calls=[]
        def visit(*args):calls.append(args);raise RunLimitReached('limit')
        r=self.run_probe([dict(url='https://a.example/item/'+str(i),title='guitar') for i in range(3)],visit)
        self.assertEqual(len(calls),1)
        self.assertEqual(r['state'],'incomplete')
    def test_nine_openings_maximum(self):
        r=self.run_probe([dict(url='https://a.example/item/'+str(i),title='guitar') for i in range(12)],lambda *a:dict(author_text='guitar'))
        self.assertEqual(len(r['opened']),9)
        self.assertEqual(r['unopened_candidates'],3)
    def test_blocked_page_retains_error(self):
        def visit(*a):raise ValueError('HTTP 403')
        r=self.run_probe([dict(url='https://a.example/item/1',title='guitar')],visit)
        self.assertEqual(r['state'],'no_readable_source')
        self.assertIn('HTTP 403',r['opened'][0]['error'])
if __name__=='__main__':unittest.main()
