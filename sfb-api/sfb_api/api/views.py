from datetime import datetime, timezone

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .matcher import analyze_match
from .seeker_markets import (
    DISCOVERY_UNAVAILABLE,
    assemble_markets,
    classify_heartbeat,
    is_market_discovery,
    market_detail,
    market_scope,
    present_run,
)
from .supabase_client import get_supabase


def _server_error(exc):
    return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── Health check ──

@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    return Response({"status": "ok", "service": "SFB API", "version": "1.0.0"})


# ── Matches ──

@api_view(["GET"])
def match_list(request):
    """List matches with optional filters: status, section_id, min_confidence."""
    try:
        sb = get_supabase()
        query = sb.table("matches").select(
            "*, buyer_posts(username, post_text, source_platform, source_url), "
            "seller_listings(username, listing_text, source_platform, source_url, price), "
            "sections(name)"
        ).order("created_at", desc=True)

        match_status = request.query_params.get("status")
        if match_status:
            query = query.eq("status", match_status)

        section_id = request.query_params.get("section_id")
        if section_id:
            query = query.eq("section_id", section_id)

        min_confidence = request.query_params.get("min_confidence")
        if min_confidence:
            query = query.gte("confidence", int(min_confidence))

        limit = min(int(request.query_params.get("limit", 1000)), 1000)
        offset = int(request.query_params.get("offset", 0))
        query = query.range(offset, offset + limit - 1)

        result = query.execute()
        return Response({"matches": result.data, "count": len(result.data)})

    except Exception as e:
        return _server_error(e)


