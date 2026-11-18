"""Isolated steps 1/2 proof: search leads -> directories -> original buyer posts.
No production imports, seller search, model calls, credentials or database access.
"""
import argparse,hashlib,json,re,time,urllib.request,urllib.error
from collections import deque
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit,urlunsplit
from bs4 import BeautifulSoup
BUY=re.compile(r'\b(?:wtb|wanted|want(?:ed)? to buy|looking (?:to buy|for)|in search of|seeking|need(?:ed)?)\b',re.I)
CLOSED=re.compile(r'\b(?:no longer (?:needed|looking)|found one|bought one|request (?:fulfilled|withdrawn))\b',re.I)
ADMIN=re.compile(r'\b(?:rules|scamm\w*|please read|announcement|guidelines)\b',re.I)
LIST_SELECTORS=('.ipsData--topic-list .ipsData__title a[href]','.ipsDataList .ipsDataItem_title a[href]','#threadslist a[id^="thread_title"]','.structItemContainer .structItem-title a[href]')
POST_SELECTORS=('[data-role="commentContent"]','.message-body','.Message.userContent','[id^="post_message_"]','#postingbody')
def canonical(url):
 p=urlsplit(url)
 return urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
def safe_link(href,base):
 u=canonical(urljoin(base,href));p=urlsplit(u)
 if p.scheme not in ('http','https') or p.hostname!=urlsplit(base).hostname:return None
 if re.search(r'/(?:profile|profiles|members|users|login|register)(?:/|$)',p.path,re.I):return None
 if p.username or p.password:return None
 return u

def directory_links(markup,url):
 soup=BeautifulSoup(markup,'html.parser');main=soup.select_one('#ipsLayout_mainArea, main, #content') or soup
 title=main.find('h1') or soup.find('h1') or soup.title
 title=title.get_text(' ',strip=True) if title else ''
 rows=[];seen=set();recognized=False
 for selector in LIST_SELECTORS:
  anchors=main.select(selector)
  if anchors:recognized=True
  for a in anchors:
   text=a.get_text(' ',strip=True);target=safe_link(a.get('href',''),url)
   if not target or not text or target in seen or ADMIN.search(text):continue
   # Only primary topic links; never preview, last-post, author or reply links.
   p=urlsplit(target)
   if re.search(r'(?:[?&](?:do=|view=|preview=|page=)|/page[-/]?\d+/?$)',target,re.I):continue
   if a.find('time') or a.get('title','').lower()=='go to last post':continue
   if not BUY.search(title+' '+text):continue
   seen.add(target);rows.append(dict(url=target,title=text,origin=url,kind='post',directory_title=title))
 pages=[]
 if recognized:
  for a in main.select('a[rel="next"],.ipsPagination_next a,.pageNav-jump--next'):
   u=safe_link(a.get('href',''),url)
   # A next page must stay within this directory, not another forum section.
   root=re.sub(r'/(?:page/\d+|page-?\d+)/?$','/',urlsplit(url).path).rstrip('/')
   if u and urlsplit(u).path.startswith(root+'/') and u not in pages:pages.append(u)
 return dict(recognized=recognized,title=title,posts=rows,next_pages=pages)

def original_post(markup,url,directory_title=""):
 soup=BeautifulSoup(markup,'html.parser');heading=soup.find('h1')
 if not heading:raise ValueError('No original thread title')
 title=heading.get_text(' ',strip=True)
 main=soup.select_one('#ipsLayout_mainArea, main, #content') or soup
 regions=main.select(','.join(POST_SELECTORS))
 if not regions:raise ValueError('Original-post layout unsupported; no snippet substituted')
 post=regions[0]
 for junk in post.select('blockquote,.signature,.ipsQuote,.bbCodeBlock--quote,script,style'):junk.decompose()
 body=post.get_text('\n',strip=True)
 if not body:raise ValueError('Original post empty')
 explicit=bool(BUY.search(title+'\n'+body))
 if not explicit and not BUY.search(directory_title):raise ValueError('Original text has no buying-request wording')
 container=post.find_parent('article') or post.find_parent(class_=re.compile(r'(?:^|\s)(?:message|ipsComment)(?:\s|$)')) or post.parent
 dates=[t.get('datetime') for t in container.select('time[datetime]')]
 text=title+'\n'+body
 if CLOSED.search(text):raise ValueError('Original request says fulfilled or withdrawn')
 if len(text)>20000:raise ValueError('Original post exceeds text allowance')
 return dict(url=url,title=title,original_post=body,text=text,source_sha256=hashlib.sha256(text.encode()).hexdigest(),posted_at=dates[0] if dates else None,status='buyer_post_candidate' if explicit else 'role_needs_review',directory_title=directory_title,availability='unconfirmed',limitations=['Original title and first supported post only; no model assessment.','Age, replies and current buying intent require subsequent review.'])

