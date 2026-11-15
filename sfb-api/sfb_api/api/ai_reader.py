"""Reader: structured parts from a short catalog excerpt.

This is the Type step. It uses gpt-4o-mini when OPENAI_API_KEY is set.
Without that key the growth command keeps the parsed page as a confirmed fit.
"""

import json

from django.conf import settings

from .catalog_route import normalize_reader_parts

READER_SYSTEM = """You are an automotive parts data extraction expert.
You read raw text scraped from car parts websites and extract structured data.
You understand chassis codes, generation names, year ranges, fitment qualifiers,
and OEM cross-references. Return ONLY valid JSON."""

READER_USER = """Extract all parts and fitment data from this scraped text.

Source URL: {url}
Vehicle context: {year} {make} {model}

Raw text:
{raw_text}

Return JSON:
{{
  "parts": [
    {{
      "part_name": "",
      "category": "",
      "oem_numbers": [],
      "brand": "",
      "also_fits": [
        {{
          "year_start": 0,
          "year_end": 0,
          "make": "",
          "model": "",
          "qualifier": null,
          "confidence": 1.0
        }}
      ]
    }}
  ]
}}

Rules:
- Include only parts and vehicles the text states. Do not invent numbers or cars.
- confidence 1.0 is an explicit statement. 0.9 is strongly implied. 0.7 shares a platform. 0.5 is possible.
- qualifier is any limitation, or null.
- category is one of: Engine, Cooling, Exhaust, Fuel System, Brakes, Suspension, Steering, Transmission, Electrical, Body, Interior, HVAC, Wheels & Tires.
- Omit the source vehicle from also_fits.
"""


LISTING_SYSTEM = """You extract car-part listings from search snippets.
Return ONLY valid JSON. Use only words that appear in the text."""

LISTING_USER = """Vehicle context: {year} {make} {model}

Listings:
{raw_text}

Return JSON:
{{
  "listings": [
    {{
      "part_name": "",
      "oem_number": "",
      "price": "",
      "condition": "",
      "fitment_vehicles": [],
      "cross_fitment_hints": [],
      "source": "",
      "link": ""
    }}
  ]
}}

Rules:
- condition is New, Used, Rebuilt, or Unknown.
- fitment_vehicles are cars the text says the part fits.
- cross_fitment_hints are explicit also-fits or interchange statements.
- Leave a field blank when the text does not state it.
"""


class ReaderUnavailable(RuntimeError):
    pass


def read_catalog(raw_text, url, vehicle):
    if not settings.OPENAI_API_KEY:
        raise ReaderUnavailable("OPENAI_API_KEY is not set.")
    clipped = (raw_text or "").strip()[:8000]
    if not clipped:
        return []

    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.OPENAI_READER_MODEL,
        messages=[
            {"role": "system", "content": READER_SYSTEM},
            {
                "role": "user",
                "content": READER_USER.format(
                    url=url,
                    year=vehicle.get("year"),
                    make=vehicle.get("make"),
                    model=vehicle.get("model"),
                    raw_text=clipped,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=4000,
        timeout=45,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return normalize_reader_parts(payload, vehicle)


def read_listings(raw_text, vehicle, timeout=30):
    """Structured listings from forum, auction, and feed text. Nothing is saved."""
    if not settings.OPENAI_API_KEY:
        raise ReaderUnavailable("OPENAI_API_KEY is not set.")
    clipped = (raw_text or "").strip()[:48000]
    if not clipped:
        return []

    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.OPENAI_READER_MODEL,
        messages=[
            {"role": "system", "content": LISTING_SYSTEM},
            {
                "role": "user",
                "content": LISTING_USER.format(
                    year=vehicle.get("year") or "",
                    make=vehicle.get("make") or "",
                    model=vehicle.get("model") or "",
                    raw_text=clipped,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=8000,
        timeout=timeout,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    listings = payload.get("listings") if isinstance(payload, dict) else None
    return listings or []
