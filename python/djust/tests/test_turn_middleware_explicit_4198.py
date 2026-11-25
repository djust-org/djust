"""#4198: a revoked session is refused before app middleware runs (explicit view).

The legacy ``reauth_on_event`` built-in sat inside the chain, ahead of app
middleware; its tests (``test_failed_reauth_refuses_before_app_middleware_and_body``)
are gone with it. An explicit view is authorized by ``authorize_explicit_turn``
on every turn, so the same ordering guarantee is pinned here end to end: a
``LIVEVIEW_MIDDLEWARE`` entry sees the turn while the session lives (control),
and sees nothing, and the handler does not run, once it is revoked.
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


async def probe(turn, next):
    SEEN.append(turn.kind)
    return await next(turn)


class Page(LiveView):
    exposure_policy = "explicit"
    login_required = True
    template = "<div dj-root><b>page</b></div>"

    @event_handler()
    def bump(self, **kwargs):
        RAN.append("bump")


urlpatterns = [path("p/", Page.as_view()), path("djust/", include(sse_urlpatterns))]


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    SEEN.clear()
    RAN.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        LIVEVIEW_MIDDLEWARE=[f"{__name__}.probe"],
    ):
        yield


async def test_a_revoked_session_never_reaches_app_middleware_or_the_handler():
    user = await sync_to_async(get_user_model().objects.create_user)("mw4198", password="x")
    page = await sync_to_async(open_live_page)("/p/", user=user)
    async with await page.connect_sse() as stream:
        assert (await stream.send_event("bump"))["type"] != "error"
        assert RAN == ["bump"]
        assert "event" in SEEN  # the control: middleware wraps the turn while the session lives
        seen_before = list(SEEN)
        await sync_to_async(page.session.delete)()
        with pytest.raises(AssertionError, match="answered 403.*permission_denied"):
            await stream.send_event("bump")
    assert RAN == ["bump"], "the handler ran for a revoked session"
    assert SEEN == seen_before, "app middleware ran for a revoked session"
