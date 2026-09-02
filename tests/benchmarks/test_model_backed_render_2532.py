"""Model-backed render benchmark — the five-bucket boundary profile (#2532).

A 50-row × 6-column list of Django models rendered through the REAL WebSocket
mount path and event path (``WebsocketCommunicator`` → ``LiveViewConsumer`` →
``ViewRuntime.dispatch_mount`` / ``dispatch_event``), profiled per phase into
the five buckets ``model_backed_profile_2532`` defines: Rust render proper;
Python boundary crossings; ORM; state serialization on the event path; HTML
parse + VDOM diff tagged by the differ's ``fast_path`` flag. ADR-027 changes
the boundary bucket 2 measures and is scored against this table.

Six variants, one benchmark test each (``--benchmark-only`` runs them all;
the table prints from ``pytest_terminal_summary`` in ``conftest.py``):

==================== ========================================== ======================================
variant              row shape / variant column                 why it is in the table
==================== ========================================== ======================================
``list_control``     ``list(qs.select_related("author"))``,     the spec's control; JIT-eager
                     ``{{ row.author.name }}``
``list_property``    ``{{ row.word_count }}`` (``@property``)   the spec's "sidecar" case — it is not
``list_reverse``     ``{{ row.comments.count }}``               JIT prefetch + per-event re-query
``list_fk_nosel``    no ``select_related``                      JIT re-queries with ``select_related``
``presenter_control`` ``Page(rows)``, ``{% for row in           the Rust list-extraction cost
                     page.rows %}``, ``{{ row.author.name }}``
``presenter_reverse`` ``Page(rows)``, ``{{ row.comments.count }}`` the per-segment sidecar walk — the
                                                                informative contrast (acceptance)
==================== ========================================== ======================================

Three events per variant: ``text_change`` (a label outside the loop → the
fragment fast path), ``attr_change`` (a ``class`` on a ``<tr>`` → full parse +
diff), ``row_text_change`` (a persisted ``views += 1`` on row 7 → the
text-region fast path).

**Premise correction the spike proved (each traced to code).** The issue's
table says ``@property`` and reverse relations on a list row reach the
sidecar. They do not: ``ContextMixin.get_context_data`` JIT-serialises a
``list[Model]`` in Python by template-extracted paths (re-query with
``select_related`` / ``prefetch_related`` + codegen per row), and
``rust_bridge.py`` never admits a ``list`` to the sidecar — so all four
``list_*`` variants cross the Rust boundary ZERO times. The sidecar walk
carries traffic only for a container the JIT skips (a presenter object):
``presenter_reverse`` makes 302 direct Rust→Python crossings (+950 transitive
re-wraps inside them) and 50 ``COUNT(*)`` queries per full render — an N+1 on
every attribute-change event — and ``presenter_control`` still pays 52
crossings (+850) for the one-off ``Value`` extraction of the row list. The
assertions below pin exactly that contrast.

Assertions are on COUNTS and FLAGS only — never on a duration (v1.0.5-4
rule; #1534 keeps timing non-gating until runner-stable):

(a) the four ``list_*`` variants have 0 Rust-origin crossings in every phase
    and ``presenter_reverse`` has > 0 on every full render (the
    fixture-is-informative proof and the ADR-027 invariant);
(b) the differ's ``fast_path`` is True for ``text_change`` and
    ``row_text_change`` and False for ``attr_change`` in every variant, and
    agrees with the pre-#2532 inference (``diff_ms == 0`` ∧ all patches
    ``SetText``) so the flag is load-bearing (#1859) and the inference cannot
    drift silently;
(c) ``presenter_reverse`` issues more queries per full render than
    ``presenter_control`` (the N+1) — asserted as ``>``, not a number;
(d) the variant column actually rendered (row 7's expected cell is in the
    mount HTML) — a column that silently rendered empty would report 0
    crossings for the wrong reason.

Run::

    make benchmark-model
    .venv/bin/python -m pytest tests/benchmarks/test_model_backed_render_2532.py \\
        --benchmark-disable -q -p no:cacheprovider     # assertions only

Numbers only mean anything from a RELEASE build of the extension
(``maturin develop --release``; the cp312 ``.so`` is ~6 MB, a debug build is
~35 MB and inverts every timing).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional

import pytest
from asgiref.sync import sync_to_async
from django.db import connection, models
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from tests.benchmarks.model_backed_profile_2532 import (
    PROFILE_ROWS,
    Crossings,
    PhaseRow,
    install_crossing_counters,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.benchmark(group="model_backed_2532"),
]

MOD = __name__

ROW_COUNT = 50
#: Row whose cell is checked for rendering and whose ``views`` the
#: ``row_text_change`` event bumps. 7 → 3 comments, 12 words, author 2.
PROBE_ROW = 7

VARIANTS = (
    "list_control",
    "list_property",
    "list_reverse",
    "list_fk_nosel",
    "presenter_control",
    "presenter_reverse",
)
LIST_VARIANTS = tuple(v for v in VARIANTS if v.startswith("list_"))
EVENTS = ("text_change", "attr_change", "row_text_change")

#: Rounds measured per variant (plus one warm-up: the JIT / template caches
#: are process globals, so the first session pays their population).
ROUNDS = 3


# ---------------------------------------------------------------------------
# Throwaway models. ``app_label`` of an installed app with no migrations, so
# the test-DB setup's syncdb creates the tables (and ``_ensure_tables`` is the
# guard for a process where the module was imported after DB setup).
# ---------------------------------------------------------------------------


class Author(models.Model):
    name = models.CharField(max_length=64)
    email = models.CharField(max_length=64)

    class Meta:
        app_label = "demo_app"


class Post(models.Model):
    title = models.CharField(max_length=128)
    body = models.TextField()
    status = models.CharField(max_length=16)
    views = models.IntegerField(default=0)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="posts")

    class Meta:
        app_label = "demo_app"

    @property
    def word_count(self) -> int:
        return len(self.body.split())


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    text = models.CharField(max_length=64)

    class Meta:
        app_label = "demo_app"


def _ensure_tables() -> None:
    existing = set(connection.introspection.table_names())
    missing = [m for m in (Author, Post, Comment) if m._meta.db_table not in existing]
    if not missing:
        return
    with connection.schema_editor() as editor:
        for model in missing:
            editor.create_model(model)


def _seed() -> None:
    """5 authors / 50 posts / ``i % 4`` comments. Idempotent; re-run after the
    ``transaction=True`` flush between tests."""
    if Post.objects.exists():
        return
    authors = [Author.objects.create(name=f"Author {i}", email=f"a{i}@x.org") for i in range(5)]
    for i in range(ROW_COUNT):
        post = Post.objects.create(
            title=f"Post {i}",
            body=" ".join(["lorem"] * (5 + i % 7)),
            status="published" if i % 3 else "draft",
            views=1000 + i * 7,
            author=authors[i % 5],
        )
        for j in range(i % 4):
            Comment.objects.create(post=post, text=f"c{j}")


# ---------------------------------------------------------------------------
# Instrumentation shared by every variant
# ---------------------------------------------------------------------------

CROSSINGS = Crossings()
LAST_VIEW: List[Any] = []
QUERY_LOG: List[float] = []
SQL_LOG: List[str] = []
SYNC_SECS: List[float] = []
GCD_SECS: List[float] = []
PERSIST_SECS: List[float] = []


def _query_wrapper(execute, sql, params, many, context):  # type: ignore[no-untyped-def]
    t0 = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        QUERY_LOG.append(time.perf_counter() - t0)
        SQL_LOG.append(sql[:90])


def _install_query_log() -> None:
    """Hook THIS thread's connection. Called from ``mount()``, which runs in
    the consumer's ``sync_to_async`` worker thread — the thread whose
    connection every ORM statement of the session goes through. A
    ``CaptureQueriesContext`` on the test thread sees nothing."""
    if _query_wrapper not in connection.execute_wrappers:
        connection.execute_wrappers.append(_query_wrapper)


def _reset_phase_counters() -> None:
    CROSSINGS.reset()
    QUERY_LOG.clear()
    SQL_LOG.clear()
    SYNC_SECS.clear()
    GCD_SECS.clear()
    PERSIST_SECS.clear()


class Page:
    """Plain presenter object: not a Model / QuerySet / list, so the JIT skips
    it, ``rust_bridge.py`` admits it raw to the sidecar, and the Rust alias
    walk resolves ``page.rows.<i>.<col>`` per segment."""

    def __init__(self, rows: List[Post]) -> None:
        self.rows = rows


_VARIANT_COLUMN = {
    "list_control": "{{ row.author.name }}",
    "list_property": "{{ row.word_count }}",
    "list_reverse": "{{ row.comments.count }}",
    "list_fk_nosel": "{{ row.author.name }}",
    "presenter_control": "{{ row.author.name }}",
    "presenter_reverse": "{{ row.comments.count }}",
}


def _expected_cell(variant: str, post: Post) -> str:
    """Row 7's variant-column cell as it must appear in the mount HTML."""
    if variant.endswith("_reverse"):
        return f">{post.comments.count()}</td>"
    if variant == "list_property":
        return f">{post.word_count}</td>"
    return f">{post.author.name}</td>"


