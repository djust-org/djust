#!/usr/bin/env python3
"""Parity ratchet: djust's own template libraries, Django engine vs Rust engine (#3663).

``djust_components`` and ``theme_tags`` can each render a tag two ways: through
Django's engine (the Python tag) or through ``DjustTemplateBackend``, where a
registered native handler wins. The two are independent implementations. This
tool renders the same templates both ways for every tag that has a native
handler, classifies each tag, and compares the table with the checked-in
baseline ``scripts/template-library-parity-baseline.csv``::

    scripts/template-library-parity.py                     check (what CI runs)
    scripts/template-library-parity.py --write-baseline    regenerate the baseline
    scripts/template-library-parity.py --repro TAG         print both outputs for TAG
    scripts/template-library-parity.py --self-compare      gate-off: Django vs Django
    scripts/template-library-parity.py --plant TAG         gate-off: plant a Python-only diff

Exit codes: 0 the table equals the baseline (or the gate-off held); 1 it does
not — a tag got WORSE (a regression), got BETTER (regenerate the baseline so
the gain is locked in), has no row yet, or lost its row; 2 the harness could
not run.

The ratchet is exact in both directions on purpose. A baseline that only
guarded against regressions would let a fixed tag slide back to its old level
unnoticed; here the baseline can only ever shrink, and every shrink is a
reviewed diff of one CSV.

The harness replaces ``uuid`` and ``datetime`` for its whole process, so it
must be the only thing running in it. ``make template-library-parity`` wraps
this; the tests in ``python/tests/test_template_library_parity_3663.py`` start
it as a subprocess.
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

from scripts.lib.template_library_parity import classify, harness  # noqa: E402

DEFAULT_BASELINE = _REPO_ROOT / "scripts" / "template-library-parity-baseline.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", type=Path, help="also write the measured table here")
    parser.add_argument(
        "--only",
        help="comma-separated tags (bare name or <library>:<name>); compares only those",
    )
    parser.add_argument("--quiet", action="store_true", help="print only findings")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write-baseline", action="store_true")
    mode.add_argument("--repro", metavar="TAG", help="print every variant's source and outputs")
    parser.add_argument(
        "--self-compare",
        action="store_true",
        help="gate-off: render Django against itself; every tag must be IDENTICAL",
    )
    parser.add_argument(
        "--plant",
        metavar="TAG",
        help="gate-off: make the Python tag render one extra element; the ratchet must flag it",
    )
    parser.add_argument("--variant", help="with --repro: only this variant")
    return parser


def _measure(args: argparse.Namespace, only: set[str] | None) -> harness.Audit:
    harness.configure_django()
    return harness.run_audit(only=only, plant=args.plant, self_compare=args.self_compare)


def _write_json(path: Path, audit: harness.Audit) -> None:
    payload = {
        "rows": {
            tag: dict(zip(("class", "aspects", "errors", "compared"), row.as_fields()))
            for tag, row in sorted(audit.rows.items())
        },
        "unmeasured": sorted(audit.unmeasured),
    }
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _repro(audit: harness.Audit, tag: str, variant: str | None) -> int:
    matches = [k for k in audit.detail if k == tag or k.split(":", 1)[1] == tag]
    if not matches:
        print("no measured tag %r (a tag needs a native handler to be compared)" % tag)
        return 2
    for key in matches:
        print("== %s: %s" % (key, audit.rows[key].as_fields()[0]))
        for record in audit.detail[key]:
            if variant and record.variant != variant:
                continue
            print("-- %s [%s]\n%s" % (record.variant, record.cls, record.src))
            print("   python (%s): %s" % record.python)
            print("   rust   (%s): %s" % record.rust)
    return 0


def _self_compare(audit: harness.Audit) -> int:
    off = {k: r for k, r in audit.rows.items() if r.tag_class != classify.IDENTICAL}
    for key, row in sorted(off.items()):
        print("%s: harness difference with Django against itself: %s" % (key, row.as_fields()))
    if off:
        print(
            "FAIL: %d of %d tags differ from themselves; the harness is not deterministic"
            % (len(off), len(audit.rows))
        )
        return 1
    print("gate-off held: all %d tags IDENTICAL with Django against itself" % len(audit.rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write_baseline and (args.only or args.plant or args.self_compare):
        print("refusing to write a baseline from a restricted, planted or self-compared run")
        return 2
    only = set(filter(None, (args.only or "").split(","))) or None
    harness.install_determinism()
    try:
        audit = _measure(args, only)
    except Exception:  # noqa: BLE001 - exit 2 ("could not run"), distinct from 1 ("differs")
        traceback.print_exc()
        return 2
    if not audit.rows:
        print("no tag was measured (--only matched nothing, or no native handler is registered)")
        return 2
    if args.json:
        _write_json(args.json, audit)
    if args.repro:
        return _repro(audit, args.repro, args.variant)
    if args.self_compare:
        return _self_compare(audit)
    if args.write_baseline:
        classify.write_baseline(args.baseline, audit.rows)
        print(
            "wrote %s: %d tags %s"
            % (args.baseline, len(audit.rows), classify.summarize(audit.rows))
        )
        return 0
    findings = classify.compare(
        audit.rows, classify.read_baseline(args.baseline), partial=only is not None
    )
    if not args.quiet:
        print("measured %d tags %s" % (len(audit.rows), classify.summarize(audit.rows)))
    for finding in findings:
        print(finding)
    if findings:
        print("FAIL: %d tag(s) differ from %s" % (len(findings), args.baseline.name))
        return 1
    if not args.quiet:
        print("OK: every measured tag matches %s" % args.baseline.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
