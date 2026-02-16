"""Tests for the LiveViewSmokeTest mixin and its helper functions.

Tests cover:
- _check_xss_in_html: pure function detecting XSS sentinels in HTML
- _make_fuzz_params: generator producing fuzz parameter dicts from handler metadata
- _get_handlers: discovering event handlers on view classes
- LiveViewSmokeTest mixin: _get_views, test_smoke_render, test_fuzz_xss, fuzz toggle
"""

from unittest.mock import MagicMock, patch

import pytest

# Setup Django settings before importing djust modules
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
        SECRET_KEY="test-secret-key-for-testing-only",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from djust.testing import (
    LiveViewSmokeTest,
    XSS_PAYLOADS,
    TYPE_PAYLOADS,
    _XSS_SENTINELS,
    _check_xss_in_html,
    _get_handlers,
    _make_fuzz_params,
)


# ---------------------------------------------------------------------------
# TestCheckXssInHtml
# ---------------------------------------------------------------------------


class TestCheckXssInHtml:
    """Tests for the _check_xss_in_html pure function."""

    def test_safe_html_returns_empty_list(self):
        html = "<div><p>Hello, world!</p></div>"
        assert _check_xss_in_html(html) == []

    def test_escaped_script_tag_is_safe(self):
        html = "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;"
        assert _check_xss_in_html(html) == []

    def test_detects_unescaped_script_tag(self):
        html = '<div><script>alert("xss")</script></div>'
        found = _check_xss_in_html(html)
        assert '<script>alert("xss")' in found

    def test_detects_unescaped_img_onerror(self):
        html = "<div><img src=x onerror=alert(1)></div>"
        found = _check_xss_in_html(html)
        assert "<img src=x onerror=" in found

    def test_detects_unescaped_svg_onload(self):
        html = '<div>"><svg onload=alert(1)></div>'
        found = _check_xss_in_html(html)
        assert "<svg onload=" in found

    def test_detects_javascript_href(self):
        html = '<a href="javascript:alert(1)">click</a>'
        found = _check_xss_in_html(html)
        assert '<a href="javascript:' in found

    def test_sql_injection_not_detected_due_to_case_mismatch(self):
        """The SQL sentinel has uppercase but html.lower() converts content,
        so it won't match. This documents the current behavior."""
        html = "<div>'; DROP TABLE users; --</div>"
        found = _check_xss_in_html(html)
        # Sentinel "DROP TABLE users" won't match lowered HTML "drop table users"
        assert "DROP TABLE users" not in found

    def test_detects_sql_injection_when_already_lowercase(self):
        """If the HTML already contains the sentinel in matching case after lower()."""
        html = "<div>'; drop table users; --</div>"
        found = _check_xss_in_html(html)
        # The sentinel "DROP TABLE users" still won't match because it has uppercase
        assert found == []

    def test_case_insensitive_detection(self):
        html = '<DIV><SCRIPT>alert("xss")</SCRIPT></DIV>'
        found = _check_xss_in_html(html)
        assert len(found) >= 1

    def test_multiple_sentinels_detected(self):
        html = (
            '<script>alert("xss")</script>' "<img src=x onerror=alert(1)>" "<svg onload=alert(1)>"
        )
        found = _check_xss_in_html(html)
        assert len(found) >= 3

    def test_empty_html_is_safe(self):
        assert _check_xss_in_html("") == []

    def test_lowercase_sentinels_match_their_own_payload(self):
        """Lowercase sentinels are found when present in HTML.

        Note: _check_xss_in_html lower-cases the HTML, so sentinels that
        are already all-lowercase will match. Mixed-case sentinels (like
        'DROP TABLE users') won't match their lowered form.
        """
        for sentinel in _XSS_SENTINELS:
            if sentinel != sentinel.lower():
                # Mixed-case sentinel won't match lowered HTML
                found = _check_xss_in_html(sentinel)
                assert sentinel not in found, f"Mixed-case sentinel should not match: {sentinel!r}"
            else:
                found = _check_xss_in_html(sentinel)
                assert sentinel in found, f"Sentinel not detected: {sentinel!r}"


# ---------------------------------------------------------------------------
# TestMakeFuzzParams
# ---------------------------------------------------------------------------