def _template(variant: str, cls_name: str) -> str:
    loop_source = "page.rows" if variant.startswith("presenter") else "rows"
    return (
        f'<div dj-view="{MOD}.{cls_name}" dj-id="0">'
        "<p>{{ label }}</p>"
        "<table><tbody>"
        f"{{% for row in {loop_source} %}}"
        '<tr class="{% if row.id == highlight_id %}hl{% endif %}">'
        "<td>{{ row.title }}</td><td>{{ row.views }}</td><td>{{ row.status }}</td>"
        f"<td>{_VARIANT_COLUMN[variant]}</td>"
        "<td>{{ row.author.email }}</td><td>{{ row.body|truncatechars:12 }}</td>"
        "</tr>{% endfor %}"
        "</tbody></table></div>"
    )


def _make_view(variant: str) -> str:
    """Build + register (at module level, so the consumer can import it by
    dotted path) one LiveView subclass per variant. Lifted from
    ``test_request_path.py`` (module-level registration +
    ``LIVEVIEW_ALLOWED_MODULES=[__name__]``)."""
    cls_name = f"_V_{variant}"
    presenter = variant.startswith("presenter")
    select = variant != "list_fk_nosel"

    class _V(LiveView):
        template = _template(variant, cls_name)

        def mount(self, request: Any, **kwargs: Any) -> None:
            LAST_VIEW[:] = [self]
            _install_query_log()
            qs = Post.objects.all().order_by("id")
            if select:
                qs = qs.select_related("author")
            t0 = time.perf_counter()
            rows = list(qs)
            self._orm_list_s = time.perf_counter() - t0
            if presenter:
                self.page = Page(rows)
            else:
                self.rows = rows
            self.label = "v0"
            self.highlight_id = 0
            self._highlight_index = -1

        def _rows(self) -> List[Post]:
            return self.page.rows if presenter else self.rows

        def _sync_state_to_rust(self, *args: Any, **kwargs: Any) -> None:
            t0 = time.perf_counter()
            try:
                return super()._sync_state_to_rust(*args, **kwargs)
            finally:
                SYNC_SECS.append(time.perf_counter() - t0)

        def get_context_data(self, **kwargs: Any) -> Dict[str, Any]:
            t0 = time.perf_counter()
            try:
                return super().get_context_data(**kwargs)
            finally:
                GCD_SECS.append(time.perf_counter() - t0)

        @event_handler()
        def text_change(self, **kwargs: Any) -> None:
            self.label = f"v{int(self.label[1:]) + 1}"

        @event_handler()
        def attr_change(self, **kwargs: Any) -> None:
            # By row POSITION, not ``pk % 50``: the transactional flush between
            # tests re-seeds with fresh autoincrement ids, and a highlight that
            # matches no row renders byte-identical HTML (an empty patch).
            self._highlight_index = (self._highlight_index + 1) % ROW_COUNT
            self.highlight_id = self._rows()[self._highlight_index].id

        @event_handler()
        def row_text_change(self, **kwargs: Any) -> None:
            row = self._rows()[PROBE_ROW]
            row.views += 1
            # The list path's JIT re-queries a static list[Model] on every
            # event when the template has a relation path, so an in-memory
            # mutation alone would be discarded (spike run 7): persist it.
            row.save(update_fields=["views"])
            # New container identity so change detection sees the change.
            if presenter:
                self.page = Page([*self.page.rows])
            else:
                self.rows = [*self.rows]

    _V.__name__ = cls_name
    _V.__qualname__ = cls_name
    _V.__module__ = MOD
    setattr(sys.modules[MOD], cls_name, _V)
    return cls_name


