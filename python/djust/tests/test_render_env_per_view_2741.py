"""ADR-029 (#2741) — the render environment as per-view config.

Every per-render Django setting Rust needs is a ``thread_local!`` cell that
``djust.render_env.apply_render_env`` pushes on the thread about to render.
That is correct for a render on the pushing thread and silently wrong for a
render on any other thread: the ``ViewActor`` under ``use_actors=True``
renders on a tokio worker that never pushed, so ``template_resolve_lazy:
False``, ``TIME_ZONE`` and the locale's number format were all ignored there
(the #2751 probe observed it).

The fix carries the environment the way ``template_auto_call`` is carried —
a FIELD on the backend (``capture_render_env`` / ``set_render_env``), applied
by every render entry on whatever thread it runs, under a guard that restores
the cells afterwards. These cases are the Python half of ADR-029 §5; the Rust
half (the flipped #2751 probe, the session-mount test, the ``ComponentActor``
parity test) lives beside the actors.

Gate-off per mechanism (#2135), measured with the Rust extension rebuilt:

* the field not applied at ONE backend entry — the parametrized case in
  ``TestACapturedEnvReachesARenderOnAFreshThread`` for that entry goes red
  (and only that one; three entries, three cases, #1104);
* the guard not restoring — ``test_a_render_leaves_poisoned_cells_as_it_found_them``
  goes red, everything else stays green;
* ``render_template`` ignoring ``render_env=`` —
  ``TestThePlainEntryTakesAnExplicitEnv`` goes red;
* the mixin not capturing — ``test_the_mixin_captures_the_env_it_pushed`` goes
  red, and so does the WS actor mount case below.
"""

from __future__ import annotations

import contextlib
import re
import threading
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import translation

from djust import _rust
from djust.render_env import apply_render_env

# `X` under resolve_lazy=true, `Y` under false — measured through
# `_rust.render_template` (the #2751 probe template).
PROBE = '{% firstof nope|default_if_none:"X" "Y" %}'


@pytest.fixture(autouse=True)
def _clean_cells():
    """Leave the pytest thread's cells at their defaults on both sides."""
    _rust.set_resolve_lazy(True)
    _rust.set_active_timezone(None)
    _rust.set_number_format(None)
    yield
    _rust.set_resolve_lazy(True)
    _rust.set_active_timezone(None)
    _rust.set_number_format(None)


def _on_fresh_thread(fn):
    """Run ``fn`` on a thread that never pushed anything; return its result.

    The only place the Rust defaults are observable (the #2539 pins say the
    same): the pytest thread has been pushed on by every test before this one.
    """
    box: list = []

    def worker():
        box.append(fn())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return box[0]


def _fr_push():
    """Push a French number format the way a ``fr`` render would."""
    with translation.override("fr"):
        apply_render_env()


# ---------------------------------------------------------------------------
# The value: what `capture()` sees is what the push made this thread read.
# ---------------------------------------------------------------------------


@override_settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo", USE_I18N=True)
def test_capture_reflects_the_push():
    _rust.set_resolve_lazy(False)
    _fr_push()
    env = _rust.RenderEnv.capture()
    assert env.resolve_lazy is _rust.resolve_lazy_enabled()
    assert env.timezone == "Asia/Tokyo" == _rust.active_timezone_name()
    assert env.number_format == _rust.active_number_format()
    assert env.number_format[0] == ","
    assert env.unlocalized_number_format == _rust.active_unlocalized_number_format()


def test_a_fresh_thread_captures_the_shipped_defaults():
    """`RenderEnv::default()` and a never-pushed thread agree (#1646)."""
    from djust.config import template_resolve_lazy_default

    env = _on_fresh_thread(_rust.RenderEnv.capture)
    assert env.resolve_lazy is template_resolve_lazy_default()
    assert env.timezone is None
    assert env.number_format is None


# ---------------------------------------------------------------------------
# The field reaches every backend entry on a thread that never pushed.
# ---------------------------------------------------------------------------

ENTRIES = ["render", "render_with_diff", "render_binary_diff"]


def _render_via(entry: str, lv) -> str:
    """The probe's one output byte. The diff entries wrap the body in the
    `<html dj-id=...>` document `render` alone does not; strip that."""
    out = getattr(lv, entry)()
    html = out if isinstance(out, str) else out[0]
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.S)
    return m.group(1) if m else html


