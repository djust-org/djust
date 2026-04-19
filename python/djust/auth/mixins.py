from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class LoginRequiredLiveViewMixin:
    """Add to any LiveView to require authentication.

    Intercepts at dispatch() before get() -> mount() runs,
    so no LiveView state is initialized for anonymous users.
    """

    login_url = None  # Falls back to settings.LOGIN_URL

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = self.login_url or getattr(settings, "LOGIN_URL", "/accounts/login/")
            url = f"{login_url}?{urlencode({'next': request.get_full_path()})}"
            return redirect(url)
        return super().dispatch(request, *args, **kwargs)


class PermissionRequiredLiveViewMixin:
    """Add to any LiveView to require specific permissions.

    Raises PermissionDenied (403) if the user lacks the required permission.
    Must be used together with LoginRequiredLiveViewMixin or Django's
    AuthenticationMiddleware.
    """

    permission_required = None  # Set to a permission string like "app.change_model"

    def dispatch(self, request, *args, **kwargs):
        if self.permission_required and not request.user.has_perm(self.permission_required):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
