"""Connect the owner's existing API runner without changing its seller rules."""
from contextlib import contextmanager
from candidate_navigation import BuyerNavigationScreen


def ebay_options(environ):
    names = ('EBAY_CLIENT_ID', 'EBAY_CLIENT_SECRET')
    missing = [name for name in names if not environ.get(name, '').strip()]
    if missing:
        raise ValueError('Missing eBay production credentials: ' + ', '.join(missing))
    return {'ebay_client_id': environ[names[0]].strip(),
            'ebay_client_secret': environ[names[1]].strip()}


class OwnerApiBridge:
    def __init__(self, folder, public_browser=None, **kwargs):
        if public_browser is not None:
            from pathlib import Path
            kwargs['browser_factory'] = lambda: public_browser(Path(folder) / 'browser', stop=self.cancelled)
        super().__init__(folder, **kwargs)
        self.buyer_screen = BuyerNavigationScreen(self.buyer_screen)
        # ApiRun.search and discover_seller stay inherited verbatim. Browser
        # title-token filters must not discard the API's item summaries.


class MixedSellerBrowser:
    """Use API evidence only for items actually returned by that API search."""
    def __init__(self, wrapped):
        self.wrapped = wrapped
    def __getattr__(self, name):return getattr(self.wrapped, name)
    def __enter__(self):self.wrapped.__enter__();return self
    def __exit__(self, *args):return self.wrapped.__exit__(*args)
    def visit(self, url, purpose, parent=None):
        if purpose == 'seller' and url in self.wrapped.client.ids:
            return self.wrapped.client.detail(url)
        return self.wrapped.original.visit(url, purpose, parent)


def diverse_leads(leads):
    """Round-robin hosts, preserving relevance order within each host."""
    from urllib.parse import urlsplit
    groups = {}
    for score, row in leads:
        host = (urlsplit(row['url']).hostname or '').removeprefix('www.')
        if 'ebay.' in host:host = 'ebay'
        groups.setdefault(host, []).append((score, row))
    hosts = sorted(groups, key=lambda h: h == 'ebay')
    result = []
    while any(groups.values()):
        for host in hosts:
            if groups[host]:result.append(groups[host].pop(0))
    return result


class MultiSourceSellerMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        factory = self.browser_factory
        self.browser_factory = lambda: MixedSellerBrowser(factory())
        self.r['seller_source_mode'] = 'Public multi-engine search plus eBay Browse API'

    def persist(self):
        super().persist()
        path = self.folder / 'REPORT.html'
        if path.exists():
            import re
            text = path.read_text(encoding='utf-8')
            text = re.sub(r'<p><strong>eBay API seller mode.*?</p>',
                '<p>Seller discovery uses public search engines plus the eBay API. '
                'Public ads use opened page text; API items use returned seller evidence. '
                'The nine-seller and 1,200-browser-request limits remain shared.</p>', text)
            text = text.replace('Buyer search uses the public browser; seller search uses eBay Browse API.',
                'Buyer and seller searches use the public browser; eBay API supplements seller discovery.')
            path.write_text(text, encoding='utf-8')

    def prepare_seller_sources(self):
        # eBay is supplemental; authentication is deferred until its search.
        pass

    def search(self, query, purpose):
        if purpose != 'seller':return super().search(query, purpose)
        from browser_driver import Held, RunLimitReached
        from ebay_api import ApiStop
        from lead_navigation import classify
        rows = []
        for provider in ('public', 'ebay'):
            self.boundary()
            try:
                if provider == 'public':
                    found = self.browser.search(query)
                else:
                    if getattr(self, '_ebay_unavailable', False):continue
                    if not self.api.token:self.api.authenticate()
                    found = self.api.search(query)
                    self.r['search_api_calls'] = self.api.report['searches']
                self.r['searches'].append(dict(purpose=purpose, query=query,
                    provider=provider, results=found))
                for row in found:
                    kind, reason = classify(row)
                    if kind != 'ad':
                        self.r['screening'].append(dict(side='seller', url=row['url'],
                            phase='pre_navigation', reason=reason))
                        continue
                    rows.append(dict(row, seller_search_provider=provider))
            except RunLimitReached:raise
            except (Held, ApiStop, ValueError) as exc:
                if provider == 'ebay':self._ebay_unavailable = True
                self.r.setdefault('seller_search_failures', []).append(
                    dict(provider=provider, query=query, reason=str(exc)))
            self.persist()
        return rows


@contextmanager
def observe_completion(route, received):
    original_model = route.model
    def dispatch(*args, **kwargs):
        original_transport = route.model_transport
        def transport(*a, **kw):
            raw = original_transport(*a, **kw)
            received[0] = True
            return raw
        route.model_transport = transport
        try:
            return original_model(*args, **kwargs)
        finally:
            route.model_transport = original_transport
    route.model = dispatch
    try:
        yield
    finally:
        route.model = original_model
