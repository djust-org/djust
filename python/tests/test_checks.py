"""Tests for djust system checks (djust/checks.py)."""

import textwrap
from unittest.mock import patch

from djust.checks import _DOC_DJUST_EVENT_RE


class TestT004Regex:
    """T004 -- document.addEventListener for djust: events."""

    def test_matches_document_djust_push_event(self):
        content = """document.addEventListener('djust:push_event', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_double_quoted(self):
        content = """document.addEventListener("djust:push_event", (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_djust_stream(self):
        content = """document.addEventListener('djust:stream', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_djust_connected(self):
        content = """document.addEventListener('djust:connected', () => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_with_space_after_dot(self):
        content = """document .addEventListener('djust:error', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_no_match_window_listener(self):
        """window.addEventListener is correct -- should NOT match."""
        content = """window.addEventListener('djust:push_event', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None

    def test_no_match_non_djust_event(self):
        """Non-djust events are fine on document."""
        content = """document.addEventListener('click', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None

    def test_no_match_djust_without_colon(self):
        """'djust' without colon prefix is not a djust event."""
        content = """document.addEventListener('djust_init', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None


class TestT004CheckIntegration:
    """Integration test for T004 using the actual check function."""

    def test_t004_detects_document_listener(self, tmp_path, settings):
        """T004 should flag document.addEventListener for djust: events."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<script>document.addEventListener('djust:push_event', (e) => {});</script>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t004_errors = [e for e in errors if e.id == "djust.T004"]
        assert len(t004_errors) == 1
        assert "document.addEventListener" in t004_errors[0].msg

    def test_t004_passes_window_listener(self, tmp_path, settings):
        """T004 should NOT flag window.addEventListener for djust: events."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text(
            "<script>window.addEventListener('djust:push_event', (e) => {});</script>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t004_errors = [e for e in errors if e.id == "djust.T004"]
        assert len(t004_errors) == 0


# ---------------------------------------------------------------------------
# Configuration checks (C001-C004, S004)
# ---------------------------------------------------------------------------


class TestC001AsgiApplication:
    """C001 -- ASGI_APPLICATION not set."""

    def test_c001_missing_asgi_application(self, settings):
        """C001 fires when ASGI_APPLICATION is not set."""
        settings.ASGI_APPLICATION = None
        # Ensure other settings exist so we only see C001
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c001 = [e for e in errors if e.id == "djust.C001"]
        assert len(c001) == 1
        assert "ASGI_APPLICATION" in c001[0].msg

    def test_c001_passes_when_set(self, settings):
        """C001 should not fire when ASGI_APPLICATION is configured."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c001 = [e for e in errors if e.id == "djust.C001"]
        assert len(c001) == 0


class TestC002ChannelLayers:
    """C002 -- CHANNEL_LAYERS not configured."""

    def test_c002_missing_channel_layers(self, settings):
        """C002 fires when CHANNEL_LAYERS is not set."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = None
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c002 = [e for e in errors if e.id == "djust.C002"]
        assert len(c002) == 1
        assert "CHANNEL_LAYERS" in c002[0].msg

    def test_c002_empty_channel_layers(self, settings):
        """C002 fires when CHANNEL_LAYERS is empty dict."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c002 = [e for e in errors if e.id == "djust.C002"]
        assert len(c002) == 1

    def test_c002_passes_when_configured(self, settings):
        """C002 should not fire when CHANNEL_LAYERS is set."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c002 = [e for e in errors if e.id == "djust.C002"]
        assert len(c002) == 0


class TestC003DaphneOrdering:
    """C003 -- daphne ordering in INSTALLED_APPS."""

    def test_c003_daphne_after_staticfiles(self, settings):
        """C003 Warning when daphne is listed after staticfiles."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["django.contrib.staticfiles", "daphne", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c003 = [e for e in errors if e.id == "djust.C003"]
        assert len(c003) == 1
        assert "before" in c003[0].msg

    def test_c003_daphne_before_staticfiles_ok(self, settings):
        """C003 should not fire when daphne is before staticfiles."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c003 = [e for e in errors if e.id == "djust.C003"]
        assert len(c003) == 0

    def test_c003_daphne_missing_info(self, settings):
        """C003 Info when daphne is missing entirely."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c003 = [e for e in errors if e.id == "djust.C003"]
        assert len(c003) == 1
        assert "not in INSTALLED_APPS" in c003[0].msg


class TestC004DjustInstalled:
    """C004 -- djust not in INSTALLED_APPS."""

    def test_c004_djust_missing(self, settings):
        """C004 fires when djust is not in INSTALLED_APPS."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c004 = [e for e in errors if e.id == "djust.C004"]
        assert len(c004) == 1
        assert "djust" in c004[0].msg

    def test_c004_passes_when_installed(self, settings):
        """C004 should not fire when djust is in INSTALLED_APPS."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c004 = [e for e in errors if e.id == "djust.C004"]
        assert len(c004) == 0


class TestC006DaphneWithoutWhitenoise:
    """C006 -- daphne without whitenoise for static file serving."""

    def test_c006_daphne_no_whitenoise(self, settings):
        """C006 fires when daphne is installed but whitenoise is not in MIDDLEWARE."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]
        settings.MIDDLEWARE = [
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
        ]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c006 = [e for e in errors if e.id == "djust.C006"]
        assert len(c006) == 1
        assert "WhiteNoise" in c006[0].hint
        assert "static" in c006[0].msg.lower()

    def test_c006_passes_with_whitenoise(self, settings):
        """C006 should not fire when whitenoise middleware is present."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]
        settings.MIDDLEWARE = [
            "django.middleware.security.SecurityMiddleware",
            "whitenoise.middleware.WhiteNoiseMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
        ]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c006 = [e for e in errors if e.id == "djust.C006"]
        assert len(c006) == 0

    def test_c006_not_triggered_without_daphne(self, settings):
        """C006 should not fire when daphne is not installed."""
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["django.contrib.staticfiles", "djust"]
        settings.MIDDLEWARE = [
            "django.middleware.security.SecurityMiddleware",
        ]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c006 = [e for e in errors if e.id == "djust.C006"]
        assert len(c006) == 0


class TestS004DebugAllowedHosts:
    """S004 -- DEBUG=True with non-localhost ALLOWED_HOSTS."""

    def test_s004_debug_with_public_host(self, settings):
        """S004 fires when DEBUG=True with non-local ALLOWED_HOSTS."""
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["example.com", "localhost"]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        s004 = [e for e in errors if e.id == "djust.S004"]
        assert len(s004) == 1
        assert "example.com" in s004[0].msg

    def test_s004_debug_with_only_localhost(self, settings):
        """S004 should not fire with localhost-only ALLOWED_HOSTS."""
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1", "::1"]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        s004 = [e for e in errors if e.id == "djust.S004"]
        assert len(s004) == 0

    def test_s004_debug_false_no_warning(self, settings):
        """S004 should not fire when DEBUG=False."""
        settings.DEBUG = False
        settings.ALLOWED_HOSTS = ["example.com"]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        s004 = [e for e in errors if e.id == "djust.S004"]
        assert len(s004) == 0

    def test_s004_private_network_allowed(self, settings):
        """S004 should not flag 192.168.* or 10.* addresses."""
        settings.DEBUG = True
        settings.ALLOWED_HOSTS = ["192.168.1.100", "10.0.0.5", "localhost"]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        s004 = [e for e in errors if e.id == "djust.S004"]
        assert len(s004) == 0


class TestC010TailwindCdnInProduction:
    """C010 -- Tailwind CDN detected in production templates."""

    def test_c010_detects_cdn_in_production(self, tmp_path, settings):
        """C010 fires when Tailwind CDN is in base template and DEBUG=False."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "base.html").write_text(
            '<html><head><script src="https://cdn.tailwindcss.com"></script></head></html>'
        )
        settings.DEBUG = False
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c010 = [e for e in errors if e.id == "djust.C010"]
        assert len(c010) == 1
        assert "Tailwind CDN" in c010[0].msg
        assert "base.html" in c010[0].msg

    def test_c010_does_not_fire_in_development(self, tmp_path, settings):
        """C010 should not fire when DEBUG=True (development mode)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "base.html").write_text(
            '<html><head><script src="https://cdn.tailwindcss.com"></script></head></html>'
        )
        settings.DEBUG = True
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c010 = [e for e in errors if e.id == "djust.C010"]
        assert len(c010) == 0

    def test_c010_passes_with_compiled_css(self, tmp_path, settings):
        """C010 should not fire when compiled CSS is used instead of CDN."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "base.html").write_text(
            '<html><head><link rel="stylesheet" href="/static/css/output.css"></head></html>'
        )
        settings.DEBUG = False
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c010 = [e for e in errors if e.id == "djust.C010"]
        assert len(c010) == 0


class TestC011MissingCompiledCss:
    """C011 -- Tailwind configured but compiled CSS not found."""

    def test_c011_detects_missing_output_css_dev(self, tmp_path, settings, monkeypatch):
        """C011 fires as Info when Tailwind configured but output.css missing in dev."""
        # Create tailwind.config.js
        config_file = tmp_path / "tailwind.config.js"
        config_file.write_text("module.exports = { content: ['./templates/**/*.html'] }")

        # Create static dir with input.css but no output.css
        static_dir = tmp_path / "static" / "css"
        static_dir.mkdir(parents=True)
        (static_dir / "input.css").write_text("@import 'tailwindcss';")

        monkeypatch.chdir(tmp_path)
        settings.DEBUG = True
        settings.STATICFILES_DIRS = [str(tmp_path / "static")]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c011 = [e for e in errors if e.id == "djust.C011"]
        assert len(c011) == 1
        assert "output.css not found" in c011[0].msg

    def test_c011_detects_missing_output_css_production(self, tmp_path, settings, monkeypatch):
        """C011 fires as Warning when Tailwind configured but output.css missing in production."""
        # Create tailwind.config.js
        config_file = tmp_path / "tailwind.config.js"
        config_file.write_text("module.exports = { content: ['./templates/**/*.html'] }")

        # Create static dir with input.css but no output.css
        static_dir = tmp_path / "static" / "css"
        static_dir.mkdir(parents=True)
        (static_dir / "input.css").write_text("@import 'tailwindcss';")

        monkeypatch.chdir(tmp_path)
        settings.DEBUG = False
        settings.STATICFILES_DIRS = [str(tmp_path / "static")]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c011 = [e for e in errors if e.id == "djust.C011"]
        assert len(c011) == 1
        assert "output.css not found" in c011[0].msg

    def test_c011_passes_when_output_exists(self, tmp_path, settings, monkeypatch):
        """C011 should not fire when output.css exists."""
        # Create tailwind.config.js
        config_file = tmp_path / "tailwind.config.js"
        config_file.write_text("module.exports = { content: ['./templates/**/*.html'] }")

        # Create static dir with both input.css and output.css
        static_dir = tmp_path / "static" / "css"
        static_dir.mkdir(parents=True)
        (static_dir / "input.css").write_text("@import 'tailwindcss';")
        (static_dir / "output.css").write_text("/* compiled css */")

        monkeypatch.chdir(tmp_path)
        settings.DEBUG = False
        settings.STATICFILES_DIRS = [str(tmp_path / "static")]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c011 = [e for e in errors if e.id == "djust.C011"]
        assert len(c011) == 0

    def test_c011_passes_when_tailwind_not_configured(self, tmp_path, settings, monkeypatch):
        """C011 should not fire when Tailwind is not configured."""
        # No tailwind.config.js, no input.css
        monkeypatch.chdir(tmp_path)
        settings.DEBUG = False
        settings.STATICFILES_DIRS = []
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c011 = [e for e in errors if e.id == "djust.C011"]
        assert len(c011) == 0


class TestC012ManualClientJs:
    """C012 -- Manual client.js loading in base templates."""

    def test_c012_detects_manual_client_js(self, tmp_path, settings):
        """C012 fires when manual client.js script tag is found in base template."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "base.html").write_text(
            "<html><head><script src=\"{% static 'djust/client.js' %}\" defer></script></head></html>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c012 = [e for e in errors if e.id == "djust.C012"]
        assert len(c012) == 1
        assert "client.js" in c012[0].msg
        assert "base.html" in c012[0].msg

    def test_c012_detects_in_layout_template(self, tmp_path, settings):
        """C012 fires when manual client.js script tag is found in layout template."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "layout.html").write_text(
            '<html><body><script src="/static/djust/client.js"></script></body></html>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c012 = [e for e in errors if e.id == "djust.C012"]
        assert len(c012) == 1
        assert "layout.html" in c012[0].msg

    def test_c012_passes_without_manual_script(self, tmp_path, settings):
        """C012 should not fire when client.js is not manually loaded."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "base.html").write_text(
            "<html><head><!-- djust auto-injects client.js --></head></html>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c012 = [e for e in errors if e.id == "djust.C012"]
        assert len(c012) == 0

    def test_c012_passes_for_non_base_templates(self, tmp_path, settings):
        """C012 should not fire for client.js in non-base/layout templates."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page.html").write_text(
            '<div><script src="/static/djust/client.js"></script></div>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.ASGI_APPLICATION = "myproject.asgi.application"
        settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
        settings.INSTALLED_APPS = ["daphne", "django.contrib.staticfiles", "djust"]

        from djust.checks import check_configuration

        errors = check_configuration(None)
        c012 = [e for e in errors if e.id == "djust.C012"]
        assert len(c012) == 0


# ---------------------------------------------------------------------------
# LiveView checks (V001-V004)
# ---------------------------------------------------------------------------


def _liveview_available():
    """Return True if LiveView can be imported (Rust extension built)."""
    try:
        from djust.live_view import LiveView  # noqa: F401

        return True
    except ImportError:
        return False


def _force_gc():
    """Force garbage collection to clean up dynamically created subclasses."""
    import gc

    gc.collect()


class TestV001MissingTemplateName:
    """V001 -- missing template_name on LiveView subclass."""

    def test_v001_no_template_name(self):
        """V001 fires for a LiveView subclass missing template_name."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        cls = type("V001NoTemplateView", (LiveView,), {"__module__": "myapp.views"})

        try:
            errors = check_liveviews(None)
            v001 = [e for e in errors if e.id == "djust.V001"]
            assert any("V001NoTemplateView" in e.msg for e in v001)
        finally:
            del cls
            _force_gc()

    def test_v001_passes_with_template_name(self):
        """V001 should not fire when template_name is present."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        cls = type(
            "V001WithTemplateView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "my_template.html"},
        )

        try:
            errors = check_liveviews(None)
            v001 = [e for e in errors if e.id == "djust.V001"]
            assert not any("V001WithTemplateView" in e.msg for e in v001)
        finally:
            del cls
            _force_gc()


class TestV002MissingMount:
    """V002 -- no mount() method on LiveView subclass."""

    def test_v002_no_mount(self):
        """V002 fires for a LiveView subclass without mount()."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        cls = type(
            "V002NoMountView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html"},
        )

        try:
            errors = check_liveviews(None)
            v002 = [e for e in errors if e.id == "djust.V002"]
            assert any("V002NoMountView" in e.msg for e in v002)
        finally:
            del cls
            _force_gc()

    def test_v002_passes_with_mount(self):
        """V002 should not fire when mount() is defined."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V002HasMountView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": mount},
        )

        try:
            errors = check_liveviews(None)
            v002 = [e for e in errors if e.id == "djust.V002"]
            assert not any("V002HasMountView" in e.msg for e in v002)
        finally:
            del cls
            _force_gc()


