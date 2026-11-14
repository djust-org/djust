"""The page load's tab id meta and its state save (#4107, #4106).

* Where ``page_with_tab`` puts ``<meta name="djust-tab">`` for each page shape.
  ``tests/js/tab-id-4106.test.js`` parses the same shapes and shows the id is in
  ``document.head`` and the page is not in quirks mode.
* A page load is served even when its session cannot be updated: over the plain
  GET, the chunked GET and the streamed ASGI GET, with a fresh client that has
  no session row and with a session row deleted during the request.
"""

from __future__ import annotations

import logging
import re

import pytest
from django.contrib.sessions.models import Session
from django.test import AsyncClient, Client, override_settings
from django.urls import path

from djust import LiveView, _automatic_state, _exposure_sessions
from djust._automatic_state import page_with_tab

META = '<meta name="djust-tab" content="tab-placed01">'
REST = "<div dj-root><p>0</p></div>"


@pytest.mark.parametrize(
    "page,expected",
    [
        # A head: inside it, last.
        (
            f"<!DOCTYPE html><html><head><title>t</title></head><body>{REST}</body></html>",
            f"<!DOCTYPE html><html><head><title>t</title>{META}</head><body>{REST}</body></html>",
        ),
        (
            f"<HTML><HEAD></HEAD ><BODY>{REST}</BODY></HTML>",
            f"<HTML><HEAD>{META}</HEAD ><BODY>{REST}</BODY></HTML>",
        ),
        # No head: right after <html>, where the parser's head begins.
        (f"<html><body>{REST}</body></html>", f"<html>{META}<body>{REST}</body></html>"),
        (
            f'<!DOCTYPE html>\n<html lang="en"><body>{REST}</body></html>',
            f'<!DOCTYPE html>\n<html lang="en">{META}<body>{REST}</body></html>',
        ),
        # A doctype and no <html>: after the doctype, never before it.
        (f"<!DOCTYPE html>{REST}", f"<!DOCTYPE html>{META}{REST}"),
        (f"\n<!doctype html>\n{REST}", f"\n<!doctype html>{META}\n{REST}"),
        # Only a fragment: in front.
        (REST, f"{META}{REST}"),
        ("", META),
    ],
)
def test_where_the_tab_meta_goes(page, expected):
    assert page_with_tab(page, "tab-placed01") == expected


@pytest.mark.parametrize("tab", ["", "short", "has space in it", None])
def test_no_valid_tab_id_adds_no_meta(tab):
    assert page_with_tab(f"<html><head></head>{REST}</html>", tab) == (
        f"<html><head></head>{REST}</html>"
    )


def test_a_doctype_is_never_preceded_by_the_meta():
    for page in (
        f"<!DOCTYPE html>{REST}",
        f"<!DOCTYPE html><html>{REST}</html>",
        f"<!DOCTYPE html><html><head></head>{REST}</html>",
        f"  <!DOCTYPE html>\n{REST}",
    ):
        placed = page_with_tab(page, "tab-placed01")
        assert placed.index("<!DOCTYPE") < placed.index("<meta"), placed


# ---------------------------------------------------------------------------
# The page load's save
# ---------------------------------------------------------------------------

SHELL = "<!DOCTYPE html><html><head><title>t</title></head><body>%s</body></html>"


class Plain(LiveView):
    exposure_policy = "explicit"
    template = SHELL % "<div dj-root><p>Count: {{ count }}</p></div>"

    def mount(self, request, **kwargs):
        self.count = 3
        self.note = "kept"

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)


class Streamed(Plain):
    streaming_render = True


class SessionGoes(Plain):
    """The session row is deleted while the request runs (a logout elsewhere)."""

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        request.session.get("anything")  # the session is loaded
        Session.objects.all().delete()


class SessionGoesStreamed(SessionGoes):
    streaming_render = True


urlpatterns = [
    path("p/plain/", Plain.as_view()),
    path("p/streamed/", Streamed.as_view()),
    path("p/goes/", SessionGoes.as_view()),
    path("p/goes-streamed/", SessionGoesStreamed.as_view()),
]


