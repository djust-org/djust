"""The actor-session state merge drops keys absent from the full context (#2592).

The twin of #2564 for ``use_actors=True``. The ``ViewActor`` (``crates/
djust_live/src/actors/view.rs``) pulls the WHOLE ``get_context_data()`` in
``sync_state_from_python`` on every event and merged it with
``update_state_rust`` — a merge with no removal path, so a key the view
stopped carrying kept its last value in the actor's state: ``del self.secret``
and the ``if self.show: ctx["secret"] = …`` gate both failed open. The
Python-side #2564 pin could not see it because the actor path never runs
``_sync_state_to_rust`` (and so never calls ``retain_state_keys``).

The fix mirrors #2564 in Rust: ``retain_state_keys_rust`` runs with the full
context's keys BEFORE the merge, revokes the removed keys' safe grants, and
joins them to a pending changed set. The BEHAVIOUR is pinned where the actor's
state is observable — ``cargo test -p djust_live --no-default-features 2592``
drives the real ``sync_state_from_python`` path with a real Python object on an
actor whose backend carries a template (``ViewActor::with_template``). It
cannot be pinned from here through a render: the mount path's ``ViewActor::new``
builds the backend with an EMPTY template (the LIMITATION note at view.rs
``new``), so a ``use_actors=True`` mount renders an empty document today and a
Python-level assertion on the HTML would be vacuous for any value.

What THIS file pins is the Python half of the decision — the callers that hand
the actor its context, the premise that lets the Rust retain run with no
``static_assigns`` exemption, and that both actor merge sites are decided — plus
the real ``create_session_actor`` round trip under both ADR-027 flag states,
which is where the new retain call executes inside ``Python::attach``.

Refs #2592, #2564, #2539 (ADR-027 movement 3 prerequisite), #1646, #1468.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from pathlib import Path

import pytest

pytest.importorskip("django")

from djust import _rust  # noqa: E402

_PKG = Path(__file__).resolve().parents[1] / "djust"
_ACTORS = Path(__file__).resolve().parents[2] / "crates" / "djust_live" / "src" / "actors"

SECRET = "SECRET-A"
FLAGS = [pytest.param(False, id="flag-off"), pytest.param(True, id="flag-on")]


@contextlib.contextmanager
def resolve_lazy(enabled: bool):
    """Flip the ADR-027 kill-switch through the REAL wiring (mirrors the 2564 net)."""
    from djust.config import config
    from djust.render_env import apply_render_env

    previous = config.get("template_resolve_lazy", False)
    config.update({"template_resolve_lazy": enabled})
    apply_render_env()
    assert _rust.resolve_lazy_enabled() is enabled, "the flag did not reach Rust"
    try:
        yield
    finally:
        config.update({"template_resolve_lazy": previous})
        apply_render_env()


def _fn_body(source: str, name: str) -> str:
    """The text of Rust ``fn name`` up to the next ``fn`` at the same indent."""
    start = source.index(f"fn {name}(")
    indent = source[:start].rsplit("\n", 1)[-1]
    nxt = re.compile(rf"^{re.escape(indent)}(pub )?(async )?fn ", re.M).search(source, start + 1)
    return source[start : nxt.start() if nxt else len(source)]


def _production(source: str) -> str:
    """Rust source with its ``#[cfg(test)] mod tests`` tail cut off."""
    cut = source.find("#[cfg(test)]")
    return source if cut == -1 else source[:cut]