class TestV003MountSignature:
    """V003 -- mount() has wrong signature."""

    def test_v003_mount_missing_request_param(self):
        """V003 fires when mount() does not accept 'request' as second param."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self):
            pass

        cls = type(
            "V003BadMountView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": mount},
        )

        try:
            errors = check_liveviews(None)
            v003 = [e for e in errors if e.id == "djust.V003"]
            assert any("V003BadMountView" in e.msg for e in v003)
        finally:
            del cls
            _force_gc()

    def test_v003_mount_wrong_second_param_name(self):
        """V003 fires when second param is not named 'request'."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, req, **kwargs):
            pass

        cls = type(
            "V003WrongParamView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": mount},
        )

        try:
            errors = check_liveviews(None)
            v003 = [e for e in errors if e.id == "djust.V003"]
            assert any("V003WrongParamView" in e.msg for e in v003)
        finally:
            del cls
            _force_gc()

    def test_v003_passes_correct_signature(self):
        """V003 should not fire when mount(self, request, **kwargs) is correct."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        cls = type(
            "V003GoodMountView",
            (LiveView,),
            {"__module__": "myapp.views", "template_name": "t.html", "mount": mount},
        )

        try:
            errors = check_liveviews(None)
            v003 = [e for e in errors if e.id == "djust.V003"]
            assert not any("V003GoodMountView" in e.msg for e in v003)
        finally:
            del cls
            _force_gc()


class TestV004MissingEventHandlerDecorator:
    """V004 -- public method looks like event handler but missing @event_handler."""

    def test_v004_handle_prefix_without_decorator(self):
        """V004 fires for handle_* method without @event_handler."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        def handle_submit(self, **kwargs):
            pass

        cls = type(
            "V004MissingDecView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "handle_submit": handle_submit,
            },
        )

        try:
            errors = check_liveviews(None)
            v004 = [e for e in errors if e.id == "djust.V004"]
            assert any("V004MissingDecView" in e.msg and "handle_submit" in e.msg for e in v004)
        finally:
            del cls
            _force_gc()

    def test_v004_passes_with_decorator(self):
        """V004 should not fire for a properly decorated method."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.decorators import event_handler
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        @event_handler()
        def handle_save(self, **kwargs):
            pass

        cls = type(
            "V004DecoratedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "handle_save": handle_save,
            },
        )

        try:
            errors = check_liveviews(None)
            v004 = [e for e in errors if e.id == "djust.V004"]
            assert not any("V004DecoratedView" in e.msg for e in v004)
        finally:
            del cls
            _force_gc()

    def test_v004_private_method_ignored(self):
        """V004 should not fire for _private methods."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        def _handle_internal(self, **kwargs):
            pass

        cls = type(
            "V004PrivateView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "_handle_internal": _handle_internal,
            },
        )

        try:
            errors = check_liveviews(None)
            v004 = [e for e in errors if e.id == "djust.V004"]
            assert not any("V004PrivateView" in e.msg for e in v004)
        finally:
            del cls
            _force_gc()


