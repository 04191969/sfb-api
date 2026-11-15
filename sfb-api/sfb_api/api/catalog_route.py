"""Decide what a collected page is worth, then how sure a fitment link is.

The collection loop mirrors Nu World: a refused host stops, a missing page
is rejected, and a repeat pass updates a row only when the new score is better.
Confidence tiers are the new SFB piece. Nu World does not score fitment.
"""

YEAR_CAP = 8


def confidence_label(score):
    """Confirmed, High, or Possible. Below 0.50 is not shown to a buyer."""
    if score is None or score == "":
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value >= 0.90:
        return "Confirmed"
    if value >= 0.70:
        return "High"
    if value >= 0.50:
        return "Possible"
    return None


def accept_fitment(score):
    return confidence_label(score) is not None


def visible_to_buyers(row):
    """Seed rows have no score and stay visible. A scored row under 0.50 does not."""
    score = (row or {}).get("confidence")
    if score is None or score == "":
        return True
    return accept_fitment(score)


def expand_years(start, end, cap=YEAR_CAP):
    try:
        first = int(start)
        last = int(end if end is not None else start)
    except (TypeError, ValueError):
        return []
    if last < first:
        first, last = last, first
    if first < 1950 or last > 2039:
        return []
    return list(range(first, min(last, first + cap - 1) + 1))


def fetch_decision(status, attempt):
    """What to do with an HTTP status. The second 429 stops the host."""
    if status == 429 and attempt == 0:
        return "retry"
    if status in (401, 403, 429):
        return "stop"
    if status != 200:
        return "skip"
    return "ok"


def request_decision(attempt):
    return "retry" if attempt == 0 else "skip"


def retry_wait(header, default=2, cap=60):
    try:
        seconds = int(float(header))
    except (TypeError, ValueError):
        seconds = default
    return max(1, min(seconds, cap))


def normalize_reader_parts(payload, vehicle, limit=20):
    """Turn the reader's JSON into part rows. Drop the source car and weak fits."""
    from .catalog_parse import guess_category

    source_key = (
        int(vehicle.get("year") or 0),
        (vehicle.get("make") or "").casefold(),
        (vehicle.get("model") or "").casefold(),
    )
    parts = []
    for raw in (payload or {}).get("parts") or []:
        name = (raw.get("part_name") or "").strip()
        if not name:
            continue
        numbers = [
            str(number).strip()
            for number in (raw.get("oem_numbers") or [])
            if str(number).strip()
        ]
        if not numbers and raw.get("oem_part_number"):
            numbers = [str(raw["oem_part_number"]).strip()]
        also = []
        for fit in raw.get("also_fits") or []:
            if len(also) >= limit:
                break
            score = fit.get("confidence", 1.0)
            if not accept_fitment(score):
                continue
            make = (fit.get("make") or "").strip()
            model = (fit.get("model") or "").strip()
            if not make or not model:
                continue
            start = fit.get("year_start", fit.get("year"))
            end = fit.get("year_end", start)
            for year in expand_years(start, end):
                if (year, make.casefold(), model.casefold()) == source_key:
                    continue
                also.append({
                    "year": year,
                    "make": make[:40],
                    "model": model[:80],
                    "qualifier": fit.get("qualifier") or None,
                    "confidence": float(score),
                    "match_method": "reader_inferred",
                })
                if len(also) >= limit:
                    break
        category = (raw.get("category") or "").strip() or guess_category(name)
        parts.append({
            "part_name": name[:200],
            "category": category,
            "oem_part_number": numbers[0] if numbers else None,
            "brand": (raw.get("brand") or "").strip() or None,
            "also_fits": also,
            "match_method": "exact_oem" if numbers else "source_page",
        })
        if len(parts) >= 100:
            break
    return parts


def mark_collected_parts(parts):
    """A parsed page is an explicit fit. Confidence is 1.0 until a reader says otherwise."""
    marked = []
    for part in parts or []:
        oem = (part.get("oem_part_number") or "").strip()
        also = []
        for fit in part.get("also_fits") or []:
            score = fit.get("confidence", 1.0)
            if not accept_fitment(score):
                continue
            also.append({
                **fit,
                "confidence": float(score),
                "qualifier": fit.get("qualifier"),
                "match_method": fit.get("match_method") or "reader_inferred",
            })
        marked.append({
            **part,
            "match_method": "exact_oem" if oem else "source_page",
            "also_fits": also,
        })
    return marked


def keep_new_matches(payload, known):
    """Keep matcher suggestions at 0.50 or above that are not already known fits."""
    known_keys = set()
    for row in known or []:
        try:
            known_keys.add((
                int(row.get("year")),
                (row.get("make") or "").casefold(),
                (row.get("model") or "").casefold(),
            ))
        except (TypeError, ValueError):
            continue
    kept = []
    for match in (payload or {}).get("potential_matches") or []:
        if not accept_fitment(match.get("confidence")):
            continue
        make = (match.get("make") or "").strip()
        model = (match.get("model") or "").strip()
        if not make or not model:
            continue
        years = expand_years(match.get("year_start"), match.get("year_end", match.get("year_start")))
        fresh = [
            year for year in years
            if (year, make.casefold(), model.casefold()) not in known_keys
        ]
        if not fresh:
            continue
        kept.append({
            "year_start": fresh[0],
            "year_end": fresh[-1],
            "years": fresh,
            "make": make,
            "model": model,
            "confidence": float(match["confidence"]),
            "qualifier": match.get("qualifier") or None,
            "reasoning": (match.get("reasoning") or "").strip(),
            "match_method": "matcher_cross_ref",
        })
    return kept


def compact_catalog_text(parts, url):
    """A short excerpt for the reader. Full listing HTML is too large to send."""
    lines = [f"Source: {url}"]
    for part in parts or []:
        bits = [part.get("part_name") or "", part.get("category") or ""]
        if part.get("brand"):
            bits.append(f"brand {part['brand']}")
        if part.get("oem_part_number"):
            bits.append(f"OEM {part['oem_part_number']}")
        lines.append(" | ".join(bit for bit in bits if bit))
        for fit in part.get("also_fits") or []:
            lines.append(f"Also fits: {fit.get('year')} {fit.get('make')} {fit.get('model')}")
    return "\n".join(lines)[:8000]


class SourceReport:
    """One line per source, in the same shape as a Nu World supplier check."""

    def __init__(self, source):
        self.source = source
        self.usable = 0
        self.rejected = 0
        self.parts_new = 0
        self.parts_existing = 0
        self.fitment_new = 0
        self.fitment_updated = 0

    def add_save(self, saved):
        if saved.get("part_created"):
            self.parts_new += 1
        else:
            self.parts_existing += 1
        self.fitment_new += saved.get("fitment_created", 0)
        self.fitment_updated += saved.get("fitment_updated", 0)

    def line(self):
        return (
            f"{self.source}: {self.usable} usable records; {self.rejected} rejected. "
            f"new parts {self.parts_new}, existing parts {self.parts_existing}, "
            f"new fitment {self.fitment_new}, updated fitment {self.fitment_updated}"
        )
