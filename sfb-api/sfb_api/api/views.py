import uuid
from datetime import datetime, timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status

from .supabase_client import get_supabase
from .matcher import analyze_match


# ── Health check ──

@api_view(['GET'])
def health_check(request):
    return Response({"status": "ok", "service": "SFB API", "version": "1.0.0"})


# ── Matches ──

@api_view(['GET'])
def match_list(request):
    """List matches with optional filters: status, section_id, min_confidence."""
    try:
        sb = get_supabase()
        query = sb.table('matches').select(
            '*, buyer_posts(username, post_text, source_platform), '
            'seller_listings(username, listing_text, source_platform), '
            'sections(name)'
        ).order('created_at', desc=True)

        # Filters
        match_status = request.query_params.get('status')
        if match_status:
            query = query.eq('status', match_status)

        section_id = request.query_params.get('section_id')
        if section_id:
            query = query.eq('section_id', section_id)

        min_confidence = request.query_params.get('min_confidence')
        if min_confidence:
            query = query.gte('confidence', int(min_confidence))

        # Pagination
        limit = int(request.query_params.get('limit', 50))
        offset = int(request.query_params.get('offset', 0))
        query = query.range(offset, offset + limit - 1)

        result = query.execute()
        return Response({"matches": result.data, "count": len(result.data)})

    except Exception as e:
        # Return empty list rather than 500 — Lovable falls back to demo data
        return Response({"matches": [], "count": 0, "error": str(e)})


