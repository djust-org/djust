"""#2737 — the render entries share the view's state map, they do not rebuild it.

`RustLiveViewBackend::state` is held as a `djust_core::SharedValues`, so a
render is `Context::from_shared(self.state.clone())` — one atomic increment —
rather than `Context::from_dict(self.state.clone())`, which deep-cloned every
key and every `Value` and then rebuilt them into a fresh map. That copy was
charged on EVERY render whether or not the template read any of it, and it was
the last such charge on the LiveView path after #2733 removed the
per-loop-entry one.

**Why these are STRUCTURAL and not behavioural.** Copy-on-write is invisible by
construction: a render that shares and a render that deep-copies produce
byte-identical output and leave identical state, which is exactly what makes
the change safe and exactly what makes it unobservable from the outside. The
gate-off proved the point — mutating the render entry back to `from_dict` left
every behavioural test in `djust_live` green. So the property is pinned as the
pair it actually is:

* that `from_shared` genuinely adopts the caller's map — behavioural, by
  `Arc::ptr_eq`, in `crates/djust_core/src/context.rs`
  (`from_shared_adopts_the_callers_map_rather_than_rebuilding_it`);
* that the render entries genuinely call it — mechanically, here.

Neither half is sufficient alone, and a pin that cannot go red is decorative
(#1859).
"""

from __future__ import annotations

import pathlib
import re

import pytest

CRATES = pathlib.Path(__file__).resolve().parents[2] / "crates"
LIVE = CRATES / "djust_live" / "src" / "lib.rs"
CORE = CRATES / "djust_core" / "src" / "context.rs"


def _code_lines(source: str) -> list[str]:
    """Source lines that are not Rust comments.

    Load-bearing: the field's own doc comment quotes
    ``Context::from_shared(self.state.clone())``, so a naive count of that text
    reports one more site than exists. A pin that counts its own documentation
    is the #2738 separator-pin mistake in another costume.
    """
    return [ln for ln in source.splitlines() if not ln.strip().startswith("//")]


def _fn_body(source: str, signature: str) -> str:
    """The text of one Rust fn: its signature to the first 4-space-indented ``}``.

    Splitting on ``\\n}\\n`` overruns — an inner fn ends at ``    }`` and the
    next ``\\n}\\n`` is the end of the whole ``impl`` block, which swallows every
    sibling method.
    """
    parts = source.split(signature, 1)
    assert len(parts) == 2, f"{signature!r} not found — was it renamed?"
    body: list[str] = []
    for line in parts[1].splitlines():
        if line == "    }":
            break
        body.append(line)
    return "\n".join(body)


class TestTheRenderEntriesShareTheStateMap:
    """The half the gate-off showed was otherwise unpinned."""

    def test_no_render_entry_rebuilds_the_state_map(self) -> None:
        offenders = [
            line.strip()
            for line in _code_lines(LIVE.read_text(encoding="utf-8"))
            if "Context::from_dict" in line and "self.state" in line
        ]
        assert not offenders, (
            "a render entry rebuilds the view's state map instead of sharing it — "
            f"the #2737 per-render O(entire state) copy is back: {offenders}"
        )

    def test_every_render_entry_uses_from_shared(self) -> None:
        """A floor would pass if a new entry forgot the helper while the old
        ones kept it, so this asserts the COUNT of sharing entries as well
        (#1125). Three today: `render`, the diff render, and the component
        render."""
        code = "\n".join(_code_lines(LIVE.read_text(encoding="utf-8")))
        shared = re.findall(r"Context::from_shared\(self\.state\.clone\(\)\)", code)
        assert len(shared) == 3, (
            "the number of render entries sharing the state map changed "
            f"(found {len(shared)}, expected 3). If an entry was added, it must "
            "share too; if one was removed, update this count deliberately."
        )


class TestFromDictHasOneStatementOfWhatFrameZeroIs:
    """#1646: two constructors building frame 0 independently is how they drift.

    Also structural, and for the same reason: both spellings *behave*
    identically (a hand-built frame wraps the map in an `Arc` too), so a
    behavioural test cannot tell them apart — the gate-off confirmed a
    hand-built `from_dict` leaves the `djust_core` sharing test green.
    """

    def test_from_dict_routes_through_from_shared(self) -> None:
        body = _fn_body(
            CORE.read_text(encoding="utf-8"),
            "pub fn from_dict<M: IntoIterator<Item = (String, Value)>>(dict: M) -> Self {",
        )
        assert "Self::from_shared(" in body, (
            "from_dict builds its own frame 0 instead of routing through "
            "from_shared — the two constructors can now drift (#1646)"
        )
        assert "stack: vec![ScopeFrame {" not in body, (
            "from_dict constructs a ScopeFrame directly; frame 0 must have one statement"
        )


class TestThePinsCanActuallyGoRed:
    """A pin that cannot fail is worse than absent (#1859/#2135).

    Each predicate is run against the regressed source shape, so the guard is
    demonstrated rather than assumed — the same check that caught a false
    positive in the #2738 separator pin, where the assertion matched Rust's
    closure syntax instead of the thing it meant to guard.
    """

    @pytest.mark.parametrize(
        ("regressed_line", "should_flag"),
        [
            ("let mut context = Context::from_dict((*self.state).clone());", True),
            ("let mut context = Context::from_shared(self.state.clone());", False),
            ("// Context::from_dict(self.state.clone()) — historical note", False),
        ],
    )
    def test_the_rebuild_predicate_flags_the_regressed_shape(
        self, regressed_line: str, should_flag: bool
    ) -> None:
        stripped = regressed_line.strip()
        flagged = (
            "Context::from_dict" in regressed_line
            and "self.state" in regressed_line
            and not stripped.startswith("//")
        )
        assert flagged is should_flag

    def test_the_from_dict_predicate_flags_a_hand_built_frame(self) -> None:
        hand_built = (
            "        Self {\n            stack: vec![ScopeFrame {\n                values: x,"
        )
        assert "Self::from_shared(" not in hand_built
        assert "stack: vec![ScopeFrame {" in hand_built