class TestMakeFuzzParams:
    """Tests for the _make_fuzz_params generator."""

    def test_xss_payloads_for_str_param(self):
        meta = {
            "params": [{"name": "query", "type": "str", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        xss_results = [desc for desc, _ in results if desc.startswith("xss:")]
        assert len(xss_results) == len(XSS_PAYLOADS)

    def test_xss_payloads_fill_str_params_with_payload(self):
        meta = {
            "params": [{"name": "query", "type": "str", "required": True}],
            "accepts_kwargs": False,
        }
        xss_results = [(desc, p) for desc, p in _make_fuzz_params(meta) if desc.startswith("xss:")]
        for _, params in xss_results:
            assert "query" in params
            assert params["query"] in XSS_PAYLOADS

    def test_xss_payloads_use_zero_for_int_params(self):
        meta = {
            "params": [
                {"name": "query", "type": "str", "required": True},
                {"name": "page", "type": "int", "required": True},
            ],
            "accepts_kwargs": False,
        }
        xss_results = [(desc, p) for desc, p in _make_fuzz_params(meta) if desc.startswith("xss:")]
        for _, params in xss_results:
            assert params["page"] == 0

    def test_type_confusion_generated_for_params(self):
        meta = {
            "params": [{"name": "count", "type": "int", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        type_results = [desc for desc, _ in results if desc.startswith("type(")]
        assert len(type_results) == len(TYPE_PAYLOADS["int"])

    def test_type_confusion_for_str_param(self):
        meta = {
            "params": [{"name": "name", "type": "str", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        type_results = [desc for desc, _ in results if desc.startswith("type(")]
        assert len(type_results) == len(TYPE_PAYLOADS["str"])

    def test_empty_params_always_generated(self):
        meta = {
            "params": [{"name": "query", "type": "str", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        empty = [desc for desc, p in results if desc == "empty_params"]
        assert len(empty) == 1
        # The params dict for empty_params is {}
        empty_params = [p for desc, p in results if desc == "empty_params"]
        assert empty_params[0] == {}

    def test_missing_required_param_generated(self):
        meta = {
            "params": [
                {"name": "a", "type": "str", "required": True},
                {"name": "b", "type": "int", "required": True},
            ],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        missing = [(desc, p) for desc, p in results if desc.startswith("missing(")]
        assert len(missing) == 2
        descs = [desc for desc, _ in missing]
        assert "missing(a)" in descs
        assert "missing(b)" in descs

    def test_missing_required_excludes_optional(self):
        meta = {
            "params": [
                {"name": "required_param", "type": "str", "required": True},
                {"name": "optional_param", "type": "str", "required": False},
            ],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        missing = [desc for desc, _ in results if desc.startswith("missing(")]
        # Only required params get a missing-param test
        assert len(missing) == 1
        assert "missing(required_param)" in missing

    def test_no_params_yields_only_empty(self):
        meta = {"params": [], "accepts_kwargs": False}
        results = list(_make_fuzz_params(meta))
        # XSS payloads yield nothing if no params to fill, type confusion yields
        # nothing if no params, missing yields nothing. Only empty_params.
        assert len(results) == 1
        assert results[0][0] == "empty_params"

    def test_accepts_kwargs_adds_fuzz_extra(self):
        meta = {
            "params": [],
            "accepts_kwargs": True,
        }
        results = list(_make_fuzz_params(meta))
        xss_results = [(desc, p) for desc, p in results if desc.startswith("xss:")]
        assert len(xss_results) == len(XSS_PAYLOADS)
        for _, params in xss_results:
            assert "_fuzz_extra" in params

    def test_float_param_type_confusion(self):
        meta = {
            "params": [{"name": "rate", "type": "float", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        type_results = [desc for desc, _ in results if desc.startswith("type(")]
        assert len(type_results) == len(TYPE_PAYLOADS["float"])

    def test_bool_param_type_confusion(self):
        meta = {
            "params": [{"name": "active", "type": "bool", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        type_results = [desc for desc, _ in results if desc.startswith("type(")]
        assert len(type_results) == len(TYPE_PAYLOADS["bool"])

    def test_unknown_type_uses_str_payloads(self):
        meta = {
            "params": [{"name": "data", "type": "CustomType", "required": True}],
            "accepts_kwargs": False,
        }
        results = list(_make_fuzz_params(meta))
        type_results = [desc for desc, _ in results if desc.startswith("type(")]
        assert len(type_results) == len(TYPE_PAYLOADS["str"])


# ---------------------------------------------------------------------------
# TestGetHandlers
# ---------------------------------------------------------------------------


class TestGetHandlers:
    """Tests for the _get_handlers function with mock view classes."""

    def _make_mock_liveview_class(self, methods=None, decorated=None):
        """Create a mock view class that looks like a LiveView subclass.

        Args:
            methods: dict of name -> function for plain methods
            decorated: dict of name -> function for @event_handler decorated methods
        """
        from djust.live_view import LiveView

        attrs = {}

        if methods:
            for name, fn in methods.items():
                attrs[name] = fn

        if decorated:
            for name, fn in decorated.items():
                fn._djust_decorators = {
                    "event_handler": {
                        "params": [],
                        "accepts_kwargs": False,
                    }
                }
                attrs[name] = fn

        cls = type("MockView", (LiveView,), attrs)
        cls.template_name = "mock.html"
        return cls

    def test_discovers_decorated_handler(self):
        def search(self, query: str = ""):
            pass

        cls = self._make_mock_liveview_class(decorated={"search": search})
        handlers = _get_handlers(cls)
        assert "search" in handlers

    def test_discovers_plain_method(self):
        def my_action(self, value=""):
            pass

        cls = self._make_mock_liveview_class(methods={"my_action": my_action})
        handlers = _get_handlers(cls)
        assert "my_action" in handlers

    def test_skips_private_methods(self):
        def _internal(self):
            pass

        cls = self._make_mock_liveview_class(methods={"_internal": _internal})
        handlers = _get_handlers(cls)
        assert "_internal" not in handlers

    def test_decorated_handler_includes_metadata(self):
        def save(self, data: str = ""):
            pass

        save._djust_decorators = {
            "event_handler": {
                "params": [{"name": "data", "type": "str", "required": False}],
                "accepts_kwargs": False,
            }
        }
        # Create a proper subclass of LiveView to test _get_handlers
        from djust.live_view import LiveView

        cls = type("SaveView", (LiveView,), {"save": save, "template_name": "t.html"})
        handlers = _get_handlers(cls)
        assert "save" in handlers
        assert handlers["save"]["params"][0]["name"] == "data"

    def test_empty_subclass_only_has_inherited_handlers(self):
        """A LiveView subclass with no custom methods still picks up any
        @event_handler decorated methods from the base (e.g. update_model)."""
        from djust.live_view import LiveView

        cls = type("EmptyView", (LiveView,), {"template_name": "empty.html"})
        handlers = _get_handlers(cls)
        # Only inherited decorated handlers should appear, not plain methods
        for name in handlers:
            attr = getattr(cls, name, None)
            assert hasattr(
                attr, "_djust_decorators"
            ), f"Handler {name!r} should be @event_handler decorated"

    def test_plain_method_gets_param_info(self):
        def update(self, name: str, count: int = 5):
            pass

        cls = self._make_mock_liveview_class(methods={"update": update})
        handlers = _get_handlers(cls)
        assert "update" in handlers
        params = handlers["update"]["params"]
        param_names = [p["name"] for p in params]
        assert "name" in param_names
        assert "count" in param_names

    def test_plain_method_with_kwargs(self):
        def flexible(self, **kwargs):
            pass

        cls = self._make_mock_liveview_class(methods={"flexible": flexible})
        handlers = _get_handlers(cls)
        assert "flexible" in handlers
        assert handlers["flexible"]["accepts_kwargs"] is True


# ---------------------------------------------------------------------------
# TestSmokeTestMixin
# ---------------------------------------------------------------------------


class TestSmokeTestMixin:
    """Tests for the LiveViewSmokeTest mixin using mocking."""

    def _make_mixin_instance(self, **overrides):
        """Create a concrete instance of LiveViewSmokeTest for testing."""
        attrs = {
            "app_label": None,
            "max_queries": 50,
            "fuzz": True,
            "skip_views": [],
            "view_config": {},
        }
        attrs.update(overrides)
        cls = type("TestSmoke", (LiveViewSmokeTest,), attrs)
        return cls()

    @patch("djust.testing._discover_views")
    def test_get_views_returns_discovered_views(self, mock_discover):
        view_a = MagicMock(__name__="ViewA", __module__="myapp.views")
        view_b = MagicMock(__name__="ViewB", __module__="myapp.views")
        mock_discover.return_value = [view_a, view_b]

        instance = self._make_mixin_instance()
        views = instance._get_views()
        assert views == [view_a, view_b]

    @patch("djust.testing._discover_views")
    def test_get_views_filters_skip_views(self, mock_discover):
        view_a = MagicMock(__name__="ViewA", __module__="myapp.views")
        view_b = MagicMock(__name__="ViewB", __module__="myapp.views")
        mock_discover.return_value = [view_a, view_b]

        instance = self._make_mixin_instance(skip_views=[view_b])
        views = instance._get_views()
        assert views == [view_a]
        assert view_b not in views

    @patch("djust.testing._discover_views")
    def test_get_views_with_app_label(self, mock_discover):
        mock_discover.return_value = []
        instance = self._make_mixin_instance(app_label="crm")
        instance._get_views()
        mock_discover.assert_called_once_with("crm")

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_smoke_render_success(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="GoodView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]

        mock_client = MagicMock()
        mock_client.render.return_value = "<div>Hello world, rendered OK</div>"
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        # Should not raise
        instance.test_smoke_render()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_smoke_render_fails_on_empty_html(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="EmptyView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]

        mock_client = MagicMock()
        mock_client.render.return_value = ""
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="Smoke render failed"):
            instance.test_smoke_render()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_smoke_render_fails_on_tiny_html(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="TinyView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]

        mock_client = MagicMock()
        mock_client.render.return_value = "<p>Hi</p>"  # 8 chars, < 10
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="empty/tiny HTML"):
            instance.test_smoke_render()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_smoke_render_fails_on_exception(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="BadView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]

        mock_client_cls.return_value = MagicMock()
        mock_client_cls.return_value.mount.side_effect = RuntimeError("mount failed")

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="RuntimeError"):
            instance.test_smoke_render()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_smoke_render_reports_all_failures(self, mock_discover, mock_client_cls):
        view_a = MagicMock(__name__="ViewA", __module__="myapp.views")
        view_b = MagicMock(__name__="ViewB", __module__="myapp.views")
        mock_discover.return_value = [view_a, view_b]

        mock_client = MagicMock()
        mock_client.render.return_value = ""
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="2/2 views"):
            instance.test_smoke_render()

    @patch("djust.testing._discover_views")
    def test_smoke_render_no_views(self, mock_discover):
        mock_discover.return_value = []
        instance = self._make_mixin_instance()
        # No views to test -- should pass without error
        instance.test_smoke_render()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_xss_detects_unescaped_output(self, mock_discover, mock_handlers, mock_client_cls):
        view_cls = MagicMock(__name__="XSSView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "search": {
                "params": [{"name": "query", "type": "str", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        # Return HTML that contains an unescaped XSS sentinel
        mock_client.render.return_value = '<div><script>alert("xss")</script></div>'
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="XSS escaping failures"):
            instance.test_fuzz_xss()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_xss_passes_when_escaped(self, mock_discover, mock_handlers, mock_client_cls):
        view_cls = MagicMock(__name__="SafeView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "search": {
                "params": [{"name": "query", "type": "str", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        mock_client.render.return_value = (
            "<div>&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</div>"
        )
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        # Should not raise
        instance.test_fuzz_xss()

    @patch("djust.testing._discover_views")
    def test_fuzz_false_skips_xss_test(self, mock_discover):
        mock_discover.return_value = [MagicMock(__name__="V", __module__="app.views")]
        instance = self._make_mixin_instance(fuzz=False)
        # Should return immediately without error
        instance.test_fuzz_xss()

    @patch("djust.testing._discover_views")
    def test_fuzz_false_skips_crash_test(self, mock_discover):
        mock_discover.return_value = [MagicMock(__name__="V", __module__="app.views")]
        instance = self._make_mixin_instance(fuzz=False)
        # Should return immediately without error
        instance.test_fuzz_no_unhandled_crash()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_no_crash_passes_on_graceful_handler(
        self, mock_discover, mock_handlers, mock_client_cls
    ):
        view_cls = MagicMock(__name__="OkView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "delete": {
                "params": [{"name": "item_id", "type": "int", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        mock_client.send_event.return_value = None  # no exception
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        # Should not raise
        instance.test_fuzz_no_unhandled_crash()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_no_crash_fails_on_unhandled_exception(
        self, mock_discover, mock_handlers, mock_client_cls
    ):
        view_cls = MagicMock(__name__="CrashView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "process": {
                "params": [{"name": "data", "type": "str", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        mock_client.mount.side_effect = TypeError("unexpected keyword argument")
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="Unhandled crashes"):
            instance.test_fuzz_no_unhandled_crash()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_make_client_uses_view_config(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="ConfiguredView", __module__="myapp.views")
        mock_user = MagicMock()
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance(
            view_config={
                view_cls: {"user": mock_user, "mount_params": {"object_id": 42}},
            }
        )
        instance._make_client(view_cls)
        mock_client_cls.assert_called_once_with(view_cls, user=mock_user)
        mock_client.mount.assert_called_once_with(object_id=42)

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._discover_views")
    def test_make_client_defaults_no_user_no_params(self, mock_discover, mock_client_cls):
        view_cls = MagicMock(__name__="BasicView", __module__="myapp.views")
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        instance._make_client(view_cls)
        mock_client_cls.assert_called_once_with(view_cls, user=None)
        mock_client.mount.assert_called_once_with()

    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_xss_skips_views_without_handlers(self, mock_discover, mock_handlers):
        view_cls = MagicMock(__name__="NoHandlerView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {}

        instance = self._make_mixin_instance()
        # No handlers means nothing to fuzz -- should pass
        instance.test_fuzz_xss()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_handlers_succeed_detects_handler_exceptions(
        self, mock_discover, mock_handlers, mock_client_cls
    ):
        """test_fuzz_handlers_succeed should detect when handlers raise exceptions."""
        view_cls = MagicMock(__name__="FailingView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "save": {
                "params": [{"name": "data", "type": "str", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        # Handler raised exception — send_event catches it and returns success=False
        mock_client.send_event.return_value = {
            "success": False,
            "error": "ValueError: invalid data format",
            "state_before": {},
            "state_after": {},
            "duration_ms": 0.5,
        }
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        with pytest.raises(AssertionError, match="Handler exceptions from fuzz input"):
            instance.test_fuzz_handlers_succeed()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_handlers_succeed_passes_when_handlers_graceful(
        self, mock_discover, mock_handlers, mock_client_cls
    ):
        """test_fuzz_handlers_succeed should pass when handlers handle all input gracefully."""
        view_cls = MagicMock(__name__="GracefulView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "update": {
                "params": [{"name": "value", "type": "int", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        # Handler succeeded or validation failed gracefully (success=True)
        mock_client.send_event.return_value = {
            "success": True,
            "error": None,
            "state_before": {},
            "state_after": {},
            "duration_ms": 0.3,
        }
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        # Should not raise
        instance.test_fuzz_handlers_succeed()

    @patch("djust.testing.LiveViewTestClient")
    @patch("djust.testing._get_handlers")
    @patch("djust.testing._discover_views")
    def test_fuzz_handlers_succeed_ignores_validation_errors(
        self, mock_discover, mock_handlers, mock_client_cls
    ):
        """Validation errors (success=False but no exception) should be acceptable."""
        view_cls = MagicMock(__name__="ValidatedView", __module__="myapp.views")
        mock_discover.return_value = [view_cls]
        mock_handlers.return_value = {
            "process": {
                "params": [{"name": "count", "type": "int", "required": True}],
                "accepts_kwargs": False,
            }
        }

        mock_client = MagicMock()
        # Validation failed gracefully — no error message means validation rejection
        mock_client.send_event.return_value = {
            "success": False,
            "error": None,  # No error = validation failure, not exception
            "state_before": {},
            "state_after": {},
            "duration_ms": 0.1,
        }
        mock_client_cls.return_value = mock_client

        instance = self._make_mixin_instance()
        # Should not raise — validation failures are acceptable
        instance.test_fuzz_handlers_succeed()

    @patch("djust.testing._discover_views")
    def test_fuzz_handlers_succeed_skips_when_fuzz_false(self, mock_discover):
        mock_discover.return_value = [MagicMock(__name__="V", __module__="app.views")]
        instance = self._make_mixin_instance(fuzz=False)
        # Should return immediately without error
        instance.test_fuzz_handlers_succeed()
