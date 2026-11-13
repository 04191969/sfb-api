from supabase import create_client
from django.conf import settings

_client = None

def get_supabase():
    """Return a singleton Supabase client using the service_role key."""
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client