class TestV005AllowedModules:
    """V005 -- LiveView module not in LIVEVIEW_ALLOWED_MODULES."""

    def test_v005_module_not_allowed(self, settings):
        """V005 fires when module is not in LIVEVIEW_ALLOWED_MODULES."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        settings.LIVEVIEW_ALLOWED_MODULES = ["other_app.views"]

        cls = type(
            "V005NotAllowedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
            },
        )

        try:
            errors = check_liveviews(None)
            v005 = [e for e in errors if e.id == "djust.V005"]
            assert any("V005NotAllowedView" in e.msg for e in v005)
            assert any("LIVEVIEW_ALLOWED_MODULES" in e.msg for e in v005)
        finally:
            del cls
            _force_gc()

    def test_v005_module_allowed(self, settings):
        """V005 should not fire when module is in LIVEVIEW_ALLOWED_MODULES."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        settings.LIVEVIEW_ALLOWED_MODULES = ["myapp.views"]

        cls = type(
            "V005AllowedView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
            },
        )

        try:
            errors = check_liveviews(None)
            v005 = [e for e in errors if e.id == "djust.V005"]
            assert not any("V005AllowedView" in e.msg for e in v005)
        finally:
            del cls
            _force_gc()

    def test_v005_no_setting_configured(self, settings):
        """V005 should not fire when LIVEVIEW_ALLOWED_MODULES is not set."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        # Remove the setting if it exists
        if hasattr(settings, "LIVEVIEW_ALLOWED_MODULES"):
            delattr(settings, "LIVEVIEW_ALLOWED_MODULES")

        cls = type(
            "V005NoSettingView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
            },
        )

        try:
            errors = check_liveviews(None)
            v005 = [e for e in errors if e.id == "djust.V005"]
            assert not any("V005NoSettingView" in e.msg for e in v005)
        finally:
            del cls
            _force_gc()


# ---------------------------------------------------------------------------
# Security checks - LiveView authentication (S005)
# ---------------------------------------------------------------------------


class TestS005UnauthenticatedViews:
    """S005 -- LiveView exposes state without authentication."""

    def test_s005_fires_when_auth_not_addressed(self):
        """S005 fires when neither login_required nor permission_required is set."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_configuration

        def mount(self, request, **kwargs):
            self.user_data = {"email": "test@example.com"}

        cls = type(
            "UnauthView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
                "mount": mount,
            },
        )

        try:
            errors = check_configuration(None)
            s005 = [e for e in errors if e.id == "djust.S005"]
            assert any("UnauthView" in e.msg for e in s005)
        finally:
            del cls
            _force_gc()

    def test_s005_suppressed_with_login_required_true(self):
        """S005 should not fire when login_required = True."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_configuration

        def mount(self, request, **kwargs):
            self.user_data = {"email": "test@example.com"}

        cls = type(
            "AuthRequiredView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
                "mount": mount,
                "login_required": True,
            },
        )

        try:
            errors = check_configuration(None)
            s005 = [e for e in errors if e.id == "djust.S005"]
            assert not any("AuthRequiredView" in e.msg for e in s005)
        finally:
            del cls
            _force_gc()

    def test_s005_suppressed_with_login_required_false(self):
        """S005 should not fire when login_required = False (intentionally public)."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_configuration

        def mount(self, request, **kwargs):
            self.public_data = {"version": "1.0"}

        cls = type(
            "PublicView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "test.html",
                "mount": mount,
                "login_required": False,  # Intentionally public
            },
        )

        try:
            errors = check_configuration(None)
            s005 = [e for e in errors if e.id == "djust.S005"]
            # After the fix, this should pass
            assert not any("PublicView" in e.msg for e in s005)
        finally:
            del cls
            _force_gc()


