import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parent

class IsolationTests(unittest.TestCase):
 def test_live_algorithm_and_v2_are_exact_working_baseline(self):
  names=['SFB_Seeker_v2_env.bat','live_matches_f972a58.bat','live_matches_restore.py','live_matches_discovery.py','live_matches_currency.py']
  names += ['live_matches_v2/'+p.name for p in (REPO/'worker/live_matches_v2').iterdir() if p.is_file()]
  for name in names:
   expected=subprocess.check_output(['git','show','bea39775a007c5153dcedd5a241f1470a119c078:worker/'+name],cwd=REPO)
   self.assertEqual((ROOT/'live_matches'/name).read_bytes(),expected,name)
 def test_v2_are_separate_identical_physical_files(self):
  for name in ('request_template.json','reading_contract.py','shared_rules.py','INTEGRITY.json','adapter.py'):
   a=ROOT/'live_matches/live_matches_v2'/name;b=ROOT/'seeker/seeker_v2'/name
   self.assertEqual(a.read_bytes(),b.read_bytes())
   self.assertNotEqual(a.stat().st_ino,b.stat().st_ino)
 def test_manifests_cover_files_and_independent_release_ids(self):
  ids=[]
  for component in ('live_matches','seeker'):
   root=ROOT/component;r=json.loads((root/'release.json').read_text());ids.append(r['release_id'])
   for name,digest in r['files'].items():self.assertEqual(hashlib.sha256((root/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest(),digest,name)
   actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='release.json'}
   self.assertEqual(actual,set(r['files']))
  self.assertEqual(len(set(ids)),2)
 def test_queue_predicates_partition_every_job(self):
  jobs=[{'job_kind':'consumer_search','search_query':'Fender'}, {'job_kind':'consumer_search','search_query':' '},
        {'job_kind':'market_discovery','search_query':'Fender','market_id':'x'}, {'job_kind':'recheck','search_query':'Fender'},
        {'job_kind':'public_discovery'}, {'search_query':'Epiphone'}, {'search_query':None}]
  outputs=[]
  for component in ('live_matches','seeker'):
   code='import runtime_support,json;print(json.dumps([runtime_support.belongs(x) for x in '+repr(jobs)+']))'
   outputs.append(json.loads(subprocess.check_output([sys.executable,'-c',code],cwd=ROOT/component)))
  self.assertEqual(outputs[0],[True,False,False,False,False,True,False])
  self.assertTrue(all(a!=b for a,b in zip(*outputs)))
 def test_seeker_bridge_uses_own_v2_exact_payload_and_validation(self):
  code="""
import sys,json
sys.path.insert(0,sys.argv[1])
import test_live_matches_v2 as fixtures
import v2_bridge
from seeker_v2.adapter import template
pair={'buyer_text':fixtures.BUYER,'seller_text':fixtures.PAGE}
body=v2_bridge.payload(pair)
assert body==dict(template(),input=json.dumps(pair,ensure_ascii=False))
r=fixtures.reading();answer=v2_bridge.wrap(r,pair)
assert answer['decision']=='Send'
assert v2_bridge.validate_answer(answer,pair)['Decision']=='Send'
answer['decision']='Reject'
try:v2_bridge.validate_answer(answer,pair)
except ValueError:pass
else:raise AssertionError('tampering accepted')
assert not any('live_matches_v2' in name for name in sys.modules)
"""
  # Fixtures import Live Matches modules, so inspect component imports separately below.
  code=code.replace("assert not any('live_matches_v2' in name for name in sys.modules)","")
  subprocess.run([sys.executable,'-c',code,str(REPO/'worker')],cwd=ROOT/'seeker',check=True)
 def test_seeker_has_no_cross_component_imports(self):
  code="import v2_bridge,sys;assert not any(n.startswith('live_matches_v2') for n in sys.modules)"
  subprocess.run([sys.executable,'-c',code],cwd=ROOT/'seeker',check=True)
 def test_mutating_seeker_does_not_modify_live_manifest(self):
  with tempfile.TemporaryDirectory() as tmp:
   import shutil
   path=Path(tmp);shutil.copytree(ROOT/'seeker',path/'seeker');shutil.copytree(ROOT/'live_matches',path/'live_matches')
   before=(path/'live_matches/live_matches_v2/request_template.json').read_bytes()
   (path/'seeker/seeker_v2/request_template.json').write_text('{}')
   self.assertEqual((path/'live_matches/live_matches_v2/request_template.json').read_bytes(),before)
   result=subprocess.run([sys.executable,'-c','import runtime;runtime.load_release()'],cwd=path/'seeker',capture_output=True)
   self.assertNotEqual(result.returncode,0)
   subprocess.run([sys.executable,'-c','import runtime;runtime.load_release()'],cwd=path/'live_matches',check=True)

if __name__=='__main__':unittest.main()
