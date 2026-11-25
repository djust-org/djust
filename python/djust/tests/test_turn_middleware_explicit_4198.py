"""#4198: where an explicit view's refusals sit relative to ``LIVEVIEW_MIDDLEWARE``.

The legacy ``reauth_on_event`` built-in ran ahead of app middleware; its test
(``test_failed_reauth_refuses_before_app_middleware_and_body``) is gone with it.
An explicit view has no such built-in. The documented chain (turns.py) runs
``check_view_auth`` *inside* app middleware, so two different things are pinned:

* a same-user denial (``check_permissions`` now False) passes through app
  middleware, the handler does not run, and the answer is ``permission_denied``;
* a deleted session never gets that far: the SSE endpoint's owner binding
  (``_request_owns_session``) refuses the POST with 403 before any turn starts.

Each case sends one event first and asserts the handler ran and middleware saw
it, so a refusal cannot be a view that never worked (the control).
"""

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import include, path

from djust import LiveView, event_handler, sse
from djust.sse import sse_urlpatterns
from djust.testing import open_live_page

pytestmark = [pytest.mark.django_db(transaction=True)]

SEEN: list = []
RAN: list = []
VERDICT: dict = {"value": True}


async def probe(turn, next):
    SEEN.append(turn.kind)
    return await next(turn)


class Page(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root><b>page</b></div>"

    def check_permissions(self, request):
        return VERDICT["value"]

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("bump")


urlpatterns = [path("p/", Page.as_view()), path("djust/", include(sse_urlpatterns))]


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    SEEN.clear()
    RAN.clear()
    VERDICT["value"] = True
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        LIVEVIEW_MIDDLEWARE=[f"{__name__}.probe"],
    ):
        yield


async def test_a_denied_turn_for_the_same_user_passes_app_middleware_and_runs_no_handler():
    user = await sync_to_async(get_user_model().objects.create_user)("deny4198", password="x")
    page = await sync_to_async(open_live_page)("/p/", user=user)
    async with await page.connect() as socket:
        assert (await socket.send_event("bump"))["type"] != "error"
        assert RAN == ["bump"]
        assert "event" in SEEN  # the control: middleware wraps the turn while it is allowed
        seen_before = len(SEEN)
        VERDICT["value"] = False
        answer = await socket.send_event("bump")
    assert answer["type"] == "error" and answer.get("code") == "permission_denied", answer
    assert RAN == ["bump"], "the handler ran for a denied turn"
    assert len(SEEN) > seen_before, "the denied turn did not pass through app middleware"


async def test_a_revoked_session_fails_the_sse_owner_binding_before_any_turn():
    user = await sync_to_async(get_user_model().objects.create_user)("owner4198", password="x")
    page = await sync_to_async(open_live_page)("/p/", user=user)
    async with await page.connect_sse() as stream:
        assert (await stream.send_event("bump"))["type"] != "error"
        assert RAN == ["bump"]
        seen_before = list(SEEN)
        await sync_to_async(page.session.delete)()
        with pytest.raises(AssertionError, match="answered 403.*permission_denied"):
            await stream.send_event("bump")
    assert RAN == ["bump"], "the handler ran for a revoked session"
    assert SEEN == seen_before, "a turn started for a request that is not the stream's owner"