# ---------------------------------------------------------------------------
# Security checks (S001-S003) -- AST-based
# ---------------------------------------------------------------------------


class TestS001MarkSafeFString:
    """S001 -- mark_safe(f'...') with interpolated values."""

    def test_s001_detects_mark_safe_fstring(self, tmp_path):
        """S001 fires for mark_safe(f'...')."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.utils.safestring import mark_safe

                def render_tag(name):
                    return mark_safe(f'<div>{name}</div>')
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s001 = [e for e in errors if e.id == "djust.S001"]
        assert len(s001) == 1
        assert "mark_safe" in s001[0].msg
        assert "XSS" in s001[0].msg

    def test_s001_passes_format_html(self, tmp_path):
        """S001 should not fire for format_html() usage."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.utils.html import format_html

                def render_tag(name):
                    return format_html('<div>{}</div>', name)
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s001 = [e for e in errors if e.id == "djust.S001"]
        assert len(s001) == 0

    def test_s001_passes_mark_safe_plain_string(self, tmp_path):
        """S001 should not fire for mark_safe with a plain string (no f-string)."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.utils.safestring import mark_safe

                def render_tag():
                    return mark_safe('<div>static</div>')
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s001 = [e for e in errors if e.id == "djust.S001"]
        assert len(s001) == 0


class TestS002CsrfExempt:
    """S002 -- @csrf_exempt without justification."""

    def test_s002_csrf_exempt_no_justification(self, tmp_path):
        """S002 fires for @csrf_exempt without a docstring mentioning csrf."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.views.decorators.csrf import csrf_exempt

                @csrf_exempt
                def webhook(request):
                    return None
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s002 = [e for e in errors if e.id == "djust.S002"]
        assert len(s002) == 1
        assert "csrf_exempt" in s002[0].msg

    def test_s002_csrf_exempt_with_justification(self, tmp_path):
        """S002 should not fire when docstring mentions csrf."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.views.decorators.csrf import csrf_exempt

                @csrf_exempt
                def webhook(request):
                    \"\"\"CSRF exempt: external webhook from Stripe, verified by signature.\"\"\"
                    return None
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s002 = [e for e in errors if e.id == "djust.S002"]
        assert len(s002) == 0

    def test_s002_async_function(self, tmp_path):
        """S002 fires for async functions with @csrf_exempt too."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                from django.views.decorators.csrf import csrf_exempt

                @csrf_exempt
                async def async_webhook(request):
                    return None
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s002 = [e for e in errors if e.id == "djust.S002"]
        assert len(s002) == 1


