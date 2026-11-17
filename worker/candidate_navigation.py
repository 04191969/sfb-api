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
        self.unresolved_searches = []

    def discover_seller(self, queries, buyer):
        self.product_groups = product_groups(queries)
        self.unresolved_searches = []
        try:
            seller = super().discover_seller(queries, buyer)
            if seller is None and self.unresolved_searches:
                self.r.update(state='search_extraction_failed', reason='Search incomplete: both document-only and rendered Bing returned results without requested-product evidence. This does not establish that no sellers exist.')
                self.persist()
            return seller
        finally:
            self.product_groups = None

    def search(self, query, purpose):
        rows = super().search(query, purpose)
        if purpose != 'seller' or self.product_groups is None:
            return rows
        def relevant(row):
            evidence = terms(row.get('title', '') + ' ' + row.get('snippet', ''))
            return any(group <= evidence for group in self.product_groups)
        raw_count = len(rows)
        if rows and not any(relevant(row) for row in rows) and hasattr(self.browser, 'rendered_search'):
            self.event('Retrying unproductive search in rendered Bing', query=query,
                       reason='Document-only results lack requested-product evidence')
            rendered = self.browser.rendered_search(query)
            self.r['searches'].append(dict(purpose=purpose, query=query,
                retrieval='rendered_bing_fallback', results=rendered))
            raw_count += len(rendered)
            seen = {row['url'] for row in rows}
            rows = rows + [row for row in rendered if row['url'] not in seen]
            if not any(relevant(row) for row in rendered):
                self.unresolved_searches.append(query)
                self.event('Search relevance unresolved', query=query)
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
        # The inherited loop adds retained rows. Include raw rejected/duplicate
        # rows here so returned_rows is the actual retrieval count.
        self.r['seller_counts']['returned_rows'] += raw_count - len(kept)
        self.persist()
        return kept
