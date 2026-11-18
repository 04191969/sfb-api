"""Isolated browser retrieval proof. No worker, database, or model imports."""
import argparse, json, re, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, quote_plus
from buyer_discovery_steps12 import BUY, walk

QUERIES = ['"WTB" forum', '"want to buy" classifieds', '"looking to buy" forum']
ENGINES = {'qwant': 'https://www.qwant.com/?q=', 'lilo': 'https://search.lilo.org/?q=', 'yep': 'https://yep.com/web?q='}
BLOCK = re.compile(r'verify you are human|checking your browser|unusual traffic|automated queries|captcha|access denied', re.I)

def result_links(html, base):
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, 'html.parser')
    rows, seen = [], set()
    for a in soup.select('h2 a[href], h3 a[href]'):
        u = urljoin(base, a['href']); title = a.get_text(' ', strip=True)
        host = urlsplit(u).hostname or ''
        if urlsplit(u).scheme not in ('http', 'https') or not host or host == urlsplit(base).hostname:
            continue
        if u not in seen and title:
            rows.append({'url': u, 'title': title}); seen.add(u)
    return rows[:10]

def eligible(rows):
    return [r['url'] for r in rows if BUY.search(r['title']) and not re.search(r'dictionary|glossary|wikipedia|grokipedia|milsurps', r['url'], re.I)]

def run(out, channel='msedge'):
    out.mkdir(parents=True, exist_ok=False)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'state':'running', 'attempts':[], 'pages':[], 'buyers':[], 'failures':[], 'model_calls':0, 'database_writes':0, 'production_changed':False, 'acceptance':'NOT PROVEN', 'limits':{'page_navigation_limit':12,'seconds':300}}
    state={}; started=time.monotonic()
    def save():
        (out/'REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (out/'STATE.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(channel=channel, headless=True)
            context=browser.new_context()
            page=context.new_page(); page.set_default_timeout(12000); page.set_default_navigation_timeout(15000)
            seeds=[]
            for engine, base in ENGINES.items():
                for query in QUERIES:
                    if time.monotonic()-started>300: raise TimeoutError('Proof time limit; saved partial results retained')
                    row={'engine':engine,'query':query,'results':[]}; report['attempts'].append(row)
                    print('Search: '+engine+' '+query, flush=True)
                    try:
                        response=page.goto(base+quote_plus(query),wait_until='domcontentloaded')
                        text=page.locator('body').inner_text()
                        if BLOCK.search(text):
                            row.update(state='blocked',reason='Visible verification/block page; not bypassed'); break
                        # The live proof showed Qwant/Lilo need form submission after the initial shell.
                        if engine in ('qwant','lilo'):
                            page.get_by_role('button',name='Search the web',exact=True).click()
                        page.locator('h2 a, h3 a').first.wait_for(state='visible',timeout=12000)
                        rows=result_links(page.content(),page.url)
                        row.update(state='results' if rows else 'no_extracted_results',url=page.url,results=rows)
                        seeds.extend(eligible(rows))
                        print('  Result links: '+str(len(rows)),flush=True)
                    except Exception as exc:
                        row.update(state='incomplete',reason=type(exc).__name__+': '+str(exc)[:500])
                        print('  '+row['reason'],flush=True)
                    finally: save()
            # Round-robin by source avoids spending the whole proof on one forum.
            unique=[]; hosts=set(); later=[]
            for u in dict.fromkeys(seeds):
                h=urlsplit(u).hostname
                if h in hosts: later.append(u)
                else: unique.append(u); hosts.add(h)
            report['seed_urls']=unique+later
            def fetch(url):
                if time.monotonic()-started>300: raise TimeoutError('Proof time allowance reached')
                print('Open: '+url,flush=True)
                r=page.goto(url,wait_until='domcontentloaded')
                body=page.locator('body').inner_text()
                if BLOCK.search(body[:15000]): raise ValueError('Visible verification/block page; not bypassed')
                return {'status':r.status if r else 0,'url':page.url,'html':page.content()}
            walk(report['seed_urls'],fetch,state,report,save,max_pages=12,max_ads_per_source=2)
            report['state']='completed_bounded_proof'
            report['acceptance']='Original-post candidates extracted; current intent and matching NOT PROVEN' if report['buyers'] else 'NOT PROVEN: no original buyer post extracted'
            browser.close()
    except Exception as exc:
        report.update(state='incomplete',error=type(exc).__name__+': '+str(exc))
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat(); save()
        with zipfile.ZipFile(out/'SEND_THIS_REPORT.zip','w',zipfile.ZIP_DEFLATED) as z:
            z.write(out/'REPORT.json','REPORT.json'); z.write(out/'STATE.json','STATE.json')
        print('Results: '+str(out/'SEND_THIS_REPORT.zip'),flush=True)
        print(report['acceptance'],flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--channel',default='msedge');a=p.parse_args();run(a.output,a.channel)
