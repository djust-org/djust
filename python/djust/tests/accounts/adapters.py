"""allauth adapters for the account check tests."""

from djust.auth.accounts.backends.allauth_integration import DjustAccountAdapter


class ClientIpAdapter(DjustAccountAdapter):
    def get_client_ip(self, request):
        return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "")


class PlainAdapter(DjustAccountAdapter):
    pass