VIEW_CLASS = {variant: _make_view(variant) for variant in VARIANTS}


# ---------------------------------------------------------------------------
# Driver (real WebSocket path)
# ---------------------------------------------------------------------------

_TERMINAL_FRAMES = ("noop", "html_update", "error")


async def _recv_until(
    comm: Any, wanted: str, *, ref: Optional[int] = None, tries: int = 8, timeout: float = 5.0
) -> Dict[str, Any]:
    """Drain frames until ``wanted`` (or a terminal ``noop`` / ``html_update`` /
    ``error``) with a matching ``ref``. Lifted from
    ``test_global_isolation_1883.py`` — the ``ref`` filter is what keeps a
    stray broadcast from being read as the answer."""
    last: Dict[str, Any] = {}
    for _ in range(tries):
        last = await comm.receive_json_from(timeout=timeout)
        ftype = last.get("type")
        if (ftype == wanted or ftype in _TERMINAL_FRAMES) and (
            ref is None or last.get("ref") == ref
        ):
            if ftype == "error":
                raise AssertionError(f"consumer returned an error frame: {last}")
            return last
    return last


def _patch_kinds(frame: Dict[str, Any]) -> Dict[str, int]:
    patches = frame.get("patches")
    if patches is None:
        return {}
    if isinstance(patches, str):
        patches = json.loads(patches)
    kinds: Dict[str, int] = {}
    for patch in patches:
        kind = patch.get("type") if isinstance(patch, dict) else str(patch)[:20]
        kinds[kind] = kinds.get(kind, 0) + 1
    return kinds


