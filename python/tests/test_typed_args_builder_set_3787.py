"""The handler-context builders are a closed SET, and each one scrubs the reserved key (#3787).

#3773 made ``__djust_typed_args__`` the engine's: an application context key of that name is
removed before any handler is called. The removal lives at three sites, one per way the engine
turns the view's context into what a Python handler receives:

* ``bridged_context_map``  (renderer.rs)  - the flat map of the live frames;
* ``build_py_context``     (registry.rs)  - the flat map as a ``dict``, plus the raw-Python
  sidecar laid over it;
* ``build_py_context_memo`` (registry.rs) - the live frames as a ``dict``, plus the sidecar.

A fourth builder would hand a handler the application's value in the engine's place, and nothing
in the behavioural suite would notice until somebody wrote a probe for the new path
(`test_typed_args_reserved_key_3773.py` only covers the builders that exist). This pin closes
that: it DERIVES the builders from the sink and fails when one has no scrub.

How the set is derived (nothing is a typed-in count)
----------------------------------------------------
Every non-test Rust function under ``crates/*/src`` is scanned. A function is a builder when it
performs one of the three materialisations a handler context is made of, and each materialisation
carries its own scrub obligation:

=============================================  ==========================================
the function ...                               must ...
=============================================  ==========================================
calls ``.to_hashmap()`` (flat map of frames)   ``.remove(TYPED_ARGS_KEY)``
calls ``.bridged_py_dict(`` (frames as dict)   ``del_item(... TYPED_ARGS_KEY)``
takes ``raw_py_objects`` and returns a         skip the reserved key in the sidecar loop
``PyDict`` (the sidecar overlay)
=============================================  ==========================================

A builder that satisfies only one of its two obligations (``build_py_context_memo`` has two) is
reported by obligation, so a gate-off of one scrub points at one assertion.

The derived NAMES are then compared with the set recorded below, in both directions: a new
builder that scrubs still fails until the author has looked at this list and added it, and a
builder that disappears fails until it is removed. Counts are never asserted; names are.

Each mutation test below runs the same audit over an edited copy of the real sources, so the pin
is shown to go red for every removal and for a fourth builder with and without a scrub (#1468).

Refs #3787, #3773, #1125, #1859.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

pytestmark = pytest.mark.pr_tier

REPO = Path(__file__).resolve().parents[2]
CRATES = REPO / "crates"

#: The builders as of #3773, by ``(file relative to crates/, function)``. This is a deliberate
#: record, not a count: adding a builder means adding its row here AFTER checking it scrubs.
EXPECTED_BUILDERS: Set[Tuple[str, str]] = {
    ("djust_templates/src/renderer.rs", "bridged_context_map"),
    ("djust_templates/src/registry.rs", "build_py_context"),
    ("djust_templates/src/registry.rs", "build_py_context_memo"),
}

KEY = "TYPED_ARGS_KEY"

FN_RE = re.compile(
    r"^(?P<indent> *)(?:pub(?:\([a-z:]+\))? +)?(?:const +)?(?:unsafe +)?fn +(?P<name>\w+)"
)


# ---------------------------------------------------------------------------
# A small Rust reader. rustfmt is enforced in this repo, so an item that opens at an indent closes
# at the first later line that is exactly that indent plus ``}``.
# ---------------------------------------------------------------------------


def _code_lines(text: str) -> List[str]:
    """Source lines with whole-line comments blanked (line numbers are preserved)."""
    return ["" if ln.lstrip().startswith("//") else ln for ln in text.splitlines()]


def _blank_cfg_test_items(lines: List[str]) -> List[str]:
    """Blank every item introduced by ``#[cfg(test)]``: a test fn may call anything."""
    out = list(lines)
    i = 0
    while i < len(out):
        stripped = out[i].lstrip()
        if stripped.startswith("#[cfg(test)]"):
            indent = len(out[i]) - len(stripped)
            j = i
            out[j] = ""
            j += 1
            # The item: ends at ``;`` on its own opening line, or at the closing brace.
            while j < len(out) and (out[j].strip() == "" or out[j].lstrip().startswith("#[")):
                out[j] = ""
                j += 1
            opened_here = j
            if j < len(out) and out[j].rstrip().endswith(";"):
                out[j] = ""
                i = j + 1
                continue
            while j < len(out):
                closing = out[j].rstrip() == " " * indent + "}"
                out[j] = ""
                if closing and j > opened_here:
                    break
                j += 1
            i = j + 1
        else:
            i += 1
    return out


def _functions(text: str) -> Dict[str, Tuple[str, str]]:
    """``{name: (signature, body)}`` for every non-test fn; a duplicate name keeps the last."""
    lines = _blank_cfg_test_items(_code_lines(text))
    found: Dict[str, Tuple[str, str]] = {}
    for idx, line in enumerate(lines):
        match = FN_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        end = idx
        if not (line.rstrip().endswith("}") and "{" in line):
            end = idx + 1
            while end < len(lines) and lines[end].rstrip() != " " * indent + "}":
                end += 1
        chunk = lines[idx : end + 1]
        sig_end = next(
            (n for n, ln in enumerate(chunk) if ln.rstrip().endswith("{")), len(chunk) - 1
        )
        found[match.group("name")] = (
            "\n".join(chunk[: sig_end + 1]),
            "\n".join(chunk[sig_end + 1 :]),
        )
    return found


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------


