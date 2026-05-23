"""LVN-II PR-3 gate test: native template variant resolver."""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            }
        ],
        SECRET_KEY="test-secret",
        USE_TZ=True,
    )
    django.setup()


class TestVariantName:
    def test_inserts_format_before_html(self):
        from djust.renderers.template_resolver import variant_name

        assert variant_name("medicare/home.html", "swiftui") == "medicare/home.swiftui.html"
        assert variant_name("home.html", "compose") == "home.compose.html"

    def test_handles_paths_without_html_suffix(self):
        from djust.renderers.template_resolver import variant_name

        assert variant_name("home", "swiftui") == "home.swiftui.html"

    def test_handles_nested_extensions(self):
        from djust.renderers.template_resolver import variant_name

        # Edge case: template path like home.partial.html
        assert variant_name("home.partial.html", "swiftui") == "home.partial.swiftui.html"


class TestResolveVariant:
    def test_html_falls_through_to_base(self):
        from djust.renderers.template_resolver import resolve_variant

        assert resolve_variant("home.html", "html") == "home.html"

    def test_none_format_falls_through_to_base(self):
        from djust.renderers.template_resolver import resolve_variant

        assert resolve_variant("home.html", None) == "home.html"

    def test_missing_variant_falls_back_to_base(self):
        """Asking for a swiftui variant that doesn't exist anywhere on
        the template path falls back to the base HTML name. The handshake
        never errors out for a missing variant — the native client just
        gets the HTML (which will fail its widget-tag check downstream
        in LVN-II PR-4 — that's the right error layer)."""
        from djust.renderers.template_resolver import resolve_variant

        assert (
            resolve_variant("definitely-does-not-exist.html", "swiftui")
            == "definitely-does-not-exist.html"
        )
