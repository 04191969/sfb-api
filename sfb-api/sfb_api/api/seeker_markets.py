"""Seeker markets assembled from saved records.

Counts come from the linked rows. A missing fact stays "Not available";
a verified zero is 0.
"""

from datetime import datetime, timedelta, timezone

NOT_AVAILABLE = "Not available"
WORKER_UNKNOWN = "Worker status unknown"
DISCOVERY_UNAVAILABLE = "Market discovery not connected yet"
WORKER_REVISION = "reservation-v2"

_BLANK = {"none", "n/a", "na", "null", "not specified", "not stated"}
_INSTRUMENT_MAKES = {
    "epiphone", "fender", "gibson", "greco", "squier", "korg", "yamaha",
    "ludwig", "tama", "premier", "kay", "alesis", "nord", "eminence",
}
_INSTRUMENT_WORDS = ("guitar", "bass", "piano", "drum", "synth", "stratocaster")
_AUTO_MAKES = {"ford", "porsche"}
_COLLECTIBLE_WORDS = ("card", "cards", "comic", "baseball", "pokemon", "token", "rookie")


def _clean(value):
    text = " ".join(str(value or "").split())
    if text.casefold() in _BLANK:
        return ""
    return text


def display_name(make, model, name=""):
    """Drop a literal None. Do not invent a model that was not stored."""
    brand = _clean(make)
    variant = _clean(model)
    if brand and variant:
        return f"{brand} {variant}"
    if brand:
        return brand
    if variant:
        return variant
    kept = [part for part in str(name or "").split() if part.casefold() not in _BLANK]
    return " ".join(kept)


def market_key(make, model, name=""):
    """Case, hyphens, and a trailing generic 'guitar' do not make a new market."""
    label = display_name(make, model, name).casefold().replace("-", "")
    words = label.split()
    if len(words) > 1 and words[-1] == "guitar":
        words = words[:-1]
    return " ".join(words)


def category_for(make, model, name=""):
    brand = _clean(make).casefold()
    text = display_name(make, model, name).casefold()
    if brand in _INSTRUMENT_MAKES or any(word in text.split() for word in _INSTRUMENT_WORDS):
        return "Musical Instruments"
    if brand in _AUTO_MAKES:
        return "Automotive"
    if any(word in text.split() for word in _COLLECTIBLE_WORDS):
        return "Collectibles"
    return "Uncategorized"


def _slug(text, suffix):
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in raw:
        raw = raw.replace("--", "-")
    if not raw:
        raw = "market"
    return f"{raw}-{suffix[:8]}"


