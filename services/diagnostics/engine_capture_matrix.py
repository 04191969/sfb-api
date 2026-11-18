"""Public browser reachability diagnostic, not a buyer/matcher qualification run."""
import argparse,json,time,zipfile,re
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlencode,urlsplit
ENGINES=[
 ('Google','https://www.google.com/search','q'),('Bing','https://www.bing.com/search','q'),
 ('Yahoo','https://search.yahoo.com/search','p'),('DuckDuckGo','https://html.duckduckgo.com/html/','q'),
 ('Brave public website','https://search.brave.com/search','q'),('Mojeek','https://www.mojeek.com/search','q'),
 ('Startpage','https://www.startpage.com/sp/search','query'),('Ecosia','https://www.ecosia.org/search','q'),
 ('Yep','https://yep.com/web','q'),('Swisscows','https://swisscows.com/en/web','query'),
 ('Lilo','https://search.lilo.org/','q'),('OceanHero','https://oceanhero.today/web','q'),
 ('Dogpile','https://www.dogpile.com/search','q'),('Yandex','https://yandex.com/search/','text'),
 ('Seznam','https://search.seznam.cz/','q'),('Mwmbl','https://mwmbl.org/search','q'),
 ('Naver','https://search.naver.com/search.naver','query'),('Sogou','https://www.sogou.com/web','query'),
 ('Daum','https://search.daum.net/search','q'),('SearchMySite','https://searchmysite.net/search/','q')]
QUERY='"WTB" forum'
BLOCK=re.compile(r'verify (?:that )?you are human|checking your browser|unusual traffic|automated queries|captcha|robot check',re.I)

def safe_network_url(url):
 p=urlsplit(url);return p.scheme+'://'+(p.hostname or '')+p.path

def classify(text,status,links):
 if BLOCK.search(text):return 'verification_present_not_bypassed'
 if status and status>=400:return 'http_error'
 if re.search(r'temporarily unavailable|access denied|forbidden',text,re.I):return 'page_reports_error'
 return 'candidate_links_need_review' if links else 'no_candidate_links_extracted'

def probe(browser,entry,index,out):
 name,base,param=entry;folder=out/('%02d'%index);folder.mkdir()
 row={'engine':name,'query':QUERY,'requested_url':base+'?'+urlencode({param:QUERY}),'capture_folder':folder.name,'network':[],'errors':{},'candidate_links':[]}
 page=None;context=None;started=time.monotonic()
 def capture(key,action):
  try:return action()
  except Exception as e:row['errors'][key]=type(e).__name__+': '+str(e)[:500]
 try:
  context=browser.new_context();page=context.new_page();page.set_default_timeout(6000);page.set_default_navigation_timeout(12000)
  def response(r):
   if len(row['network'])<120 and r.request.resource_type in ('document','xhr','fetch'):
    row['network'].append({'url':safe_network_url(r.url),'status':r.status,'type':r.request.resource_type})
  def failed(r):
   if len(row['network'])<120:row['network'].append({'url':safe_network_url(r.url),'failure':r.failure,'type':r.resource_type})
  page.on('response',response);page.on('requestfailed',failed)
  r=page.goto(row['requested_url'],wait_until='domcontentloaded');row['navigation_status']=r.status if r else None
  text=page.locator('body').inner_text(timeout=3000)
  if not BLOCK.search(text) and name=='Lilo':
   button=page.get_by_role('button',name='Search the web',exact=True)
   if button.count():button.click()
  if not BLOCK.search(text):
   # One bounded render wait, not repeated requests or challenge solving.
   capture('render_wait',lambda:page.wait_for_function("document.querySelector('h2 a,h3 a,a h3,.result__a') || /temporarily unavailable|verify you are human|captcha/i.test(document.body.innerText)",timeout=6000))
 except Exception as e:row['errors']['navigation']=type(e).__name__+': '+str(e)[:500]
 finally:
  if page:
   row['final_url']=capture('url',lambda:page.url);row['title']=capture('title',page.title)
   capture('html',lambda:(folder/'page.html').write_text(page.content(),encoding='utf-8'))
   text=capture('text',lambda:page.locator('body').inner_text(timeout=3000)) or ''
   (folder/'page.txt').write_text(text,encoding='utf-8')
   capture('screenshot',lambda:page.screenshot(path=str(folder/'page.png'),timeout=3000,full_page=False))
   # Links are evidence for inspection, NOT asserted to be search results or ads.
   row['candidate_links']=capture('links',lambda:page.evaluate("""() => Array.from(document.querySelectorAll('h2 a[href],h3 a[href],a:has(h3),a.result__a')).map(a=>({title:a.innerText,url:a.href})).filter(x=>x.title && /^https?:/.test(x.url)).slice(0,30)""")) or []
   row['state']=classify(text,row.get('navigation_status'),row['candidate_links'])
  else:row['state']='browser_setup_failed'
  if context:capture('close',context.close)
  row['seconds']=round(time.monotonic()-started,2)
  (folder/'CAPTURE.json').write_text(json.dumps(row,indent=2,ensure_ascii=False),encoding='utf-8')
 return row

def run(out,entries=None,factory=None):
 out.mkdir(parents=True,exist_ok=False)
 report={'started_at':datetime.now(timezone.utc).isoformat(),'query':QUERY,'attempts':[],'state':'running','model_calls':0,'database_writes':0,'production_changed':False,'scope':'Connection and rendered-page evidence only; one query per engine, no retry, no buyer-page navigation. Qwant already diagnosed separately. Not exhaustive of all public engines.'}
 def save():(out/'REPORT.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
 save()
 try:
  if factory is None:
   from playwright.sync_api import sync_playwright
   factory=sync_playwright
  with factory() as p:
   browser=p.chromium.launch(channel='msedge',headless=True)
   try:
    entries=ENGINES if entries is None else entries
    for i,entry in enumerate(entries,1):
     print(f'[{i}/{len(entries)}] {entry[0]}',flush=True)
     row=probe(browser,entry,i,out);report['attempts'].append(row);save()
     print('  '+row['state']+'; capture '+row['capture_folder'],flush=True)
   finally:browser.close()
  report['state']='captures_complete_not_qualification'
 except Exception as e:report.update(state='setup_or_runner_failed',error=type(e).__name__+': '+str(e))
 finally:
  report['finished_at']=datetime.now(timezone.utc).isoformat();save()
  with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
   for f in sorted(out.rglob('*')):
    if f.is_file() and f.name!='SEND_THIS_REPORT.zip':z.write(f,str(f.relative_to(out)))
  print('Results: '+str(out/'SEND_THIS_REPORT.zip'),flush=True)
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.output)
