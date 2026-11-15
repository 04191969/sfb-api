"""Matcher: other cars that may share a part. This step is new. Nu World matches one buyer to one listing.

Suggestions under 0.50 are dropped. Cars already on the part are dropped.
The call uses the stronger model and only runs when OPENAI_API_KEY is set.
"""

import json

from django.conf import settings

from .catalog_route import keep_new_matches

MATCHER_SYSTEM = """You are an automotive parts interchange expert.
You know which cars share platforms, engines, and supplier parts.
Suggest only matches you would bet money on. Return ONLY valid JSON.
Do not suggest a match below 0.5 confidence, and do not repeat a known fit."""

MATCHER_USER = """Given this part and the vehicles it is known to fit, what OTHER vehicles likely use the same part?

Part: {part_name}
Category: {category}
OEM numbers: {oem_numbers}
Known to fit:
{known_fits}

Return JSON:
{{
  "potential_matches": [
    {{
      "year_start": 0,
      "year_end": 0,
      "make": "",
      "model": "",
      "reasoning": "",
      "confidence": 0.5,
      "qualifier": null
    }}
  ]
}}
"""


def find_matches(part_name, category, oem_numbers, known_fits):
    if not settings.OPENAI_API_KEY:
        return []
    known_text = "\n".join(
        f"  - {row.get('year')} {row.get('make')} {row.get('model')}"
        for row in known_fits or []
    ) or "  (none yet)"
    numbers = ", ".join(str(number) for number in oem_numbers if number) or "unknown"

    from openai import OpenAI

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=settings.OPENAI_MATCHER_MODEL,
        messages=[
            {"role": "system", "content": MATCHER_SYSTEM},
            {
                "role": "user",
                "content": MATCHER_USER.format(
                    part_name=part_name,
                    category=category or "",
                    oem_numbers=numbers,
                    known_fits=known_text,
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
        max_tokens=3000,
        timeout=45,
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return keep_new_matches(payload, known_fits)
