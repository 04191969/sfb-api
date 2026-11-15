"""Read listing sites for one part. Nothing here is written into the catalog.

eBay and OldCarsData run only when their keys are set. RockAuto's closeout
feed runs only when the caller already has that car's RockAuto code.
A refused host is skipped. The next source is still tried.
"""

import os
import time

from .assessment import part_number_of, verified_part_number
from .catalog_fetch import CatalogBlocked, CatalogMissing, fetch_catalog
from .ebay_client import EbayBrowseClient, EbayNotConfigured, build_query, listing_facts, relevance_query
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
        if item.get("sale_status") != "sold" or not item.get("sold_at"):
            item["sale_status"] = "asking"
        kept.append(item)
    return kept


# Gunicorn stops the worker at 90 seconds. A catalog timeout is tried once,
# so leave room for that retry and for the response itself.
SEARCH_BUDGET = 55
# A new item call has to leave the other sources and the response inside the worker limit.
DETAIL_FLOOR = 28


def _fetch_rss(url, get, pause, limit=8, timeout=12):
    text = fetch_catalog(url, timeout=timeout, pause=pause, get=get)
    pause(1)
    return rss_items(text, limit=limit)


def _mark_deadline(skipped):
    if any(row.get("source") == "deadline" for row in skipped):
        return
    skipped.append({
        "source": "deadline",
        "reason": "Returned the listings already collected so the search finishes before the worker limit.",
    })


def _out_of_time(skipped, time_left):
    if time_left() >= 18:
        return False
    _mark_deadline(skipped)
    return True


def _call_timeout(time_left, default=6):
    """Per attempt. fetch_catalog tries a timeout once more, so keep each attempt short."""
    remaining = time_left()
    if remaining < 8:
        return 2
    return min(default, max(2, int((remaining - 12) / 2)))


def _merge_facts(listing, facts):
    """Add part numbers from the item call. A price already in hand stays."""
    seen = {
        (item.get("kind"), str(item.get("value") or "").casefold())
        for item in listing.get("identifiers") or []
    }
    merged = list(listing.get("identifiers") or [])
    for item in facts.get("identifiers") or []:
        key = (item.get("kind"), str(item.get("value") or "").casefold())
        if not item.get("value") or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    listing["identifiers"] = merged
    for field in (
        "price_amount", "price_currency", "shipping_amount",
        "shipping_currency", "item_country", "configuration", "side", "assembly",
    ):
        if listing.get(field) in (None, "") and facts.get(field) not in (None, ""):
            listing[field] = facts[field]
    claims = list(listing.get("fitment_claims") or [])
    seen_claims = {(item.get("type"), str(item.get("value") or "").casefold()) for item in claims}
    for item in facts.get("fitment_claims") or []:
        key = (item.get("type"), str(item.get("value") or "").casefold())
        if not item.get("value") or key in seen_claims:
            continue
        seen_claims.add(key)
        claims.append(item)
    if claims:
        listing["fitment_claims"] = claims


def _catalog_oem_numbers(part_name, year, make, model, request):
    """Manufacturer numbers for this exact request. A coarse request does not widen the search.

    A caliper number comes from structured fitment. Unconfirmed previous numbers
    are not searched. Other parts still use the catalog text lookup.
    """
    from django.conf import settings

    from .request_match import is_caliper_request, request_is_specific

    if not request_is_specific(request):
        return []
    if not str(year or "").strip().isdigit() or not make or not model or not part_name:
        return []
    if is_caliper_request(part_name):
        from .sourced_fitment import oem_numbers_for_request as sourced_numbers

        return sourced_numbers(request)
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return []
    try:
        from .catalog import oem_numbers_for_request

        return oem_numbers_for_request(part_name, make, model, year, request=request)
    except Exception:
        return []


def _ebay_hit(item):
    facts = listing_facts(item)
    return {
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
        "fitment_claims": facts["fitment_claims"],
        "configuration": facts["configuration"],
        "side": facts["side"],
        "assembly": facts["assembly"],
    }


