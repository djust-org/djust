#!/usr/bin/env python3
"""Strict-CSP inline sweep: every djust template tag and component, both engines, both modes (#4139).

Renders every tag of djust's template libraries (the parity harness's calls) through Django's
engine and ``DjustTemplateBackend``, and every component class under each ``css_framework``, with
``LIVEVIEW_CONFIG["csp_strict_styles"]`` off and on and a request carrying a CSP nonce, and reports
every ``style`` attribute, every ``<style>`` and every executable inline ``<script>`` without that
nonce. See ``scripts/lib/template_library_parity/csp_sweep.py``.

    scripts/template-library-csp-sweep.py                  print the findings
    scripts/template-library-csp-sweep.py --json out.json  also write the measurement
    scripts/template-library-csp-sweep.py --only data_table restrict to some tags or classes
    scripts/template-library-csp-sweep.py --plant djust_components:badge   gate-off

Exit codes: 0 the run completed (the judgement is the test's,
``python/djust/tests/test_csp_constant_styles_4139.py``); 2 the harness could not run.

Like ``template-library-parity.py`` this configures Django for its whole process, so it must be
the only thing running in it; the test starts it as a subprocess.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib.template_library_parity import harness  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", type=Path)
    parser.add_argument("--only", help="comma-separated tags or component classes")
    parser.add_argument("--plant", metavar="LIB:TAG", help="gate-off: plant an inline style")
    args = parser.parse_args(argv)
    try:
        harness.configure_django()
        from scripts.lib.template_library_parity import csp_sweep

        only = {x.strip() for x in args.only.split(",")} if args.only else None
        sweep = csp_sweep.run_sweep(only=only, plant=args.plant)
    except Exception:  # noqa: BLE001 - report and exit 2, the harness could not run
        traceback.print_exc()
        return 2
    found = [r for r in sweep.renders if r.findings]
    for r in found:
        for kind, detail in r.findings:
            print("%s %s %s [%s] %s: %s" % (r.mode, r.engine, r.key, r.variant, kind, detail))
    print(
        "%d renders, %d with findings" % (len(sweep.renders), len(found)),
        file=sys.stderr,
    )
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "renders": [dataclasses.asdict(r) for r in sweep.renders],
                    "rendered": {
                        "%s|%s" % key: sorted(value) for key, value in sweep.rendered.items()
                    },
                    "inventory": sweep.inventory,
                    "skipped": sweep.skipped,
                    "probe": sweep.probe,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
