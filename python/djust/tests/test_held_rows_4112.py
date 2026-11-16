"""#4112: rows held on a view are read again after a step that wrote to the database.

A view keeps a QuerySet (or a ``cached_property`` listing rows, or a model loaded
with ``prefetch_related``) and a handler writes to the database. The answer
must show the new rows on every transport: a real ``WebsocketCommunicator``,
the real SSE stream and message views, and the HTTP POST fallback. A turn that
wrote nothing must not query again, and an evaluated list or a single model
instance stays the snapshot it is.

Real rows throughout (``auth.Group``); nothing is stubbed.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection, connections, models, transaction
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils.functional import cached_property

from djust import LiveView
from djust import _held_rows
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import background, event_handler, state
from djust.tests._live_session import live_session

TAG = {"value": ""}
TEMPLATE = (
    "<div dj-root><ul>{% for r in rows %}<li>{{ r.name }}</li>{% endfor %}</ul><p>{{ n }}</p></div>"
)


def _rows():
    return Group.objects.filter(name__startswith=TAG["value"]).order_by("pk")


def _owner():
    return get_user_model().objects.get(username="owner" + TAG["value"])


def _rename():
    group = Group.objects.get(name=TAG["value"] + "alice")
    group.name = TAG["value"] + "bob"
    group.save()


def _create():
    carol = Group.objects.create(name=TAG["value"] + "carol")
    _owner().groups.add(carol)


def _delete():
    Group.objects.filter(name=TAG["value"] + "alice").delete()


class _Ops:
    """The three writes, plus a turn that writes nothing."""

    @event_handler()
    def update(self, **kwargs: Any) -> None:
        _rename()
        self.n += 1

    @event_handler()
    def create(self, **kwargs: Any) -> None:
        _create()
        self.n += 1

    @event_handler()
    def delete(self, **kwargs: Any) -> None:
        _delete()
        self.n += 1

    @event_handler()
    def noop(self, **kwargs: Any) -> None:
        self.n += 1


class _Explicit(_Ops, LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.n = 0

    def rows_value(self) -> Any:
        raise NotImplementedError

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(rows=self.rows_value(), n=self.n, **kwargs)


class HeldQuerySet(_Explicit):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = _rows()

    def rows_value(self) -> Any:
        return self._rows


class SavedEveryTurn(HeldQuerySet):
    """A declared field: the turn's state is saved before the reply."""

    n = state(0, persist="server")


class DeferredSave(HeldQuerySet):
    """The deferred state save of #4165: saved later, not before the reply."""

    n = state(0)
    state_save_interval = 30


class HeldInContainer(_Explicit):
    """The QuerySet inside a dict inside a list (depth 2)."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._panels = [{"rows": _rows()}]

    def rows_value(self) -> Any:
        return self._panels[0]["rows"]


class CachedRows(_Explicit):
    @cached_property
    def listed(self) -> list:
        return list(_rows())

    def rows_value(self) -> Any:
        return self.listed


class RelatedQuerySet(_Explicit):
    """``owner.groups.all()`` held from mount."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = _owner().groups.order_by("pk")

    def rows_value(self) -> Any:
        return self._rows


class Prefetched(_Explicit):
    """A model loaded with ``prefetch_related``: an instance is a snapshot, its
    prefetched relations included (re-fetch it, or ``refresh_from_db()``)."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._owner = get_user_model().objects.prefetch_related("groups").get(pk=_owner().pk)

    def rows_value(self) -> Any:
        return self._owner.groups.all()


class SnapshotList(_Explicit):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = list(_rows())

    def rows_value(self) -> Any:
        return self._rows


class SnapshotInstance(_Explicit):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._row = Group.objects.get(name=TAG["value"] + "alice")

    def rows_value(self) -> Any:
        return [self._row]


class LegacyQuerySet(_Ops, LiveView):
    exposure_policy = "legacy"  # legacy subject: rows a legacy view publishes refresh too
    template = TEMPLATE

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.rows = _rows()
        self.n = 0


class LegacyList(_Ops, LiveView):
    exposure_policy = "legacy"  # legacy subject: rows a legacy view publishes refresh too
    template = TEMPLATE

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.rows = list(_rows())
        self.n = 0


# What the answer to each operation shows: fresh rows, or the snapshot (no change).
FRESH = {"update": {"bob"}, "create": {"insert"}, "delete": {"remove"}}
SNAPSHOT = {"update": set(), "create": set(), "delete": set()}

SHAPES = {
    "queryset": (HeldQuerySet, FRESH),
    "queryset-in-container": (HeldInContainer, FRESH),
    "cached_property": (CachedRows, FRESH),
    "related-queryset": (RelatedQuerySet, FRESH),
    "prefetched-instance": (Prefetched, SNAPSHOT),
    "list": (SnapshotList, SNAPSHOT),
    "instance": (SnapshotInstance, SNAPSHOT),
    "legacy-queryset": (LegacyQuerySet, FRESH),
    "legacy-list": (LegacyList, SNAPSHOT),
}


# ---------------------------------------------------------------- opt-out, writers


class FrozenRows(_Explicit):
    """``frozen_rows``: ``_archive`` and ``listed`` stay as first read; ``_rows`` does not."""

    template = (
        "<div dj-root><ul>{% for r in rows %}<li>{{ r.name }}</li>{% endfor %}</ul>"
        "<ol>{% for r in archive %}<li>{{ r.name }}</li>{% endfor %}</ol>"
        "<em>{% for r in listed %}{{ r.name }}{% endfor %}</em><p>{{ n }}</p></div>"
    )
    frozen_rows = ("_archive", "listed")

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = _rows()
        self._archive = _rows()

    @cached_property
    def listed(self) -> list:
        return list(_rows())

    def get_context_data(self, **kwargs: Any) -> dict:
        return LiveView.get_context_data(
            self, rows=self._rows, archive=self._archive, listed=self.listed, n=self.n, **kwargs
        )


def _raw_update():
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE auth_group SET name = %s WHERE name = %s",
            [TAG["value"] + "bob", TAG["value"] + "alice"],
        )


class Writers(HeldQuerySet):
    """Every way of writing, each handler with its own write."""

    @event_handler()
    def raw(self, **kwargs: Any) -> None:
        _raw_update()

    @event_handler()
    def queryset_update(self, **kwargs: Any) -> None:
        Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob")

    @event_handler()
    def queryset_delete(self, **kwargs: Any) -> None:
        Group.objects.filter(name=TAG["value"] + "alice").delete()

    @event_handler()
    def bulk(self, **kwargs: Any) -> None:
        Group.objects.bulk_create([Group(name=TAG["value"] + "carol")])

    @event_handler()
    def rolled_back(self, **kwargs: Any) -> None:
        try:
            with transaction.atomic():
                _rename()
                raise RuntimeError("roll back")
        except RuntimeError:
            pass

    @event_handler()
    def locked_read(self, **kwargs: Any) -> None:
        with transaction.atomic():
            list(get_user_model().objects.select_for_update().filter(pk=_owner().pk))

    @event_handler()
    async def async_writer(self, **kwargs: Any) -> None:
        await sync_to_async(_rename)()

    @event_handler()
    async def async_unsensitive_writer(self, **kwargs: Any) -> None:
        await sync_to_async(_rename, thread_sensitive=False)()

    @event_handler()
    @background
    def background_writer(self, **kwargs: Any) -> None:
        _rename()

    @event_handler()
    def session_save(self, **kwargs: Any) -> None:
        self.request.session["seen_4112"] = uuid.uuid4().hex
        self.request.session.save()

    @event_handler()
    def read_cte(self, **kwargs: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("WITH x AS (SELECT 1) SELECT * FROM x")
            cursor.fetchall()

    @event_handler()
    def commented_read(self, **kwargs: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("/* app=shop */ SELECT 1")
            cursor.fetchall()

    @event_handler()
    def unseen_write(self, **kwargs: Any) -> None:
        """A write djust cannot see: the DB-API cursor under Django's wrappers."""
        connection.ensure_connection()
        cursor = connection.connection.cursor()
        mark = "?" if connection.vendor == "sqlite" else "%s"  # the driver's own paramstyle
        cursor.execute(
            "UPDATE auth_group SET name = %s WHERE name = %s" % (mark, mark),
            [TAG["value"] + "bob", TAG["value"] + "alice"],
        )
        cursor.close()

    @event_handler()
    def unseen_write_marked(self, **kwargs: Any) -> None:
        self.unseen_write()
        self.rows_changed()

    @event_handler()
    def thread_write(self, **kwargs: Any) -> None:
        """A thread the handler starts: outside the step, so it says so itself."""

        def later() -> None:
            try:
                assert THREAD_GO.wait(10)  # after this turn's render, as a late write is
                _rename()
                self.rows_changed()
            finally:
                connections.close_all()
                THREAD_DONE.set()

        threading.Thread(target=later).start()


THREAD_DONE = threading.Event()
THREAD_GO = threading.Event()
BOOM = {"on": False}


class FailingRender(HeldQuerySet):
    @event_handler()
    def write_then_fail_render(self, **kwargs: Any) -> None:
        _rename()
        BOOM["on"] = True

    def get_context_data(self, **kwargs: Any) -> dict:
        if BOOM["on"]:
            BOOM["on"] = False
            raise RuntimeError("the render failed")
        return super().get_context_data(**kwargs)


class ParamsWrite(HeldQuerySet):
    def handle_params(self, params: Any, uri: str) -> None:
        if params.get("read"):
            _rename()  # "mark the notification read" when its URL is opened


class BigListFirst(HeldQuerySet):
    """Large plain data held next to the QuerySet."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        self._points = list(range(12_000))
        self._records = [{"a": i, "b": "x", "c": 1.5, "d": None, "e": True} for i in range(1_700)]
        self._many = [{"k": i} for i in range(12_000)]
        super().mount(request, **kwargs)


class StickyRows(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "rows-dock-4112"
    template = (
        "<section><ol>{% for r in rows %}<li>c:{{ r.name }}</li>{% endfor %}</ol>"
        "<em>{{ k }}</em></section>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.k = 0
        self._rows = _rows()

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(rows=self._rows, k=self.k, **kwargs)

    @event_handler()
    def child_write(self, **kwargs: Any) -> None:
        _rename()

    @event_handler()
    def child_nothing(self, **kwargs: Any) -> None:
        pass


class StickyParent(HeldQuerySet):
    template = (
        "{% load live_tags %}<div dj-root><ul>{% for r in rows %}<li>{{ r.name }}</li>"
        "{% endfor %}</ul><p>{{ n }}</p>{% live_render '"
        + __name__
        + ".StickyRows' sticky=True %}</div>"
    )


class HeldNestedPrefetch(_Explicit):
    """A held QuerySet with a two-level prefetch: re-read once per level."""

    template = "<div dj-root>{% for u in users %}<i>{{ u }}</i>{% endfor %}<p>{{ n }}</p></div>"

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._users = (
            get_user_model()
            .objects.filter(username="owner" + TAG["value"])
            .prefetch_related("groups__permissions")
        )

    def get_context_data(self, **kwargs: Any) -> dict:
        users = [
            "%s:%s"
            % (
                u.username,
                ",".join("%s/%d" % (g.name, len(g.permissions.all())) for g in u.groups.all()),
            )
            for u in self._users
        ]
        return LiveView.get_context_data(self, users=users, n=self.n, **kwargs)


class InstanceNestedPrefetch(HeldNestedPrefetch):
    """A held instance with the same prefetch: a snapshot, no query after a write."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        _Explicit.mount(self, request, **kwargs)
        self._users = [
            get_user_model()
            .objects.prefetch_related("groups__permissions")
            .get(username="owner" + TAG["value"])
        ]


