@echo off & python --version >nul 2>&1 && (python -x "%~f0" %* & pause & exit /b) || (echo ERROR: Python is not installed. Download it from https://python.org & pause & exit /b)
#!/usr/bin/env python3
"""
SFB Seeker v2.1 — Autonomous buyer-seller matching
Searches forums for WTB posts, finds listings across marketplaces, AI-matches them.

Double-click to start. Leave running.
"""

import json
import os
import sys
import time
import re
import hashlib
import random
import subprocess
from datetime import datetime, timezone, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

# --- Configuration ---
# Keys come from the environment. They are not stored in this file.
# Set them once, then open a new window before running this file:
#   setx OPENAI_KEY "your-openai-key"
#   setx SUPABASE_KEY "your-supabase-service-key"
OPENAI_KEY = os.environ.get("OPENAI_KEY", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fyxglozykvhrfejrcrqz.supabase.co").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
COOLDOWN_HOURS = 24
POLL_INTERVAL = 15
SEARCH_DELAY = 3          # seconds between DuckDuckGo searches to avoid throttling
SEEN_FILE = os.path.join(os.path.expanduser("~"), "sfb_seen_items.json")
USAGE_FILE = os.path.join(os.path.expanduser("~"), "sfb_ai_usage.json")
# Automatic category searches stay off unless SEEKER_AUTO_SEARCH=1.
# Dashboard Run Seeker requests still run, until the daily OpenAI cap.
AUTO_SEARCH = os.environ.get("SEEKER_AUTO_SEARCH", "0").strip().lower() in ("1", "true", "yes", "on")
try:
    DAILY_AI_CAP = int(os.environ.get("SEEKER_DAILY_AI_CAP", "40"))
except ValueError:
    DAILY_AI_CAP = 40


def ai_calls_today():
    today = datetime.now(timezone.utc).date().isoformat()
    if os.path.exists(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r") as f:
                data = json.load(f)
            if data.get("date") == today:
                return int(data.get("calls", 0))
        except Exception:
            pass
    return 0


def record_ai_call():
    today = datetime.now(timezone.utc).date().isoformat()
    calls = ai_calls_today() + 1
    with open(USAGE_FILE, "w") as f:
        json.dump({"date": today, "calls": calls}, f)
    return calls


def ai_cap_reached():
    return ai_calls_today() >= DAILY_AI_CAP


class ReservationClosed(Exception):
    """The model was not called. The run keeps this reason."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ClaimClosed(Exception):
    """The claim did not succeed. Do not take the same job by another path."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def rpc_failure(exc):
    """Timeout and permission stay closed. A missing function is the pre-cutover case."""
    text = " ".join(str(exc or "").split()).lower()
    if any(token in text for token in ("timeout", "timed out", "statement timeout", "57014", "canceling statement")):
        return "timeout"
    if any(token in text for token in ("permission", "42501", "not authorized", "unauthorized", "jwt", "pgrst301")):
        return "permission"
    if any(token in text for token in ("pgrst202", "could not find the function", "schema cache", "does not exist")):
        return "missing"
    return "other"


def reserve_closed_reason(kind, detail=""):
    if kind == "cap":
        return f"daily OpenAI cap of {DAILY_AI_CAP} reached"
    if kind == "timeout":
        return "AI reservation timed out"
    if kind == "permission":
        return "AI reservation was denied"
    if kind == "missing":
        return "AI reservation is not available"
    text = " ".join(str(detail or "").split())
    if text:
        return f"AI reservation failed — {text[:180]}"
    return "AI reservation failed"


def claim_closed_reason(kind, detail=""):
    if kind == "timeout":
        return "job claim timed out"
    if kind == "permission":
        return "job claim was denied"
    if kind == "missing":
        return "job claim is not available"
    text = " ".join(str(detail or "").split())
    if text:
        return f"job claim failed — {text[:180]}"
    return "job claim failed"


def usage_blocked(sb):
    """The database cap counts once the usage table exists. A read error stays closed."""
    if sb is None:
        return ai_cap_reached()
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        rows = (
            sb.table("seeker_ai_usage")
            .select("baseline,succeeded")
            .eq("usage_date", today)
            .limit(1)
            .execute()
            .data
        )
        open_rows = (
            sb.table("seeker_ai_reservations")
            .select("id")
            .eq("usage_date", today)
            .eq("state", "reserved")
            .execute()
            .data
        )
    except Exception as exc:
        if rpc_failure(exc) == "missing":
            return ai_cap_reached()
        raise ReservationClosed(reserve_closed_reason(rpc_failure(exc), exc))
    if not rows:
        return False
    row = rows[0]
    return (row.get("baseline") or 0) + (row.get("succeeded") or 0) + len(open_rows or []) >= DAILY_AI_CAP


WORKER_ID = f"{os.environ.get('COMPUTERNAME', 'pc')}-{os.getpid()}"
WORKER_REVISION = "reservation-v2"


def reservation_id_from(payload):
    """A reserve call returns one reservation id. False, null, and empty are no reservation."""
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if payload is None or payload is False:
        return None
    text = str(payload).strip()
    if not text or text.lower() in {"false", "none", "null"}:
        return None
    return text


def market_scope_text(name, market_id):
    return (
        "Market discovery\n"
        f"Market: {name}\n"
        f"Market id: {market_id}\n"
        "This market name is search scope, not a buyer request."
    )


def is_market_discovery_run(run):
    if str((run or {}).get("job_kind") or "") == "market_discovery":
        return True
    return str((run or {}).get("search_query") or "").startswith("Market discovery\n")


def source_identity(url):
    for pattern in (
        r"facebook\.com/marketplace/(?:item|profile)/(\d+)",
        r"/itm/(\d+)",
        r"reverb\.com/item/(\d+)",
    ):
        found = re.search(pattern, url or "", re.I)
        if found:
            return found.group(1)
    return ""


def _reserve_ai(sb, allow_file_counter):
    """A timeout or permission error does not use the JSON file or call the model."""
    if sb is None:
        if allow_file_counter and not ai_cap_reached():
            return ("file", None)
        kind = "cap" if ai_cap_reached() else "missing"
        raise ReservationClosed(reserve_closed_reason(kind))
    try:
        result = sb.rpc(
            "reserve_seeker_ai",
            {"cap": DAILY_AI_CAP, "baseline": ai_calls_today()},
        ).execute()
    except Exception as exc:
        kind = rpc_failure(exc)
        if kind == "missing" and allow_file_counter and not ai_cap_reached():
            return ("file", None)
        if kind == "missing" and allow_file_counter:
            raise ReservationClosed(reserve_closed_reason("cap"))
        raise ReservationClosed(reserve_closed_reason(kind, exc))
    token = reservation_id_from(result.data)
    if token:
        return ("db", token)
    raise ReservationClosed(reserve_closed_reason("cap"))


def _settle_ai(sb, reservation_id, outcome):
    """Settle the original reservation once. A second call is the database's no-op."""
    if not reservation_id:
        return False
    try:
        sb.rpc(
            "settle_seeker_ai",
            {"reservation_id": reservation_id, "outcome": outcome},
        ).execute()
    except Exception as exc:
        print(f"    AI settlement error: {exc}")
        return False
    return True


def write_heartbeat(sb):
    try:
        sb.table("seeker_worker_heartbeat").upsert({
            "worker_id": WORKER_ID,
            "seen_at": datetime.now(timezone.utc).isoformat(),
            "detail": "manual discovery and dashboard requests",
            "revision": WORKER_REVISION,
        }).execute()
    except Exception:
        return False
    return True


def claim_next_run(sb):
    try:
        result = sb.rpc("claim_next_seeker_run", {"worker": WORKER_ID}).execute()
    except Exception as exc:
        kind = rpc_failure(exc)
        if kind == "missing":
            return "fallback"
        raise ClaimClosed(claim_closed_reason(kind, exc))
    data = result.data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    return data or None

# What to search for — WTB queries by category (shuffled each cycle)
SEARCH_PLAN = [
    # Audio — general
    {"category": "audio", "buyer_query": "WTB speakers forum", "seller_query": "speakers"},
    {"category": "audio", "buyer_query": "WTB amplifier forum", "seller_query": "amplifier"},
    {"category": "audio", "buyer_query": "WTB receiver vintage forum", "seller_query": "vintage receiver"},
    {"category": "audio", "buyer_query": "WTB turntable forum", "seller_query": "turntable"},
    {"category": "audio", "buyer_query": "WTB headphones forum", "seller_query": "headphones"},
    {"category": "audio", "buyer_query": "WTB subwoofer forum", "seller_query": "subwoofer"},
    # Audio — specific brands
    {"category": "audio", "buyer_query": "want to buy Marantz forum", "seller_query": "Marantz"},
    {"category": "audio", "buyer_query": "want to buy Klipsch forum", "seller_query": "Klipsch speakers"},
    {"category": "audio", "buyer_query": "WTB McIntosh amplifier forum", "seller_query": "McIntosh amplifier"},
    {"category": "audio", "buyer_query": "WTB Technics turntable forum", "seller_query": "Technics turntable"},
    {"category": "audio", "buyer_query": "WTB Sennheiser headphones forum", "seller_query": "Sennheiser headphones"},
    {"category": "audio", "buyer_query": "WTB JBL vintage speakers forum", "seller_query": "JBL vintage speakers"},
    # Cameras — general
    {"category": "cameras", "buyer_query": "WTB camera forum", "seller_query": "camera"},
    {"category": "cameras", "buyer_query": "WTB camera lens forum", "seller_query": "camera lens"},
    {"category": "cameras", "buyer_query": "WTB film camera forum", "seller_query": "film camera"},
    # Cameras — specific brands
    {"category": "cameras", "buyer_query": "WTB Nikon camera forum", "seller_query": "Nikon camera"},
    {"category": "cameras", "buyer_query": "WTB Canon lens forum", "seller_query": "Canon lens"},
    {"category": "cameras", "buyer_query": "WTB Leica camera forum", "seller_query": "Leica camera"},
    {"category": "cameras", "buyer_query": "WTB Fujifilm camera forum", "seller_query": "Fujifilm camera"},
    {"category": "cameras", "buyer_query": "WTB Hasselblad forum", "seller_query": "Hasselblad"},
    {"category": "cameras", "buyer_query": "WTB Pentax camera forum", "seller_query": "Pentax camera"},
    # Musical instruments
    {"category": "instruments", "buyer_query": "WTB guitar forum", "seller_query": "guitar"},
    {"category": "instruments", "buyer_query": "WTB Fender guitar forum", "seller_query": "Fender guitar"},
    {"category": "instruments", "buyer_query": "WTB Gibson guitar forum", "seller_query": "Gibson guitar"},
    {"category": "instruments", "buyer_query": "WTB synthesizer forum", "seller_query": "synthesizer"},
    {"category": "instruments", "buyer_query": "want to buy bass guitar forum", "seller_query": "bass guitar"},
    {"category": "instruments", "buyer_query": "WTB piano keyboard forum", "seller_query": "piano keyboard"},
    {"category": "instruments", "buyer_query": "WTB drum kit forum", "seller_query": "drum kit"},
    {"category": "instruments", "buyer_query": "WTB violin cello forum", "seller_query": "violin cello"},
    # Collectibles
    {"category": "collectibles", "buyer_query": "WTB trading cards forum", "seller_query": "trading cards"},
    {"category": "collectibles", "buyer_query": "WTB baseball cards vintage forum", "seller_query": "baseball cards vintage"},
    {"category": "collectibles", "buyer_query": "WTB Pokemon cards forum", "seller_query": "Pokemon cards"},
    {"category": "collectibles", "buyer_query": "WTB coins collection forum", "seller_query": "coins collection"},
    {"category": "collectibles", "buyer_query": "WTB comic books forum", "seller_query": "comic books vintage"},
    {"category": "collectibles", "buyer_query": "WTB vinyl records forum", "seller_query": "vinyl records"},
    {"category": "collectibles", "buyer_query": "WTB sports memorabilia forum", "seller_query": "sports memorabilia"},
    {"category": "collectibles", "buyer_query": "WTB vintage watch forum", "seller_query": "vintage watch"},
]

# --- Install dependencies ---
def install_deps():
    deps = {
        "supabase": "supabase>=2.34.0",
        "openai": "openai",
        "ddgs": "ddgs",
        "httpx": "httpx",
    }
    for mod, pkg in deps.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"Installing {pkg}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

install_deps()

from supabase import create_client
from openai import OpenAI
from ddgs import DDGS

# --- Seen items tracking ---
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def item_key(url):
    return hashlib.md5(url.encode()).hexdigest()

def is_seen(seen, url):
    key = item_key(url)
    if key not in seen:
        return False
    last_seen = datetime.fromisoformat(seen[key]["timestamp"])
    if datetime.now(timezone.utc) - last_seen > timedelta(hours=COOLDOWN_HOURS):
        return False  # Cooldown expired
    return True

def mark_seen(seen, url, title=""):
    key = item_key(url)
    seen[key] = {
        "url": url,
        "title": title[:100],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_seen(seen)

# --- Search functions ---
def ddg_text(query, max_results=10):
    """One DuckDuckGo query, retried once when the first response is empty."""
    for attempt in range(2):
        try:
            rows = list(DDGS().text(query, max_results=max_results))
            print(f"    DuckDuckGo returned {len(rows)} raw results")
            if rows or attempt == 1:
                return rows
            print("    Empty result. Retrying once.")
        except Exception as e:
            print(f"    Search error: {e}")
            if attempt == 1:
                return []
        time.sleep(SEARCH_DELAY)
    return []


def search_wtb_posts(query, seen, max_results=15):
    """Search for WTB forum posts using DuckDuckGo."""
    posts = []
    try:
        results = ddg_text(query, max_results=max_results)
        for r in results:
            url = r.get("href", "")
            title = r.get("title", "")
            body = r.get("body", "")
            # Skip shopping sites (we want forum posts)
            if any(s in url.lower() for s in ["ebay.com", "amazon.com", "walmart.com", "target.com", "bestbuy.com"]):
                continue
            # Skip if already seen
            if is_seen(seen, url):
                continue
            posts.append({
                "title": title,
                "text": f"{title}\n{body}",
                "url": url,
                "platform": extract_platform(url),
            })
            if len(posts) >= 5:
                break
    except Exception as e:
        print(f"    Search error: {e}")
    return posts


LISTING_QUERIES = (
    "{query} for sale",
    "{query} (site:ebay.com/itm OR site:reverb.com OR site:craigslist.org OR site:mercari.com OR site:offerup.com OR site:etsy.com OR site:amazon.com)",
    "{query} (site:pelicanparts.com OR site:bringatrailer.com OR site:carsandbids.com OR site:audiogon.com OR site:shopgoodwill.com OR site:facebook.com/marketplace)",
)

SKIP_LISTING_HOSTS = (
    "wikipedia.org", "youtube.com", "youtu.be", "pinterest.", "instagram.com",
    "tiktok.com", "linkedin.com", "google.", "bing.com", "duckduckgo.com",
    "yelp.com", "reddit.com",
)


def facebook_item_url(url):
    """A direct Marketplace item page. A profile is not one of these."""
    return bool(re.search(r"facebook\.com/marketplace/item/\d+", (url or "").lower()))


def facebook_profile_url(url):
    return bool(re.search(r"facebook\.com/marketplace/profile/\d+", (url or "").lower()))


def listing_allowed(url, title, body):
    url_l = (url or "").lower()
    if "facebook.com" in url_l:
        return facebook_item_url(url) or facebook_profile_url(url)
    if any(host in url_l for host in SKIP_LISTING_HOSTS):
        return False
    blob = f"{title} {body}".lower()
    if any(token in url_l for token in ("/forum", "/forums", "rennlist.com")):
        return any(word in blob for word in ("for sale", "fs:", "wts", "selling", "$"))
    return True


def _url_path(url):
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://example.test/" + raw.lstrip("/")
    return urlsplit(raw).path.lower()


def is_catalog_page(url):
    """A product, search, category, or model page. It is a source, not an offer."""
    url_l = (url or "").lower()
    path = _url_path(url)
    if re.search(r"reverb\.com/(?:p|m)/", url_l):
        return True
    if "/itm/" in path or "/marketplace/item" in path or re.search(r"/item/\d+", path):
        return False
    return any(marker in path for marker in (
        "/sch/", "/search", "/category", "/categories", "/browse", "/model/",
    ))


def is_item_page(url):
    """One seller's listing. A product or search page is not one."""
    if is_catalog_page(url):
        return False
    url_l = (url or "").lower()
    return any(token in url_l for token in ("/itm/", "/listing/", "/item/", "/marketplace/item"))


# A web-search excerpt is not evidence that a listing is still for sale.
# The site in a URL is not the seller's location. ebay.co.uk does not mean the UK.
ENDED_PAGE_MARKS = (
    "this listing has ended",
    "this listing was ended",
    "listing has ended",
    "listing ended",
    "this listing is no longer available",
    "listing is no longer available",
    "this item is no longer available",
    "no longer available",
    "sold ·",
    "sold •",
)
CURRENT_OFFER_MARKS = (
    "buy it now",
    "add to cart",
    "place bid",
    "make an offer",
    "make offer",
)


def availability_label(status):
    """Found for sale requires a page that still offers the item."""
    if status == "available":
        return "Found for sale"
    if status == "ended":
        return "Listing ended"
    return "Availability unconfirmed"


POLICY_NOT_SOLD = (
    "sold as-described",
    "sold as described",
)
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
PAGE_BUDGET = 8
MATCH_TARGET = 4
SIMILAR_SECTION = re.compile(
    r"(?is)<section\b[^>]*similarlistings[^>]*>.*?</section>"
)


def classify_availability(page_text):
    """Page text can confirm ended or a current offer. An excerpt cannot."""
    text = unescape(page_text or "")
    for phrase in POLICY_NOT_SOLD:
        text = re.sub(re.escape(phrase), " ", text, flags=re.I)
    text = " ".join(text.lower().split())
    if not text:
        return "unconfirmed"
    if any(mark in text for mark in ENDED_PAGE_MARKS):
        return "ended"
    if any(mark in text for mark in CURRENT_OFFER_MARKS):
        return "available"
    return "unconfirmed"


def model_codes(query):
    """The requested model, not a shorter code that merely shares a prefix."""
    codes = []
    pattern = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+|[A-Za-z]*\d[A-Za-z0-9]*"
    for token in re.findall(pattern, query or ""):
        code = re.sub(r"[^a-z0-9]", "", token.lower())
        if len(code) >= 4 and any(character.isdigit() for character in code):
            codes.append(code)
    kept = []
    for code in sorted(codes, key=len, reverse=True):
        if any(code != other and code in other for other in codes):
            continue
        if code not in kept:
            kept.append(code)
    return kept


def text_has_model(text, query):
    codes = model_codes(query)
    compact = re.sub(r"[^a-z0-9]", "", unescape(text or "").lower())
    if codes:
        return all(code in compact for code in codes)
    words = [
        word for word in re.findall(r"[a-z0-9]{3,}", (query or "").lower())
        if word not in DETAIL_STOP
    ]
    lowered = unescape(text or "").lower()
    return bool(words) and all(word in lowered for word in words)


def page_unreadable(html):
    sample = (html or "")[:12000].lower()
    if len(html or "") < 200:
        return True
    if "just a moment" in sample and ("cf-" in sample or "challenge" in sample):
        return True
    head = sample[:2000]
    if "access denied" in head and "listing" not in sample:
        return True
    # eBay's bot error is not a listing page and does not prove the item is gone.
    if "something went wrong" in head and "buy it now" not in head and "add to cart" not in head:
        return True
    return False


def canonical_item_url(url, base=""):
    """Direct item path. Tracking query parameters are not part of the item."""
    raw = unescape(url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/") and base:
        host = urlsplit(base)
        raw = f"{host.scheme}://{host.netloc}{raw}"
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc or not is_item_page(parts.path):
        return ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def similar_sections(html):
    return " ".join(SIMILAR_SECTION.findall(html or ""))


def excise_similar_sections(html):
    return SIMILAR_SECTION.sub(" ", html or "")


def catalog_offer_links(html, query, page_url):
    """Individual listings linked from a model or search page. The page itself is not one."""
    source = excise_similar_sections(html or "")
    page_matches = text_has_model(plain_page_text(source), query)
    found = []
    seen = set()
    anchors = re.findall(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', source)
    for href, label in anchors:
        url = canonical_item_url(href, page_url)
        if not url or url in seen or not is_item_page(url) or is_catalog_page(url):
            continue
        if url.rstrip("/") == (canonical_item_url(page_url) or page_url or "").rstrip("/"):
            continue
        text = plain_page_text(label)
        words = [
            word for word in re.findall(r"[a-z0-9]{3,}", text.lower())
            if word not in DETAIL_STOP
        ]
        if words and not text_has_model(text, query):
            continue
        if not words and not page_matches:
            continue
        seen.add(url)
        found.append({"url": url, "title": text[:180] or "Listing", "text": text[:500]})
    return found


def similar_item_links(html, query, page_url):
    """Item links whose own text names the requested model. A different model stays out."""
    found = []
    seen = set()
    anchors = re.findall(
        r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html or "",
    )
    for href, label in anchors:
        url = canonical_item_url(href, page_url)
        text = plain_page_text(label)
        if not url or url in seen or url == canonical_item_url(page_url):
            continue
        if not text_has_model(text, query):
            continue
        seen.add(url)
        found.append({"url": url, "title": text[:180], "text": text[:500]})
    return found


def _amount(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    return float(text)


def _json_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _json_nodes(item)
        return
    if not isinstance(data, dict):
        return
    yield data
    graph = data.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _json_nodes(item)


def structured_offer(html, page_url):
    """This listing's own offer. A recommended item's price is not this offer."""
    page = canonical_item_url(page_url) or (page_url or "").split("?")[0]
    for block in re.findall(r"(?is)<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html or ""):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in _json_nodes(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" not in kinds:
                continue
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = next((item for item in offers if isinstance(item, dict)), {})
            if not isinstance(offers, dict):
                continue
            offer_url = canonical_item_url(offers.get("url") or "", page_url) or (offers.get("url") or "").split("?")[0]
            if offer_url and page and offer_url.rstrip("/") != page.rstrip("/"):
                continue
            price = _amount(offers.get("price"))
            details = offers.get("shippingDetails") or {}
            if isinstance(details, list):
                details = details[0] if details else {}
            rate = details.get("shippingRate") if isinstance(details, dict) else None
            shipping = _amount(rate.get("value")) if isinstance(rate, dict) else None
            if price is not None:
                return price, shipping
    return None, None


def current_asking_price(text):
    """The seller's current price. The crossed-out original price is not the ask."""
    found = re.search(r"(?i)\bnew price\s*\$\s*([\d,]+(?:\.\d{1,2})?)", text or "")
    if not found:
        return None
    try:
        return float(found.group(1).replace(",", ""))
    except ValueError:
        return None


def inspect_listing_html(html, query, page_url):
    """Availability comes from this listing, not from a recommended item."""
    if page_unreadable(html):
        return {
            "status": "unconfirmed",
            "text": "",
            "location": "",
            "similar": [],
            "price": None,
            "shipping": None,
            "readable": False,
        }
    main_text = plain_page_text(excise_similar_sections(html))
    price, shipping = structured_offer(html, page_url)
    # A crossed-out original price is not the price the seller is asking now.
    visible = current_asking_price(main_text)
    if visible is not None:
        price = visible
    return {
        "status": classify_availability(main_text),
        "text": main_text,
        "location": stated_location(main_text),
        "similar": similar_item_links(html, query, page_url),
        "price": price,
        "shipping": shipping,
        "readable": True,
    }


def stated_location(page_text):
    """A place written on the listing page. The URL host is never a place."""
    text = " ".join((page_text or "").split())
    if not text:
        return ""
    patterns = (
        r"(?:located in|ships from|item location)\s*:\s*([A-Z][A-Za-z.'-]+,\s*[A-Z][A-Za-z.'-]+(?:,\s*[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+)?)?)",
        r"\b([A-Z][a-z]+,\s*[A-Z]{2},\s*United States)\b",
        r"\b([A-Z][a-z]+,\s*[A-Z]{2})\b",
    )
    for index, pattern in enumerate(patterns):
        found = re.search(pattern, text, re.I if index == 0 else 0)
        if found:
            return " ".join(found.group(1).split())
    return ""


def required_location(buyer_text):
    """A location the buyer asked for. 'None stated' is not a requirement."""
    text = buyer_text or ""
    requirements = re.search(r"Requirements:\s*(.+)", text, re.I)
    if not requirements:
        return ""
    value = requirements.group(1).strip()
    if not value or value.lower().startswith("none stated"):
        return ""
    return value


def location_meets_request(buyer_text, seller_location):
    """Unknown does not satisfy a stated place. A URL does not count as a place."""
    required = required_location(buyer_text)
    if not required:
        return True
    if not seller_location:
        return False
    return required.lower() in seller_location.lower() or seller_location.lower() in required.lower()


def instrument_claim(record):
    """Year, model, finish, and stated city. A platform URL is not an identity."""
    blob = " ".join(
        str(record.get(key) or "")
        for key in ("title", "text", "page_text", "location")
    )
    year = re.search(r"\b(19|20)\d{2}\b", blob)
    model = re.search(r"\bSE-?600\b", blob, re.I)
    finish = "natural" if re.search(r"\bnatural\b", blob, re.I) else ""
    city = ""
    place = re.search(r"\b([A-Z][a-z]+),\s*([A-Z]{2})\b", record.get("location") or blob)
    if place:
        city = place.group(0)
    if not (year and model and finish and city):
        return ""
    return f"{year.group(0)}|se600|{finish}|{city.lower()}"


def flag_cross_posts(listings):
    """Same claimed instrument on two sites is one candidate until identity is shown."""
    groups = {}
    for listing in listings:
        key = instrument_claim(listing)
        listing["cross_post"] = ""
        if key:
            groups.setdefault(key, []).append(listing)
    for group in groups.values():
        urls = {item.get("url") for item in group}
        if len(urls) < 2:
            continue
        note = (
            "Possible cross-post. "
            "Not counted as two instruments or two comparisons "
            "until identity is established."
        )
        for item in group:
            item["cross_post"] = note
    return listings


def active_offer(listing, buyer_text, confidence):
    """Product-match confidence cannot keep an ended listing active."""
    status = listing.get("availability") or "unconfirmed"
    if status == "ended":
        return False
    if not location_meets_request(buyer_text, listing.get("location") or ""):
        return False
    return bool(confidence)


def plain_page_text(html):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(unescape(text).split())[:8000]


def read_listing_page(url):
    """Read a public listing page. Failure leaves availability unconfirmed."""
    if not url or not url.lower().startswith("https://"):
        return ""
    try:
        request = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (SFB availability check)"},
        )
        with urlopen(request, timeout=12) as response:
            raw = response.read(400_000)
        return plain_page_text(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return ""


def edge_executable():
    candidates = (
        os.environ.get("EDGE_PATH", ""),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    )
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def read_rendered_html(url):
    """Facebook paints the item list and the sold heading after load."""
    edge = edge_executable()
    if not edge or not url:
        return ""
    profile = Path(os.environ.get("TEMP", ".")) / f"sfb-edge-{os.getpid()}-{time.time_ns()}"
    try:
        profile.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [
                edge,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                f"--user-data-dir={profile}",
                "--user-agent=" + BROWSER_UA,
                "--virtual-time-budget=20000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
    except Exception:
        return ""
    return (completed.stdout or b"").decode("utf-8", errors="ignore")


def read_rendered_page(url):
    return plain_page_text(read_rendered_html(url))


def read_listing_html(url):
    """Raw item page. A blocked response is replaced by a rendered page."""
    raw = ""
    if url and url.lower().startswith("https://"):
        try:
            request = Request(url, headers={"User-Agent": BROWSER_UA})
            with urlopen(request, timeout=12) as response:
                raw = response.read(800_000).decode("utf-8", errors="ignore")
        except Exception:
            raw = ""
    if page_unreadable(raw):
        rendered = read_rendered_html(url)
        if not page_unreadable(rendered):
            return rendered
    return raw


DETAIL_STOP = {
    "marketplace", "facebook", "available", "stock", "sort", "item", "items",
    "for", "sale", "the", "and", "with", "from", "this", "that", "your",
    "profile", "seller", "in", "by", "of", "a", "to", "on",
}


def detail_tokens(text):
    """Product words and stated details. A profile's stock label is not a detail."""
    raw = unescape(text or "").lower().replace("se-600", "se600").replace("se 600", "se600")
    tokens = []
    for word in re.findall(r"\$?\d+|[a-z][a-z0-9'-]{2,}", raw):
        if word in DETAIL_STOP or word == "available":
            continue
        if word.startswith("$"):
            digits = word[1:]
            if digits.isdigit():
                tokens.append(digits)
            continue
        if word.isdigit() and len(word) < 4 and not re.fullmatch(r"(19|20)\d{2}", word):
            continue
        if word.isdigit() or len(word) >= 4 or re.fullmatch(r"(19|20)\d{2}", word):
            tokens.append(word)
    seen = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen


def meaningful_tokens(tokens):
    return [
        token for token in tokens
        if not token.isdigit() and not re.fullmatch(r"(19|20)\d{2}", token)
    ]


def extract_facebook_cards(html):
    """Item cards on a rendered profile. Order is not a choice."""
    cards = []
    seen = set()
    pattern = re.compile(r"marketplace/item/(\d+)", re.I)
    source = html or ""
    for found in pattern.finditer(source):
        item_id = found.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)
        window_html = source[found.end():found.end() + 700]
        next_card = re.search(r"marketplace/item/\d+", window_html, re.I)
        if next_card:
            window_html = window_html[:next_card.start()]
        window = plain_page_text(window_html)
        cards.append({
            "id": item_id,
            "url": f"https://www.facebook.com/marketplace/item/{item_id}/",
            "text": window,
        })
    return cards


def choose_facebook_card(cards, wanted):
    """The matching card wins. A tie, or the first card, is not a choice."""
    ranked = []
    for card in cards:
        text = (card.get("text") or "").lower().replace("se-600", "se600")
        hits = [token for token in wanted if token in text]
        product = meaningful_tokens(hits)
        if not product:
            continue
        ranked.append((len(product), len(hits), card, hits))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best = ranked[0]
    tied = [row for row in ranked if row[0] == best[0] and row[1] == best[1]]
    if len(tied) != 1:
        return None
    return best[2], best[3]


def same_item_page(page_text, hits):
    page = unescape(page_text or "").lower().replace("se-600", "se600")
    required = meaningful_tokens(hits)
    return bool(required) and all(token in page for token in required)


def unresolved_profile(profile_url):
    return {
        "url": profile_url,
        "discovery_url": profile_url,
        "resolved": False,
        "page_text": "",
        "availability": "unconfirmed",
        "link_label": "View seller profile",
        "keep_ended": False,
    }


def resolve_facebook_profile(profile_url, request_text, excerpt, read_html, read_item):
    """Follow one matching card. A stock label on the profile is not availability."""
    html = read_html(profile_url) if profile_url else ""
    cards = extract_facebook_cards(html)
    wanted = detail_tokens(f"{request_text}\n{excerpt}")
    chosen = choose_facebook_card(cards, wanted) if cards else None
    if not chosen:
        return unresolved_profile(profile_url)
    card, hits = chosen
    item_body = read_item(card["url"]) if card.get("url") else ""
    page_text = plain_page_text(item_body) if "<" in (item_body or "")[:300] else (item_body or "")
    if not same_item_page(page_text, hits):
        return unresolved_profile(profile_url)
    status = classify_availability(page_text)
    return {
        "url": card["url"],
        "discovery_url": profile_url,
        "resolved": True,
        "page_text": page_text,
        "availability": status,
        "link_label": "View item",
        "keep_ended": status == "ended",
    }


def listing_record(title, body, url, page_text, status, location, discovery_url="", link_label=""):
    evidence = "listing_page" if page_text else "search_excerpt"
    return {
        "title": title,
        "text": f"{title}\n{body}",
        "url": url,
        "discovery_url": discovery_url,
        "link_label": link_label,
        "keep_ended": False,
        "price": extract_price_from_text(f"{title} {body} {page_text[:400]}"),
        "shipping": extract_shipping(f"{title} {body} {page_text[:1500]}"),
        "platform": extract_platform(url),
        "is_item": facebook_item_url(url) or is_item_page(url),
        "page_text": page_text,
        "availability": status,
        "availability_evidence": evidence,
        "location": location or "",
    }


def _direct_listing(url):
    """A seller's item page. A price guide or unresolved social post is not one."""
    url_l = (url or "").lower()
    if "facebook.com" in url_l or re.search(r"reverb\.com/(?:p|m)/", url_l):
        return False
    return is_item_page(url)


def _queue_similar(queue, inspected, links, discovery, item_query):
    """Follow a same-model link before a guide or a different model, after unread search listings."""
    added = []
    for link in links or []:
        if link["url"] in inspected or any(item.get("url") == link["url"] for item in queue):
            continue
        added.append({
            "url": link["url"],
            "title": link["title"],
            "body": link["text"],
            "discovery_url": discovery,
            "origin": "similar",
        })
    index = 0
    while index < len(queue):
        item = queue[index]
        described = f"{item.get('title') or ''}\n{item.get('body') or ''}"
        if (
            item.get("origin") == "search"
            and _direct_listing(item.get("url"))
            and text_has_model(described, item_query)
        ):
            index += 1
            continue
        break
    for item in reversed(added):
        queue.insert(index, item)


def expand_candidates(seeds, query, fetch_html, budget=PAGE_BUDGET, buyer_text="", target=MATCH_TARGET):
    """Keep opening pages until a qualifying offer is found, or the page budget is used."""
    request = buyer_text or query
    item_query = requested_item(request)
    qualifying = []
    rejected = []
    queue = []
    for seed in seeds:
        item = dict(seed)
        item["origin"] = item.get("origin") or "search"
        queue.append(item)
    inspected = set()
    opened = 0
    attempts = 0
    attempt_cap = max(budget * 2, budget)
    while queue and opened < budget and attempts < attempt_cap and len(qualifying) < target:
        seed = queue.pop(0)
        url = canonical_item_url(seed.get("url") or "") or (seed.get("url") or "")
        if not url or url in inspected:
            continue
        inspected.add(url)
        attempts += 1
        title = seed.get("title") or ""
        body = seed.get("body") or ""
        discovery = seed.get("discovery_url") or ""
        if facebook_profile_url(url):
            resolved = resolve_facebook_profile(
                url, item_query, f"{title}\n{body}", fetch_html, fetch_html,
            )
            status = resolved["availability"]
            if resolved.get("page_text"):
                opened += 1
            if status == "ended":
                continue
            record = listing_record(
                title, body, resolved["url"], resolved["page_text"], status,
                stated_location(resolved["page_text"]),
                resolved["discovery_url"], resolved["link_label"],
            )
            record["qualifying"] = qualifies(record, request)
            if record["qualifying"]:
                qualifying.append(record)
            else:
                rejected.append(record)
            continue
        if is_catalog_page(url):
            html = fetch_html(url)
            opened += 1
            links = []
            if not page_unreadable(html):
                links = catalog_offer_links(html, item_query, url)
            _queue_similar(queue, inspected, links, url, item_query)
            continue
        if not is_item_page(url):
            continue
        info = inspect_listing_html(fetch_html(url), item_query, url)
        described = f"{title}\n{body}\n{info['text']}"
        matches = text_has_model(described, item_query)
        if not info["readable"]:
            if matches:
                _queue_similar(queue, inspected, info["similar"], url, item_query)
            continue
        opened += 1
        if info["status"] == "ended":
            if matches:
                _queue_similar(queue, inspected, info["similar"], url, item_query)
            continue
        if not matches:
            continue
        record = listing_record(
            title or info["text"][:180],
            body or info["text"][:500],
            url,
            info["text"],
            info["status"],
            info["location"],
            discovery,
            "View item",
        )
        if info.get("price") is not None:
            record["price"] = info["price"]
        if info.get("shipping") is not None:
            record["shipping"] = info["shipping"]
        record["qualifying"] = qualifies(record, request)
        if record["qualifying"]:
            qualifying.append(record)
        else:
            rejected.append(record)
        _queue_similar(queue, inspected, info["similar"], url, item_query)
    incomplete = ""
    if len(qualifying) < target and queue and (opened >= budget or attempts >= attempt_cap):
        incomplete = "the page budget was used before every candidate was opened"
    return unique_offers(qualifying + rejected), incomplete


def listing_seeds(rows, limit):
    """Item pages only. A category or article does not take a candidate slot."""
    seeds = []
    seen_urls = set()
    for row in rows or []:
        url = row.get("href") or ""
        title = row.get("title") or ""
        body = row.get("body") or ""
        direct = canonical_item_url(url) or url
        if not url or direct in seen_urls:
            continue
        if not listing_allowed(url, title, body):
            continue
        if not (is_item_page(direct) or facebook_profile_url(direct) or is_catalog_page(direct)):
            continue
        seen_urls.add(direct)
        seeds.append({"url": direct, "title": title, "body": body})
        if len(seeds) >= limit:
            break
    return seeds


def search_listings(query, seen, max_keep=8, buyer_text=""):
    """Search every configured source. A rejected page does not end the search."""
    request = buyer_text or query
    item_query = requested_item(request)
    groups = []
    for template in LISTING_QUERIES:
        search_text = template.format(query=item_query)
        print(f"    Listing search: {search_text[:110]}")
        groups.append(listing_seeds(ddg_text(search_text, max_results=10), PAGE_BUDGET))
        time.sleep(SEARCH_DELAY)
    seeds = []
    seen_urls = set()
    while any(groups) and len(seeds) < PAGE_BUDGET:
        for group in groups:
            if not group or len(seeds) >= PAGE_BUDGET:
                continue
            seed = group.pop(0)
            if seed["url"] in seen_urls:
                continue
            if is_seen(seen, seed["url"]) and not is_item_page(seed["url"]) and not facebook_profile_url(seed["url"]):
                continue
            seen_urls.add(seed["url"])
            seeds.append(seed)
    listings, incomplete = expand_candidates(
        seeds, item_query, read_listing_html, PAGE_BUDGET, request, MATCH_TARGET,
    )
    listings = [
        item for item in listings
        if not (is_seen(seen, item.get("url") or "") and item.get("availability") != "available")
    ]
    qualifying = [item for item in listings if item.get("qualifying")]
    others = [item for item in listings if not item.get("qualifying")]
    kept = flag_cross_posts((qualifying + others)[:max_keep])
    return {"listings": kept, "incomplete": incomplete}


def search_ebay_listings(query, seen, max_results=10):
    """Listings now come from the open web. This name remains for older callers."""
    return search_listings(query, seen, max_keep=max_results)["listings"]


def extract_platform(url):
    url_lower = url.lower()
    named = (
        ("ebay.", "ebay"),
        ("reverb.com", "reverb"),
        ("pelicanparts.com", "pelican"),
        ("bringatrailer.com", "bringatrailer"),
        ("carsandbids.com", "carsandbids"),
        ("craigslist.org", "craigslist"),
        ("facebook.com/marketplace", "facebook"),
        ("mercari.com", "mercari"),
        ("offerup.com", "offerup"),
        ("etsy.com", "etsy"),
        ("shopgoodwill.com", "shopgoodwill"),
        ("amazon.com", "amazon"),
        ("audiogon", "audiogon"),
    )
    for needle, name in named:
        if needle in url_lower:
            return name
    if "usaudiomart" in url_lower: return "usaudiomart"
    if "canuckaudiomart" in url_lower: return "canuckaudiomart"
    if "head-fi" in url_lower: return "head-fi"
    if "fredmiranda" in url_lower: return "fredmiranda"
    if "talkbass" in url_lower: return "talkbass"
    if "thegearpage" in url_lower: return "thegearpage"
    if "polkaudio" in url_lower: return "polkaudio"
    if "reddit" in url_lower: return "reddit"
    if "forum" in url_lower: return "forum"
    return "web"


def extract_ebay_query(buyer_title, fallback_query):
    """Pull specific item keywords from a buyer's WTB title for eBay search."""
    cleaned = buyer_title

    # Step 1: Strip everything after the last " - " or " | " — that's always the site name
    # "WTB: Leica M10R - Buy & Sell Photo-Gear - Photography - FM Forums"
    #   → keep splitting from the right until we have the item part
    for sep in [" | ", " - ", " – ", " — "]:
        if sep in cleaned:
            # Split and keep the first part (the item), drop site/section names
            parts = cleaned.split(sep)
            # The item is usually in the first 1-2 parts, site name is the last 1-2
            if len(parts) >= 3:
                cleaned = sep.join(parts[:2])  # keep first two parts
            elif len(parts) == 2:
                cleaned = parts[0]  # keep first part only
            break

    # Step 2: Remove WTB prefixes
    for prefix in ["[WTB]", "WTB:", "Wtb:", "WTB", "Wtb",
                    "Wanted:", "Wanted", "Want to buy:", "Want to buy",
                    "ISO:", "ISO", "Looking for:", "Looking for",
                    "In Search Of", "W.T.B."]:
        cleaned = re.sub(r'(?i)^' + re.escape(prefix) + r'\s*', '', cleaned)

    # Step 3: Remove page numbers, subreddit refs, and common noise
    cleaned = re.sub(r'(?i)\bPage\s+\d+\b', '', cleaned)
    cleaned = re.sub(r'(?i)\br/\w+', '', cleaned)           # r/synthesizers
    cleaned = re.sub(r'(?i)\b(forum|topic|thread|post)\b', '', cleaned)
    cleaned = re.sub(r'(?i)\b(buy\s*&?\s*sell|classifieds?|marketplace)\b', '', cleaned)
    cleaned = re.sub(r'(?i)\b(photo-?gear|photography|ultimate guitar)\b', '', cleaned)

    # Step 4: Clean up special chars and extra spaces
    cleaned = re.sub(r'[^\w\s.,&/\'-]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = cleaned.strip(" -:,.").strip()

    if len(cleaned) >= 3:
        return cleaned[:60]
    return fallback_query


def extract_price_from_text(text):
    m = re.search(r'\$\s*([\d,]+\.?\d*)', text or "")
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except:
            pass
    return None


def extract_shipping(text):
    """A stated shipping amount. Free shipping is zero. A missing amount stays unknown."""
    blob = unescape(text or "")
    if re.search(r"(?i)\bfree shipping\b", blob):
        return 0.0
    patterns = (
        r"(?i)(?:\bshipping\b[^$\n]{0,40})\$\s*([\d,]+\.?\d*)",
        r"(?i)\$\s*([\d,]+\.?\d*)\s+(?:economy|standard)\s+shipping",
        r"(?i)\+\s*\$\s*([\d,]+\.?\d*)\s*shipping",
    )
    for pattern in patterns:
        found = re.search(pattern, blob)
        if not found:
            continue
        try:
            return float(found.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


# --- AI Matching ---
def ai_evaluate_match(buyer_text, seller_text, client, sb=None, allow_file_counter=True):
    """Use OpenAI to evaluate if a buyer post matches a seller listing."""
    reservation_kind, reservation_id = _reserve_ai(sb, allow_file_counter)
    prompt = f"""You are a matching engine for a marketplace. Your job is to determine if a seller's listing could fulfill what a buyer is looking for.

BUYER'S WANT-TO-BUY POST:
{buyer_text[:1500]}

SELLER'S LISTING:
{seller_text[:1500]}

RULES:
- match=true ONLY if the seller listing is a SPECIFIC ITEM (not a category page) that could fulfill the buyer's request
- match=false if the seller listing is a generic eBay category/browse page (titles like "25+ items for sale", "items for sale | Updated", or just a category name)
- match=false if the items are in different categories (e.g. buyer wants Pokemon cards but seller has Bitcoin cards)
- match=false if you can't tell what specific item the seller is offering
- confidence 100: the listing states the same brand AND the same model the buyer named. Do not pick 80 as a default for that case.
- confidence 60-79: same type of item, but the specific model is not the one named
- confidence below 60: weak, vague, or no match — set match=false
- Do not lower the score because availability, shipping, or location is unknown. Those are separate from the product match.

Respond in this exact JSON format:
{{
    "match": true or false,
    "confidence": 0-100,
    "reason": "one sentence explaining why this is or isn't a match",
    "make": "brand name if identifiable",
    "model": "model name/number if identifiable",
    "part": "type of item (speaker, camera, guitar, etc.)"
}}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        if reservation_kind == "db":
            _settle_ai(sb, reservation_id, "succeeded")
            print(f"    OpenAI call reserved and settled. Cap {DAILY_AI_CAP}.")
        else:
            used = record_ai_call()
            print(f"    OpenAI calls today: {used}/{DAILY_AI_CAP}")
        text = response.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        if reservation_kind == "db":
            _settle_ai(sb, reservation_id, "failed")
        print(f"    AI error: {e}")
    return None


def money(amount):
    return f"${amount:,.2f}"


def buyer_price_cap(buyer_text):
    """The item-price limit. Plus shipping does not raise it."""
    text = buyer_text or ""
    line = re.search(r"(?im)^Budget:\s*(.+)$", text)
    if line:
        source = line.group(1).strip()
        if re.match(r"(?i)(not stated|none\b)", source):
            return None
    else:
        paid = re.search(
            r"(?i)(?:\bbudget\b|i would pay|willing to pay|pay up to|no more than)\s*\$?\s*(\d[\d,]*(?:\.\d{1,2})?)",
            text,
        )
        if not paid:
            return None
        source = paid.group(0)
    amount = re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", source)
    if not amount:
        return None
    firm = re.search(r"(?i)\bfirm\b|hard limit|maximum", source) is not None
    negotiable = re.search(r"(?i)flexible|negoti", source) is not None and not firm
    plus_shipping = re.search(r"(?i)plus shipping|\+ shipping|shipping extra", source) is not None
    delivered = re.search(
        r"(?i)including shipping|with shipping|delivered|landed cost|total price",
        source,
    ) is not None
    return {
        "limit": float(amount.group(1).replace(",", "")),
        "plus_shipping": plus_shipping,
        "basis": "item" if plus_shipping or not delivered else "delivered",
        "negotiable": negotiable,
    }


def price_against_budget(item_price, cap, shipping=None):
    """Compare the price the buyer limited. A missing amount stays unknown."""
    if not cap:
        return {"status": "no_cap"}
    limit = cap["limit"]
    basis = cap.get("basis") or "item"
    common = {
        "limit": limit,
        "negotiable": cap["negotiable"],
        "plus_shipping": cap["plus_shipping"],
        "basis": basis,
    }
    if basis == "delivered":
        if item_price is None or shipping is None:
            return {"status": "unknown", **common}
        price = round(float(item_price) + float(shipping), 2)
    else:
        if item_price is None:
            return {"status": "unknown", **common}
        price = float(item_price)
    if price <= limit:
        return {"status": "within", "price": price, **common}
    return {"status": "over", "price": price, "over": round(price - limit, 2), **common}


def requested_item(query):
    """The item to search for. Budget and other requirements are not search words."""
    text = query or ""
    for pattern in (r"(?im)^Item:\s*(.+)$", r"(?im)^Looking for:\s*(.+)$"):
        found = re.search(pattern, text)
        if not found:
            continue
        item = found.group(1).strip()
        if item and not re.match(r"(?i)none\b|not stated", item):
            return item
    return text.strip()


def stated_condition(buyer_text):
    found = re.search(r"(?im)^Condition:\s*(.+)$", buyer_text or "")
    if not found:
        return ""
    value = found.group(1).strip()
    if not value or re.match(r"(?i)none stated|not stated|no preference", value):
        return ""
    return value


def condition_meets_request(buyer_text, listing_text):
    """Unknown does not satisfy a stated condition. No stated condition is not a failure."""
    required = stated_condition(buyer_text)
    if not required:
        return True
    return required.lower() in (listing_text or "").lower()


def qualifies(listing, buyer_text):
    """Every stated requirement. A rejected candidate is not a successful match."""
    if (listing.get("availability") or "unconfirmed") != "available":
        return False
    if not location_meets_request(buyer_text, listing.get("location") or ""):
        return False
    blob = "\n".join(
        str(listing.get(key) or "")
        for key in ("title", "text", "page_text")
    )
    if not condition_meets_request(buyer_text, blob):
        return False
    cap = buyer_price_cap(buyer_text)
    shipping = listing.get("shipping")
    # A refreshed page's missing amount stays missing. Old listing text is not a current cost.
    if shipping is None and not listing.get("current_costs"):
        shipping = extract_shipping(blob)
    price = price_against_budget(listing.get("price"), cap, shipping)
    return price["status"] in ("no_cap", "within")


def search_outcome(listings, incomplete=""):
    """Say what the sources checked showed. Do not claim the item exists nowhere."""
    count = sum(1 for item in listings or [] if item.get("qualifying"))
    if incomplete and count:
        return f"Found {count} qualifying match(es). Search incomplete — {incomplete}"
    if incomplete:
        return f"Search incomplete — {incomplete}"
    if count:
        return f"Found {count} qualifying match(es)"
    return "No qualifying match found in the sources checked"


def assessment_text(availability, confidence, product_reason, location, item_price=None, buyer_text="", shipping=None):
    """A product match cannot override a failed price limit."""
    price = price_against_budget(item_price, buyer_price_cap(buyer_text), shipping)
    product_ok = availability == "available" and confidence >= 60
    send = product_ok and price["status"] in ("no_cap", "within")
    decision = "send" if send else "no_send"
    if price["status"] == "over" and price["negotiable"]:
        introduction = "Over-budget lead"
    elif price["status"] == "over":
        introduction = "No send — over budget"
    elif price["status"] == "unknown":
        introduction = "on hold"
    elif send:
        introduction = "Send"
    else:
        introduction = "on hold"
    availability_value = "Found for sale" if availability == "available" else (
        "Listing ended" if availability == "ended" else "unconfirmed"
    )
    limit_name = "delivered-price limit" if price.get("basis") == "delivered" else "item-price limit"
    if price["status"] == "over":
        budget_value = (
            f"fails: {money(price['price'])} is {money(price['over'])} over "
            f"the {money(price['limit'])} {limit_name}"
        )
        if price.get("plus_shipping"):
            budget_value += ". Shipping is additional and does not raise the limit"
    elif price["status"] == "within":
        budget_value = f"within the {money(price['limit'])} {limit_name}"
    elif price["status"] == "unknown":
        budget_value = f"unknown, so the {money(price['limit'])} {limit_name} cannot be checked"
    else:
        budget_value = "no item-price limit was stated"
    place = location or "not stated on the listing"
    text = " | ".join([
        "Product: matches — MATCH",
        f"Availability: {availability_value}",
        f"Item-price budget: {budget_value}",
        f"Introduction: {introduction}",
        (
            f"Note: {product_reason} Seller location: {place}. "
            "The product result, the availability result, and the item-price limit are separate."
        ),
    ])
    return decision, text


# --- Market filing ---
_BLANK_MARKET = {"none", "n/a", "na", "null", "not specified", "not stated"}


def market_label(make, model, name=""):
    """Same confirmed-duplicate key the API uses. A literal None is not a model."""
    def clean(value):
        text = " ".join(str(value or "").split())
        return "" if text.casefold() in _BLANK_MARKET else text

    brand, variant = clean(make), clean(model)
    label = f"{brand} {variant}".strip() or " ".join(
        part for part in str(name or "").split() if part.casefold() not in _BLANK_MARKET
    )
    words = label.casefold().replace("-", "").split()
    if len(words) > 1 and words[-1] == "guitar":
        words = words[:-1]
    return brand, variant, " ".join(words)


def find_market_section(sb, make, model, part):
    brand, variant, key = market_label(make, model)
    name = f"{brand} {variant}".strip() or (part or "").strip() or "General"
    rows = sb.table("sections").select("id,name,make,model").execute().data or []
    for row in rows:
        existing = market_label(row.get("make"), row.get("model"), row.get("name"))[2]
        if key and existing == key:
            return row["id"], brand, variant
        if not key and str(row.get("name") or "").strip() == name:
            return row["id"], brand, variant
    resp = sb.table("sections").insert({
        "name": name, "make": brand, "model": variant,
        "match_count": 1, "last_match_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return resp.data[0]["id"], brand, variant


# --- Push to Supabase ---
def push_match(sb, buyer, seller, match_result, section_id=None):
    """Push a buyer-seller match to Supabase."""
    try:
        make = match_result.get("make", "")
        model = match_result.get("model", "")
        part = match_result.get("part", "")
        confidence = match_result.get("confidence", 50)
        reason = match_result.get("reason", "")
        availability = seller.get("availability") or "unconfirmed"
        if availability == "ended" and not seller.get("keep_ended"):
            print("    Ended listing excluded from active matches.")
            return None
        label = availability_label(availability)
        place = seller.get("location") or "not stated on the listing"
        cross = seller.get("cross_post") or ""
        decision, reason = assessment_text(
            availability, confidence, reason, place,
            seller.get("price"), buyer.get("text") or "", seller.get("shipping"),
        )
        if cross:
            reason = f"{reason} | Note: {cross}"

        buyer_username = buyer["title"][:80] if buyer.get("title") else "unknown"
        seller_username = seller["title"][:80] if seller.get("title") else "unknown"
        observed = datetime.now(timezone.utc).isoformat()
        discovery = seller.get("discovery_url") or ""
        extra = ""
        if discovery and discovery != seller.get("url"):
            extra = f"\nDiscovery source: {discovery}"
        if seller.get("link_label"):
            extra += f"\nLink: {seller['link_label']}"
        shown = f"{label}\n{seller['text']}{extra}"

        # Re-observing the same source updates that record. It does not create another.
        existing = sb.table("buyer_posts").select("id").eq("source_url", buyer["url"]).execute()
        buyer_row = {
            "username": buyer_username,
            "post_text": (buyer.get("text") or "")[:5000],
            "make": make, "model": model, "part": part,
            "source_url": buyer["url"],
            "source_platform": buyer.get("platform", "forum"),
            "scraped_at": observed,
        }
        if existing.data:
            buyer_id = existing.data[0]["id"]
            sb.table("buyer_posts").update(buyer_row).eq("id", buyer_id).execute()
        else:
            buyer_id = sb.table("buyer_posts").insert(buyer_row).execute().data[0]["id"]

        existing = sb.table("seller_listings").select("id").eq("source_url", seller["url"]).execute()
        seller_row = {
            "username": seller_username,
            "listing_text": shown[:5000],
            "make": make, "model": model, "part": part,
            "price": seller.get("price"),
            "source_url": seller["url"],
            "source_platform": seller.get("platform", "ebay"),
            "scraped_at": observed,
        }
        if existing.data:
            seller_id = existing.data[0]["id"]
            sb.table("seller_listings").update(seller_row).eq("id", seller_id).execute()
        else:
            seller_id = sb.table("seller_listings").insert(seller_row).execute().data[0]["id"]

        # File this result into the confirmed market. Old section rows stay in place.
        if not section_id:
            section_id, make, model = find_market_section(sb, make, model, part)

        # Check for duplicate match
        existing = sb.table("matches").select("id").eq("buyer_post_id", buyer_id).eq("seller_listing_id", seller_id).execute()
        if existing.data:
            return existing.data[0]["id"], False

        # Insert match. decision was set with the separated labels above.
        resp = sb.table("matches").insert({
            "buyer_post_id": buyer_id, "seller_listing_id": seller_id,
            "section_id": section_id,
            "ai_reasoning": reason, "confidence": confidence,
            "match_decision": decision, "status": "pending_review",
            "make": make, "model": model, "part": part,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return resp.data[0]["id"], True

    except Exception as e:
        print(f"    Supabase error: {e}")
        return None, False


# --- Auto mode: autonomous searching ---
def auto_search(sb, openai_client, seen, search_index, shuffled_plan):
    """Run one search from the plan. Returns next search_index."""
    if search_index >= len(shuffled_plan):
        # Reshuffle for next cycle
        random.shuffle(shuffled_plan)
        search_index = 0
        print("\n--- Starting new search cycle (reshuffled) ---")

    plan = shuffled_plan[search_index]
    category = plan["category"]
    buyer_query = plan["buyer_query"]
    seller_query = plan["seller_query"]

    print(f"\n--- Auto search [{search_index+1}/{len(shuffled_plan)}] [{category}]: {buyer_query}")

    # Step 1: Find WTB posts
    wtb_posts = search_wtb_posts(buyer_query, seen)
    if not wtb_posts:
        print(f"    No new WTB posts found. Moving on.")
        return search_index + 1

    print(f"    Found {len(wtb_posts)} WTB post(s)")

    matches_found = 0
    for found_post in wtb_posts:
        buyer, _offer = public_post_roles(found_post)
        if not buyer:
            print(f"    Not a buyer request: {(found_post.get('title') or '')[:70]}")
            continue
        print(f"    Buyer: {buyer['title'][:70]}")

        # Step 2: Search listings across marketplaces using the buyer's actual post
        listing_query = extract_ebay_query(buyer["title"], seller_query)
        print(f"    Listing query: {listing_query}")

        found = search_listings(listing_query, seen, max_keep=5, buyer_text=buyer["text"])
        listings = found["listings"]
        if not listings:
            print(f"    No listings found. Skipping.")
            mark_seen(seen, buyer["url"], buyer.get("title", ""))
            continue

        # Step 3: AI match against listings. A rejection does not end this buyer.
        qualified = False
        for seller in listings:
            print(f"    Seller: {seller['title'][:70]}")
            if not seller.get("qualifying"):
                print("    Not a qualifying offer. Continuing.")
                continue
            if usage_blocked(sb):
                print(f"    Daily OpenAI cap reached ({DAILY_AI_CAP}). Stopping this buyer.")
                break
            result = ai_evaluate_match(buyer["text"], seller["text"], openai_client, sb)
            if not result:
                continue

            is_match = result.get("match", False)
            confidence = result.get("confidence", 0)
            print(f"    AI says: match={is_match}, confidence={confidence}%")

            if is_match and confidence >= 60 and qualifies(seller, buyer["text"]):
                match_id, _created = push_match(sb, buyer, seller, result)
                if match_id:
                    matches_found += 1
                    qualified = True
                    print(f"    >>> MATCH PUSHED! {result.get('make','')} {result.get('model','')} ({confidence}%) ID:{match_id[:8]}")
                    mark_seen(seen, buyer["url"], buyer.get("title", ""))
                    mark_seen(seen, seller["url"], seller.get("title", ""))
                    break
            else:
                print(f"    Not a qualifying match. Trying next...")
        if not qualified:
            mark_seen(seen, buyer["url"], buyer.get("title", ""))

        time.sleep(SEARCH_DELAY)  # Pace between buyers

    print(f"    Search complete: {matches_found} match(es) found.")
    return search_index + 1


def _finish_run(sb, run_id, summary):
    sb.table("seeker_runs").update({
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result_summary": summary,
    }).eq("id", run_id).execute()


def _save_source(sb, table, url, row):
    existing = sb.table(table).select("id").eq("source_url", url).execute()
    if existing.data:
        sb.table(table).update(row).eq("id", existing.data[0]["id"]).execute()
        return existing.data[0]["id"]
    return sb.table(table).insert(row).execute().data[0]["id"]


def offer_identity(url):
    """Same platform and item id are one offer, whatever slug or page linked to it."""
    identity = source_identity(url)
    platform = extract_platform(url)
    if identity:
        return (platform, identity)
    canonical = canonical_item_url(url) or (url or "").split("?")[0]
    return (platform, canonical.rstrip("/").lower())


def unique_offers(rows):
    kept = []
    seen = set()
    for row in rows or []:
        url = row.get("url") or ""
        if not url or is_catalog_page(url):
            continue
        key = offer_identity(url)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def unique_by_url(rows):
    kept = []
    seen_urls = set()
    for row in rows or []:
        url = row.get("url") or ""
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        kept.append(row)
    return kept


def stored_buyer(row):
    return {
        "title": (row.get("username") or "unknown")[:80],
        "text": row.get("post_text") or "",
        "url": row.get("source_url") or "",
        "platform": row.get("source_platform") or "web",
    }


_BUY_INTENT = re.compile(
    r"(?i)\b(?:looking to buy|want to buy|wanted to buy|willing to buy|in search of|wtb)\b"
)
_LOOKING_FOR = re.compile(r"(?i)\blooking for\b")
_MEDIA_BUYER = re.compile(
    r"(?i)(?:youtube\.com/watch|youtube\.com/shorts|youtube\.com/embed|youtu\.be/)"
)
_NOT_A_REQUEST = re.compile(
    r"(?i)\b(?:tutorial|how to play|lyrics|official video|music video|guitar lesson|review)\b"
)
_ABSTRACT_WORDS = {
    "love", "lover", "heart", "baby", "girl", "boy", "friend", "someone",
    "happiness", "dream", "dreams", "night", "life", "time", "way", "thing",
    "things", "one", "answers", "trouble", "home", "world", "music", "song",
    "video", "tutorial", "review", "you", "me", "him", "her", "them", "new",
    "looking", "want", "wanted", "willing", "buy", "wtb", "search",
}
_PRODUCT_NOUNS = {
    "guitar", "guitars", "bass", "amp", "amplifier", "pedal", "keyboard", "piano",
    "drum", "drums", "violin", "cello", "ukulele", "mandolin", "synth", "synthesizer",
    "camera", "lens", "speaker", "speakers", "turntable", "headphone", "headphones",
    "receiver", "subwoofer", "watch", "card", "cards", "coin", "coins", "comic",
    "comics", "record", "records", "vinyl", "memorabilia", "pickup", "pickups",
}
_SELL_INTENT = re.compile(r"(?i)\b(?:selling|for sale|wts|up for sale)\b|\bfs\s*:")
_LOOKING_FOR_BUYER = re.compile(r"(?i)\blooking for (?:a )?buyers?\b")
_RELATED_LISTINGS = re.compile(
    r"(?is)\b(?:related listings?|similar listings?|similar items|you may also like|people also viewed|recommended(?: items)?)\b.*"
)
_QUOTED_TEXT = re.compile(r"[\"“][^\"”]{0,500}[\"”]")
_NAV_WORDS = {
    "home", "marketplace", "buy", "sell", "wtb", "wts", "categories", "category",
    "filter", "filters", "sort", "by", "saved", "inbox", "notifications", "search",
}


def _navigation_line(line):
    words = re.findall(r"[A-Za-z']+", line or "")
    if not words or len(words) > 10:
        return False
    hits = sum(1 for word in words if word.casefold() in _NAV_WORDS)
    return hits >= max(2, (len(words) + 1) // 2)


def author_statement(text):
    """Author wording. Quotes, navigation, and related listings are not the author."""
    raw = _RELATED_LISTINGS.sub(" ", text or "")
    raw = _QUOTED_TEXT.sub(" ", raw)
    kept = [line.strip() for line in re.split(r"[\r\n]+", raw) if line.strip() and not _navigation_line(line)]
    return "\n".join(kept)


def _intent_clauses(text):
    parts = re.split(r"[\r\n]+|(?<=[.!?])\s+|\s*;\s*", text or "")
    return [part.strip() for part in parts if part.strip(" .")]


_NEGATION_BEFORE = re.compile(
    r"(?i)(?:\bnot\b|\bno\b|\bnever\b|\bwithout\b|\bno longer\b|\bnot currently\b|n't\b)"
    r"(?:\s+\w+){0,4}\s*$"
)
_APOSTROPHE_FOLDS = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201b": "'",
    "\u02bc": "'",
    "\u2032": "'",
})


def _straight_apostrophes(text):
    """Curly and straight apostrophes are the same word for negation."""
    return (text or "").translate(_APOSTROPHE_FOLDS)


def _negated_at(clause, start):
    prefix = _straight_apostrophes((clause or "")[max(0, start - 48):start])
    return _NEGATION_BEFORE.search(prefix) is not None


def _content_words(text):
    skips = {"a", "an", "the", "for", "to", "of", "and", "or", "my", "your", "in", "on"}
    return [
        word for word in re.findall(r"[A-Za-z0-9]+", text or "")
        if word.casefold() not in skips
    ]


def _identifiable_item(text):
    """A product the author can acquire. A song, feeling, or bare search word is not one."""
    words = _content_words(text)
    if not words:
        return False
    if any(word.casefold() in _PRODUCT_NOUNS for word in words):
        return True
    specific = [
        word for word in words
        if word.casefold() not in _ABSTRACT_WORDS and (len(word) >= 4 or any(char.isdigit() for char in word))
    ]
    return bool(specific)


def _looking_for_item(clause):
    """'Looking for' needs a product. A title or a search for a buyer does not qualify."""
    if _LOOKING_FOR_BUYER.search(clause or "") or _NOT_A_REQUEST.search(clause or ""):
        return False
    match = _LOOKING_FOR.search(clause or "")
    if not match or _negated_at(clause, match.start()):
        return False
    return _identifiable_item(clause[match.end():])


def _positive_buy(clause):
    if _NOT_A_REQUEST.search(clause or "") and not _BUY_INTENT.search(clause or ""):
        return False
    for match in _BUY_INTENT.finditer(clause or ""):
        if _negated_at(clause, match.start()):
            continue
        if _identifiable_item(clause):
            return True
    return _looking_for_item(clause)


def _positive_sell(clause):
    buyer_hunt = _LOOKING_FOR_BUYER.search(clause or "")
    if buyer_hunt and not _negated_at(clause, buyer_hunt.start()):
        return True
    for match in _SELL_INTENT.finditer(clause or ""):
        if _negated_at(clause, match.start()):
            continue
        return True
    return False


def _clause_intent(clause):
    buying = _positive_buy(clause)
    selling = _positive_sell(clause)
    if buying and selling:
        return "mixed"
    if buying:
        return "buy"
    if selling:
        return "sell"
    return ""


def _split_mixed_clause(clause):
    marks = []
    for match in _BUY_INTENT.finditer(clause or ""):
        if _negated_at(clause, match.start()) or not _identifiable_item(clause):
            continue
        marks.append((match.start(), "buy"))
    looking = _LOOKING_FOR.search(clause or "")
    if looking and _looking_for_item(clause):
        marks.append((looking.start(), "buy"))
    buyer_hunt = _LOOKING_FOR_BUYER.search(clause or "")
    if buyer_hunt and not _negated_at(clause, buyer_hunt.start()):
        marks.append((buyer_hunt.start(), "sell"))
    for match in _SELL_INTENT.finditer(clause or ""):
        if not _negated_at(clause, match.start()):
            marks.append((match.start(), "sell"))
    marks.sort()
    ordered = []
    if not marks:
        return ordered
    leading = (clause or "")[:marks[0][0]].strip(" ,;.")
    for index, (start, kind) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(clause or "")
        piece = (clause or "")[start:end].strip(" ,;.")
        if index == 0 and leading:
            piece = f"{leading} {piece}".strip()
        if piece:
            ordered.append((kind, piece))
    return ordered


def _join_clauses(parts):
    return "\n".join(part.strip() for part in parts if part and part.strip()).strip()


def author_intent(text, url=""):
    """What the author is doing. Requirements stay with that item. The source stays intact."""
    buys, sells = [], []
    pending = []
    side = ""
    for clause in _intent_clauses(author_statement(text)):
        kind = _clause_intent(clause)
        if kind == "mixed":
            ordered = _split_mixed_clause(clause)
            if not ordered:
                continue
            if pending:
                (buys if ordered[0][0] == "buy" else sells).extend(pending)
                pending = []
            for part_kind, piece in ordered:
                if part_kind == "buy":
                    buys.append(piece)
                else:
                    sells.append(piece)
            side = ordered[-1][0]
            continue
        if kind == "buy":
            if not side:
                buys.extend(pending)
                pending = []
            side = "buy"
            buys.append(clause)
        elif kind == "sell":
            if not side:
                sells.extend(pending)
                pending = []
            side = "sell"
            sells.append(clause)
        elif side == "buy":
            buys.append(clause)
        elif side == "sell":
            sells.append(clause)
        else:
            pending.append(clause)
    wanted = _join_clauses(buys)
    offered = _join_clauses(sells)
    if _MEDIA_BUYER.search(url or "") or (wanted and not _identifiable_item(wanted)):
        wanted = ""
    if wanted and offered:
        role = "mixed"
    elif wanted:
        role = "buy"
    elif offered:
        role = "sell"
    else:
        role = "unresolved"
    return {"role": role, "wanted": wanted, "offered": offered, "source": text or ""}


def as_matching_buyer(buyer):
    """A stored or discovered post may enter matching only as the item its author wants."""
    if str((buyer or {}).get("text") or "").startswith("Market discovery\n"):
        return None
    platform = str((buyer or {}).get("platform") or "").casefold()
    url = str((buyer or {}).get("url") or "").casefold()
    if platform == "sfb" or "searchfindbuy.local" in url:
        return buyer
    intent = author_intent((buyer or {}).get("text") or "", (buyer or {}).get("url") or "")
    if intent["role"] not in ("buy", "mixed") or not intent["wanted"]:
        return None
    kept = dict(buyer)
    kept["source_text"] = (buyer or {}).get("source_text") or (buyer or {}).get("text") or ""
    kept["text"] = intent["wanted"]
    return kept


def relevant_to_market(text, market_name):
    """The wanted item has to be the market's item. Another request stays unrelated."""
    tokens = [
        token for token in re.findall(r"[a-z0-9]+", (market_name or "").lower())
        if len(token) >= 3 and token not in {"the", "for", "and", "guitar", "guitars"}
    ]
    if not tokens:
        return True
    blob = (text or "").lower()
    return all(re.search(rf"\b{re.escape(token)}\b", blob) for token in tokens)


def public_post_roles(post, market_name=""):
    """Split a public page into a buyer request, a seller offer, neither, or both."""
    original = (post or {}).get("text") or ""
    intent = author_intent(original, (post or {}).get("url") or "")
    buyer = seller = None
    if (
        intent["role"] in ("buy", "mixed")
        and intent["wanted"]
        and relevant_to_market(intent["wanted"], market_name)
    ):
        buyer = dict(post)
        buyer["source_text"] = original
        buyer["text"] = intent["wanted"]
    if intent["role"] in ("sell", "mixed") and intent["offered"]:
        seller = {
            "title": intent["offered"][:80],
            "text": intent["offered"],
            "source_text": original,
            "url": (post or {}).get("url") or "",
            "platform": (post or {}).get("platform") or "web",
            "availability": "unconfirmed",
            "qualifying": False,
            "price": None,
            "shipping": None,
        }
    return buyer, seller


def stored_seller(row):
    """A saved available flag is not current until the item page is read again."""
    return {
        "title": (row.get("username") or "unknown")[:80],
        "text": row.get("listing_text") or "",
        "url": row.get("source_url") or "",
        "platform": row.get("source_platform") or "web",
        "price": row.get("price"),
        "shipping": row.get("shipping"),
        "availability": "unconfirmed",
        "reported_availability": row.get("availability") or "",
        "qualifying": False,
        "stored": True,
        "id": row.get("id"),
    }


def offer_buyer_query(seller):
    """The offer's own item. A market-scope note is not a buyer request."""
    text = seller.get("text") or ""
    title = seller.get("title") or ""
    if text.startswith("Market discovery\n") or title.startswith("Market discovery"):
        return ""
    item = requested_item(title).strip() or requested_item(text).strip()
    if not item or item.lower().startswith("market discovery"):
        return ""
    return item


def buyer_queries_for_offers(sellers, limit):
    """One buyer search per current offer, inside the existing match limit."""
    queries = []
    overflow = False
    for seller in sellers or []:
        if seller.get("cross_post") or is_catalog_page(seller.get("url") or ""):
            continue
        if (seller.get("availability") or "unconfirmed") == "ended":
            continue
        query = offer_buyer_query(seller)
        if not query or query in queries:
            continue
        if len(queries) >= limit:
            overflow = True
            break
        queries.append(query)
    return queries, overflow


def market_comparisons(buyers, sellers, market_name=""):
    """Pair stored and newly found requests with stored and newly found offers."""
    pairs = []
    offer_rows = [
        seller for seller in unique_offers(sellers)
        if not seller.get("cross_post")
    ]
    for buyer in unique_by_url(buyers):
        buyer = as_matching_buyer(buyer)
        if not buyer:
            continue
        platform = str(buyer.get("platform") or "").casefold()
        buyer_url = str(buyer.get("url") or "").casefold()
        if (
            market_name
            and platform != "sfb"
            and "searchfindbuy.local" not in buyer_url
            and not relevant_to_market(buyer.get("text") or "", market_name)
        ):
            continue
        for seller in offer_rows:
            if buyer.get("url") and buyer.get("url") == seller.get("url"):
                continue
            pairs.append((buyer, seller))
    return pairs


def market_alias_ids(sections, market_id):
    """Section ids for one confirmed market. A blank name does not attach other rows."""
    market_id = str(market_id or "")
    chosen = next((row for row in sections or [] if str(row.get("id") or "") == market_id), None)
    if not chosen:
        return {market_id} if market_id else set()
    key = market_label(chosen.get("make"), chosen.get("model"), chosen.get("name"))[2]
    if not key:
        return {market_id}
    return {
        str(row.get("id"))
        for row in sections or []
        if row.get("id") and market_label(row.get("make"), row.get("model"), row.get("name"))[2] == key
    }


def section_market_key(sections, section_id):
    section_id = str(section_id or "")
    if not section_id:
        return ""
    for row in sections or []:
        if str(row.get("id") or "") != section_id:
            continue
        key = market_label(row.get("make"), row.get("model"), row.get("name"))[2]
        return key or f"id:{section_id}"
    return f"id:{section_id}"


def linked_record_ids(sections, matches, buyers, sellers, market_id):
    """Records for this market. Mixed market links are left unassigned."""
    target = section_market_key(sections, market_id)
    by_buyer = {}
    by_seller = {}
    for match in matches or []:
        market = section_market_key(sections, match.get("section_id"))
        if not market:
            continue
        if match.get("buyer_post_id"):
            by_buyer.setdefault(match["buyer_post_id"], set()).add(market)
        if match.get("seller_listing_id"):
            by_seller.setdefault(match["seller_listing_id"], set()).add(market)

    def decide(row, linked):
        markets = set(linked or ())
        explicit = str(row.get("section_id") or "")
        if explicit:
            markets.add(section_market_key(sections, explicit))
        if not markets or markets != {target}:
            return False, False
        return True, not explicit

    buyer_ids = []
    seller_ids = []
    backfill = []
    for row in buyers or []:
        include, fill = decide(row, by_buyer.get(row.get("id"), set()))
        if include:
            buyer_ids.append(row.get("id"))
        if fill:
            backfill.append(("buyer_posts", row.get("id")))
    for row in sellers or []:
        include, fill = decide(row, by_seller.get(row.get("id"), set()))
        if include:
            seller_ids.append(row.get("id"))
        if fill:
            backfill.append(("seller_listings", row.get("id")))
    return buyer_ids, seller_ids, backfill


def load_market_records(sb, market_id):
    sections = sb.table("sections").select("id,name,make,model").execute().data or []
    matches = sb.table("matches").select("buyer_post_id,seller_listing_id,section_id").execute().data or []
    buyers = sb.table("buyer_posts").select(
        "id,username,post_text,source_url,source_platform,section_id"
    ).execute().data or []
    sellers = sb.table("seller_listings").select(
        "id,username,listing_text,source_url,source_platform,price,shipping,availability,section_id"
    ).execute().data or []
    buyer_ids, seller_ids, backfill = linked_record_ids(sections, matches, buyers, sellers, market_id)
    for table, row_id in backfill:
        if not row_id:
            continue
        sb.table(table).update({"section_id": market_id}).eq("id", row_id).execute()
    buyer_map = {row.get("id"): row for row in buyers}
    seller_map = {row.get("id"): row for row in sellers}
    return (
        [stored_buyer(buyer_map[row_id]) for row_id in buyer_ids if row_id in buyer_map],
        [stored_seller(seller_map[row_id]) for row_id in seller_ids if row_id in seller_map],
    )


def refresh_stored_offer(seller, fetch_html):
    """Read the item page. Old amounts are history and are not the current cost."""
    checked = dict(seller)
    checked["stored"] = True
    checked["checked_now"] = True
    checked["previous_price"] = seller.get("price")
    checked["previous_shipping"] = seller.get("shipping")
    checked["historical_text"] = seller.get("text") or ""
    checked["price"] = None
    checked["shipping"] = None
    checked["current_costs"] = True
    checked["text"] = ""
    checked["page_text"] = ""
    url = seller.get("url") or ""
    html = fetch_html(url) if url else ""
    info = inspect_listing_html(html, seller.get("title") or "", url)
    if not info.get("readable"):
        checked["availability"] = "unconfirmed"
        checked["qualifying"] = False
        return checked
    checked["availability"] = info.get("status") or "unconfirmed"
    checked["price"] = info.get("price")
    checked["shipping"] = info.get("shipping")
    checked["text"] = info.get("text") or ""
    checked["qualifying"] = checked["availability"] == "available"
    return checked


def verify_stored_offer(seller, fetch_html):
    if not seller.get("stored") or seller.get("checked_now"):
        return seller
    refreshed = refresh_stored_offer(dict(seller), fetch_html)
    seller.clear()
    seller.update(refreshed)
    return seller


def record_offer_observation(sb, seller):
    if not seller.get("url"):
        return
    sb.table("seller_listings").update({
        "availability": seller.get("availability") or "unconfirmed",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "price": seller.get("price"),
        "shipping": seller.get("shipping"),
    }).eq("source_url", seller["url"]).execute()


def _remember_offer(sb, market_id, offer):
    if not offer.get("url"):
        return
    observed = datetime.now(timezone.utc).isoformat()
    _save_source(sb, "seller_listings", offer["url"], {
        "username": (offer.get("title") or "unknown")[:80],
        "listing_text": f"Availability unconfirmed\n{offer.get('text') or ''}"[:5000],
        "price": None,
        "shipping": None,
        "currency": None,
        "availability": "unconfirmed",
        "source_url": offer["url"],
        "source_platform": offer.get("platform") or "web",
        "source_id": source_identity(offer["url"]),
        "section_id": market_id,
        "scraped_at": observed,
        "observed_at": observed,
    })


def _take_public_post(sb, market_id, post, buyers, sellers, seen, market_name=""):
    """Save a public page by what its author is doing. A search hit is not a buyer."""
    buyer, offer = public_post_roles(post, market_name)
    if buyer:
        _remember_buyer(sb, market_id, buyer)
        buyers.append(buyer)
    if offer:
        _remember_offer(sb, market_id, offer)
        sellers.append(offer)
    if buyer or offer:
        mark_seen(seen, post.get("url") or "", post.get("title", ""))


def _remember_buyer(sb, market_id, post):
    observed = datetime.now(timezone.utc).isoformat()
    _save_source(sb, "buyer_posts", post["url"], {
        "username": (post.get("title") or "unknown")[:80],
        "post_text": (post.get("source_text") or post.get("text") or "")[:5000],
        "source_url": post["url"],
        "source_platform": post.get("platform") or "web",
        "source_id": source_identity(post["url"]),
        "section_id": market_id,
        "scraped_at": observed,
        "observed_at": observed,
    })


def run_market_discovery(sb, openai_client, seen, run):
    """Find public buyers and sellers for one market. The market name is not a buyer."""
    run_id = run["id"]
    text = run.get("search_query") or ""
    found_name = re.search(r"(?m)^Market:\s*(.+)$", text)
    found_id = re.search(r"(?m)^Market id:\s*(\S+)$", text)
    market_name = found_name.group(1).strip() if found_name else ""
    market_id = run.get("market_id") or (found_id.group(1) if found_id else "")
    if not market_name or not market_id:
        _finish_run(sb, run_id, "Market discovery not connected yet")
        return
    try:
        sb.table("seller_listings").select("section_id,availability").limit(1).execute()
        sb.table("seeker_ai_usage").select("usage_date").limit(1).execute()
    except Exception:
        _finish_run(sb, run_id, "Market discovery not connected yet")
        return
    print(f"    Market discovery scope: {market_name}")
    try:
        existing_buyers, existing_sellers = load_market_records(sb, market_id)
    except Exception as exc:
        if rpc_failure(exc) == "missing":
            _finish_run(sb, run_id, "Market discovery not connected yet")
        else:
            _finish_run(sb, run_id, search_outcome([], f"existing market records could not be read — {exc}"))
        return
    buyers = []
    discovered_offers = []
    for post in search_wtb_posts(f"\"{market_name}\" (WTB OR \"want to buy\" OR \"looking for\")", seen):
        if is_market_discovery_run({"search_query": post.get("text") or ""}):
            continue
        _take_public_post(sb, market_id, post, buyers, discovered_offers, seen, market_name)
    found = search_listings(market_name, seen, max_keep=8, buyer_text=market_name)
    sellers = found["listings"] + discovered_offers
    observed = datetime.now(timezone.utc).isoformat()
    for seller in sellers:
        label = availability_label(seller.get("availability"))
        _save_source(sb, "seller_listings", seller["url"], {
            "username": (seller.get("title") or "unknown")[:80],
            "listing_text": f"{label}\n{seller.get('text') or ''}"[:5000],
            "price": seller.get("price"),
            "shipping": seller.get("shipping"),
            "currency": "USD" if seller.get("price") is not None else None,
            "availability": seller.get("availability") or "unconfirmed",
            "source_url": seller["url"],
            "source_platform": seller.get("platform") or "web",
            "source_id": source_identity(seller["url"]),
            "discovery_url": seller.get("discovery_url") or None,
            "section_id": market_id,
            "scraped_at": observed,
            "observed_at": observed,
        })
    incomplete = found.get("incomplete") or ""
    offer_queries, overflow = buyer_queries_for_offers(sellers, MATCH_TARGET)
    if overflow and not incomplete:
        incomplete = "the buyer-search limit was reached before every offer was checked"
    for query in offer_queries:
        for post in search_wtb_posts(f"\"{query}\" (WTB OR \"want to buy\" OR \"looking for\")", seen):
            if is_market_discovery_run({"search_query": post.get("text") or ""}):
                continue
            _take_public_post(sb, market_id, post, buyers, sellers, seen, market_name)
    compared = market_comparisons(
        buyers + existing_buyers, sellers + existing_sellers, market_name,
    )
    verified = 0
    try:
        if compared and usage_blocked(sb):
            incomplete = reserve_closed_reason("cap")
        else:
            for buyer, seller in compared:
                if verified >= MATCH_TARGET:
                    break
                seller = verify_stored_offer(seller, read_listing_html)
                if seller.get("stored") and not seller.get("observation_saved"):
                    record_offer_observation(sb, seller)
                    seller["observation_saved"] = True
                if not qualifies(seller, buyer.get("text") or ""):
                    print(f"    Rejected candidate, still searching: {(seller.get('title') or '')[:70]}")
                    continue
                result = ai_evaluate_match(
                    buyer.get("text") or "", seller.get("text") or "", openai_client, sb, False,
                )
                if not (result and result.get("match") and result.get("confidence", 0) >= 60):
                    continue
                if not qualifies(seller, buyer.get("text") or ""):
                    continue
                _match_id, created = push_match(sb, buyer, seller, result, section_id=market_id)
                if created:
                    verified += 1
                    mark_seen(seen, seller["url"], seller.get("title", ""))
    except ReservationClosed as exc:
        incomplete = exc.reason
    summary = search_outcome([{"qualifying": True}] * verified, incomplete)
    _finish_run(sb, run_id, summary)
    print(f"    Market discovery complete: {summary}")


# --- Manual mode: handle website requests ---
def check_manual_requests(sb, openai_client, seen):
    """Check Supabase for manual run requests from the website."""
    try:
        claimed = claim_next_run(sb)
    except ClaimClosed as exc:
        print(f"    Claim failed closed: {exc.reason}")
        return False
    run_id = None
    try:
        if claimed == "fallback":
            result = sb.table("seeker_runs").select("*").eq("status", "pending").order(
                "requested_at", desc=False
            ).limit(1).execute()
            if not result.data:
                return False
            run = result.data[0]
            sb.table("seeker_runs").update({
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run["id"]).execute()
        elif not claimed:
            return False
        else:
            run = claimed

        run_id = run["id"]
        query = (run.get("search_query") or "").strip()

        print(f"\n>>> MANUAL REQUEST: {run_id[:8]}...")
        if is_market_discovery_run(run):
            run_market_discovery(sb, openai_client, seen, run)
            return True

        matches_found = 0
        verified_found = 0

        if usage_blocked(sb):
            print(f"    Daily OpenAI cap reached ({DAILY_AI_CAP}). Not searching.")
            sb.table("seeker_runs").update({
                "status": "completed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "result_summary": f"Search incomplete — daily OpenAI cap of {DAILY_AI_CAP} reached",
            }).eq("id", run_id).execute()
            return True

        if query:
            print(f"    Search: {query}")

            print(f"    Searching listings for: {requested_item(query)}")
            buyer = {
                "title": requested_item(query)[:80],
                "text": query if query.lower().startswith("item:") or "\nItem:" in query or query.lower().startswith("find sellers") else f"Looking for: {query}",
                "url": f"https://searchfindbuy.local/requests/{run_id}",
                "platform": "sfb",
            }
            found = search_listings(query, seen, max_keep=6, buyer_text=buyer["text"])
            listings = found["listings"]
            incomplete = found["incomplete"]
            if not listings:
                print(f"    No listings found for '{requested_item(query)}'.")
            else:
                print(f"    Scoring {len(listings)} listing(s) against the request.")
                for seller in listings:
                    if verified_found >= MATCH_TARGET:
                        break
                    try:
                        if usage_blocked(sb):
                            incomplete = reserve_closed_reason("cap")
                            print(f"    {incomplete}")
                            break
                    except ReservationClosed as exc:
                        incomplete = exc.reason
                        print(f"    {exc.reason}")
                        break
                    if not seller.get("qualifying"):
                        print(
                            f"    Rejected candidate, still searching: {seller['title'][:70]} "
                            f"[{availability_label(seller.get('availability'))} price={seller.get('price')}]"
                        )
                        continue
                    try:
                        result_ai = ai_evaluate_match(buyer["text"], seller["text"], openai_client, sb)
                    except ReservationClosed as exc:
                        incomplete = exc.reason
                        print(f"    {exc.reason}")
                        break
                    if usage_blocked(sb) and not result_ai:
                        incomplete = f"daily OpenAI cap of {DAILY_AI_CAP} reached"
                        break
                    if not (result_ai and result_ai.get("match") and result_ai.get("confidence", 0) >= 60):
                        print(f"    Not a qualifying product: {seller['title'][:70]}")
                        continue
                    if not qualifies(seller, buyer["text"]):
                        print(f"    Requirement failed: {availability_label(seller.get('availability'))}")
                        continue
                    match_id, _created = push_match(sb, buyer, seller, result_ai)
                    if match_id:
                        verified_found += 1
                        matches_found += 1
                        print(f"    >>> QUALIFYING MATCH: {result_ai.get('make','')} {result_ai.get('model','')} ({result_ai.get('confidence')}%) [{seller.get('platform')}]")
                        mark_seen(seen, seller["url"])
            summary = search_outcome(
                [{"qualifying": True}] * verified_found,
                incomplete,
            )
        else:
            print(f"    No search query — running next auto search instead.")
            shuffled = list(SEARCH_PLAN)
            random.shuffle(shuffled)
            auto_search(sb, openai_client, seen, 0, shuffled)
            matches_found = 1
            summary = f"Found {matches_found} match(es)" if matches_found > 0 else "Search complete — no new matches this run"
        sb.table("seeker_runs").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result_summary": summary,
        }).eq("id", run_id).execute()

        print(f"    Manual request complete: {summary}")
        return True

    except ReservationClosed as exc:
        if run_id:
            summary = search_outcome([], exc.reason)
            _finish_run(sb, run_id, summary)
            print(f"    {summary}")
            return True
        print(f"    {exc.reason}")
        return False
    except Exception as e:
        print(f"    Manual request error: {e}")
        return False


# --- Main ---
def main():
    print("=" * 60)
    print("  SFB Seeker v2.1")
    print("  Autonomous buyer-seller matching")
    print("=" * 60)
    print()

    # Connect
    missing = [name for name, value in (("OPENAI_KEY", OPENAI_KEY), ("SUPABASE_KEY", SUPABASE_KEY)) if not value]
    if missing:
        print("Missing environment variable(s): " + ", ".join(missing))
        print("Set them in Windows, then open a new window and run this file again.")
        print('  setx OPENAI_KEY "your-openai-key"')
        print('  setx SUPABASE_KEY "your-supabase-service-key"')
        sys.exit(1)

    print("Connecting to Supabase...")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Connected.")

    print("Connecting to OpenAI...")
    openai_client = OpenAI(api_key=OPENAI_KEY)
    print("Connected.")

    # Load seen items
    seen = load_seen()
    print(f"Loaded {len(seen)} previously seen items.")
    print()

    # Shuffle the search plan so each run starts differently
    shuffled_plan = list(SEARCH_PLAN)
    random.shuffle(shuffled_plan)

    auto_label = "on" if AUTO_SEARCH else "off — dashboard requests only"
    print(f"Seeker v2.1 is running. ({len(shuffled_plan)} searches queued)")
    print(f"  - Auto mode: {auto_label}")
    print("  - Manual mode: listening for website Run Seeker requests")
    print(f"  - OpenAI today: {ai_calls_today()}/{DAILY_AI_CAP}")
    print(f"  - Cooldown: items re-checked after {COOLDOWN_HOURS} hours")
    print(f"  - Search delay: {SEARCH_DELAY}s between searches")
    print("-" * 60)

    search_index = 0
    auto_counter = 0
    cap_announced = False

    while True:
        try:
            write_heartbeat(sb)
            # Always check for manual requests first
            had_manual = check_manual_requests(sb, openai_client, seen)

            # Every 4th poll cycle, do an auto search when that switch is on
            auto_counter += 1
            if AUTO_SEARCH and not had_manual and auto_counter >= 4:
                if usage_blocked(sb):
                    if not cap_announced:
                        print(f"    Daily OpenAI cap reached ({DAILY_AI_CAP}). Auto search paused.")
                        cap_announced = True
                else:
                    search_index = auto_search(sb, openai_client, seen, search_index, shuffled_plan)
                auto_counter = 0

        except KeyboardInterrupt:
            print("\n\nStopping Seeker v2.1. Goodbye!")
            break
        except Exception as e:
            print(f"  Error: {e}")

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\n\nStopping Seeker v2.1. Goodbye!")
            break


def check_rules():
    excerpt = "Sep 18, 2026 · This is a vintage Greco SE600"
    assert classify_availability(excerpt) == "unconfirmed"
    assert classify_availability("") == "unconfirmed"
    assert availability_label("unconfirmed") == "Availability unconfirmed"
    assert classify_availability("This listing has ended. Greco SE600") == "ended"
    assert classify_availability("Sold · 1973 Greco Stratocaster") == "ended"
    assert classify_availability("Sold\u00a0 · 1973 Greco Stratocaster") == "ended"
    assert classify_availability("Sold &nbsp; · 1973 Greco Stratocaster") == "ended"
    assert classify_availability("This listing is no longer available") == "ended"
    assert classify_availability("Sold as a set of original pickups") == "unconfirmed"
    assert availability_label("ended") == "Listing ended"
    assert active_offer({"availability": "ended"}, "Looking for a guitar", 90) is False
    assert classify_availability("Buy it now. Add to cart.") == "available"
    assert stated_location("https://www.ebay.co.uk/itm/188911067315") == ""
    assert stated_location("Located in: Oswego, Illinois, United States Buy it now") == "Oswego, Illinois, United States"
    error_page = "<html><title>Error Page | eBay</title><body>SORRY Something went wrong on our end</body></html>"
    assert page_unreadable(error_page) is True
    held, held_text = assessment_text("unconfirmed", 80, "The listing names the requested model.", "")
    assert held == "no_send"
    assert held_text.startswith("Product: matches — MATCH | Availability: unconfirmed | ")
    assert "Introduction: on hold" in held_text
    ready, ready_text = assessment_text("available", 80, "The listing names the requested model.", "Oswego, Illinois, United States")
    assert ready == "send"
    assert "Introduction: Send" in ready_text
    assert "NO MATCH" not in ready_text
    cap = buyer_price_cap("Budget: 875 plus shipping — firm maximum")
    assert cap["limit"] == 875
    assert cap["plus_shipping"] is True
    assert cap["basis"] == "item"
    assert cap["negotiable"] is False
    delivered_cap = buyer_price_cap("Budget: 875 including shipping — firm maximum")
    assert delivered_cap["basis"] == "delivered"
    assert price_against_budget(800, delivered_cap, None)["status"] == "unknown"
    assert price_against_budget(800, delivered_cap, 50)["status"] == "within"
    assert price_against_budget(800, delivered_cap, 100)["status"] == "over"
    assert buyer_price_cap("I would pay $850 plus shipping")["limit"] == 850
    assert buyer_price_cap("Looking for: Epiphone USA Map guitar") is None
    assert buyer_price_cap("Budget: Not stated") is None
    over = price_against_budget(1129.99, cap)
    assert over["status"] == "over"
    assert over["over"] == 254.99
    assert price_against_budget(None, cap)["status"] == "unknown"
    assert price_against_budget(875, cap)["status"] == "within"
    assert price_against_budget(875.01, cap)["status"] == "over"
    blocked, blocked_text = assessment_text(
        "available", 100, "The listing names the requested model.",
        "Oswego, Illinois, United States", 1129.99,
        "Budget: 875 plus shipping — firm maximum",
    )
    assert blocked == "no_send"
    assert "Product: matches — MATCH" in blocked_text
    assert "Introduction: No send — over budget" in blocked_text
    assert "does not raise the limit" in blocked_text
    lead, lead_text = assessment_text(
        "available", 100, "The listing names the requested model.", "",
        1129.99, "Budget: $850 plus shipping — flexible target",
    )
    assert lead == "no_send"
    assert "Introduction: Over-budget lead" in lead_text
    page = "Seller Budda Bowl Dover, MA, United States Buy it now"
    assert stated_location(page) == "Dover, MA, United States"
    buyer = "Requirements: None stated Preferences: None stated"
    assert location_meets_request(buyer, "") is True
    assert location_meets_request("Requirements: United States", "") is False
    assert location_meets_request("Requirements: United States", "Dover, MA, United States") is True
    reverb = {
        "url": "https://reverb.com/item/102324948-patina-queen-greco-se600-1974-natural",
        "title": "Patina Queen Greco Se600 1974 - Natural",
        "text": "1974 Greco SE-600 natural",
        "location": "Dover, MA, United States",
    }
    facebook = {
        "url": "https://www.facebook.com/marketplace/item/1764002841581704/",
        "title": "1974 Greco se600 Stratocaster Vintage Patina natural",
        "text": "1974 Greco SE600 natural wood finish Dover, MA",
        "location": "Dover, MA",
    }
    flagged = flag_cross_posts([reverb, facebook])
    assert flagged[0]["cross_post"].startswith("Possible cross-post")
    assert flagged[1]["cross_post"] == flagged[0]["cross_post"]
    profile = "https://www.facebook.com/marketplace/profile/100075249962505/"
    item = "https://www.facebook.com/marketplace/item/1764002841581704/"
    assert listing_allowed(profile, "Marketplace - Facebook", "Available & in stock Sort by") is True
    assert listing_allowed(item, "1974 Greco se600", "$725 Dover, MA") is True
    assert listing_allowed("https://www.facebook.com/marketplace/search/?query=greco", "", "") is False
    assert classify_availability("Available & in stock Sort by $725") == "unconfirmed"
    request = "Greco Se600 1974 natural"
    excerpt = "Available & in stock Sort by $725 1974 Greco se600 Stratocaster Vintage Patina Dover, MA"
    profile_html = """
        <a href="/marketplace/item/111/">1973 Fender Stratocaster $400 Boston</a>
        <a href="/marketplace/item/222/">1974 Greco se600 sunburst $900 Boston</a>
        <a href="/marketplace/item/333/">1974 Greco se600 Stratocaster Vintage Patina $725 Dover</a>
    """
    ended_page = "Sold · 1974 Greco se600 Stratocaster Vintage Patina $725 Dover, MA"
    quiet_page = "1974 Greco se600 Stratocaster Vintage Patina $725 Dover, MA Message seller"
    pages = {"https://www.facebook.com/marketplace/item/333/": quiet_page}

    def read_profile(_url):
        return profile_html

    def read_item(url):
        return pages.get(url, "")

    found = resolve_facebook_profile(profile, request, excerpt, read_profile, read_item)
    assert found["resolved"] is True
    assert found["url"].endswith("/333/")
    assert found["discovery_url"] == profile
    assert found["availability"] == "unconfirmed"
    assert found["link_label"] == "View item"
    pages["https://www.facebook.com/marketplace/item/333/"] = ended_page
    ended = resolve_facebook_profile(profile, request, excerpt, read_profile, read_item)
    assert ended["availability"] == "ended"
    assert ended["keep_ended"] is True
    assert ended["url"].endswith("/333/")
    tied_html = """
        <a href="/marketplace/item/111/">1974 Greco se600 Stratocaster Vintage Patina $725 Dover</a>
        <a href="/marketplace/item/222/">1974 Greco se600 Stratocaster Vintage Patina $725 Dover</a>
    """
    tied = resolve_facebook_profile(profile, request, excerpt, lambda _url: tied_html, read_item)
    assert tied["resolved"] is False
    assert tied["url"] == profile
    assert "/marketplace/item/" not in tied["url"]
    assert tied["link_label"] == "View seller profile"
    assert tied["availability"] == "unconfirmed"
    blocked = resolve_facebook_profile(profile, request, excerpt, lambda _url: "Log in to Facebook", read_item)
    assert blocked["resolved"] is False
    assert blocked["url"] == profile
    assert blocked["link_label"] == "View seller profile"
    mismatch = resolve_facebook_profile(
        profile, request, excerpt, read_profile, lambda _url: "Honda Civic for sale",
    )
    assert mismatch["resolved"] is False
    assert mismatch["url"] == profile
    assert classify_availability("Sold As-Described. Buy it now.") == "available"
    assert classify_availability("Every order includes the Sold As-Described guarantee.") == "unconfirmed"
    ended_url = "https://shop.example/item/ended-model"
    active_url = "https://shop.example/item/active-model"
    decoy_url = "https://shop.example/item/other-model"
    ended_html = f"""
        <h1>Widget ST-62G-SPL ended</h1>
        <p>This listing has ended.</p>
        <p>Sold As-Described.</p>
        <section id="PromotedSimilarListings">
          <h2>Promoted similar listings</h2>
          <a href="{active_url}?bk=tracking-token">Widget ST-62G-SPL available</a>
          Add to cart
          <a href="{decoy_url}">Widget ST62G-65 other</a>
          Add to cart
        </section>
    """
    active_html = f"""
        <h1>Widget ST-62G-SPL</h1>
        <p>Buy it now. Add to cart. Sold As-Described.</p>
        <p>Seller Budda Bowl Dover, MA, United States</p>
        <section id="PromotedSimilarListings">
          <p>This listing has ended</p>
          <a href="{ended_url}">Widget ST-62G-SPL old</a>
        </section>
    """
    ended_info = inspect_listing_html(ended_html, "Widget ST-62G-SPL", ended_url)
    assert ended_info["status"] == "ended"
    assert [link["url"] for link in ended_info["similar"]] == [active_url]
    active_info = inspect_listing_html(active_html, "Widget ST-62G-SPL", active_url)
    assert active_info["status"] == "available"
    assert active_info["location"] == "Dover, MA, United States"
    pages = {ended_url: ended_html, active_url: active_html}
    found, incomplete = expand_candidates(
        [{"url": ended_url, "title": "Widget ST-62G-SPL ended", "body": ""}],
        "Widget ST-62G-SPL",
        lambda url: pages.get(url, ""),
    )
    assert incomplete == ""
    assert [item["url"] for item in found] == [active_url]
    assert found[0]["availability"] == "available"
    assert found[0]["qualifying"] is True
    assert found[0]["location"] == "Dover, MA, United States"
    assert found[0]["discovery_url"] == ended_url
    expensive = "https://shop.example/item/expensive-map"
    affordable = "https://shop.example/item/affordable-map"
    budget_text = "Item: Epiphone USA Map guitar\nBudget: 875 plus shipping — firm maximum"
    offer_pad = " Electric guitar reissue in used working condition as pictured." * 4
    offer_pages = {
        expensive: f"<h1>Epiphone USA Map guitar</h1><p>Buy it now $1,129.99.{offer_pad}</p>",
        affordable: f"<h1>Epiphone USA Map guitar</h1><p>Buy it now $700.00.{offer_pad}</p>",
    }
    continued, continued_incomplete = expand_candidates(
        [
            {"url": expensive, "title": "Epiphone USA Map guitar", "body": "$1,129.99"},
            {"url": affordable, "title": "Epiphone USA Map guitar", "body": "$700.00"},
        ],
        budget_text,
        lambda url: offer_pages.get(url, ""),
        buyer_text=budget_text,
    )
    assert continued_incomplete == ""
    assert [item["url"] for item in continued if item.get("qualifying")] == [affordable]
    assert qualifies(continued[0], budget_text) is False or continued[0]["url"] == affordable
    assert any(item["url"] == expensive and item["qualifying"] is False for item in continued)
    assert requested_item(budget_text) == "Epiphone USA Map guitar"
    assert text_has_model("Epiphone Les Paul guitar for sale", "Epiphone USA Map guitar") is False
    shipped = {
        "availability": "available",
        "location": "",
        "title": "Epiphone USA Map guitar",
        "text": "Buy it now $800",
        "page_text": "Buy it now $800. Shipping $50.00.",
        "price": 800,
        "shipping": None,
    }
    assert qualifies(shipped, "Budget: 875 including shipping — firm maximum") is True
    assert qualifies(
        {**shipped, "page_text": "Buy it now $800", "shipping": None},
        "Budget: 875 including shipping — firm maximum",
    ) is False
    seeds = listing_seeds(
        [
            {"href": "https://www.ebay.com/itm/1", "title": "Epiphone USA Map guitar", "body": ""},
            {"href": "https://reverb.com/item/2-epiphone-usa-map", "title": "Epiphone USA Map guitar", "body": ""},
            {"href": "https://www.guitar-list.com/compareprices/8597", "title": "EPIPHONE USA MAP GUITAR", "body": ""},
            {"href": "https://www.ebay.com/itm/3", "title": "Epiphone USA Map guitar used", "body": ""},
        ],
        8,
    )
    assert [item["url"] for item in seeds] == [
        "https://www.ebay.com/itm/1",
        "https://reverb.com/item/2-epiphone-usa-map",
        "https://www.ebay.com/itm/3",
    ]
    over_url = "https://shop.example/item/over-budget-map"
    within_url = "https://shop.example/item/within-budget-map"
    other_url = "https://shop.example/item/other-guitar"
    offer_pad = " Electric guitar reissue in used working condition as pictured." * 3

    def offer_page(url, price, links):
        linked = "".join(f'<a href="{href}">{label}</a>' for href, label in links)
        return (
            "<html><head><script type=\"application/ld+json\">"
            "{\"@type\":\"Product\",\"name\":\"Epiphone USA Map guitar\","
            f"\"offers\":{{\"price\":\"{price}\",\"priceCurrency\":\"USD\",\"url\":\"{url}\","
            "\"shippingDetails\":{\"shippingRate\":{\"value\":\"25.00\"}}}}"
            "</script></head><body><h1>Epiphone USA Map guitar</h1>"
            f"<p>Buy it now. Add to cart.{offer_pad}</p>{linked}</body></html>"
        )

    linked_pages = {
        over_url: offer_page(
            over_url,
            "882.41",
            [
                (within_url, "Epiphone USA Map guitar"),
                (other_url, "Epiphone Les Paul guitar"),
            ],
        ),
        within_url: offer_page(within_url, "875.00", []),
        other_url: offer_page(other_url, "100.00", []),
    }
    followed, followed_incomplete = expand_candidates(
        [{"url": over_url, "title": "Epiphone USA Map guitar", "body": ""}],
        budget_text,
        lambda url: linked_pages.get(url, ""),
        buyer_text=budget_text,
    )
    assert followed_incomplete == ""
    assert [item["url"] for item in followed if item.get("qualifying")] == [within_url]
    assert any(item["url"] == over_url and item["price"] == 882.41 and item["qualifying"] is False for item in followed)
    assert all(item["url"] != other_url for item in followed)
    assert followed[0]["price"] == 875.0
    later_url = "https://shop.example/item/later-les-paul"
    linked_pages[later_url] = (
        "<h1>Epiphone Les Paul guitar</h1><p>Buy it now. Add to cart. "
        + ("Different model in stock. " * 12)
        + "</p>"
    )
    followed_tight, tight_incomplete = expand_candidates(
        [
            {"url": over_url, "title": "Epiphone USA Map guitar", "body": ""},
            {"url": later_url, "title": "Epiphone Les Paul guitar", "body": ""},
        ],
        budget_text,
        lambda url: linked_pages.get(url, ""),
        buyer_text=budget_text,
        budget=2,
    )
    assert [item["url"] for item in followed_tight if item.get("qualifying")] == [within_url]
    assert all(item["url"] != later_url for item in followed_tight)
    assert "page budget" in tight_incomplete
    cap_850 = "Item: Epiphone USA Map guitar\nBudget: 850 plus shipping — firm maximum"
    cluster_url = "https://shop.example/item/higher-cluster"
    parent_url = "https://shop.example/item/parent-reissue"
    exact_url = "https://shop.example/item/beautiful-condition"
    higher_url = "https://shop.example/item/still-over"
    filler_url = "https://shop.example/item/unrelated-model"
    sale_pad = " Electric guitar reissue in used working condition as pictured." * 3

    def reduced_page(url, original, current, shipping, links):
        linked = "".join(f'<a href="{href}">{label}</a>' for href, label in links)
        return (
            "<html><head><script type=\"application/ld+json\">"
            "{\"@type\":\"Product\",\"offers\":{"
            f"\"price\":\"{original}\",\"priceCurrency\":\"USD\",\"url\":\"{url}\","
            f"\"shippingDetails\":{{\"shippingRate\":{{\"value\":\"{shipping}\"}}}}}}}}"
            "</script></head><body><h1>Epiphone USA Map guitar</h1>"
            f"<p>Original Price ${original} New Price ${current} + ${shipping} Shipping "
            f"Buy it now. Add to cart.{sale_pad}</p>{linked}</body></html>"
        )

    sale_pages = {
        cluster_url: reduced_page(cluster_url, "980.45", "882.41", "310.71", [
            (higher_url, "Epiphone USA Map guitar"),
        ]),
        parent_url: reduced_page(parent_url, "1200.00", "1119.99", "46.00", [
            (exact_url, "Epiphone USA Map guitar Beautiful Condition"),
            (higher_url, "Epiphone USA Map guitar"),
        ]),
        exact_url: reduced_page(exact_url, "918.00", "850.00", "150.00", []),
        higher_url: reduced_page(higher_url, "918.00", "875.00", "80.00", []),
        filler_url: (
            "<h1>Epiphone Les Paul guitar</h1><p>Buy it now. Add to cart. "
            + ("Different model in stock. " * 12)
            + "</p>"
        ),
    }
    exact_info = inspect_listing_html(sale_pages[exact_url], "Epiphone USA Map guitar", exact_url)
    assert exact_info["price"] == 850
    assert exact_info["shipping"] == 150
    assert exact_info["price"] != 918
    found_exact, exact_incomplete = expand_candidates(
        [
            {"url": cluster_url, "title": "Epiphone USA Map guitar", "body": ""},
            {"url": parent_url, "title": "Epiphone USA Map guitar", "body": ""},
            {"url": filler_url, "title": "Epiphone Les Paul guitar", "body": ""},
        ],
        cap_850,
        lambda url: sale_pages.get(url, ""),
        buyer_text=cap_850,
        budget=3,
    )
    assert [item["url"] for item in found_exact if item.get("qualifying")] == [exact_url]
    exact_match = next(item for item in found_exact if item["url"] == exact_url)
    assert exact_match["price"] == 850
    assert exact_match["shipping"] == 150
    assert price_against_budget(850, buyer_price_cap(cap_850), 150)["status"] == "within"
    assert exact_url not in {cluster_url, parent_url}
    assert search_outcome([]) == "No qualifying match found in the sources checked"
    assert search_outcome(found) == "Found 1 qualifying match(es)"
    assert search_outcome([{"availability": "unconfirmed"}]) == "No qualifying match found in the sources checked"
    assert search_outcome([], "the page budget was used before every candidate was opened") == (
        "Search incomplete — the page budget was used before every candidate was opened"
    )
    usa = market_label("Epiphone", "USA Map")[2]
    assert usa == market_label("Epiphone", "USA Map guitar")[2]
    assert usa != market_label("Epiphone", "USA Map Guitar Reissue 2005")[2]
    assert market_label("Fender", "ST-62G-spl")[2] == market_label("Fender", "ST62G-SPL")[2]
    assert market_label("Klipsch", "None") == ("Klipsch", "", "klipsch")
    assert market_label("None", "None")[2] == ""
    scope = market_scope_text("Epiphone USA Map", "market-1")
    assert scope.startswith("Market discovery\n")
    assert "not a buyer request" in scope
    assert is_market_discovery_run({"search_query": scope})
    assert not is_market_discovery_run({"search_query": "Find sellers for this buying request:"})
    assert source_identity("https://www.ebay.com/itm/278015631338") == "278015631338"
    assert source_identity("https://reverb.com/item/97393148-epiphone-usa-map") == "97393148"
    assert reservation_id_from(None) is None
    assert reservation_id_from(False) is None
    assert reservation_id_from(["550e8400-e29b-41d4-a716-446655440000"]) == "550e8400-e29b-41d4-a716-446655440000"
    assert WORKER_REVISION == "reservation-v2"
    assert rpc_failure(TimeoutError("The read operation timed out")) == "timeout"
    assert rpc_failure(Exception("permission denied for function reserve_seeker_ai")) == "permission"
    assert rpc_failure(Exception("Could not find the function public.claim_next_seeker_run")) == "missing"
    assert reserve_closed_reason("timeout") == "AI reservation timed out"
    assert claim_closed_reason("permission") == "job claim was denied"
    assert search_outcome([], reserve_closed_reason("cap")).startswith("Search incomplete — daily OpenAI cap")
    assert search_outcome([{"qualifying": True}], "AI reservation timed out").startswith("Found 1 qualifying match(es). Search incomplete")
    scope = market_scope_text("Epiphone USA Map", "market-1")
    assert offer_buyer_query({"title": "scope", "text": scope}) == ""
    assert offer_buyer_query({"title": "Epiphone USA Map guitar", "text": "for sale"}) == "Epiphone USA Map guitar"
    compared = market_comparisons(
        [{"text": scope, "url": "https://scope.example"}, {"text": "WTB Epiphone USA Map", "url": "https://buyer.example"}],
        [{"text": "for sale", "url": "https://seller.example", "availability": "available"}],
    )
    assert [buyer["url"] for buyer, _seller in compared] == ["https://buyer.example"]
    offer_queries, overflow = buyer_queries_for_offers([
        {"title": "Ended guitar", "text": "listing ended", "availability": "ended", "url": "https://ended.example"},
        {"title": "Epiphone USA Map guitar", "text": "for sale", "availability": "available", "url": "https://live.example"},
    ], 4)
    assert offer_queries == ["Epiphone USA Map guitar"]
    assert overflow is False
    legacy_sections = [
        {"id": "market-1", "make": "Epiphone", "model": "USA Map"},
        {"id": "alias-9", "make": "Epiphone", "model": "USA Map guitar"},
        {"id": "other-2", "make": "Ford", "model": "Mustang"},
    ]
    legacy_ids, _legacy_sellers, legacy_backfill = linked_record_ids(
        legacy_sections,
        [
            {"buyer_post_id": "legacy", "section_id": "alias-9"},
            {"buyer_post_id": "mixed", "section_id": "alias-9"},
            {"buyer_post_id": "mixed", "section_id": "other-2"},
        ],
        [
            {"id": "legacy", "section_id": None},
            {"id": "mixed", "section_id": None},
        ],
        [],
        "market-1",
    )
    assert legacy_ids == ["legacy"]
    assert legacy_backfill == [("buyer_posts", "legacy")]
    stale = stored_seller({"id": "seller-1", "availability": "available", "source_url": "https://shop.example/item/old", "username": "Old"})
    assert stale["qualifying"] is False
    ended_page = "<html><body>" + ("This listing has ended. " * 20) + "</body></html>"
    ended_offer = refresh_stored_offer(stale, lambda url: ended_page)
    assert ended_offer["availability"] == "ended"
    assert ended_offer["qualifying"] is False
    unreadable_offer = refresh_stored_offer(stale, lambda url: "nope")
    assert unreadable_offer["availability"] == "unconfirmed"
    assert unreadable_offer["qualifying"] is False
    facebook = "https://www.facebook.com/marketplace/item/1411175104520622/"
    selling = "Selling a rare piece of guitar history."
    assert author_intent(selling, facebook)["role"] == "sell"
    assert as_matching_buyer({"text": selling, "url": facebook, "platform": "facebook"}) is None
    assert author_intent("Looking to buy an Epiphone USA Map")["role"] == "buy"
    quoted = 'The index lists "WTB Epiphone USA Map". Selling a rare piece of guitar history.'
    assert author_intent(quoted)["role"] == "sell"
    nav = "Marketplace · Buy · Sell · WTB\nEpiphone USA Map"
    assert author_intent(nav)["role"] == "unresolved"
    related = "Looking to buy an Epiphone USA Map.\nRelated listings\nWTB Fender Stratocaster"
    related_intent = author_intent(related)
    assert "Looking to buy an Epiphone USA Map" in related_intent["wanted"]
    assert "Fender" not in related_intent["wanted"]
    mixed = author_intent("WTB Epiphone USA Map. WTS Gibson Les Paul for sale.")
    assert mixed["role"] == "mixed"
    assert "Gibson" not in mixed["wanted"]
    assert "Epiphone USA Map" in mixed["wanted"]
    buy_request = "\n".join((
        "Looking to buy an Epiphone USA Map.",
        "Budget: 850 plus shipping — firm maximum.",
        "Requirements: Ohio",
        "Condition: excellent",
        "No relic.",
        "Must include the original case.",
    ))
    buy_intent = author_intent(buy_request)
    assert buy_intent["source"] == buy_request
    assert "Budget: 850 plus shipping — firm maximum." in buy_intent["wanted"]
    assert "Requirements: Ohio" in buy_intent["wanted"]
    assert "Condition: excellent" in buy_intent["wanted"]
    assert "No relic." in buy_intent["wanted"]
    assert "Must include the original case." in buy_intent["wanted"]
    assert buyer_price_cap(buy_intent["wanted"])["limit"] == 850
    mixed_request = "\n".join((
        "WTB Epiphone USA Map.",
        "Budget: 850 plus shipping — firm maximum.",
        "Must include the original case.",
        "WTS Gibson Les Paul for sale.",
        "Asking $2000 including shipping.",
        "Requirements: Texas.",
    ))
    mixed_kept = author_intent(mixed_request)
    assert mixed_kept["source"] == mixed_request
    assert "Must include the original case." in mixed_kept["wanted"]
    assert "plus shipping" in mixed_kept["wanted"]
    assert "Gibson" not in mixed_kept["wanted"]
    assert "including shipping" in mixed_kept["offered"]
    assert "850" not in mixed_kept["offered"]
    assert author_intent("Not looking to buy an Epiphone USA Map.")["role"] == "unresolved"
    assert author_intent("I don't want to buy an Epiphone USA Map.")["role"] == "unresolved"
    sell_then_buy = "WTS Gibson Les Paul, but WTB Epiphone USA Map. Budget: $850 plus shipping, firm maximum. Must include the original case."
    reversed_mixed = author_intent(sell_then_buy)
    assert "Epiphone USA Map" in reversed_mixed["wanted"]
    assert "Budget: $850 plus shipping, firm maximum." in reversed_mixed["wanted"]
    assert "Must include the original case." in reversed_mixed["wanted"]
    assert "850" not in reversed_mixed["offered"]
    assert "original case" not in reversed_mixed["offered"]
    assert "Gibson" in reversed_mixed["offered"]
    buy_then_sell = "WTB Epiphone USA Map, but WTS Gibson Les Paul for sale. Asking $2000 including shipping."
    forward_mixed = author_intent(buy_then_sell)
    assert "2000" not in forward_mixed["wanted"]
    assert "including shipping" in forward_mixed["offered"]
    curly = "I don\u2019t want to buy an Epiphone USA Map. Selling mine."
    curly_sale = author_intent(curly)
    assert curly_sale["role"] == "sell"
    assert curly_sale["wanted"] == ""
    song = "Jody Watley — Looking For A New Love"
    song_url = "https://www.youtube.com/watch?v=qpMj8c_Imss"
    assert author_intent(song, song_url)["role"] == "unresolved"
    assert as_matching_buyer({"text": song, "url": song_url, "platform": "youtube", "title": song}) is None
    genuine = "WTB Epiphone USA Map.\nBudget: $850 plus shipping, firm maximum.\nMust include the original case."
    genuine_intent = author_intent(genuine)
    assert genuine_intent["role"] == "buy"
    assert genuine_intent["source"] == genuine
    assert "Budget: $850 plus shipping, firm maximum." in genuine_intent["wanted"]
    assert "Must include the original case." in genuine_intent["wanted"]
    product_page = "https://reverb.com/p/epiphone-usa-map-guitar-reissue"
    direct_item = "https://reverb.com/item/97393148-epiphone-usa-map"
    linked_item = "https://reverb.com/item/97393148-epiphone-usa-map-guitar"
    assert is_catalog_page(product_page) is True
    assert is_item_page(product_page) is False
    item_pad = "Epiphone USA Map guitar reissue in stock. " * 8
    item_html = (
        "<html><body><h1>Epiphone USA Map guitar</h1>"
        "<p>Buy it now. Add to cart. New Price $850. Located in Austin, TX.</p>"
        + item_pad + "</body></html>"
    )
    product_html = (
        "<html><body><h1>Epiphone USA Map Guitar Reissue</h1>"
        "<p>Shop new and used listings. From $800. Buy it now.</p>"
        f'<a href="{linked_item}">Epiphone USA Map guitar</a>'
        + item_pad + "</body></html>"
    )
    empty_product = (
        "<html><body><h1>Epiphone USA Map Guitar Reissue</h1>"
        "<p>From $800. Buy it now. No current listings.</p>"
        + item_pad + "</body></html>"
    )
    catalog_pages = {
        product_page: product_html,
        direct_item: item_html,
        linked_item: item_html,
    }
    followed, _followed_incomplete = expand_candidates(
        [{"url": product_page, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"}],
        "Epiphone USA Map",
        lambda url: catalog_pages.get(url, ""),
    )
    assert [item["url"] for item in followed] == [linked_item]
    assert followed[0]["price"] == 850
    assert followed[0]["discovery_url"] == product_page
    assert followed[0]["availability"] == "available"
    together, _together_incomplete = expand_candidates(
        [
            {"url": product_page, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"},
            {"url": direct_item, "title": "Epiphone USA Map guitar", "body": "New Price $850"},
        ],
        "Epiphone USA Map",
        lambda url: catalog_pages.get(url, ""),
    )
    assert len(together) == 1
    assert source_identity(together[0]["url"]) == "97393148"
    assert product_page not in [item["url"] for item in together]
    none_found, _none_incomplete = expand_candidates(
        [{"url": product_page, "title": "Epiphone USA Map Guitar Reissue", "body": "From $800"}],
        "Epiphone USA Map",
        lambda url: empty_product if url == product_page else "",
    )
    assert none_found == []
    stored_song = {"text": song, "url": song_url, "platform": "youtube", "title": song}
    stored_product = {
        "url": product_page,
        "text": "Epiphone USA Map Guitar Reissue From $800 Buy it now",
        "availability": "available",
        "title": "Epiphone USA Map Guitar Reissue",
    }
    stored_item = {
        "url": direct_item,
        "text": "Epiphone USA Map guitar for sale",
        "availability": "available",
        "price": 850,
        "title": "Epiphone USA Map guitar",
    }
    stored_buyer = {
        "text": genuine,
        "url": "https://forum.example/epiphone-wtb",
        "platform": "web",
        "title": "WTB Epiphone USA Map",
    }
    assert market_comparisons([stored_song], [stored_item], "Epiphone USA Map") == []
    assert market_comparisons([stored_buyer], [stored_product], "Epiphone USA Map") == []
    stored_pairs = market_comparisons([stored_buyer], [stored_product, stored_item], "Epiphone USA Map")
    assert [seller["url"] for _buyer, seller in stored_pairs] == [direct_item]
    misclassified = {"text": selling, "url": facebook, "platform": "facebook", "title": selling}
    assert market_comparisons([misclassified], [{"url": f"https://reverb.com/item/{i}", "text": "for sale"} for i in range(4)]) == []
    priced = dict(stale)
    priced["price"] = 400
    priced["shipping"] = 25
    priced["text"] = "Found for sale. Epiphone USA Map guitar $400 + $25 shipping"
    available_no_price = "<html><body>" + ("Buy it now. Add to cart. Epiphone USA Map guitar in stock. " * 8) + "</body></html>"
    no_price = refresh_stored_offer(priced, lambda url: available_no_price)
    assert no_price["price"] is None
    assert no_price["previous_price"] == 400
    assert qualifies(no_price, "Budget: 850 plus shipping — firm maximum") is False
    available_no_shipping = "<html><body>" + ("Buy it now. Add to cart. New Price $800. Epiphone USA Map guitar. " * 8) + "</body></html>"
    no_shipping = refresh_stored_offer(priced, lambda url: available_no_shipping)
    assert no_shipping["price"] == 800
    assert no_shipping["shipping"] is None
    assert no_shipping["previous_shipping"] == 25
    assert qualifies(no_shipping, "Budget: 900 including shipping — firm maximum") is False
    print("availability rules ok")


if __name__ == "__main__":
    if "--check" in sys.argv:
        check_rules()
    else:
        main()
