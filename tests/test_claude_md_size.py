"""CLAUDE.md stays a rule sheet.

CLAUDE.md is injected into every Claude Code session and every pipeline
subagent, so each byte is paid for on every agent. By 2026-11 it had grown to
150 KB (about 37k tokens), mostly retro canon appended one arc at a time. That
canon now lives in docs/process-canon/, read on demand. This test keeps the
rule sheet from growing back: new retro rules go to docs/process-canon/.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUDGET_BYTES = 24 * 1024


def test_claude_md_within_budget():
    size = (ROOT / "CLAUDE.md").stat().st_size
    assert size <= BUDGET_BYTES, (
        f"CLAUDE.md is {size} bytes, over its {BUDGET_BYTES}-byte budget. "
        "Move retro rules and long rationale to docs/process-canon/ "
        "(one page per arc, a row in its README) and keep a one-line pointer here."
    )


def test_no_retro_canon_sections_in_claude_md():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## Process canonicalizations from" not in text, (
        "Retro canon belongs in docs/process-canon/, not CLAUDE.md."
    )


def test_process_canon_index_lists_every_page():
    canon = ROOT / "docs" / "process-canon"
    index = (canon / "README.md").read_text(encoding="utf-8")
    pages = sorted(p.name for p in canon.glob("*.md") if p.name != "README.md")
    missing = [p for p in pages if f"({p})" not in index]
    assert not missing, f"docs/process-canon/README.md does not list: {missing}"