def _collect_ebay(ebay, query, listings, seen, limit):
    for item in ebay.search_parts(query, limit=limit, sort=None):
        hit = _ebay_hit(item)
        item_id = hit["item_id"]
        if item_id and item_id in seen:
            continue
        if item_id:
            seen.add(item_id)
        listings.append(hit)


def _fill_part_numbers(ebay, listings, time_left):
    """Read item specifics while time remains. Cached details do not wait on the clock.

    Seller text is merged as the seller wrote it. A price already in hand stays.
    """
    if not hasattr(ebay, "get_item"):
        return
    for listing in listings:
        if listing.get("source") != "ebay" or part_number_of(listing):
            continue
        item_id = listing.get("item_id")
        detail = ebay.peek_item(item_id) if hasattr(ebay, "peek_item") else None
        if not isinstance(detail, dict):
            if time_left() < DETAIL_FLOOR:
                continue
            try:
                detail = ebay.get_item(
                    item_id,
                    timeout=min(6, max(2, int(time_left()) - 22)),
                )
            except Exception:
                continue
        if isinstance(detail, dict):
            _merge_facts(listing, listing_facts(detail))


def _source_reader(listings):
    """Rows the page can show when the reader does not run or does not answer."""
    rows = []
    for item in listings:
        number = verified_part_number(item)
        price = _money_label(item.get("price_amount"), item.get("price_currency"))
        rows.append({
            "part_name": item.get("title") or "",
            "oem_number": number or "Not listed",
            "price": price or "Not listed",
            "condition": item.get("condition") or "Not listed",
            "fitment_vehicles": [],
            "source": item.get("source") or "",
            "link": item.get("url") or "",
        })
    return rows


def find_listings(part_name, make="", model="", year="", carcode=None, get=None, pause=None, ebay=None, old_cars=None, read=None, evidence=None, today=None, now=None, budget_seconds=SEARCH_BUDGET, confirm_fitment=None, variant="", brake_configuration="", axle="", side=""):
    """Return listings, sources that were skipped, and the raw text a reader can use."""
    part_name = str(part_name or "").strip()
    request = {
        "year": year,
        "make": make,
        "model": model,
        "part_name": part_name,
        "variant": str(variant or "").strip(),
        "brake_configuration": str(brake_configuration or "").strip(),
        "axle": str(axle or "").strip(),
        "side": str(side or "").strip(),
    }
    text = search_text(part_name, make, model, year)
    listings = []
    skipped = []
    if pause is None:
        pause = time.sleep
    clock = now or time.monotonic
    deadline = clock() + budget_seconds

    def time_left():
        return deadline - clock()

    if ebay is None:
        try:
            ebay = EbayBrowseClient()
        except EbayNotConfigured as exc:
            ebay = exc
    if isinstance(ebay, EbayNotConfigured):
        skipped.append({"source": "ebay", "reason": str(ebay)})
    else:
        try:
            seen = set()
            vehicle_fields = {"year": year, "make": make, "model": model}
            for number in _catalog_oem_numbers(part_name, year, make, model, request):
                if time_left() < 40:
                    break
                try:
                    _collect_ebay(
                        ebay,
                        build_query({"part_name": part_name, "oem_part_number": number}, vehicle_fields),
                        listings,
                        seen,
                        limit=8,
                    )
                except Exception as exc:
                    skipped.append({"source": "ebay", "reason": str(exc)})
                    break
            _collect_ebay(
                ebay,
                relevance_query(part_name, year, make, model),
                listings,
                seen,
                limit=20,
            )
            _fill_part_numbers(ebay, listings, time_left)
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
        listings.extend(_safe("rockauto", rockauto, part_name, get, pause, skipped, time_left=time_left))

    if not _out_of_time(skipped, time_left):
        listings.extend(_craigslist(part_name, get, pause, skipped, time_left=time_left))
    if not _out_of_time(skipped, time_left):
        listings.extend(_reddit(part_name, make, model, get, pause, skipped, time_left=time_left))
    if not _out_of_time(skipped, time_left):
        listings.extend(_safe("rennlist", RENNLIST_RSS, part_name, get, pause, skipped, time_left=time_left))
    if not _out_of_time(skipped, time_left):
        listings.extend(_forums(part_name, make, get, pause, skipped, time_left=time_left))
    if not _out_of_time(skipped, time_left):
        listings.extend(_shared_listings(part_name, get, pause, skipped, time_left=time_left))

    found = {
        "query": text,
        "listings": listings[:100],
        "skipped": skipped,
        "sources": accessible_sources(),
        "reader": None,
    }
    from .evidence_store import add_assessment

    vehicle = request
    found["request"] = request
    if time_left() < 22:
        _mark_deadline(skipped)
        found["reader"] = keep_source_facts(found["listings"], _source_reader(found["listings"]))
    else:
        found = attach_reader(
            found,
            vehicle,
            read=read,
            read_timeout=min(15, max(8, int(time_left()) - 12)),
        )
    return add_assessment(found, evidence=evidence, today=today, confirm_fitment=confirm_fitment)


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


