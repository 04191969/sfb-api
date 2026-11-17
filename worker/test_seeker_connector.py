import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from seeker_connector import connect_run, UnsupportedRequest, verify_package

ID='00000000-0000-4000-8000-000000000001'
class Locked:
    def validate_answer(self, answer, facts):
        if answer.get('decision') not in ('Send','No Send'): raise ValueError('Invalid')
        return {'Decision':answer['decision']}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.saved=[];self.finished=[]
        self.job={'id':ID,'status':'running','search_query':None}
        self.state='completed';self.tamper=False;self.crash=False
    def tearDown(self): self.tmp.cleanup()
    def factory(self, folder, **opts):
        outer=self
        class Runner:
            def execute(self, **kw):
                if outer.crash: raise RuntimeError('secret must not be persisted')
                pair={s:{'author_text':s,'url':'https://example.com/'+s,
                         'text_sha256':hashlib.sha256(s.encode()).hexdigest()} for s in ('buyer','seller')}
                answer={'decision':'Send'}
                data={'pair':pair,'matcher_answer':answer,'validated':{'Decision':'Send'}}
                report={'state':outer.state,'reason':'No readable buyer','pair':pair,'matcher_answer':answer}
                if outer.tamper: data['validated']={'Decision':'No Send'}
                Path(folder).mkdir();(Path(folder)/'HANDOFF_INPUT.json').write_text(json.dumps(data))
                return report
        return Runner()
    def run_bridge(self):
        return connect_run(self.job,self.root,self.factory,'unused','1.00',
                           lambda *x:self.saved.append(x),lambda *x:self.finished.append(x),Locked())
    def test_bound_result(self):
        self.assertTrue(self.run_bridge()['published']);self.assertEqual(self.saved[0][0],ID)
        self.assertIsNone(self.saved[0][1]['confidence'])
    def test_other_handoff_ignored(self):
        (self.root/'HANDOFF_INPUT.json').write_text('unrelated')
        self.assertTrue(self.run_bridge()['published'])
    def test_stopped_does_not_publish_even_with_handoff(self):
        self.state='site_blocked';self.assertFalse(self.run_bridge()['published']);self.assertFalse(self.saved)
    def test_empty_result_not_failure(self):
        self.state='no_wtb_leads';self.run_bridge();self.assertEqual(self.finished[0][1],'completed')
    def test_stopped_run_is_not_completed(self):
        self.state='stopped';self.run_bridge();self.assertEqual(self.finished[0][1],'failed')
    def test_blocked_run_is_not_completed(self):
        self.state='site_blocked';self.run_bridge();self.assertEqual(self.finished[0][1],'failed')
    def test_invalid_validation(self):
        self.tamper=True
        with self.assertRaises(ValueError): self.run_bridge()
        self.assertFalse(self.saved);self.assertEqual(self.finished[-1][1],'failed')
    def test_exception_finishes(self):
        self.crash=True
        with self.assertRaises(RuntimeError):self.run_bridge()
        self.assertNotIn('secret',str(self.finished));self.assertEqual(self.finished[-1][1],'failed')
    def test_consumer_query_not_ignored(self):
        self.job['search_query']='Epiphone $850'
        with self.assertRaises(UnsupportedRequest):self.run_bridge()
        self.assertFalse(self.finished)
    def test_market_not_ignored(self):
        self.job['market_id']='market'
        with self.assertRaises(UnsupportedRequest):self.run_bridge()
    def test_no_duplicate_execution(self):
        self.run_bridge()
        with self.assertRaises(ValueError):self.run_bridge()
        self.assertEqual(len(self.saved),1)
    def test_package_partial_manifest_rejected(self):
        (self.root/'automatic_run.py').write_text('x');(self.root/'other.py').write_text('y')
        with self.assertRaises(ValueError):verify_package(self.root,{'automatic_run.py':hashlib.sha256(b'x').hexdigest()})

if __name__=='__main__':unittest.main()
