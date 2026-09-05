"""No doc or code may prescribe a deleted or inert API (#2679, #2680, #2656).

This exists because a hand-audit failed. The first pass at #2679/#2680 claimed
to have corrected "the four places" that taught the deleted `StateBus` and the
deleted `djustSecurity` global. Review found **21** — including two LIVE
`.semgrep` rules whose remediation text told a developer hitting a real XSS or
prototype-pollution finding to call `djustSecurity.safeSetInnerHTML()`, a
`window.StateBus.subscribe(...)` snippet against a class that no longer exists,
`README.md`, and four demo-project files.

Counting by hand is the thing that broke, so the fix is mechanical (#1859: a
pin nobody can drift past beats a promise of completeness). The next doc that
teaches `@client_state` as a working feature fails this test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check-inert-api-claims.py"


def test_no_docs_or_code_prescribe_a_deleted_or_inert_api() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (
        "docs or code prescribe an API that does not do what they say:\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _load_checker() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_inert_check", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_the_checker_actually_catches_a_reintroduction(tmp_path: Path) -> None:
    """The guard is load-bearing, not decorative (#1859, #1459).

    The first version of this test asserted on the regexes in ISOLATION, so a
    broken ``SCAN_DIRS`` / ``_files()`` — a directory silently dropped from the
    walk — would not have failed it (#2692). Run the real ``check()`` against a
    tmp tree instead, so the finding has to survive discovery, decoding, the
    exemption list and the scoping rules.
    """
    mod = _load_checker()
    root = _tree(
        tmp_path,
        {
            # The exact pre-fix shape from .semgrep/djust-security.yaml.
            ".semgrep/rules.yaml": "message: Use djustSecurity.safeSetInnerHTML() instead\n",
            # The exact pre-fix shape from STATE_MANAGEMENT_API.md.
            "docs/state.md": "```js\nwindow.StateBus.subscribe('temperature', cb)\n```\n",
            # A doc teaching the inert decorator with no marker at all.
            "docs/guide.md": '# Guide\n\n## Use it\n\n@client_state(keys=["filter"])\n',
            # An import of it, in a directory the original SCAN_DIRS omitted.
            "tests/e2e/test_x.py": "from djust.decorators import client_state\n",
            # A repo-root `.md` other than README/llms.txt — the original walk
            # named those two by hand, so this one was invisible (#2692).
            "QUICKSTART.md": "# Quickstart\n\nCall djustSecurity.safeSetInnerHTML(el, s).\n",
        },
    )
    found = mod.check(root)
    blob = "\n".join(found)

    assert any("rules.yaml" in f and "djustSecurity" in f for f in found), blob
    assert any("state.md" in f and "StateBus" in f for f in found), blob
    assert any("guide.md" in f and "client_state" in f for f in found), blob
    # Ceiling 2 of #2692: `tests/` was not scanned at all before...
    assert any("tests/e2e/test_x.py" in f for f in found), blob
    # ...and neither was any repo-root `.md` beyond the two named by hand.
    assert any("QUICKSTART.md" in f for f in found), blob


def test_a_marker_elsewhere_in_the_file_does_not_discharge_the_mention(tmp_path: Path) -> None:
    """#2692 ceiling 1 — the reported hole, as an executable case.

    Deleting only the ``@client_state`` caveat in ``docs/BEST_PRACTICES_AI.md``
    left the checker green, because ``@debounce``'s marker 28 lines earlier —
    inside the SAME code fence — satisfied the file-level rule. That shape is
    the one #2680 was about: siblings labelled inert, this one not.
    """
    mod = _load_checker()
    same_fence = (
        "# Decorators\n\n"
        "## Usage\n\n"
        "```python\n"
        "@debounce(wait=0.5)  # INERT — no client impl (#2656)\n"
        "def search(self, **kwargs): ...\n"
        "\n"
        '@client_state(keys=["active_tab"])\n'
        "def switch_tab(self, **kwargs): ...\n"
        "```\n"
    )
    found = mod.check(_tree(tmp_path / "a", {"docs/d.md": same_fence}))
    assert any("d.md" in f and "client_state" in f for f in found), found

    # ...and a marker in the mention's OWN block does discharge it.
    marked = same_fence.replace(
        '@client_state(keys=["active_tab"])',
        '@client_state(keys=["active_tab"])  # INERT (#2656)',
    )
    assert marked != same_fence
    assert mod.check(_tree(tmp_path / "b", {"docs/d.md": marked})) == []


def test_a_header_banner_discharges_every_mention_in_the_file(tmp_path: Path) -> None:
    """The banner escape hatch, which the doc corpus relies on.

    A marker in the file's opening construct — the markdown preamble, a module
    docstring, a leading template comment — covers the whole file, because a
    reader meets it before any example. Below that opening it covers only its
    own block.
    """
    mod = _load_checker()
    body = '## Examples\n\n@client_state(keys=["a"])\n\n@client_state(keys=["b"])\n'

    banner = "# Doc\n\n> **Inert.** `@client_state` does nothing (#2656).\n\n" + body
    assert mod.check(_tree(tmp_path / "banner", {"docs/d.md": banner})) == []

    # The same sentence one line BELOW the preamble covers neither mention.
    late = "# Doc\n\n" + body + "\n> **Inert.** `@client_state` does nothing (#2656).\n"
    assert len(mod.check(_tree(tmp_path / "late", {"docs/d.md": late}))) == 2

    # Module docstring, for the .py arm of the banner rule.
    py_banner = '"""Helper.\n\n@client_state is INERT (#2656).\n"""\n\n@client_state(keys=["a"])\n'
    assert mod.check(_tree(tmp_path / "py", {"docs/x.py": py_banner})) == []

    # Leading `{# ... #}` template comment, for the .html arm.
    html_banner = '{# @client_state is INERT (#2656) #}\n<div>@client_state(keys=["a"])</div>\n'
    assert mod.check(_tree(tmp_path / "html", {"docs/t.html": html_banner})) == []


def test_the_checker_does_not_fire_on_the_corrections_themselves(tmp_path: Path) -> None:
    """If a fix is itself a violation, the guard is unusable."""
    mod = _load_checker()
    assert (
        mod.check(
            _tree(
                tmp_path,
                {
                    "docs/fix.md": (
                        "# Fix\n\n> There is no `djustSecurity` global; use textContent.\n"
                        "> `StateBus` was deleted in #2680.\n"
                    )
                },
            )
        )
        == []
    )
