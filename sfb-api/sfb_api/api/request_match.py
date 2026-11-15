"""Decide whether a listing offers the part the buyer asked for.

A bolt, piston, tool, or repair kit is a different product. The word kit
alone is not: it may be a complete assembly or only the related pieces.
Missing or ambiguous text is held. A listing that does offer the requested
part still waits when fitment is not established. A vehicle named in the
listing is a claim, not that establishment.
"""

import re

# A listing that sells one of these is not selling the larger part named beside it.
CONFLICT_WORDS = frozenset({
    "adapter", "adapters", "bleeder", "bleeders", "bolt", "bolts", "boot", "boots",
    "bracket", "brackets", "clip", "clips", "gasket", "gaskets", "guide", "guides",
    "hardware", "hose", "hoses", "line", "lines", "nut", "nuts",
    "pad", "pads", "pin", "pins", "piston", "pistons", "plug", "plugs", "rotor",
    "rotors", "screw", "screws", "seal", "seals", "sensor", "sensors", "shim",
    "shims", "spring", "springs", "stud", "studs", "tool", "tools", "washer",
    "washers",
})


def tokens(text):
    return re.findall(r"[a-z0-9]+", str(text or "").casefold())


def _head(requested):
    words = [word for word in tokens(requested) if len(word) > 2]
    return words[-1] if words else ""


def _listing_text(listing):
    return " ".join(
        str(listing.get(field) or "")
        for field in ("title", "offered_name", "part_name", "snippet")
    )


def _conflict_word(requested, text):
    """The last clear conflicting noun. A bare kit is not clear."""
    words = tokens(text)
    request_words = set(tokens(requested))
    if "repair" in words and "kit" in words and "kit" not in request_words:
        return "kit"
    found = ""
    for word in words:
        if word in CONFLICT_WORDS and word not in request_words:
            found = word
    return found


def _kit_is_ambiguous(requested, text):
    words = tokens(text)
    request_words = set(tokens(requested))
    return "kit" in words and "kit" not in request_words and "repair" not in words


def _offered_phrase(text, conflict):
    words = tokens(text)
    if conflict not in words:
        return conflict
    index = words.index(conflict)
    start = max(0, index - 2)
    return " ".join(words[start:index + 1])


def _vehicle_label(vehicle):
    vehicle = vehicle or {}
    return " ".join(
        piece for piece in (
            str(vehicle.get("year") or "").strip(),
            str(vehicle.get("make") or "").strip(),
            str(vehicle.get("model") or "").strip(),
        ) if piece
    )


def _hold(reason, offered="", ambiguous=False):
    return {
        "status": "uncertain",
        "offered": offered,
        "fitment": "uncertain",
        "ambiguous": ambiguous,
        "reason": reason,
    }


def classify_listing(requested, listing, vehicle=None, fitment_established=False, fitment_by=""):
    """Return conflict, uncertain, or qualifying for this buyer request."""
    requested = " ".join(str(requested or "").split())
    if not requested:
        return _hold("Held for clarification: the requested part is not stated.", ambiguous=True)
    text = _listing_text(listing)
    conflict = _conflict_word(requested, text)
    if conflict:
        offered = _offered_phrase(text, conflict)
        return {
            "status": "conflict",
            "offered": offered,
            "fitment": "not_applicable",
            "ambiguous": False,
            "reason": f"This listing offers a {offered}, not the requested {requested}.",
        }
    if _kit_is_ambiguous(requested, text):
        return _hold(
            "Held for clarification: the listing text does not establish whether this is "
            f"a complete {requested} or a kit of related parts.",
            ambiguous=True,
        )
    head = _head(requested)
    if head and head in set(tokens(text)):
        if fitment_established:
            return {
                "status": "qualifying",
                "offered": requested,
                "fitment": fitment_by or "established",
                "ambiguous": False,
                "reason": "",
            }
        car = _vehicle_label(vehicle)
        where = f" to the {car}" if car else ""
        return _hold(
            f"Held for clarification: the listing offers a {requested}, "
            f"and fitment{where} is not established.",
            offered=requested,
        )
    return _hold(
        "Held for clarification: the listing text does not establish what is offered.",
        ambiguous=True,
    )
