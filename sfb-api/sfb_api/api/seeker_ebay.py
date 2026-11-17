"""Worker-only eBay relay. Credentials and OAuth tokens never leave Railway."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import quote

import httpx
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .ebay_client import EbayBrowseClient, EbayNotConfigured, SEARCH_URL, ITEM_URL

PATH = '/api/seeker/ebay/'
DOMAIN = b'SFB-Seeker-eBay-relay-v1'


def signature(key, stamp, body):
    derived = hmac.new(key.encode(), DOMAIN, hashlib.sha256).digest()
    return hmac.new(derived, stamp.encode() + b'\nPOST\n' + PATH.encode() + b'\n' + body, hashlib.sha256).hexdigest()


@csrf_exempt
@require_POST
def relay(request):
    # Independent of the site's optional API-secret gate. A public caller cannot
    # use this endpoint when that gate is disabled. Existing worker credential
    # proves possession through a scoped HMAC; it is never sent in the request.
    if len(request.body) > 8192:
        return JsonResponse({'error': 'Request too large'}, status=413)
    stamp = request.headers.get('X-SFB-Time', '')
    sig = request.headers.get('X-SFB-Signature', '')
    try:
        valid_time = abs(time.time() - int(stamp)) <= 120
    except ValueError:
        valid_time = False
    key = settings.SUPABASE_KEY
    if not key or not valid_time or not hmac.compare_digest(signature(key, stamp, request.body), sig):
        return JsonResponse({'error': 'Worker authentication required'}, status=401)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict): raise ValueError()
        operation = data.get('operation')
        if operation == 'search':
            query = data.get('query')
            if not isinstance(query, str) or not query.strip() or len(query) > 1000: raise ValueError()
        elif operation == 'item':
            item_id = data.get('item_id', '')
            if not isinstance(item_id, str) or not re.fullmatch(r'v1\|[0-9]{6,20}\|0', item_id): raise ValueError()
        elif operation != 'authenticate':
            raise ValueError()
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid relay operation'}, status=400)
    try:
        client = EbayBrowseClient()
        if operation == 'authenticate':
            # Verify real OAuth access, but return no access token.
            client.token
            return JsonResponse({'ready': True})
        if operation == 'search':
            # Exact locked ApiRun query semantics; no parts-category restriction.
            response = client._request(SEARCH_URL, params={'q': query, 'limit': 50,
                'filter': 'buyingOptions:{FIXED_PRICE|AUCTION}'})
        else:
            # Fresh detail: bypass Parts' per-worker item cache.
            response = client._request(ITEM_URL + quote(item_id, safe=''))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict): raise ValueError()
        return JsonResponse(payload)
    except EbayNotConfigured:
        return JsonResponse({'error': 'Railway eBay credentials unavailable'}, status=503)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        return JsonResponse({'error': 'eBay request failed', 'upstream_status': code},
                            status=code if code in (404,429) else 502)
    except (httpx.HTTPError, ValueError, RuntimeError):
        return JsonResponse({'error': 'eBay request unavailable'}, status=502)