class ListPrefetch(HeldNestedPrefetch):
    """``list(qs.prefetch_related(...))``: a snapshot, no per-row query after a write."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        _Explicit.mount(self, request, **kwargs)
        self._users = list(
            get_user_model()
            .objects.filter(username="owner" + TAG["value"])
            .prefetch_related("groups__permissions")
        )


class ServerTurns(HeldQuerySet):
    """Server-originated turns that write and change nothing on the view."""

    _listen_channels = frozenset({"held_rows_4112"})
    tick_interval = None

    def handle_go(self, **kwargs: Any) -> None:
        _rename()

    async def handle_go_async(self, **kwargs: Any) -> None:
        await sync_to_async(_rename)()

    def handle_info(self, message: Any) -> None:
        _rename()

    @event_handler()
    def spawn_coroutine(self, **kwargs: Any) -> None:
        self.start_async(self._write_later)

    async def _write_later(self) -> None:
        await sync_to_async(_rename)()


class RenderEntry(HeldQuerySet):
    pass


TICKED: set = set()


class TickWrites(HeldQuerySet):
    """Writes on its first tick and changes nothing on the view (the flag is not on it)."""

    tick_interval = 50

    def handle_tick(self) -> None:
        if TAG["value"] not in TICKED:
            TICKED.add(TAG["value"])
            _rename()


class HiddenActivity(Writers):
    """Events sent while the activity is hidden are queued, then dispatched later."""

    _listen_channels = frozenset({"held_rows_activity_4112"})

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self.set_activity_visible("panel", False)

    def handle_info(self, message: Any) -> None:
        self.set_activity_visible("panel", True)

    @event_handler()
    def show(self, **kwargs: Any) -> None:
        self.set_activity_visible("panel", True)


class PushOnly(HeldQuerySet):
    @event_handler()
    def write_and_push(self, **kwargs: Any) -> None:
        _rename()
        self.push_event("saved", {})


class RowWriter(LiveComponent):
    class State(TypedState):
        label: str = "w"

    template = '<span class="writer">{{ label }}</span>'

    @event_handler()
    def rename(self, **kwargs: Any) -> None:
        _rename()


class ComponentWrites(HeldQuerySet):
    template = TEMPLATE.replace("<p>", "{{ writer }}<p>")
    writer = RowWriter()

    def get_context_data(self, **kwargs: Any) -> dict:
        return LiveView.get_context_data(self, rows=self._rows, n=self.n, **kwargs)


class Unrelated(HeldQuerySet):
    """The owner's case (#4112): rows of one table held, a click writes another."""

    @event_handler()
    def nothing_at_all(self, **kwargs: Any) -> None:
        pass

    @event_handler()
    def write_only(self, **kwargs: Any) -> None:
        _rename()

    @event_handler()
    def other_table(self, **kwargs: Any) -> None:
        _permission()

    @event_handler()
    def other_table_marked(self, **kwargs: Any) -> None:
        self.rows_changed(Permission)

    @event_handler()
    def own_table_marked(self, **kwargs: Any) -> None:
        self.rows_changed(Group)

    @event_handler()
    def unreadable_write(self, **kwargs: Any) -> None:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE IF NOT EXISTS scratch_4112 (x INTEGER)")


def _permission() -> None:
    from django.contrib.contenttypes.models import ContentType

    Permission.objects.create(
        codename="p" + uuid.uuid4().hex[:12],
        name="p",
        content_type=ContentType.objects.get_for_model(Group),
    )


class ReadsInContext(_Explicit):
    """No held QuerySet: the rows are read in get_context_data() each render."""

    def rows_value(self) -> Any:
        return list(_rows())

    @event_handler()
    def write_only(self, **kwargs: Any) -> None:
        _rename()

    @event_handler()
    def other_table(self, **kwargs: Any) -> None:
        _permission()


COMPUTED = {"n": 0}


class CachedScoped(_Explicit):
    @cached_property
    def listed(self) -> list:
        COMPUTED["n"] += 1
        return list(_rows())

    def rows_value(self) -> Any:
        return self.listed

    @event_handler()
    def other_table(self, **kwargs: Any) -> None:
        _permission()

    @event_handler()
    def other_table_and_bump(self, **kwargs: Any) -> None:
        _permission()
        self.n += 1


class ThroughFilter(_Explicit):
    """Rows found through a many-to-many: the write is to the through table only."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = Group.objects.filter(user__username="owner" + TAG["value"]).order_by("pk")

    def rows_value(self) -> Any:
        return self._rows

    @event_handler()
    def join(self, **kwargs: Any) -> None:
        _owner().groups.add(Group.objects.get(name=TAG["value"] + "zed"))


class PrefetchedPerms(_Explicit):
    """A held QuerySet reads a relation only through ``prefetch_related``."""

    template = (
        "<div dj-root><ul>{% for r in rows %}<li>{{ r }}</li>{% endfor %}</ul><p>{{ n }}</p></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._groups = _rows().prefetch_related("permissions")

    def rows_value(self) -> Any:
        return ["%s/%d" % (g.name, len(g.permissions.all())) for g in self._groups]

    @event_handler()
    def grant(self, **kwargs: Any) -> None:
        Group.objects.get(name=TAG["value"] + "alice").permissions.add(Permission.objects.first())


class RelationAtRender(_Explicit):
    """A held instance; the template walks a relation the render queries each time."""

    template = (
        "<div dj-root><ul>{% for g in owner.groups.all %}<li>{{ g.name }}</li>{% endfor %}"
        "</ul><p>{{ n }}</p></div>"
    )

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._owner = _owner()

    def get_context_data(self, **kwargs: Any) -> dict:
        return LiveView.get_context_data(self, owner=self._owner, n=self.n, **kwargs)

    @event_handler()
    def join_new(self, **kwargs: Any) -> None:
        _create()

    @event_handler()
    def other_table(self, **kwargs: Any) -> None:
        _permission()


class UnknownRead(Unrelated):
    """Its render runs a read whose tables cannot be named (a function in FROM)."""

    def get_context_data(self, **kwargs: Any) -> dict:
        with connection.cursor() as cursor:
            function = "json_each" if connection.vendor == "sqlite" else "json_array_elements_text"
            cursor.execute("SELECT value FROM %s('[1, 2]') AS value" % function)
            cursor.fetchall()
        return super().get_context_data(**kwargs)


class ReadsSessionTable(Writers):
    """Its render reads django_session: a session save must still not count as a write."""

    def get_context_data(self, **kwargs: Any) -> dict:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM "django_session"')
            cursor.fetchall()
        return super().get_context_data(**kwargs)


ALL_VIEWS = [cls for cls, _ in SHAPES.values()]
ALL_VIEWS += [FrozenRows, Writers, ComponentWrites, SavedEveryTurn, DeferredSave]
ALL_VIEWS += [ServerTurns, TickWrites, HiddenActivity, PushOnly]
ALL_VIEWS += [FailingRender, ParamsWrite, BigListFirst, StickyRows, StickyParent]
ALL_VIEWS += [HeldNestedPrefetch, InstanceNestedPrefetch, ListPrefetch]
ALL_VIEWS += [Unrelated, ReadsInContext, CachedScoped, ThroughFilter, PrefetchedPerms]
ALL_VIEWS += [RelationAtRender, UnknownRead, ReadsSessionTable]
urlpatterns = [path("v/%d/" % i, cls.as_view()) for i, cls in enumerate(ALL_VIEWS)]
URL = {cls: "/v/%d/" % i for i, cls in enumerate(ALL_VIEWS)}


@pytest.fixture(autouse=True)
def _levers():
    with override_settings(
        ROOT_URLCONF=__name__,
        DJUST_TENANTS=None,
        LIVEVIEW_ALLOWED_MODULES=[__name__],
        DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__],
        DEBUG=False,
        DJUST_ALLOW_MISSING_ORIGIN=True,
    ):
        yield
    BOOM["on"] = False


def _world():
    TAG["value"] = uuid.uuid4().hex[:8] + "-"
    owner = get_user_model().objects.create_user(username="owner" + TAG["value"])
    alice = Group.objects.create(name=TAG["value"] + "alice")
    owner.groups.add(alice)
    return owner


# ---------------------------------------------------------------- statements


@pytest.fixture
def sql(monkeypatch):
    """Every statement, from every thread and connection."""
    from django.db.backends import utils

    statements: list = []
    original = utils.CursorWrapper._execute

    def record(self, query, params, *args):
        statements.append(query)
        return original(self, query, params, *args)

    monkeypatch.setattr(utils.CursorWrapper, "_execute", record)
    return statements


def _row_reads(statements) -> int:
    """SELECTs of the held rows (the group table; not the handlers' own lookups)."""
    return sum(
        1
        for s in statements
        if s.lstrip().upper().startswith("SELECT")
        and '"auth_group"' in s
        and '"auth_group"."name" = ' not in s  # the handlers' own lookup by name
    )


def _change(frame: dict) -> set:
    """What a frame does to the rows: ``bob`` shown, a row inserted, a row removed."""
    seen: set = set()
    if frame.get("type") == "html_update":
        names = set(re.findall(re.escape(TAG["value"]) + r"(alice|bob|carol)", frame["html"]))
        return names
    for patch in frame.get("patches", []):
        kind = patch.get("type")
        if kind == "SetText" and TAG["value"] + "bob" in str(patch.get("text")):
            seen.add("bob")
        elif kind == "InsertChild":
            seen.add("insert")
        elif kind == "RemoveChild":
            seen.add("remove")
    return seen


# ---------------------------------------------------------------- transports


class _WS:
    name = "ws"

    async def open(self, cls, owner):
        from channels.testing import WebsocketCommunicator

        from djust.websocket import LiveViewConsumer

        session = await sync_to_async(live_session)(owner)
        self.sock = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        self.sock.scope.update(session=session, user=owner, tenant=None)
        assert (await self.sock.connect())[0]
        await self.sock.receive_json_from()
        await self.sock.send_json_to(
            {"type": "mount", "view": __name__ + "." + cls.__name__, "url": URL[cls], "params": {}}
        )
        mount = await self.sock.receive_json_from()
        assert mount.get("type") == "mount", mount
        return mount

    async def event(self, name, params=None, *, frames=1):
        await self.sock.send_json_to(
            {"type": "event", "event": name, "params": params or {}, "ref": 1}
        )
        out = []
        while len(out) < frames:
            frame = await self.sock.receive_json_from()
            if frame.get("type") in {"patch", "html_update", "error", "noop"}:
                out.append(frame)
        return out

    async def close(self):
        await self.sock.disconnect()


class _SSE:
    name = "sse"

    async def next_rows_change(self, *, polls=1000):
        for _ in range(polls):
            for frame in self._drain():
                if frame.get("type") in {"patch", "html_update"} and _change(frame):
                    return frame
            await asyncio.sleep(0.01)
        return None

    def _request(self, method, url, body, owner):
        from django.contrib.sessions.backends.db import SessionStore

        factory = RequestFactory()
        if method == "GET":
            request = factory.get(url, data=body)
        else:
            request = factory.post(url, data=json.dumps(body), content_type="application/json")
        request.session = SessionStore(self.key)
        request.user = owner
        request.tenant = None
        return request

    def _drain(self):
        out = []
        while not self.session.queue.empty():
            frame = self.session.queue.get_nowait()
            if frame is not None:
                out.append(frame)
        return out

    async def open(self, cls, owner):
        from djust.sse import DjustSSEStreamView, _sse_sessions

        self.owner = owner
        self.key = (await sync_to_async(live_session)(owner)).session_key
        self.sid = str(uuid.uuid4())
        request = await sync_to_async(self._request)(
            "GET",
            "/djust/sse/%s/" % self.sid,
            {"view": __name__ + "." + cls.__name__, "_djust_url": URL[cls]},
            owner,
        )
        assert (await DjustSSEStreamView().get(request, session_id=self.sid)).status_code == 200
        self.session = _sse_sessions[self.sid]
        frames = self._drain()
        return next(f for f in frames if f.get("type") == "mount")

    async def event(self, name, params=None, *, frames=1):
        from djust.sse import DjustSSEMessageView

        request = await sync_to_async(self._request)(
            "POST",
            "/djust/sse/%s/message/" % self.sid,
            {"type": "event", "event": name, "params": params or {}},
            self.owner,
        )
        response = await DjustSSEMessageView().post(request, session_id=self.sid)
        assert response.status_code == 200, response.content
        out = []
        for _ in range(500):
            out.extend(
                f
                for f in self._drain()
                if f.get("type") in {"patch", "html_update", "error", "noop"}
            )
            if len(out) >= frames:
                break
            await asyncio.sleep(0.01)
        return out

    async def close(self):
        from djust.sse import _sse_sessions

        _sse_sessions.pop(self.sid, None)


#: The WebSocket twice: stock, and with the worker pool (``worker_threads``), where
#: a sync handler runs in one offloaded hop with the change-detection snapshots.
TRANSPORTS = {"ws": _WS, "ws-worker-pool": _WS, "sse": _SSE}


def _client(transport, request):
    if transport == "ws-worker-pool":
        request.getfixturevalue("worker_pool")
    return TRANSPORTS[transport]()


def _http(cls, op, owner) -> set:
    """GET then a fallback POST; what the answer shows."""
    session = live_session(owner)
    factory = RequestFactory()
    get = factory.get(URL[cls])
    get.user, get.session, get.tenant = owner, session, None
    assert cls.as_view()(get).status_code == 200
    post = factory.post(
        URL[cls], data=json.dumps({"event": op, "params": {}}), content_type="application/json"
    )
    post.user, post.session, post.tenant = owner, session, None
    response = cls.as_view()(post)
    assert response.status_code == 200, response.content[:400]
    body = response.content.decode()
    return set(re.findall(re.escape(TAG["value"]) + r"(alice|bob|carol)", body))


# ============================================================================
# The matrix: transport x shape x operation x exposure
# ============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("op", ["update", "create", "delete"])
@pytest.mark.parametrize("shape", list(SHAPES))
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_write_shows_on_the_live_transports(request, transport, shape, op):
    cls, expected = SHAPES[shape]
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(cls, owner)
    try:
        (frame,) = await client.event(op)
        assert frame.get("type") == "patch", frame
        assert _change(frame) == expected[op], (shape, op, frame)
    finally:
        await client.close()


# Over the fallback, the expected rows after each operation (the page is answered whole).
HTTP_FRESH = {"update": {"bob"}, "create": {"alice", "carol"}, "delete": set()}
HTTP_SNAPSHOT = {"update": {"alice"}, "create": {"alice"}, "delete": {"alice"}}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("op", ["update", "create", "delete"])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_write_shows_on_the_http_fallback(shape, op):
    """The same rule as the live transports: a held instance (its prefetched relations
    included) is restored as it was saved, a snapshot."""
    cls, expected = SHAPES[shape]
    owner = _world()
    want = (HTTP_FRESH if expected is FRESH else HTTP_SNAPSHOT)[op]
    assert _http(cls, op, owner) == want


# ============================================================================
# Queries: none added on a turn that wrote nothing; one re-read after a write
# ============================================================================


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cls", [HeldQuerySet, LegacyQuerySet, CachedRows])
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_queries_per_turn(request, transport, cls, sql):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(cls, owner)
    try:
        counts = []
        for op in ("noop", "noop", "update", "noop", "noop"):
            sql.clear()
            await client.event(op)
            counts.append((op, _row_reads(sql), len(sql)))
        quiet = [c for c in counts if c[0] == "noop"]
        assert all(reads == 0 for _, reads, _ in quiet), counts
        assert len({total for _, _, total in quiet}) == 1, counts  # the same work every quiet turn
        assert counts[2][1] == 1, counts  # exactly one re-read after the write
    finally:
        await client.close()


def _session_writes(statements) -> int:
    return sum(
        1
        for s in statements
        if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and "django_session" in s
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cls,saves", [(SavedEveryTurn, 1), (DeferredSave, 0)])
async def test_a_reset_alone_is_not_a_state_change(cls, saves, sql):
    """A write turn costs its own statements and one re-read; nothing else
    (the state save, the session) changes, and only the rows' patch ships."""
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(cls, owner)
    try:
        sql.clear()
        (quiet,) = await client.event("noop")
        quiet_statements = list(sql)
        sql.clear()
        (frame,) = await client.event("update")
        writes = [s for s in sql if _held_rows.is_write(s) and "django_session" not in s]
        handler_reads = [s for s in sql if '"auth_group"."name" = ' in s]
        assert _session_writes(quiet_statements) == saves, quiet_statements
        assert _session_writes(sql) == saves, sql
        assert len(sql) == len(quiet_statements) + len(writes) + len(handler_reads) + 1, sql
        # The counter changed in both; the rows only after the write.
        assert [p["type"] for p in quiet["patches"]] == ["SetText"]
        assert sorted(p["type"] for p in frame["patches"]) == ["SetText", "SetText"]
    finally:
        await client.close()


# ============================================================================
# What counts as a write
# ============================================================================


WRITES = {
    "raw": (1, {"bob"}),
    "queryset_update": (1, {"bob"}),
    "queryset_delete": (1, {"remove"}),
    "bulk": (1, {"insert"}),
    # Rolled back: the rows did not change, but they are read again (harmless).
    "rolled_back": (1, set()),
    # SELECT ... FOR UPDATE is a read.
    "locked_read": (0, set()),
    # Not writes: the session's own table, a read-only CTE, a commented read.
    "session_save": (0, set()),
    # A WITH whose every part is a SELECT is a read.
    "read_cte": (0, set()),
    "commented_read": (0, set()),
    # A write under Django's execute wrappers is not seen; rows_changed() says so.
    "unseen_write": (0, set()),
    "unseen_write_marked": (1, {"bob"}),
    "async_writer": (1, {"bob"}),
    "async_unsensitive_writer": (1, {"bob"}),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("handler", list(WRITES))
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_every_kind_of_write(request, transport, handler, sql):
    reads, change = WRITES[handler]
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(Writers, owner)
    try:
        sql.clear()
        (frame,) = await client.event(handler)
        assert _row_reads(sql) == reads, sql
        assert _change(frame) == change, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_background_work_that_writes(request, transport):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(Writers, owner)
    try:
        frames = await client.event("background_writer", frames=2)
        assert _change(frames[-1]) == {"bob"}, frames
    finally:
        await client.close()


async def _until_rows_change(client, *, frames=20):
    """Frames arriving without an event (a push, a tick, background work).

    One wait per frame at the default timeout: a timed-out receive cancels the
    consumer (``ApplicationCommunicator.receive_output``), so it is never polled.
    """
    for _ in range(frames):
        frame = await client.sock.receive_json_from()
        if frame.get("type") in {"patch", "html_update"} and _change(frame):
            return frame
    return None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("handler", ["handle_go", "handle_go_async", "db_notify"])
async def test_a_server_push_that_writes(handler):
    from channels.layers import get_channel_layer

    from djust.push import view_group_name

    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(ServerTurns, owner)
    try:
        if handler == "db_notify":
            message = {"type": "db_notify", "channel": "held_rows_4112", "payload": {}}
            group = "djust_db_notify_held_rows_4112"
        else:
            message = {"type": "server_push", "handler": handler, "payload": {}}
            group = view_group_name(__name__ + ".ServerTurns")
        await get_channel_layer().group_send(group, message)
        frame = await _until_rows_change(client)
        assert frame is not None and _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.fixture
def worker_pool(monkeypatch):
    """The worker pool on (``worker_threads``): ticks run offloaded, in one hop."""
    from djust import worker_pool as pool_module
    from djust.config import config as djust_config

    monkeypatch.setitem(djust_config._config, "worker_threads", 2)
    monkeypatch.setitem(djust_config._config, "worker_pinning", True)
    monkeypatch.setattr(pool_module, "_pool", [])
    lane_pools: dict = {}
    monkeypatch.setattr(pool_module, "_lane_pools", lane_pools)
    yield
    for lane_pool in lane_pools.values():
        lane_pool.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_tick_that_writes(request, transport):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(TickWrites, owner)
    try:
        if transport == "sse":
            frame = await client.next_rows_change()
        else:
            frame = await _until_rows_change(client)
        assert frame is not None and _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("release", ["db_notify", "event"])
async def test_a_queued_activity_event_that_writes(release):
    """Queued while hidden; dispatched by the consumer (a NOTIFY) or the runtime (an event)."""
    from channels.layers import get_channel_layer

    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(HiddenActivity, owner)
    try:
        (ack,) = await client.event("raw", {"_activity": "panel"})
        assert ack.get("type") == "noop", ack
        if release == "db_notify":
            await get_channel_layer().group_send(
                "djust_db_notify_held_rows_activity_4112",
                {"type": "db_notify", "channel": "held_rows_activity_4112", "payload": {}},
            )
        else:
            await client.sock.send_json_to(
                {"type": "event", "event": "show", "params": {}, "ref": 2}
            )
        frame = await _until_rows_change(client)
        assert frame is not None and "bob" in _change(frame), frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_handler_that_writes_and_only_pushes(request, transport):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(PushOnly, owner)
    try:
        (frame,) = await client.event("write_and_push")
        assert _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_coroutine_start_async_callback_that_writes(request, transport):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(ServerTurns, owner)
    try:
        frames = await client.event("spawn_coroutine", frames=2)
        assert _change(frames[-1]) == {"bob"}, frames
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_components_write_refreshes_its_parent():
    owner = await sync_to_async(_world)()
    client = _WS()
    mount = await client.open(ComponentWrites, owner)
    try:
        component_id = re.search(r'data-component-id="([^"]+)"', mount["html"]).group(1)
        (frame,) = await client.event("rename", {"component_id": component_id})
        assert frame.get("type") in {"patch", "html_update"}, frame
        assert TAG["value"] + "bob" in json.dumps(frame), frame
    finally:
        await client.close()


@pytest.mark.django_db
def test_a_write_on_another_alias_is_recorded():
    """A connection of another alias gets the recorder when it connects."""
    from django.db.backends.sqlite3.base import DatabaseWrapper

    settings = dict(connections["default"].settings_dict, NAME=":memory:")
    other = DatabaseWrapper(settings, alias="other_4112")
    try:
        other.ensure_connection()
        assert _held_rows._recorder in other.execute_wrappers

        class Owner:
            pass

        view = Owner()
        with _held_rows.writes_by(view), other.cursor() as cursor:
            cursor.execute("CREATE TABLE t (x INTEGER)")
            assert not _held_rows.is_marked(Owner())
        assert _held_rows.is_marked(view)
        _held_rows.unmark(view)
        assert not _held_rows.is_marked(view)
    finally:
        other.close()


def test_ignored_tables_follow_the_settings():
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": "app_cache",
            }
        },
        DJUST_IGNORED_WRITE_TABLES=["heartbeat"],
    ):
        assert not _held_rows.is_write('INSERT INTO "app_cache" VALUES (1)')
        assert not _held_rows.is_write('UPDATE "heartbeat" SET at = now()')
        assert _held_rows.is_write('UPDATE "orders" SET x = 1')
    assert _held_rows.is_write('UPDATE "heartbeat" SET at = now()')