def walk(seeds,fetch,state,report,persist,max_pages=20,max_ads_per_source=2):
 queue=deque(state.get('pending') or [dict(url=u,kind='directory',origin=u) for u in dict.fromkeys(seeds)])
 visited=set(state.get('visited',[]));opened=0;per_source={}
 def save():
  state.update(pending=list(queue),visited=sorted(visited));persist()
 while queue and opened<max_pages:
  entry=queue.popleft();url=canonical(entry['url']);source=entry.get('origin',url)
  if url in visited:continue
  if entry['kind']=='post' and per_source.get(source,0)>=max_ads_per_source:
   # Retain leftovers, while permitting other sources to get their turn.
   queue.append(entry)
   if all(x['kind']=='post' and per_source.get(x.get('origin',x['url']),0)>=max_ads_per_source for x in queue):break
   continue
  visited.add(url);opened+=1
  if entry['kind']=='post':per_source[source]=per_source.get(source,0)+1
  try:
   page=fetch(url);event=dict(url=url,origin=source,kind=entry['kind'],http_status=page['status'],final_url=page.get('url',url));report['pages'].append(event)
   if page['status']!=200:raise ValueError('HTTP '+str(page['status'])+'; not treated as readable evidence')
   final=page.get('url',url)
   if urlsplit(final).hostname!=urlsplit(url).hostname:raise ValueError('Cross-site redirect; no evidence accepted')
   if entry['kind']=='directory':
    # A thread can contain related-topic lists. Its original post takes priority.
    document=BeautifulSoup(page['html'],'html.parser')
    main=document.select_one('#ipsLayout_mainArea, main, #content') or document
    if main.select(','.join(POST_SELECTORS)):
     candidate=original_post(page['html'],final)
     candidate['discovery_url']=source;report['buyers'].append(candidate)
     event['resolved_kind']='individual_post'
     save();continue
    parsed=directory_links(page['html'],final);event['topic_links']=len(parsed['posts']);event['next_pages']=parsed['next_pages']
    if parsed['recognized']:
     # Read a small batch immediately; retain the rest for later turns.
     immediate=parsed['posts'][:max_ads_per_source]
     for x in reversed(immediate):queue.appendleft(x)
     for x in parsed['posts'][max_ads_per_source:]:queue.append(x)
     for u in parsed['next_pages']:queue.append(dict(url=u,kind='directory',origin=source))
     if not parsed['posts']:event['reason']='No eligible topic links'
    else:
     # A search hit may already be an individual thread.
     candidate=original_post(page['html'],final);candidate['discovery_url']=source;report['buyers'].append(candidate)
   else:
    candidate=original_post(page['html'],final,entry.get('directory_title',''));candidate['discovery_url']=source;report['buyers'].append(candidate)
  except Exception as exc:
   report['failures'].append(dict(url=url,kind=entry['kind'],reason=type(exc).__name__+': '+str(exc)))
  save()
 save();report['pending_count']=len(queue);report['opened_this_run']=opened
 return report

def execute(search_report,out):
 out.mkdir(parents=True,exist_ok=False);captures=out/'pages';captures.mkdir()
 source=json.loads(search_report.read_text(encoding='utf-8-sig'))
 attempts=source if isinstance(source,list) else source['attempts']
 seeds=[]
 for attempt in attempts:
  for row in attempt.get('results',[]):
   u=row.get('href','');text=row.get('title','')+' '+row.get('body','')
   if BUY.search(text) and not re.search(r'dictionary|glossary|wikipedia|grokipedia',u,re.I):seeds.append(u)
 report=dict(state='running',search_report=str(search_report),seed_urls=list(dict.fromkeys(seeds)),pages=[],buyers=[],failures=[],model_calls=0,database_writes=0,production_changed=False,started_at=datetime.now(timezone.utc).isoformat())
 state={}
 def save():
  (out/'REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8');(out/'STATE.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
 def fetch(url):
  print('Opening '+url,flush=True)
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
  try:
   with urllib.request.urlopen(req,timeout=15) as r:
    data=r.read(2000000);encoding=r.headers.get_content_charset() or 'utf-8';result=dict(status=r.status,url=r.url,html=data.decode(encoding,errors='replace'))
  except urllib.error.HTTPError as exc:result=dict(status=exc.code,url=url,html='')
  (captures/(hashlib.sha256(url.encode()).hexdigest()+'.json')).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8');return result
 save()
 try:walk(seeds,fetch,state,report,save);report['state']='captured_candidates' if report['buyers'] else 'no_verified_step2_result'
 finally:report['finished_at']=datetime.now(timezone.utc).isoformat();save()
 print('Captured original buyer-post candidates:',len(report['buyers']));return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--search-report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();execute(a.search_report,a.output)
