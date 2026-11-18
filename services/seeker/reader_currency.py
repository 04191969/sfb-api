"""Explicit currency evidence only; no locale or dollar-symbol guesses."""
import json
import re
from decimal import Decimal, InvalidOperation


def equal_amount(a, b):
    try:return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:return False


def craigslist_product_binding(html, url, price, nodes, scope):
    """Bind a URL-less Craigslist Product to its own visible ad, never a related item."""
    from reader_discovery import _CraigslistAd, craigslist_sale_ad
    if len(nodes) != 1 or not craigslist_sale_ad(html, url, {'price': price}, scope):
        return None
    page = _CraigslistAd()
    page.feed(html)
    node = nodes[0]
    normalize = lambda value: ' '.join(str(value or '').split())
    name = normalize(node.get('name'))
    description = normalize(node.get('description'))
    if (name and name == normalize(page.fields['titletextonly']) and description
            and description in normalize(page.fields['postingbody'])):
        return node
    return None


def offer_currency(html, url, price, scope):
    """Only an individual Offer explicitly bound to this item and current price."""
    target = scope['canonical_item_url'](url)
    found = set()
    if not target or price is None:return None
    source = scope['excise_similar_sections'](html or '')
    products = []
    for block in re.findall(r'(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>', source):
        try:data = json.loads(block)
        except (ValueError, TypeError):continue
        for node in scope['_json_nodes'](data):
            kind = node.get('@type')
            if 'Product' not in (kind if isinstance(kind, list) else [kind]):continue
            products.append(node)
    bound = craigslist_product_binding(source, url, price, products, scope)
    for node in products:
        offers = node.get('offers') or []
        if isinstance(offers, dict):offers = [offers]
        for offer in offers:
            if not isinstance(offer, dict):continue
            link = offer.get('url') or node.get('url')
            if link:
                if scope['canonical_item_url'](link, url).rstrip('/') != target.rstrip('/'):continue
            elif node is not bound:
                continue
            currency = offer.get('priceCurrency')
            if isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency) and equal_amount(offer.get('price'), price):
                found.add(currency)
    return next(iter(found)) if len(found) == 1 else None


def install(scope):
    original = scope['inspect_listing_html']

    def inspect(html, query, url):
        info = original(html, query, url)
        if info.get('readable'):
            currency = offer_currency(html, url, info.get('price'), scope)
            if currency:
                info['text'] += ('\n[Current item page structured Offer metadata: '
                                 f'price {info["price"]}; priceCurrency {currency}; item URL {url}]')
        return info

    scope['inspect_listing_html'] = inspect
