"""Consumer-only discovery corrections; no changes to the frozen worker or V2."""
import re
import json
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote


def requirement_text(request):
    match = re.search(r'(?im)^Requirements:\s*([^\r\n]*)', request or '')
    value = match.group(1).strip() if match else ''
    return '' if value.lower() in ('none', 'none stated', 'unknown') else value


def explicit_location(request):
    # Requirements is free-form: a finish, case, voltage, etc. is not a place.
    match = re.search(r'(?im)^(?:Location|Seller location):\s*([^\r\n]*)', request or '')
    value = match.group(1).strip() if match else ''
    return '' if value.lower() in ('none', 'none stated', 'unknown', 'anywhere') else value


def item_identity(url):
    parts = urlsplit(url or '')
    host = (parts.hostname or '').lower()
    if re.fullmatch(r'(?:www\.)?ebay\.[a-z.]+', host):
        match = re.search(r'/itm/(?:[^/]+/)?(\d+)(?:/|$)', parts.path)
        if match:return ('ebay', match.group(1))
    if host in ('reverb.com', 'www.reverb.com'):
        match = re.search(r'/(?:[a-z]{2}(?:-[a-z]{2})?/)?item/(\+?\d+)(?:-|/|$)', parts.path)
        if match:return ('reverb', match.group(1))
    if host in ('facebook.com', 'www.facebook.com', 'm.facebook.com'):
        match = re.search(r'/marketplace/item/(\d+)', parts.path)
        if match:return ('facebook', match.group(1))
    return ('url', url)


# Retrieval hints only: never establish product identity or qualification.
def product_words(text):
    stop = {'for', 'sale', 'wanted', 'buy', 'used', 'new', 'working', 'good', 'condition', 'the', 'and', 'with'}
    return {w for w in re.findall(r'[a-z0-9]+', (text or '').lower()) if len(w) > 2 and w not in stop}


def candidate_hits(row, item):
    url = row.get('url') or row.get('href') or ''
    text = ' '.join(str(row.get(k) or '') for k in ('title', 'body'))
    text += ' ' + unquote(urlsplit(url).path)
    return len(product_words(item) & product_words(text))


def unrelated(row, item):
    # Only discard descriptive results with ZERO product-word overlap.
    # Sparse snippets and partial/variant names remain eligible for reading.
    text = ' '.join(str(row.get(k) or '') for k in ('title', 'body'))
    return len(product_words(item)) >= 2 and len(product_words(text)) >= 3 and candidate_hits(row, item) == 0


