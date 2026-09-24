"""djust account-backend checks, ``djust.A100``-``A107`` (ADR-039).

All are silent unless ``DJUST_CONFIG["ACCOUNTS"]`` is set: accounts are opt-in.
Each check is isolated so a bug in one never hides the others or crashes
``manage.py check``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

from django.conf import settings
from django.core.checks import CheckMessage, Error, Info, Warning, register

from .utils import _is_check_suppressed

logger = logging.getLogger(__name__)

_ALLAUTH_APPS = ("allauth", "allauth.account")
_ALLAUTH_MIDDLEWARE = "allauth.account.middleware.AccountMiddleware"
_ALLAUTH_AUTH_BACKEND = "allauth.account.auth_backends.AuthenticationBackend"
_INTEGRATION = "djust.auth.accounts.backends.allauth_integration"
#: allauth setting -> (key inside a dict setting or None, djust class it must subclass)
_PROTECTIONS = (
    ("ACCOUNT_ADAPTER", None, "DjustAccountAdapter", "strict ?next= redirects and the signup gate"),
    ("ACCOUNT_FORMS", "signup", "DjustSignupForm", "signup_validators on sign-up"),
    (
        "SOCIALACCOUNT_ADAPTER",
        None,
        "DjustSocialAccountAdapter",
        "signup_validators on social auto-signup",
    ),
    (
        "SOCIALACCOUNT_FORMS",
        "signup",
        "DjustSocialSignupForm",
        "signup_validators on social sign-up",
    ),
)
_STALE_EXTENDS = re.compile(
    r"""\{%\s*extends\s+["'](account/base_entrance\.html|allauth/layouts/base\.html)["']"""
)


def _spec() -> Any:
    from djust.config import get_djust_config

    return get_djust_config().get("ACCOUNTS")


def _backend_class(spec: Any) -> Any:
    from django.utils.module_loading import import_string

    from djust.auth.accounts.registry import backend_path

    return import_string(backend_path(spec))


def _is_allauth(spec: Any) -> bool:
    try:
        from djust.auth.accounts.backends.allauth import AllauthBackend
    except ImportError:
        from djust.auth.accounts.registry import backend_path

        return bool(backend_path(spec).endswith(".AllauthBackend"))
    try:
        cls = _backend_class(spec)
    except ImportError:
        return False
    return isinstance(cls, type) and issubclass(cls, AllauthBackend)


def _a100(spec: Any) -> list[CheckMessage]:
    from djust.auth.accounts.base import AccountBackend
    from djust.auth.accounts.registry import backend_path

    path = backend_path(spec)
    try:
        cls = _backend_class(spec)
    except ImportError as exc:
        return [
            Error(
                f'DJUST_CONFIG["ACCOUNTS"]["BACKEND"] = {path!r} could not be imported: {exc}',
                hint='Use "django", "allauth", or the dotted path of an AccountBackend subclass.',
                id="djust.A100",
            )
        ]
    if not (isinstance(cls, type) and issubclass(cls, AccountBackend)):
        return [
            Error(
                f"{path!r} is not an AccountBackend subclass.",
                hint="Subclass djust.auth.accounts.AccountBackend (or a built-in backend).",
                id="djust.A100",
            )
        ]
    return []


def _a101(spec: Any) -> list[CheckMessage]:
    if not _is_allauth(spec):
        return []
    problems = []
    try:
        import allauth  # noqa: F401
    except ImportError:
        problems.append('django-allauth is not installed (pip install "djust[auth-allauth]")')
    missing = [a for a in _ALLAUTH_APPS if a not in settings.INSTALLED_APPS]
    if missing:
        problems.append(f"INSTALLED_APPS is missing {', '.join(missing)}")
    if _ALLAUTH_MIDDLEWARE not in getattr(settings, "MIDDLEWARE", []):
        problems.append(f"MIDDLEWARE is missing {_ALLAUTH_MIDDLEWARE}")
    if _ALLAUTH_AUTH_BACKEND not in getattr(settings, "AUTHENTICATION_BACKENDS", []):
        problems.append(
            f"AUTHENTICATION_BACKENDS is missing {_ALLAUTH_AUTH_BACKEND} (needed to sign in by email)"
        )
    if not problems:
        return []
    return [
        Error(
            "The allauth account backend is configured but allauth is not set up: "
            + "; ".join(problems)
            + ".",
            hint="See the accounts guide's quick start for the INSTALLED_APPS and MIDDLEWARE entries.",
            id="djust.A101",
        )
    ]


def _a102(spec: Any) -> list[CheckMessage]:
    if not _is_allauth(spec):
        return []
    from djust._client_ip import _trusted_proxy_count

    behind_proxy = bool(getattr(settings, "USE_X_FORWARDED_HOST", False)) or bool(
        getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    )
    # allauth reads the client IP from ALLAUTH_TRUSTED_CLIENT_IP_HEADER (e.g.
    # "X-Real-IP", which ingress-nginx sets) before any proxy count, so a
    # non-empty header is a complete configuration on its own (#3068).
    client_ip_header = getattr(settings, "ALLAUTH_TRUSTED_CLIENT_IP_HEADER", None)
    has_client_ip_header = isinstance(client_ip_header, str) and bool(client_ip_header.strip())
    if (
        behind_proxy
        and _trusted_proxy_count() == 0
        and not getattr(settings, "ALLAUTH_TRUSTED_PROXY_COUNT", 0)
        and not has_client_ip_header
    ):
        return [
            Warning(
                "Django is configured to run behind a proxy, but DJUST_TRUSTED_PROXY_COUNT is 0: every visitor "
                "shares the proxy's IP, so one client's failed logins rate-limit everyone.",
                hint="Set DJUST_TRUSTED_PROXY_COUNT to the number of reverse proxies in front of Django "
                "(e.g. 1 behind ingress-nginx), or ALLAUTH_TRUSTED_CLIENT_IP_HEADER to the header your "
                'proxy sets with the real client IP (e.g. "X-Real-IP").',
                id="djust.A102",
            )
        ]
    return []


def _a103(spec: Any) -> list[CheckMessage]:
    if not _is_allauth(spec) or settings.DEBUG:
        return []
    if getattr(settings, "ACCOUNT_EMAIL_VERIFICATION", "mandatory") == "none":
        return [
            Warning(
                'ACCOUNT_EMAIL_VERIFICATION = "none" in production: anyone can sign up with an address they '
                "don't own.",
                hint='Remove the setting (djust defaults to "mandatory") or set it to "mandatory".',
                id="djust.A103",
            )
        ]
    return []


def _count_includes(patterns: Any, counts: dict[str, int]) -> None:
    from django.urls import URLResolver

    for p in patterns:
        if isinstance(p, URLResolver):
            module = p.urlconf_name
            name = getattr(module, "__name__", module if isinstance(module, str) else "")
            if name in counts:
                counts[name] += 1
            _count_includes(p.url_patterns, counts)


def _a104(spec: Any) -> list[CheckMessage]:
    from django.urls import get_resolver

    counts = {"allauth.urls": 0, "djust.auth.accounts.urls": 0}
    _count_includes(get_resolver().url_patterns, counts)
    # the accounts include mounts allauth.urls itself for the allauth backend
    expected_allauth = counts["djust.auth.accounts.urls"] if _is_allauth(spec) else 0
    if counts["djust.auth.accounts.urls"] > 1 or counts["allauth.urls"] > expected_allauth:
        return [
            Error(
                "Account URLs are included more than once "
                f"(djust.auth.accounts.urls x{counts['djust.auth.accounts.urls']}, allauth.urls x{counts['allauth.urls']}).",
                hint='Include only path("accounts/", include("djust.auth.accounts.urls")); it mounts allauth.urls '
                "itself for the allauth backend.",
                id="djust.A104",
            )
        ]
    return []


def _a105(spec: Any) -> list[CheckMessage]:
    stale = []
    for template_config in settings.TEMPLATES:
        for directory in template_config.get("DIRS", []):
            for sub in ("account", "allauth/layouts"):
                base = Path(directory) / sub
                if not base.is_dir():
                    continue
                for path in base.rglob("*.html"):
                    try:
                        if _STALE_EXTENDS.search(path.read_text(errors="ignore")):
                            stale.append(str(path))
                    except OSError:
                        continue
    if not stale:
        return []
    return [
        Info(
            f"{len(stale)} project template(s) extend allauth's own base instead of the djust account layout "
            f"(first: {stale[0]}).",
            hint='Extend "djust_auth/layouts/auth.html" (or override its blocks) so the page matches the kit.',
            id="djust.A105",
        )
    ]


def _a106(spec: Any) -> list[CheckMessage]:
    apps = list(settings.INSTALLED_APPS)
    problems = []
    if "djust.auth" not in apps:
        problems.append('"djust.auth" is not in INSTALLED_APPS')
    if "djust.theming" not in apps:
        problems.append(
            '"djust.theming" is not in INSTALLED_APPS (the account layout uses {% theme_head %})'
        )
    if "djust.auth" in apps and _is_allauth(spec) and "allauth" in apps:
        if apps.index("djust.auth") > apps.index("allauth"):
            problems.append('"djust.auth" must come before "allauth" so its allauth templates win')
    if not problems:
        return []
    return [
        Error(
            "Account pages are misconfigured: " + "; ".join(problems) + ".",
            hint='INSTALLED_APPS needs "djust.theming" and "djust.auth" (before "allauth").',
            id="djust.A106",
        )
    ]


def _a107(spec: Any) -> list[CheckMessage]:
    if not _is_allauth(spec):
        return []
    from django.utils.module_loading import import_string

    messages: list[CheckMessage] = []
    for setting, key, djust_class, protects in _PROTECTIONS:
        value = getattr(settings, setting, None)
        if key is not None:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        if not value:
            continue
        try:
            configured = import_string(value) if isinstance(value, str) else value
            required = import_string(f"{_INTEGRATION}.{djust_class}")
        except ImportError:
            continue
        if not (isinstance(configured, type) and issubclass(configured, required)):
            where = f"{setting}[{key!r}]" if key else setting
            messages.append(
                Warning(
                    f"{where} is not a subclass of {djust_class}, so djust's {protects} are off.",
                    hint=f"Subclass {_INTEGRATION}.{djust_class} in your own class.",
                    id="djust.A107",
                )
            )
    return messages


_CHECKS: list[tuple[str, Callable[[Any], list[CheckMessage]]]] = [
    ("A100", _a100),
    ("A101", _a101),
    ("A102", _a102),
    ("A103", _a103),
    ("A104", _a104),
    ("A105", _a105),
    ("A106", _a106),
    ("A107", _a107),
]


@register("djust")
def check_accounts(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """``djust.A100``-``A107``: account backend configuration (silent when accounts aren't configured)."""
    spec = _spec()
    if not spec:
        return []
    messages: list[CheckMessage] = []
    for check_id, check in _CHECKS:
        if _is_check_suppressed(check_id):
            continue
        try:
            messages.extend(check(spec))
        except Exception:  # noqa: BLE001 - one broken check must not hide the rest
            logger.exception("djust.%s account check raised; skipping it", check_id)
    return messages