# ============================================================================
# What is reset, and what is not
# ============================================================================


@pytest.mark.django_db(transaction=True)
async def test_frozen_rows_keep_what_was_first_read():
    owner = await sync_to_async(_world)()
    client = _WS()
    mount = await client.open(FrozenRows, owner)
    assert mount["html"].count(TAG["value"] + "alice") == 3
    try:
        (frame,) = await client.event("update")
        texts = [str(p.get("text")) for p in frame["patches"] if p.get("type") == "SetText"]
        # Only the unfrozen list (and the counter) changed.
        assert texts.count(TAG["value"] + "bob") == 1, frame
    finally:
        await client.close()


@pytest.mark.django_db
@pytest.mark.parametrize("entry", ["sync_step", "run_sync_step"])
def test_every_render_entry_refreshes_a_marked_view(entry):
    """Both ways a render is entered reset a marked view: ``run_sync_step`` (the
    view's own render) and the callable ``sync_step`` builds (the scoped component
    render and ``StreamingMixin``, which run ``get_context_data`` through it alone)."""
    from djust import sync_turn

    _world()
    view = object.__new__(RenderEntry)
    view.__dict__["_rows"] = rows = _rows()
    list(rows)
    _held_rows.mark_tree(view)
    seen = []

    def render():
        seen.append(rows._result_cache)
        return {}

    def failing():
        list(rows)
        raise RuntimeError("the render failed")

    def call(fn):
        if entry == "sync_step":
            return sync_turn.sync_step(view, "render", fn)()
        return sync_turn.run_sync_step(view, "render", fn)

    with pytest.raises(RuntimeError):
        call(failing)
    assert _held_rows.is_marked(view), "a failed render keeps the mark"
    call(render)
    assert seen == [None]
    assert not _held_rows.is_marked(view), "a render that returned clears it"


@pytest.mark.django_db
def test_reset_bounds_and_exclusions():
    _world()

    class Holder:
        pass

    holder = Holder()
    shallow = _rows()
    deep = _rows()
    locked = _rows().select_for_update()
    holder.__dict__.update(
        shallow=shallow,
        nested=[{"rows": (deep,)}],  # depth 3: list -> dict -> tuple -> QuerySet
        too_deep=[[[[_rows()]]]],  # depth 4: not reached
        listed=list(_rows()),
    )
    too_deep = holder.too_deep[0][0][0][0]
    with transaction.atomic():
        for qs in (shallow, deep, too_deep, locked):
            list(qs)
        holder.__dict__["locked"] = locked
        snapshot = holder.listed
        _held_rows.reset_held_rows(holder)
        assert shallow._result_cache is None
        assert deep._result_cache is None
        assert too_deep._result_cache is not None
        assert locked._result_cache is not None
        assert holder.listed is snapshot and len(snapshot) == 1


def test_mark_tree_reaches_the_parent_chain_and_the_tree():
    class Node:
        def render(self):
            return ""

    root, child, grandchild, component, sibling = (Node() for _ in range(5))
    child._parent_view = root
    grandchild._parent_view = child
    component._parent = grandchild
    root._child_views = {"c": child, "s": sibling}
    child._child_views = {"g": grandchild}
    grandchild._components = {"x": component}
    _held_rows.mark_tree(component)
    try:
        for node in (root, child, grandchild, component, sibling):
            assert _held_rows.is_marked(node)
    finally:
        for node in (root, child, grandchild, component, sibling):
            _held_rows.unmark(node)


# ============================================================================
# The recorder stays inside its step
# ============================================================================


@pytest.mark.django_db(transaction=True)
def test_the_attribution_does_not_leak_to_the_next_query_on_the_thread():
    _world()

    class View:
        pass

    view = View()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_held_rows.attributed(view, lambda: list(_rows()))).result()
        assert not _held_rows.is_marked(view)
        pool.submit(_rename).result()  # same thread, outside the step
        assert not _held_rows.is_marked(view)
        pool.submit(_held_rows.attributed(view, _create)).result()
        assert _held_rows.is_marked(view)
    assert _held_rows._writer.get() is None
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
async def test_concurrent_turns_are_attributed_to_their_own_views():
    await sync_to_async(_world)()

    class View:
        pass

    writer, reader = View(), View()
    ready = threading.Event()

    # Two threads at once, each in its own attributed step; the read finishes
    # before the write starts (sqlite's shared in-memory cache locks a table).
    def write():
        assert ready.wait(5)
        _rename()

    def read():
        list(_rows())
        ready.set()

    await asyncio.gather(
        sync_to_async(_held_rows.attributed(writer, write), thread_sensitive=False)(),
        sync_to_async(_held_rows.attributed(reader, read), thread_sensitive=False)(),
    )
    assert _held_rows.is_marked(writer) and not _held_rows.is_marked(reader)
    _held_rows.unmark(writer)


