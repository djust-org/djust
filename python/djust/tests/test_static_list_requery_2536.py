"""#2536 — a ``list[Model]`` assigned once in ``mount()`` must not be
re-queried on every event, and an in-memory row mutation must render.

Reproduced through the real path the issue names: a ``WebsocketCommunicator``
against ``LiveViewConsumer``, one ``mount`` and then events that do NOT touch
the list. The query log is installed on the consumer's worker-thread
connection from ``mount()`` (a ``CaptureQueriesContext`` on the test thread
sees nothing — lifted from ``tests/benchmarks/test_model_backed_render_2532.py``).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.db import connection, models
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler

pytestmark = [pytest.mark.django_db(transaction=True)]

MOD = __name__
ROW_COUNT = 8


class Author2536(models.Model):
    name = models.CharField(max_length=64)
    email = models.CharField(max_length=64)

    class Meta:
        app_label = "demo_app"


class Post2536(models.Model):
    title = models.CharField(max_length=128)
    views = models.IntegerField(default=0)
    author = models.ForeignKey(Author2536, on_delete=models.CASCADE, related_name="posts2536")

    class Meta:
        app_label = "demo_app"


def _ensure_tables() -> None:
    existing = set(connection.introspection.table_names())
    missing = [m for m in (Author2536, Post2536) if m._meta.db_table not in existing]
    if not missing:
        return
    with connection.schema_editor() as editor:
        for model in missing:
            editor.create_model(model)


def _seed() -> None:
    if Post2536.objects.exists():
        return
    a = Author2536.objects.create(name="Ann", email="ann@x.org")
    for i in range(ROW_COUNT):
        Post2536.objects.create(title=f"Post {i}", views=i, author=a)


SQL_LOG: List[str] = []
LAST_VIEW: List[Any] = []


def _query_wrapper(execute, sql, params, many, context):  # type: ignore[no-untyped-def]
    SQL_LOG.append(sql[:120])
    return execute(sql, params, many, context)


def _install_query_log() -> None:
    if _query_wrapper not in connection.execute_wrappers:
        connection.execute_wrappers.append(_query_wrapper)


def _uninstall_query_log() -> bool:
    while _query_wrapper in connection.execute_wrappers:
        connection.execute_wrappers.remove(_query_wrapper)
    return _query_wrapper not in connection.execute_wrappers


def _make_view(name: str, relation: bool, select: bool = True) -> str:
    rel_cell = "<td>{{ row.author.email }}</td>" if relation else ""

    class _V(LiveView):
        template = (
            f'<div dj-view="{MOD}.{name}" dj-id="0"><p>{{{{ label }}}}</p><table><tbody>'
            "{% for row in rows %}<tr><td>{{ row.title }}</td><td>{{ row.views }}</td>"
            f"{rel_cell}</tr>{{% endfor %}}</tbody></table></div>"
        )

        def mount(self, request: Any, **kwargs: Any) -> None:
            LAST_VIEW[:] = [self]
            _install_query_log()
            qs = Post2536.objects.order_by("id")
            if select:
                qs = qs.select_related("author")
            self.rows = list(qs)
            self.label = "v0"

        @event_handler()
        def text_change(self, **kwargs: Any) -> None:
            self.label = f"v{int(self.label[1:]) + 1}"

        @event_handler()
        def bump_row(self, **kwargs: Any) -> None:
            # In-memory only — no save(). Django's engine renders this value.
            # A model instance is an identity leaf for change detection, so
            # the documented hatch marks the attr changed (#2664 / #2682).
            self.rows[3].views = 999_999
            self.set_changed_keys("rows")

    _V.__name__ = name
    _V.__qualname__ = name
    _V.__module__ = MOD
    setattr(sys.modules[MOD], name, _V)
    return name


REL_VIEW = _make_view("_RelView2536", relation=True)
PLAIN_VIEW = _make_view("_PlainView2536", relation=False)
NOSEL_VIEW = _make_view("_NoSelView2536", relation=True, select=False)

_TERMINAL = {"noop", "html_update", "error", "patch"}


async def _recv_until(comm: Any, wanted: str, *, ref: Any = None) -> Dict[str, Any]:
    last: Dict[str, Any] = {}
    for _ in range(8):
        last = await comm.receive_json_from(timeout=5)
        if (last.get("type") == wanted or last.get("type") in _TERMINAL) and (
            ref is None or last.get("ref") == ref
        ):
            return last
    return last


async def _drive(cls_name: str, events: List[str]) -> Dict[str, Any]:
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)
    out: Dict[str, Any] = {"queries": {}, "frames": {}}
    try:
        await comm.send_json_to({"type": "mount", "view": f"{MOD}.{cls_name}", "url": "/x/"})
        out["mount"] = await _recv_until(comm, "mount")
        for i, event in enumerate(events):
            ref = 100 + i
            SQL_LOG.clear()
            await comm.send_json_to({"type": "event", "event": event, "params": {}, "ref": ref})
            out["frames"][event] = await _recv_until(comm, "patch", ref=ref)
            out["queries"][event] = list(SQL_LOG)
    finally:
        try:
            await comm.disconnect()
        finally:
            removed = await sync_to_async(_uninstall_query_log)()
            assert removed
    return out


def _run(cls_name: str, events: List[str]) -> Dict[str, Any]:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drive(cls_name, events))
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _env(transactional_db: Any):
    pytest.importorskip("channels")
    _ensure_tables()
    _seed()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        yield


def _frame_text(frame: Dict[str, Any]) -> str:
    import json

    return json.dumps(frame)


class TestStaticListIsNotRequeried:
    def test_relation_path_list_costs_no_query_on_an_unrelated_event(self) -> None:
        out = _run(REL_VIEW, ["text_change", "text_change"])
        post_sql = [q for q in out["queries"]["text_change"] if Post2536._meta.db_table in q]
        assert post_sql == [], (
            "a static list[Model] was re-queried on an event that never touched it:\n"
            + "\n".join(post_sql)
        )
        assert "v2" in _frame_text(out["frames"]["text_change"])

    def test_unoptimized_list_is_fetched_once_then_never_again(self) -> None:
        # No select_related in mount(): the JIT may re-query ONCE to avoid the
        # N+1, grafting the caches onto the rows; the next event costs nothing.
        out = _run(NOSEL_VIEW, ["text_change", "text_change", "text_change"])
        counts = [
            len([q for q in out["queries"][e] if Post2536._meta.db_table in q])
            for e in ["text_change"]
        ]
        assert counts == [0], counts

    def test_plain_list_costs_no_query_on_an_unrelated_event(self) -> None:
        out = _run(PLAIN_VIEW, ["text_change"])
        post_sql = [q for q in out["queries"]["text_change"] if Post2536._meta.db_table in q]
        assert post_sql == []


class TestInMemoryRowMutationRenders:
    @pytest.mark.parametrize("cls_name", [REL_VIEW, PLAIN_VIEW, NOSEL_VIEW])
    def test_row_attribute_write_reaches_the_patch(self, cls_name: str) -> None:
        out = _run(cls_name, ["bump_row"])
        text = _frame_text(out["frames"]["bump_row"])
        assert "999999" in text, (
            "an in-memory row mutation was silently discarded by a re-query: " + text[:400]
        )