@api_view(["GET"])
def match_detail(request, match_id):
    """Get a single match with full AI analysis."""
    try:
        sb = get_supabase()
        result = sb.table("matches").select(
            "*, buyer_posts(*), seller_listings(*), sections(name)"
        ).eq("id", match_id).execute()

        if not result.data:
            return Response(
                {"error": "Match not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(result.data[0])

    except Exception as e:
        return _server_error(e)


@api_view(["POST"])
def match_action(request, match_id):
    """Update a match status: approved, rejected, or flagged."""
    action = request.data.get("action")

    if action not in ("approved", "rejected", "flagged"):
        return Response(
            {"error": "action must be: approved, rejected, or flagged"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        sb = get_supabase()
        sb.table("matches").update({
            "status": action,
        }).eq("id", match_id).execute()

        return Response({"success": True, "match_id": match_id, "action": action})

    except Exception as e:
        return _server_error(e)


def _table_rows(sb, table, preferred, fallback):
    try:
        return sb.table(table).select(preferred).execute().data or []
    except Exception:
        return sb.table(table).select(fallback).execute().data or []


def _seeker_rows(sb):
    sections = sb.table("sections").select("*").execute().data or []
    matches = sb.table("matches").select(
        "id, section_id, buyer_post_id, seller_listing_id, match_decision, "
        "status, ai_reasoning, created_at"
    ).execute().data or []
    buyers = _table_rows(
        sb, "buyer_posts",
        "id, post_text, source_platform, source_url, section_id",
        "id, post_text, source_platform, source_url",
    )
    sellers = _table_rows(
        sb, "seller_listings",
        "id, listing_text, price, source_platform, source_url, availability, section_id",
        "id, listing_text, price, source_platform, source_url",
    )
    runs = _table_rows(
        sb, "seeker_runs",
        "id, search_query, status, result_summary, requested_at, completed_at, job_kind, market_id",
        "id, search_query, status, result_summary, requested_at, completed_at",
    )
    return sections, matches, buyers, sellers, runs


def _discovery_ready(sb):
    try:
        sb.table("seeker_runs").select("job_kind,market_id").limit(1).execute()
        sb.table("seeker_worker_heartbeat").select("worker_id,seen_at").limit(1).execute()
        sb.table("seeker_ai_usage").select("usage_date,reserved,succeeded,failed").limit(1).execute()
        sb.table("buyer_posts").select("section_id").limit(1).execute()
        sb.table("seller_listings").select("section_id,availability").limit(1).execute()
    except Exception:
        return False
    return True


def _worker_report(sb):
    try:
        rows = sb.table("seeker_worker_heartbeat").select("worker_id,seen_at").execute().data or []
    except Exception:
        return classify_heartbeat([])
    return classify_heartbeat(rows)


# ── Sections ──

@api_view(["GET"])
def section_list(request):
    """List all sections with match counts."""
    try:
        sb = get_supabase()
        result = sb.table("sections").select("*").order("name").execute()
        return Response({"sections": result.data})

    except Exception as e:
        return _server_error(e)


@api_view(["GET"])
def seeker_markets(request):
    """Markets and counts from linked records."""
    try:
        sb = get_supabase()
        payload = assemble_markets(
            *_seeker_rows(sb),
            worker=_worker_report(sb),
            discovery_ready=_discovery_ready(sb),
        )
    except Exception as e:
        return _server_error(e)
    return Response(payload)


@api_view(["GET"])
def seeker_market_detail(request, market_id):
    """One market, addressed by its id, an older section id, or its slug."""
    try:
        sb = get_supabase()
        payload = market_detail(
            market_id, *_seeker_rows(sb),
            worker=_worker_report(sb),
            discovery_ready=_discovery_ready(sb),
        )
    except Exception as e:
        return _server_error(e)
    if payload is None:
        return Response({"error": "Market not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response(payload)


@api_view(["POST"])
def seeker_market_discover(request, market_id):
    """Queue one manual market discovery. A market name is not a buyer request."""
    try:
        sb = get_supabase()
        ready = _discovery_ready(sb)
        worker = _worker_report(sb)
        if not ready or worker.get("status") != "online":
            return Response(
                {
                    "discovery_controls": {"available": False, "message": DISCOVERY_UNAVAILABLE},
                    "worker": worker,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        detail = market_detail(
            market_id, *_seeker_rows(sb), worker=worker, discovery_ready=ready,
        )
    except Exception as e:
        return _server_error(e)
    if detail is None:
        return Response({"error": "Market not found"}, status=status.HTTP_404_NOT_FOUND)
    market = detail["market"]
    try:
        active = (
            sb.table("seeker_runs")
            .select("id,status,job_kind,market_id,result_summary,requested_at")
            .eq("market_id", market["id"])
            .eq("job_kind", "market_discovery")
            .in_("status", ["pending", "running"])
            .limit(1)
            .execute()
        )
    except Exception as e:
        return _server_error(e)
    if active.data:
        return Response(present_run(active.data[0]), status=status.HTTP_200_OK)
    row = {
        "search_query": market_scope(market["name"], market["id"]),
        "status": "pending",
        "job_kind": "market_discovery",
        "market_id": market["id"],
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        created = sb.table("seeker_runs").insert(row).execute()
    except Exception as e:
        return _server_error(e)
    saved = created.data[0] if created.data else row
    if not is_market_discovery(saved):
        return _server_error(RuntimeError("Market discovery was not stored as its own job."))
    return Response(present_run(saved), status=status.HTTP_201_CREATED)


# ── Listings ──

@api_view(["GET"])
def buyer_list(request):
    """List buyer posts with optional make/model filter."""
    try:
        sb = get_supabase()
        query = sb.table("buyer_posts").select("*").order("scraped_at", desc=True)

        make = request.query_params.get("make")
        if make:
            query = query.ilike("make", f"%{make}%")

        model = request.query_params.get("model")
        if model:
            query = query.ilike("model", f"%{model}%")

        limit = int(request.query_params.get("limit", 50))
        query = query.limit(limit)

        result = query.execute()
        return Response({"buyers": result.data, "count": len(result.data)})

    except Exception as e:
        return _server_error(e)


@api_view(["GET"])
def seller_list(request):
    """List seller listings with optional make/model filter."""
    try:
        sb = get_supabase()
        query = sb.table("seller_listings").select("*").order("scraped_at", desc=True)

        make = request.query_params.get("make")
        if make:
            query = query.ilike("make", f"%{make}%")

        model = request.query_params.get("model")
        if model:
            query = query.ilike("model", f"%{model}%")

        limit = int(request.query_params.get("limit", 50))
        query = query.limit(limit)

        result = query.execute()
        return Response({"sellers": result.data, "count": len(result.data)})

    except Exception as e:
        return _server_error(e)


# ── Seeker ──

@api_view(["GET"])
def seeker_status(request):
    """Return the newest seeker_runs row. Idle when the table is empty."""
    try:
        sb = get_supabase()
        result = (
            sb.table("seeker_runs")
            .select("*")
            .order("requested_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return _server_error(e)

    if not result.data:
        return Response({"status": "idle"})
    return Response(present_run(result.data[0]))


@api_view(["POST"])
def seeker_run(request):
    """Insert a pending seeker_runs row for the Windows poller to pick up."""
    search_query = request.data.get("search_query")
    if isinstance(search_query, str):
        search_query = search_query.strip() or None
    else:
        search_query = None

    user_id = request.data.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        user_id = None

    row = {
        "search_query": search_query,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    if user_id:
        row["user_id"] = user_id.strip()

    try:
        sb = get_supabase()
        try:
            result = sb.table("seeker_runs").insert(row).execute()
        except Exception:
            if "user_id" not in row:
                raise
            row.pop("user_id")
            result = sb.table("seeker_runs").insert(row).execute()
    except Exception as e:
        return _server_error(e)

    created = result.data[0] if result.data else row
    return Response(created, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def seeker_run_detail(request, run_id):
    """Return one seeker_runs row so a customer site can poll its own search."""
    try:
        sb = get_supabase()
        result = sb.table("seeker_runs").select("*").eq("id", run_id).limit(1).execute()
    except Exception as e:
        return _server_error(e)

    if not result.data:
        return Response({"error": "Run not found"}, status=status.HTTP_404_NOT_FOUND)
    return Response(present_run(result.data[0]))


# ── Live Matcher ──

@api_view(["POST"])
def match_analyze(request):
    """Send a buyer+seller pair to OpenAI for live analysis.

    Body: {"buyer_text": "...", "seller_text": "...", "store": false}
    Returns the analysis even when an optional store write fails.
    """
    buyer_text = request.data.get("buyer_text", "")
    seller_text = request.data.get("seller_text", "")

    if not buyer_text or not seller_text:
        return Response(
            {"error": "buyer_text and seller_text are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = analyze_match(buyer_text, seller_text)
    except Exception as e:
        return Response(
            {"error": f"Matcher failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    store = request.data.get("store", False)
    body = {"analysis": result}
    if not store:
        return Response(body)

    if not result.get("match"):
        body["stored"] = False
        return Response(body)

    try:
        sb = get_supabase()

        buyer = sb.table("buyer_posts").insert({
            "post_text": buyer_text,
            "make": result.get("make", ""),
            "model": result.get("model", ""),
            "part": result.get("part", ""),
        }).execute()

        seller = sb.table("seller_listings").insert({
            "listing_text": seller_text,
            "make": result.get("make", ""),
            "model": result.get("model", ""),
            "part": result.get("part", ""),
        }).execute()

        sb.table("matches").insert({
            "buyer_post_id": buyer.data[0]["id"],
            "seller_listing_id": seller.data[0]["id"],
            "ai_reasoning": result.get("reasoning", ""),
            "confidence": result.get("confidence", 0),
            "match_decision": "send" if result.get("match") else "no_send",
            "status": "pending_review",
            "make": result.get("make", ""),
            "model": result.get("model", ""),
            "part": result.get("part", ""),
        }).execute()
        body["stored"] = True
    except Exception as e:
        body["stored"] = False
        body["storage_error"] = str(e)

    return Response(body)
