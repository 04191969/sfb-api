"""Read listing sites for one part. Nothing here is written into the catalog.

eBay and OldCarsData run only when their keys are set. RockAuto's closeout
feed runs only when the caller already has that car's RockAuto code.
A refused host is skipped. The next source is still tried.
"""

import os
import time

from .catalog_fetch import CatalogBlocked, CatalogMissing, fetch_catalog
from .ebay_client import EbayBrowseClient, EbayNotConfigured, build_query
from .listing_sources import (
    OLD_CARS_LIVE,
    RENNLIST_RSS,
    accessible_sources,
    bing_site_url,
    craigslist_rss_url,
    keep_listing,
    rockauto_rss_url,
    rss_items,
    search_text,
    source_for_url,
)


def _named(items, source_id, part_name, require_part=True):
    kept = []
    for item in items:
        if require_part and not keep_listing(item, part_name):
            continue
        item["source"] = source_id
        kept.append(item)
    return kept


def _fetch_rss(url, get, pause):
    text = fetch_catalog(url, timeout=12, pause=pause, get=get)
    pause(1)
    return rss_items(text)


def find_listings(part_name, make="", model="", year="", carcode=None, get=None, pause=None, ebay=None, old_cars=None, read=None):
    """Return listings, sources that were skipped, and the raw text a reader can use."""
    part_name = str(part_name or "").strip()
    text = search_text(part_name, make, model, year)
    listings = []
    skipped = []
    if pause is None:
        pause = time.sleep

    if ebay is None:
        try:
            ebay = EbayBrowseClient()
        except EbayNotConfigured as exc:
            ebay = exc
    if isinstance(ebay, EbayNotConfigured):
        skipped.append({"source": "ebay", "reason": str(ebay)})
    else:
        try:
            query = build_query(
                {"part_name": part_name},
                {"year": year, "make": make, "model": model},
            )
            for item in ebay.search_parts(query, limit=5):
                listings.append({
                    "source": "ebay",
                    "title": (item.get("title") or "")[:180],
                    "url": item.get("itemWebUrl") or item.get("itemAffiliateWebUrl") or "",
                    "snippet": "",
                })
        except Exception as exc:
            skipped.append({"source": "ebay", "reason": str(exc)})

    key = os.environ.get("OLD_CARS_DATA_API_KEY", "")
    if old_cars is None and not key:
        skipped.append({"source": "oldcarsdata", "reason": "OLD_CARS_DATA_API_KEY is not set."})
    else:
        try:
            payload = old_cars(text) if old_cars else _old_cars(text, key, get, pause)
            for item in payload:
                if keep_listing(item, part_name):
                    item["source"] = "oldcarsdata"
                    listings.append(item)
        except (CatalogBlocked, CatalogMissing) as exc:
            skipped.append({"source": "oldcarsdata", "reason": str(exc)})
        except Exception as exc:
            skipped.append({"source": "oldcarsdata", "reason": str(exc)})

    rockauto = rockauto_rss_url(carcode)
    if not rockauto:
        skipped.append({"source": "rockauto", "reason": "RockAuto closeout RSS needs the carcode from the vehicle page."})
    else:
        listings.extend(_safe("rockauto", rockauto, part_name, get, pause, skipped))

    listings.extend(_safe("craigslist", craigslist_rss_url(text), part_name, get, pause, skipped))
    listings.extend(_safe("rennlist", RENNLIST_RSS, part_name, get, pause, skipped))

    try:
        for item in _fetch_rss(bing_site_url(text), get, pause):
            source = source_for_url(item.get("url"))
            if not source:
                continue
            item["source"] = source
            listings.append(item)
    except (CatalogBlocked, CatalogMissing) as exc:
        skipped.append({"source": "bing", "reason": str(exc)})

    found = {
        "query": text,
        "listings": listings[:40],
        "skipped": skipped,
        "sources": accessible_sources(),
        "reader": None,
    }
    return attach_reader(
        found,
        {"year": year, "make": make, "model": model},
        read=read,
    )


def attach_reader(found, vehicle, read=None):
    """Hand the raw listings to the reader. A missing key or a bad reply keeps the listings."""
    lines = [
        " | ".join((
            item.get("source") or "",
            item.get("title") or "",
            item.get("url") or "",
            item.get("snippet") or "",
        ))
        for item in found.get("listings") or []
    ]
    raw = "\n".join(lines).strip()
    if not raw:
        return found
    read = read or _read_listings
    try:
        found["reader"] = read(raw, vehicle)
    except Exception:
        found["reader"] = None
    return found


def _read_listings(raw_text, vehicle):
    from .ai_reader import read_catalog

    return read_catalog(raw_text, "listing-search", vehicle)


def _safe(source_id, url, part_name, get, pause, skipped):
    try:
        return _named(_fetch_rss(url, get, pause), source_id, part_name)
    except (CatalogBlocked, CatalogMissing) as exc:
        skipped.append({"source": source_id, "reason": str(exc)})
        return []


def _old_cars(text, key, get, pause):
    import json
    from urllib.parse import urlencode

    url = OLD_CARS_LIVE + "?" + urlencode({"keyword": text, "limit": 5})
    response_text = fetch_catalog(
        url,
        timeout=12,
        pause=pause,
        get=get,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    pause(1)
    payload = json.loads(response_text or "{}")
    rows = payload.get("data") if isinstance(payload, dict) else payload
    items = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or row.get("name") or ""
        snippet = " ".join(
            str(piece)
            for piece in (
                row.get("source"),
                row.get("year"),
                row.get("listing_make"),
                row.get("listing_model"),
                row.get("price"),
            )
            if piece not in (None, "")
        )
        items.append({
            "title": str(title)[:180],
            "url": row.get("url") or row.get("link") or row.get("listing_url") or "",
            "snippet": snippet[:250],
        })
    return items
