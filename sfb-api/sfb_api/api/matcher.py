import json
from openai import OpenAI
from django.conf import settings

SYSTEM_PROMPT = """You are an automotive and collectibles parts matcher for Search Find Buy (SFB).
You analyze whether a buyer's want-to-buy post matches a seller's listing.

Consider:
- Make and model compatibility (exact match or same generation/platform)
- Part compatibility (same part, or compatible across years)
- Year range overlap (buyer's requested years vs seller's listed year)
- Cross-compatibility (e.g., Chevy 350 fits across GM platforms, Porsche 993 parts fit 1994-1998)
- Part number match (if provided, but least important)

Return valid JSON with these fields:
{
  "match": true/false,
  "confidence": 0-100,
  "reasoning": "detailed explanation of why this is or isn't a match",
  "make": "extracted make",
  "model": "extracted model",
  "part": "extracted part name",
  "buyer_years": "buyer's requested year(s)",
  "seller_years": "seller's listed year(s)",
  "cross_compatibility": "notes on cross-model/cross-brand compatibility if any"
}"""


def analyze_match(buyer_text: str, seller_text: str) -> dict:
    """Send a buyer/seller pair to OpenAI and return structured match analysis."""
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"BUYER POST:\n{buyer_text}\n\nSELLER LISTING:\n{seller_text}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        timeout=30,
    )

    result = json.loads(response.choices[0].message.content)

    # Ensure all expected fields exist
    result.setdefault("match", False)
    result.setdefault("confidence", 0)
    result.setdefault("reasoning", "")
    result.setdefault("make", "")
    result.setdefault("model", "")
    result.setdefault("part", "")
    result.setdefault("buyer_years", "")
    result.setdefault("seller_years", "")
    result.setdefault("cross_compatibility", "")

    return result
