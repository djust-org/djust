"""`[dj-virtual]` keyed VDOM ops are unconditional (#2017, ADR-026).

2.0 removed the ``LIVEVIEW_CONFIG['virtual_keyed_ops']`` kill switch and then
the Rust switch behind it (``set_virtual_keyed_ops`` /
``virtual_keyed_ops_enabled``): the differ emits key-addressed ops for every
``[dj-virtual]`` parent. The history below explains why ON is the right, and
now only, behaviour.

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

import json

from djust import _rust


def test_the_config_key_is_gone():
    """Setting the key now fails startup (``djust._removed_settings``)."""
    from djust.config import LiveViewConfig

    assert "virtual_keyed_ops" not in LiveViewConfig._defaults


def test_the_rust_switch_is_gone():
    assert not hasattr(_rust, "set_virtual_keyed_ops")
    assert not hasattr(_rust, "virtual_keyed_ops_enabled")


def test_the_differ_emits_keyed_ops_for_a_virtual_parent():
    """End-to-end through the real differ, with nothing switched on first."""

    def html(note: str) -> str:
        rows = "".join(
            f'<div dj-key="k{i}" class="vrow">{i}<b>{note if i == 0 else ""}</b></div>'
            for i in range(6)
        )
        return f'<div dj-virtual="items" dj-virtual-item-height="32">{rows}</div>'

    patches = _rust.diff_html(html(""), html("EDITED"))
    # diff_html is PyResult<String> (crates/djust_live/src/lib.rs).
    on = patches if isinstance(patches, str) else json.dumps(patches)
    assert "VirtualUpdate" in on, "keyed ops must be emitted for [dj-virtual]"
    assert '"key": "k0"' in on or '"key":"k0"' in on, (
        "the op must carry the KEY — addressing by index is the bug it exists "
        "to fix, since index 7 means the 8th item to the differ and the 8th "
        "VISIBLE item to a windowed DOM"
    )
