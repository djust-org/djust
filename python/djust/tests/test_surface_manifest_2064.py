"""Tests for #2064 — machine-readable surface manifest + parity canaries.

Context
-------
djust.org's ``/docs/directives/`` reference catalog silently lacked the
entire JS-commands family and both new hook attributes (``dj-hook-value-*``,
``dj-hook-target``) for months, fixed only by hand in djust.org PR #52.
Nothing on the framework side detected the drift.

Phase 1 (this file + ``djust.schema.get_surface_manifest``) ships a
machine-readable manifest of djust's user-facing surface. Phase 2 (NOT this
PR) is djust.org CI diffing its reference-doc fixture against this manifest.

A manifest that can silently drift from the real framework surface is
decorative (#1859) — the point of this file is the PARITY CANARIES, not the
manifest plumbing:

- ``TestJSCommandTriParity`` — extracts the client chain-method names from
  ``static/djust/src/26-js-commands.js``, and asserts client == Python
  ``djust.js.JS`` == manifest ``js_commands``.
- ``TestDirectiveBindingParity`` — extracts every ``dj-*`` attribute name the
  CLIENT binds via one of the covered call shapes (selector arguments,
  ``get/has/set/removeAttribute``, ``startsWith`` prefix checks, attribute-name
  constants, and strict-equality ``name === 'dj-X'`` reads — across every
  ``static/djust/src/*.js`` module) and asserts each one is documented in
  ``DIRECTIVES`` (or is in the explicit, justified ``_STRUCTURAL_EXCLUSIONS``
  list).
- ``TestEqualityShapeCoverage`` — pins that the ``name === 'dj-X'`` /
  ``m.attributeName === 'dj-X'`` read shape (the sole trigger for the
  MutationObserver modules) is covered and load-bearing (#1459/#1859).
- ``TestGateOffNonVacuous`` — proves both canaries are load-bearing (#1468):
  neuter the thing being checked and confirm the assertion actually goes red.

Dogfooding (#1459): both canaries were run BEFORE the ``DIRECTIVES`` backfill
in this same PR and were RED — see the PR description for the captured
pre-backfill failure output. Every gap they found was backfilled into
``python/djust/schema.py`` with a real description sourced from reading the
corresponding client module (never invented).
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from django.core.management import call_command

from djust import __version__
from djust.js import JS
from djust.schema import DIRECTIVES, get_surface_manifest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parents[1] / "static" / "djust" / "src"
_JS_COMMANDS_FILE = _SRC_DIR / "26-js-commands.js"


def _all_client_src_text() -> str:
    files = sorted(_SRC_DIR.glob("*.js"))
    assert len(files) >= 30, (
        f"only found {len(files)} client JS modules under {_SRC_DIR} — the "
        "source layout moved; extraction below would silently scan nothing."
    )
    return "\n".join(f.read_text() for f in files)


# ===========================================================================
# (a) JS-command tri-parity: client 26-js-commands.js <-> Python djust.js.JS
#     <-> manifest["js_commands"]
# ===========================================================================

# Client-only chain-starting helper: `djust.js.chain()` just returns a fresh
# empty JSChain(); there's no Python-side equivalent factory method because
# the Python `JSChain()` dataclass constructor isn't exposed as a public
# `_JSFactory` method (chains start via `JS.<command>(...)` directly). This
# is a genuine, justified client-only affordance, not an undocumented gap.
_JS_CLIENT_ONLY_NAMES = {"chain"}

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


def _extract_client_js_command_names() -> Set[str]:
    """Extract the public keys of the ``const factory = {...}`` object
    literal in 26-js-commands.js (the ``window.djust.js`` chain factory).

    Anchored to the structural shape (an object literal named exactly
    ``factory``) rather than a loose attribute-name grep, per #2064's
    "anchor it to the structural shape" instruction — a rename of the
    factory variable or a restructure will make
    ``test_client_extraction_is_not_vacuous`` fail loudly instead of the
    parity assertion silently passing on an empty set.
    """
    text = _JS_COMMANDS_FILE.read_text()
    m = re.search(r"const factory = \{(.*?)\n    \};", text, re.DOTALL)
    assert m is not None, (
        "could not locate `const factory = {...};` block in "
        f"{_JS_COMMANDS_FILE} — has window.djust.js been restructured? "
        "Update the extraction regex."
    )
    names: Set[str] = set()
    for line in m.group(1).splitlines():
        key_match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", line)
        if not key_match:
            continue
        name = key_match.group(1)
        if name.startswith("_"):  # internal hooks (_executeOps, _JSChain, ...)
            continue
        names.add(name)
    return names


def _extract_python_js_command_names() -> Set[str]:
    return {m for m in dir(JS) if not m.startswith("_")}


class TestJSCommandTriParity:
    """(a) client chain factory <-> Python JS helper <-> manifest."""

    def test_client_extraction_is_not_vacuous(self) -> None:
        names = _extract_client_js_command_names()
        assert len(names) >= 10, (
            f"client JS-command extraction returned only {len(names)} names "
            f"({sorted(names)!r}) out of an expected 13 (12 commands + "
            "'chain'). A near-empty extraction would make the tri-parity "
            "assertion below vacuously pass — the regex likely broke."
        )

    def test_python_helper_extraction_is_not_vacuous(self) -> None:
        names = _extract_python_js_command_names()
        assert len(names) >= 10, (
            f"dir(djust.js.JS) returned only {len(names)} public names "
            f"({sorted(names)!r}) out of an expected 12 — either JS has "
            "been gutted or the introspection is broken."
        )

    def test_client_python_manifest_tri_parity(self) -> None:
        client_names = {
            _camel_to_snake(n)
            for n in _extract_client_js_command_names()
            if n not in _JS_CLIENT_ONLY_NAMES
        }
        python_names = _extract_python_js_command_names()
        manifest_names = set(get_surface_manifest()["js_commands"])

        assert client_names == python_names, (
            "Client `window.djust.js` chain factory and Python `djust.js.JS` "
            f"have drifted. client(snake_case, minus client-only 'chain')="
            f"{sorted(client_names)!r} python={sorted(python_names)!r} "
            f"client-only={sorted(client_names - python_names)!r} "
            f"python-only={sorted(python_names - client_names)!r}"
        )
        assert python_names == manifest_names, (
            "djust.js.JS and the surface manifest's js_commands have "
            f"drifted: python={sorted(python_names)!r} "
            f"manifest={sorted(manifest_names)!r}"
        )


# ===========================================================================
# (b) Directive-binding parity: every dj-* attribute the CLIENT binds must
#     be documented in DIRECTIVES (or explicitly, justifiably excluded).
# ===========================================================================

# --- extraction regexes, each anchored to a real DOM-binding call shape ---

# `[dj-xxx]` / `[dj-xxx="value"]` bracket group found inside a larger string
# (handles compound selectors like '[dj-view]:not([dj-sticky-root])').
_BRACKET_RE = re.compile(r"\[dj-([a-zA-Z0-9_.\\-]+?)(?:=[^\]]*)?\]")

# Selector string literals passed directly to a DOM query call.
_CALL_ARG_BACKTICK_RE = re.compile(
    r"\.(?:closest|querySelector|querySelectorAll|matches)\(\s*`([^`]*)`"
)
_CALL_ARG_QUOTE_RE = re.compile(
    r"""\.(?:closest|querySelector|querySelectorAll|matches)\(\s*['"]([^'"]*)['"]"""
)

# Bare selector-literal `'[dj-xxx]'` / `` `[dj-xxx="${x}"]` `` — catches
# selectors built as an array of literals and joined/used indirectly (e.g.
# 10-loading-states.js's `[dj-loading]`, `[dj-loading\.disable]`, ... list)
# and selectors returned from a helper function (e.g. 19-hooks.js's
# `_targetSelector` returning `` `[dj-hook-target="${esc}"]` ``).
_BARE_SELECTOR_LITERAL_QUOTE_RE = re.compile(
    r"""['"](\[dj-[a-zA-Z0-9_.\\-]+?(?:=[^\]'"]*)?\])['"]"""
)
_BARE_SELECTOR_LITERAL_BACKTICK_RE = re.compile(r"`(\[dj-[a-zA-Z0-9_.\\-]+?(?:=[^\]`]*)?\])`")

# el.getAttribute('dj-xxx') / hasAttribute / setAttribute / removeAttribute
_ATTR_METHOD_RE = re.compile(
    r"""\.(?:get|has|set|remove)Attribute\(\s*['"]dj-([a-zA-Z0-9_.-]+)['"]"""
)

# attr.name.startsWith('dj-xxx') — dynamic/prefix attribute discovery
_STARTSWITH_RE = re.compile(r"""\.startsWith\(\s*['"]dj-([a-zA-Z0-9_.-]+)['"]\s*\)""")

# two-arg helper(el, 'dj-xxx', ...) style, e.g. hasBoolAttr/parseIntAttr/parseFloatAttr
_HELPER_ARG_RE = re.compile(r"""\w+\(\s*\w+,\s*['"]dj-([a-zA-Z0-9_.-]+)['"]""")

# `attr.name === 'dj-xxx'` / `m.attributeName === 'dj-xxx'` — the strict-equality
# read shape. This is the SOLE observation trigger for MutationObserver-driven
# modules (35-dj-dialog.js / 37-dj-mutation.js / 38-dj-sticky-scroll.js /
# 41-dj-transition.js / 42-dj-remove.js check `m.attributeName === 'dj-X'`) and
# the primary attribute-iteration loop in 20-model-binding.js / 08-event-parsing.js
# (`name === 'dj-model'` / `attr.name === 'dj-id'`). Without this pattern the
# canary is blind to any directive discovered ONLY via equality — a confirmed
# false-negative (a reviewer's injected `else if (name === 'dj-probeonly')`
# slipped past a green canary). Covered by
# TestEqualityShapeCoverage::test_equality_pattern_is_load_bearing.
_EQ_ATTR_RE = re.compile(r"""(?:name|attributeName)\s*===\s*['"]dj-([a-zA-Z0-9_.-]+)['"]""")

# `const _XXX_ATTR = 'dj-...';` — module-level attribute-name constants.
# Anchored to a `const/let/var` DECLARATION of an identifier that itself
# starts with `_` (e.g. `_FLIP_ATTR`, `_GROUP_ENTER_ATTR`) to avoid matching
# unrelated identifiers like `CONTAINER_ID` / `BUBBLE_ID` (CSS id/class
# constants, not dj-* attribute names — verified by reading 23-flash.js /
# 28-tutorial-bubble.js).
_CONST_ATTR_RE = re.compile(
    r"""(?:const|let|var)\s+_[A-Za-z][A-Za-z0-9_]*\s*=\s*['"]dj-([a-zA-Z0-9_.-]+)['"]"""
)

# Quoted string ending in a hyphen — prefix-family constants like
# 'dj-window-', 'dj-hook-value-'. Extracted separately from the exact-name
# regexes above because a prefix constant is never itself a complete
# attribute name.
_PREFIX_CONST_RE = re.compile(r"""['"](dj-[a-zA-Z0-9_-]+-)['"]""")


def _normalize(name: str) -> str:
    return name.replace("\\", "")


def _extract_client_bound_directives(
    text: str | None = None, *, include_equality: bool = True
) -> Set[str]:
    """Every ``dj-*`` attribute name the client queries/reads, prefixed
    with ``dj-`` (e.g. ``{"dj-click", "dj-model.lazy", ...}``).

    ``text`` defaults to the concatenated real client source; a test may pass
    a synthetic bundle (the #1459 empirical-canary shape). ``include_equality``
    is the gate-off toggle for `_EQ_ATTR_RE` — with it False, the equality
    read shape is NOT scanned, which is what `TestEqualityShapeCoverage`
    uses to prove the pattern is load-bearing."""
    if text is None:
        text = _all_client_src_text()
    found: Set[str] = set()

    for m in _CALL_ARG_BACKTICK_RE.finditer(text):
        for bm in _BRACKET_RE.finditer(m.group(1)):
            found.add(_normalize(bm.group(1)))
    for m in _CALL_ARG_QUOTE_RE.finditer(text):
        for bm in _BRACKET_RE.finditer(m.group(1)):
            found.add(_normalize(bm.group(1)))
    for m in _BARE_SELECTOR_LITERAL_QUOTE_RE.finditer(text):
        for bm in _BRACKET_RE.finditer(m.group(1)):
            found.add(_normalize(bm.group(1)))
    for m in _BARE_SELECTOR_LITERAL_BACKTICK_RE.finditer(text):
        for bm in _BRACKET_RE.finditer(m.group(1)):
            found.add(_normalize(bm.group(1)))
    simple_patterns = [_ATTR_METHOD_RE, _STARTSWITH_RE, _HELPER_ARG_RE, _CONST_ATTR_RE]
    if include_equality:
        simple_patterns.append(_EQ_ATTR_RE)
    for pattern in simple_patterns:
        for m in pattern.finditer(text):
            found.add(_normalize(m.group(1)))

    return {"dj-" + n for n in found}


def _extract_equality_bound_names() -> Set[str]:
    """Only the names discovered via the `=== 'dj-X'` shape (`_EQ_ATTR_RE`),
    prefixed with ``dj-``. Used by the load-bearing coverage test to prove the
    equality pattern actually catches its shape independently of the other
    extractors."""
    text = _all_client_src_text()
    return {"dj-" + _normalize(m.group(1)) for m in _EQ_ATTR_RE.finditer(text)}


def _extract_client_bound_prefix_families() -> Set[str]:
    """Quoted 'dj-xxx-'-shaped prefix constants (e.g. ``'dj-window-'``)."""
    text = _all_client_src_text()
    return {m.group(1) for m in _PREFIX_CONST_RE.finditer(text)}


# --- explicit, justified exclusions (#2064: "no silent exclusions") ---

_STRUCTURAL_EXCLUSIONS: Dict[str, str] = {
    "dj-id": (
        "Auto-assigned by the Rust template parser for VDOM identity; an "
        "authored dj-id is IGNORED, never read back (#1253 / "
        "project_vdom_authored_djid_ignored memory). Never something a "
        "developer adds for behavior."
    ),
    "dj-root": (
        "Structural required attribute, documented separately in "
        "BEST_PRACTICES['templates']['required_attributes'] — not a "
        "behavioral directive (matches the pre-existing design: dj-root was "
        "never in DIRECTIVES either)."
    ),
    "dj-view": (
        "Structural required attribute, documented separately in "
        "BEST_PRACTICES['templates']['required_attributes'] — see dj-root."
    ),
    "dj-liveview-root": (
        "Auto-stamped by client JS itself (14-init.js, "
        "`container.setAttribute('dj-liveview-root', '')`) on every "
        "[dj-view] container; never authored by users in a template."
    ),
    "dj-sticky-root": (
        "Auto-emitted by the server-side {% live_render ... sticky=True %} "
        "tag output (see python/djust/template_tags); the user-facing "
        "surface a template author writes is the tag, not this attribute."
    ),
    "dj-sticky-view": ("Auto-emitted by {% live_render sticky=True %}; see dj-sticky-root."),
    "dj-sticky-slot": (
        "Auto-emitted reattachment-slot marker from {% live_render "
        "sticky=True %}; see dj-sticky-root."
    ),
}

# Prefix-constant false positive: 13-lazy-hydration.js uses the STRING
# 'dj-target-' as a prefix for a RANDOM SYNTHETIC ELEMENT ID
# (`'dj-target-' + Math.random().toString(36).slice(2, 10)`), not as an
# HTML attribute-name family. Verified by reading the source at that line.
_PREFIX_FAMILY_EXCLUSIONS: Dict[str, str] = {
    "dj-target-": (
        "False positive: used as a prefix for a random synthetic element id "
        "in 13-lazy-hydration.js, not an attribute-name family."
    ),
}


def _declared_directive_names_and_prefixes(
    directives: List[Dict[str, Any]] = DIRECTIVES,
) -> Tuple[Set[str], Set[str], Dict[str, Set[str]]]:
    """Flatten DIRECTIVES into (exact names, prefix families, dotted-modifier map).

    - ``names``: every literal ``dj-*`` name declared as a top-level `name`
      or inside a `related_attributes` list (covers e.g. dj-copy-class as a
      related attribute of dj-copy, and dj-loading.disable as its own
      literal dotted top-level entry).
    - ``prefixes``: every declared family prefix (a `name` or
      `related_attributes` entry ending in "-*"), stored WITHOUT the
      trailing "*" so `attr.startswith(prefix)` works directly.
    - ``modifiers_by_base``: for directives that use a DOTTED-ATTRIBUTE-NAME
      modifier convention where the modifier itself isn't a separate literal
      entry (currently only dj-model: dj-model.lazy / dj-model.debounce-N),
      maps the base name to the set of modifier keys (e.g.
      {"dj-model": {"lazy", "debounce"}}).
    """
    names: Set[str] = set()
    prefixes: Set[str] = set()
    modifiers_by_base: Dict[str, Set[str]] = {}

    for d in directives:
        name = d["name"]
        if name.endswith("-*"):
            prefixes.add(name[:-1])
        else:
            names.add(name)

        for extra in d.get("related_attributes", []):
            if extra.endswith("-*"):
                prefixes.add(extra[:-1])
            else:
                names.add(extra)

        if d.get("modifiers"):
            mod_keys = modifiers_by_base.setdefault(name, set())
            for mod in d["modifiers"]:
                mod_keys.add(mod.split("-")[0])

    return names, prefixes, modifiers_by_base


def _is_covered(
    attr: str,
    names: Set[str],
    prefixes: Set[str],
    modifiers_by_base: Dict[str, Set[str]],
) -> bool:
    if attr in names:
        return True
    if any(attr.startswith(p) for p in prefixes):
        return True
    if "." in attr:
        base, modifier = attr.split(".", 1)
        mod_key = modifier.split("-")[0]
        if base in modifiers_by_base and mod_key in modifiers_by_base[base]:
            return True
    return False


class TestDirectiveBindingParity:
    """(b) every client-bound dj-* attribute is documented in DIRECTIVES."""

    def test_extraction_is_not_vacuous(self) -> None:
        found = _extract_client_bound_directives()
        assert len(found) >= 50, (
            f"client directive-binding extraction returned only {len(found)} "
            f"names — expected 90+. A near-empty extraction would make the "
            "parity assertion below vacuously pass; the regex likely broke "
            "against a restructured module."
        )

    def test_prefix_family_extraction_is_not_vacuous(self) -> None:
        found = _extract_client_bound_prefix_families()
        assert len(found) >= 3, (
            f"client prefix-family extraction returned only {len(found)} "
            f"({sorted(found)!r}) — expected at least dj-window-, "
            "dj-document-, dj-hook-value-, dj-value-."
        )

    def test_every_client_bound_directive_is_documented_or_excluded(self) -> None:
        found = _extract_client_bound_directives()
        names, prefixes, modifiers_by_base = _declared_directive_names_and_prefixes()

        undocumented = sorted(
            attr
            for attr in found
            if attr not in _STRUCTURAL_EXCLUSIONS
            and not _is_covered(attr, names, prefixes, modifiers_by_base)
        )
        assert not undocumented, (
            "Client-bound dj-* attributes with no DIRECTIVES entry — "
            "backfill into python/djust/schema.py DIRECTIVES with a real "
            "description (read the corresponding client module; don't "
            f"invent semantics), or add a justified _STRUCTURAL_EXCLUSIONS "
            f"entry: {undocumented}"
        )

    def test_every_prefix_family_is_documented_or_excluded(self) -> None:
        found = _extract_client_bound_prefix_families()
        _names, prefixes, _modifiers = _declared_directive_names_and_prefixes()

        undocumented = sorted(
            prefix
            for prefix in found
            if prefix not in _PREFIX_FAMILY_EXCLUSIONS and prefix not in prefixes
        )
        assert not undocumented, (
            "Client-bound dj-*- prefix families with no '<prefix>*' "
            "DIRECTIVES entry (as a top-level name or related_attribute) — "
            f"backfill or exclude: {undocumented}"
        )

    def test_exclusions_are_not_stale(self) -> None:
        """Every documented exclusion must correspond to something the
        extraction actually finds — an exclusion for a name/prefix that no
        longer appears client-side is dead documentation, and (worse) could
        mask a FUTURE real gap if the excluded string is ever reintroduced
        with different semantics."""
        found_names = _extract_client_bound_directives()
        found_prefixes = _extract_client_bound_prefix_families()

        stale_name_exclusions = sorted(set(_STRUCTURAL_EXCLUSIONS) - found_names)
        assert not stale_name_exclusions, (
            f"_STRUCTURAL_EXCLUSIONS entries not found by extraction (stale?): "
            f"{stale_name_exclusions}"
        )
        stale_prefix_exclusions = sorted(set(_PREFIX_FAMILY_EXCLUSIONS) - found_prefixes)
        assert not stale_prefix_exclusions, (
            f"_PREFIX_FAMILY_EXCLUSIONS entries not found by extraction (stale?): "
            f"{stale_prefix_exclusions}"
        )


# ===========================================================================
# (b') Equality-shape coverage — `_EQ_ATTR_RE` is load-bearing (#1859).
#
# The `name === 'dj-X'` / `m.attributeName === 'dj-X'` read shape is the SOLE
# observation trigger for the MutationObserver-driven modules and the primary
# attribute-iteration loop. A reviewer proved the pre-fix canary was blind to
# it: an injected `else if (name === 'dj-probeonly')` slipped past a green
# canary. These tests pin that `_EQ_ATTR_RE` closes that blind spot.
# ===========================================================================


class TestEqualityShapeCoverage:
    def test_equality_extractor_finds_real_shapes(self) -> None:
        """The equality regex actually matches the real `=== 'dj-X'` shapes in
        the shipped source (e.g. `m.attributeName === 'dj-sticky-scroll'` in
        38-dj-sticky-scroll.js, `name === 'dj-model'` in 20-model-binding.js)."""
        eq_names = _extract_equality_bound_names()
        for expected in ("dj-sticky-scroll", "dj-mutation", "dj-model", "dj-dialog"):
            assert expected in eq_names, (
                f"{expected} is bound via the `=== 'dj-X'` shape in the client "
                f"source but `_EQ_ATTR_RE` did not extract it — got {sorted(eq_names)!r}"
            )

    def test_equality_pattern_is_load_bearing(self) -> None:
        """Empirical canary (#1459) mirroring the reviewer's reproduction:
        inject an equality-ONLY binding (`else if (name === 'dj-probeonly')`,
        with NO selector/getAttribute/... sibling) into the real client bundle.

        - WITH `_EQ_ATTR_RE` in the extraction loop, the probe IS discovered.
        - WITHOUT it (`include_equality=False` — the gate-off), the probe is
          NOT discovered.

        This goes RED the moment `_EQ_ATTR_RE` is dropped from the loop, proving
        the pattern is load-bearing rather than decorative (#1859/#1468). The
        probe name is deliberately equality-only so no other extractor can mask
        the regression."""
        probe_snippet = (
            "\nfunction _probe(name) {\n"
            "    if (name === 'dj-probeonly') { return true; }\n"
            "    return false;\n"
            "}\n"
        )
        mutated = _all_client_src_text() + probe_snippet

        with_eq = _extract_client_bound_directives(mutated, include_equality=True)
        without_eq = _extract_client_bound_directives(mutated, include_equality=False)

        assert "dj-probeonly" in with_eq, (
            "Equality pattern failed to catch an injected `name === 'dj-probeonly'` "
            "binding — `_EQ_ATTR_RE` is not wired into the extraction loop."
        )
        assert "dj-probeonly" not in without_eq, (
            "Gate-off failed: the equality-only probe was discovered even with "
            "`_EQ_ATTR_RE` disabled, so some OTHER extractor is catching it and "
            "this test is not actually proving the equality pattern is load-bearing "
            "(#1468). Choose a probe name only reachable via the `===` shape."
        )


# ===========================================================================
# (c) Manifest shape pins
# ===========================================================================


class TestManifestShape:
    def test_manifest_version_is_pinned(self) -> None:
        assert get_surface_manifest()["manifest_version"] == 1

    def test_djust_version_matches_package(self) -> None:
        assert get_surface_manifest()["djust_version"] == __version__

    def test_json_serializable(self) -> None:
        json.dumps(get_surface_manifest())  # must not raise

    def test_top_level_keys(self) -> None:
        manifest = get_surface_manifest()
        assert set(manifest.keys()) == {
            "manifest_version",
            "djust_version",
            "directives",
            "js_commands",
            "view_api",
        }

    def test_deterministic_repeat_calls(self) -> None:
        first = get_surface_manifest()
        second = get_surface_manifest()
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    def test_directives_sorted_by_name(self) -> None:
        names = [d["name"] for d in get_surface_manifest()["directives"]]
        assert names == sorted(names)

    def test_js_commands_sorted(self) -> None:
        commands = get_surface_manifest()["js_commands"]
        assert commands == sorted(commands)

    def test_view_api_sections_sorted_by_name(self) -> None:
        view_api = get_surface_manifest()["view_api"]
        assert set(view_api.keys()) == {
            "lifecycle_methods",
            "class_attributes",
            "decorators",
            "navigation_methods",
            "stream_methods",
            "push_event_methods",
        }
        for section_name, entries in view_api.items():
            entry_names = [e["name"] for e in entries]
            assert entry_names == sorted(entry_names), f"{section_name} not sorted"
            assert entries, f"{section_name} is empty"

    def test_directives_are_lossless_copy_of_DIRECTIVES(self) -> None:
        manifest_directives = get_surface_manifest()["directives"]
        assert len(manifest_directives) == len(DIRECTIVES)
        by_name = {d["name"]: d for d in DIRECTIVES}
        for entry in manifest_directives:
            assert entry == by_name[entry["name"]]


# ===========================================================================
# Gate-off (#1468): prove both parity canaries are load-bearing, not
# decorative (#1859) — neuter the thing being checked and confirm the
# coverage-check machinery actually reports it as a gap.
# ===========================================================================


class TestGateOffNonVacuous:
    def test_directive_parity_flags_a_removed_entry(self) -> None:
        """Removing the dj-click-away DIRECTIVES entry must make the
        coverage check report it as undocumented."""
        mutated = [d for d in DIRECTIVES if d["name"] != "dj-click-away"]
        assert len(mutated) == len(DIRECTIVES) - 1  # sanity: entry existed

        names, prefixes, modifiers_by_base = _declared_directive_names_and_prefixes(mutated)
        assert not _is_covered("dj-click-away", names, prefixes, modifiers_by_base), (
            "Gate-off check failed: with the dj-click-away entry removed, "
            "the coverage-check logic should report it as NOT covered. If "
            "this assertion fails, test_every_client_bound_directive_is_"
            "documented_or_excluded is not load-bearing (#1468)."
        )

    def test_directive_parity_flags_a_removed_prefix_family(self) -> None:
        """Removing dj-hook's dj-hook-value-* related_attribute must make
        the prefix-family coverage check report it as undocumented."""
        mutated = []
        for d in DIRECTIVES:
            if d["name"] == "dj-hook":
                d = dict(d)
                d["related_attributes"] = [
                    a for a in d["related_attributes"] if a != "dj-hook-value-*"
                ]
                assert "dj-hook-value-*" not in d["related_attributes"]
            mutated.append(d)

        _names, prefixes, _modifiers = _declared_directive_names_and_prefixes(mutated)
        assert "dj-hook-value-" not in prefixes, (
            "Gate-off check failed: with dj-hook-value-* removed from "
            "dj-hook's related_attributes, the prefix should no longer be "
            "declared. If this assertion fails, "
            "test_every_prefix_family_is_documented_or_excluded is not "
            "load-bearing (#1468)."
        )

    def test_js_command_parity_flags_a_removed_python_method(self) -> None:
        """Removing 'push' from the Python-side comparison set must break
        tri-parity with the (unchanged) client extraction."""
        client_names = {
            _camel_to_snake(n)
            for n in _extract_client_js_command_names()
            if n not in _JS_CLIENT_ONLY_NAMES
        }
        mutated_python_names = _extract_python_js_command_names() - {"push"}

        assert client_names != mutated_python_names, (
            "Gate-off check failed: removing 'push' from the Python-side "
            "set should break tri-parity with the client set. If this "
            "assertion fails, test_client_python_manifest_tri_parity is "
            "not load-bearing (#1468)."
        )


# ===========================================================================
# Management command — `djust_surface_manifest`
# ===========================================================================


class TestManagementCommand:
    def test_stdout_emits_valid_manifest_json(self) -> None:
        buf = io.StringIO()
        call_command("djust_surface_manifest", stdout=buf)
        payload = json.loads(buf.getvalue())
        assert payload == get_surface_manifest()

    def test_indent_option_pretty_prints(self) -> None:
        buf = io.StringIO()
        call_command("djust_surface_manifest", "--indent", "2", stdout=buf)
        out = buf.getvalue()
        assert "\n  " in out  # a 2-space indent level appears
        assert json.loads(out) == get_surface_manifest()

    def test_output_flag_writes_clean_json_file(self, tmp_path: Path) -> None:
        """`--output <file>` must write ONLY the JSON (no stdout banner noise),
        so a DEBUG-mode HVR `[HotReload]` line can't corrupt the file the
        phase-2 consumer reads (finding #3)."""
        target = tmp_path / "manifest.json"
        buf = io.StringIO()
        call_command("djust_surface_manifest", "--output", str(target), stdout=buf)

        # Nothing written to stdout on the --output path (so a `> file`
        # redirect of a banner-polluted stdout is moot; the file is clean).
        assert buf.getvalue() == ""

        text = target.read_text(encoding="utf-8")
        assert text.startswith("{"), (
            f"manifest file must start with the JSON object, not a banner line — got: {text[:60]!r}"
        )
        assert json.loads(text) == get_surface_manifest()
