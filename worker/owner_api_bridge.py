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