# ---------------------------------------------------------------------------
# The Rust side of the decision, pinned from the Python suite (#1646)
# ---------------------------------------------------------------------------
class TestActorMergeSitesAreDecided2592:
    def test_sync_state_from_python_retains_before_the_merge(self) -> None:
        """The site the issue cites at view.rs:447: the full context arrives
        here, so the retain runs here, and BEFORE the merge."""
        view_rs = _production((_ACTORS / "view.rs").read_text())
        body = _fn_body(view_rs, "sync_state_from_python")
        assert "retain_state_keys_rust(state.keys().cloned().collect())" in body, body
        assert body.index("retain_state_keys_rust(") < body.index("update_state_rust(state)"), (
            "retain must run before the merge"
        )

    def test_both_merge_sites_are_accounted_for(self) -> None:
        """Two ``update_state_rust`` sites in the actor (view.rs:243 and :447).
        The sync site retains inline; the ``UpdateState`` delta site keeps merge
        semantics and gets a truth-half message a long-lived caller pairs it
        with. A third site is a decision someone has to make."""
        view_rs = _production((_ACTORS / "view.rs").read_text())
        assert view_rs.count("update_state_rust(") == 2, view_rs.count("update_state_rust(")
        assert "fn handle_update_state(" in view_rs
        assert "fn handle_retain_state_keys(" in view_rs, "the delta site has no truth half"
        assert "retain_state_keys_rust(keys)" in _fn_body(view_rs, "handle_retain_state_keys")
        messages_rs = (_ACTORS / "messages.rs").read_text()
        assert "RetainStateKeys {" in messages_rs
        assert "pub async fn retain_state_keys(" in view_rs, "the handle does not expose it"

    def test_the_session_mount_merges_into_a_fresh_actor(self) -> None:
        """The only production caller of the delta message: ``handle_mount``
        creates the actor and sends the initial context — nothing to retain."""
        session_rs = _production((_ACTORS / "session.rs").read_text())
        assert session_rs.count(".update_state(") == 1, "a new update_state caller must be decided"
        body = _fn_body(session_rs, "handle_mount")
        assert body.index("ViewActor::new(") < body.index("view_handle.update_state(params)")

    def test_the_static_assigns_flag_is_written_only_by_the_bridge(self) -> None:
        """Why the actor retain has NO ``static_assigns`` exemption (unlike the
        bridge): ``get_context_data`` skips static keys only once
        ``_static_assigns_sent`` is set, and only ``_sync_state_to_rust`` sets
        it — which the actor path never runs. A second writer would make the
        actor's context stop carrying the static keys and the retain would
        drop them."""
        writers = sorted(
            str(p.relative_to(_PKG))
            for p in _PKG.rglob("*.py")
            if "tests" not in p.parts and "_static_assigns_sent = True" in p.read_text()
        )
        assert writers == ["mixins/rust_bridge.py"], writers

    def test_the_python_actor_callers_hand_over_the_full_context(self) -> None:
        """The Python half: the actor mount sends ``get_context_data()`` and the
        actor event lets Rust pull it — the full context crosses on both, which
        is what makes the Rust-side retain the truth."""
        runtime = (_PKG / "runtime.py").read_text()
        mount = runtime.index("result = await consumer.actor_handle.mount(")
        context = runtime.rindex(
            "context_data = await sync_to_async(view.get_context_data)()", 0, mount
        )
        assert mount - context < 200, "the context is rendered right before the actor mount"
        assert "actor_handle.event(event_name, params)" in runtime
        view_rs = _production((_ACTORS / "view.rs").read_text())
        assert 'getattr("get_context_data")' in _fn_body(view_rs, "sync_state_from_python")


# ---------------------------------------------------------------------------
# The real Python → actor round trip, both flag states
# ---------------------------------------------------------------------------
class Holder:
    def __init__(self) -> None:
        self.secret = SECRET


class _DeleteView:
    """The #2564 delete-then-render shape, driven through the real actor."""

    def __init__(self, value) -> None:
        self.k = value

    def get_context_data(self):
        return {"k": self.k} if hasattr(self, "k") else {}

    def forget(self):
        del self.k


class TestActorRoundTrip2592:
    """The new retain executes inside ``Python::attach`` on the real
    ``create_session_actor`` path. These are NOT the removal assertion (the
    actor's render is an empty document, see the module docstring) — they pin
    that the path survives a delete for every value shape under both flags,
    including the plain object the lazy flag adds to the class."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag", FLAGS)
    @pytest.mark.parametrize(
        "make_value",
        [
            pytest.param(lambda: SECRET, id="string"),
            pytest.param(lambda: {"secret": SECRET}, id="dict"),
            pytest.param(Holder, id="plain-object"),
        ],
    )
    async def test_delete_then_event_round_trips_through_the_actor(self, flag, make_value) -> None:
        from djust._rust import create_session_actor

        with resolve_lazy(flag):
            # A fresh id per case: the supervisor keys sessions by id, and a
            # reused id hands back the previous case's shut-down actor.
            handle = await create_session_actor(f"t2592-{uuid.uuid4()}")
            try:
                view = _DeleteView(make_value())
                mounted = await handle.mount("t2592.V", view.get_context_data(), view)
                assert "html" in mounted
                result = await handle.event("forget", {})
                assert not hasattr(view, "k"), "premise: the handler ran"
                assert result["version"] > 1, result
            finally:
                await handle.shutdown()
