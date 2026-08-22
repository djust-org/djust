"""`virtual_keyed_ops` reaches the Rust differ from Django config (#2017, ADR-026).

Before this, `djust_vdom::diff::set_virtual_keyed_ops` had **no Python surface at
all** — its only callers were that crate's own tests. So ADR-026 iteration 3
("flag flips ON after a soak") was not a one-line default change; there was no
switch for a Django user to reach.

Why the flag is applied at startup rather than per view: the Rust side is a
process-global ``AtomicBool``, unlike ``set_loop_render_cache_enabled`` (#1967)
which is per-``RustLiveView`` state. Driving a process global from a per-view
hook would be last-view-wins.

**The default is ON since 1.1.0.** It was OFF for several releases, and not for
the reason this docstring gave for two
revisions: it claimed the browser gate had proven the list "is not windowed at
patch time after the WS mount morph, so there is no pool to apply it to". That
was a wrong diagnosis of #2164 — twice. The differ and the client applier were
both correct; the config never reached the Rust flag, so the browser was
running the feature OFF while the settings said ON. With that fixed, an insert
at server position 5 lands at pool index 5.

Both OFF-rationales above are superseded. The later one said that

"browser evidence covers VirtualInsert only" line was true when written; the
2026-08-11 gate run extended it to the whole op set — insert (lands at pool
index 5), remove (drops the right key, no duplicates), reverse (exact), and a
content edit (lands on its own row only), each reporting "Patches applied
successfully" with no recovery round-trip.

What kept it OFF was #2185 — `[dj-virtual]` initialisation intermittently lost
on page load — together with #2194, post-mount reinit being rAF-only so a
hidden tab never recovered. Both shipped (PRs #2195, #2196), and only then did
the browser gate have a working control arm: before that, every A/B compared
OFF against OFF, which is how this feature accumulated four withdrawn
diagnoses. With them fixed the gate passed and the default flipped ON in 1.1.0
(#1122 governed the flip itself).
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


def test_the_config_default_is_on():
    """Iteration 3 shipped in 1.1.0. This pin is now the inverse of what it was.

    It spent several releases asserting ``is False`` with the message "the
    evidence did not support it". The evidence now does, and it is recorded
    here so a future reader can tell a deliberate flip from a drift — which is
    the only reason this pin exists.

    Measured on a healthy, initialised ``[dj-virtual]`` list (which required
    #2185 and #2194 to be fixed first — before those, the list was
    intermittently never initialised, so BOTH arms of every earlier A/B were
    really the OFF arm). Same start state, flag the only variable:

    ==========================  =======================  ==================
    case                        OFF                      ON
    ==========================  =======================  ==================
    insert at position 5        pool index 60 (tail)     pool index 5
    remove at 3                 size unchanged, k3 kept  k3 gone, no dupes
    reverse                     not reordered            exact reversal
    edit a scrolled row         correct                  correct
    ==========================  =======================  ==================

    OFF fails every mid-list structural mutation; ON fixes all three and
    regresses none. If this ever flips back, that too must carry evidence.
    """
    from djust.config import LiveViewConfig

    assert LiveViewConfig._defaults["virtual_keyed_ops"] is True, (
        "the dj-virtual keyed splice ops default OFF again. ON is the shipped "
        "behaviour since 1.1.0 (ADR-026 iteration 3) — turning it back off "
        "reintroduces tail-landing inserts, dropped removals and ignored "
        "reorders on every windowed list. Revert deliberately, with evidence."
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
    """Drives the REAL `DjustConfig.ready()` and checks the flag actually moves.

    An earlier version evaluated `want if True else cfg.get(...)`, setting the
    flag to whatever the test had already decided — it would have passed with
    `ready()` deleted. Invoking the real method is the only way to know the
    wiring runs.

    Patches the SINGLETON, which is what `ready()` reads. That was the wrong
    thing to patch while `ready()` bypassed it (#2164) — the test passed on a
    path no deployment had. It is the right thing to patch now that `ready()`
    recovers the singleton first, and the singleton's own correctness under a
    hostile import order is pinned separately, in
    `test_config_settings_recovery_2166.py`. Neither test is sufficient alone:
    this one proves the value is forwarded, that one proves the value is real.
    """
    import logging

    from djust.config import config

    from djust.apps import DjustConfig

    _lg = logging.getLogger("djust")
    _filters = list(_lg.filters)
    try:
        for want in (True, False):
            _rust.set_virtual_keyed_ops(not want)  # opposite, so a no-op fails
            monkeypatch.setitem(config._config, "virtual_keyed_ops", want)
            app = DjustConfig.__new__(DjustConfig)
            app.ready()
            assert _rust.virtual_keyed_ops_enabled() is want, (
                f"ready() did not forward virtual_keyed_ops={want} to the differ"
            )
    finally:
        # In a `finally` so a mid-loop assertion failure cannot leak a filter
        # into every later test in the session.
        _lg.filters[:] = _filters


# `test_the_applier_does_not_read_the_stale_config_singleton` lived here. It
# was a source-grep over a fixed 1200-character window, and the Stage 11 review
# measured it as a decorative pin (#1859): it stayed GREEN against three
# reintroductions of the defect in different shapes (`get_config()`, a module-
# attribute read, and one where only a COMMENT carried the tokens it searched
# for — the `code_only` hole, 7th recurrence), while going RED against correct
# code whose comment merely mentioned the banned string. Every case it caught
# was already caught by `test_ready_applies_the_config_value_to_rust`, so it was
# a strict subset with false-positive hazards.
#
# The property it was reaching for is now tested behaviourally, against the real
# import order, in `test_config_settings_recovery_2166.py`.


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