def _phase_row(
    variant: str, phase: str, frame: Dict[str, Any], total_s: float, view: Any
) -> PhaseRow:
    timing = view._rust_view.get_render_timing() or {}
    is_mount = phase == "mount"
    return PhaseRow(
        variant=variant,
        phase=phase,
        frame_type=str(frame.get("type")),
        total_ms=total_s * 1000.0,
        render_ms=timing.get("render_ms", 0.0),
        xings=CROSSINGS.rust_calls,
        proxy_xings=CROSSINGS.proxy_calls,
        xing_ms=(CROSSINGS.rust_secs + CROSSINGS.proxy_secs) * 1000.0,
        py_xings=CROSSINGS.python_calls,
        xing_kinds=dict(CROSSINGS.kinds),
        queries=len(QUERY_LOG),
        sql_ms=sum(QUERY_LOG) * 1000.0,
        list_ms=(getattr(view, "_orm_list_s", 0.0) * 1000.0) if is_mount else 0.0,
        sync_ms=sum(SYNC_SECS) * 1000.0,
        jit_ms=sum(GCD_SECS) * 1000.0,
        persist_ms=sum(PERSIST_SECS) * 1000.0,
        parse_ms=timing.get("parse_ms", 0.0),
        diff_ms=timing.get("diff_ms", 0.0),
        ser_ms=timing.get("serialize_ms", 0.0),
        fast_path=None if is_mount else timing.get("fast_path"),
        patches={} if is_mount else _patch_kinds(frame),
    )


