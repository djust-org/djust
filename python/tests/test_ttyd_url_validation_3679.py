"""``TtydTerminalView`` refuses a ``?url=`` that names an endpoint nobody allowed (#3679).

The view hands the browser a WebSocket URL and the hook connects the terminal to
it: what the user types goes to that endpoint and what it answers is drawn as
terminal output. Before #3679 the URL came straight from ``request.GET["url"]``,
so a link chose the endpoint (a fake "password:" prompt that receives the
keystrokes). The fix keeps ``?url=`` as a dev convenience but only for an origin
the view allows: the class's own ``ttyd_url`` plus ``ttyd_allowed_origins``.

What each class proves:

* ``TestRefused`` -- the reproducer: every encoded / alternate form of an
  off-allowlist target ends up as the CONFIGURED url, in the mounted state AND in
  the rendered ``data-ttyd-url`` attribute (the sink the browser reads).
* ``TestAccepted`` -- the allowlist is not a wall: the configured origin, an
  allowlisted origin, a path on it, and its uppercase spelling all still work, so
  a refusal test cannot pass merely because everything is refused.
* ``TestAttributeSink`` -- a quote in an accepted path cannot break out of the
  attribute (the rebuilt value is escaped by the template).

Refs #3679, #3665, #1825 (validation that runs before a decode is defeated by the
encoded form, so the corpus carries the encoded forms), #1468.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote

import pytest
from django.test import RequestFactory

from djust.components.ttyd import TtydTerminalView

pytestmark = pytest.mark.pr_tier

CONFIGURED = "ws://localhost:7681"


class AllowlistedView(TtydTerminalView):
    ttyd_url = "ws://localhost:7681"
    ttyd_allowed_origins = ("wss://shell.example.com", "ws://10.0.0.5:7000", "ws://[::1]:7000")


def _mount(view_cls, query: str) -> TtydTerminalView:
    request = RequestFactory().get("/shell/" + query)
    view = view_cls()
    view.request = request
    view.mount(request)
    return view


def _get(view_cls, query: str) -> str:
    """The HTML of a real GET through ``as_view()`` (what the browser is sent)."""
    from django.contrib.sessions.backends.cache import SessionStore

    request = RequestFactory().get("/shell/" + query)
    request.session = SessionStore()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    assert response.status_code == 200
    return response.content.decode()


def _rendered_url(view_cls, query: str) -> str:
    """The ``data-ttyd-url`` attribute of the real GET response (what the browser reads)."""
    html = _get(view_cls, query)
    found = re.findall(r'data-ttyd-url="([^"]*)"', html)
    assert len(found) == 1, html
    return unescape(found[0])


#: Off-allowlist targets, in the shapes a link would carry. Each must be ignored.
REFUSED = [
    ("plain wss host", "wss://evil.example.com/ws"),
    ("plain ws host", "ws://evil.example.com:7681"),
    ("http scheme", "http://localhost:7681"),
    ("https scheme", "https://localhost:7681"),
    ("javascript scheme", "javascript:alert(1)"),
    ("data scheme", "data:text/html,<script>alert(1)</script>"),
    ("file scheme", "file:///etc/passwd"),
    ("no scheme, host only", "localhost:7681"),
    ("scheme-relative", "//evil.example.com/ws"),
    ("scheme-relative to the allowed host", "//localhost:7681/ws"),
    ("same host, other port", "ws://localhost:9999"),
    ("same host, wrong scheme for the port", "wss://localhost:7681"),
    ("userinfo host trick", "ws://localhost:7681@evil.example.com/ws"),
    ("userinfo naming the allowed host", "ws://localhost:7681:x@evil.example.com/"),
    ("allowed host as a subdomain of evil", "ws://localhost.evil.example.com:7681"),
    ("allowed host as a prefix of evil", "ws://localhost7681.evil.example.com"),
    (
        "backslash userinfo (a browser reads it as a slash)",
        "ws://evil.example.com\\@localhost:7681",
    ),
    ("backslash before the path", "ws://evil.example.com\\.localhost:7681"),
    ("tab inside the scheme", "w\ts://evil.example.com"),
    ("leading space", " ws://evil.example.com"),
    ("newline in the url", "ws://localhost:7681\nws://evil.example.com"),
    ("control character", "ws://evil.example.com\x00"),
    ("IPv6 literal", "ws://[::1]:7681"),
    ("IPv6 literal, allowed port", "ws://[2001:db8::1]:7681/ws"),
    ("percent-encoded host dot", "ws://evil%2eexample.com"),
    ("percent-encoded scheme", "%77s://evil.example.com"),
    ("fullwidth dot host", "ws://evil\u3002example.com"),
    ("empty host", "ws://"),
    ("empty value", ""),
]


class TestRefused:
    @pytest.mark.parametrize("label,target", REFUSED, ids=[r[0] for r in REFUSED])
    def test_an_unallowed_target_is_ignored_in_the_mounted_state(self, label, target):
        view = _mount(TtydTerminalView, "?url=" + quote(target, safe=""))
        assert view.ttyd_url == CONFIGURED, (label, target, view.ttyd_url)

    @pytest.mark.parametrize("label,target", REFUSED, ids=[r[0] for r in REFUSED])
    def test_an_unallowed_target_never_reaches_the_rendered_attribute(self, label, target):
        assert _rendered_url(TtydTerminalView, "?url=" + quote(target, safe="")) == CONFIGURED

    def test_the_default_allowlist_is_only_the_views_own_endpoint(self):
        """An allowlisted origin of a SUBCLASS is not allowed on the base view."""
        view = _mount(TtydTerminalView, "?url=" + quote("wss://shell.example.com/ws", safe=""))
        assert view.ttyd_url == CONFIGURED

    def test_the_allowlist_is_exact_on_scheme_host_and_port(self):
        for target in (
            "ws://shell.example.com",  # allowed host, scheme downgraded
            "wss://shell.example.com:8443",  # allowed host, other port
            "wss://shell.example.com.evil.example.com",
            "ws://10.0.0.5:7001",  # allowed host, other port
            "wss://10.0.0.5:7000",  # allowed host and port, other scheme
        ):
            view = _mount(AllowlistedView, "?url=" + quote(target, safe=""))
            assert view.ttyd_url == AllowlistedView.ttyd_url, target

    def test_a_malformed_allowlist_entry_allows_nothing(self):
        class Bad(TtydTerminalView):
            ttyd_allowed_origins = ("evil.example.com", "javascript:alert(1)", "", None, 7)

        for target in ("ws://evil.example.com", "javascript:alert(1)", "ws://"):
            assert _mount(Bad, "?url=" + quote(target, safe="")).ttyd_url == CONFIGURED

    def test_a_refusal_is_logged_without_echoing_the_value(self, caplog):
        secret = "wss://evil.example.com/ws?token=hunter2"
        with caplog.at_level("WARNING", logger="djust.components.ttyd.terminal"):
            _mount(TtydTerminalView, "?url=" + quote(secret, safe=""))
        assert caplog.records, "a refused ?url= is silent"
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "hunter2" not in text and "evil.example.com" not in text


#: Forms that NAME an allowed host but are malformed. They differ from ``REFUSED``
#: in what a lax parser would do: not "connect to evil" but "accept and rebuild as
#: the allowed origin". Against ``AllowlistedView`` that rebuilt value
#: (``wss://shell.example.com...``) is not the fallback, so a refusal and a silent
#: normalisation are told apart; on the default view they would both read as
#: ``CONFIGURED`` (the survivors of the first gate-off run, M5/M6/M7).
MALFORMED_ON_AN_ALLOWED_HOST = [
    ("userinfo", "wss://evil@shell.example.com/ws"),
    ("userinfo with password", "wss://user:pw@shell.example.com/ws"),
    ("empty userinfo", "wss://@shell.example.com/ws"),
    ("backslash in the path", "wss://shell.example.com/ws\\evil"),
    ("backslash before the path", "wss://shell.example.com\\ws"),
    ("space in the path", "wss://shell.example.com/ws x"),
    ("control character in the path", "wss://shell.example.com/ws\x01"),
    ("DEL in the path", "wss://shell.example.com/ws\x7f"),
    ("non-ASCII in the path", "wss://shell.example.com/ws\u00e9"),
    ("non-ASCII look-alike dot in the host", "wss://shell\u3002example.com/ws"),
    ("leading space", " wss://shell.example.com/ws"),
    ("trailing space", "wss://shell.example.com/ws "),
    ("over-long value", "wss://shell.example.com/" + "a" * 2100),
    # A query would reach a ttyd started with --url-arg as command-line arguments.
    ("query string", "wss://shell.example.com/ws?arg=-x"),
    ("query on the bare origin", "wss://shell.example.com?arg=-x"),
    ("query after a fragment-looking part", "wss://shell.example.com/ws?a=1#b"),
    ("trailing dot on the host", "wss://shell.example.com./ws"),
]


class TestMalformedOnAnAllowedHost:
    @pytest.mark.parametrize(
        "label,target",
        MALFORMED_ON_AN_ALLOWED_HOST,
        ids=[r[0] for r in MALFORMED_ON_AN_ALLOWED_HOST],
    )
    def test_is_refused_not_normalised(self, label, target):
        # The control: the same origin with a clean path IS accepted, so the
        # fallback below can only be a refusal.
        clean = quote("wss://shell.example.com/ws", safe="")
        assert _mount(AllowlistedView, "?url=" + clean).ttyd_url == "wss://shell.example.com/ws"
        query = "?url=" + quote(target, safe="")
        assert _mount(AllowlistedView, query).ttyd_url == AllowlistedView.ttyd_url, label
        assert _rendered_url(AllowlistedView, query) == AllowlistedView.ttyd_url, label


class TestAccepted:
    def test_no_url_param_uses_the_configured_endpoint(self):
        assert _mount(TtydTerminalView, "").ttyd_url == CONFIGURED
        assert _rendered_url(TtydTerminalView, "") == CONFIGURED

    def test_restating_the_configured_endpoint_is_accepted(self):
        view = _mount(TtydTerminalView, "?url=" + quote("ws://localhost:7681/ws", safe=""))
        assert view.ttyd_url == "ws://localhost:7681/ws"

    def test_the_uppercase_spelling_of_an_allowed_origin_is_accepted_and_normalised(self):
        view = _mount(AllowlistedView, "?url=" + quote("WSS://Shell.Example.COM/ws", safe=""))
        assert view.ttyd_url == "wss://shell.example.com/ws"

    @pytest.mark.parametrize(
        "target,expected",
        [
            ("wss://shell.example.com", "wss://shell.example.com"),
            ("wss://shell.example.com:443/ws", "wss://shell.example.com/ws"),
            ("ws://10.0.0.5:7000/ws", "ws://10.0.0.5:7000/ws"),
            ("ws://[::1]:7000/ws", "ws://[::1]:7000/ws"),
            ("wss://shell.example.com/ws#frag", "wss://shell.example.com/ws"),
            ("wss://shell.example.com/a/../b", "wss://shell.example.com/a/../b"),
            ("wss://shell.example.com/ws?", "wss://shell.example.com/ws"),
        ],
    )
    def test_an_allowlisted_origin_is_accepted_in_state_and_in_the_attribute(
        self, target, expected
    ):
        query = "?url=" + quote(target, safe="")
        assert _mount(AllowlistedView, query).ttyd_url == expected
        assert _rendered_url(AllowlistedView, query) == expected

    def test_a_bare_string_allowlist_is_one_entry_not_a_set_of_characters(self):
        class OneString(TtydTerminalView):
            ttyd_allowed_origins = "wss://shell.example.com"  # type: ignore[assignment]

        query = "?url=" + quote("wss://shell.example.com/ws", safe="")
        assert _mount(OneString, query).ttyd_url == "wss://shell.example.com/ws"
        assert _mount(OneString, "?url=" + quote("wss://evil.example.com", safe="")).ttyd_url == (
            CONFIGURED
        )

    def test_the_other_query_params_still_work(self):
        view = _mount(TtydTerminalView, "?rows=30&cols=100")
        assert (view.rows, view.cols) == (30, 100)

    def test_a_subclass_ttyd_url_is_trusted_not_validated(self):
        """Author code is not an attacker: a subclass may name any endpoint."""

        class Deploy(TtydTerminalView):
            ttyd_url = "wss://logs.internal.example:7682/ws"

        assert _mount(Deploy, "").ttyd_url == "wss://logs.internal.example:7682/ws"
        assert _rendered_url(Deploy, "") == "wss://logs.internal.example:7682/ws"


class TestAttributeSink:
    def test_a_quote_in_an_accepted_path_cannot_break_out_of_the_attribute(self):
        target = 'ws://10.0.0.5:7000/ws"onmouseover="alert(1)'
        html = _get(AllowlistedView, "?url=" + quote(target, safe=""))
        assert 'data-ttyd-url="' in html
        assert 'onmouseover="alert(1)' not in html


class TestSubclassOwnUrl:
    def test_a_subclass_ttyd_url_may_carry_a_query_and_a_request_cannot(self):
        class WithToken(TtydTerminalView):
            ttyd_url = "wss://logs.internal.example/ws?token=abc"

        assert _mount(WithToken, "").ttyd_url == "wss://logs.internal.example/ws?token=abc"
        # The view's own origin is allowed, but not a query a link supplies.
        query = "?url=" + quote("wss://logs.internal.example/ws?token=evil", safe="")
        assert _mount(WithToken, query).ttyd_url == "wss://logs.internal.example/ws?token=abc"
