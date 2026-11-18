"""Seeker-only broad source discovery. Qualification and model prompts unchanged."""
import json, os, re, sqlite3, time
from pathlib import Path
from urllib.parse import urlsplit
from automatic_run import (now, sha, tokens, excluded,
    Held, RunLimitReached, SearchExtractionError, discovery_body, validate_output, plan)
from browser_driver import public_url
from lead_navigation import classify, classify_buyer, ITEM

QUERIES = (
 ('buyer', '"want to buy" classifieds'), ('seller', '"for sale" classifieds'),
 ('buyer', '"WTB" forum'), ('seller', '"WTS" forum'),
 ('buyer', '"looking to buy" forum'), ('seller', '"selling" marketplace'),
 ('buyer', '"wanted" marketplace'), ('seller', '"FS" classifieds'),
 ('buyer', '"in search of" classifieds'), ('seller', '"for sale" forum'),
)
BUY = re.compile(r'\b(?:wtb|wanted|want to buy|looking to buy|looking for|in search of|seeking)\b', re.I)
SELL = re.compile(r'\b(?:wts|fs|for sale|selling|asking)\b', re.I)

class DiscoveryState:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.executescript('CREATE TABLE IF NOT EXISTS rotation (id INTEGER PRIMARY KEY, cursor INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS checked (side TEXT, url TEXT, checked REAL, digest TEXT, outcome TEXT, PRIMARY KEY(side,url));')
    def start(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT cursor FROM rotation WHERE id=1').fetchone()
            n=row[0] if row else 0
            self.db.execute('INSERT OR REPLACE INTO rotation VALUES(1,?)',((n+1)%len(QUERIES),))
        return n
    def recent(self,side,url):
        row=self.db.execute('SELECT checked,outcome FROM checked WHERE side=? AND url=?',(side,url)).fetchone()
        return bool(row and time.time()-row[0] < (3600 if row[1]=='unreadable' else 86400))
    def mark(self,side,url,digest='',outcome='checked'):
        with self.db:self.db.execute('INSERT OR REPLACE INTO checked VALUES(?,?,?,?,?)',(side,url,time.time(),digest,outcome))
    def close(self):self.db.close()


def source_record(page,side):
    """Keep source-boundary checks; remove only the four-category buyer allowlist."""
    text=page.get('source_text','');title=page.get('title','');extraction=page.get('extraction','')
    if not 80<=len(text)<=20000:raise ValueError('Source size outside capture bounds')
    if not extraction or extraction=='ambiguous_multiple_articles':raise ValueError('Original author region unresolved')
    if not tokens(title):raise ValueError('Missing product title')
    if excluded(title+'\n'+text):raise ValueError('Excluded source')
    if re.search(r'\b(?:classifieds|search results|latest ads|all listings)\b',title,re.I):raise ValueError('Directory/search heading')
    if re.search(r'/(?:forum|forums|categories)/',urlsplit(page['url']).path) and not extraction.startswith('first_original_post:'):raise ValueError('Forum directory without original post')
    parsed=urlsplit(page['url'])
    if side=='buyer' and (parsed.hostname or '').removeprefix('www.')=='facebook.com' and re.match(r'^/groups/[^/]+/?$',parsed.path):
        raise ValueError('Facebook group index, not an individual buyer post')
    if side=='buyer' and extraction in ('main_region','single_article'):
        requests=[link for link in page.get('links',[]) if BUY.search(link.get('title','')) and link['url']!=page['url']]
        if len(requests)>4:raise ValueError('Wanted directory, not an individual ad')
    if side=='seller':
        if page.get('single_listing') is not True and not extraction.startswith('first_original_post:'):raise ValueError('Seller source not bound to an individual ad')
    anchor=BUY if side=='buyer' else SELL
    # This is lead admission only. The unchanged discovery reader confirms role.
    if not anchor.search(title+'\n'+text):raise ValueError('No relevant ad wording')
    return dict(url=public_url(page['url']),capture_id=page['capture_id'],author_text=text,
        text_sha256=sha(text.encode()),side=side,captured_at=now(),source_timestamp=None,
        availability='unconfirmed',source_coverage='automatically selected displayed source region',
        extraction=extraction,listing_binding=page.get('listing_binding'),
        limitations=['Automatically selected author region; source age and current availability are unconfirmed.',
                     'Search snippets are leads only; original page text is used for assessment.'])


class BroadDiscoveryMixin:
    def public_search(self,query,side):
        self.boundary();self.event('Searching public '+side,query=query)
        rows=self.browser.search(query)
        self.r['searches'].append(dict(purpose=side,query=query,results=rows,provider='public search'))
        self.persist();return rows

    def public_sources(self,query,side,root=False,exclude_urls=()):
        excluded_sources={self.buyer_screen.canonical_url(u) for u in exclude_urls}
        rows=self.public_search(query,side)
        queue=[(r,0) for r in rows];seen=set();opened=0
        while queue:
            row,depth=queue.pop(0);url=self.buyer_screen.canonical_url(row['url'])
            if url in excluded_sources:
                self.event("Originating source skipped",url=url,side=side);continue
            if url in seen:continue
            seen.add(url)
            kind,reason=(classify_buyer(row) if side=='buyer' else classify(row))
            if kind=='skip':
                self.event('Non-ad lead skipped',url=url,reason=reason);continue
            path=urlsplit(url).path.lower()
            if re.search(r'/(?:profile|profiles|members|users)(?:/|$)',path):
                self.event('Profile skipped',url=url);continue
            if not self.buyer_screen.pre_navigation(row.get('title',''),url)['passed']:continue
            if excluded(row.get('title','')+' '+row.get('snippet','')+' '+url):continue
            if root and self.discovery_state.recent(side,url):
                self.event('Recently checked source skipped',url=url,side=side);continue
            # Two seed pages per query lets other broad wording get a turn.
            if root and opened>=2:
                self.r['discovery_coverage_limited']=True;break
            if kind=='directory':
                # Directory navigation remains bounded by the existing global
                # 32-navigation / 1,200-request / time limits, not ad allowances.
                self.r['directory_page_visits']=self.r.get('directory_page_visits',0)+1
            else:
                count=self.r['public_page_counts'][side] + (self.r['seller_counts']['page_visits_attempted'] if side=='seller' else 0)
                if count >= (8 if side=='buyer' else 9):raise RunLimitReached('Shared '+side+' individual-ad page allowance reached')
                self.r['public_page_counts'][side]+=1
            opened+=1;page=None
            self.boundary()
            try:
                # SellerApiBrowser routes purpose=seller to eBay; use the wrapped
                # public browser explicitly for public WTS originals.
                public=getattr(self.browser,'original',self.browser)
                if kind=='directory':
                    page=public.visit_directory(url,row.get('parent'))
                    self.event('Directory inspected for ad links only',url=url)
                    source=None
                else:
                    page=public.visit(url,side,row.get('parent'))
                    if self.buyer_screen.canonical_url(page['url']) in excluded_sources:
                        self.event('Redirect to originating source skipped',url=page['url'],side=side)
                        continue
                    source=source_record(page,side)
            except RunLimitReached:raise
            except (Held,ValueError) as exc:
                self.event('Public source skipped',side=side,url=url,reason=str(exc))
                if root:self.discovery_state.mark(side,url,outcome='unreadable')
            else:
                if source is not None:
                    if root:self.discovery_state.mark(side,url,source['text_sha256'])
                    yield source
            if page is not None and depth==0:
                anchor=BUY if side=='buyer' else SELL
                children=[(dict(link,parent=page['url']),1) for link in page.get('links',[])
                    if urlsplit(link['url']).hostname==urlsplit(page['url']).hostname and (anchor.search(link.get('title','')) or (side=='seller' and ITEM.search(urlsplit(link['url']).path)))]
                if kind=='directory' and not children:
                    self.r['discovery_coverage_limited']=True
                    self.event('Directory yielded no readable individual ad links',url=url)
                queue=children+queue

    def read_source(self,source,key):
        direction='find_sellers' if source['side']=='buyer' else 'find_buyers'
        packet=dict(direction=direction,author_text=source['author_text'],
            source={k:source[k] for k in ('url','capture_id','text_sha256')},source_limitations=source['limitations'])
        body=discovery_body(packet)
        reservation=self.ledger.reserve(plan(body,'discovery'),'discovery',True)
        output=validate_output(self.model('discovery',body,key,reservation),packet)
        self.r.setdefault('discovery_readings',[]).append(dict(source=source,output=output))
        return output

    def iter_pairs(self,key):
        state_dir=Path(os.environ.get('SFB_COMPONENT_STATE',self.folder.parent/'discovery_state'))
        self.discovery_state=DiscoveryState(state_dir/'broad_discovery.sqlite3')
        start=self.discovery_state.start()
        self.r.update(public_page_counts={'buyer':0,'seller':0},directory_page_visits=0,query_rotation_start=start,
            test_categories=[],scope='Broad WTB and WTS across categories; existing excluded-source rules retained.',
            discovery_queries=[dict(side=s,query=q) for s,q in QUERIES],discovery_coverage_limited=False)
        attempted=set();failures=[]
        try:
            for offset in range(len(QUERIES)):
                side,query=QUERIES[(start+offset)%len(QUERIES)]
                try:
                    for source in self.public_sources(query,side,root=True):
                        reading=self.read_source(source,key)
                        if reading['state']!='ready':continue
                        queries=[q['text'] for q in reading['queries']]
                        if side=='buyer':
                            self.r['buyers_evaluated']+=1
                            counterparts=self.iter_sellers(queries,source)
                        else:
                            counterparts=(buyer for q in queries for buyer in self.public_sources(q,'buyer',exclude_urls=(source['url'],)))
                        for counterpart in counterparts:
                            if side=='seller':
                                # Confirm the found author's own buying intent before
                                # pairing; generated seller queries are not needed here.
                                other=self.read_source(counterpart,key)
                                if other['state']!='ready':continue
                                self.r['buyers_evaluated']+=1
                            buyer,seller=(source,counterpart) if side=='buyer' else (counterpart,source)
                            identity=(buyer['url'],buyer['text_sha256'],seller['url'],seller['text_sha256'])
                            if identity in attempted:continue
                            attempted.add(identity)
                            self.event('Pair discovered',direction='WTB_to_WTS' if side=='buyer' else 'WTS_to_WTB',buyer_url=buyer['url'],seller_url=seller['url'])
                            yield buyer,seller
                except SearchExtractionError as exc:
                    failures.append(str(exc));self.event('Public search incomplete',query=query,reason=str(exc))
            if failures:raise SearchExtractionError('One or more public searches were blocked or unreadable; see run events')
            if self.r['discovery_coverage_limited']:raise RunLimitReached('Broad query page allowance used; not every candidate was opened')
        finally:self.discovery_state.close()
