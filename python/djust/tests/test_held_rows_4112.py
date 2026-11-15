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
from django.db import connection, connections, transaction
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
        cursor.execute(
            "UPDATE auth_group SET name = ? WHERE name = ?",
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
    """Plain data set before the QuerySet: it must not spend the walk."""

    def mount(self, request: Any, **kwargs: Any) -> None:
        self._points = list(range(12_000))
        self._records = [{"a": i, "b": "x", "c": 1.5, "d": None, "e": True} for i in range(1_700)]
        self._many = [{"k": i} for i in range(12_000)]  # 12,000 containers: spends the walk
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
            cursor.execute("SELECT value FROM json_each('[1, 2]')")
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


@pytest.mark.parametrize(
    "statement,write",
    [
        ("SELECT 1", False),
        ('  SELECT "a" FROM "t" FOR UPDATE', False),
        ('(SELECT "a" FROM "t") UNION (SELECT "b" FROM "u")', False),
        ('SAVEPOINT "s1"', False),
        ('RELEASE SAVEPOINT "s1"', False),
        ("ROLLBACK", False),
        ("BEGIN", False),
        ("SET LOCAL app.user = 1", False),
        ("PRAGMA foreign_keys", False),
        ("INSERT INTO t VALUES (1)", True),
        ("update t set x = 1", True),
        ("DELETE FROM t", True),
        ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", True),
        ("/* app=shop */ SELECT 1", False),
        ("-- a note\n  /* another */ (SELECT 1)", False),
        ("/* app=shop */ UPDATE t SET x = 1", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", False),
        ("WITH RECURSIVE x(n) AS (SELECT 1 UNION SELECT n + 1 FROM x) SELECT * FROM x", False),
        ("WITH x AS MATERIALIZED (SELECT ')' AS p) SELECT * FROM x", False),
        ("WITH a AS (SELECT 1), b AS (SELECT 2) UPDATE t SET x = 1", True),
        ("WITH a AS (SELECT 1), b AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1", True),
        ("START TRANSACTION", False),
        ("END", False),
        ("LISTEN orders", False),
        ("NOTIFY orders, 'x'", False),
        ("DESCRIBE t", False),
        ("USE shop", False),
        ("TRUNCATE t", True),
        ("MERGE INTO t USING u ON t.id = u.id WHEN MATCHED THEN DELETE", True),
        (b"SELECT 1", False),
        (b"DELETE FROM t", True),
        # Django's and djust's bookkeeping: the session, the database cache tables
        ('UPDATE "django_session" SET "session_data" = %s', False),
        ('INSERT INTO "django_session" ("session_key") VALUES (%s)', False),
        ('DELETE FROM "public"."django_session" WHERE 1', False),
        ("", True),  # unreadable: one extra re-read, never stale rows
    ],
)
def test_is_write(statement, write):
    assert _held_rows.is_write(statement) is write


class _FakeSQL:
    """The shape of psycopg2's ``sql.SQL`` (``.string``); psycopg is not installed here."""

    def __init__(self, string):
        self.string = string


class _FakeComposed:
    """The shape of psycopg2's ``sql.Composed`` (``.seq``)."""

    def __init__(self, *seq):
        self.seq = list(seq)


class _FakeSQL3:
    """The shape of psycopg 3's ``sql.SQL`` / ``sql.Composed`` (``._obj``)."""

    def __init__(self, obj):
        self._obj = obj


@pytest.mark.parametrize(
    "statement,write",
    [
        (_FakeComposed(_FakeSQL("SELECT * FROM "), _FakeSQL('"t"')), False),
        (_FakeComposed(_FakeComposed(_FakeSQL("UPDATE t SET x = ")), 1), True),
        (_FakeSQL3([_FakeSQL3("SELECT 1")]), False),
        (_FakeSQL3([_FakeSQL3("DELETE FROM t")]), True),
        (object(), True),
    ],
)
def test_is_write_reads_composed_statements(statement, write):
    assert _held_rows.is_write(statement) is write


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


def test_the_walk_warns_once_per_class_when_spent(caplog):
    class Holder:
        pass

    holder = Holder()
    holder.__dict__["_many"] = [{"k": i} for i in range(_held_rows._VISIT_LIMIT + 10)]
    _held_rows._budget_warned.discard(Holder)
    try:
        with caplog.at_level("WARNING", logger="djust._held_rows"):
            _held_rows.reset_held_rows(holder)
            _held_rows.reset_held_rows(holder)
    finally:
        _held_rows._budget_warned.discard(Holder)
    spent = [r.getMessage() for r in caplog.records if "Holder" in r.getMessage()]
    assert len(spent) == 1, spent
    assert "'k'" not in spent[0] and "10010" not in spent[0]  # it names no values


@pytest.mark.django_db
def test_scalar_containers_do_not_spend_the_walk():
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
def test_depth_zero_querysets_are_reset_even_after_the_walk_is_spent():
    _world()

    class Holder:
        pass

    holder = Holder()
    holder.__dict__["_many"] = [{"k": i} for i in range(12_000)]  # past the 10,000 limit
    holder.__dict__["_rows"] = rows = _rows()
    holder.__dict__["_late"] = late = [_rows()]
    list(rows)
    list(late[0])
    _held_rows._budget_warned.add(Holder)  # quiet
    try:
        _held_rows.reset_held_rows(holder)
    finally:
        _held_rows._budget_warned.discard(Holder)
    assert rows._result_cache is None
    assert late[0]._result_cache is not None  # past the spent walk: documented


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
        assert any("could not be read" in r.getMessage() for r in caplog.records)
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


@pytest.mark.parametrize(
    "statement,kind,tables",
    [
        ('SELECT "a"."x" FROM "a" INNER JOIN "b" T3 ON ("a"."id" = T3."a_id")', "read", {"a", "b"}),
        ('SELECT * FROM "public"."a" AS x, "b" WHERE 1', "read", {"a", "b"}),
        ('SELECT 1 FROM (SELECT "id" FROM "c") sub', "read", {"c"}),
        ('SELECT EXISTS(SELECT 1 FROM "d" U0 WHERE U0."x" = "e"."x") FROM "e"', "read", {"d", "e"}),
        ("SELECT * FROM generate_series(1, 3)", "read", None),
        ("SELECT 'FROM nowhere'", "read", set()),
        ('INSERT INTO "auth_permission" ("name") VALUES (%s)', "write", {"auth_permission"}),
        ('INSERT OR IGNORE INTO "t" VALUES (1)', "write", {"t"}),
        ('UPDATE "auth_group" SET "name" = %s WHERE "id" = %s', "write", {"auth_group"}),
        ('DELETE FROM "auth_user_groups" WHERE "id" IN (%s)', "write", {"auth_user_groups"}),
        ("TRUNCATE a, b", "write", {"a", "b"}),
        ("CREATE TABLE t (x INTEGER)", "write", None),
        ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "write", None),
        ('WITH x AS (SELECT 1 FROM "t") SELECT * FROM x', "read", {"t", "x"}),
        ("SAVEPOINT s1", "other", set()),
    ],
)
def test_statement_tables(statement, kind, tables):
    got_kind, got = _held_rows.statement_tables(statement)
    assert got_kind == kind
    assert (None if got is None else set(got)) == tables


@pytest.mark.django_db
def test_queryset_tables():
    from django.db.models import Exists, OuterRef, Prefetch, Subquery

    User = get_user_model()
    tables = _held_rows.queryset_tables
    assert tables(Group.objects.all()) == {"auth_group"}
    assert tables(Group.objects.filter(user__username="x")) == {
        "auth_group",
        "auth_user_groups",
        "auth_user",
    }
    assert tables(Permission.objects.select_related("content_type")) == {
        "auth_permission",
        "django_content_type",
    }
    assert tables(User.objects.prefetch_related("groups__permissions")) >= {
        "auth_user",
        "auth_user_groups",
        "auth_group",
        "auth_group_permissions",
        "auth_permission",
    }
    assert tables(Group.objects.prefetch_related("user_set")) >= {"auth_user", "auth_user_groups"}
    assert "django_content_type" in tables(
        Group.objects.prefetch_related(
            Prefetch("permissions", queryset=Permission.objects.select_related("content_type"))
        )
    )
    assert "auth_permission" in tables(
        Group.objects.filter(Exists(Permission.objects.filter(group=OuterRef("pk"))))
    )
    assert "auth_user" in tables(
        Group.objects.annotate(u=Subquery(User.objects.values("username")[:1]))
    )
    assert "auth_permission" in tables(
        Group.objects.values_list("name").union(Permission.objects.order_by().values_list("name"))
    )
    assert tables(Group.objects.none()) == {"auth_group"}
    assert tables(Group.objects.prefetch_related("no_such_relation")) is None


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
