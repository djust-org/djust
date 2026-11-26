"""#4223: an admin widget keeps its id and its state across parent events.

``_change_form_widgets.html`` / ``_change_list_widgets.html`` embedded each
widget with ``{% live_render %}`` and no ``view_id``. Over a socket every
parent re-render then mounted the widget again under a new ``child_N`` id:
``mount()`` ran again, its state was lost, and an event sent to the old id got
"Embedded view not found". Each widget slot now has a stable id, so the
explicit child is reused while its inputs are unchanged (ADR-038 D-m).

Driven over the WebSocket and SSE (where the parent's view lives across
events), on the change form and the change list.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from typing import List, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import path

from djust import LiveView
from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.decorators import event_handler, state
from djust.tests._frames import frame_matches, legacy_type
from djust.tests.test_admin_page_render import _session_cookie, _Socket
from djust.tests.test_framework_classes_explicit_4185 import _sse_frame, _sse_request

pytestmark = pytest.mark.admin

MODULE = __name__

#: One entry per ``mount()`` of the widget.
MOUNTS: List[object] = []


class CounterWidget(LiveView):
    exposure_policy = "explicit"
    template = (
        '<div><p class="clicks">clicks={{ clicks }}</p><button dj-click="bump">+</button></div>'
    )
    label = "Counter"
    clicks = state(default=0)

    def mount(self, request, object_id=None, **kwargs):
        MOUNTS.append(object_id)

    @event_handler
    def bump(self) -> None:
        self.clicks += 1


setattr(sys.modules[__name__], "CounterWidget", CounterWidget)


class GroupAdmin(DjustModelAdmin):
    list_display = ["name"]
    change_form_widgets = [CounterWidget]
    change_list_widgets = [CounterWidget]


SITE = DjustAdminSite(name="widget_identity_4223_admin")
SITE.register(Group, GroupAdmin)

urlpatterns = [path("x4223-admin/", SITE.urls)]

LIST = "/x4223-admin/auth/group/"
LIST_VIEW = "djust.admin_ext.views.ModelListView"
DETAIL_VIEW = "djust.admin_ext.views.ModelDetailView"

_ANSWERS = ("patch", "html_update", "noop", "error", "embedded_update", "permission_denied")


@pytest.fixture
def site_urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    MOUNTS.clear()
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


def _widget_ids(text: str) -> List[str]:
    return sorted(set(re.findall(r'data-djust-embedded=\\?"([^"\\]+)', text)))


async def _staff(django_user_model) -> Client:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return browser


async def _ws(browser: Client, view: str, url: str, script) -> Tuple[dict, List[dict]]:
    answers: List[dict] = []
    async with _Socket(_session_cookie(browser)) as ws:
        mounted = await ws.mount(view, url)
        for name, params in script(mounted):
            await ws.send({"type": "event", "event": name, "params": params})
            for _ in range(30):
                frame = await ws.comm.receive_json_from(timeout=10)
                if frame_matches(frame, _ANSWERS):
                    answers.append(frame)
                    break
    return mounted, answers


async def _sse(browser: Client, view: str, url: str, script) -> Tuple[dict, List[dict]]:
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    sid = str(uuid.uuid4())
    opened = await sync_to_async(_sse_request)(
        "GET", f"/djust/sse/{sid}/", {"view": view, "_djust_url": url}, key
    )
    response = await DjustSSEStreamView().get(opened, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    answers: List[dict] = []
    try:
        await _sse_frame(stream, "sse_connect")
        mounted = await _sse_frame(stream, "mount", "error")
        assert mounted["type"] == "mount", mounted
        for name, params in script(mounted):
            posted = await sync_to_async(_sse_request)(
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": name, "params": params},
                key,
            )
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
            answers.append(await asyncio.wait_for(_sse_frame(stream, *_ANSWERS), 10))
    finally:
        _sse_sessions.pop(sid, None)
        await stream.aclose()
    return mounted, answers


def _script(parent_event):
    """bump, bump, a parent event, bump: all addressed to the widget the mount rendered."""

    def script(mounted):
        ids = _widget_ids(mounted["html"])
        assert len(ids) == 1, ids
        bump = ("bump", {"view_id": ids[0]})
        return [bump, bump, parent_event, bump]

    return script


async def _run(transport, browser, view, url, parent_event):
    run = _ws if transport == "websocket" else _sse
    mounted, answers = await run(browser, view, url, _script(parent_event))
    first_id = _widget_ids(mounted["html"])[0]
    assert [legacy_type(a) for a in answers if a["type"] == "error"] == [], answers
    clicks = re.findall(r"clicks=(\d+)", json.dumps(answers))
    # The widget's id does not change: no answer names another embedded view.
    assert set(_widget_ids(json.dumps(answers))) <= {first_id}, answers
    assert clicks[-1] == "3", (clicks, answers)
    assert len(MOUNTS) == 1, MOUNTS


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["websocket", "sse"])
async def test_a_change_form_widget_keeps_its_state(site_urls, django_user_model, transport):
    group = await sync_to_async(Group.objects.create)(name="edit-me")
    browser = await _staff(django_user_model)
    url = "%s%d/change/" % (LIST, group.pk)
    await _run(
        transport,
        browser,
        DETAIL_VIEW,
        url,
        ("validate_field", {"field": "name", "value": "edited"}),
    )
    assert MOUNTS == [group.pk]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["websocket", "sse"])
async def test_a_change_list_widget_keeps_its_state(site_urls, django_user_model, transport):
    await sync_to_async(Group.objects.create)(name="alpha")
    browser = await _staff(django_user_model)
    await _run(transport, browser, LIST_VIEW, LIST, ("sort_by", {"field": "name"}))


@pytest.mark.django_db(transaction=True)
async def test_the_http_fallback_renders_the_same_slot_id(site_urls, django_user_model):
    """The page GET and a fallback POST's render name the widget by the same id.

    (The HTTP fallback does not route an event to an explicit-exposure child at
    all, by design: ``RequestMixin._http_embedded_child``. What it does show is
    the id, which before #4223 differed between the GET and every POST.)
    """
    from djust.tests.test_framework_classes_explicit_4185 import _post, _tab

    group = await sync_to_async(Group.objects.create)(name="edit-me")
    browser = await _staff(django_user_model)
    url = "%s%d/change/" % (LIST, group.pk)
    page = await sync_to_async(browser.get)(url)
    assert page.status_code == 200
    ids = _widget_ids(page.content.decode())
    assert ids == ["djust_admin_widget_1"], ids
    answer = await sync_to_async(_post)(
        browser, url, "validate_field", {"field": "name", "value": "edited"}, _tab(page)
    )
    assert _widget_ids(json.dumps(answer)) == ids, answer