class _CraigslistAd(HTMLParser):
    """Read only the page's own posting fields and sale-category metadata."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical = ''
        self.fields = {'titletextonly': '', 'postingbody': ''}
        self.active = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href', '')
        if tag in ('br', 'img', 'input', 'meta', 'link', 'hr'):return
        field = attrs.get('id')
        self.active.append((tag, field if field in self.fields else None))

    def handle_endtag(self, tag):
        for i in range(len(self.active) - 1, -1, -1):
            if self.active[i][0] == tag:
                del self.active[i:]
                break

    def handle_data(self, data):
        for _, field in self.active:
            if field:self.fields[field] += data


def craigslist_sale_ad(html, url, info, scope):
    host = (urlsplit(url).hostname or '').lower()
    if not (host == 'craigslist.org' or host.endswith('.craigslist.org')):return False
    if not scope['is_classified_ad'](url) or info.get('price') is None:return False
    page = _CraigslistAd()
    page.feed(html or '')
    target = url.rstrip('/')
    if page.canonical.rstrip('/') != target:return False
    if not all(value.strip() for value in page.fields.values()):return False
    if scope['classified_closed'](' '.join(page.fields.values())):return False
    # Craigslist's own breadcrumb binds this specific post to its sale section.
    # A generic navigation link, related ad, wanted post, or search page is insufficient.
    for block in re.findall(r'(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html or ''):
        try:data = json.loads(block)
        except (ValueError, TypeError):continue
        for node in scope['_json_nodes'](data):
            if node.get('@type') != 'BreadcrumbList':continue
            entries = node.get('itemListElement') or []
            if not entries or not all(isinstance(x, dict) for x in entries):continue
            if str(entries[-1].get('item', '')).rstrip('/') != target:continue
            names = [str(x.get('name', '')).lower().strip() for x in entries[:-1]]
            if any('wanted' in name for name in names):continue
            if 'for sale' in names:return True
    return False


def install(scope):
    original_canonical = scope['canonical_item_url']
    original_qualifies = scope['qualifies']
    original_inspect = scope['inspect_listing_html']
    aliases = {}
    reported = set()

    def inspect(html, query, url):
        info = original_inspect(html, query, url)
        if (info.get('readable') and info.get('status') == 'unconfirmed'
                and craigslist_sale_ad(html, url, info, scope)):
            info.update(status='available', evidence='classified_ad', checked_at=scope['_checked_now']())
        if info.get('readable') and info.get('price') is None:
            # Only an explicit current asking-price label on the already scoped
            # item text. Never take the first dollar amount from an entire page.
            amounts = re.findall(r'(?:^|[\n.;])\s*(?:asking(?: price)?|item price|current price)\s*[:—-]?\s*(?:US\s*)?\$\s*(\d[\d,]*(?:\.\d{1,2})?)', info.get('text') or '', re.I)
            values = {float(amount.replace(',', '')) for amount in amounts}
            if len(values) == 1:
                info['price'] = values.pop()
        return info

    def canonical(url, base=''):
        result = original_canonical(url, base)
        if not result:return result
        # Same marketplace + item ID, not title similarity or cross-post guesses.
        key = item_identity(result)
        return aliases.setdefault(key, result)

    def qualifies(listing, request):
        ok = original_qualifies(listing, request)
        if ok:return True
        reasons = []
        if listing.get('availability') != 'available':
            reasons.append('availability ' + str(listing.get('availability') or 'unconfirmed'))
        if not scope['location_meets_request'](request, listing.get('location') or ''):
            reasons.append('explicit requested location not established')
        blob = '\n'.join(str(listing.get(k) or '') for k in ('title', 'text', 'page_text'))
        if not scope['condition_meets_request'](request, blob):
            reasons.append('requested condition not established')
        shipping = listing.get('shipping')
        if shipping is None and not listing.get('current_costs'):
            shipping = scope['extract_shipping'](blob)
        price = scope['price_against_budget'](listing.get('price'), scope['buyer_price_cap'](request), shipping)
        if price['status'] == 'unknown':
            reasons.append('current ' + ('item price' if listing.get('price') is None else 'shipping cost') + ' unknown for budget check')
        elif price['status'] == 'over':
            reasons.append(f"{price['basis']} price {price['price']:.2f} exceeds limit {price['limit']:.2f}")
        key = (listing.get('url'), tuple(reasons))
        if key not in reported:
            print('    Pre-assessment rejection: ' + '; '.join(reasons) + ' | ' + str(listing.get('url') or ''), flush=True)
            reported.add(key)
        return False

    def search(query, seen, max_keep=8, buyer_text=''):
        aliases.clear();reported.clear()
        request = buyer_text or query
        item = scope['requested_item'](request)
        requirements = requirement_text(request)
        queries = [template.format(query=item) for template in scope['LISTING_QUERIES']]
        queries.insert(1, f'{item} site:craigslist.org')
        if requirements:
            queries.insert(0, f'{item} {requirements} for sale')
        groups = [];skipped = []
        for text in dict.fromkeys(queries):
            print('    Listing search: ' + text, flush=True)
            rows = list(scope['ddg_text'](text, max_results=10))
            if not any(candidate_hits(row, item) for row in rows):
                # Same query; bounded public backend fallback, no model calls.
                for engine in ('google', 'bing'):
                    try:
                        extra = list(scope['DDGS'](timeout=10).text(text, max_results=10, backend=engine))
                        print(f'    Public search fallback {engine}: {len(extra)} raw results', flush=True)
                        rows.extend(extra)
                        if any(candidate_hits(row, item) for row in extra):break
                    except Exception as exc:
                        print(f'    Public search fallback {engine} unavailable: {exc}', flush=True)
            eligible = []
            for row in rows:
                if unrelated(row, item):
                    scope['_note_skip'](skipped, row.get('href') or '', 'no requested-product evidence in descriptive search result; not opened')
                else:eligible.append(row)
            groups.append(scope['listing_seeds'](eligible, 30, skipped))
            scope['time'].sleep(scope['SEARCH_DELAY'])
        seeds = [];seen_ids = set()
        while any(groups):
            for group in groups:
                if not group:continue
                seed = group.pop(0);url = seed['url'];key = item_identity(url)
                if key in seen_ids:
                    scope['_note_skip'](skipped, url, 'duplicate marketplace item ID')
                    continue
                if scope['is_seen'](seen, url) and not scope['is_item_page'](url) and not scope['facebook_profile_url'](url):continue
                seen_ids.add(key);seeds.append(seed)
        words = re.findall(r'[a-z0-9]+', requirements.lower())
        def priority(seed):
            direct = scope['is_item_page'](seed['url']) and not scope['is_catalog_page'](seed['url'])
            text = (str(seed.get('title') or '') + ' ' + str(seed.get('body') or '')).lower()
            hits = sum(word in text for word in words)
            return (-candidate_hits(seed, item), 0 if direct else 1, -hits)
        seeds.sort(key=priority)
        listings, incomplete = scope['expand_candidates'](
            seeds, item, scope['read_listing_html'], scope['PAGE_BUDGET'], request, scope['MATCH_TARGET'], skipped)
        for seed in seeds:
            print('    Discovery candidate: ' + seed['url'], flush=True)
        for skipped_item in skipped:
            print(f"    Skipped {skipped_item['url']} — {skipped_item['reason']}")
        listings = [row for row in listings if not (scope['is_seen'](seen, row.get('url') or '') and row.get('availability') != 'available')]
        kept = scope['flag_cross_posts'](([r for r in listings if r.get('qualifying')] + [r for r in listings if not r.get('qualifying')])[:max_keep])
        return {'listings': kept, 'incomplete': incomplete, 'skipped': skipped}

    scope['required_location'] = explicit_location
    scope['inspect_listing_html'] = inspect
    scope['canonical_item_url'] = canonical
    scope['qualifies'] = qualifies
    scope['search_listings'] = search
