"""Consumer price assessment for one listing.

Confirmed sales and asking prices stay in separate sets. One valid asking
comparison is a limited reference. Two or more produce a raw median, the
count, the age, and the spread. None produces Needs data. This does not
decide whether a dealer should buy.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ASKING_DAYS = 14
SOLD_DAYS = 180
DOMESTIC = {"us", "usa", "united states"}


def part_number_of(record):
    """Manufacturer part number only. An interchange id is not this part."""
    for item in record.get("identifiers") or []:
        if item.get("kind") in ("mpn", "oem") and str(item.get("value") or "").strip():
            return str(item["value"]).strip()
    number = record.get("part_number")
    text = str(number or "").strip()
    if text and text.casefold() not in {"not listed", "unknown", "n/a", "none"}:
        return text
    return ""


def listing_key(record):
    source = str(record.get("source") or "").strip()
    listing_id = str(record.get("listing_id") or record.get("item_id") or record.get("url") or "").strip()
    return source, listing_id


def money(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _text(value):
    return " ".join(str(value or "").split())


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _age(today, observed):
    if observed is None or observed > today:
        return None
    return (today - observed).days


def _same_currency(left, right):
    return bool(left and right) and left.casefold() == right.casefold()


def interchange_applies(subject, other_number, records):
    """A supported interchange counts only with configuration, side, and assembly.

    The presence of an identifier is not enough, and the subject has to state
    the same three qualifiers the record was approved with.
    """
    subject_number = part_number_of(subject)
    other = _text(other_number)
    config = _text(subject.get("configuration"))
    side = _text(subject.get("side"))
    assembly = _text(subject.get("assembly"))
    if not subject_number or not other or not config or not side or not assembly:
        return False
    if subject_number.casefold() == other.casefold():
        return False
    wanted = {subject_number.casefold(), other.casefold()}
    for record in records or []:
        fields = (
            record.get("part_number"),
            record.get("equivalent_part_number"),
            record.get("configuration"),
            record.get("side"),
            record.get("assembly"),
        )
        if not all(_text(field) for field in fields):
            continue
        if _text(record.get("configuration")).casefold() != config.casefold():
            continue
        if _text(record.get("side")).casefold() != side.casefold():
            continue
        if _text(record.get("assembly")).casefold() != assembly.casefold():
            continue
        pair = {_text(record.get("part_number")).casefold(), _text(record.get("equivalent_part_number")).casefold()}
        if pair == wanted:
            return True
    return False


def same_part(subject, evidence, interchange):
    subject_number = part_number_of(subject)
    evidence_number = part_number_of(evidence)
    if not subject_number or not evidence_number:
        return False
    if subject_number.casefold() == evidence_number.casefold():
        return True
    return interchange_applies(subject, evidence_number, interchange)


def _recent(status, row, today):
    if status == "sold":
        observed = _day(row.get("sold_at"))
        limit = SOLD_DAYS
    else:
        observed = _day(row.get("observed_at"))
        limit = ASKING_DAYS
    age = _age(today, observed)
    if age is None or age > limit:
        return None
    return age


def _eligible(subject, evidence, interchange, today, status, basis):
    """Rows that can sit in one comparison set. The subject itself is excluded."""
    chosen = []
    subject_key = listing_key(subject)
    subject_currency = subject.get("price_currency")
    for row in evidence or []:
        if (row.get("status") or "asking") != status:
            continue
        if listing_key(row) == subject_key and all(subject_key):
            continue
        if not same_part(subject, row, interchange):
            continue
        price = money(row.get("price_amount"))
        if price is None or not _same_currency(subject_currency, row.get("price_currency")):
            continue
        age = _recent(status, row, today)
        if age is None:
            continue
        if basis == "item":
            chosen.append({"price": price, "age": age})
            continue
        shipping = money(row.get("shipping_amount"))
        tax = money(row.get("tax_amount"))
        if shipping is None or tax is None:
            continue
        if not _same_currency(row.get("price_currency"), row.get("shipping_currency")):
            continue
        if row.get("tax_currency") and not _same_currency(row.get("price_currency"), row.get("tax_currency")):
            continue
        chosen.append({"price": price + shipping + tax, "age": age})
    return chosen


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _money_text(value):
    if value is None:
        return None
    return f"{value.quantize(Decimal('0.01'))}"


def _set_result(rows, status_name, basis):
    count = len(rows)
    if count == 0:
        return {
            "status": "needs_data",
            "set": status_name,
            "basis": basis,
            "count": 0,
            "reference_price": None,
            "median": None,
            "spread": None,
            "low": None,
            "high": None,
            "age_days": None,
            "oldest_days": None,
            "limitation": "No valid comparison.",
        }
    prices = [row["price"] for row in rows]
    ages = [row["age"] for row in rows]
    if count == 1:
        return {
            "status": "limited",
            "set": status_name,
            "basis": basis,
            "count": 1,
            "reference_price": _money_text(prices[0]),
            "median": None,
            "spread": None,
            "low": None,
            "high": None,
            "age_days": ages[0],
            "oldest_days": ages[0],
            "limitation": "One comparison. This does not show whether prices are consistent.",
        }
    low, high = min(prices), max(prices)
    return {
        "status": "median",
        "set": status_name,
        "basis": basis,
        "count": count,
        "reference_price": None,
        "median": _money_text(_median(prices)),
        "spread": _money_text(high - low),
        "low": _money_text(low),
        "high": _money_text(high),
        "age_days": min(ages),
        "oldest_days": max(ages),
        "limitation": None,
    }


def _import_applicable(country):
    text = _text(country).casefold()
    if not text:
        return None
    if text in DOMESTIC:
        return False
    return True


def listing_costs(listing):
    """Known amounts stay visible. A total is present only when every part of it is known."""
    item = money(listing.get("price_amount"))
    shipping = money(listing.get("shipping_amount"))
    tax = money(listing.get("tax_amount"))
    core = money(listing.get("core_amount"))
    refund = money(listing.get("core_refund"))
    applicable = _import_applicable(listing.get("item_country"))
    import_cost = money(listing.get("import_cost")) if applicable else None
    missing = []
    if item is None:
        missing.append("item_price")
    if shipping is None:
        missing.append("shipping")
    if tax is None:
        missing.append("tax")
    if core is None:
        missing.append("core_deposit")
    if refund is None:
        missing.append("core_refund")
    if applicable is None:
        missing.append("ship_from_country")
    elif applicable and import_cost is None:
        missing.append("import_costs")
    amount_due = None
    if item is not None and shipping is not None and tax is not None and core is not None:
        if applicable is False or import_cost is not None:
            amount_due = item + shipping + tax + core + (import_cost or Decimal(0))
    expected = None
    if amount_due is not None and refund is not None:
        expected = amount_due - refund
    return {
        "item_price": _money_text(item),
        "item_currency": listing.get("price_currency"),
        "shipping": _money_text(shipping),
        "shipping_currency": listing.get("shipping_currency"),
        "tax": _money_text(tax),
        "core_deposit": _money_text(core),
        "core_refund": _money_text(refund),
        "import_applicable": applicable,
        "import_costs": _money_text(import_cost) if applicable else None,
        "amount_due": _money_text(amount_due),
        "expected_after_core": _money_text(expected),
        "missing": missing,
    }


def _summary(market, costs):
    if not part_number_of({"part_number": market.get("part_number")}):
        identity = "Needs data: this listing has no manufacturer part number, so no comparison was made."
    elif market["status"] == "needs_data":
        identity = "Needs data: no recent asking comparison for this part on the same price basis."
    elif market["status"] == "limited":
        identity = (
            f"Limited evidence: one asking comparison at {market['reference_price']} {market.get('currency') or ''}. "
            f"Checked {market['age_days']} days ago. Item price only."
        ).strip()
    else:
        identity = (
            f"Asking median {market['median']} {market.get('currency') or ''} "
            f"from {market['count']} comparisons. Newest {market['age_days']} days, "
            f"oldest {market['oldest_days']} days, spread {market['spread']}. Item price only."
        ).strip()
    item = costs.get("item_price")
    shipping = costs.get("shipping")
    parts = []
    if item:
        parts.append(f"Item price {item} {costs.get('item_currency') or ''}".strip())
    if shipping:
        parts.append(f"shipping {shipping} {costs.get('shipping_currency') or ''}".strip())
    if costs.get("missing"):
        parts.append("Needs data: " + ", ".join(costs["missing"]))
    return identity + (" " + ". ".join(parts) + "." if parts else "")


def assess_listing(listing, evidence, interchange=None, today=None):
    """Assess one listing. Asking evidence supplies the consumer reference."""
    today = today or date.today()
    number = part_number_of(listing)
    subject = dict(listing)
    subject["part_number"] = number
    costs = listing_costs(listing)
    if not number:
        market = _set_result([], "asking", "item")
        market["limitation"] = "No manufacturer part number, so listings were not compared."
    else:
        market = _set_result(_eligible(subject, evidence, interchange, today, "asking", "item"), "asking", "item")
    market["currency"] = listing.get("price_currency")
    market["part_number"] = number or None
    delivered = _set_result(
        _eligible(subject, evidence, interchange, today, "asking", "delivered") if number else [],
        "asking",
        "delivered",
    )
    if delivered["status"] == "needs_data":
        delivered["missing"] = [name for name in ("shipping", "tax") if name in costs["missing"]]
    sold = _set_result(
        _eligible(subject, evidence, interchange, today, "sold", "item") if number else [],
        "sold",
        "item",
    )
    sold["currency"] = listing.get("price_currency")
    result = {
        "part_number": number or None,
        "market": market,
        "delivered": delivered,
        "sold": sold,
        "costs": costs,
        "summary": _summary(market, costs),
    }
    return result
