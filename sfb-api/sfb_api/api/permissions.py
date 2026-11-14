import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class SfbApiKeyPermission(BasePermission):
    """Require X-SFB-Key when SFB_API_SECRET is set. Open when the secret is unset."""

    message = "Missing or invalid API key."

    def has_permission(self, request, view):
        secret = settings.SFB_API_SECRET
        if not secret:
            return True
        provided = request.headers.get("X-SFB-Key", "")
        return hmac.compare_digest(provided, secret)
