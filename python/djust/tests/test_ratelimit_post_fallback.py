"""The per-handler ``@rate_limit`` is enforced on the page-POST fallback too.

F27 unified the per-handler ``@rate_limit`` onto one shared per-caller store and
stated the invariant as "ONE ``@rate_limit`` budget per handler regardless of
connection count or **transport**". Three transports were wired to it — the
WebSocket/SSE chokepoint (``websocket_utils._validate_event_security``) and the
HTTP API (``api/dispatch._rate_limit_check``) — and the page-POST fallback
(``mixins/request.post``) was missed, so a handler throttled everywhere else
accepted unbounded calls over POST.

These tests drive the real ``post()`` path (the same harness
``python/tests/test_http_fallback.py`` uses: a real session attached to the
request, plus an initial ``get()`` to establish state) rather than calling the
check helper directly, so they fail if the wiring is removed even though the
helper still works.
"""

from __future__ import annotations

import sys

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from djust import LiveView
from djust.decorators import event_handler, rate_limit
from djust.rate_limit import reset_handler_buckets


def _add_session(request):
    """Attach a real session so ``caller_key`` resolves to ``session:<key>``."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


@pytest.fixture(autouse=True)
def _clear_shared_store():
    """Each test starts with an empty shared per-caller bucket store."""
    reset_handler_buckets()
    yield
    reset_handler_buckets()


class _PostFallbackThrottled(LiveView):
    """A view whose handler declares ``@rate_limit(rate=1, burst=2)``.

    ``rate=1, burst=2`` makes the accepted count deterministic inside one test
    window: a token refills per second, so at most 3 calls could ever be
    admitted by accident, and only 2 within the assertion's own duration.
    """

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._PostFallbackThrottled" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    @rate_limit(rate=1, burst=2)
    def expensive_send(self, **kwargs):
        self.calls += 1


class _PostFallbackUnthrottled(LiveView):
    """A view whose handler declares no ``@rate_limit`` — must stay callable."""

    template = (
        '<div dj-view="djust.tests.test_ratelimit_post_fallback._PostFallbackUnthrottled" '
        'dj-id="0">x</div>'
    )

    def mount(self, request, **kwargs):
        self.calls = 0

    @event_handler()
    def cheap_read(self, **kwargs):
        self.calls += 1


# The view classes are referenced by name inside their own ``dj-view`` template,
# so the module must expose them.
setattr(sys.modules[__name__], "_PostFallbackThrottled", _PostFallbackThrottled)
setattr(sys.modules[__name__], "_PostFallbackUnthrottled", _PostFallbackUnthrottled)


def _drive_post(view, factory, session_request, event, n):
    """POST ``event`` n times through the real fallback.

    Returns ``(ran, statuses)`` where ``ran`` counts the 200s — the calls the
    server admitted.
    """
    ran = 0
    statuses = []
    for _ in range(n):
        request = factory.post(
            "/test/",
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
        request.session = session_request.session
        response = view.post(request)
        statuses.append(response.status_code)
        if response.status_code == 200:
            ran += 1
    return ran, statuses


def _fresh(request_cls):
    factory = RequestFactory()
    session_request = _add_session(factory.get("/test/"))
    view = request_cls()
    view.get(session_request)
    return view, factory, session_request


@pytest.mark.django_db
def test_rate_limit_is_enforced_on_the_post_fallback():
    """burst=2: calls 3..6 must be refused, not run."""
    view, factory, session_request = _fresh(_PostFallbackThrottled)

    ran, statuses = _drive_post(view, factory, session_request, "expensive_send", 6)

    assert ran == 2, (
        "the shared per-handler budget must cap the page-POST fallback at burst "
        f"(2); admitted {ran}/6 (statuses={statuses})"
    )
    # The handler ran exactly as often as the server admitted, so the 200s are
    # not an artefact of something else answering.
    assert view.calls == ran
    assert statuses[-1] == 429
    assert statuses[-4:] == [429, 429, 429, 429]


@pytest.mark.django_db
def test_the_post_fallback_budget_is_the_same_bucket_as_the_http_api():
    """The F27 invariant: one budget per (caller, handler) across transports.

    Burn the burst over POST, then ask the HTTP API's check for the same caller
    and handler. If the two transports kept separate buckets, the API would
    still admit calls — which is exactly the "summing across transports" defect
    F27 removed.
    """
    from djust.api.dispatch import _rate_limit_check

    view, factory, session_request = _fresh(_PostFallbackThrottled)

    ran, _ = _drive_post(view, factory, session_request, "expensive_send", 2)
    assert ran == 2, "the burst should have been admitted over POST"

    handler = _PostFallbackThrottled.expensive_send
    assert not _rate_limit_check(session_request, "expensive_send", handler), (
        "the HTTP API must see the budget the POST path just consumed — a "
        "separate bucket means a caller can sum allowances across transports"
    )


@pytest.mark.django_db
def test_a_distinct_caller_gets_its_own_budget():
    """The cap is per caller, not a global switch: a second caller is admitted."""
    view, factory, session_request = _fresh(_PostFallbackThrottled)
    ran_first, _ = _drive_post(view, factory, session_request, "expensive_send", 4)
    assert ran_first == 2

    other_view, other_factory, other_session = _fresh(_PostFallbackThrottled)
    ran_second, _ = _drive_post(other_view, other_factory, other_session, "expensive_send", 2)

    assert ran_second == 2, (
        "a second caller must get the full burst; a shared bucket would be a "
        "mutual-denial bug rather than a per-caller limit"
    )


@pytest.mark.django_db
def test_a_handler_without_a_rate_limit_is_unaffected():
    """No regression for the ordinary case: no decorator, no cap."""
    view, factory, session_request = _fresh(_PostFallbackUnthrottled)

    ran, statuses = _drive_post(view, factory, session_request, "cheap_read", 6)

    assert ran == 6, f"an undecorated handler must not be throttled; got {statuses}"
