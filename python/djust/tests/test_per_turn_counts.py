"""What one turn costs, as counted work (#3996, #3997, #3999).

No assertion here reads a clock. A turn is run over a real
``WebsocketCommunicator`` with spies on the functions the three issues name
(``_per_turn_harness.counting``), and the tests assert how many times a class
was compiled, a thread was hopped to, a container was walked, and how many keys
and bytes were handed to ``update_state``.

Three scenarios, the ones the issues measured:

* a bare counter (explicit exposure, nothing persisted),
* the same counter with the standard context processors and the theme,
* a 1,000-row list with one cell changed in place.

Every shortcut has a gate-off: the same turn with that one shortcut turned off
in :mod:`djust._per_turn`, asserting that the work it saves comes back. A
gate-off that changed no count would mean the shortcut is not reached by the
scenario, so each asserts a strict difference. The expected figures are derived
from the view (its rows, its processors), not restated.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from django.test import RequestFactory

from djust import LiveView, _per_turn, event_handler
from djust.decorators import state
from djust.tests import _per_turn_harness as harness

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
ROWS = 1000

PROCESSORS = [
    "django.template.context_processors.debug",
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
    "django.contrib.messages.context_processors.messages",
    "djust.theming.context_processors.theme_context",
]


def make_rows(n: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": i,
            "sku": f"SKU-{i:05d}",
            "name": f"Catalogue item number {i}",
            "price": round(i * 3.75, 2),
            "qty": i % 7,
            "active": i % 3 != 0,
            "tags": ["alpha", "beta", f"t{i % 5}"],
        }
        for i in range(n)
    ]


class BareCounter(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><p>Count: {{ count }}</p><button dj-click="inc">+</button></div>'
    count = state(0)

    def _get_context_processors(self):
        return []

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass


class ServerCounter(BareCounter):
    count = state(0, persist="server")


class ClientCounter(BareCounter):
    count = state(0, persist="client", client=True)


class ProcessorCounter(BareCounter):
    template = (
        "<div dj-root><p>Count: {{ count }}</p><p>{{ theme_mode }} {{ user }}</p>"
        '<button dj-click="inc">+</button></div>'
    )
    count = state(0)

    def _get_context_processors(self):
        return PROCESSORS


class Table(LiveView):
    exposure_policy = "explicit"
    rows_wanted = ROWS
    template = (
        '<div dj-root><p>{{ title }}</p><table>{% for row in items %}<tr id="row-{{ row.id }}">'
        "<td>{{ row.sku }}</td><td>{{ row.name }}</td><td>{{ row.price }}</td>"
        "<td>{{ row.qty }}</td></tr>{% endfor %}</table></div>"
    )
    title = state("")
    items = state(default_factory=list)

    def _get_context_processors(self):
        return []

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.items = make_rows(self.rows_wanted)

    @event_handler()
    def cell(self, **kwargs):
        self.items[self.rows_wanted // 2]["qty"] += 1

    @event_handler()
    def retitle(self, **kwargs):
        self.title += "!"


class SmallTable(Table):
    rows_wanted = ROWS // 4
    title = state("")
    items = state(default_factory=list)


class IfTable(Table):
    """``Table`` with a ``{% if %}`` in the row, as the benchmark's table has:
    the content-hash loop cache refuses that body, the item store takes it."""

    exposure_policy = "explicit"
    template = (
        '<div dj-root><p>{{ title }}</p><table>{% for row in items %}<tr id="row-{{ row.id }}">'
        "<td>{{ row.sku }}</td><td>{{ row.qty }}</td>"
        "<td>{% if row.active %}on{% else %}off{% endif %}</td></tr>{% endfor %}</table></div>"
    )


async def turn(monkeypatch, view_class, event_name, *off) -> Dict[str, Any]:
    """The counts of ONE *event_name* turn on a warmed-up *view_class*."""
    with monkeypatch.context() as spies:
        counts = harness.counting(spies)
        key = await harness.new_session_key_async()
        # Counted with the save before the reply (the work a turn does when it
        # saves; the default interval, #4164, moves it to the flush), unless
        # the test states an interval.
        from django.conf import settings

        pinned = {"DJUST_STATE_SAVE_INTERVAL": getattr(settings, "DJUST_STATE_SAVE_INTERVAL", 0)}
        with harness.shortcuts(monkeypatch, *off), harness.ws_settings(MODULE, **pinned):
            for name in off:
                assert not _per_turn.on(name), f"{name} was not turned off"
            comm, _mount = await harness.mount_view(view_class, key)
            try:
                await harness.event(comm, event_name)  # warm-up: first event after mount
                counts.reset()
                reply = await harness.event(comm, event_name)
                held = harness.fingerprint_bytes(counts.view)
            finally:
                await comm.disconnect()
    out = counts.snapshot()
    out["reply"] = reply["type"]
    out["fingerprints.retained_bytes"] = held
    return out


def request_scoped_data_keys() -> set:
    """The processor keys ``PROCESSORS`` supply as fingerprinted plain data."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.utils.module_loading import import_string

    from djust.mixins.rust_bridge import _NO_FINGERPRINT, _request_scoped_fingerprint

    request = RequestFactory().get(harness.PATH)
    request.user, request.session = AnonymousUser(), SessionStore()
    keys = set()
    for path in PROCESSORS:
        for key, value in (import_string(path)(request) or {}).items():
            keeps_out, fingerprint, _ = _request_scoped_fingerprint(key, value)
            if not keeps_out and fingerprint is not _NO_FINGERPRINT:
                keys.add(key)
    return keys


