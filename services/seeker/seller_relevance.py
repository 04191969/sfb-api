"""Seller search-result evidence only; never a match or availability decision."""
import re
from urllib.parse import urlsplit

GENERIC = set('for sale buy buying sell selling wanted wtb wts fs looking find good condition used new original working only with without and or the a an in on to of head shaft driver item listing shipped shipping'.split())

def terms(text):
    text = text.casefold()
    # Preserve short identifiers, and recognize punctuation/spacing variants.
    text = re.sub(r'(?<=[a-z0-9])[-_](?=[a-z0-9])', '', text)
    text = re.sub(r'\b([a-z]{2,})\s+(\d+)\b', r'\1\2', text)
    return set(re.findall(r'[a-z0-9]+', text)) - GENERIC

def score(query, row):
    """Require product evidence in the result, not its URL or parent query.

    Missing evidence holds a lead outside the visit queue. It does not assert
    incompatibility, nonexistence, price, or availability.
    """
    host = (urlsplit(row['url']).hostname or '').lower()
    if host == 'wikipedia.org' or host.endswith('.wikipedia.org'):
        return 0, 'Reference article, not a seller ad'
    requested = terms(query)
    evidence = terms(row.get('title', '')+' '+row.get('snippet', ''))
    models = {t for t in requested if re.search('[a-z]', t) and re.search('[0-9]', t)}
    if models and not models & evidence:
        return 0, 'Requested model identifier absent from search-result evidence'
    words = {t for t in requested if len(t)>2 and not t.isdigit()}
    overlap = words & evidence
    if len(overlap) < min(2, len(words)) or not (overlap or models & evidence):
        return 0, 'Insufficient requested-product evidence in search result'
    return 10*len(models & evidence)+len(overlap), 'Product evidence present; original ad still requires validation'
