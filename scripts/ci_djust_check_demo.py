#!/usr/bin/env python3
"""CI gate: dogfood ``djust_check`` against the demo project (#1708).

Background
----------
#1683 shipped dead ``@click`` buttons (deprecated attribute syntax) to the 1.0
GA demo even though the ``djust.T001`` system check existed — because the demo
templates were never run through ``djust_check`` in CI. This script wires that
check into CI mechanically (CLAUDE.md #1060: "dogfood CLI tools against the
demo").

Why a wrapper (and not bare ``djust_check``)
--------------------------------------------
``manage.py djust_check`` ALWAYS exits 0 — its ``handle()`` only prints results
and returns, with no exit-code logic. So a bare invocation can never fail CI.
This wrapper runs ``djust_check --json`` as a subprocess, parses the summary,
and sets the process exit code.

Scope of the gate (WORKFLOW-HEADER claim, #1244)
------------------------------------------------
The gate fails (exit 1) on, and ONLY on:

  * any ERROR-severity djust check (errors are never intentional), AND
  * any *deprecated-attribute* finding — check IDs ``djust.T001`` (deprecated
    ``@click``/``@input``), ``djust.T014`` (deprecated ``data-dj-id``), and
    ``djust.T015`` (legacy root attributes). This is exactly the #1683 / #1697
    bug class.

It deliberately does NOT fail on the demo's other warnings/info (e.g. S005
public-view-without-auth, T012 partial fragments, V004 informational) — those
are intentional demo states, and failing on them would make the demo
perpetually red. Per Action #1079, this fixes exactly what #1708 cites.

Usage
-----
Run from the demo project directory (``examples/demo_project``)::

    PYTHONPATH=<repo-root> python scripts/ci_djust_check_demo.py

Or pass the demo directory explicitly::

    python scripts/ci_djust_check_demo.py --demo-dir examples/demo_project
"""

import argparse
import json
import subprocess
import sys

# Deprecated-attribute / dead-binding check IDs — the #1683 / #1697 bug class.
DEPRECATED_ATTR_IDS = frozenset({"djust.T001", "djust.T014", "djust.T015"})


def _extract_json(stdout):
    """Return the parsed JSON body from djust_check --json stdout.

    The demo runs with DEBUG=True, so hot-reload prints a ``[HotReload] ...``
    line to stdout before the JSON. Locate the first line that begins a JSON
    object and parse from there.
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("{"):
            return json.loads("\n".join(lines[i:]))
    raise ValueError("no JSON object found in djust_check output")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--demo-dir",
        default=".",
        help="Demo project directory containing manage.py (default: cwd)",
    )
    args = parser.parse_args(argv)

    proc = subprocess.run(
        [sys.executable, "manage.py", "djust_check", "--json"],
        cwd=args.demo_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # djust_check itself errored (e.g. import/config failure) — surface it.
        print("ERROR: `manage.py djust_check --json` exited %d" % proc.returncode)
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        return 1

    try:
        data = _extract_json(proc.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        print("ERROR: could not parse djust_check JSON output: %s" % exc)
        print(proc.stdout)
        return 1

    checks = data.get("checks", [])
    summary = data.get("summary", {})

    errors = [c for c in checks if c.get("severity") == "error"]
    deprecated = [c for c in checks if c.get("id") in DEPRECATED_ATTR_IDS]

    print(
        "djust_check (demo): %d total, %d error(s), %d warning(s), %d info"
        % (
            summary.get("total", len(checks)),
            summary.get("errors", len(errors)),
            summary.get("warnings", 0),
            summary.get("info", 0),
        )
    )

    blocking = errors + [c for c in deprecated if c not in errors]
    if blocking:
        print("")
        print(
            "FAIL: %d blocking finding(s) (errors + deprecated-attribute "
            "classes T001/T014/T015):" % len(blocking)
        )
        for c in blocking:
            print("  - %s [%s] %s" % (c.get("id"), c.get("severity"), c.get("message")))
        print("")
        print("This is the #1683 dead-@click bug class. Fix the templates above.")
        return 1

    print("OK: no errors and no deprecated-attribute findings (T001/T014/T015).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
