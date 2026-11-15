"""Pure shaping helpers for the fitment catalog. No network and no Django."""

import re

from .catalog_parse import present_category, present_name
from .catalog_route import confidence_label, visible_to_buyers

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

PAID_STATUSES = {"active", "trialing"}


def blank(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_uuid(value):
    return isinstance(value, str) and bool(UUID_RE.match(value.strip()))


def clean_tokens(query):
    """Turn a part search into a short list of literal tokens."""
    if not isinstance(query, str):
        return []
    tokens = []
    for piece in query.replace("+", " ").split():
        token = "".join(ch for ch in piece if ch.isalnum() or ch in "-/")
        if token:
            tokens.append(token[:40])
        if len(tokens) == 8:
            break
    return tokens


def name_matches(part_name, query):
    tokens = clean_tokens(query)
    if not tokens:
        return False
    haystack = (part_name or "").casefold()
    return all(token.casefold() in haystack for token in tokens)


def _unique_sorted(values):
    seen = {}
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        seen.setdefault(text.casefold(), text)
    return [seen[key] for key in sorted(seen)]


def cascade(rows, make=None, model=None):
    """Shape vehicle rows into the next dropdown level."""
    make_filter = (make or "").strip().casefold()
    model_filter = (model or "").strip().casefold()
    matched = []
    for row in rows:
        row_make = (row.get("make") or "").strip()
        row_model = (row.get("model") or "").strip()
        if make_filter and row_make.casefold() != make_filter:
            continue
        if model_filter and row_model.casefold() != model_filter:
            continue
        matched.append(row)

    if make_filter and model_filter:
        years = sorted({int(row["year"]) for row in matched})
        return {
            "make": matched[0]["make"] if matched else make.strip(),
            "model": matched[0]["model"] if matched else model.strip(),
            "years": years,
        }
    if make_filter:
        return {
            "make": matched[0]["make"] if matched else make.strip(),
            "models": _unique_sorted(row.get("model") for row in matched),
        }
    return {"makes": _unique_sorted(row.get("make") for row in matched)}


def group_parts(fitment_rows, parts_by_id):
    """Group a vehicle's fitment rows by part category."""
    grouped = {}
    for row in fitment_rows:
        part = parts_by_id.get(row.get("part_id")) or {}
        name = present_name(part.get("part_name"))
        category = present_category(name, part.get("category"))
        grouped.setdefault(category, []).append({
            "id": part.get("id") or row.get("part_id"),
            "part_name": name,
            "category": category,
            "oem_part_number": part.get("oem_part_number"),
            "brand": part.get("brand"),
            "source": row.get("source"),
            "confirmed_at": row.get("confirmed_at"),
        })

    categories = []
    for name in sorted(grouped):
        parts = sorted(grouped[name], key=lambda item: (item.get("part_name") or "").casefold())
        categories.append({"category": name, "parts": parts})
    count = sum(len(category["parts"]) for category in categories)
    return categories, count


def fitment_vehicles(fitment_rows, vehicles_by_id, exclude_vehicle_id=None):
    """One vehicle entry per fitment row, skipping a source car when asked."""
    excluded = str(exclude_vehicle_id) if exclude_vehicle_id else None
    seen = set()
    vehicles = []
    for row in fitment_rows:
        vehicle = vehicles_by_id.get(row.get("vehicle_id")) or {}
        vehicle_id = str(vehicle.get("id") or row.get("vehicle_id") or "")
        if not vehicle_id or vehicle_id == excluded or vehicle_id in seen:
            continue
        if not visible_to_buyers(row):
            continue
        seen.add(vehicle_id)
        entry = {
            "id": vehicle.get("id") or row.get("vehicle_id"),
            "fitment_id": row.get("id"),
            "year": vehicle.get("year"),
            "make": vehicle.get("make"),
            "model": vehicle.get("model"),
            "generation": vehicle.get("generation"),
            "engine": vehicle.get("engine"),
            "body_style": vehicle.get("body_style"),
            "source": row.get("source"),
            "confirmed_at": row.get("confirmed_at"),
        }
        label = confidence_label(row.get("confidence"))
        if label:
            entry["confidence"] = float(row["confidence"])
            entry["confidence_label"] = label
        if row.get("qualifier"):
            entry["qualifier"] = row["qualifier"]
        if row.get("match_method"):
            entry["match_method"] = row["match_method"]
        vehicles.append(entry)
    vehicles.sort(key=lambda item: (
        item.get("year") or 0,
        (item.get("make") or "").casefold(),
        (item.get("model") or "").casefold(),
    ))
    return vehicles


def cross_car_matches(matched_parts, fitment_rows, vehicles_by_id, source_vehicle_id):
    by_part = {}
    for row in fitment_rows:
        by_part.setdefault(row.get("part_id"), []).append(row)

    matches = []
    total = 0
    for part in matched_parts:
        also_fits = fitment_vehicles(
            by_part.get(part.get("id"), []),
            vehicles_by_id,
            exclude_vehicle_id=source_vehicle_id,
        )
        total += len(also_fits)
        matches.append({
            "part": {
                "id": part.get("id"),
                "part_name": part.get("part_name"),
                "category": part.get("category"),
                "oem_part_number": part.get("oem_part_number"),
                "brand": part.get("brand"),
            },
            "also_fits": also_fits,
        })
    matches.sort(key=lambda item: (item["part"].get("part_name") or "").casefold())
    return matches, total


def subscription_is_paid(row):
    if not row:
        return False
    return row.get("tier") == "paid" and row.get("status") in PAID_STATUSES


def tier_for_status(status):
    stored = status or "inactive"
    if stored in PAID_STATUSES:
        return "paid", stored
    return "free", stored


def vehicle_record(row):
    try:
        year = int(str(row.get("year", "")).strip())
    except (TypeError, ValueError):
        return None
    vehicle_id = blank(row.get("id"))
    make = blank(row.get("make"))
    model = blank(row.get("model"))
    if not is_uuid(vehicle_id) or not make or not model or not 1900 <= year <= 2100:
        return None
    return {
        "id": vehicle_id,
        "year": year,
        "make": make,
        "model": model,
        "generation": blank(row.get("generation")),
        "engine": blank(row.get("engine")),
        "body_style": blank(row.get("body_style")),
    }


def part_record(row):
    part_id = blank(row.get("id"))
    part_name = blank(row.get("part_name"))
    if not is_uuid(part_id) or not part_name:
        return None
    return {
        "id": part_id,
        "part_name": part_name,
        "category": blank(row.get("category")),
        "oem_part_number": blank(row.get("oem_part_number")),
        "brand": blank(row.get("brand")),
    }


def fitment_record(row):
    fitment_id = blank(row.get("id"))
    part_id = blank(row.get("part_id"))
    vehicle_id = blank(row.get("vehicle_id"))
    if not is_uuid(fitment_id) or not is_uuid(part_id) or not is_uuid(vehicle_id):
        return None
    return {
        "id": fitment_id,
        "part_id": part_id,
        "vehicle_id": vehicle_id,
        "source": blank(row.get("source")),
        "confirmed_at": blank(row.get("confirmed_at")),
    }


def dedupe_fitment(rows):
    """Keep the first row for each part/vehicle pair."""
    seen = set()
    kept = []
    dropped = 0
    for row in rows:
        key = (row["part_id"], row["vehicle_id"])
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


def normalize_extracted_parts(parts, source_vehicle=None):
    """Keep only explicit part and fitment fields from a Reader result."""
    if not isinstance(parts, list):
        raise ValueError("parts must be a list")
    source_key = None
    if source_vehicle:
        source_key = (
            int(source_vehicle["year"]),
            source_vehicle["make"].strip().casefold(),
            source_vehicle["model"].strip().casefold(),
        )

    cleaned = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        name = blank(item.get("part_name"))
        if not name:
            continue
        also_fits = []
        for vehicle in item.get("also_fits") or []:
            if not isinstance(vehicle, dict):
                continue
            try:
                year = int(vehicle.get("year"))
            except (TypeError, ValueError):
                continue
            make = blank(vehicle.get("make"))
            model = blank(vehicle.get("model"))
            if not make or not model or not 1900 <= year <= 2100:
                continue
            if source_key == (year, make.casefold(), model.casefold()):
                continue
            also_fits.append({
                "year": year,
                "make": make[:40],
                "model": model[:80],
                "engine": (blank(vehicle.get("engine")) or "")[:80] or None,
            })
            if len(also_fits) == 20:
                break
        cleaned.append({
            "part_name": name[:120],
            "category": (blank(item.get("category")) or "")[:80] or None,
            "oem_part_number": (blank(item.get("oem_part_number")) or "")[:80] or None,
            "brand": (blank(item.get("brand")) or "")[:80] or None,
            "also_fits": also_fits,
        })
        if len(cleaned) == 100:
            break
    return cleaned
