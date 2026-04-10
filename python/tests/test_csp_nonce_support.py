"""
Tests for nonce-based CSP support (#655).

djust emits several inline ``<script>`` and ``<style>`` tags during render:
the handler metadata bootstrap, the ``live_session`` route map, and the PWA
service worker / offline tags. Before #655 none of them carried a CSP nonce,
so downstream apps had to allow ``'unsafe-inline'`` in ``CSP_SCRIPT_SRC`` and
``CSP_STYLE_SRC``. After #655 each emission site reads ``request.csp_nonce``
and emits ``nonce="..."`` when a nonce is available — while staying backward
compatible (no attribute when no nonce, same as pre-fix behavior).
"""

from unittest.mock import MagicMock

from django.template import Context, Template
from django.test import RequestFactory

from djust.routing import get_route_map_script, live_session
from djust.utils import get_csp_nonce


# ---------------------------------------------------------------------------
# get_csp_nonce() helper
# ---------------------------------------------------------------------------


class TestGetCspNonceHelper:
    def test_none_request(self):
        assert get_csp_nonce(None) == ""

    def test_request_without_nonce_attr(self):
        req = RequestFactory().get("/")
        # django-csp middleware not installed — csp_nonce is absent
        assert get_csp_nonce(req) == ""

    def test_request_with_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = "abc123"  # type: ignore[attr-defined]
        assert get_csp_nonce(req) == "abc123"

    def test_request_with_empty_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = ""  # type: ignore[attr-defined]
        assert get_csp_nonce(req) == ""

    def test_request_with_none_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = None  # type: ignore[attr-defined]
        assert get_csp_nonce(req) == ""

    def test_context_with_request(self):
        """Helper can unwrap a Context-like object carrying a request."""
        req = RequestFactory().get("/")
        req.csp_nonce = "xyz789"  # type: ignore[attr-defined]
        wrapper = MagicMock()
        wrapper.request = req
        assert get_csp_nonce(wrapper) == "xyz789"


# ---------------------------------------------------------------------------
# routing.get_route_map_script()
# ---------------------------------------------------------------------------


class TestRouteMapScriptNonce:
    """``get_route_map_script`` must emit a nonce when the request carries one."""

    def setup_method(self):
        # Stash any existing route maps so we can restore them.
        self._saved = getattr(live_session, "_route_maps", None)
        live_session._route_maps = {
            "test": [("/dashboard", "myapp.views.DashboardView")],
        }

    def teardown_method(self):
        if self._saved is None:
            if hasattr(live_session, "_route_maps"):
                delattr(live_session, "_route_maps")
        else:
            live_session._route_maps = self._saved

    def test_no_request_no_nonce(self):
        """No request → plain <script> tag, same as pre-#655 behavior."""
        out = get_route_map_script()
        assert out.startswith("<script>")
        assert "nonce=" not in out
        assert "window.djust._routeMap" in out

    def test_request_without_nonce_attr_no_nonce(self):
        req = RequestFactory().get("/")
        out = get_route_map_script(req)
        assert out.startswith("<script>")
        assert "nonce=" not in out

    def test_request_with_nonce_emits_nonce_attr(self):
        req = RequestFactory().get("/")
        req.csp_nonce = "N0nce123"  # type: ignore[attr-defined]
        out = get_route_map_script(req)
        assert 'nonce="N0nce123"' in out
        assert "window.djust._routeMap" in out

    def test_empty_nonce_omits_attribute(self):
        """Empty-string nonce → no attribute (not nonce="")."""
        req = RequestFactory().get("/")
        req.csp_nonce = ""  # type: ignore[attr-defined]
        out = get_route_map_script(req)
        assert 'nonce="' not in out

    def test_no_routes_returns_empty(self):
        """No route maps → empty string, unchanged by #655."""
        live_session._route_maps = {}
        out = get_route_map_script(RequestFactory().get("/"))
        assert out == ""