# --- #3997: the per-turn cost of explicit exposure -----------------------------------


async def test_a_counter_turn_compiles_no_contract_and_builds_no_codec(monkeypatch):
    now = await turn(monkeypatch, BareCounter, "inc")
    assert now["reply"] == "patch"
    # Nothing is compiled on a turn: the class's contract and provider
    # manifest are reused after being re-checked against the class.
    assert now.get("contract.built.calls", 0) == 0
    assert now["contract.from_view_class.calls"] >= 1, "the contract is still consulted"
    # A view with no client-persisted field mints no token: no codec is built.
    assert now.get("snapshot_codec.calls", 0) == 0
    # The save still runs, in its one hop, and still checks the binding.
    assert now["explicit_save.hops.calls"] == 1
    assert now["save_server_state.calls"] == 1
    assert now["request_binding.calls"] >= 1
    # One session load per turn: the authorization's. The save reuses it.
    assert now["session.load.calls"] == 1
    assert now["sent_keys"] == [["count"]]


async def test_contract_cache_gate_off(monkeypatch):
    now = await turn(monkeypatch, ServerCounter, "inc")
    off = await turn(monkeypatch, ServerCounter, "inc", "contract_cache")
    assert now.get("contract.built.calls", 0) == 0
    # Off, every consult compiles: the adapter, the codec check and
    # ``project_view``'s own re-check of the declarations.
    assert off["contract.built.calls"] == off["contract.from_view_class.calls"] >= 3
    assert now["server_state.save.calls"] == off["server_state.save.calls"] == 1


@pytest.mark.parametrize(
    "view_class", [BareCounter, ServerCounter, ClientCounter], ids=["none", "server", "client"]
)
async def test_snapshot_single_hop_gate_off(monkeypatch, view_class):
    """The event snapshot: two hops became none (no client field), three became one."""
    now = await turn(monkeypatch, view_class, "inc")
    off = await turn(monkeypatch, view_class, "inc", "snapshot_single_hop")
    assert off["runtime.sync_to_async.hops"] - now["runtime.sync_to_async.hops"] == 2
    # The token is still minted exactly when a client field is declared.
    minted = 1 if view_class is ClientCounter else 0
    assert now.get("client_snapshot.capture.calls", 0) == minted
    assert off.get("client_snapshot.capture.calls", 0) == minted
    # The save's own binding check is untouched.
    assert now["explicit_save.hops.calls"] == off["explicit_save.hops.calls"] == 1


async def test_a_turn_that_changes_nothing_still_commits(monkeypatch):
    """Commit-before-success is unchanged: a no-op turn still runs its save hop."""
    now = await turn(monkeypatch, ServerCounter, "nothing")
    assert now["reply"] == "noop"
    assert now["explicit_save.hops.calls"] == 1
    assert now["server_state.save.calls"] == 1


# --- #3996: context-processor keys --------------------------------------------------


async def test_unchanged_processor_values_are_not_sent_again(monkeypatch):
    data_keys = request_scoped_data_keys()
    assert {"theme_mode", "theme_preset", "DEFAULT_MESSAGE_LEVELS"} <= data_keys
    now = await turn(monkeypatch, ProcessorCounter, "inc")
    off = await turn(monkeypatch, ProcessorCounter, "inc", "request_scoped_changes")
    assert now["sent_keys"] == [["count"]]
    assert set(off["sent_keys"][0]) == {"count"} | data_keys
    assert off["update_state.keys"] - now["update_state.keys"] == len(data_keys)
    assert off["update_state.bytes"] > now["update_state.bytes"]
    # Neither reports a processor key as changed when none changed.
    assert now["changed_keys"] == off["changed_keys"] == [["count"]]
    # The keeps-out question is asked once per request-scoped key, not twice.
    assert now["keeps_out_of_state.sync.calls"] < off["keeps_out_of_state.sync.calls"]


