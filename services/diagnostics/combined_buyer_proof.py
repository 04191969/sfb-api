"""Isolated broad-search + rotating-board evidence probe. No production imports."""
import argparse,json,hashlib,time,zipfile,re
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from bs4 import BeautifulSoup
from engine_capture_matrix import probe
from buyer_candidate_selection import order
from buyer_discovery_steps12 import walk,canonical,BUY
QUERIES=['"WTB" forum','"want to buy" classifieds','"looking to buy" forum']
ENGINES=[('OceanHero','https://oceanhero.today/web','q'),('Daum','https://search.daum.net/search','q')]
# WTB boards observed in the user's captured broad search, not fixed product searches.
INITIAL_BOARDS=['https://forums.golfwrx.com/forum/94-wanted-to-buy-forum/','https://www.performancetrucks.net/forums/wtb-wanted-buy-211/','https://www.watchforum.com/forums/want-to-buy-watches-parts-accessories-wtb.3/']
def links(markup,engine):
 s=BeautifulSoup(markup,'html.parser');rows=[];seen=set()
 selector='a[data-ph-result][href],a:has(h3)' if engine=='OceanHero' else 'c-title a[href],.item-title a[href],.tit-g a[href]'
 for a in s.select(selector):
  u=a.get('href','');t=a.get_text(' ',strip=True);p=urlsplit(u)
  if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:continue
  if p.hostname.endswith(('daum.net','oceanhero.today')) or u in seen:continue
  seen.add(u);rows.append({'url':canonical(u),'title':t})
 return rows

def prepare(state,seeds,selected):
 # Revisit selected board indexes for new threads; retain already-read thread IDs.
 visited=set(state.get('visited',[]))-set(selected)
 pending=state.get('pending',[]);queued={canonical(x['url']) for x in pending}
 for u in list(selected)+list(seeds):
  u=canonical(u)
  if u not in visited and u not in queued:
   pending.append({'url':u,'kind':'directory','origin':u});queued.add(u)
 state.update(pending=pending,visited=sorted(visited))

def prioritize_direct(state):
 # URL shape selects navigation order only; it never establishes buyer status.
 def direct(e):
  u=e['url']
  return e.get('kind')=='post' or bool(re.search(r'/(?:threads?|topics?|post|comments)/|[?&]num=|/[^/?]+-\d{5,}/?$',u,re.I))
 pending=state.get('pending',[])
 state['pending']=[e for e in pending if direct(e)]+[e for e in pending if not direct(e)]

def rotate(state,count=2):
 boards=state.setdefault('boards',list(INITIAL_BOARDS));offset=state.get('board_cursor',0)
 picked=[boards[(offset+i)%len(boards)] for i in range(min(count,len(boards)))] if boards else []
 state['board_cursor']=(offset+len(picked))%len(boards) if boards else 0
 return picked

def execute(out,state_path):
 from playwright.sync_api import sync_playwright
 import engine_capture_matrix as capture
 out.mkdir(parents=True,exist_ok=False)
 state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
 selected=rotate(state)
 report=dict(state='running',searches=[],pages=[],buyers=[],failures=[],selected_boards=selected,model_calls=0,database_writes=0,production_changed=False,limits=f'{len(QUERIES)*len(ENGINES)} searches; six board-route and six broad-route source visits. Original posts are candidates, not confirmed current buyers.')
 def save():
  (out/'REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
  (out/'STATE.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
  state_path.parent.mkdir(parents=True,exist_ok=True)
  tmp=state_path.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2),encoding='utf-8');tmp.replace(state_path)
 save()
 try:
  with sync_playwright() as p:
   browser=p.chromium.launch(channel='msedge',headless=True)
   try:
    def fetch(url):
     print('Opening '+url,flush=True)
     ctx=browser.new_context();page=ctx.new_page()
     try:
      r=page.goto(url,wait_until='domcontentloaded',timeout=12000)
      # Bounded DOM polling, without unsafe-eval or automatic reloads.
      end=time.monotonic()+4
      while time.monotonic()<end:
       if page.locator('h1').count():break
       page.wait_for_timeout(250)
      result={'status':r.status if r else 0,'url':page.url,'html':page.content()}
      f=out/'sources';f.mkdir(exist_ok=True)
      (f/(hashlib.sha256(url.encode()).hexdigest()+'.json')).write_text(json.dumps(result),encoding='utf-8')
      return result
     finally:ctx.close()
    # Each route has its own queue allowance so broad searches cannot starve boards.
    board_state=state.setdefault('board_route',{})
    prepare(board_state,[],selected)
    before=len(report['pages']);walk([],fetch,board_state,report,save,max_pages=6,max_ads_per_source=2)
    for e in report['pages'][before:]:e['route']='rotating_board'
    broad=state.setdefault('broad_route',{})
    # Cross-route deduplication shares visited thread IDs, not production state.
    broad['visited']=sorted(set(broad.get('visited',[]))|set(board_state.get('visited',[])))
    seeds=[];titles={};searchdir=out/'searches';searchdir.mkdir()
    for engine in ENGINES:
     for query in QUERIES:
      print('Search: '+engine[0]+' '+query,flush=True)
      capture.QUERY=query
      row=probe(browser,engine,len(report['searches'])+1,searchdir)
      html=(searchdir/row['capture_folder']/'page.html')
      row['extracted_results']=links(html.read_text(encoding='utf-8'),engine[0]) if html.exists() else []
      for hit in row['extracted_results']:
       if BUY.search(hit['title']):
        seeds.append(hit['url']);titles[hit['url']]=hit['title']
      report['searches'].append(row);save()
      print('  '+str(len(row['extracted_results']))+' result links',flush=True)
    prepare(broad,seeds,[])
    for entry in broad['pending']:
     entry['title']=entry.get('title') or titles.get(entry['url'],'')
    order(broad,report)
    before=len(report['pages']);walk([],fetch,broad,report,save,max_pages=6,max_ads_per_source=2)
    for e in report['pages'][before:]:e['route']='broad_search'
    board_state['visited']=sorted(set(board_state.get('visited',[]))|set(broad.get('visited',[])))
    for e in report['pages']:
     if e.get('topic_links',0)>0 and e['url'] not in state['boards']:state['boards'].append(e['url'])
    report['state']='original_post_candidates_captured' if report['buyers'] else 'no_original_buyer_post_proven'
    report['opened_this_run']=len(report['pages'])
    report['pending_count']=len(board_state.get('pending',[]))+len(broad.get('pending',[]))
   finally:browser.close()
 except Exception as e:report.update(state='incomplete',error=type(e).__name__+': '+str(e))
 finally:
  save()
  with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
   for f in out.rglob('*'):
    if f.is_file() and f.suffix!='.zip':z.write(f,str(f.relative_to(out)))
  print('Original buyer-post candidates: '+str(len(report['buyers'])),flush=True)
  print('Results: '+str(out/'SEND_THIS_REPORT.zip'),flush=True)
 return report
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);a.add_argument('--state',type=Path,required=True);v=a.parse_args();execute(v.output,v.state)
