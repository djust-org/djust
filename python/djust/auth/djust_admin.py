"""djust-admin plugin for djust auth.

Registers an Authentication plugin with:
- Dashboard widget showing user/auth stats
- OAuth Providers admin page
- Social Accounts admin page (when allauth is installed)
- SocialAccount model registration (when allauth is installed)

Only active when djust.admin_ext (or djust-admin) is installed.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)

try:
    from djust.admin_ext import DjustModelAdmin, site
    from djust.admin_ext.decorators import register
    from djust.admin_ext.plugins import AdminPage, AdminPlugin, AdminWidget
except ImportError:
    try:
        from djust_admin import DjustModelAdmin, site
        from djust_admin.decorators import register
        from djust_admin.plugins import AdminPage, AdminPlugin, AdminWidget
    except ImportError:
        # djust-admin not installed — skip plugin registration
        DjustModelAdmin = None

if DjustModelAdmin is not None:
    from .admin_views import OAuthProvidersView, SocialAccountsView

    # ---- Conditional model registration ----

    try:
        if apps.is_installed("allauth.socialaccount"):
            from allauth.socialaccount.models import SocialAccount

            @register(SocialAccount)
            class SocialAccountAdmin(DjustModelAdmin):
                list_display = ["user", "provider", "uid", "date_joined"]
                list_filter = ["provider"]
                search_fields = ["user__username", "user__email", "uid"]
                ordering = ["-date_joined"]
    except Exception:
        pass  # App registry not ready or allauth not configured

    # ---- Dashboard widget ----

    class AuthSummaryWidget(AdminWidget):
        """Dashboard widget showing user and auth statistics."""

        widget_id = "auth_summary"
        label = "Authentication"
        template_name = "djust_auth/admin/widgets/auth_summary.html"
        order = 5
        size = "lg"

        def get_context(self, request):
            User = get_user_model()
            week_ago = timezone.now() - timedelta(days=7)

            # Count configured OAuth providers and OAuth users
            oauth_count = 0
            oauth_users = 0
            try:
                if apps.is_installed("allauth.socialaccount"):
                    from allauth.socialaccount.models import SocialAccount
                    from allauth.socialaccount.providers import registry

                    if not registry.loaded:
                        registry.load()
                    oauth_count = len(registry.get_class_list())
                    oauth_users = SocialAccount.objects.values("user").distinct().count()
            except Exception as exc:
                # django-allauth is an optional dependency; its registry/models may be missing.
                logger.debug("OAuth provider/user stats unavailable: %s", exc)

            return {
                "total_users": User.objects.count(),
                "recent_signups": User.objects.filter(date_joined__gte=week_ago).count(),
                "staff_users": User.objects.filter(is_staff=True).count(),
                "superusers": User.objects.filter(is_superuser=True).count(),
                "oauth_users": oauth_users,
                "oauth_providers": oauth_count,
            }

    # ---- Plugin ----

    class AuthAdminPlugin(AdminPlugin):
        name = "auth"
        verbose_name = "Authentication"

        def get_pages(self):
            pages = [
                AdminPage(
                    url_path="auth/providers",
                    url_name="auth_providers",
                    view_class=OAuthProvidersView,
                    label="OAuth Providers",
                    icon="🔑",
                    nav_section="Authentication",
                    nav_order=10,
                ),
            ]
            if apps.is_installed("allauth.socialaccount"):
                pages.append(
                    AdminPage(
                        url_path="auth/accounts",
                        url_name="auth_social_accounts",
                        view_class=SocialAccountsView,
                        label="Social Accounts",
                        icon="🔗",
                        nav_section="Authentication",
                        nav_order=20,
                    )
                )
            return pages

        def get_widgets(self):
            return [AuthSummaryWidget()]

    site.register_plugin(AuthAdminPlugin)
