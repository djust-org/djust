"""LiveView pages for the djust auth admin plugin."""

import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.utils import timezone
from djust import LiveView
from djust.decorators import debounce, event_handler, state

logger = logging.getLogger(__name__)

try:
    from djust.admin_ext.views import AdminBaseMixin
except ImportError:
    try:
        from djust_admin.views import AdminBaseMixin
    except ImportError:

        class AdminBaseMixin:
            def get_admin_context(self):
                return {}


class OAuthProvidersView(AdminBaseMixin, LiveView):
    """Admin page showing configured OAuth providers and their status."""

    template_name = "djust_auth/admin/providers.html"

    def mount(self, request, **kwargs):
        self.request = request

    def get_context_data(self, **kwargs):
        providers = self._get_providers(self.request)
        User = get_user_model()
        allauth_installed = self._is_allauth_installed()

        # Summary stats
        total_linked = 0
        total_oauth_users = 0
        total_users = User.objects.count()
        oauth_percentage = 0

        if allauth_installed:
            try:
                from allauth.socialaccount.models import SocialAccount

                total_linked = SocialAccount.objects.count()
                total_oauth_users = SocialAccount.objects.values("user").distinct().count()
                if total_users > 0:
                    oauth_percentage = round((total_oauth_users / total_users) * 100, 1)
            except Exception as exc:
                # django-allauth is optional; its models may not be available or migrated.
                logger.debug("OAuth stats unavailable (allauth probe failed): %s", exc)

        return {
            **self.get_admin_context(),
            "title": "OAuth Providers",
            "providers": providers,
            "total_users": total_users,
            "allauth_installed": allauth_installed,
            "total_linked": total_linked,
            "total_oauth_users": total_oauth_users,
            "oauth_percentage": oauth_percentage,
        }

    def _is_allauth_installed(self):
        try:
            from django.apps import apps

            return apps.is_installed("allauth.socialaccount")
        except Exception:
            return False

    # Per-provider reference data: recommended scopes and developer console URLs
    PROVIDER_REFERENCE = {
        "github": {
            "recommended_scopes": ["user:email"],
            "console_url": "https://github.com/settings/developers",
            "console_label": "GitHub Developer Settings",
        },
        "google": {
            "recommended_scopes": ["profile", "email"],
            "console_url": "https://console.cloud.google.com/apis/credentials",
            "console_label": "Google Cloud Console",
        },
        "gitlab": {
            "recommended_scopes": ["read_user"],
            "console_url": "https://gitlab.com/-/user_settings/applications",
            "console_label": "GitLab Applications",
        },
        "microsoft": {
            "recommended_scopes": ["User.Read"],
            "console_url": "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps",
            "console_label": "Azure App Registrations",
        },
        "twitter": {
            "recommended_scopes": [],
            "console_url": "https://developer.twitter.com/en/portal/projects-and-apps",
            "console_label": "Twitter Developer Portal",
        },
        "facebook": {
            "recommended_scopes": ["email", "public_profile"],
            "console_url": "https://developers.facebook.com/apps/",
            "console_label": "Meta for Developers",
        },
    }

    def _get_providers(self, request):
        """Build provider status list from allauth configuration."""
        if not self._is_allauth_installed():
            return []

        try:
            from allauth.socialaccount.providers import registry

            if not registry.loaded:
                registry.load()
        except Exception:
            return []

        from django.conf import settings

        configured_providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})

        # Provider icons (reuse from social.py)
        icons = {
            "github": "GH",
            "google": "G",
            "gitlab": "GL",
            "microsoft": "MS",
            "twitter": "X",
            "facebook": "FB",
        }

        providers = []
        thirty_days_ago = timezone.now() - timedelta(days=30)

        for provider_cls in registry.get_class_list():
            pid = provider_cls.id
            provider_conf = configured_providers.get(pid, {})
            app_conf = provider_conf.get("APP", {})
            has_credentials = bool(app_conf.get("client_id"))

            # Count and stats for social accounts
            social_account_count = 0
            last_linked = None
            active_users_30d = 0
            try:
                from allauth.socialaccount.models import SocialAccount

                provider_accounts = SocialAccount.objects.filter(provider=pid)
                social_account_count = provider_accounts.count()
                last_linked_result = provider_accounts.aggregate(last=Max("date_joined"))
                last_linked = last_linked_result.get("last")

                active_users_30d = (
                    provider_accounts.filter(user__last_login__gte=thirty_days_ago)
                    .values("user")
                    .distinct()
                    .count()
                )
            except Exception as exc:
                # Per-provider stats are optional; skip if the provider schema isn't available.
                logger.debug("Per-provider active-user stats unavailable: %s", exc)

            # Build full callback URL from request
            scheme = "https" if request.is_secure() else "http"
            host = request.get_host()
            callback_url = f"{scheme}://{host}/accounts/{pid}/login/callback/"

            # Build safe config summary (never expose secrets)
            client_id = app_conf.get("client_id", "")
            if client_id:
                masked_client_id = client_id[:8] + "..." + client_id[-4:]
            else:
                masked_client_id = ""
            has_secret = bool(app_conf.get("secret"))
            # SCOPE belongs at provider level, but check inside APP too
            scopes = provider_conf.get("SCOPE", []) or app_conf.get("SCOPE", [])
            scope_misplaced = bool(app_conf.get("SCOPE") and not provider_conf.get("SCOPE"))
            extra_settings = {k: v for k, v in provider_conf.items() if k not in ("APP", "SCOPE")}

            # Reference data and setup checklist
            ref = self.PROVIDER_REFERENCE.get(pid, {})
            recommended_scopes = ref.get("recommended_scopes", [])
            console_url = ref.get("console_url", "")
            console_label = ref.get("console_label", "")

            # Build checklist items: (label, is_done)
            checklist = [
                ("Client ID", bool(client_id)),
                ("Client Secret", has_secret),
                ("Scopes configured", bool(scopes)),
            ]

            # Build a settings snippet with env vars for credentials
            env_prefix = pid.upper()
            display_scopes = scopes or recommended_scopes
            snippet_lines = [
                "SOCIALACCOUNT_PROVIDERS = {",
                f'    "{pid}": {{',
                '        "APP": {',
                f'            "client_id": os.getenv("{env_prefix}_CLIENT_ID", ""),',
                f'            "secret": os.getenv("{env_prefix}_CLIENT_SECRET", ""),',
                "        },",
            ]
            if display_scopes:
                scope_str = ", ".join(f'"{s}"' for s in display_scopes)
                snippet_lines.append(f'        "SCOPE": [{scope_str}],')
            snippet_lines.extend(
                [
                    "    },",
                    "}",
                ]
            )
            settings_snippet = "\n".join(snippet_lines)

            providers.append(
                {
                    "id": pid,
                    "name": provider_cls.name,
                    "icon": icons.get(pid, pid[:2].upper()),
                    "has_credentials": has_credentials,
                    "account_count": social_account_count,
                    "callback_url": callback_url,
                    "last_linked": last_linked,
                    "active_users_30d": active_users_30d,
                    "masked_client_id": masked_client_id,
                    "has_secret": has_secret,
                    "scopes": scopes,
                    "recommended_scopes": recommended_scopes,
                    "extra_settings": extra_settings,
                    "console_url": console_url,
                    "console_label": console_label,
                    "checklist": checklist,
                    "settings_snippet": settings_snippet,
                    "scope_misplaced": scope_misplaced,
                }
            )

        return providers