class TestACapturedEnvReachesARenderOnAFreshThread:
    """The Python twin of the flipped #2751 probe: configure on this thread,
    capture onto the view, render on a thread whose own cell is the default."""

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_the_configured_answer_on_a_fresh_thread(self, entry):
        _rust.set_resolve_lazy(False)
        lv = _rust.RustLiveView(PROBE)
        lv.capture_render_env()
        assert lv.render_env().resolve_lazy is False

        def worker():
            before = _rust.resolve_lazy_enabled()
            html = _render_via(entry, lv)
            after = _rust.resolve_lazy_enabled()
            return before, html, after

        before, html, after = _on_fresh_thread(worker)
        assert before is True, "premise: the fresh thread's own cell is the default"
        assert html == "Y", f"{entry} on a fresh thread rendered the thread default, not the env"
        assert after is True, f"{entry} must leave the thread's cell as it found it"

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_gate_off_an_uncaptured_view_reads_the_thread_default(self, entry):
        """The pre-ADR-029 shape, kept as the sibling that proves the field
        (not the flag on this thread) decided the case above."""
        _rust.set_resolve_lazy(False)
        lv = _rust.RustLiveView(PROBE)
        assert lv.render_env() is None
        assert _on_fresh_thread(lambda: _render_via(entry, lv)) == "X"

    def test_set_render_env_takes_a_value_and_none_clears_it(self):
        _rust.set_resolve_lazy(False)
        env = _rust.RenderEnv.capture()
        _rust.set_resolve_lazy(True)
        lv = _rust.RustLiveView(PROBE)
        lv.set_render_env(env)
        assert _on_fresh_thread(lv.render) == "Y"
        lv.set_render_env(None)
        assert _on_fresh_thread(lv.render) == "X"


# ---------------------------------------------------------------------------
# The guard: a render leaves the cells as it found them.
# ---------------------------------------------------------------------------


def test_a_render_leaves_poisoned_cells_as_it_found_them():
    """The 'SET, not scoped' fix. Capture a clean env, then poison this
    thread the way #2728 did (a `fr` push nobody restored) and render: the
    render uses the env, and the poison is back afterwards — by the guard,
    not by anyone re-pushing."""
    lv = _rust.RustLiveView("{{ v }}")
    lv.update_state({"v": 12.3})
    lv.capture_render_env()  # clean: no number format

    _fr_push()
    _rust.set_active_timezone("Asia/Tokyo")
    assert _rust.active_number_format()[0] == ","

    assert lv.render() == "12.3", "the render must use the captured env, not the poison"

    assert _rust.active_number_format()[0] == ",", "the guard must restore the poisoned format"
    assert _rust.active_timezone_name() == "Asia/Tokyo"


def test_the_2728_shape_is_structurally_green_with_an_env():
    """ADR-029 §5: the #2728 hygiene test stays green — and with an env on
    the view it is green STRUCTURALLY (the guard restores), not because the
    fixture reset ran. A `fr` push, then a direct render on the same thread
    that did NOT go through the view, reads the thread's cells unchanged."""
    lv = _rust.RustLiveView("{{ v }}")
    lv.update_state({"v": 12.3})
    lv.capture_render_env()
    _fr_push()
    assert lv.render() == "12.3"
    # The direct entry still reads the thread — the env is per-VIEW.
    assert _rust.render_template("{{ v }}", {"v": 12.3}) == "12,3"


# ---------------------------------------------------------------------------
# The plain entry: an explicit `render_env=` beside `auto_call`.
# ---------------------------------------------------------------------------


class TestThePlainEntryTakesAnExplicitEnv:
    def test_none_is_the_current_behaviour(self):
        _fr_push()
        assert _rust.render_template("{{ v }}", {"v": 12.3}) == "12,3"
        _rust.set_number_format(None)
        assert _rust.render_template("{{ v }}", {"v": 12.3}) == "12.3"

    def test_an_explicit_env_is_installed_for_that_render_only(self):
        _fr_push()
        fr = _rust.RenderEnv.capture()
        _rust.set_number_format(None)
        assert _rust.render_template("{{ v }}", {"v": 12.3}, render_env=fr) == "12,3"
        assert _rust.active_number_format() is None, "restored on return"
        assert _rust.render_template("{{ v }}", {"v": 12.3}) == "12.3"

    def test_the_env_covers_the_conversion_too(self):
        """`resolve_lazy` is read at conversion time as well as render time;
        the guard is installed before the context is converted."""
        _rust.set_resolve_lazy(False)
        off = _rust.RenderEnv.capture()
        _rust.set_resolve_lazy(True)
        assert _rust.render_template(PROBE, {}) == "X"
        assert _rust.render_template(PROBE, {}, render_env=off) == "Y"
        assert _rust.resolve_lazy_enabled() is True


