import importlib.util
import re
import hashlib
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
