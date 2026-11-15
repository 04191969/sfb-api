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


def _phrase_text(text):
    """Keep a fraction with its inch mark, as in 3/8-inch."""
    def replace(match):
        return f" {match.group(1)}/{match.group(2)}-inch "

    return re.sub(
        r"(\d+)\s*/\s*(\d+)\s*(?:\"|″|''|inches?\b)?",
        replace,
        str(text or ""),
        flags=re.IGNORECASE,
    )


def phrase_tokens(text):
    return re.findall(r"\d+/\d+-inch|[a-z0-9]+", _phrase_text(text).casefold())


def _head(requested):
    words = [word for word in tokens(requested) if len(word) > 2]
    return words[-1] if words else ""


def _listing_text(listing):
    return " ".join(
        str(listing.get(field) or "")
        for field in ("title", "offered_name", "part_name", "snippet")
    )


REQUEST_FIELDS = (
    ("variant", "variant"),
    ("brake_configuration", "brake configuration"),
    ("axle", "axle"),
    ("side", "side"),
)


UNKNOWN_VALUES = frozenset({"unknown", "i don't know", "i dont know"})


def is_caliper_request(part_name):
    """Axle, side, variant, and brake configuration apply to a caliper, not every part."""
    return "caliper" in set(tokens(part_name))


def unresolved_request_fields(request):
    """Omitted fields, then an explicit I-don't-know. A year and model do not supply them."""
    request = request or {}
    omitted = []
    unknown = []
    for key, label in REQUEST_FIELDS:
        value = str(request.get(key) or "").strip()
        if not value:
            omitted.append(label)
        elif value.casefold() in UNKNOWN_VALUES:
            unknown.append(label)
    return omitted, unknown


def missing_request_fields(request):
    omitted, _unknown = unresolved_request_fields(request)
    return omitted


def request_is_specific(request):
    if not is_caliper_request((request or {}).get("part_name")):
        return True
    omitted, unknown = unresolved_request_fields(request)
    return not omitted and not unknown


def _shown_value(value):
    text = str(value or "").strip()
    if not text or text.casefold() in UNKNOWN_VALUES:
        return ""
    return text


def _and_list(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _repair_kit_word(words, request_words):
    """Plural repair kits are the same conflict as a repair kit."""
    if "repair" not in words:
        return ""
    if "kits" in words and "kits" not in request_words:
        return "kits"
    if "kit" in words and "kit" not in request_words:
        return "kit"
    return ""


def _conflict_word(requested, text):
    """The last clear conflicting noun. A bare kit is not clear."""
    words = tokens(text)
    request_words = set(tokens(requested))
    repair_kit = _repair_kit_word(words, request_words)
    if repair_kit:
        return repair_kit
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
    words = phrase_tokens(text)
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
            _shown_value(vehicle.get("variant")),
            _shown_value(vehicle.get("brake_configuration")),
            _shown_value(vehicle.get("axle")),
            _shown_value(vehicle.get("side")),
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
        reason = (
            f"Held for clarification: the listing offers a {requested}, "
            f"and fitment{where} is not established."
        )
        missing, unknown = unresolved_request_fields(vehicle)
        if is_caliper_request(requested) and missing:
            reason += f" The request does not specify {_and_list(missing)}."
        if is_caliper_request(requested) and unknown:
            joined = _and_list(unknown)
            verb = "remains" if len(unknown) == 1 else "remain"
            reason += f" {joined[:1].upper()}{joined[1:]} {verb} unresolved, so fitment cannot be established."
        return _hold(reason, offered=requested)
    return _hold(
        "Held for clarification: the listing text does not establish what is offered.",
        ambiguous=True,
    )
