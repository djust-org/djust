#!/usr/bin/env python3
"""URL-scheme sweep: every parameter of every djust template tag, both engines (#3665).

Drives ``javascript:`` / ``vbscript:`` / ``data:`` payloads (plain, case-folded,
whitespace-, control-character-, entity- and percent-obfuscated) through EVERY
parameter each tag can read, whatever it is named, in four argument shapes,
through Django's engine (the Python tag) and ``DjustTemplateBackend`` (the Rust
engine), and reports every attribute of the FINAL output that still carries a
script-capable URL. See ``scripts/lib/template_library_parity/url_sweep.py``.

    scripts/template-library-url-sweep.py                   print holes + disagreements
    scripts/template-library-url-sweep.py --json out.json   also write the measurement
    scripts/template-library-url-sweep.py --only avatar     restrict to some tags
    scripts/template-library-url-sweep.py --quick           fewer payloads (what the test runs)

Exit codes: 0 no hole in either engine; 1 at least one; 2 the harness could not run.

Like ``template-library-parity.py`` this replaces ``uuid`` / ``datetime`` for its
whole process, so it must be the only thing running in it; the tests start it as
a subprocess.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib.template_library_parity import harness  # noqa: E402

#: The ``--quick`` corpus: one of each way a payload can be written, so the
#: sweep stays inside a test's budget without dropping an encoding class.
QUICK = (
    "js", "js-upper", "js-embedded-tab", "js-lead-newline", "js-entity-hex", "js-percent-colon",
    "vbs", "data-html", "data-svg",
    "https", "relative", "anchor", "mailto", "data-png",
)  # fmt: skip

#: What ``python/tests/test_url_scheme_sweep_3665.py`` runs: the plain attack, the
#: three families of obfuscation (control characters, entities, percent), the
#: other script schemes, and the values that must survive.
TEST_CORPUS = (
    "js", "js-embedded-tab", "js-lead-newline", "js-entity-hex", "js-percent-colon", "vbs",
    "data-html", "https", "relative", "mailto", "data-png",
)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", type=Path)
    parser.add_argument("--only", help="comma-separated tags (bare name or <library>:<name>)")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--shapes", help="comma-separated subset of the argument shapes")
    parser.add_argument(
        "--plant",
        metavar="LIB:TAG",
        help="gate-off: make the Python tag emit a javascript: link; the sweep must report it",
    )
    parser.add_argument("--corpus", choices=("all", "quick", "test"), default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--components-only", action="store_true", help="skip the template tags")
    parser.add_argument(
        "--components",
        action="store_true",
        help="also sweep the class-based Component subclasses (djust.components.components, .ui, ...)",
    )
    args = parser.parse_args(argv)

    harness.install_determinism()
    try:
        harness.configure_django()
        from scripts.lib.template_library_parity import url_sweep

        corpus = args.corpus or ("quick" if args.quick else "all")
        wanted = {"all": None, "quick": QUICK, "test": TEST_CORPUS}[corpus]
        payloads = [p for p in url_sweep.PAYLOADS if wanted is None or p[0] in wanted]
        only = set(filter(None, (args.only or "").split(","))) or None
        shapes = tuple(filter(None, (args.shapes or "").split(","))) or url_sweep.SHAPES
        sweep = url_sweep.run_sweep(
            payloads,
            {"<no tag>"} if args.components_only else only,
            shapes,
            (lambda key: print("... %s" % key, file=sys.stderr, flush=True))
            if args.verbose
            else None,
            args.plant,
        )
    except Exception:  # noqa: BLE001 - exit 2 ("could not run"), distinct from 1 ("a hole")
        traceback.print_exc()
        return 2

    component_cells, component_driven, component_unrendered = [], {}, []
    if args.components or args.components_only:
        component_cells, component_driven, component_unrendered = url_sweep.run_component_sweep(
            payloads, only, shapes
        )
    component_holes = url_sweep.component_holes(component_cells)
    holes = url_sweep.holes(sweep)
    diffs = url_sweep.disagreements(sweep)
    rendered = sum(1 for c in sweep.cells if c.rendered_both)
    print(
        "djust from %s\n%d tags, %d cells, %d rendered in both engines; %d holes, %d disagreements"
        % (
            sweep.probe["djust"],
            len(sweep.driven),
            len(sweep.cells),
            rendered,
            len(holes),
            len(diffs),
        )
    )
    for (engine, tag, param, element, attr), labels in sorted(holes.items()):
        print(
            "HOLE %-6s %-40s %-22s <%s %s>  %s"
            % (engine, tag, param, element, attr, ",".join(sorted(labels)))
        )
    if args.components or args.components_only:
        print(
            "%d component classes, %d cells; %d holes"
            % (len(component_driven), len(component_cells), len(component_holes))
        )
    for (cls, param, element, attr), labels in sorted(component_holes.items()):
        print(
            "HOLE component %-60s %-14s <%s %s>  %s"
            % (cls, param, element, attr, ",".join(sorted(labels)))
        )
    if args.verbose:
        for cell in diffs[:200]:
            print("DISAGREE %s %s %s %s py=%s/%s rs=%s/%s" % (
                cell.tag, cell.param, cell.shape, cell.label,
                bool(cell.python), cell.python_carries, bool(cell.rust), cell.rust_carries,
            ))  # fmt: skip
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "holes": sorted(
                        [list(k) + [sorted(v)] for k, v in holes.items()], key=lambda r: r[:5]
                    ),
                    "disagreements": [
                        [
                            c.tag,
                            c.param,
                            c.shape,
                            c.label,
                            bool(c.python),
                            c.python_carries,
                            bool(c.rust),
                            c.rust_carries,
                        ]
                        for c in diffs
                    ],
                    "component_holes": sorted(
                        [list(k) + [sorted(v)] for k, v in component_holes.items()]
                    ),
                    "component_driven": component_driven,
                    "component_unrendered": component_unrendered,
                    "reached": sorted(
                        {
                            (c.tag, c.param)
                            for c in sweep.cells
                            if c.label == "https" and c.python_carries
                        }
                    ),
                    "rendered_both": sum(1 for c in sweep.cells if c.rendered_both),
                    "cells": len(sweep.cells),
                    "driven": sweep.driven,
                    "no_params": sweep.no_params,
                    "unrendered": sweep.unrendered,
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
        )
    return 1 if (holes or component_holes) else 0


if __name__ == "__main__":
    sys.exit(main())