def attach_reader(found, vehicle, read=None, read_timeout=30):
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
    try:
        parsed = (
            _read_listings(raw, vehicle, timeout=read_timeout)
            if read is None
            else read(raw, vehicle)
        )
    except Exception:
        parsed = None
    if not isinstance(parsed, list) or not parsed:
        parsed = _source_reader(listings)
    found["reader"] = keep_source_facts(listings, parsed)
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
            verified = verified_part_number({"identifiers": identifiers})
            if verified:
                row["oem_number"] = verified
            else:
                claimed = part_number_of({"identifiers": identifiers})
                current = str(row.get("oem_number") or "").strip()
                if claimed and current.casefold() == claimed.casefold():
                    row["oem_number"] = "Not listed"
    return reader


def _read_listings(raw_text, vehicle, timeout=30):
    from .ai_reader import read_listings

    return read_listings(raw_text, vehicle, timeout=timeout)


def _forums(part_name, make, get, pause, skipped, max_sites=FORUM_LIMIT, time_left=None):
    """Read a few forums from Brave, one site per query. A refusal stops the rest."""
    domains = get_forum_sites(make)[:max_sites]
    return _site_search(part_name, domains, get, pause, skipped, time_left=time_left)[:12]


def _shared_listings(part_name, get, pause, skipped, time_left=None):
    """Hemmings and Bring a Trailer run on every search, unless Brave already refused."""
    if any(row.get("source") == "brave" for row in skipped):
        return []
    return _site_search(part_name, SHARED_SITES, get, pause, skipped, time_left=time_left)


def _site_search(part_name, domains, get, pause, skipped, time_left=None):
    time_left = time_left or (lambda: 10**9)
    kept = []
    for domain in domains:
        if _out_of_time(skipped, time_left):
            break
        try:
            page = fetch_catalog(
                brave_forum_url(part_name, domain),
                timeout=_call_timeout(time_left),
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


def _craigslist(part_name, get, pause, skipped, max_cities=5, time_left=None):
    """Read a few city feeds. A refused city is skipped; the next city is still tried."""
    time_left = time_left or (lambda: 10**9)
    kept = []
    blocked = None
    for city in CL_CITIES[:max_cities]:
        if _out_of_time(skipped, time_left):
            break
        try:
            for item in _named(
                _fetch_rss(
                    craigslist_city_url(city, part_name),
                    get,
                    pause,
                    limit=10,
                    timeout=_call_timeout(time_left),
                ),
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


def _reddit(part_name, make, model, get, pause, skipped, max_subs=15, time_left=None):
    """Read the make's subs from Reddit's JSON search. A refusal stops the rest of Reddit."""
    time_left = time_left or (lambda: 10**9)
    kept = []
    blocked = None
    for subreddit in get_subs_for_vehicle(make, model)[:max_subs]:
        if _out_of_time(skipped, time_left):
            break
        try:
            payload = fetch_catalog(
                reddit_search_url(subreddit, part_name),
                timeout=_call_timeout(time_left, default=10),
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


def _safe(source_id, url, part_name, get, pause, skipped, time_left=None):
    time_left = time_left or (lambda: 10**9)
    if _out_of_time(skipped, time_left):
        return []
    try:
        return _named(
            _fetch_rss(url, get, pause, timeout=_call_timeout(time_left)),
            source_id,
            part_name,
        )
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
