"""Read-only preflight. Never changes jobs or credentials."""
import hashlib,json,os,sys
from pathlib import Path
root=Path(os.environ['SFB_INSTALL_ROOT'])
for component in ('live_matches','seeker'):
 folder=root/component
 release=json.loads((folder/'release.json').read_text(encoding='utf-8'))
 for name,expected in release['files'].items():
  assert hashlib.sha256((folder/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==expected, name
sys.path.insert(0,str(root/'seeker'))
from seeker_connector import verify_package
verify_package(os.environ['SFB_INSTALL_PACKAGE'],json.loads((root/'seeker/five_step_package_hashes.json').read_text()))
from supabase import create_client
sb=create_client('https://fyxglozykvhrfejrcrqz.supabase.co',os.environ['SUPABASE_KEY'])
rows=sb.table('sfb_component_control').select('*').execute().data
assert {r['component'] for r in rows}=={'live_matches','seeker'}, 'Isolation migration missing'
assert not any(r['enabled'] for r in rows), 'Components already activated; use their independent controls'
assert not sb.table('seeker_runs').select('id').in_('status',['pending','running']).limit(1).execute().data,'A job is pending/running. Nothing stopped.'
print('Both release hashes, Seeker package, isolation schema and idle queue verified.')
