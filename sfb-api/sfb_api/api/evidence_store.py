"""Asking and sold observations, one row per listing.

A second observation of the same source and listing id replaces the first.
It does not become a second comparison.
"""

from datetime import datetime, timezone
from decimal import Decimal

from .assessment import money, part_number_of


def _iso(value):
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _number(value):
    amount = money(value)
    if amount is None:
        return None
    return format(amount, "f")


def evidence_row(listing, observed_at=None):
    source, listing_id = (
        str(listing.get("source") or "").strip(),
        str(listing.get("item_id") or listing.get("listing_id") or listing.get("url") or "").strip(),
    )
    if not source or not listing_id:
        return None
    observed = observed_at or datetime.now(timezone.utc)
    status = listing.get("sale_status") or listing.get("status") or "asking"
    if status not in ("asking", "sold"):
        status = "asking"
    # A title that says "sale" is still an asking offer. Sold needs a sale date.
    if status == "sold" and not _iso(listing.get("sold_at")):
        status = "asking"
    return {
        "source": source,
        "listing_id": listing_id,
        "part_number": part_number_of(listing) or None,
        "price_amount": _number(listing.get("price_amount")),
        "price_currency": listing.get("price_currency") or None,
        "shipping_amount": _number(listing.get("shipping_amount")),
        "shipping_currency": listing.get("shipping_currency") or None,
        "tax_amount": _number(listing.get("tax_amount")),
        "tax_currency": listing.get("tax_currency") or None,
        "core_amount": _number(listing.get("core_amount")),
        "core_refund": _number(listing.get("core_refund")),
        "status": status,
        "observed_at": _iso(observed),
        "sold_at": _iso(listing.get("sold_at")) if status == "sold" else None,
        "item_country": listing.get("item_country") or None,
        "url": listing.get("url") or "",
        "title": (listing.get("title") or "")[:180],
    }


class MemoryEvidence:
    """Request-local store. Tests use it, and a search uses it when Supabase is unavailable."""

    def __init__(self, rows=None, interchange=None):
        self.rows = {}
        self._interchange = list(interchange or [])

    def upsert(self, records):
        for record in records:
            if not record:
                continue
            self.rows[(record["source"], record["listing_id"])] = dict(record)
        return True

    def for_parts(self, part_numbers):
        wanted = {str(number).casefold() for number in part_numbers if str(number or "").strip()}
        if not wanted:
            return []
        return [
            dict(row) for row in self.rows.values()
            if str(row.get("part_number") or "").casefold() in wanted
        ]

    def interchange_for(self, part_numbers):
        wanted = {str(number).casefold() for number in part_numbers if str(number or "").strip()}
        if not wanted:
            return []
        chosen = []
        for record in self._interchange:
            pair = {
                str(record.get("part_number") or "").casefold(),
                str(record.get("equivalent_part_number") or "").casefold(),
            }
            if pair & wanted:
                chosen.append(dict(record))
        return chosen


class SupabaseEvidence:
    def upsert(self, records):
        rows = [row for row in records if row]
        if not rows:
            return True
        from .supabase_client import get_supabase

        get_supabase().table("listing_evidence").upsert(
            rows, on_conflict="source,listing_id"
        ).execute()
        return True

    def for_parts(self, part_numbers):
        numbers = [str(number) for number in part_numbers if str(number or "").strip()]
        if not numbers:
            return []
        from .supabase_client import get_supabase

        result = (
            get_supabase()
            .table("listing_evidence")
            .select(
                "source,listing_id,part_number,price_amount,price_currency,"
                "shipping_amount,shipping_currency,tax_amount,tax_currency,"
                "core_amount,core_refund,status,observed_at,sold_at,item_country,url,title"
            )
            .in_("part_number", numbers)
            .execute()
        )
        return result.data or []

    def interchange_for(self, part_numbers):
        numbers = [str(number) for number in part_numbers if str(number or "").strip()]
        if not numbers:
            return []
        from .supabase_client import get_supabase

        client = get_supabase()
        columns = "part_number,equivalent_part_number,configuration,side,assembly"
        by_part = client.table("supported_interchange").select(columns).in_("part_number", numbers).execute()
        by_equivalent = (
            client.table("supported_interchange").select(columns).in_("equivalent_part_number", numbers).execute()
        )
        seen = set()
        chosen = []
        for row in (by_part.data or []) + (by_equivalent.data or []):
            key = (
                row.get("part_number"),
                row.get("equivalent_part_number"),
                row.get("configuration"),
                row.get("side"),
                row.get("assembly"),
            )
            if key in seen:
                continue
            seen.add(key)
            chosen.append(row)
        return chosen


