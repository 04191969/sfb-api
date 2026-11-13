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


@api_view(['GET'])
def match_detail(request, match_id):
    """Get a single match with full AI analysis."""
    sb = get_supabase()
    result = sb.table('matches').select(
        '*, buyer_posts(*), seller_listings(*), sections(name)'
    ).eq('id', match_id).single().execute()

    return Response(result.data)


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

    sb = get_supabase()

    # Map action to match status
    status_map = {
        'approved': 'approved',
        'rejected': 'rejected',
        'flagged': 'flagged',
    }

    # Update match status
    sb.table('matches').update({
        'status': status_map[action]
    }).eq('id', match_id).execute()

    # Log the action
    sb.table('match_actions').insert({
        'match_id': match_id,
        'action': action,
        'notes': notes,
        'acted_at': datetime.now(timezone.utc).isoformat(),
    }).execute()

    return Response({"success": True, "match_id": match_id, "action": action})


# ── Sections ──

@api_view(['GET'])
def section_list(request):
    """List all sections with match counts."""
    sb = get_supabase()
    result = sb.table('sections').select('*').order('name').execute()
    return Response({"sections": result.data})


# ── Listings ──

@api_view(['GET'])
def buyer_list(request):
    """List buyer posts with optional make/model filter."""
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


@api_view(['GET'])
def seller_list(request):
    """List seller listings with optional make/model filter."""
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


# ── Seeker ──

# In-memory tracker for demo (in production, use a database or Redis)
_seeker_state = {
    "last_run": None,
    "posts_found": 0,
    "status": "idle",
    "next_scheduled": None,
}


@api_view(['GET'])
def seeker_status(request):
    """Return the current Seeker pipeline status."""
    return Response(_seeker_state)


@api_view(['POST'])
def seeker_run(request):
    """Trigger a Seeker scrape run.

    For the demo, this is a placeholder that shows the flow works.
    In production, this kicks off the real Seeker pipeline.
    """
    _seeker_state["status"] = "running"
    _seeker_state["last_run"] = datetime.now(timezone.utc).isoformat()

    # TODO: In production, trigger the real Seeker pipeline here:
    # - Scrape configured sources (eBay, forums, Facebook, Craigslist)
    # - Extract structured data (make, model, part, year, price)
    # - Store in Supabase buyer_posts and seller_listings
    # - Trigger matcher on new posts

    _seeker_state["status"] = "completed"
    _seeker_state["posts_found"] = 0  # Will be real count in production

    return Response({
        "success": True,
        "message": "Seeker run triggered",
        "status": _seeker_state
    })


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

    return Response({"analysis": result})
