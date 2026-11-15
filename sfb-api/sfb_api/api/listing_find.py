"""Read listing sites for one part. Nothing here is written into the catalog.

eBay and OldCarsData run only when their keys are set. RockAuto's closeout
feed runs only when the caller already has that car's RockAuto code.
A refused host is skipped. The next source is still tried.
"""

import os
import time

from .catalog_fetch import CatalogBlocked, CatalogMissing, fetch_catalog
from .ebay_client import EbayBrowseClient, EbayNotConfigured, build_query, listing_facts
from .listing_sources import (
    CL_CITIES,
    OLD_CARS_LIVE,
    RENNLIST_RSS,
    FORUM_LIMIT,
    accessible_sources,
    brave_forum_url,
    brave_results,
    craigslist_city_url,
    forum_source_id,
    SHARED_SITES,
    get_forum_sites,
    get_subs_for_vehicle,
    keep_listing,
    reddit_posts,
    reddit_search_url,
    rockauto_rss_url,
    rss_items,
    search_text,
)


def _named(items, source_id, part_name, require_part=True):
    kept = []
    for item in items:
        if require_part and not keep_listing(item, part_name):
            continue
        item["source"] = source_id
        kept.append(item)
    return kept


def _fetch_rss(url, get, pause, limit=8):
    text = fetch_catalog(url, timeout=12, pause=pause, get=get)
    pause(1)
    return rss_items(text, limit=limit)