# ---------------------------------------------------------------------------
# The Python push sites capture.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_mixin_captures_the_env_it_pushed():
    """`RustBridgeMixin._apply_render_env` pushes and then captures, so the
    view carries what this render would have read. Driven through a real
    render (`_rust_view` is created lazily on the first one)."""
    from djust import LiveView
    from djust.testing import LiveViewTestClient

    class V(LiveView):
        template = "<div>{{ v }}</div>"

        def mount(self, request, **kwargs):
            self.v = 1

    client = LiveViewTestClient(V)
    client.mount()
    with override_settings(USE_TZ=True, TIME_ZONE="Asia/Tokyo"):
        client.render_with_patches()
    env = client.view_instance._rust_view.render_env()
    assert env is not None, "the mixin pushed but did not capture"
    assert env.timezone == "Asia/Tokyo"


def test_the_component_entry_captures_too():
    """The `template_name` LiveComponent entry wires the env beside the
    auto-call flag it already wires (components/base.py)."""
    src = Path(__file__).resolve().parents[1] / "components" / "base.py"
    body = src.read_text()
    fn = body[body.index("def _render_template_name_with_markers") :]
    fn = fn[: fn.index("\nclass ")]
    assert "set_template_auto_call(" in fn and "capture_render_env()" in fn


# ---------------------------------------------------------------------------
# Structural pin (#2727 / #1125): the set of backend entries that apply the
# env is DERIVED from the source and must equal the set that applies
# `auto_call` — the per-view-config precedent ADR-029 copies.
# ---------------------------------------------------------------------------


def test_every_backend_entry_that_applies_auto_call_applies_the_env():
    lib = Path(__file__).resolve().parents[3] / "crates" / "djust_live" / "src" / "lib.rs"
    body = lib.read_text()
    # One `fn <name>(` header per entry, then its body up to the next `    fn `.
    entries = {}
    for m in re.finditer(r"\n    fn (\w+)\(", body):
        start = m.end()
        nxt = body.find("\n    fn ", start)
        entries[m.group(1)] = body[start : nxt if nxt != -1 else len(body)]
    auto_call = {n for n, b in entries.items() if "set_auto_call(self.template_auto_call)" in b}
    env = {
        n
        for n, b in entries.items()
        if "self.render_env.as_ref().map(RenderEnvGuard::install)" in b
    }
    assert auto_call == env == {"render", "render_with_diff", "render_binary_diff"}, (
        f"auto_call entries {sorted(auto_call)} vs env entries {sorted(env)}"
    )


# ---------------------------------------------------------------------------
# The production actor path, end to end: a `use_actors=True` WS mount with
# `template_resolve_lazy: False` renders the configured answer.
# ---------------------------------------------------------------------------

_ALLOWED = "djust.tests.test_render_env_per_view_2741"


def _actor_view(name: str):
    from djust import LiveView

    return type(
        name,
        (LiveView,),
        {
            "use_actors": True,
            "template": f'<div dj-root dj-view="{_ALLOWED}.{name}" dj-id="0">{PROBE}</div>',
            "__module__": __name__,
        },
    )


ActorProbeView = _actor_view("ActorProbeView")


@contextlib.contextmanager
def _resolve_lazy_config(enabled: bool):
    """Flip the flag at its ONE reader, `config.template_resolve_lazy_enabled`
    — which `render_env.apply_resolve_lazy` imports at call time — rather
    than by spelling the settings key (only `config.py` may; the #2539 pin
    `test_the_config_reader_is_the_only_one` greps the package for it).
    `override_settings` cannot reach it: `LIVEVIEW_CONFIG` is read into a
    process-global at import."""
    import djust.config as config_module

    previous = config_module.template_resolve_lazy_enabled
    config_module.template_resolve_lazy_enabled = lambda: enabled
    try:
        yield
    finally:
        config_module.template_resolve_lazy_enabled = previous


@pytest.mark.django_db
@pytest.mark.asyncio
class TestTheActorMountAppliesTheConfiguredEnv:
    """The production actor path end to end (`runtime.py`'s
    `dispatch_actor_mount` -> `SessionActorHandle.mount` -> the tokio worker):
    the configured flag decides the actor's render, not the worker's default.
    Before ADR-029 the first case rendered `X` (#2751's finding, over the
    wire)."""

    async def _mount_html(self) -> str:
        pytest.importorskip("channels")
        from djust._rust import create_session_actor  # noqa: F401

        from .test_ws_mount_flip_parity_1911 import _connect_and_mount

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_ALLOWED]):
            comm, frame = await _connect_and_mount(f"{_ALLOWED}.ActorProbeView")
            try:
                return frame["html"]
            finally:
                await comm.disconnect()

    async def test_resolve_lazy_false_reaches_the_actor_render(self):
        with _resolve_lazy_config(False):
            html = await self._mount_html()
        assert "Y" in html and "X" not in html, html

    async def test_gate_off_the_default_config_renders_the_lazy_answer(self):
        with _resolve_lazy_config(True):
            html = await self._mount_html()
        assert "X" in html and "Y" not in html, html