async def test_serializable_by_type_gate_off(monkeypatch):
    now = await turn(monkeypatch, ProcessorCounter, "inc")
    off = await turn(monkeypatch, ProcessorCounter, "inc", "serializable_by_type")
    assert now.get("serializable.json_probes", 0) == 0
    # Off, every object that is not JSON data is probed with ``json.dumps``.
    assert off["serializable.json_probes"] > 0
    assert now["sent_keys"] == off["sent_keys"]


async def test_the_theme_preset_list_is_not_in_the_state(monkeypatch):
    """The issue's 14.4 KB per turn was the theme preset list. Since #3631 it
    rides the sidecar; what was left was a few hundred bytes of settings
    strings, which are now sent only when they change."""
    off = await turn(monkeypatch, ProcessorCounter, "inc", *_per_turn.ALL)
    assert "theme_presets" not in off["sent_keys"][0]
    assert off["update_state.bytes"] < 1024


# --- #3999: one changed cell in a 1,000-row list ------------------------------------


async def test_one_changed_cell_walks_the_list_three_times_not_four(monkeypatch):
    """Two snapshot walks and one conversion walk that builds no fingerprint.

    The sync takes the post-event snapshot's walk of the list as its
    fingerprint and as its "is this plain data" answer (``snapshot_fingerprints``),
    so the only walk it makes is the conversion for Rust (``convert_for_render``).
    """
    size = harness.nodes(make_rows(ROWS))
    now = await turn(monkeypatch, Table, "cell")
    off = await turn(monkeypatch, Table, "cell", "plain_sub_ids", "snapshot_fingerprints")
    assert now["reply"] == off["reply"] == "patch"
    # Before and after the handler, and once to convert for Rust.
    assert now["snapshot_assigns.calls"] == 2
    assert 2 * size <= now["fingerprint.snapshot.nodes"] < 2 * size + 500
    # The conversion walks the changed row only (``list_item_deltas``, #4325).
    assert now["convert_for_render.nodes"] == harness.nodes(make_rows(ROWS)[0])
    assert now.get("fingerprint_and_convert.calls", 0) == 0
    assert now.get("fingerprint.sync.calls", 0) == 0
    # No walk collects the id of every object under the list.
    assert now.get("collect_sub_ids.calls", 0) == 0
    assert off["collect_sub_ids.calls"] == 1
    assert off["collect_sub_ids.ids"] > ROWS
    # The same key is reported changed; it reaches Rust as one row.
    assert now["changed_keys"] == off["changed_keys"] == [["items"]]
    assert off["sent_keys"] == [["items"]] and now["sent_keys"] == [[]]
    assert now["update_state_items.rows"] == 1
    assert off["fingerprint_and_convert.nodes"] == size
    assert off.get("convert_for_render.calls", 0) == 0


async def test_snapshot_fingerprints_gate_off(monkeypatch):
    """Off, the sync fingerprints and converts the list in one fused walk again,
    and still skips the id walk because the fused walk found it plain (#3999's
    first shortcut, ``plain_sub_ids``)."""
    size = harness.nodes(make_rows(ROWS))
    now = await turn(monkeypatch, Table, "cell")
    off = await turn(monkeypatch, Table, "cell", "snapshot_fingerprints")
    assert off["fingerprint_and_convert.nodes"] == size
    assert off.get("convert_for_render.calls", 0) == 0
    assert off.get("collect_sub_ids.calls", 0) == now.get("collect_sub_ids.calls", 0) == 0
    # Without the snapshot's walks there is no row delta either: the list goes whole.
    assert off["sent_keys"] == [["items"]]
    assert off.get("update_state_items.calls", 0) == 0
    assert now["changed_keys"] == off["changed_keys"] == [["items"]]
    assert now["snapshot_assigns.calls"] == off["snapshot_assigns.calls"] == 2


async def test_plain_sub_ids_gate_off_with_the_snapshot_walk(monkeypatch):
    """The snapshot's plain answer is used only under ``plain_sub_ids``: off,
    the id walk comes back even though the snapshot walk is reused."""
    off = await turn(monkeypatch, Table, "cell", "plain_sub_ids")
    assert off["collect_sub_ids.calls"] == 1
    assert off.get("fingerprint_and_convert.calls", 0) == 0
    assert off["changed_keys"] == [["items"]]


