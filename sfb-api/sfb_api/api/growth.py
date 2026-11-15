"""Reader and Matcher for catalog growth.

The Reader turns catalog text the caller already has into part rows.
The Matcher reuses an existing part when the OEM number or name and
category already exist, then writes fitment so cross-car lookup works.
"""

import json

from django.conf import settings

from .catalog import ensure_fitment, find_part, find_vehicles, insert_part, insert_vehicle
from .catalog_shape import normalize_extracted_parts

READER_PROMPT = """You extract automotive parts from catalog text the user supplies.
Return JSON only, with this shape:
{"parts":[{"part_name":"","category":"","oem_part_number":"","brand":"","also_fits":[{"year":1995,"make":"","model":"","engine":""}]}]}
Rules:
- Include only parts and vehicles that the text states.
- Do not invent part numbers, brands, years, or fitment.
- also_fits lists other vehicles that share that part. Omit the source vehicle.
- Leave unknown strings empty.
"""


class AmbiguousVehicle(Exception):
    def __init__(self, vehicles):
        self.vehicles = vehicles
        super().__init__("More than one vehicle matches that year, make, and model.")


def read_parts(text, vehicle):
    from openai import OpenAI

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    clipped = text.strip()[:12000]
    if not clipped:
        raise ValueError("Catalog text is empty.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": READER_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Source vehicle: {vehicle['year']} {vehicle['make']} {vehicle['model']}\n\n"
                    f"{clipped}"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=45,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return normalize_extracted_parts(payload.get("parts") or [], vehicle)


def resolve_source_vehicle(vehicle):
    engine = vehicle.get("engine") or None
    matches = find_vehicles(vehicle["year"], vehicle["make"], vehicle["model"], engine)
    if engine and matches:
        return matches[0], False
    if not engine:
        matches = find_vehicles(vehicle["year"], vehicle["make"], vehicle["model"])
        if len(matches) > 1:
            raise AmbiguousVehicle(matches)
        if len(matches) == 1:
            return matches[0], False
    if matches:
        return matches[0], False
    created = insert_vehicle(vehicle)
    return created, True


def _resolve_other_vehicle(spec):
    matches = find_vehicles(
        spec["year"],
        spec["make"],
        spec["model"],
        spec.get("engine"),
    )
    if matches:
        return matches[0]
    if not spec.get("engine"):
        any_engine = find_vehicles(spec["year"], spec["make"], spec["model"])
        if any_engine:
            return any_engine[0]
    return insert_vehicle({
        "year": spec["year"],
        "make": spec["make"],
        "model": spec["model"],
        "engine": spec.get("engine"),
        "generation": None,
        "body_style": None,
    })


def write_growth(vehicle, parts, source="growth"):
    """Store parts and the fitment links that make cross-car search work."""
    source_vehicle, created_vehicle = resolve_source_vehicle(vehicle)
    parts_created = 0
    fitment_created = 0
    stored_parts = []

    for part in parts:
        existing = find_part(part)
        if existing:
            stored = existing
        else:
            stored = insert_part(part)
            parts_created += 1
        if ensure_fitment(stored["id"], source_vehicle["id"], source):
            fitment_created += 1

        linked = []
        for other in part.get("also_fits") or []:
            other_vehicle = _resolve_other_vehicle(other)
            if other_vehicle["id"] == source_vehicle["id"]:
                continue
            if ensure_fitment(stored["id"], other_vehicle["id"], source):
                fitment_created += 1
            linked.append({
                "id": other_vehicle["id"],
                "year": other_vehicle.get("year"),
                "make": other_vehicle.get("make"),
                "model": other_vehicle.get("model"),
            })
        stored_parts.append({
            "id": stored["id"],
            "part_name": stored.get("part_name"),
            "oem_part_number": stored.get("oem_part_number"),
            "also_fits": linked,
        })

    return {
        "vehicle": {
            "id": source_vehicle["id"],
            "year": source_vehicle.get("year"),
            "make": source_vehicle.get("make"),
            "model": source_vehicle.get("model"),
            "created": created_vehicle,
        },
        "parts_created": parts_created,
        "fitment_created": fitment_created,
        "parts": stored_parts,
    }


def grow_vehicle(vehicle, parts=None, text=None, source="growth"):
    if parts is None:
        parts = read_parts(text or "", vehicle)
    else:
        parts = normalize_extracted_parts(parts, vehicle)
    if not parts:
        raise ValueError("No parts to store.")
    return write_growth(vehicle, parts, source=source or "growth")
