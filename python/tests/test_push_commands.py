"""Tests for ``LiveView.push_commands()`` — ADR-002 Phase 1a.

Covers the server-side half of backend-driven UI automation:

- ``push_commands(chain)`` serializes a ``JSChain`` into a ``djust:exec``
  push event payload that the client-side auto-executor picks up.
- Rejects non-``JSChain`` arguments with a clear ``TypeError``.
- Queues multiple commands in order (chains and event queue both respect
  insertion order, so multi-step flows replay deterministically).
- Composes with ``push_event`` — the two APIs share the same underlying
  queue without interfering with each other.
- Serializes every JS Command op type the ``JSChain`` supports
  (show/hide/add_class/focus/dispatch/push) so the exec chain is fully
  round-trippable through JSON.

The client-side half (the ``djust:exec`` auto-executor hooking
``window.djust.js._executeOps`` off a ``djust:push_event`` event) is
covered by ``tests/js/exec-listener.test.js``.
"""

from __future__ import annotations

import json

import pytest

from djust.js import JS, JSChain
from djust.mixins.push_events import PushEventMixin


class _Bag(PushEventMixin):
    """Minimal push-event host for unit testing.

    Avoids bringing in the full ``LiveView`` so tests exercise the mixin
    in isolation and don't need Django request machinery.
    """


@pytest.fixture
def bag() -> _Bag:
    return _Bag()


class TestPushCommandsBasics:
    def test_single_op_chain_queues_one_djust_exec_event(self, bag):
        chain = JS.add_class("highlight", to="#btn")
        bag.push_commands(chain)

        events = bag._drain_push_events()
        assert len(events) == 1

        name, payload = events[0]
        assert name == "djust:exec"
        assert payload == {"ops": [["add_class", {"to": "#btn", "names": "highlight"}]]}

    def test_multi_op_chain_preserves_order(self, bag):
        chain = (
            JS.show("#modal")
            .add_class("open", to="#overlay")
            .focus("#modal-title")
            .dispatch("modal:opened", detail={"step": 1})
        )
        bag.push_commands(chain)

        _, payload = bag._drain_push_events()[0]
        op_names = [op[0] for op in payload["ops"]]
        assert op_names == ["show", "add_class", "focus", "dispatch"]

    def test_empty_chain_still_pushes_an_event(self, bag):
        """An empty chain is valid — it queues a djust:exec with ops=[]."""
        empty = JSChain()
        bag.push_commands(empty)

        _, payload = bag._drain_push_events()[0]
        assert payload == {"ops": []}


class TestSerialization:
    def test_payload_is_json_round_trippable(self, bag):
        """The payload must round-trip through JSON cleanly because the
        WebSocket transport serializes it before sending.
        """
        chain = (
            JS.show("#modal")
            .add_class("active", to="#overlay")
            .transition("fade-in", to="#modal", time=300)
            .set_attr("data-open", "true", to="#panel")
            .dispatch("my:event", detail={"nested": {"deep": [1, 2, 3]}})
            .push("save_draft", value={"id": 42})
        )
        bag.push_commands(chain)

        _, payload = bag._drain_push_events()[0]
        dumped = json.dumps(payload)
        reloaded = json.loads(dumped)
        assert reloaded == payload

    def test_chain_ops_are_copied_not_referenced(self, bag):
        """Mutating the original chain after push_commands must not affect
        the queued payload. JSChain is frozen so this should hold by
        construction — verify it explicitly so future refactors don't break
        the invariant.
        """
        chain = JS.show("#a")
        bag.push_commands(chain)

        # Build a new chain — the original is frozen and should be unchanged
        _ = chain.hide("#b")  # discard — we only care that base chain is intact
        _, payload = bag._drain_push_events()[0]
        assert len(payload["ops"]) == 1
        assert payload["ops"][0][0] == "show"


class TestTypeValidation:
    def test_rejects_string(self, bag):
        with pytest.raises(TypeError, match="expects a djust.js.JSChain"):
            bag.push_commands("not a chain")

    def test_rejects_dict(self, bag):
        with pytest.raises(TypeError, match="expects a djust.js.JSChain"):
            bag.push_commands({"ops": []})

    def test_rejects_list(self, bag):
        """A raw ops list is NOT a valid argument — require a JSChain."""
        with pytest.raises(TypeError, match="expects a djust.js.JSChain"):
            bag.push_commands([["show", {"to": "#x"}]])

    def test_rejects_none(self, bag):
        with pytest.raises(TypeError, match="expects a djust.js.JSChain"):
            bag.push_commands(None)


class TestComposition:
    def test_interleave_with_push_event(self, bag):
        """push_event and push_commands share the same queue and must preserve
        their interleaving order so event listeners downstream see a
        deterministic sequence.
        """
        bag.push_event("flash", {"message": "saving…"})
        bag.push_commands(JS.add_class("busy", to="#form"))
        bag.push_event("analytics", {"action": "save_start"})
        bag.push_commands(JS.remove_class("busy", to="#form"))

        events = bag._drain_push_events()
        assert [e[0] for e in events] == [
            "flash",
            "djust:exec",
            "analytics",
            "djust:exec",
        ]

    def test_multiple_push_commands_calls_queue_separately(self, bag):
        """Each push_commands call queues its own djust:exec event so the
        client can distinguish ordered steps — e.g. a tutorial that
        highlights step 1, waits, then highlights step 2, would fire two
        separate chains, not one merged chain.
        """
        bag.push_commands(JS.add_class("step1", to="#a"))
        bag.push_commands(JS.add_class("step2", to="#b"))

        events = bag._drain_push_events()
        assert len(events) == 2
        assert events[0][1]["ops"][0][1]["names"] == "step1"
        assert events[1][1]["ops"][0][1]["names"] == "step2"

    def test_drain_clears_queue(self, bag):
        bag.push_commands(JS.show("#modal"))
        assert len(bag._drain_push_events()) == 1
        assert bag._drain_push_events() == []


class TestJSChainFactoryParity:
    """Smoke-test every op in the JSChain factory makes it through
    push_commands round-trip. If one of the 11 commands regresses its
    serialization, this suite flags it.
    """

    @pytest.mark.parametrize(
        "chain_builder,expected_op_name",
        [
            (lambda: JS.show("#modal"), "show"),
            (lambda: JS.hide("#modal"), "hide"),
            (lambda: JS.toggle("#sidebar"), "toggle"),
            (lambda: JS.add_class("active", to="#btn"), "add_class"),
            (lambda: JS.remove_class("active", to="#btn"), "remove_class"),
            (lambda: JS.transition("fade-in", to="#modal", time=300), "transition"),
            (lambda: JS.set_attr("data-open", "true", to="#panel"), "set_attr"),
            (lambda: JS.remove_attr("disabled", to="#btn"), "remove_attr"),
            (lambda: JS.focus("#input"), "focus"),
            (lambda: JS.dispatch("my:event"), "dispatch"),
            (lambda: JS.push("save_draft"), "push"),
        ],
    )
    def test_each_op_serializes(self, bag, chain_builder, expected_op_name):
        bag.push_commands(chain_builder())
        _, payload = bag._drain_push_events()[0]
        assert payload["ops"][0][0] == expected_op_name
        # Every op's args must be a JSON-serializable dict
        args = payload["ops"][0][1]
        assert isinstance(args, dict)
        json.dumps(args)  # will raise if not serializable
