"""A key deleted from the context must stop rendering (#2564).

``RustLiveView.update_state`` merges and never removes: the bridge sends only
the keys that CHANGED since the last render, and a key that is simply absent
from the next context is not a change the detector can see. So the Rust state
kept its last value, and ``{{ secret }}`` kept answering after
``del self.secret`` — content gating fail-open, for a string, a dict, and
(with ADR-027's ``template_resolve_lazy`` ON, which widens the class to every
plain object) an object with attributes.

The fix is full-context truth, in two mechanisms that must redden separately
(#2135):

1. ``RustLiveView.retain_state_keys(keys)`` — called by ``_sync_state_to_rust``
   with the FULL context's keys before every ``update_state``, drops every
   state key absent from the context and revokes those keys' safe grants the
   way #2300's replace-revoke does. Not tombstones from ``prev_refs``: that
   fingerprint is emptied by ``_force_full_html`` on every restore, so a key
   that vanished across a restore would never be tombstoned
   (``TestRestoreThenDeleteThenRender``).
2. The removed set joins ``set_changed_keys``. A removed key is not in the
   changed context, so a partial ``render_with_diff`` would skip its region
   and the OLD text would survive the render
   (``TestPartialRenderPatchesTheRemovedRegion``).

Refs #2564, #2539 (ADR-027 movement 3 prerequisite A), #2300, #1646, #1468.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("django")

from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402

pytestmark = pytest.mark.django_db  # LiveViewTestClient.mount() creates a session

SECRET = "SECRET-A"
HOSTILE = "<img src=x onerror=alert(1)>"


@contextlib.contextmanager
def resolve_lazy(enabled: bool):
    """Flip the ADR-027 kill-switch through the REAL wiring (mirrors the 2539 net)."""
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


def _root(html: str) -> str:
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


class Holder:
    """A plain object — the shape the lazy flag adds to the class."""

    def __init__(self) -> None:
        self.secret = SECRET


SHAPES = [
    pytest.param(lambda: SECRET, "{{ k }}", id="string"),
    pytest.param(lambda: {"secret": SECRET}, "{{ k.secret }}", id="dict"),
    pytest.param(Holder, "{{ k.secret }}", id="plain-object"),
]
FLAGS = [pytest.param(False, id="flag-off"), pytest.param(True, id="flag-on")]


def _make_delete_view(make_value: Any, source: str) -> type:
    class _V(LiveView):
        def mount(self, request, **kwargs):
            self.k = make_value()

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.pop("k", None)
            if hasattr(self, "k"):
                ctx["k"] = self.k
            return ctx

        def forget(self):
            del self.k

    _V.template = f"<div dj-root>{source}</div>"
    return _V


# ---------------------------------------------------------------------------
# The vulnerability, on the LiveView entry, under both flag states
# ---------------------------------------------------------------------------
class TestDeleteThenRenderOnTheLiveViewEntry2564:
    @pytest.mark.parametrize("flag", FLAGS)
    @pytest.mark.parametrize(("make_value", "source"), SHAPES)
    def test_a_deleted_key_renders_empty(self, flag: bool, make_value: Any, source: str) -> None:
        with resolve_lazy(flag):
            client = LiveViewTestClient(_make_delete_view(make_value, source))
            client.mount()
            assert SECRET in client.render(), "premise: the value renders while present"
            assert client.send_event("forget")["success"]
            out = _root(client.render())
            assert SECRET not in out, f"a deleted key kept rendering its last value: {out!r}"
            assert out == ""

    @pytest.mark.parametrize("flag", FLAGS)
    def test_a_key_that_comes_back_renders_again(self, flag: bool) -> None:
        """Removal is not a tombstone: re-adding the key re-renders it."""

        class _V(LiveView):
            def mount(self, request, **kwargs):
                self.k = SECRET

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx.pop("k", None)
                if hasattr(self, "k"):
                    ctx["k"] = self.k
                return ctx

            def forget(self):
                del self.k

            def restore(self):
                self.k = "SECRET-B"

        _V.template = "<div dj-root>{{ k }}</div>"
        with resolve_lazy(flag):
            client = LiveViewTestClient(_V)
            client.mount()
            assert SECRET in client.render()
            client.send_event("forget")
            assert _root(client.render()) == ""
            client.send_event("restore")
            assert _root(client.render()) == "SECRET-B"


# ---------------------------------------------------------------------------
# The content-gating shape the issue names
# ---------------------------------------------------------------------------
def _make_gated_view(source: str) -> type:
    class _V(LiveView):
        def mount(self, request, **kwargs):
            self.show = True
            self.n = 0

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.pop("secret", None)
            if self.show:
                ctx["secret"] = SECRET
            return ctx

        def hide(self):
            self.show = False

        def hide_and_touch(self):
            # A SECOND changed key, so the diff render takes the PARTIAL path
            # (`set_changed_keys` is only called when something changed) and
            # the removed key's region can be skipped.
            self.show = False
            self.n += 1

    _V.template = f"<div dj-root>{source}</div>"
    return _V


class TestIfGatedShape2564:
    @pytest.mark.parametrize("flag", FLAGS)
    def test_the_region_is_empty_after_the_gate_flips(self, flag: bool) -> None:
        view = _make_gated_view("{% if secret %}<span>{{ secret }}</span>{% endif %}")
        with resolve_lazy(flag):
            client = LiveViewTestClient(view)
            client.mount()
            assert SECRET in client.render(), "premise: gated content renders while open"
            client.send_event("hide")
            out = _root(client.render())
            assert SECRET not in out, f"the gate flipped and the content survived: {out!r}"
            assert "<span" not in out, out


class TestPartialRenderPatchesTheRemovedRegion2564:
    """Mechanism 2. A removed key is not in the changed context, so without
    joining the removed set to ``set_changed_keys`` the partial render serves
    its region from the node cache — the old text, not a patch."""

    @pytest.mark.parametrize("flag", FLAGS)
    def test_the_removed_region_is_re_rendered_not_served_from_cache(self, flag: bool) -> None:
        view = _make_gated_view("<span>{{ n }}</span><p>{% if secret %}{{ secret }}{% endif %}</p>")
        with resolve_lazy(flag):
            client = LiveViewTestClient(view)
            client.mount()
            html, _, _ = client.render_with_patches()  # baseline + node cache
            assert SECRET in html
            client.send_event("hide_and_touch")
            html, patches, _ = client.render_with_patches()
            assert re.search(r"<span[^>]*>1</span>", html), "premise: the other change rendered"
            assert SECRET not in html, (
                f"partial render served the removed key's region from cache: {html!r}"
            )
            assert patches, "the removed region must produce a patch"
            assert not any(SECRET in str(p) for p in patches)


# ---------------------------------------------------------------------------
# The prev_refs-blind path: restore, then delete, then render
# ---------------------------------------------------------------------------
class TestRestoreThenDeleteThenRender2564:
    """A restore sets ``_force_full_html`` which empties ``prev_refs``, so a
    tombstone computed from the fingerprint would never see a key removed
    across it. Full-context truth does."""

    @pytest.mark.parametrize("flag", FLAGS)
    def test_a_key_removed_across_a_restore_is_gone(self, flag: bool) -> None:
        with resolve_lazy(flag):
            client = LiveViewTestClient(_make_delete_view(lambda: SECRET, "{{ k }}"))
            client.mount()
            assert SECRET in client.render()
            view = client.view_instance
            # The state backend's clone (memory.py / redis.py) — the view
            # comes back from bytes, and the runtime forces a full sync.
            clone = _rust.RustLiveView.deserialize_msgpack(view._rust_view.serialize_msgpack())
            view._rust_view = clone
            view._force_full_html = True
            del view.k
            out = _root(client.render())
            assert SECRET not in out, f"the restored clone kept the deleted key: {out!r}"
            assert out == ""

    def test_the_rust_api_alone(self) -> None:
        """Same path with no Python view: the clone carries the key, the
        caller's full context does not."""
        view = _rust.RustLiveView("{{ k }}")
        view.update_state({"k": SECRET})
        assert view.render() == SECRET
        clone = _rust.RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert clone.render() == SECRET, "premise: the clone carries the key"
        assert clone.retain_state_keys([]) == ["k"]
        clone.update_state({})
        assert clone.render() == ""


