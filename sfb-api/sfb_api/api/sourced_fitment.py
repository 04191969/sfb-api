"""Fitment records with explicit identity fields.

Variant, brake configuration, axle, side, and assembly are fields on the
record. A generation name or words in the part name do not establish them.
A supplier-reported previous number stays labeled and is not an equivalent
until a source confirms that it replaces this part.
"""

from .request_match import _head, is_caliper_request, request_is_specific, tokens

_CHECKED = "2026-11-15"

RECORDS = (
    {
        "manufacturer": "Porsche",
        "part_number": "9P1615123L",
        "part_name": "brake caliper",
        "year_from": 2014,
        "year_to": 2016,
        "make": "Porsche",
        "model": "911",
        "variant": "Turbo",
        "brake_configuration": "iron",
        "axle": "front",
        "side": "left",
        "assembly": "caliper without brake pads",
        "option_code": "PR:-450",
        "restrictions": (
            "Sources describe this row as without PCCB, option PR:-450. "
            "The PCCB left front caliper on the same diagram is 9P1615123K, option PR:450. "
            "Diagram 602-000 position 4 also lists 9P1615123L for the 991.1 Turbo S 3.8L 2014-2016. "
            "The same number is also on diagram 602-002 for the 991.2 Turbo 2016-2019. "
            "No finer 2015 production-date split is stated. Also use fastener 999 067 040 01."
        ),
        "sources": (
            {
                "url": "https://www.design911.com/parts/9P1615123L/",
                "checked_on": _CHECKED,
                "note": "Genuine Porsche caliper without brake pads. Diagram 602-000, front axle, 991 Turbo through 2016, 991.1 Turbo 3.8L 2014-16, position 4, also use 999 067 040 01.",
            },
            {
                "url": "https://www.design911.com/p/brake-caliper-front-porsche-991-turbo/",
                "checked_on": _CHECKED,
                "note": "Front calipers 9P1615123L and 9P1615124L for the 991 Turbo will not fit PCCB.",
            },
            {
                "url": "https://www.design911.com/diagrams/d/42975/9P1615123L",
                "checked_on": _CHECKED,
                "note": "Diagram 602-000, Disc brake / Front axle 991 Turbo through 2016. Indexed parts table: 9P1615123L, left, PR:-450. The live render did not repeat that table; the fastener on the part page matches the table row. The page text uses -I450 for cars without PCCB.",
            },
            {
                "url": "https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514",
                "checked_on": _CHECKED,
                "note": "Genuine Porsche left Turbo calliper, front-axle diagram 602-00, 911 991 2014-2019. Previous article numbers are supplier-reported only.",
            },
            {
                "url": "https://teile.com/pdf/teilecom_en_PCCB-fixed-calliper-C2-C2S-C4-C4S-Turbo-L_9P1615123K.pdf",
                "checked_on": _CHECKED,
                "note": "9P1615123K is the PCCB left front caliper for the 991.1 Turbo 2014-2016.",
            },
        ),
        "previous_numbers": (
            {
                "number": "99135142713",
                "relation": "supplier_reported_previous",
                "replacement_confirmed": False,
                "source_url": "https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514",
                "checked_on": _CHECKED,
                "note": "Teile lists this as a previous article number. Replacement applicability for this iron front-left caliper is not confirmed.",
            },
            {
                "number": "99135142712",
                "relation": "supplier_reported_previous",
                "replacement_confirmed": False,
                "source_url": "https://teile.com/en/porsche-parts-shop/model-911-991/27/Wheels-brakes/602A-Disc-brake-front-axle/30/602-00-Disc-brake-front/Brake-calliper-Turbo-L/67514",
                "checked_on": _CHECKED,
                "note": "Teile lists this as a previous article number. Replacement applicability for this iron front-left caliper is not confirmed.",
            },
        ),
    },
)


def _same(left, right):
    left = str(left or "").strip()
    right = str(right or "").strip()
    return bool(left and right) and left.casefold() == right.casefold()


def matches_request(record, request):
    """True when every structured identity field equals the request."""
    request = request or {}
    record = record or {}
    if not is_caliper_request(request.get("part_name")):
        return False
    if not request_is_specific(request):
        return False
    try:
        year = int(str(request.get("year") or "").strip())
        year_from = int(record.get("year_from"))
        year_to = int(record.get("year_to"))
    except (TypeError, ValueError):
        return False
    if year < year_from or year > year_to:
        return False
    for key in ("make", "model", "variant", "brake_configuration", "axle", "side"):
        if not _same(record.get(key), request.get(key)):
            return False
    if not str(record.get("assembly") or "").strip():
        return False
    head = _head(request.get("part_name"))
    if head and head not in set(tokens(record.get("part_name"))):
        return False
    return True


def _stored_records():
    """Rows from the sourced fitment table. A missing table leaves the application records."""
    try:
        from django.conf import settings

        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            return []
        from .supabase_client import get_supabase

        result = (
            get_supabase()
            .table("sourced_fitment")
            .select(
                "manufacturer,part_number,part_name,year_from,year_to,make,model,"
                "variant,brake_configuration,axle,side,assembly,option_code,restrictions"
            )
            .limit(50)
            .execute()
        )
    except Exception:
        return []
    rows = []
    for row in result.data or []:
        if not all(str(row.get(key) or "").strip() for key in (
            "part_number", "variant", "brake_configuration", "axle", "side", "assembly",
        )):
            continue
        rows.append(row)
    return rows


def applicable_records(request):
    seen = set()
    chosen = []
    for record in list(RECORDS) + _stored_records():
        number = str(record.get("part_number") or "").strip()
        if not number or number.casefold() in seen:
            continue
        if not matches_request(record, request):
            continue
        seen.add(number.casefold())
        chosen.append(record)
    return chosen


def oem_numbers_for_request(request, limit=3):
    """Current manufacturer numbers only. Unconfirmed previous numbers stay out."""
    numbers = []
    for record in applicable_records(request):
        number = str(record.get("part_number") or "").strip()
        if number.casefold() in {item.casefold() for item in numbers}:
            continue
        numbers.append(number)
        if len(numbers) >= limit:
            break
    return numbers


def confirms(part_number, request):
    number = str(part_number or "").strip()
    if not number:
        return False
    return any(
        _same(record.get("part_number"), number)
        for record in applicable_records(request)
    )


def previous_numbers(part_number):
    """Supplier-reported previous numbers. Unconfirmed ones are not equivalents."""
    number = str(part_number or "").strip().casefold()
    found = []
    for record in RECORDS:
        if str(record.get("part_number") or "").strip().casefold() != number:
            continue
        for item in record.get("previous_numbers") or ():
            found.append(dict(item))
    return found
