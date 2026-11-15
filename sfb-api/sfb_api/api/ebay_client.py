"""eBay Browse API search for watched parts. Client-credentials, no shopper login.

Token handling follows Nu World Lab: cache the application token until
120 seconds before it expires, and drop it when eBay returns an error.
"""

import base64
import os
import re
import threading
import time
from urllib.parse import quote

import httpx

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/"
PARTS_CATEGORY = "6030"
SCOPE = "https://api.ebay.com/oauth/api_scope"
ITEM_CACHE_TTL = 6 * 60 * 60
ITEM_CACHE_MAX = 400
_item_cache = {}
_item_cache_lock = threading.Lock()
# Words that mark a seller sentence. A part number does not read as an instruction.
_PROSE = re.compile(
    r"\b(?:use|with|for|fits|includes|include|drive|ratchet|socket|handle|tool|only|when|your)\b",
    re.IGNORECASE,
)
_PART_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./-]{2,39}$")


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


def relevance_query(part_name, year="", make="", model=""):
    """Buyer search. A multi-word part stays one phrase so the words are not split apart."""
    name = " ".join(str(part_name or "").split())
    if " " in name:
        name = f'"{name}"'
    return build_query(
        {"part_name": name},
        {"year": year, "make": make, "model": model},
    )


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


def clear_item_cache():
    with _item_cache_lock:
        _item_cache.clear()


def _cached_item(item_id):
    if not item_id:
        return None
    now = time.monotonic()
    with _item_cache_lock:
        row = _item_cache.get(item_id)
        if not row:
            return None
        expires, payload = row
        if expires <= now:
            _item_cache.pop(item_id, None)
            return None
        return payload


def _store_item(item_id, payload):
    if not item_id or not isinstance(payload, dict) or not payload:
        return
    now = time.monotonic()
    with _item_cache_lock:
        _item_cache[item_id] = (now + ITEM_CACHE_TTL, payload)
        if len(_item_cache) > ITEM_CACHE_MAX:
            oldest = min(_item_cache, key=lambda key: _item_cache[key][0])
            _item_cache.pop(oldest, None)


def _usable_part_number(value):
    """A compact code. An instruction or a sentence is not a part number."""
    text = " ".join(str(value or "").split())
    if not text or _PROSE.search(text):
        return False
    if any(ch.isspace() for ch in text) or not any(ch.isdigit() for ch in text):
        return False
    return _PART_TOKEN.fullmatch(text) is not None


def _classify_aspect(kind, value, source):
    """Label a field by what the seller or catalog claimed, then by the value's shape.

    An explicit manufacturer part number is kept for comparison. Catalog data
    can verify it. A seller claim stays unverified. A SKU is not that claim.
    An instruction is kept and left out of matching.
    """
    if kind == "mpn":
        if not _usable_part_number(value):
            return "text", False, False
        return "mpn", True, source == "catalog"
    if kind in ("sku", "other"):
        if not _usable_part_number(value):
            return "text", False, False
        return kind, False, False
    return kind, False, False


def _add_identifier(found, seen, kind, value, source, matches, verified=False):
    text = str(value or "").strip()
    key = (kind, text.casefold())
    if not text or key in seen:
        return
    seen.add(key)
    found.append({
        "kind": kind,
        "type": kind,
        "source": source,
        "value": text,
        "matches": matches,
        "verified": verified,
    })


def _country(location):
    if not isinstance(location, dict):
        return None
    text = str(location.get("country") or "").strip()
    return text or None


def _aspect_kind(name):
    folded = " ".join(str(name or "").casefold().replace("_", " ").replace("/", " ").split())
    if not folded:
        return ""
    if "interchange" in folded:
        return "interchange"
    if any(token in folded for token in ("sku", "custom label", "stock number", "inventory number")):
        return "sku"
    if "superseded" in folded or folded.startswith("other "):
        return "other"
    explicit = (
        "manufacturer part number" in folded
        or "oem part number" in folded
        or "oe part number" in folded
        or folded in {"mpn", "oem", "oe number", "oe oem part number"}
    )
    if explicit:
        return "mpn"
    if "part number" in folded:
        return "other"
    return ""


def _aspect_values(aspect):
    values = aspect.get("localizedValues") or aspect.get("values") or []
    if values:
        return values
    if aspect.get("value") not in (None, ""):
        return [aspect.get("value")]
    return []


