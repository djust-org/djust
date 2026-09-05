"""Model-backed render benchmark — the five-bucket boundary profile (#2532).

A 50-row × 6-column list of Django models rendered through the REAL WebSocket
mount path and event path (``WebsocketCommunicator`` → ``LiveViewConsumer`` →
``ViewRuntime.dispatch_mount`` / ``dispatch_event``), profiled per phase into
the five buckets ``model_backed_profile_2532`` defines: Rust render proper;
Python boundary crossings; ORM; state serialization on the event path; HTML
parse + VDOM diff tagged by the differ's ``fast_path`` flag. ADR-027 changes
the boundary bucket 2 measures and is scored against this table.

Seven variants, one benchmark test each (``--benchmark-only`` runs them all;
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
``snapshot``         ``list_control`` +                         the only variant whose bucket-4
                     ``enable_state_snapshot = True``           ``persist`` column is non-zero
==================== ========================================== ======================================

Three events per variant: ``text_change`` (a label outside the loop → the
fragment fast path), ``attr_change`` (a ``class`` on a ``<tr>`` → full parse +
diff), ``row_text_change`` (a persisted ``views += 1`` on row 7 → the
text-region fast path).

**Why ``snapshot`` is in and ``queryset_control`` is out.** The plan's
``snapshot`` variant was dropped from the first cut, which left bucket 4's
``persist`` column dead: ``ViewRuntime._persist_state_after_event`` runs
only for a view with ``enable_state_snapshot = True`` (the #1552 opt-in), so
no variant could ever read anything but 0 there. ``snapshot`` restores it —
the ``list_control`` shape with the opt-in — so the column measures the
per-event session save that normalises the 50 model rows to plain data
(``normalize_django_value(..., state_roundtrip=True)``) and writes them
through the session backend. That is the state-persistence path ADR-027's
transient handle must be proven to drop through, so it needs a baseline. The
mount-side signed-snapshot emission (``_capture_snapshot_state(strict=True)``)
runs on the same variant and is inside its mount ``total``. The plan's
``queryset_control`` (a bare ``QuerySet`` in state) stays dropped: it exercises
``_rust.serialize_queryset``, a different boundary from the sidecar walk this
table profiles, and is out of #2532's scope.

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

(a) the five list-shaped variants (the four ``list_*`` plus ``snapshot``)
    have 0 Rust-origin crossings in every phase and ``presenter_reverse``
    has > 0 on every full render (the fixture-is-informative proof and the
    ADR-027 invariant);
(b) the differ's ``fast_path`` is True for ``text_change`` and
    ``row_text_change`` and False for ``attr_change`` in every variant, and
    agrees with the pre-#2532 inference (``diff_ms == 0`` ∧ all patches
    ``SetText``) so the flag is load-bearing (#1859) and the inference cannot
    drift silently;
(c) ``presenter_reverse`` issues more queries per full render than
    ``presenter_control`` (the N+1) — asserted as ``>``, not a number;
(d) the variant column actually rendered (row 7's expected cell is in the
    mount HTML) — a column that silently rendered empty would report 0
    crossings for the wrong reason;
(e) the persist column is PRESENT where it is claimed: ``snapshot`` records
    exactly one ``_persist_state_after_event`` call per event, its wall time
    is non-zero, and the 50 normalised rows are readable back from the
    session store (with row 7's bumped ``views``); every other variant
    records 0 calls — by design, not by accident (#1859);
(f) the ORM ``execute_wrappers`` hook the session installs on the worker
    thread's connection is gone from that connection after teardown.

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
#: ``row_text_change`` event bumps. 7 → 3 comments (``7 % 4``), 5 words
#: (``5 + 7 % 7``), author 2 (``7 % 5``).
PROBE_ROW = 7

VARIANTS = (
    "list_control",
    "list_property",
    "list_reverse",
    "list_fk_nosel",
    "presenter_control",
    "presenter_reverse",
    "snapshot",
)
LIST_VARIANTS = tuple(v for v in VARIANTS if v.startswith("list_"))
#: Every variant whose rows are a ``list[Model]`` in state — the JIT owns
#: those, so none of them may cross the Rust boundary.
ZERO_CROSSING_VARIANTS = LIST_VARIANTS + ("snapshot",)
#: The one variant that opts into state persistence (``enable_state_snapshot``).
SNAPSHOT_VARIANT = "snapshot"
EVENTS = ("text_change", "attr_change", "row_text_change")
#: Where ``_persist_state_after_event`` writes: ``liveview_{mount path}``.
MOUNT_URL = "/bench/"
SESSION_STATE_KEY = f"liveview_{MOUNT_URL}"

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


def _uninstall_query_log() -> bool:
    """Remove the hook from THIS thread's connection; ``True`` when it is gone.

    Must run on the SAME worker thread ``mount()`` installed it on —
    ``_drive`` does so via ``sync_to_async`` in its ``finally``. asgiref's
    thread-sensitive executor is process-global, so a hook left behind
    would time every ORM statement of every later test in the process."""
    while _query_wrapper in connection.execute_wrappers:
        connection.execute_wrappers.remove(_query_wrapper)
    return _query_wrapper not in connection.execute_wrappers


def _query_hook_installed_on_worker() -> bool:
    """Whether the hook is on the ``sync_to_async`` worker thread's connection
    right now (drives a fresh loop so it lands on that thread)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            sync_to_async(lambda: _query_wrapper in connection.execute_wrappers)()
        )
    finally:
        loop.close()


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
    "snapshot": "{{ row.author.name }}",
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
        # The #1552 opt-in: only ``snapshot`` persists state after each event
        # (and emits the signed snapshot on mount). Every other variant's
        # ``persist`` column is 0 by design — assertion (e) says so.
        enable_state_snapshot = variant == SNAPSHOT_VARIANT

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
        # #2545: the proxy re-wraps run INSIDE the timed direct crossings, so
        # `proxy_secs` is already part of `rust_secs`; adding it double-counted
        # bucket 2 (4.87 ms summed vs a 4.61 ms render on presenter_control).
        # The proxy COUNT stays its own column; only its time is not re-added.
        xing_ms=CROSSINGS.rust_secs * 1000.0,
        py_xings=CROSSINGS.python_calls,
        xing_kinds=dict(CROSSINGS.kinds),
        queries=len(QUERY_LOG),
        sql_ms=sum(QUERY_LOG) * 1000.0,
        list_ms=(getattr(view, "_orm_list_s", 0.0) * 1000.0) if is_mount else 0.0,
        sync_ms=sum(SYNC_SECS) * 1000.0,
        jit_ms=sum(GCD_SECS) * 1000.0,
        persist_ms=sum(PERSIST_SECS) * 1000.0,
        persist_calls=len(PERSIST_SECS),
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

    try:
        _reset_phase_counters()
        t0 = time.perf_counter()
        await comm.send_json_to({"type": "mount", "view": f"{MOD}.{cls_name}", "url": MOUNT_URL})
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
    finally:
        try:
            await comm.disconnect()
        finally:
            # (f) Same thread ``mount()`` installed the hook on: the consumer's
            # ORM work and this call both go through asgiref's thread-sensitive
            # executor. Left in place it would outlive the session (and the test).
            removed = await sync_to_async(_uninstall_query_log)()
            assert removed, "the query hook is still on the worker thread's connection"
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
def _bench_env(monkeypatch: pytest.MonkeyPatch, transactional_db: Any):
    """Tables + seed, the crossing counters, the ``in_rust_render`` flag around
    the differ call, the ``_persist_state_after_event`` timer, and the
    consumer's module allowlist.

    ``transactional_db`` is requested explicitly (not only via the module's
    ``django_db(transaction=True)`` mark) so the fixture cannot touch the DB
    before pytest-django has set it up, whatever order the marks resolve in."""
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
        n_gcd = len(GCD_SECS)
        t0 = time.perf_counter()
        try:
            return await orig_persist(self, *args, **kwargs)
        finally:
            PERSIST_SECS.append(time.perf_counter() - t0)
            # The save's own ``get_context_data`` call is persist time, not
            # JIT time: drop it from ``jit_ms`` so ``state_ms`` (sync − jit)
            # is not under-counted on the snapshot variant.
            del GCD_SECS[n_gcd:]

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
    if variant in ZERO_CROSSING_VARIANTS:
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


