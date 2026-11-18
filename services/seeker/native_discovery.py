"""Independent broad WTB -> public seller reading -> Seeker V2.

No five-step package, eBay API, foreign component imports or introductions.
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime,timezone
from pathlib import Path
from bs4 import BeautifulSoup
from lead_navigation import classify_buyer

QUERIES=('"want to buy" classifieds','"WTB" forum','"looking to buy" forum','"wanted" marketplace','"in search of" classifieds')
BUY=re.compile(r'\b(?:wtb|wanted|want to buy|looking to buy|looking for|in search of|seeking)\b',re.I)
CLOSED=re.compile(r'\b(?:no longer (?:needed|looking)|request (?:fulfilled|withdrawn)|found one|bought one|wanted.*fulfilled)\b',re.I)

def now():return datetime.now(timezone.utc).isoformat()

def buyer_page(html,url):
    """Require an original post; never substitute a search excerpt or reply."""
    soup=BeautifulSoup(html or '', 'html.parser')
    h=soup.find('h1')
    title=h.get_text(' ',strip=True) if h else ''
    regions=soup.select('[data-role="commentContent"], .message-body, .Message.userContent, [id^="post_message_"], #postingbody')
    if not title or not regions:raise ValueError('Original buyer post could not be isolated')
    post=regions[0]
    for junk in post.select('blockquote, .signature, .ipsQuote, .bbCodeBlock--quote'):
        junk.decompose()
    text=title+'\n'+post.get_text(' ',strip=True)
    if not BUY.search(title) or CLOSED.search(text):raise ValueError('Not an open original wanted post')
    if len(text)>20000:raise ValueError('Buyer post exceeds reader allowance')
    query=BUY.sub(' ',title).strip(' :-')
    if not query:raise ValueError('Wanted title contains no product')
    return dict(url=url,title=title,text=text,query=query,platform='web',source_sha256=hashlib.sha256(text.encode()).hexdigest())

def cycle(scope,client,seen,report,persist,publish,queries):
    visited=set();buyer_reads=0;assessments=0;began=time.monotonic()
    for query in queries:
        rows=scope['ddg_text'](query,max_results=10)
        report['searches'].append(dict(query=query,results=rows));persist()
        for row in rows:
            if time.monotonic()-began>600 or buyer_reads>=8 or assessments>=9:
                report['incomplete'].append('Run allowance reached; remaining leads not evaluated');return
            url=row.get('href','')
            if not url or url in visited or scope['is_seen'](seen,url):continue
            visited.add(url)
            kind,reason=classify_buyer(dict(url=url,title=row.get('title',''),snippet=row.get('body','')))
            if kind!='ad':report['skipped'].append(dict(url=url,reason=reason));continue
            buyer_reads+=1
            try:
                html=scope['read_listing_html'](url)
                buyer=buyer_page(html,url)
            except ValueError as exc:
                report['skipped'].append(dict(url=url,reason=str(exc)));persist();continue
            report['buyers'].append(buyer);persist()
            reading_record={'buyer_url':url}
            report.setdefault('discovery_readings',[]).append(reading_record)
            def audit(record):
                reading_record.update(record);persist()
            try:
                reading=scope['read_discovery'](buyer,'find_sellers',client,report.get('_sb'),audit)
            except (ValueError,TypeError,KeyError) as exc:
                reading_record.update(state='invalid',error=str(exc))
                report['incomplete'].append('Discovery reading failed validation');persist();continue
            reading_record['reading']=reading;persist()
            if reading['state']!='ready':
                report['skipped'].append(dict(url=url,reason=reading['interpretation']));continue
            # Reader queries guide retrieval only. V2 still gets both originals.
            previous_queries=scope.get('counterpart_queries')
            scope['counterpart_queries']=[q['text'] for q in reading['queries']]
            requested_item=scope['requested_item']
            scope['requested_item']=lambda text,b=buyer: b['query'] if text==b['text'] else requested_item(text)
            try:
                found=scope['search_listings'](buyer['query'],seen,max_keep=8,buyer_text=buyer['text'])
                report['seller_searches'].append(dict(buyer_url=url,queries=scope['counterpart_queries'],result=found));persist()
                if found.get('incomplete'):report['incomplete'].append(found['incomplete'])
                for seller in found['listings']:
                    if assessments>=9 or time.monotonic()-began>600:
                        report['incomplete'].append('Assessment allowance reached; remaining offers not evaluated');return
                    if not scope['qualifies'](seller,buyer['text']):
                        report['skipped'].append(dict(url=seller['url'],reason='Listing availability or buyer requirement check withheld offer'));continue
                    assessments+=1
                    result=scope['ai_evaluate_match'](buyer['text'],seller['text'],client,report.get('_sb'),False)
                    report['assessments'].append(dict(buyer=buyer,seller=seller,result=result));persist()
                    if result.get('match') and scope['qualifies'](seller,buyer['text']):
                        match_id,created=publish(buyer,seller,result)
                        if not match_id:raise RuntimeError('Match persistence did not return an id')
                        report.update(match_id=match_id,new_matches=int(created),still_valid=int(not created),state='completed')
                        scope['mark_seen'](seen,url,buyer['title']);scope['mark_seen'](seen,seller['url'],seller.get('title',''));return
            finally:
                scope['requested_item']=requested_item
                if previous_queries is None:scope.pop('counterpart_queries',None)
                else:scope['counterpart_queries']=previous_queries
            scope['mark_seen'](seen,url,buyer['title'])
            persist()
    report['state']='incomplete' if report['incomplete'] else 'no_qualifying_match'

def run_claimed(sb,client,seen,run):
    if (run.get('search_query') or '').strip() or run.get('market_id') or run.get('job_kind') not in (None,'consumer_search','public_discovery'):
        raise ValueError('Native broad Seeker accepts only its own blank discovery job')
    from native_reader import load
    state=Path(os.environ['SFB_COMPONENT_STATE'])
    folder=state/'results'/str(run['id']);folder.mkdir(parents=True,exist_ok=False)
    report=dict(run_id=run['id'],started_at=now(),state='running',engine='native-seeker-ddgs-v2',searches=[],buyers=[],seller_searches=[],assessments=[],skipped=[],incomplete=[],introductions=0)
    def persist():
        safe={k:v for k,v in report.items() if not k.startswith('_')}
        tmp=folder/'REPORT.tmp';tmp.write_text(json.dumps(safe,indent=2,ensure_ascii=False),encoding='utf-8');tmp.replace(folder/'REPORT.json')
    try:
        scope=load(state)
        def search(query,max_results=10):
            # Keep failures distinct from genuine empty results, with no retry.
            rows=list(scope['DDGS'](timeout=15).text(query,max_results=max_results))
            report.setdefault('all_queries',[]).append(dict(query=query,results=rows));persist();return rows
        scope['ddg_text']=search
        rotation=state/'native-discovery-rotation.json'
        start=json.loads(rotation.read_text(encoding='utf-8')).get('cursor',0) if rotation.exists() else 0
        temp=rotation.with_suffix('.tmp');temp.write_text(json.dumps({'cursor':(start+1)%len(QUERIES)}),encoding='utf-8');temp.replace(rotation)
        report['_sb']=sb
        cycle(scope,client,seen,report,persist,lambda b,s,r:scope['push_match'](sb,b,s,r),QUERIES[start:]+QUERIES[:start])
        if report['state']=='running':report['state']='incomplete'
    except Exception as exc:
        report.update(state='incomplete',error=type(exc).__name__+': '+str(exc))
    finally:
        report['finished_at']=now();persist()
    success=report['state']=='completed'
    if success:summary='Seeker V2 Send saved for review; no introduction sent'
    elif report['state']=='no_qualifying_match':summary='No qualifying match in the sources checked'
    else:summary='Search incomplete — '+str(report.get('error') or '; '.join(report['incomplete']) or 'run stopped')
    changes=dict(status='completed' if report['state'] in ('completed','no_qualifying_match') else 'failed',completed_at=now(),result_summary=summary,new_matches=report.get('new_matches',0),still_valid=report.get('still_valid',0))
    if success:changes['match_id']=report['match_id']
    sb.table('seeker_runs').update(changes).eq('id',run['id']).execute()
    return report
