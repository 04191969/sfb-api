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
    if ITEM.search(path):return 'ad','Individual listing route; original evidence still required'
    if path in ('','/') or re.search(r'/marketplace/category/|/(?:categories|search)(?:/|$)',path):
        if AD.search(title+' '+row.get('snippet','')) or re.search(r'\b(?:classifieds|marketplace)\b',title,re.I):
            return 'directory','Navigation source only'
        return 'skip','Homepage without classified-ad context'
    if re.search(r'/(?:forum|forums)/',path) and not re.search(r'/(?:topic|thread|threads|discussion)/|\.html$',path):
        return ('directory','Forum navigation only') if AD.search(title+' '+row.get('snippet','')) else ('skip','Forum without ad context')
    if AD.search(title+' '+row.get('snippet','')):return 'ad','Ad wording; reader must establish author intent'
    return 'ad','Unclassified lead; original page and reader must establish intent'