def _parse_time(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _offer_state(listing_text):
    first = str(listing_text or "").split("\n", 1)[0].strip().casefold()
    if first.startswith("found for sale") or first.startswith("listed for sale"):
        return "available"
    if first.startswith("listing ended"):
        return "ended"
    if first.startswith("availability unconfirmed"):
        return "unconfirmed"
    return "unknown"


DEFAULT_DISCOVERY_MINUTES = 30
DEFAULT_RECHECK_HOURS = 6
# A published gpt-4o-mini call of this size is well under a cent. The figure is an estimate.
AI_CALL_ESTIMATE_USD = 0.001


def market_control(row=None):
    """A market with no control row is paused. Nothing starts automatically."""
    row = row or {}
    enabled = bool(row.get("enabled"))
    return {
        "enabled": enabled,
        "paused": not enabled,
        "discovery_interval_minutes": int(row.get("discovery_interval_minutes") or DEFAULT_DISCOVERY_MINUTES),
        "recheck_interval_hours": int(row.get("recheck_interval_hours") or DEFAULT_RECHECK_HOURS),
        "next_discovery_at": row.get("next_discovery_at"),
        "next_recheck_at": row.get("next_recheck_at"),
        "next_search": row.get("next_discovery_at") if enabled else "Paused",
    }


def run_counts_text(new_matches=0, still_valid=0, no_longer_qualifying=0, incomplete="", base=""):
    """Name the three results. A pure incomplete reason stays unchanged."""
    if not (new_matches or still_valid or no_longer_qualifying):
        return base or (f"Search incomplete — {incomplete}" if incomplete else "No qualifying match found in the sources checked")
    text = (
        f"New matches: {new_matches}. "
        f"Existing matches still valid: {still_valid}. "
        f"No longer qualifying: {no_longer_qualifying}."
    )
    if base:
        text = f"{text} {base}"
    if incomplete:
        text = f"{text} Search incomplete — {incomplete}"
    return text


def match_group(match):
    """Dashboard group. An unresolved cross-post is not an independent item."""
    if _is_cross_post((match or {}).get("ai_reasoning")):
        return "cross_post"
    decision = (match or {}).get("match_decision")
    status = str((match or {}).get("status") or "").casefold()
    reason = str((match or {}).get("ai_reasoning") or "").casefold()
    if decision == "no_send" or status in ("flagged", "rejected"):
        return "inactive"
    if "on hold" in reason or status == "held":
        return "held"
    if decision == "send":
        return "qualifying"
    return "held"


def usage_report(usage_row, reservations, estimate_usd=AI_CALL_ESTIMATE_USD):
    """Calls and an estimate. An uncertain call is neither a success nor a failure."""
    usage_row = usage_row or {}
    succeeded = int(usage_row.get("succeeded") or 0)
    failed = int(usage_row.get("failed") or 0)
    uncertain = sum(1 for row in reservations or [] if row.get("state") == "uncertain")
    open_calls = sum(1 for row in reservations or [] if row.get("state") == "reserved")
    return {
        "succeeded": succeeded,
        "failed": failed,
        "uncertain": uncertain,
        "open": open_calls,
        "estimated_cost_usd": round(succeeded * estimate_usd, 4),
        "cost_is_estimate": True,
    }


def crash_reservation_outcome():
    """A dead worker does not prove the model call failed, and it is not sent again."""
    return "uncertain"


def _is_cross_post(text):
    return "possible cross-post" in str(text or "").casefold()


def _buyer_origin(buyer):
    platform = str((buyer or {}).get("source_platform") or "").casefold()
    url = str((buyer or {}).get("source_url") or "").casefold()
    if platform == "sfb" or "searchfindbuy.local" in url:
        return "sfb_user"
    if buyer:
        return "public_wtb"
    return NOT_AVAILABLE


def assemble_markets(sections, matches, buyers, sellers, runs, now=None, worker=None, discovery_ready=False, controls=None):
    """One card per confirmed market. Ambiguous names stay in review."""
    now = now or datetime.now(timezone.utc)
    week_start = now - timedelta(days=7)
    buyers_by_id = {row.get("id"): row for row in buyers or []}
    sellers_by_id = {row.get("id"): row for row in sellers or []}
    groups = {}
    for section in sections or []:
        key = market_key(section.get("make"), section.get("model"), section.get("name"))
        # An empty name is not evidence that two records are the same market.
        bucket = key or f"id:{section.get('id')}"
        groups.setdefault(bucket, []).append(section)

    markets = []
    review = []
    for key, members in groups.items():
        labeled = []
        for member in members:
            label = display_name(member.get("make"), member.get("model"), member.get("name"))
            labeled.append((label, member))
        names = [label for label, _ in labeled if label]
        primary_label = min(names, key=lambda label: (len(label), label.casefold())) if names else NOT_AVAILABLE
        primary = min(
            (member for _, member in labeled),
            key=lambda member: _parse_time(member.get("created_at")) or now,
        )
        alias_ids = [member.get("id") for _, member in labeled if member.get("id")]
        markets.append({
            "key": key,
            "id": primary.get("id"),
            "slug": _slug(primary_label if primary_label != NOT_AVAILABLE else "market", primary.get("id") or "market"),
            "name": primary_label,
            "category": category_for(primary.get("make"), primary.get("model"), primary.get("name")),
            "alias_ids": alias_ids,
            "alias_names": [label for label, _ in labeled if label and label != primary_label],
        })
        if (
            key
            and not key.startswith("id:")
            and any(
                other != key
                and not str(other).startswith("id:")
                and (other.startswith(key + " ") or key.startswith(other + " "))
                for other in groups
            )
        ):
            review.append({
                "name": primary_label,
                "section_ids": alias_ids,
                "reason": "Similar name kept separate until the market is confirmed.",
            })

    markets.sort(key=lambda market: (market["category"], market["name"].casefold()))
    alias_to_market = {}
    for market in markets:
        for alias_id in market["alias_ids"]:
            alias_to_market[alias_id] = market

    offers_seen = {market["id"]: set() for market in markets}
    available = {market["id"]: set() for market in markets}
    unconfirmed = {market["id"]: set() for market in markets}
    qualifying = {market["id"]: set() for market in markets}
    for match in matches or []:
        market = alias_to_market.get(match.get("section_id"))
        if not market:
            continue
        seller = sellers_by_id.get(match.get("seller_listing_id")) or {}
        seller_id = seller.get("id")
        cross = _is_cross_post(match.get("ai_reasoning"))
        stored = str(seller.get("availability") or "").casefold()
        state = stored if stored in ("available", "ended", "unconfirmed") else _offer_state(seller.get("listing_text"))
        if seller_id and not cross:
            offers_seen[market["id"]].add(seller_id)
            if state == "available":
                available[market["id"]].add(seller_id)
            elif state == "unconfirmed":
                unconfirmed[market["id"]].add(seller_id)
        created = _parse_time(match.get("created_at"))
        if (
            not cross
            and match.get("match_decision") == "send"
            and created
            and created >= week_start
        ):
            qualifying[market["id"]].add(match.get("id"))

    def last_run(market):
        names = {market["name"].casefold()}
        names.update(alias.casefold() for alias in market["alias_names"])
        newest = None
        summary = ""
        for run in runs or []:
            query = str(run.get("search_query") or "").casefold()
            if not any(name and name in query for name in names):
                continue
            moment = _parse_time(run.get("completed_at")) or _parse_time(run.get("requested_at"))
            if moment and (newest is None or moment > newest):
                newest = moment
                summary = run.get("result_summary") or ""
        if newest is None:
            return NOT_AVAILABLE, NOT_AVAILABLE
        return newest.isoformat(), summary or NOT_AVAILABLE

    control_by_market = {
        row.get("market_id"): row for row in controls or [] if row.get("market_id")
    }
    cards = []
    for market in markets:
        run_at, summary = last_run(market)
        verified = len(available[market["id"]])
        control = market_control(control_by_market.get(market["id"]))
        cards.append({
            "id": market["id"],
            "slug": market["slug"],
            "name": market["name"],
            "category": market["category"],
            "alias_ids": market["alias_ids"],
            "alias_names": market["alias_names"],
            "open_buyer_requests": NOT_AVAILABLE,
            "verified_available_offers": verified,
            "unconfirmed_offers": len(unconfirmed[market["id"]]),
            "qualifying_matches_7d": len(qualifying[market["id"]]),
            "last_discovery_run": run_at,
            "last_result_summary": summary,
            "last_check": run_at,
            "next_search": control["next_search"],
            "automatic": control,
            "watching": NOT_AVAILABLE,
        })
    reported_worker = worker or {"status": "unknown", "message": WORKER_UNKNOWN, "revision": ""}
    connected = bool(
        discovery_ready
        and reported_worker.get("status") == "online"
        and reported_worker.get("revision") == WORKER_REVISION
    )
    return {
        "worker": reported_worker,
        "discovery_controls": {
            "available": connected,
            "message": "" if connected else DISCOVERY_UNAVAILABLE,
        },
        "markets": cards,
        "review": review,
    }


def market_detail(market_id, sections, matches, buyers, sellers, runs, now=None, worker=None, discovery_ready=False):
    assembled = assemble_markets(
        sections, matches, buyers, sellers, runs, now, worker, discovery_ready,
    )
    chosen = None
    for market in assembled["markets"]:
        if market_id in {market["id"], market["slug"], *market["alias_ids"]}:
            chosen = market
            break
    if not chosen:
        return None
    buyers_by_id = {row.get("id"): row for row in buyers or []}
    sellers_by_id = {row.get("id"): row for row in sellers or []}
    alias_ids = set(chosen["alias_ids"])
    request_rows = []
    offer_rows = []
    match_rows = []
    seen_buyers = set()
    seen_sellers = set()
    for match in matches or []:
        if match.get("section_id") not in alias_ids:
            continue
        buyer = buyers_by_id.get(match.get("buyer_post_id")) or {}
        seller = sellers_by_id.get(match.get("seller_listing_id")) or {}
        cross = _is_cross_post(match.get("ai_reasoning"))
        if buyer.get("id") and buyer["id"] not in seen_buyers:
            seen_buyers.add(buyer["id"])
            request_rows.append({
                "id": buyer.get("id"),
                "origin": _buyer_origin(buyer),
                "text": buyer.get("post_text") or "",
                "source_platform": buyer.get("source_platform") or NOT_AVAILABLE,
                "source_url": buyer.get("source_url") or NOT_AVAILABLE,
                "request_status": NOT_AVAILABLE,
            })
        if seller.get("id") and seller["id"] not in seen_sellers:
            seen_sellers.add(seller["id"])
            stored = str(seller.get("availability") or "").casefold()
            state = stored if stored in ("available", "ended", "unconfirmed") else _offer_state(seller.get("listing_text"))
            offer_rows.append({
                "id": seller.get("id"),
                "availability": state if state != "unknown" else NOT_AVAILABLE,
                "counts_as_available": state == "available" and not cross,
                "possible_cross_post": cross,
                "price": seller.get("price") if seller.get("price") is not None else NOT_AVAILABLE,
                "source_platform": seller.get("source_platform") or NOT_AVAILABLE,
                "source_url": seller.get("source_url") or NOT_AVAILABLE,
                "text": seller.get("listing_text") or "",
            })
        match_rows.append({
            "id": match.get("id"),
            "decision": match.get("match_decision") or NOT_AVAILABLE,
            "status": match.get("status") or NOT_AVAILABLE,
            "possible_cross_post": cross,
            "group": match_group(match),
            "counts_as_qualifying": (
                match.get("match_decision") == "send" and not cross
            ),
            "ai_reasoning": match.get("ai_reasoning") or "",
            "created_at": match.get("created_at"),
            "buyer_post_id": match.get("buyer_post_id"),
            "seller_listing_id": match.get("seller_listing_id"),
        })
    return {
        "market": chosen,
        "worker": assembled["worker"],
        "discovery_controls": assembled["discovery_controls"],
        "buyer_requests": request_rows,
        "seller_offers": offer_rows,
        "matches": match_rows,
    }


HEARTBEAT_FRESH = timedelta(seconds=45)


def classify_heartbeat(rows, now=None):
    """Online only from a fresh heartbeat. No row is unknown, not offline."""
    now = now or datetime.now(timezone.utc)
    newest = None
    newest_row = None
    for row in rows or []:
        seen = _parse_time(row.get("seen_at"))
        if seen and (newest is None or seen > newest):
            newest = seen
            newest_row = row
    revision = str((newest_row or {}).get("revision") or "")
    detail = str((newest_row or {}).get("detail") or "")
    code_version = str((newest_row or {}).get("code_version") or "")
    if not code_version and "code " in detail:
        code_version = detail.split("code ", 1)[1].split(";", 1)[0].strip()
    if newest is None:
        return {
            "status": "unknown",
            "message": WORKER_UNKNOWN,
            "seen_at": None,
            "revision": revision,
            "code_version": code_version,
        }
    age = now - newest
    if age <= HEARTBEAT_FRESH:
        status_name, message = "online", "Worker online"
    else:
        status_name, message = "offline", "Worker offline"
    return {
        "status": status_name,
        "message": message,
        "seen_at": newest.isoformat(),
        "revision": revision,
        "code_version": code_version,
    }


def market_scope(name, market_id):
    """Search scope for a market. This text is not a buyer request."""
    return (
        "Market discovery\n"
        f"Market: {name}\n"
        f"Market id: {market_id}\n"
        "This market name is search scope, not a buyer request."
    )


def is_market_discovery(run):
    if str((run or {}).get("job_kind") or "") == "market_discovery":
        return True
    return str((run or {}).get("search_query") or "").startswith("Market discovery\n")


def present_run(run):
    """Status the page can show without inventing a result."""
    row = dict(run or {})
    phase = row.get("status") or "pending"
    summary = row.get("result_summary") or ""
    if phase == "pending":
        label, animate = "Queued — waiting for worker", False
    elif phase == "running":
        label, animate = "Searching", True
    elif summary:
        label, animate = summary, False
    else:
        label, animate = NOT_AVAILABLE, False
    row["phase"] = phase
    row["label"] = label
    row["animate"] = animate
    return row