def _with_equivalents(numbers, interchange):
    wanted = [number for number in numbers if str(number or "").strip()]
    seen = {number.casefold() for number in wanted}
    for record in interchange or []:
        pair = (record.get("part_number"), record.get("equivalent_part_number"))
        if not any(str(number or "").casefold() in seen for number in pair):
            continue
        for number in pair:
            text = str(number or "").strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                wanted.append(text)
    return wanted


def default_evidence():
    from django.conf import settings

    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return MemoryEvidence()
    return SupabaseEvidence()


def _copy_reader_offers(found):
    """The reader's product name is part of what the listing offers."""
    reader = found.get("reader")
    if not isinstance(reader, list):
        return
    by_url = {item.get("url"): item for item in found.get("listings") or [] if item.get("url")}
    for row in reader:
        if not isinstance(row, dict):
            continue
        source = by_url.get(row.get("link") or row.get("url") or "")
        name = str(row.get("offered") or row.get("part_name") or "").strip()
        if source is not None and name:
            source["offered_name"] = name


def _mark_verified(listing, number):
    for item in listing.get("identifiers") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("value") or "").strip().casefold() != number.casefold():
            continue
        if (item.get("kind") or item.get("type")) in ("mpn", "oem"):
            item["verified"] = True


def catalog_confirms(part_number, vehicle):
    """True when catalog manufacturer data fits this number to the requested vehicle.

    A caliper matches structured fitment fields. Words in a part name, or a
    variant written into the vehicle generation, do not.
    """
    from django.conf import settings

    from .request_match import is_caliper_request, request_is_specific
    from .sourced_fitment import confirms as sourced_confirms

    number = str(part_number or "").strip()
    vehicle = vehicle or {}
    year = str(vehicle.get("year") or "").strip()
    make = str(vehicle.get("make") or "").strip()
    model = str(vehicle.get("model") or "").strip()
    if not number or not year.isdigit() or not make or not model:
        return False
    requested = vehicle.get("part_name") or ""
    if is_caliper_request(requested):
        if not request_is_specific(vehicle):
            return False
        return sourced_confirms(number, vehicle)
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return False
    try:
        from .catalog import fitment_for_parts, part_by_oem, search_vehicles
        from .request_match import _conflict_word, _head, tokens

        part = part_by_oem(number)
        if not part:
            return False
        catalog_name = part.get("part_name") or ""
        if _conflict_word(requested, catalog_name):
            return False
        head = _head(requested)
        if head and head not in set(tokens(catalog_name)):
            return False
        vehicles = search_vehicles(make, model, int(year))
        vehicle_ids = {row.get("id") for row in vehicles}
        if not vehicle_ids:
            return False
        for row in fitment_for_parts([part["id"]]):
            if row.get("vehicle_id") in vehicle_ids:
                return True
        return False
    except Exception:
        return False


def _position_agrees(listing, request):
    """A stated axle or side that differs from the request is not that part."""
    request = request or {}
    for listing_key, request_key in (("configuration", "axle"), ("side", "side")):
        stated = str(listing.get(listing_key) or "").strip()
        wanted = str(request.get(request_key) or "").strip()
        if stated and wanted and stated.casefold() != wanted.casefold():
            return False
    return True


