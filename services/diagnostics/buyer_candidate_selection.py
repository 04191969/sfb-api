"""Navigation ranking only. No product/category restriction or qualification."""
import json,re
from pathlib import Path
from urllib.parse import urlsplit
META=re.compile(r'\b(?:bumping (?:a |my |the )?wtb post|how to (?:post|write|place) (?:a |an )?(?:wtb|wanted)|(?:wtb|wanted)(?: ad| post)? (?:rules|guidelines)|posting (?:rules|guidelines))\b',re.I)
def selection(entry):
 u=entry['url'];p=urlsplit(u);title=entry.get('title','')
 if META.search(title+' '+p.path.replace('-',' ')):return 'exclude','Discussion about posting wanted ads'
 if '/article/' in p.path or '/articles/' in p.path:return 'exclude','Editorial article URL, not a classified or forum post'
 direct=bool(re.search(r'/(?:threads?|topics?|post|comments)/|[?&]num=|/[^/?]+-\d{5,}/?$',u,re.I))
 explicit=bool(re.search(r'\b(?:wtb|wanted|want to buy|looking to buy|in search of)\b',title,re.I))
 return ('wanted_thread' if explicit else 'thread','Individual-thread URL; original text still requires reading') if direct else ('other','Retained for exploration')
def order(state,report):
 rows=[]
 for e in state.get('pending',[]):
  kind,why=selection(e)
  if kind=='exclude':report.setdefault('selection_skips',[]).append(dict(url=e['url'],reason=why))
  else:rows.append(({'wanted_thread':0,'thread':1,'other':2}[kind],e))
 state['pending']=[e for rank,e in sorted(rows,key=lambda x:x[0])]

def bootstrap(home,path):
 if path.exists():return
 state={'boards':[],'board_cursor':0,'board_route':{'visited':[],'pending':[]},'broad_route':{'visited':[],'pending':[]}}
 files=list(home.glob('SFB_Combined_Buyer_Proof_*/STATE.json'))+list(home.glob('SFB_Recent_Buyer_Proof_*/STATE.json'))
 catalog={}
 for f in sorted(files,key=lambda x:x.stat().st_mtime):
  data=json.loads(f.read_text(encoding='utf-8-sig'));report=f.with_name('REPORT.json')
  if report.exists():
   for search in json.loads(report.read_text(encoding='utf-8-sig')).get('searches',[]):
    for hit in search.get('extracted_results',[]):catalog[hit['url']]=hit.get('title','')
  state['board_cursor']=data.get('board_cursor',0)
  for b in data.get('boards',[]):
   if b not in state['boards']:state['boards'].append(b)
  for route in ['board_route','broad_route']:
   part=data.get(route,{})
   state[route]['visited']=sorted(set(state[route]['visited'])|set(part.get('visited',[])))
   queued={e['url'] for e in state[route]['pending']}
   for e in part.get('pending',[]):
    if e['url'] not in queued:state[route]['pending'].append(e);queued.add(e['url'])
 if not files:return
 allvisited=set(state['board_route']['visited'])|set(state['broad_route']['visited'])
 for route in ['board_route','broad_route']:
  state[route]['pending']=[dict(e,title=e.get('title') or catalog.get(e['url'],'')) for e in state[route]['pending'] if e['url'] not in allvisited]
 state['restored_from_reports']=len(files)
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(state,indent=2),encoding='utf-8')