@pytest.mark.django_db
def test_django_query_capture_still_works():
    _world()
    with CaptureQueriesContext(connection) as captured:
        list(_rows())
    assert len(captured) == 1


@pytest.mark.django_db
def test_a_project_wrapper_entered_before_connect_removes_itself():
    """``execute_wrapper()`` pops the LAST wrapper: the recorder goes first."""
    seen: list = []

    def project_wrapper(execute, sql, params, many, context):
        seen.append(sql)
        return execute(sql, params, many, context)

    connection.ensure_connection()
    connection.execute_wrappers.remove(_held_rows._recorder)
    with connection.execute_wrapper(project_wrapper):
        _held_rows.install(connection)  # what a connect inside the block does
        _held_rows.install(connection)  # a reconnect: no second copy
        list(Group.objects.all())
    assert connection.execute_wrappers.count(_held_rows._recorder) == 1
    assert project_wrapper not in connection.execute_wrappers
    assert seen


# ============================================================================
# Review findings (#4112, round 2)
# ============================================================================


@pytest.mark.django_db
def test_a_statement_a_wrapper_refuses_marks_nothing():
    """The recorder is the OUTERMOST wrapper and records after the statement returned."""

    class View:
        pass

    view = View()

    def refuse_writes(execute, sql, params, many, context):
        if _held_rows.is_write(sql):
            raise PermissionError("read-only replica")
        return execute(sql, params, many, context)

    _world()
    assert connection.execute_wrappers[0] is _held_rows._recorder
    with _held_rows.writes_by(view), connection.execute_wrapper(refuse_writes):
        with pytest.raises(PermissionError), transaction.atomic():
            _rename()
    assert not _held_rows.is_marked(view)
    with _held_rows.writes_by(view):
        _rename()
    assert _held_rows.is_marked(view)
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
async def test_a_failed_render_keeps_the_mark():
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(FailingRender, owner)
    try:
        (failed,) = await client.event("write_then_fail_render")
        assert failed.get("type") == "error", failed
        (frame,) = await client.event("noop")
        assert "bob" in _change(frame), frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_write_in_handle_params_is_recorded(sql):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(ParamsWrite, owner)
    try:
        await client.sock.send_json_to(
            {"type": "url_change", "params": {"read": "1"}, "uri": URL[ParamsWrite] + "?read=1"}
        )
        frame = await _until_rows_change(client)
        assert frame is not None and _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_rows_changed_from_a_thread_the_handler_started():
    """The thread writes after the step: rows_changed() marks the view, and the next
    turn shows the rows. Nothing is sent by itself in between."""
    THREAD_DONE.clear()
    THREAD_GO.clear()
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(Writers, owner)
    try:
        (first,) = await client.event("thread_write")
        assert _change(first) == set(), first
        THREAD_GO.set()
        assert await sync_to_async(THREAD_DONE.wait)(10)
        (frame,) = await client.event("noop")
        assert _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_sticky_childs_write_refreshes_both_when_they_render():
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(StickyParent, owner)
    try:
        await client.sock.send_json_to(
            {
                "type": "event",
                "event": "child_write",
                "params": {"view_id": "rows-dock-4112"},
                "ref": 1,
            }
        )
        while True:
            child = await client.sock.receive_json_from()
            if child.get("type") in {"embedded_update", "patch", "html_update", "error", "noop"}:
                break
        assert child.get("type") == "embedded_update", child
        assert "c:" + TAG["value"] + "bob" in json.dumps(child), child  # the child itself
        # The parent is marked and re-reads on its next render (none was sent for it).
        (frame,) = await client.event("noop")
        assert _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_large_container_before_the_queryset_does_not_switch_the_fix_off(caplog):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(BigListFirst, owner)
    try:
        with caplog.at_level("WARNING", logger="djust._held_rows"):
            (frame,) = await client.event("update")
        assert _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db
def test_a_queryset_after_many_records_is_reset():
    """Rows as dicts (with a list of tags) are one probe, not one visit each."""
    _world()

    class Holder:
        pass

    holder = Holder()
    holder.__dict__["_items"] = [{"id": i, "tags": ["a", "b"]} for i in range(20_000)]
    holder.__dict__["_late"] = late = [_rows()]
    list(late[0])
    _held_rows.reset_held_rows(holder)
    assert late[0]._result_cache is None


@pytest.mark.django_db
def test_a_queryset_after_large_scalar_containers_is_reset():
    """A list of 50,000 numbers is one probe, not 50,000 visits."""
    _world()

    class Holder:
        pass

    holder = Holder()
    holder.__dict__["_points"] = list(range(50_000))
    holder.__dict__["_series"] = {"t%d" % i: float(i) for i in range(50_000)}
    holder.__dict__["_late"] = late = [_rows()]
    list(late[0])
    _held_rows.reset_held_rows(holder)
    assert late[0]._result_cache is None


@pytest.mark.django_db
def test_a_queryset_named_parent_is_not_evaluated_by_the_recorder():
    _world()

    class View:
        pass

    view = View()
    view._parent = parent_rows = _rows()  # a user attribute that happens to be called _parent
    with CaptureQueriesContext(connection) as captured:
        _held_rows.mark_tree(view)
    assert len(captured) == 0
    assert parent_rows._result_cache is None
    assert _held_rows.is_marked(view) and not _held_rows.is_marked(parent_rows)
    _held_rows.unmark(view)


def _extra(write_turn, quiet_turn, handler_statements=2):
    return len(write_turn) - len(quiet_turn) - handler_statements


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "cls,reads",
    [
        (HeldNestedPrefetch, 3),  # users, groups, permissions: one query per level
        (InstanceNestedPrefetch, 0),  # a held instance is a snapshot, prefetches included
        (ListPrefetch, 0),  # list(qs.prefetch_related(...)) is a snapshot
    ],
)
async def test_prefetches_after_a_write(cls, reads, sql):
    def seed():
        from django.contrib.auth.models import Permission

        owner = _world()
        groups = [Group.objects.create(name=TAG["value"] + "n%02d" % i) for i in range(10)]
        perms = list(Permission.objects.all()[:3])
        for group in groups:
            group.permissions.set(perms)
        owner.groups.add(*groups)
        return owner

    owner = await sync_to_async(seed)()
    client = _WS()
    await client.open(cls, owner)
    try:
        sql.clear()
        await client.event("noop")
        quiet = list(sql)
        sql.clear()
        await client.event("update")
        write = list(sql)
        sql.clear()
        await client.event("noop")
        after = list(sql)
        assert _extra(write, quiet) == reads, write
        assert len(after) == len(quiet), after  # no lasting cost (no N+1 left behind)
    finally:
        await client.close()


def test_the_recorded_steps_cover_every_step_a_view_hook_runs_in():
    """Every ``SYNC_STEPS`` member is recorded or excluded with a reason, and every
    hook the #3646 structural pin places in a step is in one of them."""
    from djust.sync_turn import SYNC_STEPS
    from djust.tests.test_sync_turn_structural_pin_3646 import HOOKS

    assert set(_held_rows.RECORDED_STEPS) | set(_held_rows.NOT_RECORDED) == set(SYNC_STEPS)
    assert not set(_held_rows.RECORDED_STEPS) & set(_held_rows.NOT_RECORDED)
    named = set()
    for disposition in HOOKS.values():
        kind, _, rest = disposition.partition(":")
        if kind == "stepped":
            named.add(rest)
        elif kind == "inside":
            named.update(step for step in rest.split("@")[0].split("|") if step != "none")
    assert named <= set(SYNC_STEPS), named - set(SYNC_STEPS)
    # The steps application hooks run in that are recorded:
    assert named & set(_held_rows.RECORDED_STEPS) == {
        "event",
        "mount",
        "handle_params",
        "hook",
        "tick",
        "lifecycle",
    } | (named & {"async"})


# ============================================================================
# Scoped by table (#4112, owner round)
# ============================================================================