class TestS003BareExceptPass:
    """S003 -- bare except: pass."""

    def test_s003_bare_except_pass(self, tmp_path):
        """S003 fires for bare except: pass."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                def do_something():
                    try:
                        risky()
                    except:
                        pass
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s003 = [e for e in errors if e.id == "djust.S003"]
        assert len(s003) == 1
        assert "bare" in s003[0].msg

    def test_s003_passes_specific_exception(self, tmp_path):
        """S003 should not fire for a specific exception type."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                def do_something():
                    try:
                        risky()
                    except ValueError:
                        pass
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s003 = [e for e in errors if e.id == "djust.S003"]
        assert len(s003) == 0

    def test_s003_passes_bare_except_with_logging(self, tmp_path):
        """S003 should not fire for bare except with body other than just pass."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                logger = logging.getLogger(__name__)

                def do_something():
                    try:
                        risky()
                    except:
                        logger.exception("Unexpected error")
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s003 = [e for e in errors if e.id == "djust.S003"]
        assert len(s003) == 0


# ---------------------------------------------------------------------------
# Template checks (T001-T003)
# ---------------------------------------------------------------------------


class TestT001DeprecatedAtSyntax:
    """T001 -- deprecated @click/@input syntax."""

    def test_t001_detects_at_click(self, tmp_path, settings):
        """T001 fires for @click= in templates."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "old.html").write_text('<button @click="handle_click">Go</button>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t001 = [e for e in errors if e.id == "djust.T001"]
        assert len(t001) == 1
        assert "@click" in t001[0].msg

    def test_t001_detects_at_input(self, tmp_path, settings):
        """T001 fires for @input= in templates."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "old.html").write_text('<input @input="handle_input">')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t001 = [e for e in errors if e.id == "djust.T001"]
        assert len(t001) == 1
        assert "@input" in t001[0].msg

    def test_t001_passes_dj_click(self, tmp_path, settings):
        """T001 should not fire for dj-click= (new syntax)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text('<button dj-click="handle_click">Go</button>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t001 = [e for e in errors if e.id == "djust.T001"]
        assert len(t001) == 0

    def test_t001_multiple_deprecated_attrs(self, tmp_path, settings):
        """T001 fires once per deprecated attribute occurrence."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "multi.html").write_text(
            '<button @click="go">Go</button>\n<input @change="update">'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t001 = [e for e in errors if e.id == "djust.T001"]
        assert len(t001) == 2


class TestT002MissingDjustRoot:
    """T002 -- LiveView template missing dj-root."""

    def test_t002_dj_attrs_no_root(self, tmp_path, settings):
        """T002 fires for template with dj-click but no dj-root and no extends."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_root.html").write_text('<div><button dj-click="go">Go</button></div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 1
        assert "dj-root" in t002[0].msg

    def test_t002_passes_with_root(self, tmp_path, settings):
        """T002 should not fire when dj-root is present."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "has_root.html").write_text(
            '<div dj-root><button dj-click="go">Go</button></div>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 0

    def test_t002_passes_with_extends(self, tmp_path, settings):
        """T002 should not fire when template extends a base (root likely in base)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "child.html").write_text(
            '{% extends "base.html" %}\n<button dj-click="go">Go</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 0


class TestT003IncludeInsteadOfLiveviewContent:
    """T003 -- wrapper template uses include instead of liveview_content|safe."""

    def test_t003_include_in_wrapper(self, tmp_path, settings):
        """T003 fires for wrapper template using include with liveview in path."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "wrapper.html").write_text(
            textwrap.dedent("""\
                {% block content %}
                    {% include "liveview_partial.html" %}
                {% endblock %}
            """)
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t003 = [e for e in errors if e.id == "djust.T003"]
        assert len(t003) == 1
        assert "include" in t003[0].msg

    def test_t003_passes_with_liveview_content(self, tmp_path, settings):
        """T003 should not fire when liveview_content|safe is used."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "wrapper.html").write_text(
            textwrap.dedent("""\
                <!-- liveview wrapper template -->
                {% block content %}
                    {{ liveview_content|safe }}
                {% endblock %}
            """)
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t003 = [e for e in errors if e.id == "djust.T003"]
        assert len(t003) == 0

    def test_t003_no_false_positive_for_unrelated_include(self, tmp_path, settings):
        """T003 should NOT fire when include path is unrelated (e.g. icons.svg)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "wrapper.html").write_text(
            textwrap.dedent("""\
                {% block content %}
                    {% include "icons.svg" %}
                    <div dj-click="increment">Click me</div>
                {% endblock %}
            """)
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t003 = [e for e in errors if e.id == "djust.T003"]
        assert len(t003) == 0

    def test_t003_noqa_suppresses_warning(self, tmp_path, settings):
        """T003 should be suppressed by {# noqa: T003 #} comment."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "wrapper.html").write_text(
            textwrap.dedent("""\
                {# noqa: T003 #}
                {% block content %}
                    {% include "liveview_partial.html" %}
                {% endblock %}
            """)
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t003 = [e for e in errors if e.id == "djust.T003"]
        assert len(t003) == 0


# ---------------------------------------------------------------------------
# Code Quality checks (Q001-Q003) -- AST-based
# ---------------------------------------------------------------------------


class TestQ001PrintStatement:
    """Q001 -- print() in production code."""

    def test_q001_detects_print(self, tmp_path):
        """Q001 fires for print() statements."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                def process():
                    print("debug output")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q001 = [e for e in errors if e.id == "djust.Q001"]
        assert len(q001) == 1
        assert "print()" in q001[0].msg

    def test_q001_passes_logger(self, tmp_path):
        """Q001 should not fire for logger calls."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                logger = logging.getLogger(__name__)

                def process():
                    logger.info("debug output")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q001 = [e for e in errors if e.id == "djust.Q001"]
        assert len(q001) == 0

    def test_q001_multiple_prints(self, tmp_path):
        """Q001 fires once per print() call."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                def process():
                    print("one")
                    print("two")
                    print("three")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q001 = [e for e in errors if e.id == "djust.Q001"]
        assert len(q001) == 3


class TestQ002FStringInLogger:
    """Q002 -- f-string in logger call."""

    def test_q002_detects_fstring_in_logger(self, tmp_path):
        """Q002 fires for logger.info(f'...')."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                logger = logging.getLogger(__name__)

                def process(user_id):
                    logger.info(f"Processing user {user_id}")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q002 = [e for e in errors if e.id == "djust.Q002"]
        assert len(q002) == 1
        assert "f-string" in q002[0].msg

    def test_q002_passes_percent_format(self, tmp_path):
        """Q002 should not fire for %-style formatting."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                logger = logging.getLogger(__name__)

                def process(user_id):
                    logger.info("Processing user %s", user_id)
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q002 = [e for e in errors if e.id == "djust.Q002"]
        assert len(q002) == 0

    def test_q002_detects_fstring_in_error_level(self, tmp_path):
        """Q002 fires for logger.error(f'...')."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                logger = logging.getLogger(__name__)

                def process(user_id):
                    logger.error(f"Failed for {user_id}")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q002 = [e for e in errors if e.id == "djust.Q002"]
        assert len(q002) == 1

    def test_q002_detects_log_alias(self, tmp_path):
        """Q002 fires for log.warning(f'...') (log alias)."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                import logging
                log = logging.getLogger(__name__)

                def process(user_id):
                    log.warning(f"Slow query for {user_id}")
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q002 = [e for e in errors if e.id == "djust.Q002"]
        assert len(q002) == 1


class TestQ003ConsoleLogWithoutGuard:
    """Q003 -- console.log without djustDebug guard in JS."""

    def test_q003_detects_unguarded_console_log(self, tmp_path):
        """Q003 fires for console.log without djustDebug guard."""
        js_file = tmp_path / "app.js"
        js_file.write_text(
            textwrap.dedent("""\
                function init() {
                    console.log("hello");
                }
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q003 = [e for e in errors if e.id == "djust.Q003"]
        assert len(q003) == 1
        assert "console.log" in q003[0].msg

    def test_q003_passes_with_djust_debug_guard(self, tmp_path):
        """Q003 should not fire when djustDebug guard is on same line."""
        js_file = tmp_path / "app.js"
        js_file.write_text(
            textwrap.dedent("""\
                function init() {
                    if (globalThis.djustDebug) console.log("hello");
                }
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q003 = [e for e in errors if e.id == "djust.Q003"]
        assert len(q003) == 0

    def test_q003_passes_with_djust_debug_on_previous_line(self, tmp_path):
        """Q003 should not fire when djustDebug guard is on the line above."""
        js_file = tmp_path / "app.js"
        js_file.write_text(
            textwrap.dedent("""\
                function init() {
                    if (globalThis.djustDebug) {
                        console.log("hello");
                    }
                }
            """)
        )

        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_code_quality(None)

        q003 = [e for e in errors if e.id == "djust.Q003"]
        assert len(q003) == 0


# ---------------------------------------------------------------------------
# Edge cases and multiple-check interaction tests
# ---------------------------------------------------------------------------


class TestSecurityCheckSkipsMigrations:
    """Security and quality checks should skip migrations/ directories."""

    def test_s001_ignores_migration_files(self, tmp_path):
        """AST checks should not scan files inside migrations/."""
        migrations_dir = tmp_path / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_initial.py").write_text(
            textwrap.dedent("""\
                from django.utils.safestring import mark_safe

                def forward(apps, schema_editor):
                    return mark_safe(f'<div>{"val"}</div>')
            """)
        )

        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            errors = check_security(None)

        s001 = [e for e in errors if e.id == "djust.S001"]
        assert len(s001) == 0


class TestNoAppDirsReturnsEmpty:
    """Checks return empty when no project app dirs are found."""

    def test_security_check_empty_dirs(self):
        """check_security returns empty list when no app dirs."""
        from djust.checks import check_security

        with patch("djust.checks._get_project_app_dirs", return_value=[]):
            errors = check_security(None)

        assert errors == []

    def test_code_quality_check_empty_dirs(self):
        """check_code_quality returns empty list when no app dirs."""
        from djust.checks import check_code_quality

        with patch("djust.checks._get_project_app_dirs", return_value=[]):
            errors = check_code_quality(None)

        assert errors == []


# ---------------------------------------------------------------------------
# DjustMiddlewareStack (Issue #265)
# ---------------------------------------------------------------------------


class TestDjustMiddlewareStack:
    """DjustMiddlewareStack wraps inner app with session middleware only."""

    def test_import_from_routing(self):
        """DjustMiddlewareStack is importable from djust.routing."""
        from djust.routing import DjustMiddlewareStack as DMS

        assert callable(DMS)

    def test_import_from_package(self):
        """DjustMiddlewareStack is importable from top-level djust package."""
        from djust import DjustMiddlewareStack as DMS

        assert callable(DMS)

    def test_wraps_with_session_middleware(self):
        """DjustMiddlewareStack wraps inner app with SessionMiddlewareStack."""
        from djust.routing import DjustMiddlewareStack

        class MockInnerApp:
            pass

        result = DjustMiddlewareStack(MockInnerApp)
        # SessionMiddlewareStack returns a middleware instance that has .inner
        cls_name = type(result).__name__
        mod_name = type(result).__module__ or ""
        assert "session" in cls_name.lower() or "session" in mod_name.lower()


# ---------------------------------------------------------------------------
# V006 -- Service instance detection in mount()
# ---------------------------------------------------------------------------


class TestV006ServiceInstanceInMount:
    """V006 -- detect service/client/session instantiation in mount()."""

    def test_v006_detects_service_in_mount(self, tmp_path):
        """V006 fires when self.service = SomeService() is in mount()."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.service = PaymentService()
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 1
        assert "service" in v006[0].msg
        assert "serialized" in v006[0].msg

    def test_v006_detects_boto3_client(self, tmp_path):
        """V006 fires when self.client = boto3.client(...) is in mount()."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class S3View:
                    def mount(self, request, **kwargs):
                        self.client = boto3.client('s3')
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 1
        assert "client" in v006[0].msg

    def test_v006_detects_session(self, tmp_path):
        """V006 fires for self.session = requests.Session()."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class ApiView:
                    def mount(self, request, **kwargs):
                        self.session = requests.Session()
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 1
        assert "session" in v006[0].msg

    def test_v006_passes_normal_assignment(self, tmp_path):
        """V006 should not fire for normal assignments like self.count = 0."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class CounterView:
                    def mount(self, request, **kwargs):
                        self.count = 0
                        self.items = list()
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 0

    def test_v006_passes_outside_mount(self, tmp_path):
        """V006 should not fire for service instances outside mount()."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.count = 0

                    def _get_service(self):
                        self.service = PaymentService()
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 0

    def test_v006_noqa_suppresses(self, tmp_path):
        """V006 should be suppressible with # noqa: V006."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.service = PaymentService()  # noqa: V006
            """)
        )

        from djust.checks import _check_service_instances_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_service_instances_in_mount(errors)

        v006 = [e for e in errors if e.id == "djust.V006"]
        assert len(v006) == 0


# ---------------------------------------------------------------------------
# V007 -- Event handler signature validation
# ---------------------------------------------------------------------------


class TestV007EventHandlerSignature:
    """V007 -- event handler missing **kwargs."""

    def test_v007_missing_kwargs(self):
        """V007 fires when @event_handler method lacks **kwargs."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.decorators import event_handler
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        @event_handler()
        def handle_click(self, item_id=0):
            pass

        cls = type(
            "V007NoKwargsView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "handle_click": handle_click,
            },
        )

        try:
            errors = check_liveviews(None)
            v007 = [e for e in errors if e.id == "djust.V007"]
            assert any("V007NoKwargsView" in e.msg and "handle_click" in e.msg for e in v007)
        finally:
            del cls
            _force_gc()

    def test_v007_passes_with_kwargs(self):
        """V007 should not fire when **kwargs is present."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.decorators import event_handler
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        @event_handler()
        def handle_click(self, item_id=0, **kwargs):
            pass

        cls = type(
            "V007WithKwargsView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "handle_click": handle_click,
            },
        )

        try:
            errors = check_liveviews(None)
            v007 = [e for e in errors if e.id == "djust.V007"]
            assert not any("V007WithKwargsView" in e.msg for e in v007)
        finally:
            del cls
            _force_gc()

    def test_v007_passes_with_event_alias(self):
        """V007 should not fire when **event is used instead of **kwargs."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.decorators import event_handler
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        @event_handler()
        def handle_click(self, **event):
            pass

        cls = type(
            "V007EventAliasView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "handle_click": handle_click,
            },
        )

        try:
            errors = check_liveviews(None)
            v007 = [e for e in errors if e.id == "djust.V007"]
            assert not any("V007EventAliasView" in e.msg for e in v007)
        finally:
            del cls
            _force_gc()

    def test_v007_ignores_non_event_handlers(self):
        """V007 should not fire for methods without @event_handler."""
        import pytest

        if not _liveview_available():
            pytest.skip("Rust extension not available")

        from djust.live_view import LiveView
        from djust.checks import check_liveviews

        def mount(self, request, **kwargs):
            pass

        def helper(self, item_id=0):
            pass

        cls = type(
            "V007NonHandlerView",
            (LiveView,),
            {
                "__module__": "myapp.views",
                "template_name": "t.html",
                "mount": mount,
                "helper": helper,
            },
        )

        try:
            errors = check_liveviews(None)
            v007 = [e for e in errors if e.id == "djust.V007"]
            assert not any("V007NonHandlerView" in e.msg for e in v007)
        finally:
            del cls
            _force_gc()


# ---------------------------------------------------------------------------
# V008 -- Non-primitive type assignments in mount()
# ---------------------------------------------------------------------------


class TestV008NonPrimitiveInMount:
    """V008 -- Detect non-primitive type assignments in mount()."""

    def test_non_primitive_instantiation_in_mount(self, tmp_path):
        """Warn when non-primitive types are instantiated in mount()."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.api_client = APIClient()
            """)
        )

        from djust.checks import _check_non_primitive_assignments_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_non_primitive_assignments_in_mount(errors)

        v008 = [e for e in errors if e.id == "djust.V008"]
        assert len(v008) == 1
        assert "APIClient" in v008[0].msg
        assert "api_client" in v008[0].msg

    def test_primitive_types_allowed(self, tmp_path):
        """Primitive type assignments don't trigger warning."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.items = []
                        self.count = 0
                        self.data = {}
                        self.name = "test"
            """)
        )

        from djust.checks import _check_non_primitive_assignments_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_non_primitive_assignments_in_mount(errors)

        v008 = [e for e in errors if e.id == "djust.V008"]
        # Should not flag primitive types
        assert len(v008) == 0

    def test_private_attributes_ignored(self, tmp_path):
        """Private attributes (self._foo) are ignored."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self._api_client = APIClient()
            """)
        )

        from djust.checks import _check_non_primitive_assignments_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_non_primitive_assignments_in_mount(errors)

        v008 = [e for e in errors if e.id == "djust.V008"]
        # Should not flag private attributes
        assert len(v008) == 0

    def test_noqa_suppresses_warning(self, tmp_path):
        """# noqa: V008 suppresses the warning."""
        py_file = tmp_path / "views.py"
        py_file.write_text(
            textwrap.dedent("""\
                class MyView:
                    def mount(self, request, **kwargs):
                        self.client = CustomClient()  # noqa: V008
            """)
        )

        from djust.checks import _check_non_primitive_assignments_in_mount

        errors = []
        with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
            _check_non_primitive_assignments_in_mount(errors)

        v008 = [e for e in errors if e.id == "djust.V008"]
        # Should be suppressed by noqa
        assert len(v008) == 0


# ---------------------------------------------------------------------------
# T005 -- Template structure validation (dj-view / dj-root)
# ---------------------------------------------------------------------------


class TestT005ViewRootSameElement:
    """T005 -- dj-view and dj-root on different elements."""

    def test_t005_detects_different_elements(self, tmp_path, settings):
        """T005 fires when dj-view and dj-root are on different elements."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<div dj-root>\n" '  <div dj-view="myapp.views.MyView">content</div>\n' "</div>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t005 = [e for e in errors if e.id == "djust.T005"]
        assert len(t005) == 1
        assert "different elements" in t005[0].msg

    def test_t005_passes_same_element(self, tmp_path, settings):
        """T005 should not fire when both attributes are on the same element."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text(
            '<div dj-root dj-view="myapp.views.MyView">content</div>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t005 = [e for e in errors if e.id == "djust.T005"]
        assert len(t005) == 0

    def test_t005_passes_no_view_attr(self, tmp_path, settings):
        """T005 should not fire when dj-view is not present."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_view.html").write_text(
            '<div dj-root><button dj-click="go">Go</button></div>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t005 = [e for e in errors if e.id == "djust.T005"]
        assert len(t005) == 0


# ---------------------------------------------------------------------------
# T002 enhanced -- Warning severity and dj-view detection
# ---------------------------------------------------------------------------


class TestT002Enhanced:
    """T002 enhanced -- Warning severity and dj-view without root."""

    def test_t002_is_warning_severity(self, tmp_path, settings):
        """T002 should be Info severity (since dj-root is now auto-inferred from dj-view)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_root.html").write_text('<div><button dj-click="go">Go</button></div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates, DjustInfo

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 1
        # Verify it is a DjustInfo (since PR #297, dj-root is auto-inferred)
        assert isinstance(t002[0], DjustInfo)

    def test_t002_detects_djust_view_without_root(self, tmp_path, settings):
        """T002 fires when dj-view is present but dj-root is missing."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "view_no_root.html").write_text(
            '<div dj-view="myapp.views.MyView">content</div>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 1
        assert "dj-root" in t002[0].msg

    def test_t002_improved_message(self, tmp_path, settings):
        """T002 message should mention auto-inferred dj-root."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_root.html").write_text('<div><button dj-click="go">Go</button></div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t002 = [e for e in errors if e.id == "djust.T002"]
        assert len(t002) == 1
        assert "auto-inferred" in t002[0].msg


class TestT010ClickForNavigation:
    """T010 -- dj-click used for navigation instead of dj-patch."""

    def test_t010_detects_click_with_data_view(self, tmp_path, settings):
        """T010 should flag dj-click with data-view attribute."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "nav_click.html").write_text(
            '<button dj-click="switchView" data-view="settings">Settings</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 1
        assert "dj-click" in t010[0].msg
        assert "dj-patch" in t010[0].hint

    def test_t010_detects_click_with_data_tab(self, tmp_path, settings):
        """T010 should flag dj-click with data-tab attribute."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "tab_click.html").write_text(
            '<button dj-click="selectTab" data-tab="profile">Profile</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 1
        assert "data-tab" in t010[0].msg

    def test_t010_detects_click_with_data_page(self, tmp_path, settings):
        """T010 should flag dj-click with data-page attribute."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page_click.html").write_text('<a dj-click="goToPage" data-page="2">Next</a>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 1
        assert "data-page" in t010[0].msg

    def test_t010_detects_click_with_data_section(self, tmp_path, settings):
        """T010 should flag dj-click with data-section attribute."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "section_click.html").write_text(
            '<button dj-click="showSection" data-section="about">About</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 1
        assert "data-section" in t010[0].msg

    def test_t010_passes_click_without_nav_data(self, tmp_path, settings):
        """T010 should NOT flag dj-click without navigation data attributes."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "normal_click.html").write_text(
            '<button dj-click="increment" data-count="5">Increment</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 0

    def test_t010_passes_patch_with_nav_data(self, tmp_path, settings):
        """T010 should NOT flag dj-patch with navigation data attributes (correct pattern)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "correct_patch.html").write_text(
            '<button dj-patch="/view?tab=settings" data-tab="settings">Settings</button>'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 0

    def test_t010_detects_multiple_violations(self, tmp_path, settings):
        """T010 should detect multiple violations in one file."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "multi_nav.html").write_text(
            textwrap.dedent(
                """
            <div>
                <button dj-click="switchView" data-view="home">Home</button>
                <button dj-click="selectTab" data-tab="profile">Profile</button>
                <button dj-click="showPage" data-page="3">Page 3</button>
            </div>
            """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t010 = [e for e in errors if e.id == "djust.T010"]
        assert len(t010) == 3


class TestQ010NavigationStateInHandlers:
    """Q010 -- event handlers that set navigation state without patching."""

    def test_q010_detects_active_view_in_handler(self, tmp_path):
        """Q010 should flag event handlers that set self.active_view."""
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "views.py").write_text(
            textwrap.dedent(
                """
            from djust import LiveView
            from djust.decorators import event_handler

            class MyView(LiveView):
                @event_handler()
                def switch_view(self, view_name="", **kwargs):
                    self.active_view = view_name
            """
            )
        )

        # Mock _get_project_app_dirs to return our test app
        with patch("djust.checks._get_project_app_dirs", return_value=[str(app_dir)]):
            from djust.checks import check_code_quality

            errors = check_code_quality(None)
            q010 = [e for e in errors if e.id == "djust.Q010"]
            assert len(q010) == 1
            assert "active_view" in q010[0].msg
            assert "dj-patch" in q010[0].hint

    def test_q010_detects_current_tab_in_handler(self, tmp_path):
        """Q010 should flag event handlers that set self.current_tab."""
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "views.py").write_text(
            textwrap.dedent(
                """
            from djust import LiveView
            from djust.decorators import event_handler

            class TabView(LiveView):
                @event_handler()
                def select_tab(self, tab="", **kwargs):
                    self.current_tab = tab
            """
            )
        )

        with patch("djust.checks._get_project_app_dirs", return_value=[str(app_dir)]):
            from djust.checks import check_code_quality

            errors = check_code_quality(None)
            q010 = [e for e in errors if e.id == "djust.Q010"]
            assert len(q010) == 1
            assert "current_tab" in q010[0].msg

    def test_q010_passes_handler_with_patch_usage(self, tmp_path):
        """Q010 should NOT flag handlers that use patch() or handle_params()."""
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "views.py").write_text(
            textwrap.dedent(
                """
            from djust import LiveView
            from djust.decorators import event_handler

            class GoodView(LiveView):
                @event_handler()
                def switch_view(self, view_name="", **kwargs):
                    self.patch(f"?view={view_name}")

                def handle_params(self, **params):
                    self.active_view = params.get("view", "home")
            """
            )
        )

        with patch("djust.checks._get_project_app_dirs", return_value=[str(app_dir)]):
            from djust.checks import check_code_quality

            errors = check_code_quality(None)
            q010 = [e for e in errors if e.id == "djust.Q010"]
            assert len(q010) == 0

    def test_q010_passes_non_event_handler(self, tmp_path):
        """Q010 should NOT flag methods without @event_handler decorator."""
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "views.py").write_text(
            textwrap.dedent(
                """
            from djust import LiveView

            class MyView(LiveView):
                def _internal_switch(self):
                    self.active_view = "new_view"
            """
            )
        )

        with patch("djust.checks._get_project_app_dirs", return_value=[str(app_dir)]):
            from djust.checks import check_code_quality

            errors = check_code_quality(None)
            q010 = [e for e in errors if e.id == "djust.Q010"]
            assert len(q010) == 0

    def test_q010_respects_noqa(self, tmp_path):
        """Q010 should respect # noqa: Q010 comments."""
        app_dir = tmp_path / "testapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        (app_dir / "views.py").write_text(
            textwrap.dedent(
                """
            from djust import LiveView
            from djust.decorators import event_handler

            class MyView(LiveView):
                @event_handler()  # noqa: Q010
                def switch_view(self, view_name="", **kwargs):
                    self.active_view = view_name
            """
            )
        )

        with patch("djust.checks._get_project_app_dirs", return_value=[str(app_dir)]):
            from djust.checks import check_code_quality

            errors = check_code_quality(None)
            q010 = [e for e in errors if e.id == "djust.Q010"]
            assert len(q010) == 0


