"""Pre-navigation lead screening only; never a fit, availability or Send decision."""
import re
import unicodedata
from urllib.parse import urlsplit, unquote

GENERIC = set('a an the and or for to of in on with without wanted want buy buying looking seeking wtb wts sale selling sell price asking classified classifieds ads ad working works tested untested used new good excellent condition fully functional pair set home stereo anywhere shipping shipped free original cheap affordable'.split())


def terms(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    # Explicit lexical aliases; no inferred fitment or model equivalence.
    text = re.sub(r'\bcompact[ -]+dis[ck]\b', 'cd', text)
    text = re.sub(r'\bcd[ -]*players?\b', 'cd player', text)
    text = re.sub(r'\bplayers\b', 'player', text)
    text = re.sub(r'\bguitars\b', 'guitar', text)
    text = re.sub(r'\bspeakers\b', 'speaker', text)
    # Preserve short product terms such as CD and normalize model punctuation.
    text = re.sub(r'(?<=\w)[-](?=\w)', '', text)
    return set(re.findall(r'[a-z0-9]+', text)) - GENERIC


def product_groups(queries):
    groups = [terms(q) for q in queries if terms(q)]
    if not groups:
        return []
    common = set.intersection(*groups)
    # Shared product words survive different condition/search phrasing.
    # A single generic noun must not erase more specific model wording.
    if common and (len(common) > 1 or any(any(c.isdigit() for c in t) for t in common)):
        return [common]
    return groups


class BuyerNavigationScreen:
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    def pre_navigation(self, title, url):
        p = urlsplit(url)
        host = (p.hostname or '').lower()
        path = unquote(p.path).lower()
        # Forum author routes only. Marketplace seller profiles are untouched.
        forum = host.startswith(('forum.', 'forums.')) or host == 'forum.polkaudio.com'
        if forum and re.match(r'^/(?:profile|profiles|members|users)/', path):
            return dict(passed=False, reason='Forum author profile, not a request thread',
                        matched_rule='forum_author_profile', field='url')
        return self.original.pre_navigation(title, url)


class CandidateNavigationMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.buyer_screen = BuyerNavigationScreen(self.buyer_screen)
        self.product_groups = None

    def discover_seller(self, queries, buyer):
        self.product_groups = product_groups(queries)
        try:
            return super().discover_seller(queries, buyer)
        finally:
            self.product_groups = None

    def search(self, query, purpose):
        rows = super().search(query, purpose)
        if purpose != 'seller' or self.product_groups is None:
            return rows
        kept = []
        for row in rows:
            evidence = terms(row.get('title', '') + ' ' + row.get('snippet', ''))
            if any(group <= evidence for group in self.product_groups):
                kept.append(row)
                continue
            self.r['seller_counts']['pre_navigation_skips'] += 1
            self.r['screening'].append(dict(side='seller', url=row['url'],
                title=row.get('title', ''), phase='pre_navigation',
                outcome='pre_navigation_skip', matched_rule='requested_product_evidence',
                reason='Title/snippet does not establish the requested product; generic query overlap is insufficient',
                required_product_groups=[sorted(g) for g in self.product_groups]))
        self.persist()
        return kept