def _obligations(sig: str, body: str) -> Dict[str, bool]:
    """``{obligation: satisfied}`` for one function; empty when it is not a builder."""
    out: Dict[str, bool] = {}
    if ".to_hashmap()" in body:
        out["flat map: .remove(TYPED_ARGS_KEY)"] = bool(
            re.search(rf"\.remove\(\s*(?:[\w:]+::)?{KEY}\s*\)", body)
        )
    if ".bridged_py_dict(" in body:
        out["live frames: del_item(TYPED_ARGS_KEY)"] = bool(
            re.search(rf"del_item\(\s*(?:[\w:]+::)?{KEY}\s*\)", body)
        )
    if "raw_py_objects" in sig and "PyDict" in sig.split("->", 1)[-1]:
        # The sidecar loop: ``for (key, obj) in raw { if key == KEY { continue; } ... }``.
        loop = re.search(r"for \(key, obj\) in raw \{(?P<loop>.*?)\n {8}\}", body, re.S)
        out["sidecar: skip TYPED_ARGS_KEY"] = bool(
            loop and re.search(rf"key\s*==\s*(?:[\w:]+::)?{KEY}", loop.group("loop"))
        )
    return out


def audit(sources: Dict[str, str]) -> Tuple[Set[Tuple[str, str]], List[str]]:
    """``(derived builder set, problems)`` over ``{path relative to crates/: source}``."""
    builders: Set[Tuple[str, str]] = set()
    problems: List[str] = []
    for rel, text in sorted(sources.items()):
        for name, (sig, body) in _functions(text).items():
            todo = _obligations(sig, body)
            if not todo:
                continue
            builders.add((rel, name))
            problems += [
                f"{rel}::{name} does not satisfy [{ob}]" for ob, ok in todo.items() if not ok
            ]
    return builders, problems


def _real_sources() -> Dict[str, str]:
    return {
        str(path.relative_to(CRATES)): path.read_text(encoding="utf-8")
        for path in sorted(CRATES.glob("*/src/**/*.rs"))
    }


#: A fourth handler-context builder of each shape, to be appended to a real source.
BUILDER_WITHOUT_SCRUB = {
    "to_hashmap": (
        "fn fourth_flat_builder(context: &Context) -> HashMap<String, Value> {\n"
        "    context.to_hashmap()\n}\n"
    ),
    "bridged_py_dict": (
        "fn fourth_live_builder<'py>(py: Python<'py>, context: &Context) "
        "-> Result<Bound<'py, PyDict>, String> {\n"
        "    context.bridged_py_dict(py)\n}\n"
    ),
    "sidecar": (
        "fn fourth_sidecar_builder<'py>(\n"
        "    py: Python<'py>,\n"
        "    raw_py_objects: Option<&HashMap<String, Py<PyAny>>>,\n"
        ") -> Result<Bound<'py, PyDict>, String> {\n"
        "    let d = PyDict::new(py);\n"
        "    if let Some(raw) = raw_py_objects {\n"
        "        for (key, obj) in raw {\n"
        "            d.set_item(key, obj.bind(py)).unwrap();\n"
        "        }\n"
        "    }\n"
        "    Ok(d)\n}\n"
    ),
}


def _mutated(rel: str, old: str, new: str) -> Dict[str, str]:
    """The real sources with ``old`` replaced by ``new`` once in ``rel``; both ends asserted."""
    sources = _real_sources()
    assert old in sources[rel], f"MUTATION TEXT NOT FOUND in {rel}: {old!r}"
    sources[rel] = sources[rel].replace(old, new, 1)
    assert sources[rel] != _real_sources()[rel], "NO-OP MUTATION"
    return sources


RENDERER = "djust_templates/src/renderer.rs"
REGISTRY = "djust_templates/src/registry.rs"


class TestTheRealSources:
    def test_the_derived_builder_set_is_the_recorded_one(self) -> None:
        builders, _ = audit(_real_sources())
        assert builders == EXPECTED_BUILDERS, (
            "the set of handler-context builders changed. A NEW builder must scrub "
            f"{KEY} and be added to EXPECTED_BUILDERS; a removed one must leave it.\n"
            f"added: {sorted(builders - EXPECTED_BUILDERS)}\n"
            f"removed: {sorted(EXPECTED_BUILDERS - builders)}"
        )

    def test_every_builder_scrubs_the_key_at_each_of_its_sinks(self) -> None:
        _, problems = audit(_real_sources())
        assert problems == [], problems

    def test_the_derivation_found_every_obligation_it_claims(self) -> None:
        """Looking is not finding: the memo builder has TWO sinks, the others one each. If the
        reader silently lost an obligation, the test above would pass over an unchecked sink."""
        per_builder = {
            (rel, name): set(_obligations(sig, body))
            for rel, text in _real_sources().items()
            for name, (sig, body) in _functions(text).items()
            if _obligations(sig, body)
        }
        assert per_builder == {
            (RENDERER, "bridged_context_map"): {"flat map: .remove(TYPED_ARGS_KEY)"},
            (REGISTRY, "build_py_context"): {"sidecar: skip TYPED_ARGS_KEY"},
            (REGISTRY, "build_py_context_memo"): {
                "live frames: del_item(TYPED_ARGS_KEY)",
                "sidecar: skip TYPED_ARGS_KEY",
            },
        }, per_builder

    def test_test_code_is_not_scanned(self) -> None:
        """``context.rs`` calls ``to_hashmap()`` from a ``#[cfg(test)]`` test; it must not be a
        builder, or the real set would never match."""
        assert "djust_core/src/context.rs" in _real_sources()
        builders, _ = audit(_real_sources())
        assert not any(rel.endswith("context.rs") for rel, _ in builders)


