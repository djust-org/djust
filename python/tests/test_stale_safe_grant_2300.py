"""A safe-key grant must not outlive the render that earned it (#2300).

The fourth live XSS of this drain, and the only one needing no filter chain at
all — a bare ``{{ p }}``.

``RustLiveView::mark_safe_keys`` used ``extend``, and nothing ever cleared the
set. A key marked safe once stayed safe for the lifetime of the view, which
spans **every event on a WebSocket connection**. So a view that rendered
trusted markup into ``p`` and later rendered an attacker-controlled ``p``
emitted it live:

    sync(view, mark_safe("<b>safe</b>"));  view.render()  ->  '<b>safe</b>'
    sync(view, "<img src=x onerror=alert(1)>")
    view.render()                                         ->  LIVE

The fix is ONE mechanism: ``update_state`` revokes a key's grant when it
replaces that key's value, so a grant lives exactly as long as the value it was
granted for — regardless of who is driving the API.

It did not start there. The first attempt was caller discipline: make
``mark_safe_keys`` replace rather than extend, and have the bridge call it on
every render so the empty case clears. That fixes the production path, and
#2287's forward pin — which drives the Rust API directly and never makes the
second call — stayed red against it. Gate-off then showed the replace half had
become redundant once revocation existed, and mildly wrong besides: a render
that updates only ``p`` sends only ``p``'s keys, so replacing would drop a
still-valid grant on an untouched ``q``. Both halves were removed in favour of
the single structural rule (two mechanisms covering the same half is one fix
plus one decoration, and no test can separate them — CLAUDE.md v1.1.1-2).
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

HOSTILE = "<img src=x onerror=alert(1)>"


def _sync(view: object, value: object, key: str = "p") -> None:
    """The production sequence from `_sync_state_to_rust`.

    Deliberately mirrors the bridge rather than calling `mark_safe_keys`
    directly: the bug lived in the *interaction* between what the bridge sends
    and what Rust does with it, so a test that skips the bridge's shape cannot
    see it (reproduction fidelity — a convenient proxy is not the real path).
    """
    normalized = normalize_django_value({key: value})
    keys: list[str] = []
    for k, v in normalized.items():
        keys.extend(_collect_safe_keys(v, k))
    view.update_state(normalized)
    view.mark_safe_keys(keys)


def test_a_grant_does_not_survive_into_the_next_render() -> None:
    """The vulnerability."""
    view = _rust.RustLiveView("{{ p }}")
    _sync(view, mark_safe("<b>safe</b>"))
    assert view.render() == "<b>safe</b>", "the legitimate grant must work"

    _sync(view, HOSTILE)
    out = view.render()
    assert "<img" not in out, f"a stale safe grant made hostile input live: {out!r}"
    assert out == "&lt;img src=x onerror=alert(1)&gt;"


def test_it_survives_many_renders_and_an_intervening_gap() -> None:
    """One clear is not enough — the grant must be re-earned every render."""
    view = _rust.RustLiveView("{{ p }}")
    for _ in range(3):
        _sync(view, mark_safe("<b>ok</b>"))
        assert view.render() == "<b>ok</b>"
        _sync(view, HOSTILE)
        assert "<img" not in view.render()

    # safe -> plain -> safe: the grant comes back when it is re-sent.
    _sync(view, mark_safe("<i>y</i>"))
    assert view.render() == "<i>y</i>"


def test_the_fix_does_not_over_escape_a_still_safe_value() -> None:
    """The opposite failure. Replacing the set must not drop a live grant."""
    view = _rust.RustLiveView("{{ p }}")
    _sync(view, mark_safe("<b>one</b>"))
    assert view.render() == "<b>one</b>"
    _sync(view, mark_safe("<i>two</i>"))
    assert view.render() == "<i>two</i>", "a re-sent grant must still be honoured"


def test_a_second_key_does_not_inherit_the_first_keys_grant() -> None:
    """Per-key, not per-view — the set is replaced wholesale."""
    view = _rust.RustLiveView("{{ p }}{{ q }}")
    normalized = normalize_django_value({"p": mark_safe("<b>p</b>"), "q": HOSTILE})
    keys: list[str] = []
    for k, v in normalized.items():
        keys.extend(_collect_safe_keys(v, k))
    view.update_state(normalized)
    view.mark_safe_keys(keys)
    out = view.render()
    assert "<b>p</b>" in out, "p's own grant must hold"
    assert "<img" not in out, f"q inherited p's grant: {out!r}"


def test_replacing_a_value_revokes_its_grant_whoever_is_driving() -> None:
    """The structural half, and the stronger of the two.

    Making the bridge call `mark_safe_keys` unconditionally fixes the
    production path but leaves the guarantee resting on caller discipline —
    which is exactly what failed here, since the bug WAS a call site that did
    not do it. `update_state` revoking the grant for any key whose value it
    replaces holds regardless of who drives the API.

    #2287's forward pin is what surfaced the difference: it drives the Rust API
    directly and never makes the second call, so it stayed red against the
    caller-discipline half alone.
    """
    view = _rust.RustLiveView("{{ p }}")
    view.update_state(normalize_django_value({"p": mark_safe("<b>x</b>")}))
    view.mark_safe_keys(["p"])
    assert view.render() == "<b>x</b>"

    # No mark_safe_keys call at all this time — the grant must still be gone.
    view.update_state(normalize_django_value({"p": HOSTILE}))
    out = view.render()
    assert "<img" not in out, f"the grant outlived the value it was granted for: {out!r}"


def test_revocation_is_scoped_to_the_keys_actually_updated() -> None:
    """`update_state` is a partial merge, so revocation must be too.

    Clearing wholesale would drop grants for keys the call never touched,
    turning a security fix into an over-escaping bug for every untouched
    variable.
    """
    view = _rust.RustLiveView("{{ p }}|{{ q }}")
    view.update_state(
        normalize_django_value({"p": mark_safe("<b>p</b>"), "q": mark_safe("<i>q</i>")})
    )
    view.mark_safe_keys(["p", "q"])
    assert view.render() == "<b>p</b>|<i>q</i>"

    # Touch only p. q's grant must survive; p's must not.
    view.update_state(normalize_django_value({"p": HOSTILE}))
    out = view.render()
    assert "<img" not in out, f"p kept its grant: {out!r}"
    assert "<i>q</i>" in out, f"q lost a grant it should have kept: {out!r}"


def test_revocation_reaches_item_level_grants() -> None:
    """`p.0`-style item grants are descendants of `p` and go with it (#2287)."""
    view = _rust.RustLiveView('{{ p|join:", " }}')
    view.update_state(normalize_django_value({"p": [mark_safe("<b>x</b>")]}))
    view.mark_safe_keys(["p.0"])
    assert view.render() == "<b>x</b>"

    view.update_state(normalize_django_value({"p": [HOSTILE]}))
    out = view.render()
    assert "<img" not in out, f"an item grant outlived its list: {out!r}"