class SocialAccountsView(AdminBaseMixin, LiveView):
    """Admin page showing all linked social accounts with search/filter."""

    template_name = "djust_auth/admin/social_accounts.html"

    search_query = state(default="")
    current_page = state(default=1)
    ordering = state(default="-date_joined")
    filter_provider = state(default="")

    def mount(self, request, **kwargs):
        self.request = request

    def _get_queryset(self):
        from allauth.socialaccount.models import SocialAccount

        qs = SocialAccount.objects.select_related("user").all()

        if self.filter_provider:
            qs = qs.filter(provider=self.filter_provider)

        if self.search_query:
            qs = qs.filter(
                Q(user__username__icontains=self.search_query)
                | Q(user__email__icontains=self.search_query)
                | Q(uid__icontains=self.search_query)
            )

        if self.ordering:
            qs = qs.order_by(self.ordering)

        return qs

    def _get_provider_choices(self):
        from allauth.socialaccount.models import SocialAccount

        providers = (
            SocialAccount.objects.values_list("provider", flat=True).distinct().order_by("provider")
        )
        return [{"value": p, "label": p.title()} for p in providers]

    def get_context_data(self, **kwargs):
        qs = self._get_queryset()
        paginator = Paginator(qs, 25)
        page = paginator.get_page(self.current_page)

        rows = []
        for account in page:
            rows.append(
                {
                    "pk": account.pk,
                    "username": account.user.username,
                    "email": getattr(account.user, "email", ""),
                    "provider": account.provider,
                    "uid": account.uid,
                    "date_joined": (
                        account.date_joined.strftime("%Y-%m-%d %H:%M")
                        if account.date_joined
                        else ""
                    ),
                }
            )

        pagination = {
            "number": page.number,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": (page.previous_page_number() if page.has_previous() else None),
            "next_page_number": (page.next_page_number() if page.has_next() else None),
            "num_pages": paginator.num_pages,
            "count": paginator.count,
        }

        return {
            **self.get_admin_context(),
            "title": "Social Accounts",
            "rows": rows,
            "pagination": pagination,
            "search_query": self.search_query,
            "ordering": self.ordering,
            "filter_provider": self.filter_provider,
            "provider_choices": self._get_provider_choices(),
        }

    @event_handler
    @debounce(300)
    def search(self, value: str):
        self.search_query = value
        self.current_page = 1

    @event_handler
    def sort_by(self, field: str):
        if self.ordering == field:
            self.ordering = f"-{field}"
        elif self.ordering == f"-{field}":
            self.ordering = None
        else:
            self.ordering = field
        self.current_page = 1

    @event_handler
    def filter_by_provider(self, value: str):
        self.filter_provider = value
        self.current_page = 1

    @event_handler
    def go_to_page(self, page: int):
        self.current_page = page
