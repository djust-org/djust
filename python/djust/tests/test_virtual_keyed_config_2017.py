"""`virtual_keyed_ops` reaches the Rust differ from Django config (#2017, ADR-026).

Before this, `djust_vdom::diff::set_virtual_keyed_ops` had **no Python surface at
all** — its only callers were that crate's own tests. So ADR-026 iteration 3
("flag flips ON after a soak") was not a one-line default change; there was no
switch for a Django user to reach.

Why the flag is applied at startup rather than per view: the Rust side is a
process-global ``AtomicBool``, unlike ``set_loop_render_cache_enabled`` (#1967)
which is per-``RustLiveView`` state. Driving a process global from a per-view
hook would be last-view-wins.

**The default stays OFF.** The browser gate recorded on the PR found that with
the flag on, a keyed update to an off-window row still does not land in a real
page — the differ emits the right op, but the list is not windowed at patch
time after the WS mount morph, so there is no pool to apply it to (#2164).
Flipping the default is gated on that being fixed, per ROADMAP.md and #1122.
"""

from __future__ import annotations

import pytest

from djust import _rust


@pytest.fixture(autouse=True)
def _restore_flag():
    """The switch is process-global — a leak would change every later test."""
    before = _rust.virtual_keyed_ops_enabled()
    yield
    _rust.set_virtual_keyed_ops(before)


# --- the surface exists at all --------------------------------------------


def test_the_setter_and_getter_are_exposed():
    # A setter with no getter cannot be tested end to end, which is why both
    # are exported rather than just the one the config path needs.
    assert callable(_rust.set_virtual_keyed_ops)
    assert callable(_rust.virtual_keyed_ops_enabled)


def test_the_setter_actually_moves_the_flag():
    _rust.set_virtual_keyed_ops(True)
    assert _rust.virtual_keyed_ops_enabled() is True
    _rust.set_virtual_keyed_ops(False)
    assert _rust.virtual_keyed_ops_enabled() is False


# --- the config key ---------------------------------------------------------


def test_the_config_default_is_off():
    """Iteration 3 is NOT shipped. If this flips, it must be a deliberate
    change carrying the browser evidence ROADMAP.md asks for — not a drift."""
    from djust.config import LiveViewConfig

    assert LiveViewConfig._defaults["virtual_keyed_ops"] is False, (
        "the dj-virtual keyed splice ops default ON. That changes VDOM "
        "behaviour for every [dj-virtual] user and is gated per #1122 on real "
        "browser evidence — see the #2017 iteration-3 PR for why the evidence "
        "did not support it."
    )


def test_the_config_key_is_documented_where_it_is_defined():
    import inspect

    from djust import config as config_mod

    src = inspect.getsource(config_mod).splitlines()
    idx = next(n for n, ln in enumerate(src) if '"virtual_keyed_ops"' in ln)
    # Walk back over the CONTIGUOUS comment block above the key. A fixed
    # character window was the first version and it broke the moment the
    # comment grew — the property is "the key carries an explanation", not
    # "the explanation fits in 900 bytes".
    block = []
    for n in range(idx - 1, -1, -1):
        if src[n].strip().startswith("#"):
            block.append(src[n])
        elif src[n].strip() == "":
            continue
        else:
            break
    preamble = "\n".join(block)
    assert "dj-virtual" in preamble and "ADR-026" in preamble, (
        "a bare flag name in the defaults tells a reader nothing about what "
        f"turning it on does; comment block found:\n{preamble}"
    )


# --- the startup applier ----------------------------------------------------


def test_ready_applies_the_config_value_to_rust(monkeypatch):
    """Drives the REAL `DjustConfig.ready()` with a patched config value.

    The first version of this test evaluated `want if True else cfg.get(...)`,
    which sets the flag to whatever the test already decided — it would have
    passed with `ready()` deleted. Invoking the actual method is the only way
    to know the wiring runs.
    """
    import logging

    from djust.apps import DjustConfig
    from djust.config import get_config

    cfg = get_config()
    # ready() adds a DjustLogSanitizerFilter with no idempotency guard, so
    # calling it twice leaks filters onto the shared 'djust' logger (#1200's
    # case study, in the opposite direction). Snapshot and restore.
    _lg = logging.getLogger("djust")
    _filters = list(_lg.filters)

    for want in (True, False):
        _rust.set_virtual_keyed_ops(not want)  # opposite, so a no-op fails
        monkeypatch.setitem(cfg._config, "virtual_keyed_ops", want)

        app = DjustConfig.__new__(DjustConfig)  # no AppConfig __init__ needed
        try:
            app.ready()
        except Exception:  # unrelated startup work may need Django
            pass

        assert _rust.virtual_keyed_ops_enabled() is want, (
            f"ready() did not apply virtual_keyed_ops={want} to the Rust differ"
        )

    _lg.filters[:] = _filters


def test_apps_ready_wires_the_flag():
    import inspect

    from djust.apps import DjustConfig

    src = inspect.getsource(DjustConfig)
    assert "set_virtual_keyed_ops" in src, (
        "the config key is inert unless something applies it at startup"
    )
    assert 'hasattr(_rust, "set_virtual_keyed_ops")' in src, (
        "guard the call so a Rust build predating the export cannot break "
        "startup — the same defensive shape _apply_loop_render_cache_flag uses"
    )


# `test_the_applier_is_not_gated_on_pytest` lived here. It asserted
# `indent <= 16`, and the shipped applier sits at exactly 16 — so relocating
# it INSIDE the PYTEST_CURRENT_TEST guard also yields 16 and the assertion
# could not tell the two apart. Proven: that relocation left this test green;
# only `test_ready_applies_the_config_value_to_rust` caught it. A magic indent
# threshold with zero margin is a decorative pin, and the protection it
# claimed is already covered by invoking the real method.


# --- the differ honours it --------------------------------------------------


def test_the_differ_emits_keyed_ops_only_when_enabled():
    """End-to-end through the real differ: the flag is what gates the op.

    This is the half of ADR-026 that DOES work — iteration 1. Recorded here
    because the browser gate found the client half does not land the op yet,
    and a future reader needs to know which side was proven.
    """
    import json

    def html(note: str) -> str:
        rows = "".join(
            f'<div dj-key="k{i}" class="vrow">{i}<b>{note if i == 0 else ""}</b></div>'
            for i in range(6)
        )
        return f'<div dj-virtual="items" dj-virtual-item-height="32">{rows}</div>'

    before, after = html(""), html("EDITED")

    def as_text(v):
        # diff_html is PyResult<String> (crates/djust_live/src/lib.rs), so this
        # is always the str branch. Kept as a guard rather than asserting the
        # type, but the earlier comment claiming it 'may hand back a parsed
        # structure' was false — and a blind json.dumps double-encoded the
        # string, which is what made the first version of this test miss.
        return v if isinstance(v, str) else json.dumps(v)

    _rust.set_virtual_keyed_ops(False)
    off = as_text(_rust.diff_html(before, after))
    assert "VirtualUpdate" not in off, "keyed ops must not be emitted while OFF"

    _rust.set_virtual_keyed_ops(True)
    on = as_text(_rust.diff_html(before, after))
    assert "VirtualUpdate" in on, "keyed ops must be emitted while ON"
    assert '"key": "k0"' in on or '"key":"k0"' in on, (
        "the op must carry the KEY — addressing by index is the bug it exists "
        "to fix, since index 7 means the 8th item to the differ and the 8th "
        "VISIBLE item to a windowed DOM"
    )
