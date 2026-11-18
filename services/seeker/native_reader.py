"""Seeker-owned copy of the proven listing reader; no sibling imports."""
import ast
import hashlib
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASELINE_SHA='81bf6455dab7eef094d443c6dd75bdedc559f731f0df80342282ba2a9a1be75c'

def load(state):
    path=ROOT/'reader_baseline.bat'
    raw=path.read_bytes().replace(b'\r\n',b'\n')
    if hashlib.sha256(raw).hexdigest()!=BASELINE_SHA:
        raise ValueError('Seeker reader baseline differs')
    tree=ast.parse(raw.decode('utf-8').split('\n',1)[1],filename=str(path))
    # Dependency installation is not part of a reader call.
    tree.body=[n for n in tree.body if not (isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='install_deps')]
    scope={'__name__':'seeker_reader','__file__':str(path)}
    exec(compile(tree,str(path),'exec'),scope)
    scope.update(SEEN_FILE=str(Path(state)/'seen.json'),USAGE_FILE=str(Path(state)/'usage.json'),AUTO_SEARCH=False)
    from reader_discovery import install as discovery
    from reader_currency import install as currency
    from seeker_v2.adapter import install as matcher
    discovery(scope);currency(scope);matcher(scope)
    from discovery_reader.adapter import read
    scope['read_discovery']=lambda source,direction,client,sb,audit:read(source,direction,client,sb,scope,audit)
    return scope