# ---------------------------------------------------------------------------
# The Rust method's own contract
# ---------------------------------------------------------------------------
class TestRetainStateKeys2564:
    def test_returns_the_removed_keys_and_keeps_the_rest(self) -> None:
        view = _rust.RustLiveView("{{ a }}|{{ b }}|{{ c }}")
        view.update_state({"a": "1", "b": "2", "c": "3"})
        removed = view.retain_state_keys(["b", "never-present"])
        assert sorted(removed) == ["a", "c"]
        assert view.render() == "|2|"
        assert view.retain_state_keys(["b"]) == [], "a second call has nothing left to drop"

    def test_removal_revokes_the_safe_grant(self) -> None:
        """#2300's rule extends to removal: a grant cannot outlive its value.
        ``set_state`` re-inserts WITHOUT revoking, so a surviving grant would
        make the re-inserted hostile value live."""
        view = _rust.RustLiveView("{{ p }}")
        view.update_state(normalize_django_value({"p": mark_safe("<b>x</b>")}))
        view.mark_safe_keys(["p"])
        assert view.render() == "<b>x</b>"
        assert view.retain_state_keys([]) == ["p"]
        view.set_state("p", HOSTILE)
        out = view.render()
        assert "<img" not in out, f"the grant outlived the removed key: {out!r}"

    def test_removal_revokes_descendant_grants(self) -> None:
        view = _rust.RustLiveView('{{ p|join:", " }}')
        view.update_state(normalize_django_value({"p": [mark_safe("<b>x</b>")]}))
        view.mark_safe_keys(["p.0"])
        assert view.render() == "<b>x</b>"
        view.retain_state_keys([])
        view.set_state("p", [HOSTILE])
        out = view.render()
        assert "<img" not in out, f"an item grant outlived its removed list: {out!r}"

    def test_a_grant_on_a_retained_key_survives(self) -> None:
        view = _rust.RustLiveView("{{ p }}|{{ q }}")
        view.update_state(
            normalize_django_value({"p": mark_safe("<b>p</b>"), "q": mark_safe("<i>q</i>")})
        )
        view.mark_safe_keys(["p", "q"])
        assert view.retain_state_keys(["q"]) == ["p"]
        assert view.render() == "|<i>q</i>", "q's grant must survive p's removal"


