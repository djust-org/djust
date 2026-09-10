"""ADR-027 Step 5 (#2628): the ``template_resolve_lazy`` kill-switch is gone.

Movement 3 (#2539) flipped the shipped default to the live-handle sink and
kept ``LIVEVIEW_CONFIG["template_resolve_lazy"] = False`` as an escape hatch
that selected the OLD conversion arms (the eager ``__dict__`` bulk dump, the
one-shot-iterator and over-cap declines, the ungated ``Missing``→``None``
substitution). Step 5 deletes the flag AND those arms together — a hatch
whose arms are gone is not a hatch — so after this PR there is no production
code path that can select the deleted behaviour.

The pin below is the inverted gate-off for a DELETION (CLAUDE.md, #1468):
rather than proving a mechanism is load-bearing by switching it off, it
proves the deleted mechanism is unreachable by asserting that no production
source still names the switch. The file set is DERIVED at run time from the
repo (#2727 — a count typed into a test is a claim, not a measurement), and
the scanner is proven non-vacuous against a synthetic hit before the real
assertion runs (#2129 — a harness that reports zero because it scanned
nothing is indistinguishable from one that measured).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

#: Every spelling the kill-switch had, on either side of the boundary. The
#: settings key, the Python readers, the Rust cell + its accessors, the PyO3
#: setter/getter, the ``RenderEnv`` field. One regex so the pin cannot cover
#: the Python half and miss the Rust half (#1646).
DELETED_SPELLINGS = re.compile(
    r"template_resolve_lazy"  # the settings key + every Python reader
    r"|\bset_resolve_lazy\b"  # djust_core / PyO3 setter
    r"|\bresolve_lazy_enabled\b"  # the PyO3 getter
    r"|\bresolve_lazy\b"  # the Rust cell accessor, the RenderEnv field
    r"|\bRESOLVE_LAZY(_DEFAULT)?\b"  # the thread-local + its literal
    r"|\bapply_resolve_lazy\b"  # the render_env push
)

SUFFIXES = {".py", ".pyi", ".rs"}


def production_sources() -> list[Path]:
    """The scanned file set: ``python/djust`` minus its own tests, and every
    crate's ``src/``. Derived, not listed."""
    files: list[Path] = []
    pkg = REPO / "python" / "djust"
    for path in pkg.rglob("*"):
        if path.suffix in SUFFIXES and "tests" not in path.relative_to(pkg).parts:
            files.append(path)
    for src_dir in (REPO / "crates").glob("*/src"):
        files.extend(p for p in src_dir.rglob("*") if p.suffix in SUFFIXES)
    return sorted(files)


def hits_in(paths: list[Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DELETED_SPELLINGS.search(line):
                label = path.relative_to(REPO) if path.is_relative_to(REPO) else path
                found.append(f"{label}:{lineno}: {line.strip()}")
    return found


class TestTheKillSwitchIsGone2628:
    def test_the_file_set_is_real(self) -> None:
        """Precondition (#2727): the derived set is non-trivial and spans
        BOTH halves of the boundary, or a zero below would mean nothing."""
        files = production_sources()
        rel = {str(p.relative_to(REPO)) for p in files}
        assert len(files) > 100, f"suspiciously small scan set: {len(files)}"
        assert "python/djust/config.py" in rel
        assert "python/djust/render_env.py" in rel
        assert "python/djust/_rust.pyi" in rel
        assert "crates/djust_core/src/lib.rs" in rel
        assert "crates/djust_core/src/context.rs" in rel
        assert "crates/djust_templates/src/renderer.rs" in rel
        assert "crates/djust_live/src/lib.rs" in rel

    def test_the_scanner_sees_a_synthetic_hit(self, tmp_path: Path) -> None:
        """Non-vacuity (#2129/#1468): the same scanner over a file that DOES
        name the switch reports it — so a clean real scan is a measurement."""
        for spelling in (
            'LIVEVIEW_CONFIG = {"template_resolve_lazy": False}',
            "djust_core::set_resolve_lazy(false);",
            "if crate::resolve_lazy() {",
            "static RESOLVE_LAZY: Cell<bool>",
            "apply_resolve_lazy()",
            "assert _rust.resolve_lazy_enabled()",
        ):
            probe = tmp_path / "probe.rs"
            probe.write_text(spelling + "\n", encoding="utf-8")
            assert hits_in([probe]), f"scanner missed {spelling!r}"
        clean = tmp_path / "clean.py"
        clean.write_text("x = 1\n", encoding="utf-8")
        assert not hits_in([clean])

    def test_no_production_source_names_the_switch(self) -> None:
        """The deletion itself. A hit here means a reader of the escape
        hatch survived — and with the arms it selected gone, that reader is
        either dead code or a silent behaviour fork (#1646)."""
        found = hits_in(production_sources())
        assert not found, "the ADR-027 kill-switch still has readers:\n" + "\n".join(found)

    def test_the_config_default_table_has_no_such_key(self) -> None:
        """Runtime companion to the source grep: the settings key is not
        seeded into ``LiveViewConfig`` at all, so a project that still sets it
        is setting an unknown key rather than a live one."""
        from djust.config import LiveViewConfig

        assert "template_resolve_lazy" not in LiveViewConfig._defaults
        assert "template_auto_call" in LiveViewConfig._defaults, (
            "control: the neighbouring ADR-024 key is still seeded"
        )

    def test_the_rust_module_exports_no_setter_or_getter(self) -> None:
        from djust import _rust

        assert not hasattr(_rust, "set_resolve_lazy")
        assert not hasattr(_rust, "resolve_lazy_enabled")
        assert hasattr(_rust, "set_active_timezone"), "control: a sibling setter survives"
        env = _rust.RenderEnv.capture()
        assert not hasattr(env, "resolve_lazy")
        assert hasattr(env, "timezone"), "control: a sibling field survives"


@pytest.mark.parametrize(
    "source, context, expected",
    [
        # The eager `__dict__` bulk dump is gone: `{{ o }}` is `str(o)`.
        ("{{ o }}", "plain", "Plain-object"),
        # …and its attributes are still reached, through the live handle.
        ("{{ o.a }}", "plain", "1"),
        # A one-shot iterator is consumed by `{% for %}` (row V), not
        # declined to its repr.
        ("{% for x in g %}{{ x }}{% endfor %}", "gen", "012"),
        # A filtered missing operand reaches the filter as None (#2539 m2).
        ('{% firstof nope|default_if_none:"X" "Y" %}', "plain", "X"),
    ],
)
def test_the_surviving_arm_is_the_lazy_one(source: str, context: str, expected: str) -> None:
    """Behavioural half: each cell here had a DIFFERENT answer on the escape
    hatch, and there is no longer a setting that can select it."""
    from djust import _rust

    class Plain:
        def __init__(self) -> None:
            self.a = 1

        def __str__(self) -> str:
            return "Plain-object"

    ctx = {"plain": {"o": Plain()}, "gen": {"g": (i for i in range(3))}}[context]
    assert _rust.render_template(source, ctx) == expected
