"""#4198: an explicit view's events are authorized against the stored session, every turn.

The ``reauth_on_event`` / ``reauth_server_turn_interval`` tests of the legacy
mechanism are gone (#4198): they re-checked legacy views only. An explicit view
needs no switch, because every turn is authorized fresh
(``authorize_explicit_turn``), from the session the server stores, not from
the user the connection cached when it opened. These tests pin that for the
transports and shapes the deleted cases covered and no explicit test did:

* an SSE client event after the session is revoked (the owner binding refuses it),
* an SSE client event from the same user whose check now denies (the turn's authorization),
* an event for a ``dj-lazy`` slot view after the session is revoked,
* an event that reads the user from the stored session, not the socket's copy,
* an ``async def check_permissions`` that runs before every event and fails
  closed when it denies or raises (the explicit arms of
  ``test_async_check_permissions.py`` run one event; this runs several).

Every case sends one event first and asserts the handler ran, so a refusal
cannot be a view that never worked (the control).
"""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import include, path

from djust import LiveView, event_handler, sse
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]

RAN: list = []


class Page(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root><b>page</b></div>"

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("page")

    @event_handler()
    def who(self, **kwargs):
        RAN.append(self.request.user.get_username())


class Widget(Page):
    template = "<div><b>widget</b></div>"

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("widget")


VERDICT: dict = {"value": True}
CHECKS: list = []
GATE: dict = {"gate": None}


class Checked(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root><b>checked</b></div>"

    async def check_permissions(self, request):
        CHECKS.append(1)
        gate = GATE["gate"]
        if gate is not None:
            await gate.wait()
        value = VERDICT["value"]
        if isinstance(value, BaseException):
            raise value
        return value

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("checked")


urlpatterns = [
    path("p/", Page.as_view()),
    path("c/", Checked.as_view()),
    path("djust/", include(sse_urlpatterns)),
]

SETTINGS = {
    "ROOT_URLCONF": __name__,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": [__name__],
}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    RAN.clear()
    CHECKS.clear()
    GATE["gate"] = None
    VERDICT["value"] = True
    with override_settings(**SETTINGS):
        yield


def _user(name):
    return get_user_model().objects.create_user(name, password="x")


def _log_session_in_as(session, user):
    session["_auth_user_id"] = str(user.pk)
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.save()


async def _signed_in_page(name="u4198"):
    user = await sync_to_async(_user)(name)
    return user, await sync_to_async(open_live_page)("/p/", user=user)


async def test_an_sse_event_from_a_revoked_session_fails_the_owner_binding():
    # The SSE endpoint binds the stream to its owner's session; a deleted session
    # is refused there (403), before any turn runs. Same-user denial is below.
    _, page = await _signed_in_page()
    async with await page.connect_sse() as stream:
        assert (await stream.send_event("bump"))["type"] != "error"
        assert RAN == ["page"]  # the control: the handler runs while the session lives
        await sync_to_async(page.session.delete)()
        # The message POST is refused outright: 403 permission_denied.
        with pytest.raises(AssertionError, match="answered 403.*permission_denied"):
            await stream.send_event("bump")
    assert RAN == ["page"], "the handler ran for a revoked session"


async def test_a_ws_slot_views_event_on_a_revoked_session_is_refused():
    _, page = await _signed_in_page("slot4198")
    async with await page.connect() as socket:
        await socket.send(
            {"type": "mount", "view": __name__ + ".Widget", "url": "/p/", "target_id": "w1"}
        )
        mounted = await socket.receive({"mount", "error", "view_refused"})
        assert mounted["type"] == "mount", mounted
        assert (await socket.send_event("bump", target_id="w1"))["type"] != "error"
        assert RAN == ["widget"]  # the control: the slot's handler runs while the session lives
        await sync_to_async(page.session.delete)()
        answer = await socket.send_event("bump", target_id="w1")
    assert answer["type"] in {"error", "view_refused"}, answer
    assert RAN == ["widget"], "the slot's handler ran for a revoked session"


async def test_an_event_reads_the_user_from_the_stored_session():
    _, page = await _signed_in_page("first4198")
    other = await sync_to_async(_user)("second4198")
    async with await page.connect() as socket:
        assert (await socket.send_event("who"))["type"] != "error"
        assert RAN == ["first4198"]  # the control: the handler runs for the mounted user
        # Log the browser's session in as another user, in the database only:
        # the socket still holds the user it opened with. The event is judged
        # against the stored session, so it is not run as the old user.
        await sync_to_async(_log_session_in_as)(page.session, other)
        answer = await socket.send_event("who")
    assert answer["type"] == "error", answer
    assert RAN == ["first4198"], "the handler ran as the socket's cached user"


async def test_an_async_check_permissions_runs_before_every_event():
    user = await sync_to_async(_user)("check4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect() as socket:
        mounted = len(CHECKS)  # the mount's own check
        for _ in range(3):
            assert (await socket.send_event("bump"))["type"] != "error"
        assert RAN == ["checked"] * 3
        assert len(CHECKS) == mounted + 3  # one fresh check per event
        VERDICT["value"] = False
        answer = await socket.send_event("bump")
    assert answer["type"] == "error", answer
    assert RAN == ["checked"] * 3, "a denied check let the handler run"


async def test_an_async_check_permissions_that_raises_fails_closed_without_its_text():
    user = await sync_to_async(_user)("raise4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect() as socket:
        assert (await socket.send_event("bump"))["type"] != "error"
        VERDICT["value"] = RuntimeError("CHECK_SECRET_SENTINEL")
        answer = await socket.send_event("bump")
    assert answer["type"] == "error", answer
    assert "CHECK_SECRET_SENTINEL" not in str(answer)
    assert RAN == ["checked"], "a raising check let the handler run"


async def test_an_sse_event_from_the_same_user_is_refused_when_the_check_denies():
    user = await sync_to_async(_user)("ssecheck4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect_sse() as stream:
        assert (await stream.send_event("bump"))["type"] != "error"
        assert RAN == ["checked"]  # the control: the same stream runs the handler
        VERDICT["value"] = False
        try:
            answer = await stream.send_event("bump")
        except AssertionError as refused:
            answer = {"type": "error", "text": str(refused)}
    assert answer["type"] == "error", answer
    assert RAN == ["checked"], "a denied check let the SSE handler run"


async def test_an_sse_slot_views_event_after_the_session_is_revoked_is_refused():
    _, page = await _signed_in_page("sseslot4198")
    async with await page.connect_sse() as stream:
        await stream.send(
            {"type": "mount", "view": __name__ + ".Widget", "url": "/p/", "target_id": "w1"}
        )
        mounted = await stream.receive({"mount", "error", "view_refused"})
        assert mounted["type"] == "mount", mounted
        assert (await stream.send_event("bump", target_id="w1"))["type"] != "error"
        assert RAN == ["widget"]  # the control
        await sync_to_async(page.session.delete)()
        with pytest.raises(AssertionError, match="answered 403.*permission_denied"):
            await stream.send_event("bump", target_id="w1")
    assert RAN == ["widget"], "the slot's handler ran for a revoked session"


async def _free_sync_thread(timeout=0.5):
    """Whether asgiref's shared sync thread can run work right now."""
    try:
        await asyncio.wait_for(sync_to_async(lambda: None)(), timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _two_events_behind_a_gate(socket, final_verdict):
    """Open the check gate after two events are sent; returns (ran while held, frames)."""
    gate = asyncio.Event()
    GATE["gate"] = gate
    for ref in (101, 102):
        await socket.send({"type": "event", "event": "bump", "params": {}, "ref": ref})
    await asyncio.sleep(0.3)
    ran_while_held = list(RAN)
    thread_free = await _free_sync_thread()
    VERDICT["value"] = final_verdict
    gate.set()
    await asyncio.sleep(0.5)
    return ran_while_held, thread_free


async def test_a_pending_async_check_leaves_the_shared_sync_thread_free():
    user = await sync_to_async(_user)("free4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect() as socket:
        ran_while_held, thread_free = await _two_events_behind_a_gate(socket, True)
    assert thread_free, "an async per-event check must not hold the shared sync thread"
    assert ran_while_held == []


async def test_a_second_event_waits_behind_a_pending_check_and_gets_its_own():
    user = await sync_to_async(_user)("wait4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect() as socket:
        mounted = len(CHECKS)
        ran_while_held, _ = await _two_events_behind_a_gate(socket, True)
        assert ran_while_held == [], "an event ran before its check answered"
    assert RAN == ["checked", "checked"]
    assert len(CHECKS) == mounted + 2  # one check per event, not shared


async def test_a_refusal_after_the_wait_blocks_both_events():
    user = await sync_to_async(_user)("deny4198")
    page = await sync_to_async(open_live_page)("/c/", user=user)
    async with await page.connect() as socket:
        await _two_events_behind_a_gate(socket, False)
    assert RAN == [], "a check that denied after waiting let a handler run"
