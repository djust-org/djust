"""Views and drivers for the HTTP page-POST differential (#4108).

Not a test module. ``test_http_fallback_differential_4108.py`` drives the same
event sequence over a real ``WebsocketCommunicator`` and over the page GET plus
fallback POSTs; ``tests/js/http_fallback_replay_4108.test.js`` replays the
recorded fallback answers with the built client.
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from django import forms
from django.test import Client
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler, state
from djust.forms import FormMixin

PAGE = "<!DOCTYPE html><html><head><title>t</title></head><body>\n<div dj-root>%s</div>\n</body></html>"

#: Sent by the client with each fallback POST (``11-event-handler.js``).
DIGEST_HEADER = "HTTP_X_DJUST_RENDER_DIGEST"


class Counter(LiveView):
    exposure_policy = "explicit"
    template = PAGE % (
        "\n<header><h1>Counter</h1></header>\n"
        '<main><p class="lead">Value: <strong>{{ count }}</strong></p>\n'
        '<input name="note" value="">\n'
        "<details><summary>More</summary><p>Static body</p></details>\n"
        '<button dj-click="increment">+</button></main>\n'
    )
    count = state(0, persist="server")

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


def _rows():
    return [{"id": i, "name": "Item %d" % i, "qty": i} for i in range(1, 4)]


class ListEdit(LiveView):
    """Rows are added, removed and changed: structural patches."""

    exposure_policy = "explicit"
    template = PAGE % (
        "\n<h1>Rows</h1>\n<ul>{% for r in rows %}\n"
        '<li data-key="{{ r.id }}"><span>{{ r.name }}</span> <b>{{ r.qty }}</b></li>'
        "{% endfor %}</ul>\n"
        '{% if rows|length > 3 %}<p class="many">Many rows</p>{% endif %}\n'
        '<button dj-click="add">add</button>\n'
    )
    rows = state(default_factory=_rows, persist="server")
    next_id = state(4, persist="server")

    @event_handler
    def add(self, **kwargs):
        self.rows = [*self.rows, {"id": self.next_id, "name": "Item %d" % self.next_id, "qty": 0}]
        self.next_id += 1

    @event_handler
    def remove(self, row: int = 0, **kwargs):
        self.rows = [r for r in self.rows if r["id"] != int(row)]

    @event_handler
    def bump(self, row: int = 0, **kwargs):
        self.rows = [
            {**r, "qty": r["qty"] + 1} if r["id"] == int(row) else dict(r) for r in self.rows
        ]


class _ContactForm(forms.Form):
    name = forms.CharField(max_length=100, min_length=3)
    email = forms.EmailField()
    city = forms.CharField(max_length=60)


def _field(name: str) -> str:
    return (
        '<div class="row"><label>%(n)s</label>'
        '<input type="text" name="%(n)s" value="{{ form_data.%(n)s }}" dj-change="validate_field">'
        '<div class="err">{%% for e in field_errors.%(n)s %%}<span>{{ e }}</span>{%% endfor %%}</div>'
        "</div>\n" % {"n": name}
    )


class ContactForm(FormMixin, LiveView):
    """``field_errors`` is not persisted (ADR-038 D-e): not reproducible after an error."""

    exposure_policy = "explicit"
    form_class = _ContactForm
    template = PAGE % (
        '\n<form dj-submit="submit_form">\n'
        + "".join(_field(n) for n in ("name", "email", "city"))
        + '<button type="submit">Send</button></form>\n'
    )

    def form_valid(self, form):
        pass


class Paged(LiveView):
    """The page number is URL state, read by ``handle_params`` on every request."""

    exposure_policy = "explicit"
    template = PAGE % (
        '\n<nav><a href="?page={{ page|add:1 }}">next</a></nav>\n'
        "<p>Page <b>{{ page }}</b>, starred: <i>{{ starred }}</i></p>\n"
        '<button dj-click="star">star</button>\n'
    )
    page = state(1)
    starred = state(0, persist="server")

    def handle_params(self, params, uri, **kwargs):
        self.page = int(params.get("page", 1))

    @event_handler
    def star(self, **kwargs):
        self.starred += 1


class Feed(LiveView):
    """``temporary_assigns``: the server renders only the new rows."""

    exposure_policy = "explicit"
    temporary_assigns = {"entries": []}
    template = PAGE % (
        '\n<ul id="feed" dj-update="append">{% for e in entries %}'
        '<li id="e-{{ e.id }}">{{ e.text }}</li>{% endfor %}</ul>\n'
        # The id matters: a whole-page answer with a dj-update container in it
        # updates the container's siblings only by id (``applyDjUpdateElements``).
        '<p id="sent">Sent: <b>{{ sent }}</b></p>\n'
    )
    sent = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.entries = [{"id": 0, "text": "first"}]

    def get_context_data(self, **kwargs):
        return super().get_context_data(entries=self.entries, **kwargs)

    @event_handler
    def post_entry(self, **kwargs):
        self.sent += 1
        self.entries = [{"id": self.sent, "text": "entry %d" % self.sent}]


#: Stands in for a clock: a different value on every render, with no wall time.
_render_serial = itertools.count(1)


def reset_serial() -> None:
    """Start the per-render serial again, so a recorded run does not depend on test order."""
    global _render_serial
    _render_serial = itertools.count(1)


class Unstable(LiveView):
    """Prints a value that differs on every render: never reproducible."""

    exposure_policy = "explicit"
    template = PAGE % (
        '\n<p>Value: <b>{{ count }}</b></p>\n<p class="serial">render {{ serial }}</p>\n'
    )
    count = state(0, persist="server")

    def get_context_data(self, **kwargs):
        return super().get_context_data(serial=next(_render_serial), **kwargs)

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


class Volatile(LiveView):
    """Its handler sets a plain attribute, which no later request sees."""

    exposure_policy = "explicit"
    template = PAGE % "\n<p>Kept: <b>{{ kept }}</b>, lost: <i>{{ lost }}</i></p>\n"
    kept = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.lost = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(lost=self.lost, **kwargs)

    @event_handler
    def bump(self, **kwargs):
        self.kept += 1
        self.lost += 1


def _stamp(request):
    """A context processor whose value changes on every call."""
    return {"stamp": next(_render_serial)}


class Stamped(LiveView):
    """Renders a time-varying context-processor value (see ``_stamp``)."""

    exposure_policy = "explicit"
    template = PAGE % '\n<p>Value: <b>{{ count }}</b></p>\n<p class="stamp">{{ stamp }}</p>\n'
    count = state(0, persist="server")

    def _get_context_processors(self):
        return [*super()._get_context_processors(), __name__ + "._stamp"]

    @event_handler
    def increment(self, **kwargs):
        self.count += 1


VIEWS: dict[str, type] = {
    "counter": Counter,
    "list": ListEdit,
    "form": ContactForm,
    "paged": Paged,
    "feed": Feed,
    "unstable": Unstable,
    "volatile": Volatile,
    "stamped": Stamped,
}

urlpatterns = [path("m/%s/" % name, cls.as_view()) for name, cls in VIEWS.items()]

#: name -> (url, events). The same sequence goes over both transports.
SCENARIOS: dict[str, tuple[str, list[tuple[str, dict]]]] = {
    "counter": ("/m/counter/", [("increment", {})] * 3),
    "list": (
        "/m/list/",
        [
            ("add", {}),
            ("bump", {"row": 2}),
            ("remove", {"row": 1}),
            ("add", {}),
            ("remove", {"row": 4}),
        ],
    ),
    "form": (
        "/m/form/",
        [
            ("validate_field", {"field_name": "email", "value": "not-an-email"}),
            ("validate_field", {"field_name": "name", "value": "Al"}),
            ("validate_field", {"field_name": "city", "value": "Lyon"}),
        ],
    ),
    "paged": ("/m/paged/?page=3", [("star", {})] * 2),
    "feed": ("/m/feed/", [("post_entry", {})] * 2),
    "unstable": ("/m/unstable/", [("increment", {})] * 2),
    "volatile": ("/m/volatile/", [("bump", {})] * 3),
    "stamped": ("/m/stamped/", [("increment", {})] * 2),
}

_DJ_ID = re.compile(r'\sdj-id="[^"]*"')


def normalize(html: str | None) -> str | None:
    """Root HTML with generated ``dj-id`` attributes and whitespace runs removed."""
    if html is None:
        return None
    return re.sub(r"\s+", " ", _DJ_ID.sub("", html)).strip()


class RenderLog:
    """Records the root HTML of every diff render (``render_view_with_diff``)."""

    def __init__(self, monkeypatch: Any) -> None:
        from djust import _child_rendering

        self.htmls: list[str] = []
        original = _child_rendering.render_view_with_diff

        def recording(view: Any, *args: Any, **kwargs: Any) -> Any:
            result = original(view, *args, **kwargs)
            self.htmls.append(result[0])
            return result

        monkeypatch.setattr(_child_rendering, "render_view_with_diff", recording)


def answer_kind(answer: dict) -> str:
    if "patches" in answer:
        return "patches"
    if "html" in answer:
        return "html"
    return "other"


def meta_digest(document: str) -> str | None:
    """The render digest the page GET carries, as the client reads it."""
    match = re.search(r'<meta name="djust-render-digest" content="([^"]*)">', document)
    return match.group(1) if match else None


class DigestEcho:
    """The client's rule for echoing the render digest (``11-event-handler.js``).

    Kept in step with the client by the jsdom replay, which asserts that the
    built client sends exactly the digests recorded here.
    """

    HOLD = 8

    def __init__(self, digest: str | None) -> None:
        self.digest = digest
        self.misses = 0
        self.hold = 0

    def to_send(self) -> str | None:
        if not self.digest:
            return None
        if self.hold > 0:
            self.hold -= 1
            return None
        return self.digest

    def note(self, answer: dict, sent: str | None) -> None:
        whole = isinstance(answer.get("html"), str)
        changed = whole or bool(answer.get("patches"))
        if answer.get("render_digest"):
            self.digest = answer["render_digest"]
        elif changed:
            self.digest = None
        if not sent or not changed:
            return
        if not whole:
            self.misses = 0
        else:
            self.misses += 1
            if self.misses >= 2:
                self.hold = self.HOLD


def post_event(
    client: Client, url: str, event: str, params: dict, digest: str | None = None, **extra: Any
) -> Any:
    """One fallback POST, as the client sends it."""
    headers = {"HTTP_X_DJUST_EVENT": event, **extra}
    if digest:
        headers[DIGEST_HEADER] = digest
    return client.post(url, data=json.dumps(params), content_type="application/json", **headers)


def run_fallback(
    url: str,
    events: list[tuple[str, dict]],
    log: RenderLog,
    *,
    client: Client | None = None,
) -> dict:
    """GET the page, then POST each event the way the client's fallback does.

    The digest the page and each answer carry is echoed as the client echoes it
    (``DigestEcho``). Returns the GET document, the answers, each answer's kind,
    the number of diff renders each request made, and the server's root HTML
    after each request.
    """
    client = client or Client()
    got = client.get(url)
    assert got.status_code == 200, got.status_code
    document = got.content.decode()
    echo = DigestEcho(meta_digest(document))
    out: dict = {
        "url": url,
        "get": document,
        "get_digest": echo.digest,
        "events": [list(e) for e in events],
        "answers": [],
        "kinds": [],
        "renders": [],
        "sent_digests": [],
        "html_after": [],
    }
    for event, params in events:
        before = len(log.htmls)
        sent = echo.to_send()
        out["sent_digests"].append(sent)
        response = post_event(client, url, event, params, sent)
        assert response.status_code == 200, (response.status_code, response.content[:300])
        answer = json.loads(response.content)
        echo.note(answer, sent)
        out["answers"].append(answer)
        out["kinds"].append(answer_kind(answer))
        out["renders"].append(len(log.htmls) - before)
        out["html_after"].append(log.htmls[-1])
    out["final"] = out["html_after"][-1] if out["html_after"] else None
    return out


async def run_websocket(url: str, view_path: str, events: list[tuple[str, dict]]) -> dict:
    """Mount over a real ``WebsocketCommunicator`` after the page GET, send the events."""
    from asgiref.sync import sync_to_async
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    consumers: list[Any] = []

    class Recording(LiveViewConsumer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            consumers.append(self)

    def page() -> Any:
        client = Client()
        assert client.get(url).status_code == 200
        return client.session

    session = await database_sync_to_async(page)()
    comm = WebsocketCommunicator(
        Recording.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    comm.scope["session"] = session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected

    async def until(types: tuple[str, ...]) -> dict:
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in types:
                return frame
        raise AssertionError("no frame of %r" % (types,))

    # The client's mount frame: the path, and the query string as ``params``.
    parts = urlsplit(url)
    await comm.send_json_to(
        {
            "type": "mount",
            "view": view_path,
            "url": parts.path,
            "params": dict(parse_qsl(parts.query)),
            "has_prerendered": True,
        }
    )
    frame = await until(("mount", "error"))
    assert frame["type"] == "mount", frame
    kinds = []
    for ref, (event, params) in enumerate(events, start=1):
        await comm.send_json_to({"type": "event", "event": event, "params": params, "ref": ref})
        frame = await until(("patch", "html_update", "noop", "error"))
        assert frame["type"] != "error", frame
        kinds.append(frame["type"])
    view = consumers[-1].view_instance
    final = await sync_to_async(lambda: view.render_with_diff()[0], thread_sensitive=True)()
    await comm.disconnect()
    return {"kinds": kinds, "final": final}