def _classify_rows(listings, request, confirm, answers=None, interchange=None):
    from .assessment import interchange_establishes, listing_key, part_number_of
    from .request_match import classify_listing, request_is_specific

    request = request or {}
    requested = request.get("part_name") or ""
    answers = answers if answers is not None else {}
    interchange = interchange or []

    def catalog_established(row):
        if row.get("fitment_established") is True:
            return True
        number = part_number_of(row)
        if not number or confirm is None:
            return False
        key = number.casefold()
        if key not in answers:
            try:
                answers[key] = bool(confirm(number, request))
            except Exception:
                answers[key] = False
        return answers[key]

    keys = set()
    for item in listings:
        match = classify_listing(requested, item, request, fitment_established=False)
        if (
            request_is_specific(request)
            and match.get("status") == "uncertain"
            and match.get("offered")
            and not match.get("ambiguous")
            and _position_agrees(item, request)
        ):
            fitment_by = ""
            if interchange_establishes(item, interchange, request):
                fitment_by = "interchange"
            elif catalog_established(item):
                fitment_by = "catalog"
                _mark_verified(item, part_number_of(item))
            if fitment_by:
                match = classify_listing(
                    requested, item, request, fitment_established=True, fitment_by=fitment_by,
                )
        item["request_match"] = match
        if match.get("status") == "qualifying":
            keys.add(listing_key(item))
    return keys


def _assessment_clock(today):
    """The instant this assessment uses. A date is not an observation time."""
    if isinstance(today, datetime):
        if today.tzinfo is None:
            return today.replace(tzinfo=timezone.utc)
        return today.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def add_assessment(found, evidence=None, today=None, confirm_fitment=None):
    """Save this search's observations, then assess every listing against the store."""
    from .assessment import assess_listing, listing_key

    _copy_reader_offers(found)
    if confirm_fitment is None:
        confirm_fitment = catalog_confirms
    request = found.get("request") or {}
    store = evidence if evidence is not None else default_evidence()
    listings = found.get("listings") or []
    from .assessment import part_number_of

    assessed_at = _assessment_clock(today)
    numbers = [part_number_of(item) for item in listings if part_number_of(item)]
    try:
        interchange = store.interchange_for(numbers)
    except Exception:
        interchange = []
    fitment_answers = {}
    qualifying = _classify_rows(listings, request, confirm_fitment, fitment_answers, interchange)
    rows = [evidence_row(item, observed_at=assessed_at) for item in listings]
    rows = [row for row in rows if row]
    saved = True
    try:
        store.upsert(rows)
    except Exception as exc:
        saved = False
        found["evidence_error"] = str(exc)
        store = MemoryEvidence()
        store.upsert(rows)
    numbers = [row.get("part_number") for row in rows if row.get("part_number")]
    try:
        interchange = store.interchange_for(numbers)
        numbers = _with_equivalents(numbers, interchange)
        stored = store.for_parts(numbers)
    except Exception as exc:
        saved = False
        found["evidence_error"] = str(exc)
        fallback = MemoryEvidence()
        fallback.upsert(rows)
        stored = fallback.for_parts(numbers)
        interchange = []
    found["evidence_saved"] = saved
    qualifying.update(_classify_rows(
        [row for row in stored if listing_key(row) not in qualifying],
        request,
        confirm_fitment,
        fitment_answers,
        interchange,
    ))
    for item in listings:
        record = evidence_row(item, observed_at=assessed_at) or item
        record["identifiers"] = item.get("identifiers")
        record["configuration"] = item.get("configuration")
        record["side"] = item.get("side")
        record["assembly"] = item.get("assembly")
        record["request_match"] = item.get("request_match")
        record["fitment_claims"] = item.get("fitment_claims")
        item["assessment"] = assess_listing(
            record, stored, interchange, today=assessed_at, qualifying_keys=qualifying,
        )
    reader = found.get("reader")
    if isinstance(reader, list):
        by_url = {item.get("url"): item for item in listings if item.get("url")}
        for row in reader:
            if not isinstance(row, dict):
                continue
            source = by_url.get(row.get("link") or row.get("url") or "")
            if source and source.get("assessment"):
                row["assessment"] = source["assessment"]
    found["offer_outcome"] = offer_outcome(listings)
    return found


def offer_outcome(listings):
    """Separate a search with no matching offer from a match that lacks a comparison."""
    qualifying = [
        row for row in listings or []
        if (row.get("request_match") or {}).get("status") == "qualifying"
    ]
    if not qualifying:
        return {"code": "no_matching_offer", "message": "No matching offer found."}
    statuses = {
        ((row.get("assessment") or {}).get("market") or {}).get("status")
        for row in qualifying
    }
    if statuses & {"limited", "median"}:
        return {"code": "compared", "message": ""}
    return {
        "code": "needs_comparison",
        "message": (
            "Needs data: a matching offer was found, "
            "but there is not enough comparison evidence."
        ),
    }