def _row_selects(statements) -> int:
    return sum(
        1 for s in statements if s.lstrip().upper().startswith("SELECT") and '"auth_group"' in s
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_write_to_an_unrelated_table_is_a_noop_as_before(request, transport, sql):
    """The owner's case: 5,000 rows of auth_group held; a click inserts into auth_permission.

    Same frame and same statements as before #4112: a ``noop``, the session and
    user reads, the handler's own statements, no re-read. A write to the held
    table afterwards is one re-read and a render."""

    def seed():
        owner = _world()
        Group.objects.bulk_create([Group(name=TAG["value"] + "x%05d" % i) for i in range(5_000)])
        _permission()  # warm the content-type cache, as a running app has it
        return owner

    owner = await sync_to_async(seed)()
    client = _client(transport, request)
    await client.open(Unrelated, owner)
    try:
        sql.clear()
        (quiet,) = await client.event("nothing_at_all")
        quiet_statements = list(sql)
        sql.clear()
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop", frame
        handler = [s for s in sql if "auth_permission" in s]
        assert _row_selects(sql) == 0, sql
        assert len(sql) == len(quiet_statements) + len(handler), sql
        sql.clear()
        (frame,) = await client.event("write_only")
        assert _change(frame) == {"bob"}, frame
        assert _row_reads(sql) == 1, sql
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_rows_changed_narrows_to_the_tables_named(sql):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(Unrelated, owner)
    try:
        (frame,) = await client.event("other_table_marked")
        assert frame.get("type") == "noop", frame
        sql.clear()
        (frame,) = await client.event("own_table_marked")
        assert frame.get("type") == "patch" and _row_selects(sql) == 1, (frame, sql)
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_write_whose_table_cannot_be_read_refreshes_everything(sql, caplog):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(Unrelated, owner)
    before = sum(_held_rows.fallback_counts.values())
    try:
        sql.clear()
        with caplog.at_level("DEBUG", logger="djust._held_rows"):
            (frame,) = await client.event("unreadable_write")
        assert frame.get("type") == "patch", frame
        assert _row_selects(sql) == 1, sql
        assert sum(_held_rows.fallback_counts.values()) > before
        assert any("UNKNOWN" in r.getMessage() for r in caplog.records)
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_rows_read_in_get_context_data_count_as_read(sql):
    """No held QuerySet: the read set (from the view's own renders) decides."""
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(ReadsInContext, owner)
    try:
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop", frame
        (frame,) = await client.event("write_only")
        assert _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_cached_properties_are_recomputed_only_for_tables_the_view_reads():
    COMPUTED["n"] = 0
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(CachedScoped, owner)
    try:
        assert COMPUTED["n"] == 1
        await client.event("other_table")
        await client.event("noop")
        (frame,) = await client.event("other_table_and_bump")  # renders: the counter changed
        assert frame.get("type") == "patch", frame
        assert COMPUTED["n"] == 1
        (frame,) = await client.event("update")
        assert COMPUTED["n"] == 2 and _change(frame) == {"bob"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_write_to_a_through_table_refreshes_a_queryset_filtering_through_it(sql):
    owner = await sync_to_async(_world)()
    await sync_to_async(Group.objects.create)(name=TAG["value"] + "zed")
    client = _WS()
    await client.open(ThroughFilter, owner)
    try:
        (frame,) = await client.event("join")
        assert frame.get("type") == "patch", frame
        assert {p["type"] for p in frame["patches"]} >= {"InsertChild"}, frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_write_to_a_prefetched_relation_refreshes_the_queryset():
    owner = await sync_to_async(_world)()
    client = _WS()
    mount = await client.open(PrefetchedPerms, owner)
    assert TAG["value"] + "alice/0" in mount["html"]
    try:
        (frame,) = await client.event("grant")
        assert TAG["value"] + "alice/1" in json.dumps(frame), frame
    finally:
        await client.close()


@pytest.mark.django_db
def test_a_write_through_another_alias_names_its_table():
    from django.db.backends.sqlite3.base import DatabaseWrapper

    settings = dict(connections["default"].settings_dict, NAME=":memory:")
    other = DatabaseWrapper(settings, alias="other_tables_4112")

    class Owner:
        pass

    view = Owner()
    try:
        other.ensure_connection()
        with other.cursor() as cursor:
            cursor.execute('CREATE TABLE "auth_group" (id INTEGER, name TEXT)')
        with _held_rows.writes_by(view), other.cursor() as cursor:
            cursor.execute('INSERT INTO "auth_group" (id, name) VALUES (1, %s)', ["x"])
        assert _held_rows.written_tables(view) == frozenset({"auth_group"})
    finally:
        _held_rows.unmark(view)
        other.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_relation_read_at_render_time_counts_as_read(request, transport):
    """The render's own queries (here a manager the template walks) are the view's reads."""
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(RelationAtRender, owner)
    try:
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop", frame
        (frame,) = await client.event("join_new")
        assert "insert" in _change(frame), frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_an_unknown_read_makes_every_write_refresh(sql):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(UnknownRead, owner)
    try:
        sql.clear()
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "patch", frame  # slower, never stale
        assert _row_reads(sql) == 1, sql
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_session_save_is_not_a_write_even_for_a_view_that_reads_the_session_table():
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(ReadsSessionTable, owner)
    try:
        (frame,) = await client.event("session_save")
        assert frame.get("type") == "noop", frame
    finally:
        await client.close()


def test_a_collected_view_does_not_deadlock_a_thread_holding_the_lock():
    """The garbage collector runs a weakref callback at any allocation, also in a
    thread that holds ``_lock`` (``_mark`` and ``note_reads`` allocate under it).
    The callback must not take that lock again: found as a hang of the suite."""
    import gc

    class View:
        pass

    def collect_under_the_lock():
        view = View()
        _held_rows.mark_tree(view, frozenset({"t"}))
        with _held_rows._lock:
            del view
            gc.collect()

    worker = threading.Thread(target=collect_under_the_lock, daemon=True)
    worker.start()
    worker.join(5)
    stuck = worker.is_alive()
    if stuck:  # free the deadlocked thread so the rest of the session can run
        _held_rows._lock.release()
        worker.join(5)
    assert not stuck, "a weakref callback re-entered _lock in the thread that held it"


# ============================================================================
# The strict grammars (#4112, round 3)
# ============================================================================

U = None  # an UNKNOWN table set

CLASSIFICATION = [
    # --- the ORM's own write shapes: the table is known ---
    (
        'INSERT INTO "auth_group" ("name") VALUES (%s) RETURNING "auth_group"."id"',
        "write",
        {"auth_group"},
    ),
    ('INSERT INTO "t" ("x") VALUES (%s), (%s)', "write", {"t"}),
    ('INSERT INTO "t" DEFAULT VALUES', "write", {"t"}),
    (
        'INSERT OR IGNORE INTO "auth_user_groups" ("user_id", "group_id") VALUES (%s, %s)',
        "write",
        {"auth_user_groups"},
    ),
    ("INSERT IGNORE INTO `t` (`x`) VALUES (1)", "write", {"t"}),
    ('INSERT OR REPLACE INTO "t" ("x") VALUES (1)', "write", U),
    ('INSERT INTO "t" ("x") SELECT * FROM UNNEST((%s)::integer[])', "write", {"t"}),
    (
        'INSERT INTO "t" ("x") VALUES (1) ON CONFLICT ("x") DO UPDATE SET "x" = 2 RETURNING "id"',
        "write",
        {"t"},
    ),
    ("INSERT INTO `t` (`x`) VALUES (1) ON DUPLICATE KEY UPDATE `x` = 2", "write", {"t"}),
    (
        'UPDATE "auth_group" SET "name" = %s WHERE "auth_group"."id" IN (%s)',
        "write",
        {"auth_group"},
    ),
    (
        'UPDATE "t" SET "n" = (SELECT MAX("y") FROM "u") WHERE "t"."id" IN (SELECT U0."id" FROM "t" U0)',
        "write",
        {"t"},
    ),
    ('UPDATE "T" SET "x" = 1', "write", {"t"}),
    ('UPDATE "My Table" SET x = 1', "write", {"my table"}),
    ("UPDATE \"t\" SET note = 'FROM x INTO y; DELETE FROM q'", "write", {"t"}),
    ('/* FROM y INTO z */ UPDATE "t" SET x = 1', "write", {"t"}),
    ('-- UPDATE other\nDELETE FROM "t"', "write", {"t"}),
    ('DELETE FROM "t" WHERE "t"."id" IN (%s, %s)', "write", {"t"}),
    ('DELETE FROM "t" RETURNING "id"', "write", {"t"}),
    ('UPDATE "t" SET x = 1;', "write", {"t"}),
    # --- anything else that is not a read is a write to UNKNOWN tables ---
    ('UPDATE "public"."t" SET x = 1', "write", U),
    ("UPDATE schema.t SET x = 1", "write", U),
    ("UPDATE t SET x = 1", "write", U),
    ("UPDATE `db`.`t` SET x = 1", "write", U),
    ("UPDATE [dbo].[t] SET x = 1", "write", U),
    ('UPDATE "we""ird" SET x = 1', "write", U),
    ('INSERT INTO "t" AS a ("x") VALUES (1)', "write", U),
    ('UPDATE ONLY "t" SET x = 1', "write", U),
    ('UPDATE "t1" SET x = "u"."x" FROM "t2" "u" WHERE 1 = 1', "write", U),
    ('DELETE FROM "t" USING "u" WHERE 1 = 1', "write", U),
    ('DELETE FROM "t" "a" WHERE 1 = 1', "write", U),
    ('DELETE FROM "t" AS "a" WHERE 1 = 1', "write", U),
    ('UPDATE "t" SET x = \'unterminated', "write", U),
    ('INSERT INTO "public"."t" ("x") VALUES (1)', "write", U),
    ('UPDATE "t1", "t2" SET a = 1', "write", U),
    ('UPDATE "t1" JOIN "t2" ON 1 = 1 SET b = 2', "write", U),
    ('DELETE "t1", "t2" FROM "t1" JOIN "t2" ON 1 = 1', "write", U),
    ('DELETE FROM "t1", "t2" USING "t1"', "write", U),
    ('UPDATE LOW_PRIORITY "t" SET x = 1', "write", U),
    ('DELETE QUICK FROM "t"', "write", U),
    ('INSERT LOW_PRIORITY INTO "t" VALUES (1)', "write", U),
    ('INSERT "t" VALUES (1)', "write", U),
    ('UPDATE TOP (5) "t" SET x = 1', "write", U),
    ('INSERT ALL INTO "a" VALUES (1) INTO "b" VALUES (2) SELECT 1 FROM dual', "write", U),
    ('INSERT INTO "t" SELECT * FROM "u"', "write", U),
    ('INSERT INTO "t" ("x") VALUES (1) RETURNING "id" INTO :out', "write", U),
    ('REPLACE INTO "t" VALUES (1)', "write", U),
    ('MERGE INTO "t" USING "u" ON 1 = 1 WHEN MATCHED THEN DELETE', "write", U),
    ('TRUNCATE "a", "b" CASCADE', "write", U),
    ('TRUNCATE "a"', "write", U),
    ("WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "write", U),
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "write", U),
    ('WITH x AS (SELECT 1) SELECT * FROM x JOIN "t" ON 1 = 1', "read", {"t"}),
    (
        'WITH RECURSIVE "r"(n) AS (SELECT 1 UNION SELECT n + 1 FROM "r") SELECT * FROM "r"',
        "read",
        set(),
    ),
    (
        'WITH a AS (SELECT * FROM "t"), b AS (SELECT * FROM a JOIN "u" ON 1 = 1) SELECT * FROM b',
        "read",
        {"t", "u"},
    ),
    ('WITH a AS (SELECT * FROM "t") SELECT * FROM a, generate_series(1, 2)', "read", U),
    ('WITH a AS MATERIALIZED (SELECT 1) UPDATE "t" SET x = 1', "write", U),
    # every part must start with SELECT; anything else is a write to UNKNOWN tables
    ("WITH v AS (VALUES (1)) SELECT * FROM v", "write", U),
    ('WITH a AS (TABLE "t") SELECT * FROM a', "write", U),
    (
        "WITH a AS (SELECT 1) SELECT * FROM a WHERE x IN (WITH b AS (SELECT 1) SELECT * FROM b)",
        "read",
        U,
    ),
    (
        'UPDATE "ta" SET x = 1 WHERE id IN (WITH d AS (DELETE FROM "tb" RETURNING id) SELECT id FROM d)',
        "write",
        U,
    ),
    ('UPDATE "ta" SET x = 1 WHERE id IN (SELECT id FROM "tb" FOR UPDATE)', "write", U),
    ('DELETE FROM "ta" WHERE id IN (SELECT 1 FROM (DELETE FROM "tb" RETURNING 1) x)', "write", U),
    ('UPDATE "a" SET x = 1; UPDATE "b" SET y = 2', "write", U),
    ('SELECT 1; DELETE FROM "t"', "write", U),
    ('BEGIN; UPDATE "t" SET x = 1; COMMIT', "write", U),
    ("CALL p()", "write", U),
    ("DO $$ BEGIN UPDATE t SET x = 1; END $$", "write", U),
    ("COPY t FROM STDIN", "write", U),
    ("CREATE TEMP TABLE x AS SELECT 1", "write", U),
    ("DROP TABLE t", "write", U),
    ("ALTER TABLE t ADD c int", "write", U),
    ("EXPLAIN ANALYZE DELETE FROM t", "write", U),
    ("VACUUM t", "write", U),
    ("REFRESH MATERIALIZED VIEW m", "write", U),
    ("TABLE t", "write", U),
    ('SELECT "x" INTO "y" FROM "t"', "write", U),
    ("", "write", U),
    # --- reads: the tables are known only when every FROM / JOIN is followed ---
    ('SELECT "a"."x" FROM "a" INNER JOIN "b" T3 ON ("a"."id" = T3."a_id")', "read", {"a", "b"}),
    (
        'SELECT "a"."x" FROM "a" LEFT OUTER JOIN "b" ON ("a"."id" = "b"."a_id") WHERE 1',
        "read",
        {"a", "b"},
    ),
    ('SELECT * FROM "a" AS "x", "b" WHERE 1', "read", {"a", "b"}),
    ('SELECT (SELECT MAX("id") FROM "u") FROM "t"', "read", {"u", "t"}),
    ('SELECT * FROM (SELECT * FROM "u") "s"', "read", {"u"}),
    ('SELECT EXTRACT(YEAR FROM "t"."d") FROM "t"', "read", {"t"}),
    ('SELECT 1 FROM "a" WHERE "x" IS DISTINCT FROM "y"', "read", {"a"}),
    ('SELECT EXISTS(SELECT 1 FROM "d" U0 WHERE U0."x" = "e"."x") FROM "e"', "read", {"d", "e"}),
    ('(SELECT "n" FROM "a") UNION (SELECT "n" FROM "b")', "read", {"a", "b"}),
    ('SELECT "a from b" FROM "t"', "read", {"t"}),
    ("SELECT 'x' FROM \"t\" WHERE c = 'FROM z'", "read", {"t"}),
    ('SELECT * FROM "t" FOR UPDATE', "read", {"t"}),
    ('SELECT * FROM "T"', "read", {"t"}),
    ("/* app=shop */ SELECT 1", "read", set()),
    ("SELECT nextval('s')", "read", set()),
    ('SELECT * FROM ONLY "t"', "read", U),
    ("SELECT * FROM t1, t2 a", "read", U),
    ("select * from T", "read", U),
    ('SELECT * FROM "public"."t"', "read", U),
    ('SELECT * FROM "t" WHERE x = \'unterminated', "write", U),  # unreadable
    ("SELECT * FROM generate_series(1, 2)", "read", U),
    ("SELECT value FROM json_each('[1]')", "read", U),
    ('SELECT * FROM "t" JOIN LATERAL (SELECT 1) x ON true', "read", U),
    ('SELECT * FROM "t" TABLESAMPLE SYSTEM (1)', "read", U),
    ('SELECT * FROM "ta" t1 JOIN ("tb" t2 JOIN "ta" t3 ON 1 = 1) ON 1 = 1', "read", U),
    ('SELECT * FROM ("ta" JOIN "tb" ON 1 = 1)', "read", U),
    ('SELECT * FROM "ta" WHERE x IN (TABLE "tb")', "read", U),
    ('SELECT x AS distinct FROM "ta"', "read", {"ta"}),
    ('SELECT ALL x, 1 distinct FROM "ta"', "read", {"ta"}),
    (
        'SELECT * FROM "ta" WHERE x IN (SELECT 1 FROM "tb" WHERE (y) IN ((SELECT 2 FROM "tc")))',
        "read",
        {"ta", "tb", "tc"},
    ),
    ('SELECT * FROM "ta" WHERE (SELECT 1 FROM (DELETE FROM "tb" RETURNING 1) x) = 1', "write", U),
    ('SELECT * FROM "ta" FOR NO KEY UPDATE', "read", {"ta"}),
    # PostgreSQL operators, casts and literals are inert tokens
    ('SELECT * FROM "b" WHERE "b"."title"::text ~ %s', "read", {"b"}),
    ('SELECT * FROM "b" WHERE "b"."title"::text ~* %s', "read", {"b"}),
    ('SELECT * FROM "a" WHERE ("a"."meta" #> %s) = %s', "read", {"a"}),
    ('SELECT * FROM "a" WHERE "a"."meta" @> %s AND "a"."meta" <@ %s', "read", {"a"}),
    ('SELECT ("a"."meta" -> %s), ("a"."meta" ->> %s), ("a"."meta" #>> %s) FROM "a"', "read", {"a"}),
    (
        'SELECT * FROM "a" WHERE "a"."meta" ? %s OR "a"."meta" ?| %s OR "a"."meta" ?& %s',
        "read",
        {"a"},
    ),
    ('SELECT * FROM "a" WHERE "a"."tags" && %s', "read", {"a"}),
    ('SELECT to_tsvector(COALESCE("b"."t", %s)) @@ plainto_tsquery(%s) FROM "b"', "read", {"b"}),
    ('SELECT * FROM "a" WHERE x = $1 AND y = :name AND z = %(z)s AND w = ?', "read", {"a"}),
    ("SELECT $$ FROM \"x\" $$, $tag$ it's $tag$, E'a\\'b' FROM \"a\"", "read", {"a"}),
    (
        'SELECT 1 FROM "a" WHERE x = 1e-3 AND y = ~5 AND z = 2 ^ 3 AND w = 7 & 3 | 1 AND v = !!5',
        "read",
        {"a"},
    ),
    ('SELECT 1 FROM "a" WHERE x = \'unterminated', "write", U),
    # --- no rows change ---
    ('SAVEPOINT "s1"', "other", set()),
    ("RELEASE SAVEPOINT s1", "other", set()),
    ("ROLLBACK", "other", set()),
    ("BEGIN", "other", set()),
    ("START TRANSACTION", "other", set()),
    ("END", "other", set()),
    ("SET LOCAL app.user = 1", "other", set()),
    ("PRAGMA foreign_keys = ON", "other", set()),
    ("LISTEN orders", "other", set()),
    ("NOTIFY orders, 'x'", "other", set()),
    ("LOCK TABLE t", "other", set()),
    ("EXPLAIN SELECT 1", "other", set()),
]


@pytest.mark.parametrize("statement,kind,tables", CLASSIFICATION)
def test_statement_tables(statement, kind, tables):
    got_kind, got = _held_rows.statement_tables(statement)
    assert got_kind == kind
    assert (None if got is None else set(got)) == tables


@pytest.mark.parametrize(
    "statement,write",
    [
        ('UPDATE "django_session" SET "session_data" = %s', False),
        ('INSERT INTO "django_session" ("session_key") VALUES (%s)', False),
        ('DELETE FROM "public"."django_session" WHERE 1', True),  # schema: UNKNOWN
        ('UPDATE "auth_group" SET "name" = %s', True),
        ("SELECT 1", False),
        (b'DELETE FROM "t"', True),
        (b"SELECT 1", False),
        (object(), True),
    ],
)
def test_is_write(statement, write):
    assert _held_rows.is_write(statement) is write


class SQL:
    """psycopg2's ``sql.SQL`` shape (``.string``); psycopg is not installed here."""

    def __init__(self, string):
        self.string = string


class Identifier:
    """psycopg2's ``sql.Identifier`` shape (``.strings``)."""

    def __init__(self, *strings):
        self.strings = strings


class Literal:
    def __init__(self, wrapped):
        self.wrapped = wrapped


class Composed:
    """psycopg2's ``sql.Composed`` shape (``.seq``)."""

    def __init__(self, *seq):
        self.seq = list(seq)


@pytest.mark.parametrize(
    "statement,kind,tables",
    [
        (Composed(SQL("UPDATE "), Identifier("t"), SQL(" SET x = 1")), "write", {"t"}),
        (Composed(SQL("UPDATE "), Identifier("app", "t"), SQL(" SET x = 1")), "write", U),
        (
            Composed(SQL("DELETE FROM "), Identifier("t"), SQL(" WHERE id = "), Literal(1)),
            "write",
            U,
        ),
        (Composed(SQL("SELECT * FROM "), Identifier("t")), "read", {"t"}),
        (Composed(SQL("SELECT * FROM a JOIN "), Identifier("t"), SQL(" ON 1 = 1")), "read", U),
        (Composed(Identifier('we"ird'), SQL(" x")), "write", U),
        (SQL('SELECT * FROM "t"'), "read", {"t"}),
    ],
)
def test_psycopg_composed_statements(statement, kind, tables):
    got_kind, got = _held_rows.statement_tables(statement)
    assert got_kind == kind
    assert (None if got is None else set(got)) == tables


# ============================================================================
# The decision (#4112, round 3)
# ============================================================================


class _Probe:
    """Stands for a view in the pipeline's order: a recorded render, a step, a decision."""

    def get_context_data(self):
        return {}

    def render_with_diff(self):
        return self._show()

    render_with_diff = _held_rows.recorded_render(render_with_diff)


def _turn(view, show, write):
    """render -> step(write) -> decision -> render; what the page then shows."""
    view._show = show
    view.render_with_diff()
    with _held_rows.writes_by(view):
        write()
    if _held_rows.rows_written(view):
        from djust.sync_turn import run_sync_step

        return run_sync_step(view, "render", view.render_with_diff)
    return show()


@pytest.mark.django_db(transaction=True)
def test_related_rows_read_at_render_time_are_fresh():
    """Review round 2 cases A, A2, B, C, C2 and K: the tables reach R as the
    render's own SELECTs (lazy foreign keys, prefetches, per-row queries)."""
    from django.contrib.contenttypes.models import ContentType

    owner = _world()
    group = Group.objects.get(name=TAG["value"] + "alice")
    perms = list(Permission.objects.order_by("pk")[:2])
    group.permissions.add(perms[0])
    content_type = ContentType.objects.get(pk=perms[0].content_type_id)
    original_model = content_type.model

    # A: a lazy foreign key followed at render time (no select_related)
    view = _Probe()
    view._rows = Permission.objects.filter(pk=perms[0].pk)
    show = lambda: [(p.codename, p.content_type.model) for p in view._rows]  # noqa: E731

    def rename_type():
        ContentType.objects.filter(pk=content_type.pk).update(model="renamed")

    try:
        assert _turn(view, show, rename_type) == [(perms[0].codename, "renamed")]
    finally:
        ContentType.objects.filter(pk=content_type.pk).update(model=original_model)
        ContentType.objects.clear_cache()

    # B: a prefetch read at render time
    view = _Probe()
    view._rows = Group.objects.filter(pk=group.pk).prefetch_related("permissions")
    show = lambda: [sorted(p.pk for p in g.permissions.all()) for g in view._rows]  # noqa: E731
    assert _turn(view, show, lambda: group.permissions.add(perms[1])) == [
        sorted(p.pk for p in perms)
    ]

    # C: a QuerySet filtering across a many-to-many; the write is to its through table
    view = _Probe()
    view._rows = get_user_model().objects.filter(groups__name=TAG["value"] + "zed")
    zed = Group.objects.create(name=TAG["value"] + "zed")
    show = lambda: [u.username for u in view._rows]  # noqa: E731
    assert _turn(view, show, lambda: owner.groups.add(zed)) == [owner.username]

    # K: a per-row value cached on held rows, counted at render time
    counted = Group.objects.create(name=TAG["value"] + "k")
    view = _Probe()
    view._rows = Group.objects.filter(pk=counted.pk)

    def show_k():
        out = []
        for row in view._rows:
            if not hasattr(row, "_n"):
                row._n = row.permissions.count()
            out.append(row._n)
        return out

    assert _turn(view, show_k, lambda: counted.permissions.add(perms[0])) == [1]


@pytest.mark.django_db(transaction=True)
def test_a_cached_property_computed_outside_a_recorded_render_makes_r_unknown():
    """Review round 2 case I: the read that filled it was never seen."""
    _world()

    class Cached(_Probe):
        @cached_property
        def names(self):
            return [g.name for g in _rows()]

    def rename_without_reading():  # an UPDATE alone: the step reads nothing itself
        Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob")

    view = Cached()
    view.names  # filled with nothing recording (a restore, a thread, a module cache)
    show = lambda: list(view.names)  # noqa: E731
    assert _turn(view, show, rename_without_reading) == [TAG["value"] + "bob"]

    # Filled outside, then a recorded render ran with it already there: still unknown.
    class Cached2(Cached):
        pass

    later = Cached2()
    later.names
    later._show = lambda: list(later.names)
    later.render_with_diff()
    later.render_with_diff()  # a second render does not make it known either
    with _held_rows.writes_by(later):
        Group.objects.filter(name=TAG["value"] + "bob").update(name=TAG["value"] + "cy")
    assert _held_rows.rows_written(later) is True
    _held_rows.unmark(later)

    # The same with no recorded render at all (the review's harness): the view
    # has no read set, and what it holds makes R UNKNOWN.
    class Plain:
        @cached_property
        def names(self):
            return [g.name for g in _rows()]

    plain = Plain()
    plain.names
    _held_rows.mark_tree(plain, frozenset({"auth_group"}))
    assert _held_rows.rows_written(plain) is True
    _held_rows.unmark(plain)


@pytest.mark.django_db(transaction=True)
def test_a_view_that_never_rendered_under_recording_has_r_unknown():
    _world()

    class Never:
        def render_with_diff(self):  # a view, but no recorded render has run
            return None

    view = Never()
    view._rows = rows = _rows()
    list(rows)
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is True
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
def test_a_restore_makes_r_unknown_until_the_next_render():
    _world()
    view = _Probe()
    view._rows = _rows()
    view._show = lambda: [g.name for g in view._rows]
    view.render_with_diff()
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is False  # known R, unrelated write
    _held_rows.reads_unknown(view)  # what djust's restore calls
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is True
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("switch", [False, True])
def test_writes_the_database_makes_by_itself_need_the_switch(switch):
    """Review round 2 cases D, E, H: a trigger, a database view, ON DELETE CASCADE.

    The trigger stands for all three: the statement djust sees writes one table,
    the database changes another. Stale by default (documented); fresh with
    ``DJUST_REFRESH_ON_ANY_WRITE``."""
    _world()
    rename = "UPDATE auth_group SET name = name || '!' WHERE name LIKE '%alice'"
    with connection.cursor() as cursor:
        if connection.vendor == "sqlite":
            cursor.execute(
                "CREATE TRIGGER held_rows_4112 AFTER INSERT ON auth_permission BEGIN %s; END"
                % rename
            )
        else:
            cursor.execute(
                "CREATE FUNCTION held_rows_4112() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN %s; RETURN NEW; END $$" % rename
            )
            cursor.execute(
                "CREATE TRIGGER held_rows_4112 AFTER INSERT ON auth_permission "
                "FOR EACH ROW EXECUTE FUNCTION held_rows_4112()"
            )
    try:
        view = _Probe()
        view._rows = _rows()
        show = lambda: [g.name for g in view._rows]  # noqa: E731
        with override_settings(DJUST_REFRESH_ON_ANY_WRITE=switch):
            shown = _turn(view, show, _permission)
        truth = [g.name for g in _rows()]
        assert (shown == truth) is switch, (shown, truth)
    finally:
        with connection.cursor() as cursor:
            if connection.vendor == "sqlite":
                cursor.execute("DROP TRIGGER held_rows_4112")
            else:
                cursor.execute("DROP TRIGGER held_rows_4112 ON auth_permission")
                cursor.execute("DROP FUNCTION held_rows_4112()")


@pytest.mark.django_db(transaction=True)
def test_a_mark_made_during_a_render_survives_it():
    from djust.sync_turn import run_sync_step

    _world()
    view = _Probe()
    view._rows = _rows()
    list(view._rows)
    _held_rows.mark_tree(view, None)

    def render():
        list(view._rows)
        _held_rows.mark_tree(view, None)  # another thread's write, mid-render
        return 1

    run_sync_step(view, "render", render)
    assert _held_rows.is_marked(view)
    run_sync_step(view, "render", lambda: 1)
    assert not _held_rows.is_marked(view)


def test_the_read_set_overflows_to_unknown():
    class View:
        pass

    view = View()
    _held_rows.note_reads(view, frozenset("t%d" % i for i in range(500)))
    assert _held_rows._read_sets[id(view)].unknown is False
    _held_rows.note_reads(view, frozenset("u%d" % i for i in range(20)))  # 520 > 512
    entry = _held_rows._read_sets[id(view)]
    assert entry.unknown is True and entry.tables == set()
    _held_rows.note_reads(view, frozenset({"v"}))
    assert entry.unknown is True  # stays UNKNOWN


def test_concurrent_reads_never_lose_the_overflow():
    """``note_reads`` is under the lock: threads adding at once end UNKNOWN, never
    with a partial set past the limit (a free-threaded build has no GIL to lean on)."""

    class View:
        pass

    view = View()
    barrier = threading.Barrier(8)

    def add(n):
        barrier.wait(5)
        for i in range(100):
            _held_rows.note_reads(view, frozenset({"t%d_%d" % (n, i)}))

    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    entry = _held_rows._read_sets[id(view)]
    assert entry.unknown is True and entry.tables == set()


class TickWritesUsers(HeldQuerySet):
    """A tick that writes ``auth_user`` (a last-seen stamp): the framework's own
    per-turn user check reads ``auth_user`` and must not put it in R."""

    tick_interval = 50

    def handle_tick(self) -> None:
        get_user_model().objects.filter(username="owner" + TAG["value"]).update(last_login=None)


urlpatterns.append(path("late/tick-users/", TickWritesUsers.as_view()))
URL[TickWritesUsers] = "/late/tick-users/"


@pytest.mark.django_db(transaction=True)
async def test_framework_reads_do_not_enter_the_read_set():
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(TickWritesUsers, owner)
    try:
        assert await client.sock.receive_nothing(timeout=0.5, interval=0.05)
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_the_switch_makes_an_unrelated_write_refresh(sql):
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(Unrelated, owner)
    try:
        with override_settings(DJUST_REFRESH_ON_ANY_WRITE=True):
            sql.clear()
            (frame,) = await client.event("other_table")
        assert frame.get("type") == "patch", frame
        assert _row_reads(sql) == 1, sql
    finally:
        await client.close()


class ProbeAuthor(models.Model):
    name = models.CharField(max_length=50)

    class Meta:
        app_label = "held_rows_probe_4112"
        db_table = "HeldAuthor4112"  # mixed case: compared case-folded


class ProbePost(models.Model):
    title = models.CharField(max_length=50)
    author = models.ForeignKey(ProbeAuthor, on_delete=models.CASCADE)

    class Meta:
        app_label = "held_rows_probe_4112"
        db_table = "held_post_4112"


@pytest.fixture
def probe_tables(transactional_db):
    with connection.schema_editor() as editor:
        editor.create_model(ProbeAuthor)
        editor.create_model(ProbePost)
    yield
    with connection.schema_editor() as editor:
        editor.delete_model(ProbePost)
        editor.delete_model(ProbeAuthor)


@pytest.mark.parametrize("load", ["lazy", "prefetch_related", "select_related"])
def test_a_foreign_key_read_at_render_time_on_a_mixed_case_table(probe_tables, load):
    """Review round 2 cases A, B and B2: the related row is read lazily, by a
    prefetch, or by a join; the related table's name is mixed case."""
    author = ProbeAuthor.objects.create(name="ann")
    ProbePost.objects.create(title="p1", author=author)
    view = _Probe()
    rows = ProbePost.objects.order_by("pk")
    if load != "lazy":
        rows = getattr(rows, load)("author")
    view._rows = rows
    show = lambda: [(p.title, p.author.name) for p in view._rows]  # noqa: E731
    shown = _turn(view, show, lambda: ProbeAuthor.objects.filter(pk=author.pk).update(name="bob"))
    assert shown == [("p1", "bob")]


class ProbeOverView(models.Model):
    """A model over a database view: the database changes its rows by itself."""

    title = models.CharField(max_length=50)

    class Meta:
        app_label = "held_rows_probe_4112"
        db_table = "held_post_view_4112"
        managed = False


@pytest.mark.parametrize("setting,expected", [(False, ["djust.C042"]), (True, [])])
def test_c042_names_models_the_database_changes_by_itself(monkeypatch, setting, expected):
    from django.apps import apps

    from djust.checks.configuration import _check_database_side_writes

    monkeypatch.setattr(apps, "get_models", lambda: [Group, ProbeOverView])
    errors: list = []
    with override_settings(DJUST_REFRESH_ON_ANY_WRITE=setting):
        _check_database_side_writes(errors)
    assert [e.id for e in errors] == expected
    if errors:
        assert "held_rows_probe_4112.ProbeOverView (managed = False)" in errors[0].msg
        assert "auth.Group" not in errors[0].msg


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("restore", ["restore_public_state", "restore_snapshot"])
def test_djusts_restores_make_r_unknown(restore):
    from types import SimpleNamespace

    _world()
    view = _Probe()
    view._rows = _rows()
    view._show = lambda: [g.name for g in view._rows]
    view.render_with_diff()
    if restore == "restore_public_state":
        from djust._state_restore import restore_public_state

        restore_public_state(view, {})
    else:
        from djust.time_travel import restore_snapshot

        restore_snapshot(view, SimpleNamespace(restorable=False), "before")
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is True
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
def test_a_statement_a_render_cannot_follow_makes_r_unknown():
    """A statement in a render that neither reader accepts: R becomes UNKNOWN."""
    _world()
    view = _Probe()

    def show():
        with connection.cursor() as cursor:
            cursor.execute("VALUES (1)")  # a read no grammar here accepts
            cursor.fetchall()
        return 1

    view._show = show
    view.render_with_diff()
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is True
    _held_rows.unmark(view)


def test_c042_names_a_cascade_the_database_performs(monkeypatch):
    from types import SimpleNamespace

    from django.apps import apps

    from djust.checks.configuration import _check_database_side_writes

    def DB_CASCADE():  # noqa: N802 - the name Django gives it
        pass

    field = SimpleNamespace(name="post", remote_field=SimpleNamespace(on_delete=DB_CASCADE))
    meta = SimpleNamespace(
        proxy=False, managed=True, label="app.Comment", get_fields=lambda: [field]
    )
    monkeypatch.setattr(apps, "get_models", lambda: [SimpleNamespace(_meta=meta)])
    errors: list = []
    with override_settings(DJUST_REFRESH_ON_ANY_WRITE=False):
        _check_database_side_writes(errors)
    assert [e.id for e in errors] == ["djust.C042"]
    assert "app.Comment.post (DB_CASCADE)" in errors[0].msg


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("entry", ["sync_step", "run_sync_step"])
def test_a_render_step_records_the_views_reads(entry):
    """The render step alone (as the component-scoped render and StreamingMixin
    run ``get_context_data``, outside the decorated render methods)."""
    from djust import sync_turn

    _world()

    class View:
        pass

    view = View()

    def get_context_data():
        return {"rows": list(_rows())}

    if entry == "sync_step":
        sync_turn.sync_step(view, "render", get_context_data)()
    else:
        sync_turn.run_sync_step(view, "render", get_context_data)
    assert "auth_group" in _held_rows._read_sets[id(view)].tables


# ============================================================================
# Ground-truth fuzz (the third review's, seeded and bounded): KNOWN implies
# exactly the statement's real tables.
# ============================================================================

FUZZ_IDENT_STYLES = [
    lambda n: '"%s"' % n,
    lambda n: n,
    lambda n: '"sch"."%s"' % n,
    lambda n: "sch.%s" % n,
    lambda n: "`%s`" % n,
    lambda n: "[%s]" % n,
    lambda n: '"%s"' % n.upper(),
    lambda n: '"%s"' % (n[0] + '""' + n[1:]),  # an escaped quote inside
]

# (template, truth) -- truth: the tables the statement really writes (by the names a, b), "?" = cannot be known from text
FUZZ_WRITE_TEMPLATES = [
    ("UPDATE {a} SET x = 1", {"a"}),
    ("UPDATE {a} SET x = 1 WHERE id IN (SELECT id FROM {b})", {"a"}),
    ("UPDATE {a} SET x = (SELECT max(y) FROM {b}) WHERE 1=1 RETURNING id", {"a"}),
    ("UPDATE {a} SET x = {b}.y FROM {b} WHERE {a}.id = {b}.id", {"a"}),
    ("UPDATE {a}, {b} SET {a}.x = 1, {b}.y = 2", {"a", "b"}),
    ("UPDATE {a} JOIN {b} ON 1=1 SET {b}.y = 2", {"b"}),
    ("UPDATE {a} INNER JOIN {b} ON 1=1 SET {a}.x=1, {b}.y = 2", {"a", "b"}),
    ("UPDATE {a} AS q SET x = 1", {"a"}),
    ("UPDATE {a} q SET x = 1", {"a"}),
    ("UPDATE ONLY {a} SET x = 1", {"a"}),
    ("UPDATE LOW_PRIORITY {a} SET x = 1", {"a"}),
    ("UPDATE OR REPLACE {a} SET x = 1", {"a"}),
    ("UPDATE TOP (5) {a} SET x = 1", {"a"}),
    ("INSERT INTO {a} (x) VALUES (1)", {"a"}),
    ("INSERT INTO {a} VALUES (1), (2) RETURNING id", {"a"}),
    ("INSERT INTO {a} DEFAULT VALUES", {"a"}),
    ("INSERT INTO {a} (x) VALUES (1) ON CONFLICT (x) DO UPDATE SET x = EXCLUDED.x", {"a"}),
    ("INSERT INTO {a} (x) VALUES (1) ON DUPLICATE KEY UPDATE x = 2", {"a"}),
    ("INSERT OR IGNORE INTO {a} (x) VALUES (1)", {"a"}),
    ("INSERT IGNORE INTO {a} (x) VALUES (1)", {"a"}),
    ("INSERT OR REPLACE INTO {a} (x) VALUES (1)", {"a"}),
    ("INSERT LOW_PRIORITY INTO {a} VALUES (1)", {"a"}),
    ("INSERT {a} VALUES (1)", {"a"}),
    ("INSERT INTO {a} AS q (x) VALUES (1)", {"a"}),
    ("INSERT INTO {a} (x) SELECT y FROM {b}", {"a"}),
    ("INSERT INTO {a} (x) SELECT * FROM UNNEST(%s)", {"a"}),
    ("INSERT INTO {a} (x) VALUES ((SELECT max(y) FROM {b}))", {"a"}),
    ("INSERT ALL INTO {a} VALUES (1) INTO {b} VALUES (2) SELECT 1 FROM dual", {"a", "b"}),
    ("INSERT INTO {a} (x) VALUES (1); INSERT INTO {b} (y) VALUES (2)", {"a", "b"}),
    ("INSERT INTO {a} (x) VALUES (1); DELETE FROM {b}", {"a", "b"}),
    ("DELETE FROM {a}", {"a"}),
    ("DELETE FROM {a} WHERE id IN (SELECT id FROM {b})", {"a"}),
    ("DELETE FROM {a} WHERE id = 1 RETURNING id", {"a"}),
    ("DELETE FROM {a} USING {b} WHERE 1=1", {"a"}),
    ("DELETE FROM {a}, {b} USING {a} JOIN {b} ON 1=1", {"a", "b"}),
    ("DELETE {a}, {b} FROM {a} JOIN {b} ON 1=1", {"a", "b"}),
    ("DELETE {a} FROM {a} INNER JOIN {b} ON 1=1", {"a"}),
    ("DELETE q FROM {a} AS q WHERE 1=1", {"a"}),
    ("DELETE FROM {a} AS q WHERE 1=1", {"a"}),
    ("DELETE FROM {a} q WHERE 1=1", {"a"}),
    ("DELETE FROM ONLY {a}", {"a"}),
    ("DELETE QUICK FROM {a}", {"a"}),
    ("DELETE {a} WHERE id=1", {"a"}),
    ("DELETE FROM {a} ORDER BY id LIMIT 1", {"a"}),
    ("REPLACE INTO {a} VALUES (1)", {"a"}),
    ("MERGE INTO {a} USING {b} ON 1=1 WHEN MATCHED THEN DELETE", {"a"}),
    ("TRUNCATE {a}, {b} CASCADE", {"?"}),
    ("TRUNCATE {a}", {"a"}),
    ("WITH x AS (DELETE FROM {b} RETURNING id) UPDATE {a} SET y = 1", {"a", "b"}),
    ("WITH x AS (SELECT 1) INSERT INTO {a} SELECT * FROM x", {"a"}),
    (
        "UPDATE {a} SET x = 1 WHERE id IN (WITH d AS (DELETE FROM {b} RETURNING id) SELECT id FROM d)",
        {"a", "b"},
    ),
    ("BEGIN; UPDATE {a} SET x = 1; COMMIT", {"a"}),
    ("SELECT 1; DELETE FROM {a}", {"a"}),
    ("SELECT * INTO {a} FROM {b}", {"a"}),
    ("EXPLAIN ANALYZE DELETE FROM {a}", {"a"}),
    ("EXPLAIN (ANALYZE) UPDATE {a} SET x=1", {"a"}),
    ("WITH d AS (DELETE FROM {a} RETURNING id) SELECT * FROM d", {"a"}),
    ("COPY {a} FROM STDIN", {"a"}),
    ("CALL p({a})", {"?"}),
    ("LOCK TABLE {a}", set()),
    ("SAVEPOINT s", set()),
    ("SET x = 1", set()),
    ("UPDATE {a} SET note = 'x; DELETE FROM q' WHERE 1=1", {"a"}),
    ("UPDATE {a} SET note = 'it''s FROM {b}'", {"a"}),
    ("/*! UPDATE */ UPDATE {a} SET x = 1", {"a"}),
    ("UPDATE {a} SET x = 1 /* ; DELETE FROM {b} */", {"a"}),
    ("UPDATE {a} SET x = $$ ; $$", {"a"}),
]


def _fuzz_noise(rng, sql):
    out = []
    for part in sql.split(" "):
        out.append(part)
        r = rng.random()
        out.append(
            "\n\t " if r < 0.1 else (" /* c */ " if r < 0.15 else (" -- c\n" if r < 0.2 else " "))
        )
    s = "".join(out).strip()
    if rng.random() < 0.3:
        s = s.lower() if "{" not in s and rng.random() < 0 else s
    if rng.random() < 0.2:
        s += ";"
    if rng.random() < 0.2:
        s = "  " + s
    return s


FUZZ_READ_TEMPLATES = [
    ("SELECT * FROM {a}", {"a"}),
    ("SELECT * FROM {a} x INNER JOIN {b} y ON x.id = y.id", {"a", "b"}),
    (
        "SELECT * FROM {a} AS x LEFT OUTER JOIN {b} AS y ON (x.id = y.id) WHERE x.id > 0 ORDER BY 1",
        {"a", "b"},
    ),
    ("SELECT * FROM {a}, {b}", {"a", "b"}),
    ("SELECT * FROM {a} x, {b} y WHERE 1=1", {"a", "b"}),
    ("SELECT (SELECT max(id) FROM {b}) FROM {a}", {"a", "b"}),
    ("SELECT * FROM {a} WHERE EXISTS (SELECT 1 FROM {b} U0 WHERE U0.id = 1)", {"a", "b"}),
    ("SELECT * FROM (SELECT * FROM {a}) s JOIN {b} ON 1=1", {"a", "b"}),
    ("SELECT COUNT(*) FROM (SELECT DISTINCT x FROM {a}) subquery", {"a"}),
    ("SELECT EXTRACT(YEAR FROM d) FROM {a}", {"a"}),
    ("SELECT EXTRACT(YEAR FROM (SELECT max(d) FROM {b})) FROM {a}", {"a", "b"}),
    ("SELECT SUBSTRING(x FROM 2), TRIM(BOTH 'x' FROM y) FROM {a}", {"a"}),
    ("SELECT x IS DISTINCT FROM y FROM {a}", {"a"}),
    ("SELECT x IS NOT DISTINCT FROM (SELECT max(y) FROM {b}) FROM {a}", {"a", "b"}),
    ("SELECT DISTINCT x FROM {a}", {"a"}),
    ("SELECT x AS distinct FROM {a}", {"a"}),
    ("SELECT ALL x, 1 distinct FROM {a}", {"a"}),
    ("SELECT x FROM {a} UNION SELECT y FROM {b}", {"a", "b"}),
    ("(SELECT x FROM {a}) UNION ALL (SELECT y FROM {b})", {"a", "b"}),
    ("SELECT * FROM {a} FOR UPDATE", {"a"}),
    ("SELECT * FROM {a} NATURAL JOIN {b}", {"a", "b"}),
    ("SELECT * FROM {a} CROSS JOIN {b}", {"a", "b"}),
    ("SELECT * FROM {a} STRAIGHT_JOIN {b}", {"a", "b"}),
    ("SELECT * FROM ONLY {a}", {"a"}),
    ("SELECT * FROM {a} TABLESAMPLE SYSTEM (1)", {"a"}),
    ("SELECT * FROM {a} JOIN LATERAL (SELECT * FROM {b}) z ON true", {"a", "b"}),
    ("SELECT * FROM {a}, LATERAL (SELECT * FROM {b}) z", {"a", "b"}),
    ("SELECT * FROM {a} WHERE x IN (TABLE {b})", {"a", "b"}),
    ("SELECT * FROM {a} WHERE note = 'FROM zzz' AND \"from\" = 1", {"a"}),
    (
        "SELECT x, (y) FROM {a} WHERE c IN (1,2) GROUP BY x HAVING count(*) > 1 LIMIT 3 OFFSET 1",
        {"a"},
    ),
    (
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) FROM {a} WINDOW w AS (ORDER BY x)",
        {"a"},
    ),
    ("SELECT * FROM {a} PARTITION (p0)", {"a"}),
    ("SELECT * FROM {a} USE INDEX (i) JOIN {b} ON 1=1", {"a", "b"}),
    ("SELECT * FROM {a} FINAL JOIN {b} USING (id)", {"a", "b"}),
    ("SELECT * FROM {a} AS x (c1, c2), {b}", {"a", "b"}),
    ("SELECT CAST(x AS int) FROM {a} LEFT JOIN {b} ON CAST({b}.y AS int) = 1", {"a", "b"}),
    ("SELECT * FROM {a} t1 JOIN ({b} t2 JOIN {a} t3 ON 1=1) ON 1=1", {"a", "b"}),
    ("SELECT * FROM ({a} JOIN {b} ON 1=1)", {"a", "b"}),
]


def _fuzz(templates, seed):
    import random

    rng = random.Random(seed)
    for template, truth in templates:
        for style_a in FUZZ_IDENT_STYLES:
            for style_b in FUZZ_IDENT_STYLES[:6]:
                for _ in range(3):
                    sql = _fuzz_noise(rng, template)
                    sql = sql.replace("{a}", style_a("ta")).replace("{b}", style_b("tb"))
                    real = {"ta" if t == "a" else "tb" if t == "b" else t for t in truth}
                    yield sql, real, _held_rows.statement_tables(sql)


def test_write_fuzz_against_ground_truth():
    wrong, misread, known = [], [], 0
    for sql, real, (kind, tables) in _fuzz(FUZZ_WRITE_TEMPLATES, 4112):
        if not real:
            if kind == "write" and tables is not None:
                wrong.append((sql, tables, real))
            continue
        if kind != "write":
            misread.append((sql, kind))
            continue
        if tables is not None:
            known += 1
            if "?" in real or set(tables) != real:
                wrong.append((sql, sorted(tables), sorted(real)))
    assert not misread, misread[:5]
    assert not wrong, wrong[:5]
    assert known > 500  # the ORM's own shapes stay KNOWN


def test_read_fuzz_against_ground_truth():
    wrong, known = [], 0
    for sql, real, (kind, tables) in _fuzz(FUZZ_READ_TEMPLATES, 41120):
        assert kind in ("read", "write"), (sql, kind)
        if kind == "read" and tables is not None:
            known += 1
            if set(tables) != real:
                wrong.append((sql, sorted(tables), sorted(real)))
    assert not wrong, wrong[:5]
    assert known > 500


# ============================================================================
# Third review: the walk has no shortcut on a refresh; provenance by fill stamps
# ============================================================================


class _Shape(_Explicit):
    @event_handler()
    def write_only(self, **kwargs: Any) -> None:
        Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob")

    @event_handler()
    def other_table(self, **kwargs: Any) -> None:
        _permission()


class AfterEightInList(_Shape):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._panel = ["title", 1, 2, 3, 4, 5, 6, 7, _rows()]

    def rows_value(self) -> Any:
        return self._panel[8]


class AfterEightInDict(_Shape):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._ctx = {
            "t": "t",
            "p": 1,
            "per": 10,
            "s": "pk",
            "d": "asc",
            "q": "",
            "a": 1,
            "b": 2,
            "rows": _rows(),
        }

    def rows_value(self) -> Any:
        return self._ctx["rows"]


class RecordsThenRows(_Shape):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._tabs = [{"id": i, "label": "tab%d" % i} for i in range(8)] + [
            {"id": 9, "rows": _rows()}
        ]

    def rows_value(self) -> Any:
        return self._tabs[8]["rows"]


class EvaluatedInMount(_Shape):
    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = _rows()
        self.total = len(self._rows)  # evaluated in mount(), a recorded step

    def rows_value(self) -> Any:
        return self._rows


class TouchedInHandler(_Shape):
    @cached_property
    def names(self) -> list:
        return [g.name for g in _rows()]

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        self._rows = _rows()

    def rows_value(self) -> Any:
        return self._rows

    @event_handler()
    def touch(self, **kwargs: Any) -> None:
        self.k = len(self.names)  # first computed in a handler (a recorded step)


class SessionRows(_Shape):
    """A view listing ``django_session`` rows: a bookkeeping table (documented)."""

    template = "<div dj-root><ul>{% for r in rows %}<li>{{ r.session_key }}</li>{% endfor %}</ul><p>{{ n }}</p></div>"

    def mount(self, request: Any, **kwargs: Any) -> None:
        super().mount(request, **kwargs)
        from django.contrib.sessions.models import Session

        self._rows = Session.objects.order_by("session_key")

    def rows_value(self) -> Any:
        return self._rows

    @event_handler()
    def add_session(self, **kwargs: Any) -> None:
        import datetime

        from django.contrib.sessions.models import Session
        from django.utils import timezone

        Session.objects.create(
            session_key="zz-held-" + TAG["value"],
            session_data="x",
            expire_date=timezone.now() + datetime.timedelta(days=1),
        )


for _cls in (
    AfterEightInList,
    AfterEightInDict,
    RecordsThenRows,
    EvaluatedInMount,
    TouchedInHandler,
    SessionRows,
):
    urlpatterns.append(path("third/%s/" % _cls.__name__, _cls.as_view()))
    URL[_cls] = "/third/%s/" % _cls.__name__


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cls", [AfterEightInList, AfterEightInDict, RecordsThenRows])
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_a_queryset_behind_plain_items_is_refreshed(request, transport, cls, sql):
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(cls, owner)
    try:
        sql.clear()
        (frame,) = await client.event("write_only")
        assert _change(frame) == {"bob"}, frame
        assert _row_reads(sql) == 1, sql
        sql.clear()
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop", frame  # R is known: an unrelated write costs nothing
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", list(TRANSPORTS))
async def test_rows_evaluated_in_mount_are_known(request, transport, sql):
    """mount() is a recorded step: its reads are in R and its QuerySet is stamped."""
    owner = await sync_to_async(_world)()
    client = _client(transport, request)
    await client.open(EvaluatedInMount, owner)
    try:
        for _ in range(2):
            sql.clear()
            (frame,) = await client.event("other_table")
            assert frame.get("type") == "noop", frame
            assert _row_reads(sql) == 0, sql
        (frame,) = await client.event("write_only")
        assert _change(frame) == {"bob"}, frame
        sql.clear()
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop" and _row_reads(sql) == 0, (
            frame
        )  # still known after the refresh
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_cached_property_computed_in_a_handler_is_known():
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(TouchedInHandler, owner)
    try:
        await client.event("touch")
        (frame,) = await client.event("other_table")
        assert frame.get("type") == "noop", frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