@pytest.fixture(autouse=True)
def _routes():
    _automatic_state._NOTICED.clear()
    with override_settings(
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        DEBUG=False,
        LIVEVIEW_ALLOWED_MODULES=["djust"],
    ):
        yield


def _body(response) -> str:
    if getattr(response, "streaming", False):
        return b"".join(response.streaming_content).decode()
    return response.content.decode()


async def _abody(response) -> str:
    if getattr(response, "streaming", False):
        content = response.streaming_content
        if hasattr(content, "__aiter__"):
            return b"".join([chunk async for chunk in content]).decode()
        return b"".join(content).decode()
    return response.content.decode()


def _saved():
    rows = [row.get_decoded() for row in Session.objects.all()]
    return [value for row in rows for key, value in row.items() if key.startswith("_djust_auto_")]


def _check_saved_page(page: str):
    assert "Count: 3" in page
    assert len(re.findall(r'<meta name="djust-tab" content="[A-Za-z0-9_-]{8,64}">', page)) == 1
    (envelope,) = _saved()
    assert envelope["values"] == {"count": 3, "note": "kept"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url", ["/p/plain/", "/p/streamed/"])
def test_a_fresh_client_with_no_session_row_gets_the_page_and_its_state_is_saved(url):
    assert Session.objects.count() == 0
    response = Client().get(url)
    assert response.status_code == 200
    _check_saved_page(_body(response))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url", ["/p/plain/", "/p/streamed/"])
async def test_a_fresh_client_over_asgi_gets_the_streamed_page_and_its_state_is_saved(url):
    from asgiref.sync import sync_to_async

    response = await AsyncClient().get(url)
    assert response.status_code == 200
    page = await _abody(response)
    await sync_to_async(_check_saved_page)(page)


def _with_session(client: Client) -> None:
    assert client.get("/p/plain/").status_code == 200
    assert Session.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url", ["/p/goes/", "/p/goes-streamed/"])
def test_a_session_deleted_during_the_page_load_still_serves_the_page(url, caplog):
    client = Client()
    _with_session(client)
    with caplog.at_level(logging.WARNING, logger="djust"):
        response = client.get(url)
        page = _body(response)
    assert response.status_code == 200
    assert "Count: 3" in page
    assert _saved() == []  # nothing saved, and nothing left staged for a later write
    warned = [
        r.getMessage()
        for r in caplog.records
        if "was not saved for this page load" in r.getMessage()
    ]
    assert len(warned) == 1 and "kept" not in warned[0]
    # Once per view class, not per request.
    _with_session(client)
    with caplog.at_level(logging.WARNING, logger="djust"):
        assert client.get(url).status_code == 200
    assert sum("was not saved for this page load" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_session_deleted_during_the_streamed_asgi_page_load_still_serves_the_page():
    from asgiref.sync import sync_to_async

    client = AsyncClient()
    assert (await client.get("/p/plain/")).status_code == 200
    response = await client.get("/p/goes-streamed/")
    assert response.status_code == 200
    assert "Count: 3" in await _abody(response)
    assert await sync_to_async(_saved)() == []


@pytest.mark.django_db(transaction=True)
def test_a_failed_write_leaves_nothing_staged_in_the_session(monkeypatch):
    """Gate: the staged envelope is undone when the write fails, whatever failed."""

    class Broken(RuntimeError):
        pass

    def fail(session, key):
        raise Broken()

    client = Client()
    _with_session(client)
    seen = {}
    real = _automatic_state.stage

    def stage(view, request, **kwargs):
        seen["session"] = request.session
        return real(view, request, **kwargs)

    monkeypatch.setattr(_automatic_state, "stage", stage)
    monkeypatch.setattr(_exposure_sessions, "_flush", fail)
    client.raise_request_exception = False
    assert client.get("/p/plain/").status_code == 500
    # Only the first page load's envelope: the failed one staged nothing lasting.
    session = seen["session"]
    assert len([key for key in session.keys() if key.startswith("_djust_auto_")]) == 1
    assert session.modified is False