# ---------------------------------------------------------------------------
# _inject_handler_metadata in the LiveView render path
# ---------------------------------------------------------------------------


class TestInjectHandlerMetadataNonce:
    """``_inject_handler_metadata`` must thread ``request.csp_nonce``."""

    def _make_view_with_metadata(self):
        """Build a minimal object that behaves like a LiveView for the injector."""
        from djust.mixins.template import TemplateMixin

        class _View(TemplateMixin):
            def __init__(self):
                pass

            def _extract_handler_metadata(self):
                return {"my_handler": {"lock": True}}

        return _View()

    def test_no_request_no_nonce(self):
        view = self._make_view_with_metadata()
        html = "<html><body>hi</body></html>"
        out = view._inject_handler_metadata(html, request=None)
        assert "<script>" in out
        assert 'nonce="' not in out
        assert "window.handlerMetadata" in out

    def test_request_with_nonce_emits_nonce_attr(self):
        view = self._make_view_with_metadata()
        req = RequestFactory().get("/")
        req.csp_nonce = "Bootstrap42"  # type: ignore[attr-defined]
        out = view._inject_handler_metadata("<html><body>hi</body></html>", request=req)
        assert 'nonce="Bootstrap42"' in out
        assert "window.handlerMetadata" in out

    def test_request_without_nonce_attr_no_nonce(self):
        view = self._make_view_with_metadata()
        req = RequestFactory().get("/")
        out = view._inject_handler_metadata("<html><body>hi</body></html>", request=req)
        assert 'nonce="' not in out

    def test_self_request_fallback(self):
        """When no request arg is passed, falls back to ``self.request``."""
        view = self._make_view_with_metadata()
        req = RequestFactory().get("/")
        req.csp_nonce = "SelfReq"  # type: ignore[attr-defined]
        view.request = req  # type: ignore[attr-defined]
        out = view._inject_handler_metadata("<html><body>hi</body></html>")
        assert 'nonce="SelfReq"' in out


# ---------------------------------------------------------------------------
# PWA template tags (djust_sw_register, djust_offline_indicator,
# djust_offline_styles)
# ---------------------------------------------------------------------------


class TestPwaTagNonce:
    """The PWA template tags must emit nonces when the context has a request."""

    def _render(self, template_src: str, request=None) -> str:
        tpl = Template("{% load djust_pwa %}" + template_src)
        ctx = {"request": request} if request is not None else {}
        return tpl.render(Context(ctx))

    def test_sw_register_without_request(self):
        out = self._render("{% djust_sw_register %}")
        assert out.startswith("<script>")
        assert 'nonce="' not in out
        assert "navigator.serviceWorker" in out

    def test_sw_register_with_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = "SWnonce"  # type: ignore[attr-defined]
        out = self._render("{% djust_sw_register %}", request=req)
        assert 'nonce="SWnonce"' in out
        assert "navigator.serviceWorker" in out

    def test_sw_register_request_without_nonce_attr(self):
        req = RequestFactory().get("/")
        out = self._render("{% djust_sw_register %}", request=req)
        assert 'nonce="' not in out

    def test_offline_indicator_without_request(self):
        out = self._render("{% djust_offline_indicator %}")
        assert "<style>" in out
        assert 'nonce="' not in out
        assert "djust-offline-indicator" in out

    def test_offline_indicator_with_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = "IndNonce"  # type: ignore[attr-defined]
        out = self._render("{% djust_offline_indicator %}", request=req)
        assert 'nonce="IndNonce"' in out

    def test_offline_styles_without_request(self):
        out = self._render("{% djust_offline_styles %}")
        assert "<style>" in out
        assert 'nonce="' not in out

    def test_offline_styles_with_nonce(self):
        req = RequestFactory().get("/")
        req.csp_nonce = "StylesNonce"  # type: ignore[attr-defined]
        out = self._render("{% djust_offline_styles %}", request=req)
        assert 'nonce="StylesNonce"' in out
