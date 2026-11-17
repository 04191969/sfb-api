"""Refresh only the explicitly named component manifest after review/tests."""
import argparse,hashlib,json
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('component',choices=['live_matches','seeker']);parser.add_argument('release_id');args=parser.parse_args()
root=Path(__file__).resolve().parent/args.component
v2=root/('live_matches_v2' if args.component=='live_matches' else 'seeker_v2')
files={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
 for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='release.json'}
version=hashlib.sha256(b''.join((v2/name).read_bytes().replace(b'\r\n',b'\n') for name in ('request_template.json','reading_contract.py','shared_rules.py'))).hexdigest()
(root/'release.json').write_text(json.dumps({'component':args.component,'release_id':args.release_id,
 'baseline':'bea39775a007c5153dcedd5a241f1470a119c078','v2_version':version,'files':files},indent=2)+'\n',encoding='utf-8')