def find_listings(part_name, make="", model="", year="", carcode=None, get=None, pause=None, ebay=None, old_cars=None, read=None, evidence=None, today=None):
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
            for item in ebay.search_parts(query, limit=20):
                facts = listing_facts(item)
                listings.append({
                    "source": "ebay",
                    "title": (item.get("title") or "")[:180],
                    "url": item.get("itemWebUrl") or item.get("itemAffiliateWebUrl") or "",
                    "snippet": "",
                    "item_id": facts["item_id"],
                    "price_amount": facts["price_amount"],
                    "price_currency": facts["price_currency"],
                    "shipping_amount": facts["shipping_amount"],
                    "shipping_currency": facts["shipping_currency"],
                    "item_country": facts["item_country"],
                    "identifiers": facts["identifiers"],
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

    listings.extend(_craigslist(part_name, get, pause, skipped))
    listings.extend(_reddit(part_name, make, model, get, pause, skipped))
    listings.extend(_safe("rennlist", RENNLIST_RSS, part_name, get, pause, skipped))
    listings.extend(_forums(part_name, make, get, pause, skipped))
    listings.extend(_shared_listings(part_name, get, pause, skipped))

    found = {
        "query": text,
        "listings": listings[:100],
        "skipped": skipped,
        "sources": accessible_sources(),
        "reader": None,
    }
    from .evidence_store import add_assessment

    return add_assessment(
        attach_reader(
            found,
            {"year": year, "make": make, "model": model},
            read=read,
        ),
        evidence=evidence,
        today=today,
    )


def _money_label(amount, currency):
    return " ".join(piece for piece in (str(amount or "").strip(), str(currency or "").strip()) if piece)


def _source_note(item):
    notes = []
    price = _money_label(item.get("price_amount"), item.get("price_currency"))
    if price:
        notes.append("price " + price)
    shipping = _money_label(item.get("shipping_amount"), item.get("shipping_currency"))
    if shipping:
        notes.append("shipping " + shipping)
    for identifier in item.get("identifiers") or []:
        notes.append(f"{identifier.get('kind') or 'id'} {identifier.get('value') or ''}".strip())
    return " ".join(notes)


def attach_reader(found, vehicle, read=None):
    """Hand the raw listings to the reader. A source price survives a blank reader price."""
    listings = found.get("listings") or []
    lines = [
        " | ".join((
            item.get("source") or "",
            item.get("city") or item.get("subreddit") or item.get("site") or "",
            item.get("title") or "",
            item.get("url") or "",
            item.get("snippet") or "",
            _source_note(item),
        ))
        for item in listings
    ]
    raw = "\n".join(lines).strip()
    if not raw:
        return found
    read = read or _read_listings
    try:
        found["reader"] = keep_source_facts(listings, read(raw, vehicle))
    except Exception:
        found["reader"] = None
    return found


def keep_source_facts(listings, reader):
    """A structured source price or part number replaces a reader blank or guess."""
    if not isinstance(reader, list):
        return reader
    by_url = {item.get("url"): item for item in listings if item.get("url")}
    for row in reader:
        if not isinstance(row, dict):
            continue
        source = by_url.get(row.get("link") or row.get("url") or "")
        if not source:
            continue
        if source.get("price_amount"):
            row["price"] = _money_label(source["price_amount"], source.get("price_currency"))
            row["price_amount"] = source["price_amount"]
            row["price_currency"] = source.get("price_currency")
        if source.get("shipping_amount"):
            row["shipping_amount"] = source["shipping_amount"]
            row["shipping_currency"] = source.get("shipping_currency")
        if source.get("item_id"):
            row["item_id"] = source["item_id"]
        identifiers = source.get("identifiers") or []
        if identifiers:
            row["identifiers"] = identifiers
            part_number = next(
                (item["value"] for item in identifiers if item.get("kind") in ("mpn", "oem")),
                "",
            )
            if part_number:
                row["oem_number"] = part_number
    return reader


def _read_listings(raw_text, vehicle):
    from .ai_reader import read_listings

    return read_listings(raw_text, vehicle)


def _forums(part_name, make, get, pause, skipped, max_sites=FORUM_LIMIT):
    """Read a few forums from Brave, one site per query. A refusal stops the rest."""
    domains = get_forum_sites(make)[:max_sites]
    return _site_search(part_name, domains, get, pause, skipped)[:12]


def _shared_listings(part_name, get, pause, skipped):
    """Hemmings and Bring a Trailer run on every search, unless Brave already refused."""
    if any(row.get("source") == "brave" for row in skipped):
        return []
    return _site_search(part_name, SHARED_SITES, get, pause, skipped)


def _site_search(part_name, domains, get, pause, skipped):
    kept = []
    for domain in domains:
        try:
            page = fetch_catalog(
                brave_forum_url(part_name, domain),
                timeout=12,
                pause=pause,
                get=get,
                retry_rate_limit=False,
            )
        except CatalogBlocked as exc:
            skipped.append({"source": "brave", "reason": str(exc)})
            break
        except CatalogMissing:
            pause(2)
            continue
        pause(2)
        for item in brave_results(page, domain):
            if not keep_listing(item, part_name):
                continue
            item["source"] = forum_source_id(domain)
            item["site"] = domain
            kept.append(item)
    return kept


def _craigslist(part_name, get, pause, skipped, max_cities=5):
    """Read a few city feeds. A refused city is skipped; the next city is still tried."""
    kept = []
    blocked = None
    for city in CL_CITIES[:max_cities]:
        try:
            for item in _named(
                _fetch_rss(craigslist_city_url(city, part_name), get, pause, limit=10),
                "craigslist",
                part_name,
            ):
                item["city"] = city
                kept.append(item)
        except CatalogBlocked as exc:
            blocked = exc
            if exc.status == 429:
                break
        except CatalogMissing:
            continue
    if not kept and blocked is not None:
        skipped.append({"source": "craigslist", "reason": str(blocked)})
    return kept[:15]


def _reddit(part_name, make, model, get, pause, skipped, max_subs=15):
    """Read the make's subs from Reddit's JSON search. A refusal stops the rest of Reddit."""
    kept = []
    blocked = None
    for subreddit in get_subs_for_vehicle(make, model)[:max_subs]:
        try:
            payload = fetch_catalog(
                reddit_search_url(subreddit, part_name),
                timeout=10,
                pause=pause,
                get=get,
                headers={"Accept": "application/json"},
            )
            pause(1.1)
        except CatalogBlocked as exc:
            blocked = exc
            break
        except CatalogMissing:
            continue
        for item in reddit_posts(payload, subreddit):
            if keep_listing(item, part_name):
                kept.append(item)
    if not kept and blocked is not None:
        skipped.append({"source": "reddit", "reason": str(blocked)})
    return kept[:15]


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