class TestThePinGoesRedWhenAScrubIsRemoved:
    """Each removal is a separate test, so each points at one obligation (#1468)."""

    def test_flat_map_scrub_removed(self) -> None:
        _, problems = audit(_mutated(RENDERER, "map.remove(TYPED_ARGS_KEY);", ""))
        assert problems == [
            f"{RENDERER}::bridged_context_map does not satisfy [flat map: .remove(TYPED_ARGS_KEY)]"
        ]

    def test_sidecar_scrub_removed_from_build_py_context(self) -> None:
        text = _real_sources()[REGISTRY]
        head, tail = text.split("pub(crate) fn build_py_context_memo", 1)
        guard = "if key == crate::renderer::TYPED_ARGS_KEY {"
        assert guard in head
        sources = _real_sources()
        sources[REGISTRY] = (
            head.replace(guard, "if false {", 1) + "pub(crate) fn build_py_context_memo" + tail
        )
        _, problems = audit(sources)
        assert problems == [
            f"{REGISTRY}::build_py_context does not satisfy [sidecar: skip TYPED_ARGS_KEY]"
        ]

    def test_sidecar_scrub_removed_from_build_py_context_memo(self) -> None:
        text = _real_sources()[REGISTRY]
        head, tail = text.split("pub(crate) fn build_py_context_memo", 1)
        guard = "if key == crate::renderer::TYPED_ARGS_KEY {"
        assert guard in tail
        sources = _real_sources()
        sources[REGISTRY] = (
            head + "pub(crate) fn build_py_context_memo" + tail.replace(guard, "if false {", 1)
        )
        _, problems = audit(sources)
        assert problems == [
            f"{REGISTRY}::build_py_context_memo does not satisfy [sidecar: skip TYPED_ARGS_KEY]"
        ]

    def test_live_frames_scrub_removed(self) -> None:
        _, problems = audit(
            _mutated(REGISTRY, ".del_item(crate::renderer::TYPED_ARGS_KEY)", '.del_item("x")')
        )
        assert problems == [
            f"{REGISTRY}::build_py_context_memo does not satisfy [live frames: del_item(TYPED_ARGS_KEY)]"
        ]


class TestAFourthBuilder:
    @pytest.mark.parametrize("shape", sorted(BUILDER_WITHOUT_SCRUB))
    def test_one_that_does_not_scrub_is_reported_by_name_and_obligation(self, shape: str) -> None:
        sources = _real_sources()
        sources[RENDERER] += "\n" + BUILDER_WITHOUT_SCRUB[shape]
        builders, problems = audit(sources)
        added = builders - EXPECTED_BUILDERS
        assert len(added) == 1 and next(iter(added))[0] == RENDERER, added
        assert len(problems) == 1 and problems[0].startswith(f"{RENDERER}::fourth_"), problems

    def test_one_that_does_scrub_still_fails_the_recorded_set_until_it_is_added(self) -> None:
        sources = _real_sources()
        sources[RENDERER] += (
            "\nfn fourth_flat_builder(context: &Context) -> HashMap<String, Value> {\n"
            "    let mut map = context.to_hashmap();\n"
            "    map.remove(TYPED_ARGS_KEY);\n"
            "    map\n}\n"
        )
        builders, problems = audit(sources)
        assert problems == []
        assert builders - EXPECTED_BUILDERS == {(RENDERER, "fourth_flat_builder")}

    def test_a_builder_in_another_crate_is_found(self) -> None:
        """The sink is scanned crate-wide, not in the two files that hold today's builders."""
        sources = _real_sources()
        sources["djust_live/src/lib.rs"] += "\n" + BUILDER_WITHOUT_SCRUB["to_hashmap"]
        builders, problems = audit(sources)
        assert ("djust_live/src/lib.rs", "fourth_flat_builder") in builders
        assert len(problems) == 1

    def test_a_cfg_test_caller_is_not_a_builder(self) -> None:
        sources = _real_sources()
        sources[RENDERER] += (
            "\n#[cfg(test)]\nmod probe {\n"
            "    fn only_in_tests(c: &Context) { let _ = c.to_hashmap(); }\n}\n"
        )
        builders, problems = audit(sources)
        assert builders == EXPECTED_BUILDERS and problems == []