def _placement_fields(value):
    words = set(re.findall(r"[a-z]+", str(value or "").casefold()))
    configuration = "front" if "front" in words else "rear" if "rear" in words else ""
    side = "left" if "left" in words else "right" if "right" in words else ""
    return configuration, side


def _fitment_from_aspects(aspects, source):
    """Seller or catalog statements about where a part goes. They are claims, not a match."""
    claims = []
    configuration = side = assembly = ""
    for aspect in aspects or []:
        if not isinstance(aspect, dict):
            continue
        name = " ".join(str(aspect.get("localizedName") or aspect.get("name") or "").casefold().replace("_", " ").split())
        values = [str(value).strip() for value in _aspect_values(aspect) if str(value or "").strip()]
        text = ", ".join(values)
        if not name or not text:
            continue
        if "placement" in name:
            claims.append({"source": source, "type": "placement", "value": text})
            found_configuration, found_side = _placement_fields(text)
            configuration = configuration or found_configuration
            side = side or found_side
        elif "assembly" in name:
            claims.append({"source": source, "type": "assembly", "value": text})
            assembly = assembly or text
        elif name in {"fitment", "vehicle fitment", "compatibility"} or "compatible" in name:
            claims.append({"source": source, "type": "vehicle_claim", "value": text})
    return claims, configuration, side, assembly


def _read_aspects(identifiers, seen, aspects, source):
    for aspect in aspects or []:
        if not isinstance(aspect, dict):
            continue
        kind = _aspect_kind(aspect.get("localizedName") or aspect.get("name"))
        if not kind:
            continue
        for value in _aspect_values(aspect):
            if kind in ("mpn", "sku", "other"):
                labeled, matches, verified = _classify_aspect(kind, value, source)
            else:
                labeled, matches, verified = kind, False, False
            _add_identifier(identifiers, seen, labeled, value, source, matches, verified)


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
        labeled, matches, verified = _classify_aspect("mpn", value, "catalog")
        _add_identifier(identifiers, seen, labeled, value, "catalog", matches, verified)
    for value in product.get("gtins") or []:
        _add_identifier(identifiers, seen, "gtin", value, "catalog", False)
    _add_identifier(identifiers, seen, "epid", product.get("epid"), "catalog", False)
    # Search puts catalog aspects on product. The item call puts seller specifics
    # on localizedAspects, with one value field rather than a list.
    _read_aspects(identifiers, seen, product.get("aspects"), "catalog")
    _read_aspects(identifiers, seen, item.get("localizedAspects"), "seller")
    catalog_claims, configuration, side, assembly = _fitment_from_aspects(product.get("aspects"), "catalog")
    seller_claims, seller_configuration, seller_side, seller_assembly = _fitment_from_aspects(
        item.get("localizedAspects"), "seller",
    )
    return {
        "item_id": str(item.get("itemId") or "").strip(),
        "price_amount": price_amount,
        "price_currency": price_currency,
        "shipping_amount": shipping_amount,
        "shipping_currency": shipping_currency,
        "item_country": _country(item.get("itemLocation")),
        "identifiers": identifiers,
        "fitment_claims": catalog_claims + seller_claims,
        "configuration": seller_configuration or configuration,
        "side": seller_side or side,
        "assembly": seller_assembly or assembly,
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

    def search_parts(self, query, max_price=None, limit=10, sort="price"):
        """sort price keeps a watch on the cheapest hits. A buyer search omits it for relevance."""
        params = {
            "q": query,
            "category_ids": PARTS_CATEGORY,
            "limit": limit,
            "fieldgroups": "PRODUCT",
        }
        if sort:
            params["sort"] = sort
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

    def peek_item(self, item_id):
        """Item details already read in this worker. A miss is None, not a call."""
        return _cached_item(str(item_id or "").strip())

    def get_item(self, item_id, timeout=6):
        """One listing, including seller item specifics the search summary omits."""
        item_id = str(item_id or "").strip()
        if not item_id:
            return {}
        cached = _cached_item(item_id)
        if cached is not None:
            return cached
        url = ITEM_URL + quote(item_id, safe="")
        try:
            response = self._request(url, timeout=timeout)
            if response.status_code == 401:
                self._clear_token()
                self._get_token()
                response = self._request(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload:
                return {}
            _store_item(item_id, payload)
            return payload
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
