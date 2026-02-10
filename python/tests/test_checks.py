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
    """T002 -- LiveView template missing data-djust-root."""

    def test_t002_dj_attrs_no_root(self, tmp_path, settings):
        """T002 fires for template with dj-click but no data-djust-root and no extends."""
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
        assert "data-djust-root" in t002[0].msg

    def test_t002_passes_with_root(self, tmp_path, settings):
        """T002 should not fire when data-djust-root is present."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "has_root.html").write_text(
            '<div data-djust-root><button dj-click="go">Go</button></div>'
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
        """T003 fires for wrapper template using include + mentions liveview."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "wrapper.html").write_text(
            textwrap.dedent("""\
                <!-- liveview wrapper template -->
                {% block content %}
                    {% include "partial.html" %}
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
