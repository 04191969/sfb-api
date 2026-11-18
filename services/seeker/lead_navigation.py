"""Navigation classification only; no product, category or match decisions."""
import re
from urllib.parse import urlsplit, unquote

AD = re.compile(r'\b(?:wtb|wts|fs|wanted|want to buy|looking to buy|in search of|for sale|selling|asking)\b',re.I)
ITEM = re.compile(r'/(?:itm|item|items|listing|listings|product|products)/|/[^/]+/d/[^/]+/\d+\.html$',re.I)

def classify(row):
    p=urlsplit(row['url']);path=unquote(p.path).lower();title=row.get('title','')
    if re.search(r'/(?:dictionary|thesaurus|definition|definitions|encyclopedia)(?:/|$)',path):
        return 'skip','Reference definition, not an ad or classifieds directory'
    if re.search(r'/(?:profile|profiles|members|users)(?:/|$)',path):
        return 'skip','Author profile, not an ad'
    if re.search(r'/(?:events|calendar|webinars)(?:/|$)',path):
        return 'skip','Event/calendar page, not a goods ad'
    if re.fullmatch(r'/(?:pages/)?(?:about(?:-us)?|company|company-history|our-story|contact(?:-us)?|privacy-policy|terms-of-service)/?',path):
        return 'skip','Company information page, not an individual ad'
    if ITEM.search(path):return 'ad','Individual listing route; original evidence still required'
    if path in ('','/') or re.search(r'/marketplace/category/|/(?:categories|search)(?:/|$)',path):
        if AD.search(title+' '+row.get('snippet','')) or re.search(r'\b(?:classifieds|marketplace)\b',title,re.I):
            return 'directory','Navigation source only'
        return 'skip','Homepage without classified-ad context'
    if re.search(r'/(?:forum|forums)/',path) and not re.search(r'/(?:topic|thread|threads|discussion)/|\.html$',path):
        return ('directory','Forum navigation only') if AD.search(title+' '+row.get('snippet','')) else ('skip','Forum without ad context')
    if AD.search(title+' '+row.get('snippet','')):return 'ad','Ad wording; reader must establish author intent'
    return 'ad','Unclassified lead; original page and reader must establish intent'

BUYER_AD = re.compile(r'\b(?:wtb|wanted|want(?:ed)? to buy|looking to buy|looking for|in search of|seeking|needed)\b',re.I)

def classify_buyer(row):
    """Use the lead's own evidence, never the search query, to admit a buyer."""
    kind,reason=classify(row)
    if kind=='skip':return kind,reason
    p=urlsplit(row['url']);host=(p.hostname or '').lower();path=unquote(p.path).lower()
    if host=='facebook.com' or host.endswith('.facebook.com'):
        if re.match(r'^/groups/[^/]+/?$',path):
            return 'directory','Facebook group index; individual posts only'
        if path.startswith(('/marketplace','/login')):
            return 'skip','Marketplace offer or login page, not a buyer post'
    ebay=bool(re.search(r'(^|\.)ebay\.[a-z.]+$',host))
    if ebay and re.match(r'^/(?:itm|sch|shop)(?:/|$)',path):
        return 'skip','eBay shopping result, not a buyer request'
    if re.search(r'/(?:products?|shopping|fit-finder)(?:/|$)',path):
        return 'skip','Product or shopping guide route, not a buyer request'
    if host in ('reverb.com','www.reverb.com') and re.match(r'^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:item|p)/',path):
        return 'skip','Seller listing/catalog, not a buyer request'
    if kind=='directory':return kind,reason
    evidence=row.get('title','')+' '+row.get('snippet','')
    if not BUYER_AD.search(evidence):
        return 'skip','No buying-request evidence in result title or snippet'
    return 'ad','Possible buying request; original author intent still requires reader validation'
