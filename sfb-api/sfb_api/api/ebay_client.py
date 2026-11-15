"""eBay Browse API search for watched parts. Client-credentials, no shopper login.

Token handling follows Nu World Lab: cache the application token until
120 seconds before it expires, and drop it when eBay returns an error.
"""

import base64
import os
import time
from urllib.parse import quote

import httpx

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
PARTS_CATEGORY = "6030"
SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayNotConfigured(RuntimeError):
    pass


def build_query(part, vehicle):
    """Prefer an OEM number. Otherwise search the part name on that car."""
    oem = (part or {}).get("oem_part_number") or ""
    make = (vehicle or {}).get("make") or ""
    if oem.strip():
        return " ".join(piece for piece in (oem.strip(), make) if piece)
    name = (part or {}).get("part_name") or ""
    year = (vehicle or {}).get("year")
    model = (vehicle or {}).get("model") or ""
    return " ".join(
        str(piece) for piece in (name, year, make, model) if piece not in (None, "")
    ).strip()


def listing_matches(item, part, vehicle):
    """Keep a listing when the title contains the OEM number, or the part and the make."""
    title = (item or {}).get("title") or ""
    folded = title.casefold()
    if not folded:
        return False
    oem = ((part or {}).get("oem_part_number") or "").strip()
    if oem and oem.casefold() in folded:
        return True
    name = ((part or {}).get("part_name") or "").strip().casefold()
    make = ((vehicle or {}).get("make") or "").strip().casefold()
    if not name or name not in folded:
        return False
    return not make or make in folded


def token_deadline(now, expires_in):
    """Refresh before eBay's clock runs out, the same 120 second margin Nu World uses."""
    try:
        lifetime = float(expires_in)
    except (TypeError, ValueError):
        lifetime = 0
    return now + max(0.0, lifetime - 120)


def token_is_current(now, deadline):
    return bool(deadline) and now < deadline


def _money(node):
    """A structured amount. A missing value stays unknown rather than becoming zero."""
    if not isinstance(node, dict):
        return None, None
    value = node.get("value")
    if value in (None, ""):
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    currency = str(node.get("currency") or "").strip() or None
    return text, currency


def _add_identifier(found, seen, kind, value):
    text = str(value or "").strip()
    key = (kind, text.casefold())
    if not text or key in seen:
        return
    seen.add(key)
    found.append({"kind": kind, "value": text})


def _country(location):
    if not isinstance(location, dict):
        return None
    text = str(location.get("country") or "").strip()
    return text or None


def _aspect_kind(name):
    folded = " ".join(str(name or "").casefold().replace("_", " ").split())
    if not folded:
        return ""
    if "interchange" in folded:
        return "interchange"
    if "part number" in folded or folded in ("mpn", "oem", "oe number"):
        return "mpn"
    return ""


def _aspect_values(aspect):
    values = aspect.get("localizedValues") or aspect.get("values") or []
    if values:
        return values
    if aspect.get("value") not in (None, ""):
        return [aspect.get("value")]
    return []


def _read_aspects(identifiers, seen, aspects):
    for aspect in aspects or []:
        if not isinstance(aspect, dict):
            continue
        kind = _aspect_kind(aspect.get("localizedName") or aspect.get("name"))
        if not kind:
            continue
        for value in _aspect_values(aspect):
            _add_identifier(identifiers, seen, kind, value)


def listing_facts(item):
    """Price, shipping, and part identifiers eBay returned. Missing amounts stay unknown."""
    item = item or {}
    price_amount, price_currency = _money(item.get("price"))
    shipping_amount = shipping_currency = None
    for option in item.get("shippingOptions") or []:
        if not isinstance(option, dict):
            continue
        amount, currency = _money(option.get("shippingCost"))
        if amount is not None:
            shipping_amount, shipping_currency = amount, currency
            break
    product = item.get("product") if isinstance(item.get("product"), dict) else {}
    identifiers = []
    seen = set()
    for value in product.get("mpns") or []:
        _add_identifier(identifiers, seen, "mpn", value)
    for value in product.get("gtins") or []:
        _add_identifier(identifiers, seen, "gtin", value)
    _add_identifier(identifiers, seen, "epid", product.get("epid"))
    # Search puts catalog aspects on product. The item call puts seller specifics
    # on localizedAspects, with one value field rather than a list.
    _read_aspects(identifiers, seen, product.get("aspects"))
    _read_aspects(identifiers, seen, item.get("localizedAspects"))
    return {
        "item_id": str(item.get("itemId") or "").strip(),
        "price_amount": price_amount,
        "price_currency": price_currency,
        "shipping_amount": shipping_amount,
        "shipping_currency": shipping_currency,
        "item_country": _country(item.get("itemLocation")),
        "identifiers": identifiers,
    }


def price_filter(max_price):
    if max_price in (None, ""):
        return None
    amount = float(max_price)
    if amount < 0:
        raise ValueError("max_price must be zero or more")
    return f"price:[..{amount}],priceCurrency:USD"


class EbayBrowseClient:
    def __init__(self, client_id=None, client_secret=None):
        self.client_id = client_id or os.environ.get("EBAY_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET", "")
        self._token = None
        self._deadline = 0
        if not self.client_id or not self.client_secret:
            raise EbayNotConfigured("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are required.")

    def _clear_token(self):
        self._token = None
        self._deadline = 0

    def _get_token(self):
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        response = httpx.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": SCOPE},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            self._clear_token()
            raise RuntimeError("eBay did not grant an application token.")
        self._token = token
        self._deadline = token_deadline(time.time(), payload.get("expires_in", 7200))
        return self._token

    @property
    def token(self):
        if not token_is_current(time.time(), self._deadline):
            self._get_token()
        return self._token

    def search_parts(self, query, max_price=None, limit=10):
        params = {
            "q": query,
            "category_ids": PARTS_CATEGORY,
            "limit": limit,
            "sort": "price",
            "fieldgroups": "PRODUCT",
        }
        price = price_filter(max_price)
        if price:
            params["filter"] = price
        try:
            response = self._get(params)
            if response.status_code == 401:
                self._clear_token()
                self._get_token()
                response = self._get(params)
            if response.status_code >= 400:
                self._clear_token()
            response.raise_for_status()
            return response.json().get("itemSummaries", [])
        except httpx.HTTPError:
            self._clear_token()
            raise

    def get_item(self, item_id):
        """One listing, including seller item specifics the search summary omits."""
        item_id = str(item_id or "").strip()
        if not item_id:
            return {}
        url = ITEM_URL + quote(item_id, safe="")
        try:
            response = self._request(url, timeout=6)
            if response.status_code == 401:
                self._clear_token()
                self._get_token()
                response = self._request(url, timeout=6)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except httpx.HTTPError:
            raise

    def _request(self, url, params=None, timeout=15):
        return httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params=params,
            timeout=timeout,
        )

    def _get(self, params):
        return self._request(SEARCH_URL, params=params, timeout=15)