async def test_list_item_deltas_gate_off(monkeypatch):
    """One changed cell reaches Rust as one row (#4325); off, the whole list is
    converted and sent. The page Rust renders is the same either way
    (``test_per_turn_oracle.py`` compares the frames)."""
    size = harness.nodes(make_rows(ROWS))
    now = await turn(monkeypatch, Table, "cell")
    off = await turn(monkeypatch, Table, "cell", "list_item_deltas")
    assert now["update_state_items.calls"] == 1 and now["update_state_items.rows"] == 1
    assert off.get("update_state_items.calls", 0) == 0
    assert off["convert_for_render.nodes"] == size
    assert off["sent_keys"] == [["items"]]
    sent_now = now["update_state.bytes"] + now["update_state_items.bytes"]
    assert 50 * sent_now < off["update_state.bytes"]
    assert now["changed_keys"] == off["changed_keys"] == [["items"]]


async def test_plain_model_scans_gate_off(monkeypatch):
    """A list the snapshot proved plain is not searched for a Model (#4325); off,
    the sync scans it again (``_normalize_db_values``, ``_holds_db_value``)."""
    now = await turn(monkeypatch, Table, "cell")
    off = await turn(monkeypatch, Table, "cell", "plain_model_scans")
    assert now.get("holds_db_value.calls", 0) == 0
    assert off["holds_db_value.calls"] >= 1
    assert now["normalize_db_values.calls"] < off["normalize_db_values.calls"]
    assert now["changed_keys"] == off["changed_keys"]


async def test_loop_item_render_gate_off(monkeypatch):
    """One changed cell re-renders one loop item in Rust; the other rows come
    from the per-item store (#4325). Off, every item renders. The frames are
    the same either way (``test_per_turn_oracle.py``)."""
    now = await turn(monkeypatch, IfTable, "cell")
    off = await turn(monkeypatch, IfTable, "cell", "loop_item_render")
    assert now["loop_item_store.hits"] == ROWS - 1
    assert now["loop_item_store.misses"] == 1
    assert off.get("loop_item_store.hits", 0) == 0


async def test_an_unrelated_change_does_not_walk_the_list_in_the_sync(monkeypatch):
    """A turn that does not change the list walks it twice (the two snapshots).
    The sync compares the post-event snapshot's fingerprint of the list with the
    last render's instead of walking it a third time. A view whose
    ``get_context_data`` runs between the two does not get this
    (``test_per_turn_invalidation.py`` pins that case)."""
    size = harness.nodes(make_rows(ROWS))
    now = await turn(monkeypatch, Table, "retitle")
    off = await turn(monkeypatch, Table, "retitle", "snapshot_fingerprints")
    assert now["sent_keys"] == off["sent_keys"] == [["title"]]
    assert now.get("fingerprint.sync.calls", 0) == 0
    assert off["fingerprint.sync.nodes"] == size
    assert now.get("collect_sub_ids.ids", 0) <= 1


async def test_the_walk_grows_with_the_list_and_no_faster(monkeypatch):
    """A quarter of the rows is a quarter of the walk: counted, not timed."""
    big = await turn(monkeypatch, Table, "cell")
    small = await turn(monkeypatch, SmallTable, "cell")
    ratio = big["fingerprint.snapshot.nodes"] / small["fingerprint.snapshot.nodes"]
    assert 3.9 < ratio < 4.1

    def total(counts: Dict[str, Any]) -> int:
        nodes: int = counts["fingerprint.snapshot.nodes"] + counts["convert_for_render.nodes"]
        return nodes

    assert 3.8 < total(big) / total(small) < 4.1


# --- #4107: a turn that changes nothing saved --------------------------------------


async def test_an_unchanged_turn_neither_serializes_nor_writes_the_saved_state(monkeypatch):
    """Two ``nothing`` turns after the mount. With the shortcut, the save reads
    the turn's change detection and does no work; without it, each turn
    serializes the view's saved state and writes the session."""
    from djust import _automatic_state, _session_merge

    seen: Dict[str, int] = {"project": 0, "write": 0}
    real_project, real_save = _automatic_state.project, _session_merge.save_merged

    def project(view):
        seen["project"] += 1
        return real_project(view)

    def save_merged(*args, **kwargs):
        seen["write"] += 1
        return real_save(*args, **kwargs)

    monkeypatch.setattr(_automatic_state, "project", project)
    monkeypatch.setattr(_session_merge, "save_merged", save_merged)
    now = await turn(monkeypatch, BareCounter, "nothing")
    with_shortcut = dict(seen)
    seen.update(project=0, write=0)
    off = await turn(monkeypatch, BareCounter, "nothing", "unchanged_save_skip")
    assert now["reply"] == off["reply"] == "noop"
    # The mount saves what mount() built (no page GET came first here); the
    # two turns that change nothing neither serialize nor write.
    # (Two projections at the mount: the save, and the baseline taken once
    # handle_params() has run, against which a handler's changes are found.)
    assert with_shortcut == {"project": 2, "write": 1}
    # Gate-off: the mount's two, then one projection and one write per turn.
    assert seen == {"project": 4, "write": 3}
    # The snapshots change detection takes are the same two per turn either way.
    assert now["snapshot_assigns.calls"] == off["snapshot_assigns.calls"] == 2