@api_view(['GET'])
def match_detail(request, match_id):
    """Get a single match with full AI analysis."""
    try:
        sb = get_supabase()
        result = sb.table('matches').select(
            '*, buyer_posts(*), seller_listings(*), sections(name)'
        ).eq('id', match_id).execute()

        if not result.data:
            return Response(
                {"error": "Match not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(result.data[0])

    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def match_action(request, match_id):
    """Update a match status: approved, rejected, or flagged."""
    action = request.data.get('action')
    notes = request.data.get('notes', '')

    if action not in ('approved', 'rejected', 'flagged'):
        return Response(
            {"error": "action must be: approved, rejected, or flagged"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        sb = get_supabase()

        # Update match status
        sb.table('matches').update({
            'status': action
        }).eq('id', match_id).execute()

        # Log the action
        sb.table('match_actions').insert({
            'match_id': match_id,
            'action': action,
            'notes': notes,
            'acted_at': datetime.now(timezone.utc).isoformat(),
        }).execute()

        return Response({"success": True, "match_id": match_id, "action": action})

    except Exception as e:
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ── Sections ──

@api_view(['GET'])
def section_list(request):
    """List all sections with match counts."""
    try:
        sb = get_supabase()
        result = sb.table('sections').select('*').order('name').execute()
        return Response({"sections": result.data})

    except Exception as e:
        return Response({"sections": [], "error": str(e)})


# ── Listings ──

@api_view(['GET'])
def buyer_list(request):
    """List buyer posts with optional make/model filter."""
    try:
        sb = get_supabase()
        query = sb.table('buyer_posts').select('*').order('scraped_at', desc=True)

        make = request.query_params.get('make')
        if make:
            query = query.ilike('make', f'%{make}%')

        model = request.query_params.get('model')
        if model:
            query = query.ilike('model', f'%{model}%')

        limit = int(request.query_params.get('limit', 50))
        query = query.limit(limit)

        result = query.execute()
        return Response({"buyers": result.data, "count": len(result.data)})

    except Exception as e:
        return Response({"buyers": [], "count": 0, "error": str(e)})


@api_view(['GET'])
def seller_list(request):
    """List seller listings with optional make/model filter."""
    try:
        sb = get_supabase()
        query = sb.table('seller_listings').select('*').order('scraped_at', desc=True)

        make = request.query_params.get('make')
        if make:
            query = query.ilike('make', f'%{make}%')

        model = request.query_params.get('model')
        if model:
            query = query.ilike('model', f'%{model}%')

        limit = int(request.query_params.get('limit', 50))
        query = query.limit(limit)

        result = query.execute()
        return Response({"sellers": result.data, "count": len(result.data)})

    except Exception as e:
        return Response({"sellers": [], "count": 0, "error": str(e)})


# ── Seeker ──

@api_view(['GET'])
def seeker_status(request):
    """Return the current Seeker pipeline status from Supabase."""
    try:
        sb = get_supabase()
        result = sb.table('seeker_runs').select('*').order(
            'requested_at', desc=True
        ).limit(1).execute()
        if result.data:
            latest = result.data[0]
            return Response({
                "status": latest["status"],
                "last_run": latest.get("completed_at") or latest.get("started_at") or latest.get("requested_at"),
                "run_id": latest["id"],
                "search_query": latest.get("search_query"),
                "result_summary": latest.get("result_summary"),
                "error_message": latest.get("error_message"),
            })
        else:
            return Response({"status": "idle", "last_run": None, "run_id": None})
    except Exception as e:
        return Response({"status": "idle", "last_run": None, "error": str(e)})


@api_view(['POST'])
def seeker_run(request):
    """Queue a Seeker run in Supabase for the PC polling script to pick up."""
    search_query = request.data.get('search_query', '')
    try:
        sb = get_supabase()
        # Check for already-active runs
        active = sb.table('seeker_runs').select('id, status').in_(
            'status', ['pending', 'running']
        ).execute()
        if active.data:
            return Response({
                "success": False,
                "message": "A Seeker run is already in progress",
                "run_id": active.data[0]["id"],
                "status": active.data[0]["status"],
            })
        # Insert new pending run
        result = sb.table('seeker_runs').insert({
            "status": "pending",
            "search_query": search_query,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        run_id = result.data[0]["id"]
        return Response({
            "success": True,
            "message": "Seeker run queued — your PC will pick it up shortly",
            "run_id": run_id,
            "status": "pending",
        })
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── Live Matcher ──

@api_view(['POST'])
def match_analyze(request):
    """Send a buyer+seller pair to OpenAI for live analysis.

    Body: {"buyer_text": "...", "seller_text": "..."}
    Returns: AI analysis with match/no-match, confidence, reasoning
    """
    buyer_text = request.data.get('buyer_text', '')
    seller_text = request.data.get('seller_text', '')

    if not buyer_text or not seller_text:
        return Response(
            {"error": "buyer_text and seller_text are required"},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        result = analyze_match(buyer_text, seller_text)
    except Exception as e:
        return Response(
            {"error": f"Matcher failed: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Optionally store the match in Supabase
    store = request.data.get('store', False)
    if store and result.get('match'):
        try:
            sb = get_supabase()

            # Store buyer post
            buyer = sb.table('buyer_posts').insert({
                'post_text': buyer_text,
                'make': result.get('make', ''),
                'model': result.get('model', ''),
                'part': result.get('part', ''),
                'year_range': result.get('buyer_years', ''),
            }).execute()

            # Store seller listing
            seller = sb.table('seller_listings').insert({
                'listing_text': seller_text,
                'make': result.get('make', ''),
                'model': result.get('model', ''),
                'part': result.get('part', ''),
                'year': result.get('seller_years', ''),
            }).execute()

            # Store match
            sb.table('matches').insert({
                'buyer_post_id': buyer.data[0]['id'],
                'seller_listing_id': seller.data[0]['id'],
                'ai_reasoning': result.get('reasoning', ''),
                'confidence': result.get('confidence', 0),
                'match_decision': 'send' if result.get('match') else 'no_send',
                'status': 'pending_review',
                'make': result.get('make', ''),
                'model': result.get('model', ''),
                'part': result.get('part', ''),
                'buyer_years': result.get('buyer_years', ''),
                'seller_years': result.get('seller_years', ''),
            }).execute()
        except Exception:
            pass  # Don't fail the analysis response if storage fails

    return Response({"analysis": result})