# ---------------------------------------------------------------------------
# No over-removal: keys the framework folds in every render stay
# ---------------------------------------------------------------------------
class TestFrameworkKeysAreRetained2564:
    def test_static_assigns_and_processor_keys_survive_a_second_render(self) -> None:
        class _V(LiveView):
            static_assigns = ["title"]

            def mount(self, request, **kwargs):
                self.title = "Static"
                self.n = 0

            def bump(self):
                self.n += 1

        _V.template = "<div dj-root>{{ title }}:{{ n }}:{% if request %}req{% endif %}</div>"
        client = LiveViewTestClient(_V)
        client.mount()
        assert _root(client.render()) == "Static:0:req"
        client.send_event("bump")
        assert _root(client.render()) == "Static:1:req", (
            "a key the framework re-sends every sync was dropped by the retain-set"
        )


# ---------------------------------------------------------------------------
# Structural pin: every framework `update_state` caller decided (#1646)
# ---------------------------------------------------------------------------
_PKG = Path(__file__).resolve().parents[1] / "djust"


def test_every_framework_update_state_caller_is_decided() -> None:
    """Two Python callers. The per-view bridge (long-lived state, MUST retain
    first); the page-shell ``temp_rust`` in template.py (a fresh ``RustLiveView``
    per request with nothing to retain — no call). A third caller is a decision
    someone has to make.

    And the actor's Rust merge (#2592): ``use_actors=True`` bypasses the bridge
    entirely — the ``ViewActor`` pulls the full ``get_context_data()`` itself in
    ``sync_state_from_python`` and merges with ``update_state_rust``. Decided in
    Rust: it calls ``retain_state_keys_rust`` with the full context's keys
    BEFORE that merge. The detailed pins are in
    ``test_actor_state_retains_full_context_2592.py``; this one keeps the
    actor site in the SAME caller ledger so it is never again "pure-Rust,
    unchanged"."""
    callers = {}
    for path in _PKG.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text()
        n = len(re.findall(r"\.update_state\(", text))
        if n:
            callers[str(path.relative_to(_PKG))] = n
    assert callers == {"mixins/rust_bridge.py": 1, "mixins/template.py": 1}, callers

    bridge = (_PKG / "mixins" / "rust_bridge.py").read_text()
    retain_at = bridge.index("retain_state_keys(")
    update_at = bridge.index("self._rust_view.update_state(")
    assert retain_at < update_at, "retain_state_keys must run before update_state"
    shell = (_PKG / "mixins" / "template.py").read_text()
    assert "retain_state_keys" not in shell, "temp_rust is per-request; decided: no call"

    actor = (_PKG.parents[1] / "crates" / "djust_live" / "src" / "actors" / "view.rs").read_text()
    sync_at = actor.index("fn sync_state_from_python(")
    actor_retain_at = actor.index(
        "retain_state_keys_rust(state.keys().cloned().collect())", sync_at
    )
    actor_update_at = actor.index("update_state_rust(state)", sync_at)
    assert actor_retain_at < actor_update_at, (
        "the actor's full-context merge must retain first (#2592)"
    )