async def test_a_view_of_session_rows_is_not_refreshed_by_session_writes():
    """Documented: writes to an ignored (bookkeeping) table never count."""
    owner = await sync_to_async(_world)()
    client = _WS()
    await client.open(SessionRows, owner)
    try:
        (frame,) = await client.event("add_session")
        assert frame.get("type") == "noop", frame
    finally:
        await client.close()


@pytest.mark.django_db(transaction=True)
def test_r_recovers_after_a_refresh():
    """A cached value filled while nothing recorded: R UNKNOWN, everything is read
    again; refilled inside the recorded render, R is known again."""
    _world()

    class Cached(_Probe):
        @cached_property
        def names(self):
            return [g.name for g in _rows()]

    view = Cached()
    view._rows = _rows()
    list(view._rows)  # filled outside a recorded step: unknown too
    view.names
    show = lambda: (list(view.names), [g.name for g in view._rows])  # noqa: E731
    shown = _turn(
        view,
        show,
        lambda: Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob"),
    )
    assert shown[0] == [TAG["value"] + "bob"]
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is False  # known again: nothing to do


@pytest.mark.django_db
def test_a_queryset_filled_outside_any_step_is_unknown_wherever_it_is_held():
    _world()

    class View(_Probe):
        pass

    view = View()
    view._show = lambda: 1
    view.render_with_diff()  # R complete, nothing held
    late = _rows()
    list(late)  # filled with nothing recording
    view._ctx = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6, "g": 7, "h": 8, "rows": late}
    _held_rows.mark_tree(view, frozenset({"auth_permission"}))
    assert _held_rows.rows_written(view) is True
    _held_rows.unmark(view)


@pytest.mark.django_db(transaction=True)
def test_rows_filled_outside_stay_unknown_after_a_render_iterates_them():
    """Only a fill stamps: a render reading the result cache of rows filled while
    nothing recorded ran no query, so their table is not in R."""
    _world()
    view = _Probe()
    view._rows = _rows()
    list(view._rows)  # filled with nothing recording
    show = lambda: [g.name for g in view._rows]  # noqa: E731

    def rename_without_reading() -> None:  # one UPDATE: the step reads nothing
        Group.objects.filter(name=TAG["value"] + "alice").update(name=TAG["value"] + "bob")

    shown = _turn(view, show, rename_without_reading)
    assert shown == [g.name for g in _rows()]


@pytest.mark.parametrize(
    "sql",
    [
        'WITH a AS (SELECT 1) SELECT * FROM a WHERE (SELECT 1 FROM (DELETE FROM "tb" RETURNING 1) x) = 1',
        'WITH a AS (SELECT * FROM "ta" WHERE x IN (SELECT 1 FROM (DELETE FROM "tb" RETURNING 1) d)) SELECT * FROM a',
    ],
)
def test_a_select_part_that_writes_makes_a_with_a_write(sql):
    assert _held_rows.statement_tables(sql) == ("write", None)
