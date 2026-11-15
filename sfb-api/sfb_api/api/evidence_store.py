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


def add_assessment(found, evidence=None, today=None):
    """Save this search's observations, then assess every listing against the store."""
    from .assessment import assess_listing

    store = evidence if evidence is not None else default_evidence()
    listings = found.get("listings") or []
    rows = [evidence_row(item) for item in listings]
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
    for item in listings:
        record = evidence_row(item) or item
        record["identifiers"] = item.get("identifiers")
        record["configuration"] = item.get("configuration")
        record["side"] = item.get("side")
        record["assembly"] = item.get("assembly")
        item["assessment"] = assess_listing(record, stored, interchange, today=today)
    reader = found.get("reader")
    if isinstance(reader, list):
        by_url = {item.get("url"): item for item in listings if item.get("url")}
        for row in reader:
            if not isinstance(row, dict):
                continue
            source = by_url.get(row.get("link") or row.get("url") or "")
            if source and source.get("assessment"):
                row["assessment"] = source["assessment"]
    return found
