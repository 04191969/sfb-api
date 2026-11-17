"""Explicit currency evidence only; no locale or dollar-symbol guesses."""
import json
import re
from decimal import Decimal, InvalidOperation


def equal_amount(a, b):
    try:return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:return False


def offer_currency(html, url, price, scope):
    """Only an individual Offer explicitly bound to this item and current price."""
    target = scope['canonical_item_url'](url)
    found = set()
    if not target or price is None:return None
    source = scope['excise_similar_sections'](html or '')
    for block in re.findall(r'(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>', source):
        try:data = json.loads(block)
        except (ValueError, TypeError):continue
        for node in scope['_json_nodes'](data):
            kind = node.get('@type')
            if 'Product' not in (kind if isinstance(kind, list) else [kind]):continue
            offers = node.get('offers') or []
            if isinstance(offers, dict):offers = [offers]
            for offer in offers:
                if not isinstance(offer, dict):continue
                link = offer.get('url') or node.get('url')
                if not link or scope['canonical_item_url'](link, url).rstrip('/') != target.rstrip('/'):continue
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
