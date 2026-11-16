"""Reviewed parts lists. Eight applications are conditional. Everything else is a lead.

Manufacturer brand, aftermarket part number, supplier SKU, and OEM number stay
separate. An aftermarket or supplier number is not stored as an OEM number.
A discovery lead can be labeled. It does not establish fitment or enter a
price comparison. Unknown does not satisfy a condition.
"""

import json
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"
_UNKNOWN = frozenset({"", "unknown", "i don't know", "i dont know"})
_NUMBER_CONDITIONS = frozenset({"capacity_gallons", "fuel_pump_lph"})


def _load(name):
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


def _text(value):
    return " ".join(str(value or "").split())


def _stated(value):
    text = _text(value)
    if text.casefold() in _UNKNOWN:
        return ""
    return text


def _part_name(record):
    assembly = _text((record.get("conditions") or {}).get("assembly"))
    if "dash" in assembly.casefold():
        return "dash kit"
    return "fuel tank"


def _identity(record, identifier_type):
    """Typed identity. Only an OEM-typed value may occupy oem_part_number."""
    oem = _text(record.get("oem_part_number")) or None
    if identifier_type != "oem_part_number":
        oem = None
    return {
        "brand": _text(record.get("brand")) or None,
        "publisher": _text(record.get("publisher")) or None,
        "part_number": _text(record.get("part_number")),
        "identifier_type": identifier_type,
        "oem_part_number": oem,
    }


def _applications():
    rows = []
    seen = set()
    for record in _load("supported_conditional_applications.json"):
        review_id = _text(record.get("review_id"))
        if not review_id or review_id in seen:
            continue
        seen.add(review_id)
        identity = _identity(record, "aftermarket_manufacturer_part_number")
        rows.append({
            **identity,
            "review_id": review_id,
            "part_name": _part_name(record),
            "make": _text(record.get("make")),
            "model": _text(record.get("model")),
            "year_from": int(record.get("year_from")),
            "year_to": int(record.get("year_to")),
            "conditions": dict(record.get("conditions") or {}),
            "source_url": record.get("source_url") or "",
            "fitment": "conditional",
            "price_eligible": False,
        })
    return tuple(rows)


def _lead(record, identifier_type, status):
    identity = _identity(record, identifier_type)
    if not identity["part_number"]:
        return None
    return {
        **identity,
        "identifier_status": status,
        "fitment_eligible": False,
        "price_eligible": False,
        "label": "discovery lead",
    }


def _discovery_rows():
    rows = []
    for record in _load("discovery_identifiers.json"):
        lead = _lead(
            record,
            "discovery_identifier",
            record.get("identifier_status") or "provider-reported; not manufacturer verified",
        )
        if lead:
            rows.append(lead)
    for record in _load("mechanical_part_evidence.json"):
        lead = _lead(
            record,
            record.get("identifier_type") or "manufacturer_part_number",
            record.get("fitment_status") or "discovery lead",
        )
        if lead and not lead["brand"]:
            lead["brand"] = _text(record.get("publisher")) or None
        if lead:
            lead["cross_claims"] = tuple(
                {
                    "number": _text(item.get("number")),
                    "make": _text(item.get("make")),
                    "relationship": _text(item.get("relationship")),
                    "replacement_confirmed": False,
                    "fitment_eligible": False,
                    "price_eligible": False,
                }
                for item in record.get("oe_references") or []
                if _text(item.get("number"))
            )
            rows.append(lead)
    return tuple(rows)


APPLICATIONS = _applications()
DISCOVERY = _discovery_rows()
_DISCOVERY_INDEX = {}
for _row in DISCOVERY:
    _key = (_text(_row.get("brand")).casefold(), _row["part_number"].casefold())
    _DISCOVERY_INDEX.setdefault(_key, []).append(_row)


def _head(part_name):
    words = [word for word in _text(part_name).casefold().split() if len(word) > 2]
    return words[-1] if words else ""


def _year(value):
    try:
        return int(_text(value))
    except (TypeError, ValueError):
        return None


def _condition_matches(required, stated, key):
    if required is None:
        return stated == ""
    if not stated:
        return False
    if key in _NUMBER_CONDITIONS:
        try:
            return float(stated) == float(required)
        except (TypeError, ValueError):
            return False
    return stated.casefold() == _text(required).casefold()


def application_matches(record, request):
    """Every stored condition has to be stated. A different capacity or model does not match."""
    request = request or {}
    if _head(request.get("part_name")) != _head(record.get("part_name")):
        return False
    if not _text(record.get("make")) or not _text(record.get("model")):
        return False
    if _text(request.get("make")).casefold() != record["make"].casefold():
        return False
    if _text(request.get("model")).casefold() != record["model"].casefold():
        return False
    year = _year(request.get("year"))
    if year is None or year < record["year_from"] or year > record["year_to"]:
        return False
    brand = _stated(request.get("brand"))
    if brand and brand.casefold() != _text(record.get("brand")).casefold():
        return False
    number = _stated(request.get("part_number"))
    if number and number.casefold() != record["part_number"].casefold():
        return False
    for key, required in (record.get("conditions") or {}).items():
        if not _condition_matches(required, _stated(request.get(key)), key):
            return False
    return True


def matching_applications(request):
    chosen = []
    for record in APPLICATIONS:
        if not application_matches(record, request):
            continue
        copy = dict(record)
        copy["conditions"] = dict(record["conditions"])
        chosen.append(copy)
    return chosen


def discovery_for(brand, part_number):
    """Brand and number together. The same digits under another brand are a different lead."""
    brand = _text(brand)
    number = _text(part_number)
    if not brand or not number:
        return []
    return [dict(row) for row in _DISCOVERY_INDEX.get((brand.casefold(), number.casefold()), ())]


def cross_claims(brand, part_number):
    """Labeled OE claims. None of them is a confirmed replacement or interchange."""
    claims = []
    for row in discovery_for(brand, part_number):
        claims.extend(dict(item) for item in row.get("cross_claims") or ())
    return claims


def annotate_search(found):
    """Attach conditional applications and discovery labels. Neither one qualifies a price."""
    request = (found or {}).get("request") or {}
    found["applications"] = matching_applications(request)
    found["discovery"] = discovery_for(request.get("brand"), request.get("part_number"))
    return found