async def _drive(variant: str) -> List[PhaseRow]:
    """One session: connect → mount → the three events. Returns one
    :class:`PhaseRow` per phase (mount first)."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    cls_name = VIEW_CLASS[variant]
    rows: List[PhaseRow] = []
    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from(timeout=5)

    _reset_phase_counters()
    t0 = time.perf_counter()
    await comm.send_json_to({"type": "mount", "view": f"{MOD}.{cls_name}", "url": "/bench/"})
    mount = await _recv_until(comm, "mount")
    total_s = time.perf_counter() - t0
    view = LAST_VIEW[0]
    mount_row = _phase_row(variant, "mount", mount, total_s, view)
    rows.append(mount_row)

    # (d) the variant column rendered. Computed on the worker thread — the
    # test thread's connection is a different one.
    probe = await sync_to_async(lambda: view._rows()[PROBE_ROW])()
    expected = await sync_to_async(_expected_cell)(variant, probe)
    html = mount.get("html", "")
    assert expected in html, (
        f"{variant}: the variant column did not render — expected {expected!r} for row "
        f"{PROBE_ROW} in the mount HTML ({len(html)} chars)"
    )

    for i, event in enumerate(EVENTS):
        ref = 100 + i
        _reset_phase_counters()
        t0 = time.perf_counter()
        await comm.send_json_to({"type": "event", "event": event, "params": {}, "ref": ref})
        frame = await _recv_until(comm, "patch", ref=ref)
        total_s = time.perf_counter() - t0
        rows.append(_phase_row(variant, event, frame, total_s, view))

    await comm.disconnect()
    return rows


def _run_session(variant: str) -> List[PhaseRow]:
    """Fresh event loop per session (as ``test_request_path.py`` does) so no
    state leaks between rounds."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_drive(variant))
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bench_env(monkeypatch: pytest.MonkeyPatch):
    """Tables + seed, the crossing counters, the ``in_rust_render`` flag around
    the differ call, the ``_persist_state_after_event`` timer, and the
    consumer's module allowlist."""
    pytest.importorskip("channels")
    _ensure_tables()
    _seed()

    from djust.renderers.html import HtmlRenderer
    from djust.runtime import ViewRuntime

    restore = install_crossing_counters(CROSSINGS)

    orig_render = HtmlRenderer.render_with_diff

    def render_with_diff(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Everything Python that runs between here and the return is a
        # callback Rust made into Python: that is the definition of bucket 2.
        CROSSINGS.in_rust_render = True
        try:
            return orig_render(self, *args, **kwargs)
        finally:
            CROSSINGS.in_rust_render = False

    monkeypatch.setattr(HtmlRenderer, "render_with_diff", render_with_diff)

    orig_persist = ViewRuntime._persist_state_after_event

    async def persist(self: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        try:
            return await orig_persist(self, *args, **kwargs)
        finally:
            PERSIST_SECS.append(time.perf_counter() - t0)

    monkeypatch.setattr(ViewRuntime, "_persist_state_after_event", persist)

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD], DEBUG=False):
        try:
            yield
        finally:
            restore()


# ---------------------------------------------------------------------------
# Assertions (counts + flags only)
# ---------------------------------------------------------------------------


def _by_phase(rows: List[PhaseRow]) -> Dict[str, PhaseRow]:
    return {r.phase: r for r in rows}


def _inferred_fast_path(row: PhaseRow) -> bool:
    """The pre-#2532 inference: both fast paths set ``diff_ms = 0.0`` and emit
    only ``SetText`` patches; a full parse never does."""
    return row.diff_ms == 0.0 and bool(row.patches) and set(row.patches) == {"SetText"}