def _persisted_state(view: Any) -> Optional[Dict[str, Any]]:
    """What ``_persist_state_after_event`` left in the session store for this
    view's mount path, read back fresh from the backend (``None`` when the
    request's session was never saved)."""
    session = getattr(getattr(view, "_djust_mount_request", None), "session", None)
    key = getattr(session, "session_key", None)
    if not key:
        return None
    fresh = type(session)(session_key=key)
    return fresh.get(SESSION_STATE_KEY)


def _assert_persist_presence(variant: str, rows: List[PhaseRow], view: Any) -> None:
    """(e): the persist column is non-zero exactly where it is claimed."""
    phases = _by_phase(rows)
    assert phases["mount"].persist_calls == 0, "persist runs on the event path only"
    if variant != SNAPSHOT_VARIANT:
        for event in EVENTS:
            assert phases[event].persist_calls == 0, (
                f"{variant}/{event}: _persist_state_after_event ran "
                f"{phases[event].persist_calls}× on a view that did not opt into "
                f"enable_state_snapshot — the persist column is 0 by design here"
            )
        return
    for event in EVENTS:
        row = phases[event]
        assert row.persist_calls == 1, (
            f"{variant}/{event}: expected exactly one _persist_state_after_event "
            f"call, got {row.persist_calls} — the snapshot variant must opt in"
        )
        # A presence check against literal zero, not a threshold: the pin in
        # test_model_backed_table_2532.py exempts comparisons with 0.
        assert row.persist_ms > 0.0, f"{variant}/{event}: persist ran but recorded no time"
    # The save actually wrote through: 50 normalised rows, row 7 bumped.
    saved = _persisted_state(view)
    assert saved is not None, f"{variant}: nothing under {SESSION_STATE_KEY!r} in the session"
    saved_rows = saved.get("rows")
    assert isinstance(saved_rows, list) and len(saved_rows) == ROW_COUNT, (
        f"{variant}: the session holds {type(saved_rows).__name__} "
        f"{len(saved_rows) if isinstance(saved_rows, list) else ''} under 'rows', "
        f"expected {ROW_COUNT} normalised rows"
    )
    live_views = view._rows()[PROBE_ROW].views
    assert saved_rows[PROBE_ROW]["views"] == live_views, (
        f"{variant}: row {PROBE_ROW} persisted views={saved_rows[PROBE_ROW]['views']} "
        f"but the view holds {live_views} after row_text_change"
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_model_backed_render_profile(benchmark: Any, variant: str) -> None:
    """Mount + three events over the real WS path; asserts (a), (b), (d), (e)
    for every variant and (c) for ``presenter_reverse``."""
    for rows in _measured_sessions(benchmark, variant):
        assert rows[0].frame_type == "mount"
        assert len(rows) == 1 + len(EVENTS)
        _assert_crossings(variant, rows)
        _assert_fast_path_flags(variant, rows)
        if variant == "presenter_reverse":
            _assert_reverse_walk_is_an_n_plus_one(rows)
    # ``LAST_VIEW`` is the final session's view; (e) reads its session back.
    _assert_persist_presence(variant, rows, LAST_VIEW[0])


def test_query_hook_is_removed_from_the_worker_connection_on_teardown() -> None:
    """(f): a session leaves no ``execute_wrappers`` hook behind. The hook
    lives on the ``sync_to_async`` worker thread's connection — the same
    thread a fresh loop's ``sync_to_async`` lands on, which is how this test
    can observe it from outside a session (and why a leak would time every
    ORM statement of every later test in the process)."""
    _run_session("list_control")
    assert not _query_hook_installed_on_worker(), (
        "_query_wrapper is still in the worker thread's connection.execute_wrappers "
        "after the session ended"
    )


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