class TestT012EventDirectivesWithoutView:
    """T012 -- template uses dj-* event directives but has no dj-view."""

    def test_t012_detects_events_without_view(self, tmp_path, settings):
        """T012 fires for template with dj-click but no dj-view."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_view.html").write_text(
            textwrap.dedent(
                """\
                <div>
                    <button dj-click="increment">+1</button>
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t012 = [e for e in errors if e.id == "djust.T012"]
        assert len(t012) == 1
        assert "dj-view" in t012[0].msg

    def test_t012_passes_with_view(self, tmp_path, settings):
        """T012 should not fire when dj-view is present."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "has_view.html").write_text(
            textwrap.dedent(
                """\
                <div dj-view="myapp.views.MyView">
                    <button dj-click="increment">+1</button>
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t012 = [e for e in errors if e.id == "djust.T012"]
        assert len(t012) == 0

    def test_t012_passes_for_component_template(self, tmp_path, settings):
        """T012 should not fire for component templates (dj-component present)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "component.html").write_text(
            textwrap.dedent(
                """\
                <div dj-component="myapp.components.Counter">
                    <button dj-click="increment">+1</button>
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t012 = [e for e in errors if e.id == "djust.T012"]
        assert len(t012) == 0


class TestT013InvalidViewPath:
    """T013 -- dj-view with empty or invalid value."""

    def test_t013_detects_empty_view(self, tmp_path, settings):
        """T013 fires for dj-view with empty value."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "empty_view.html").write_text('<div dj-view="">content</div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t013 = [e for e in errors if e.id == "djust.T013"]
        assert len(t013) == 1
        assert "empty or invalid" in t013[0].msg

    def test_t013_detects_no_dot(self, tmp_path, settings):
        """T013 fires for dj-view without a dotted path."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "no_dot.html").write_text('<div dj-view="MyView">content</div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t013 = [e for e in errors if e.id == "djust.T013"]
        assert len(t013) == 1
        assert "MyView" in t013[0].msg

    def test_t013_passes_valid_path(self, tmp_path, settings):
        """T013 should not fire for a valid dotted Python path."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "valid.html").write_text('<div dj-view="myapp.views.MyView">content</div>')
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t013 = [e for e in errors if e.id == "djust.T013"]
        assert len(t013) == 0


class TestT011UnsupportedTemplateTags:
    """T011 -- unsupported Django template tags in LiveView templates."""

    def test_t011_detects_unsupported_tag(self, tmp_path, settings):
        """T011 fires for tags not implemented in Rust renderer."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page.html").write_text(
            textwrap.dedent(
                """\
                <div dj-view="myapp.views.MyView">
                    {% ifchanged item.category %}
                        <h2>{{ item.category }}</h2>
                    {% endifchanged %}
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t011 = [e for e in errors if e.id == "djust.T011"]
        assert len(t011) >= 1
        assert "ifchanged" in t011[0].msg

    def test_t011_does_not_fire_for_supported_tags(self, tmp_path, settings):
        """T011 should not fire for tags implemented in Rust (widthratio, etc.)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page.html").write_text(
            textwrap.dedent(
                """\
                <div dj-view="myapp.views.MyView">
                    {% widthratio value max_val 100 %}
                    {% firstof var1 var2 "fallback" %}
                    {% templatetag openblock %}
                    {% spaceless %}<p> </p>{% endspaceless %}
                    {% cycle "a" "b" "c" %}
                    {% now "Y-m-d" %}
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t011 = [e for e in errors if e.id == "djust.T011"]
        assert len(t011) == 0

    def test_t011_noqa_suppresses_warning(self, tmp_path, settings):
        """T011 is suppressed by {# noqa: T011 #} comment."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page.html").write_text(
            textwrap.dedent(
                """\
                {# noqa: T011 #}
                <div dj-view="myapp.views.MyView">
                    {% regroup items by category as grouped %}
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t011 = [e for e in errors if e.id == "djust.T011"]
        assert len(t011) == 0

    def test_t011_multiple_unsupported_tags(self, tmp_path, settings):
        """T011 fires once per unsupported tag found."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "page.html").write_text(
            textwrap.dedent(
                """\
                <div dj-view="myapp.views.MyView">
                    {% regroup items by category as grouped %}
                    {% lorem 3 p %}
                    {% debug %}
                </div>
                """
            )
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t011 = [e for e in errors if e.id == "djust.T011"]
        assert len(t011) == 3