# --- what the socket holds after the turn (#3998) -------------------------------------


async def test_the_compact_fingerprint_is_a_third_of_the_wide_one(monkeypatch):
    """Counted, not weighed on a scale: the bytes of the fingerprint tuples
    for 1,000 rows, held as trees (``digest_fingerprints`` off, so there is a
    tree to weigh). Each leaf was a 3-tuple and each dict item a 2-tuple; a
    leaf is now the value and a dict's items one flat tuple. With digests on,
    the encoding changes what a turn allocates, not what the socket keeps."""
    now = await turn(monkeypatch, Table, "cell")
    wide = await turn(monkeypatch, Table, "cell", "compact_fingerprints")
    assert wide["reply"] == now["reply"] == "patch"
    # The walk itself is the same walk: no node more, none fewer.
    assert wide["convert_for_render.nodes"] == now["convert_for_render.nodes"]
    assert wide["fingerprint.snapshot.nodes"] == now["fingerprint.snapshot.nodes"]
    # What is kept is a digest either way.
    assert wide["fingerprints.retained_bytes"] == now["fingerprints.retained_bytes"]
    compact_trees = await turn(monkeypatch, Table, "cell", "digest_fingerprints")
    wide_trees = await turn(
        monkeypatch, Table, "cell", "compact_fingerprints", "digest_fingerprints"
    )
    assert (
        wide_trees["fingerprints.retained_bytes"] > 3 * compact_trees["fingerprints.retained_bytes"]
    )


async def test_a_digest_is_held_where_two_trees_were(monkeypatch):
    """The dirty baseline and the last render's fingerprint of the rows are a
    16-byte digest each (#3998). Turned off, the socket holds the two trees,
    each larger than the rows; the turn sends the same keys either way."""
    now = await turn(monkeypatch, Table, "cell")
    trees = await turn(monkeypatch, Table, "cell", "digest_fingerprints")
    assert trees["reply"] == now["reply"] == "patch"
    assert trees["sent_keys"] == now["sent_keys"]
    # The baseline's entries and two digests: under 2 KB for 1,000 rows.
    assert now["fingerprints.retained_bytes"] < 2048, now["fingerprints.retained_bytes"]
    assert trees["fingerprints.retained_bytes"] > 100 * now["fingerprints.retained_bytes"]


async def test_a_deferred_turn_saves_nothing_before_its_frame(monkeypatch):
    """With an interval set, an event turn's save leaves the turn (design:
    save frequency): no save, no session write, no save hop. The close's
    flush, which writes it, is held off here so only the turn is counted;
    turned off, the turn saves as before."""
    from django.test import override_settings

    from djust import runtime

    async def no_flush(self, *, reason="interval"):
        return True

    monkeypatch.setattr(runtime.ViewRuntime, "flush_state", no_flush)
    with override_settings(DJUST_STATE_SAVE_INTERVAL=60):
        now = await turn(monkeypatch, BareCounter, "inc")
        off = await turn(monkeypatch, BareCounter, "inc", "deferred_save")
    assert now["reply"] == off["reply"]
    assert now.get("save_server_state.calls", 0) == 0
    assert now.get("explicit_save.hops.calls", 0) == 0
    assert now.get("session.save.calls", 0) == 0
    assert off["save_server_state.calls"] == 1
    assert off["explicit_save.hops.calls"] == 1
    assert off["session.save.calls"] == 1


def test_every_shortcut_has_a_gate_off_in_this_file():
    """Derived from the source: each name in ``_per_turn.ALL`` is turned off, by
    name, in a ``turn(...)`` call above, so a shortcut added without a gate-off
    fails here."""
    import pathlib
    import re

    source = pathlib.Path(__file__).read_text()
    turned_off = set(re.findall(r'await turn\(monkeypatch, \w+, "\w+", "([a-z_]+)"\)', source))
    assert turned_off == set(_per_turn.ALL)
