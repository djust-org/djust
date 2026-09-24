"""Social auth helpers for djust.auth.

Provides a context processor that detects available OAuth backends
and injects provider metadata (name, label, icon) into template context.
"""

import warnings
from typing import Any

from django.utils.safestring import mark_safe

# Provider display metadata: allauth provider ID -> (label, SVG icon)
from .accounts.providers import _PROVIDER_META  # noqa: E402  (moved; kept importable)


def social_auth_providers(request: Any) -> dict[str, Any]:
    """Inject available OAuth providers into template context.

    Returns ``{"oauth_providers": [...]}``.  Each item is a dict with keys:
    ``name`` (provider id), ``label`` (display name), ``icon`` (SVG markup),
    ``login_url`` (allauth login URL for this provider).

    If ``django-allauth`` is not installed the list will be empty.

    .. deprecated:: 1.3
        Use ``{% auth_providers auth %}`` from ``{% load djust_auth %}`` (ADR-039).
    """
    warnings.warn(
        "djust.auth.social.social_auth_providers is deprecated; use {% auth_providers auth %} "
        "from {% load djust_auth %} (ADR-039).",
        DeprecationWarning,
        stacklevel=2,
    )
    try:
        from allauth.socialaccount.providers import registry
        from django.urls import reverse
    except ImportError:
        return {"oauth_providers": []}

    try:
        if not registry.loaded:
            registry.load()
        provider_classes = registry.get_class_list()
    except Exception:
        return {"oauth_providers": []}

    providers = []
    for provider_cls in provider_classes:
        provider_id = provider_cls.id
        meta = _PROVIDER_META.get(provider_id)
        if meta:
            label, icon = meta
        else:
            label = provider_cls.name
            icon = ""
        try:
            login_url = (
                reverse(
                    "socialaccount_login",
                )
                + "?provider="
                + provider_id
            )
        except Exception:
            login_url = f"/accounts/{provider_id}/login/"
        providers.append(
            {
                "name": provider_id,
                "label": label,
                "icon": mark_safe(icon),
                "login_url": login_url,
            }
        )

    return {"oauth_providers": providers}
