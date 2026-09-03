#!/usr/bin/env python3
"""Pin documented flag defaults to the ACTUAL default in code.

Written after the `virtual_keyed_ops` flip (#2017) needed **five** manual
documentation sweeps, every one of which under-counted. The misses were not
obscure — they were sentences a few lines from ones just edited: `CHANGELOG.md`
saying "the default stays OFF" in the same `[Unreleased]` section as its own
"default ON" entry; `diff.rs` opening a doc comment with "Default OFF" twelve
lines above "The DEFAULT is ON".

A first version of this script tried to detect prose self-contradiction — find
sentences claiming ON and OFF in the same file. It did not work: an empirical
canary replaying the real `006f7ba0` contradiction did not fire, because the ON
claim lived in a sentence that never names the flag. Rather than tune regexes
until the canary passed (which would have produced a guard trusted more than it
deserved), that approach was abandoned.

What this checks instead is a STRUCTURAL fact with no natural-language guessing:
for each tracked flag, read the real default out of the code, then require every
registered doc site to state that same value. Each site is an explicit
(file, regex) pair, so the check can only pass by the doc actually agreeing —
never by the regex failing to find anything, which is asserted separately.

Adding a flag means adding its rows here. That is deliberate: a registry you
must edit is visible, whereas a heuristic that silently stops matching is not.
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def python_default(flag: str) -> bool:
    """The authoritative default: `LiveViewConfig._defaults[flag]` as source text."""
    src = (REPO / "python" / "djust" / "config.py").read_text()
    m = re.search(rf'"{re.escape(flag)}":\s*(True|False)\s*,', src)
    if not m:
        raise SystemExit(f"FAIL — could not read `{flag}` default from config.py")
    return m.group(1) == "True"


# For each flag: the doc sites that state its default, and how to read what they
# claim. Each `pattern` MUST have one group capturing a word meaning on/off.
SITES: dict[str, list[tuple[str, str]]] = {
    "virtual_keyed_ops": [
        # The ADR's iteration table — the canonical status of iteration 3.
        (
            "docs/adr/026-dj-virtual-differ-awareness.md",
            r"\|\s*3\.\s*flag flips ON after a soak\s*\|\s*\*\*(shipped|not shipped)",
        ),
        # The config key's own comment block, immediately above the default.
        (
            "python/djust/config.py",
            r"#\s*(?:ADR-026 iteration 3: )?[Dd]efault (ON|OFF)",
        ),
        # The Rust doc comment on the process-global.
        (
            "crates/djust_vdom/src/diff.rs",
            r"///\s*Default (ON|OFF) since|///\s*Default (ON|OFF)\b",
        ),
    ],
    # ADR-027's kill-switch (#2539). Registered by movement 3, which flipped it
    # — the flip touched six prose statements of the default across four files
    # plus two Rust literals, which is the shape #2017 needed five sweeps for.
    "template_resolve_lazy": [
        # The ADR's sequencing table — the canonical status of the flip.
        (
            "docs/adr/027-template-variable-resolution-follows-django.md",
            r"\|\s*4\.\s*flip the default\s*\|\s*\*\*(shipped|not shipped)",
        ),
        # The config key's own comment block, immediately above the default.
        (
            "python/djust/config.py",
            r"[Dd]efault \*\*(True|False)\*\* since\n\s*#\s*movement 3 \(#2539\)",
        ),
        # `apply_resolve_lazy`'s docstring — the module every render path uses.
        (
            "python/djust/render_env.py",
            r"Default \*\*(ON|OFF)\*\* since movement 3",
        ),
        # The type stub, which is what an editor shows a caller.
        (
            "python/djust/_rust.pyi",
            r"`LIVEVIEW_CONFIG\[\"template_resolve_lazy\"\]`, default \*\*(True|False)\*\*",
        ),
        # The Rust thread-local's doc comment — the OTHER literal, and the one
        # a fresh thread actually answers.
        (
            "crates/djust_core/src/lib.rs",
            r"ADR-027's kill-switch, per THREAD \(#2539\)\. Default `(true|false)`",
        ),
        # The PyO3 setter's doc comment, one crate over.
        (
            "crates/djust_live/src/lib.rs",
            r"/// `LIVEVIEW_CONFIG\[\"template_resolve_lazy\"\]`, default \*\*(ON|OFF)\*\*",
        ),
    ],
}

TRUTHY = {"shipped", "on", "true"}
FALSEY = {"not shipped", "off", "false"}


def claim_of(match: re.Match) -> bool | None:
    """Reduce a captured word to on/off, or None if unrecognised."""
    for g in match.groups():
        if g is None:
            continue
        g = g.strip().lower()
        if g in TRUTHY:
            return True
        if g in FALSEY:
            return False
    return None


def main() -> int:
    problems: list[str] = []

    for flag, sites in SITES.items():
        actual = python_default(flag)
        for rel, pattern in sites:
            path = REPO / rel
            if not path.is_file():
                problems.append(f"{rel}: registered doc site for `{flag}` does not exist")
                continue

            m = re.search(pattern, path.read_text())
            if not m:
                # A silently-unmatched pattern is the failure mode that made the
                # previous version useless. Treat it as a hard error, never a pass.
                problems.append(
                    f"{rel}: no statement of `{flag}`'s default found.\n"
                    f"    The site is registered in {pathlib.Path(__file__).name} but its\n"
                    f"    pattern no longer matches — either the wording moved (update the\n"
                    f"    pattern) or the statement was deleted (restore it)."
                )
                continue

            claimed = claim_of(m)
            if claimed is None:
                problems.append(f"{rel}: matched {m.group(0)!r} but could not read on/off from it")
            elif claimed != actual:
                problems.append(
                    f"{rel}: says `{flag}` defaults "
                    f"{'ON' if claimed else 'OFF'}, but config.py says "
                    f"{'ON' if actual else 'OFF'}\n    matched: {m.group(0).strip()[:90]!r}"
                )

    if problems:
        print("FAIL — documented flag defaults disagree with the code:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}\n", file=sys.stderr)
        return 1

    n = sum(len(v) for v in SITES.values())
    print(f"OK — {n} documented default(s) agree with config.py across {len(SITES)} flag(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
