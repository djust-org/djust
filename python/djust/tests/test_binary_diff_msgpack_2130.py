"""``render_binary_diff`` output must actually be readable (#2130).

The empirical canary this fix needs (#1459): the whole claim is "the bytes can
be deserialized", so the test deserializes them — from Python, through the real
PyO3 producer, with the same msgpack library a consumer would use. A Rust-side
round-trip proves serde agrees with itself; it does not prove the shipped bytes
are readable by anything else.

The bug: ``Patch`` is an internally tagged enum, and ``rmp_serde::to_vec``
encodes those as a POSITIONAL array. Measured, the old payload for one appended
row decoded to::

    [['InsertChild', [0], '1', 2, ['li', {...}, [...], None, 'c', '8']]]

So it is *valid msgpack* — it simply is not interpretable. Two consequences,
and the second is the real corruption:

* every field is an anonymous slot, so a consumer must hard-code the field
  order of every variant to read anything;
* ``skip_serializing_if`` on the interior ``d`` DROPS its slot when ``None``,
  so the same logical field sits at a different index depending on whether an
  earlier optional happened to be present. Rust's own deserializer rejects it
  outright (``invalid length 2, expected 3 elements``); a hand-written reader
  would silently read ``index`` where it expected ``d``.

It affected EVERY variant, and went unnoticed because nothing deserializes this
yet (``LiveViewConsumer.use_binary`` is hardcoded ``False``, "MessagePack
support TODO").

Fixed at the producer with ``to_vec_named``, which emits a map — an omitted
optional is simply an absent key. No change to the JSON path, which is what the
live wire actually uses, and no change to any deployed client.
"""

from __future__ import annotations

import msgpack
import pytest

pytest.importorskip("djust._rust")
from djust._rust import RustLiveView  # noqa: E402


SRC = '<div dj-root><ul>{% for x in xs %}<li dj-key="{{ x }}">{{ x }}</li>{% endfor %}</ul></div>'


def _binary_patches(states):
    """Drive the real producer over a sequence of states; return each payload."""
    lv = RustLiveView(SRC)
    out = []
    for i, state in enumerate(states):
        if i > 0:
            lv.set_changed_keys(["xs"])
        lv.update_state({"xs": state})
        _html, patches, _ver = lv.render_binary_diff()
        out.append(bytes(patches) if patches is not None else None)
    return out


def test_binary_diff_payload_deserializes_into_readable_patches():
    # The headline — stated as INTERPRETABILITY, not parseability. An earlier
    # draft of this test asserted only that unpackb() succeeds, and it passed
    # against the broken encoding: a positional array is perfectly valid
    # msgpack. Gate-off caught that; the assertion now names what the fix
    # actually buys.
    payloads = _binary_patches([["a", "b"], ["a", "b", "c"]])

    assert payloads[0] is None, "the first render has no baseline, so no patches"
    decoded = msgpack.unpackb(payloads[1], raw=False)

    assert isinstance(decoded, list), f"expected a list of patches, got {type(decoded)}"
    assert decoded, "an appended item must produce at least one patch"
    assert all(isinstance(p, dict) for p in decoded), (
        "each patch must decode to a NAMED map. A list here means the "
        f"positional encoding is back and the fields are anonymous: {decoded!r}"
    )


def test_every_patch_carries_its_type_tag_as_a_readable_key():
    # The positional encoding is exactly what made this impossible: a reader
    # got an array of anonymous slots. A named map means a consumer can
    # dispatch on the tag without knowing the field order of every variant.
    payloads = _binary_patches([["a", "b", "c"], ["c", "a", "b"], ["c", "a"]])

    for payload in payloads[1:]:
        decoded = msgpack.unpackb(payload, raw=False)
        for patch in decoded:
            assert isinstance(patch, dict), (
                f"each patch must be a map, not a positional array; got {patch!r}"
            )
            assert "type" in patch, f"every patch must carry its variant tag; got {patch!r}"


def test_omitted_optionals_are_absent_keys_not_dropped_slots():
    # The mechanism of the bug, stated as a property — and the assertions have
    # to demonstrate it, not just restate the name. An earlier version asserted
    # only that `type` was truthy and an addressing field was present, which is
    # a weaker duplicate of the test above and never showed the actual
    # property: that an OMITTED optional is an absent key while its siblings
    # stay addressable BY NAME. Under the positional encoding the omission
    # removed an element and shifted everything after it.
    payloads = _binary_patches([["a"], ["a", "b"]])
    decoded = msgpack.unpackb(payloads[1], raw=False)

    omitted = [p for p in decoded if "ref_d" not in p or "d" not in p]
    assert omitted, (
        f"expected at least one patch with an omitted optional; got {[sorted(p) for p in decoded]}"
    )
    for patch in omitted:
        # The siblings of the omitted key are still there, under their own
        # names — which is exactly what a dropped positional slot destroys.
        assert patch.get("type"), patch
        assert "path" in patch, f"`path` must survive the omission; got {sorted(patch)}"
        if patch["type"] == "InsertChild":
            assert "index" in patch and "node" in patch, (
                "`index`/`node` must stay addressable by name when an earlier "
                f"optional is omitted; got {sorted(patch)}"
            )


def test_an_empty_diff_still_produces_readable_bytes():
    # CHARACTERIZATION, not a proof of this change: an empty list encodes
    # identically either way, so this stays green with the fix gated off — and
    # that is correct. It is here because the empty-Vec branch is a SEPARATE
    # call site in the producer, and a future edit could touch one and not the
    # other (#1646).
    lv = RustLiveView(SRC)
    lv.update_state({"xs": ["a"]})
    lv.render_binary_diff()
    lv.set_changed_keys(["xs"])
    lv.update_state({"xs": ["a"]})  # no change
    _html, patches, _ver = lv.render_binary_diff()

    assert patches is not None
    decoded = msgpack.unpackb(bytes(patches), raw=False)
    assert decoded == [], f"an unchanged render must yield an empty patch list, got {decoded!r}"
