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
(#1859). The predicates are module-level functions so that the pins
(`Test*` classes below) and the red-checks (`TestThePinsCanActuallyGoRed`) run
the SAME code — a red-check that re-implements the predicate inline verifies
the re-implementation, not the pin, and the two drift apart.
"""

from __future__ import annotations

import pathlib
import re

import pytest

CRATES = pathlib.Path(__file__).resolve().parents[2] / "crates"
LIVE = CRATES / "djust_live" / "src" / "lib.rs"
CORE = CRATES / "djust_core" / "src" / "context.rs"
LIVE_CRATE_SRC = CRATES / "djust_live" / "src"

FROM_DICT_SIGNATURE = "pub fn from_dict<M: IntoIterator<Item = (String, Value)>>(dict: M) -> Self {"
ISOLATION_TABLE_TEST = "fn every_copy_on_write_door_preserves_isolation_from_a_held_render()"


# --------------------------------------------------------------------------- #
# Source helpers
# --------------------------------------------------------------------------- #


def _code_lines(source: str) -> list[str]:
    """Source lines that are not Rust comments.

    Load-bearing: the field's own doc comment quotes
    ``Context::from_shared(self.state.clone())``, so a naive count of that text
    reports one more site than exists. A pin that counts its own documentation
    is the #2738 separator-pin mistake in another costume.
    """
    return [ln for ln in source.splitlines() if not ln.strip().startswith("//")]


def _code(source: str) -> str:
    return "\n".join(_code_lines(source))


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


def _enclosing_fn(code: str, index: int) -> str:
    """Name of the nearest ``fn`` declared before ``index`` in ``code``."""
    names = [m.group(1) for m in re.finditer(r"\bfn\s+(\w+)\s*[<(]", code[:index])]
    assert names, "no fn declared before the site — the file shape changed"
    return names[-1]


# --------------------------------------------------------------------------- #
# The predicates. Each is what a pin asserts AND what a red-check runs.
# --------------------------------------------------------------------------- #

_REBUILD = re.compile(r"Context::from_dict\([^;]*?self\.state", re.S)
_SHARING = re.compile(r"Context::from_shared\(self\.state\.clone\(\)\)")
_DOOR = re.compile(r"make_mut\(&mut self\.state\)")
_TABLE_LABEL = re.compile(r'^\s*"(\w+)",$', re.M)


def rebuild_offenders(live_source: str) -> list[str]:
    """Every non-comment ``Context::from_dict(… self.state …)`` in lib.rs.

    Matches across lines (``[^;]*?``), so a two-line spelling of the regressed
    shape is caught as well as the one-line one.
    """
    return [m.group(0) for m in _REBUILD.finditer(_code(live_source))]


def sharing_entries(live_source: str) -> list[str]:
    """Every non-comment ``Context::from_shared(self.state.clone())`` in lib.rs."""
    return _SHARING.findall(_code(live_source))


def from_dict_routes_through_from_shared(core_source: str) -> bool:
    """``from_dict`` delegates frame 0 to ``from_shared`` and builds no frame itself."""
    body = _fn_body(core_source, FROM_DICT_SIGNATURE)
    return "Self::from_shared(" in body and "stack: vec![ScopeFrame {" not in body


def copy_on_write_doors(live_source: str) -> set[str]:
    """The fn enclosing each ``Arc::make_mut(&mut self.state)`` in lib.rs — derived
    from the file, so the set is whatever the file says it is."""
    code = _code(live_source)
    return {_enclosing_fn(code, m.start()) for m in _DOOR.finditer(code)}


def isolation_table_labels(live_source: str) -> set[str]:
    """The door labels the Rust isolation test enumerates in its table."""
    parts = live_source.split(ISOLATION_TABLE_TEST, 1)
    assert len(parts) == 2, f"{ISOLATION_TABLE_TEST!r} not found — was it renamed?"
    body = parts[1].split("\n    }\n", 1)[0]
    return set(_TABLE_LABEL.findall(body))


def remaining_from_dict_over_own_state(crate_src: pathlib.Path) -> set[str]:
    """Files under the crate whose CODE still spells ``from_dict(self.state.clone())``."""
    return {
        str(path.relative_to(crate_src))
        for path in sorted(crate_src.rglob("*.rs"))
        if "Context::from_dict(self.state.clone())" in _code(path.read_text(encoding="utf-8"))
    }


# --------------------------------------------------------------------------- #
# The pins
# --------------------------------------------------------------------------- #


class TestTheRenderEntriesShareTheStateMap:
    """The half the gate-off showed was otherwise unpinned."""

    def test_no_render_entry_rebuilds_the_state_map(self) -> None:
        offenders = rebuild_offenders(LIVE.read_text(encoding="utf-8"))
        assert not offenders, (
            "a render entry rebuilds the view's state map instead of sharing it — "
            f"the #2737 per-render O(entire state) copy is back: {offenders}"
        )

    def test_every_render_entry_uses_from_shared(self) -> None:
        """Pins the COUNT of sharing entries, and what a count catches is
        narrower than it looks (#1125).

        An entry that FORGETS the helper is caught by the sibling
        `test_no_render_entry_rebuilds_the_state_map`, not by this — a
        forgotten entry leaves the count at 3 and this stays green. What the
        exact count pins is the OTHER direction: a fourth entry that shares
        (count 4) or a removed one (count 2) must be acknowledged here
        deliberately, so the set of render entries is a maintained list and
        not an accident. Three today: `render`, `render_with_diff`, and
        `render_binary_diff`; `render_rust` / `render_with_diff_rust`
        delegate to them.
        """
        shared = sharing_entries(LIVE.read_text(encoding="utf-8"))
        assert len(shared) == 3, (
            "the number of render entries sharing the state map changed "
            f"(found {len(shared)}, expected 3). If an entry was added, it must "
            "share too; if one was removed, update this count deliberately."
        )

    def test_the_only_from_dict_over_own_state_left_in_the_crate_is_the_component_actor(
        self,
    ) -> None:
        """`ComponentActor::render` (`actors/component.rs`) is a fourth sibling of
        the same shape — `Context::from_dict(self.state.clone())` — and the
        count-pin above does not reach it, because it is over a DIFFERENT
        field: `ComponentActor::state` is its own `HashMap<String, Value>`
        (component props), not `RustLiveViewBackend::state`, and is not a
        `SharedValues`. It is out of #2737's scope (that issue is the LiveView
        render path; component props are small), so this pins the remaining
        set as exactly that one file: converting it later, or adding another
        `from_dict` over an own-state field anywhere in the crate, must update
        this deliberately rather than pass silently.
        """
        assert remaining_from_dict_over_own_state(LIVE_CRATE_SRC) == {"actors/component.rs"}


class TestFromDictHasOneStatementOfWhatFrameZeroIs:
    """#1646: two constructors building frame 0 independently is how they drift.

    Also structural, and for the same reason: both spellings *behave*
    identically (a hand-built frame wraps the map in an `Arc` too), so a
    behavioural test cannot tell them apart — the gate-off confirmed a
    hand-built `from_dict` leaves the `djust_core` sharing test green.
    """

    def test_from_dict_routes_through_from_shared(self) -> None:
        assert from_dict_routes_through_from_shared(CORE.read_text(encoding="utf-8")), (
            "from_dict builds its own frame 0 instead of routing through "
            "from_shared — the two constructors can now drift (#1646)"
        )


class TestTheIsolationTableIsComplete:
    """The Rust isolation test says "every copy-on-write door"; this is what
    makes "every" true rather than asserted.

    The set of doors is DERIVED from lib.rs (the fn enclosing each
    `Arc::make_mut(&mut self.state)`), and the Rust table's labels must equal
    it exactly — a new `make_mut` site without a table row fails here, as does
    a table row for a site that no longer exists.
    """

    def test_the_isolation_table_names_every_copy_on_write_door(self) -> None:
        source = LIVE.read_text(encoding="utf-8")
        doors = copy_on_write_doors(source)
        labels = isolation_table_labels(source)
        assert doors, "no `Arc::make_mut(&mut self.state)` sites found — was the field changed?"
        assert labels == doors, (
            f"the isolation table ({sorted(labels)}) and the copy-on-write doors "
            f"in lib.rs ({sorted(doors)}) disagree — add a row for a new door, or "
            "remove the row for a door that is gone"
        )


# --------------------------------------------------------------------------- #
# The red-checks: the REAL predicates against a regressed source
# --------------------------------------------------------------------------- #


class TestThePinsCanActuallyGoRed:
    """A pin that cannot fail is worse than absent (#1859/#2135).

    Each REAL predicate above is run against the real source with one
    regression substituted in, so the guard is demonstrated rather than
    assumed — the same check that caught a false positive in the #2738
    separator pin, where the assertion matched Rust's closure syntax instead
    of the thing it meant to guard. Because these call the same functions the
    pins call, a change that blinds a pin blinds its red-check too.
    """

    @pytest.fixture
    def live(self) -> str:
        return LIVE.read_text(encoding="utf-8")

    @pytest.fixture
    def core(self) -> str:
        return CORE.read_text(encoding="utf-8")

    @staticmethod
    def _regress_one_render_entry(live: str, replacement: str) -> str:
        code_line = next(
            ln for ln in _code_lines(live) if "Context::from_shared(self.state.clone())" in ln
        )
        regressed = live.replace(
            code_line, code_line.replace("Context::from_shared(self.state.clone())", replacement), 1
        )
        assert regressed != live, "NO-OP MUTATION"
        return regressed

    def test_the_rebuild_predicate_flags_a_regressed_render_entry(self, live: str) -> None:
        regressed = self._regress_one_render_entry(
            live, "Context::from_dict((*self.state).clone())"
        )
        assert len(rebuild_offenders(regressed)) == 1
        assert len(sharing_entries(regressed)) == 2

    def test_the_rebuild_predicate_flags_a_two_line_spelling(self, live: str) -> None:
        regressed = self._regress_one_render_entry(
            live, "Context::from_dict(\n                (*self.state).clone(),\n            )"
        )
        assert len(rebuild_offenders(regressed)) == 1

    def test_the_rebuild_predicate_ignores_a_comment(self, live: str) -> None:
        commented = live + "\n// Context::from_dict(self.state.clone()) — historical note\n"
        assert rebuild_offenders(commented) == []
        assert len(sharing_entries(commented)) == 3

    def test_the_from_dict_predicate_flags_a_hand_built_frame(self, core: str) -> None:
        body = _fn_body(core, FROM_DICT_SIGNATURE)
        assert "Self::from_shared(std::sync::Arc::new(map))" in body, "MUTATION TEXT NOT FOUND"
        hand_built = body.replace(
            "Self::from_shared(std::sync::Arc::new(map))",
            "Self {\n            stack: vec![ScopeFrame {\n"
            "                values: std::sync::Arc::new(map),\n"
            "                ..ScopeFrame::default()\n            }],\n"
            "            node_identity: None,\n        }",
        )
        regressed = core.replace(body, hand_built, 1)
        assert regressed != core, "NO-OP MUTATION"
        assert from_dict_routes_through_from_shared(core)
        assert not from_dict_routes_through_from_shared(regressed)

    def test_the_door_derivation_sees_a_door_that_loses_its_row(self, live: str) -> None:
        row = '                "clear_live_handles",\n'
        assert row in live, "MUTATION TEXT NOT FOUND"
        regressed = live.replace(row, "", 1)
        assert isolation_table_labels(regressed) == copy_on_write_doors(live) - {
            "clear_live_handles"
        }
        assert isolation_table_labels(regressed) != copy_on_write_doors(regressed)

    def test_the_door_derivation_sees_a_new_door_without_a_row(self, live: str) -> None:
        anchor = "    fn get_state(&self, py: Python) -> PyResult<Py<PyAny>> {\n"
        assert anchor in live, "MUTATION TEXT NOT FOUND"
        regressed = live.replace(
            anchor,
            "    fn nudge(&mut self) {\n"
            '        std::sync::Arc::make_mut(&mut self.state).remove("x");\n'
            "    }\n\n" + anchor,
            1,
        )
        assert copy_on_write_doors(regressed) == copy_on_write_doors(live) | {"nudge"}
        assert isolation_table_labels(regressed) != copy_on_write_doors(regressed)

    def test_the_crate_sweep_sees_a_converted_component_actor(self, tmp_path: pathlib.Path) -> None:
        for path in LIVE_CRATE_SRC.rglob("*.rs"):
            target = tmp_path / path.relative_to(LIVE_CRATE_SRC)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text(encoding="utf-8")
            if path.name == "component.rs":
                assert "Context::from_dict(self.state.clone())" in text, "MUTATION TEXT NOT FOUND"
                text = text.replace(
                    "Context::from_dict(self.state.clone())",
                    "Context::from_shared(self.state.clone())",
                )
            target.write_text(text, encoding="utf-8")
        assert remaining_from_dict_over_own_state(tmp_path) == set()