def _assert_fast_path_flags(variant: str, rows: List[PhaseRow]) -> None:
    """(b): flag per event + agreement with the inference."""
    phases = _by_phase(rows)
    expectations = {"text_change": True, "row_text_change": True, "attr_change": False}
    for event, expected in expectations.items():
        row = phases[event]
        assert row.frame_type == "patch", (
            f"{variant}/{event}: expected a patch frame, got {row.frame_type} (patches={row.patches})"
        )
        assert row.fast is expected, (
            f"{variant}/{event}: differ reported fast_path={row.fast_path} "
            f"({'taken' if row.fast else 'not taken'}); expected {'taken' if expected else 'not taken'}. "
            f"patches={row.patches} diff_ms={row.diff_ms:.3f}"
        )
        assert _inferred_fast_path(row) is expected, (
            f"{variant}/{event}: the flag and the diff_ms/SetText inference disagree — "
            f"fast_path={row.fast_path} diff_ms={row.diff_ms:.3f} patches={row.patches}"
        )
    assert "SetAttr" in phases["attr_change"].patches, (
        f"{variant}/attr_change: expected a SetAttr patch, got {phases['attr_change'].patches}"
    )


def _assert_crossings(variant: str, rows: List[PhaseRow]) -> None:
    """(a): list rows never reach the sidecar; the presenter reverse path does."""
    phases = _by_phase(rows)
    if variant in LIST_VARIANTS:
        for phase, row in phases.items():
            assert row.xings == 0, (
                f"{variant}/{phase}: {row.xings} Rust-origin boundary crossings ({row.xing_kinds}); "
                f"a list[Model] is JIT-serialised in Python and never reaches the sidecar"
            )
    elif variant == "presenter_reverse":
        for phase in ("mount", "attr_change"):
            row = phases[phase]
            assert row.xings > 0, (
                f"{variant}/{phase}: 0 Rust-origin crossings — the presenter walk is the one "
                f"path that MUST cross the boundary (py-side={row.py_xings})"
            )
        # The text fast path re-renders nothing, so it crosses (almost) nothing.
        assert phases["text_change"].xings < phases["attr_change"].xings


def _measured_sessions(benchmark: Any, variant: str) -> List[List[PhaseRow]]:
    """Run the sessions under pytest-benchmark and return EVERY session's rows
    (warm-up included: flags and counts are deterministic per session, only
    the timings warm). Timing rows appended to the table exclude the warm-up.

    Under ``--benchmark-disable`` pedantic runs the function exactly once, so
    the assertions still see one full session — never zero."""
    sessions: List[List[PhaseRow]] = []

    def _one() -> List[PhaseRow]:
        rows = _run_session(variant)
        sessions.append(rows)
        return rows

    benchmark.pedantic(_one, rounds=ROUNDS, warmup_rounds=1, iterations=1)
    assert sessions, "pytest-benchmark did not run the session even once"
    measured = sessions[1:] if len(sessions) > 1 else sessions
    for rows in measured:
        PROFILE_ROWS.extend(rows)
    return sessions


@pytest.mark.parametrize("variant", VARIANTS)
def test_model_backed_render_profile(benchmark: Any, variant: str) -> None:
    """Mount + three events over the real WS path; asserts (a), (b), (d) for
    every variant and (c) for ``presenter_reverse``."""
    for rows in _measured_sessions(benchmark, variant):
        assert rows[0].frame_type == "mount"
        assert len(rows) == 1 + len(EVENTS)
        _assert_crossings(variant, rows)
        _assert_fast_path_flags(variant, rows)
        if variant == "presenter_reverse":
            _assert_reverse_walk_is_an_n_plus_one(rows)


def _assert_reverse_walk_is_an_n_plus_one(reverse_rows: List[PhaseRow]) -> None:
    """(c): the per-segment sidecar walk resolves ``row.comments.count`` with
    one ``COUNT(*)`` per row on every full render. ``presenter_control`` is
    driven inline (untimed) so the comparison is self-contained."""
    control_full = _by_phase(_run_session("presenter_control"))["attr_change"].queries
    reverse_full = _by_phase(reverse_rows)["attr_change"].queries
    assert reverse_full > control_full, (
        f"presenter_reverse issued {reverse_full} queries on a full render vs "
        f"presenter_control's {control_full}; the reverse-relation walk must re-query per row"
    )
    assert reverse_full >= ROW_COUNT, (
        f"expected at least one COUNT(*) per row ({ROW_COUNT}), got {reverse_full}"
    )
