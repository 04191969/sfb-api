"""Read-only public search endpoint sweep; no credentials, models or worker imports.
Generic outgoing links are candidates, NOT verified search results or buyer ads.
"""
import argparse, concurrent.futures, json, time, socket, hashlib
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit, parse_qs, unquote
from html.parser import HTMLParser
QUERIES=['"want to buy" classifieds','"WTB" forum','"looking to buy" forum','"wanted" marketplace','"in search of" classifieds']
# Public web routes, not paid search APIs. Unsupported/changed routes remain failures.
ENGINES={
'Google':('https://www.google.com/search','q'),
'Bing':('https://www.bing.com/search','q'),
'Yahoo':('https://search.yahoo.com/search','p'),
'DuckDuckGo HTML':('https://html.duckduckgo.com/html/','q'),
'DuckDuckGo Lite':('https://lite.duckduckgo.com/lite/','q'),
'Brave':('https://search.brave.com/search','q'),
'Mojeek':('https://www.mojeek.com/search','q'),
'Startpage':('https://www.startpage.com/sp/search','query'),
'Qwant':('https://www.qwant.com/','q'),
'Ecosia':('https://www.ecosia.org/search','q'),
'Yep':('https://yep.com/web','q'),
'Swisscows':('https://swisscows.com/en/web','query'),
'MetaGer':('https://metager.org/meta/meta.ger3','eingabe'),
'Marginalia':('https://search.marginalia.nu/search','query'),
'Mwmbl':('https://mwmbl.org/search','q'),
'Stract':('https://stract.com/search','q'),
'Yandex':('https://yandex.com/search/','text'),
'Baidu':('https://www.baidu.com/s','wd'),
'Naver':('https://search.naver.com/search.naver','query'),
'Seznam':('https://search.seznam.cz/','q'),
'AOL':('https://search.aol.com/aol/search','q'),
'Ask':('https://www.ask.com/web','q'),
'Lycos':('https://search.lycos.com/web/','q'),
'Dogpile':('https://www.dogpile.com/search','q'),
'Gibiru':('https://gibiru.com/results.html','q'),
'SearXNG sapti':('https://search.sapti.me/search','q'),
'SearXNG tiekoetter':('https://searx.tiekoetter.com/search','q'),
'SearXNG paulgo':('https://paulgo.io/search','q'),
}
class Page(HTMLParser):
 def __init__(self): super().__init__(); self.links=[]; self.text=[]; self.title=[]; self.in_title=False; self.anchor=None
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='title':self.in_title=True
  if tag=='a':self.anchor=[a.get('href',''),'']
 def handle_data(self,data):
  self.text.append(data)
  if self.in_title:self.title.append(data)
  if self.anchor is not None:self.anchor[1]+=data
 def handle_endtag(self,tag):
  if tag=='title':self.in_title=False
  if tag=='a' and self.anchor is not None:self.links.append(self.anchor);self.anchor=None

def extract(html,base):
 p=Page();p.feed(html);own=urlsplit(base).hostname or ''; links=[];seen=set()
 for href,title in p.links:
  u=urljoin(base,href);qs=parse_qs(urlsplit(u).query)
  # Unwrap only explicit destination parameters, never search-query text.
  for key in ('uddg','url'):
   if qs.get(key) and qs[key][0].startswith(('https://','http://')):u=qs[key][0];break
  if urlsplit(u).path=='/url' and qs.get('q',[''])[0].startswith(('https://','http://')):u=qs['q'][0]
  host=urlsplit(u).hostname or ''
  if not u.startswith(('http://','https://')) or host==own or not title.strip() or u in seen:continue
  seen.add(u);links.append({'url':u,'title':' '.join(title.split())[:180]})
 return {'title':' '.join(p.title),'external_links':links[:60],'text_excerpt':' '.join(' '.join(p.text).split())[:1200]}

def probe(name,endpoint,param,query,folder,index):
 url=endpoint+'?'+urlencode({param:query});row={'engine':name,'query':query,'requested_url':url};start=time.monotonic()
 try:
  try:r=urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 (compatible; SFB-Public-Search-Diagnostic/1.0)','Accept':'text/html'}),timeout=10)
  except HTTPError as e:r=e
  with r:
   body=r.read(2_000_000);row.update(status=r.status,final_url=r.url,content_type=r.headers.get('Content-Type',''),bytes=len(body))
  html=body.decode('utf-8','replace');(folder/f'{index:03d}.html').write_text(html,encoding='utf-8');row.update(extract(html,row['final_url']));row['sha256']=hashlib.sha256(body).hexdigest()
  row['state']='http_response_needs_review' if row['status']==200 else 'http_non_200'
 except Exception as e:row.update(state='request_failed',error=type(e).__name__+': '+str(e))
 row['seconds']=round(time.monotonic()-start,2);return row

def main():
 a=argparse.ArgumentParser();a.add_argument('--output',required=True);args=a.parse_args();folder=Path(args.output);folder.mkdir(parents=True,exist_ok=False)
 report={'queries':QUERIES,'routes':ENGINES,'method':'Direct public HTML GET; 10-second socket timeout; no retries, login, paid API or challenge bypass. External links need human review. One request per engine at a time; variants/instances are not distinct engines.','production_changed':False,'paid_model_calls':0,'rows':[]}
 def engine_run(pair):
  ei,(name,(endpoint,param))=pair;rows=[]
  for qi,q in enumerate(QUERIES):
   rows.append(probe(name,endpoint,param,q,folder,ei*5+qi));time.sleep(1)
  return rows
 with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
  futures=[pool.submit(engine_run,p) for p in enumerate(ENGINES.items())]
  for f in concurrent.futures.as_completed(futures):
   rows=f.result();report['rows']+=rows;(folder/'REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
   print(rows[0]['engine'],[(r.get('status',r['state']),len(r.get('external_links',[]))) for r in rows],flush=True)
 print('Saved',folder/'REPORT.json',flush=True)
if __name__=='__main__':main()
