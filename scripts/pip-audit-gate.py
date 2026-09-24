#!/usr/bin/env python3
"""Blocking pip-audit gate over every package pinned in ``uv.lock``.

Run by the "Python Security Scan" job of
``.github/workflows/pre-release-security-audit.yml``; publishing to PyPI needs
that job to pass. Exits non-zero when pip-audit finds a known vulnerability
that is not in the reviewed allowlist, or when the allowlist itself is invalid
(an entry without a reason, or whose review-by date has passed).

What is audited: the resolved lock, not an installed environment. The lock is
exported with every extra (so ``dev`` and each optional backend are included)
and without the project itself — djust is not on PyPI until the release this
audit gates has been published, so pip-audit could not look it up. Environment
markers are stripped before auditing: pip-audit would otherwise evaluate them
against its own interpreter and silently skip the packages the lock pins only
for other Python versions or platforms (e.g. the Python 3.10 autobahn fork).

Usage:
    python scripts/pip-audit-gate.py                     # audit uv.lock
    python scripts/pip-audit-gate.py --report out.md     # also write a report
    python scripts/pip-audit-gate.py --requirements r.txt  # audit a pinned file

Environment:
    PIP_AUDIT   command used to run pip-audit (default: ``uvx --from
                'pip-audit>=2.10,<3' pip-audit``).

On a machine whose global uv config points at a local mirror, export
``UV_INDEX_URL=https://pypi.org/simple`` first.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = REPO / ".github" / "security" / "pip-audit-ignore.txt"
DEFAULT_PIP_AUDIT = "uvx --from 'pip-audit>=2.10,<3' pip-audit"

ENTRY_RE = re.compile(r"^(?P<id>[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9-]+)\s+#\s*(?P<reason>.+)$")
REVIEW_RE = re.compile(r"review-by\s+(?P<date>\d{4}-\d{2}-\d{2})\s*$")
PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")


def load_allowlist(path: Path, today: dt.date) -> tuple[list[str], list[str]]:
    """Return (ids, errors). Every entry needs a reason and a live review-by date."""
    ids: list[str] = []
    errors: list[str] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENTRY_RE.match(line)
        if not m:
            errors.append(
                f"{path.name}:{lineno}: expected '<ID>  # <reason>; review-by YYYY-MM-DD', got {line!r}"
            )
            continue
        review = REVIEW_RE.search(m["reason"])
        if not review:
            errors.append(
                f"{path.name}:{lineno}: {m['id']} has no 'review-by YYYY-MM-DD' at the end of its reason"
            )
            continue
        try:
            due = dt.date.fromisoformat(review["date"])
        except ValueError:
            errors.append(
                f"{path.name}:{lineno}: {m['id']} has an invalid review-by date {review['date']!r}"
            )
            continue
        if due < today:
            errors.append(
                f"{path.name}:{lineno}: {m['id']} review-by {due} has passed — re-review the exception "
                "(remove it if a fix is locked, or extend the date with a fresh reason)"
            )
            continue
        if m["id"] in ids:
            errors.append(f"{path.name}:{lineno}: duplicate entry {m['id']}")
            continue
        ids.append(m["id"])
    return ids, errors


def lock_requirements() -> str:
    """Export uv.lock (all extras, without djust) as requirement lines."""
    out = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--all-extras",
            "--no-emit-project",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
            "--format",
            "requirements-txt",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def pinned(requirements: str) -> list[str]:
    """Reduce requirement lines to unique ``name==version`` pins, markers dropped."""
    pins: set[str] = set()
    for line in requirements.splitlines():
        m = PIN_RE.match(line.strip())
        if m:
            pins.add(f"{m['name'].lower()}=={m['version']}")
    return sorted(pins)


def split_layers(pins: list[str]) -> list[list[str]]:
    """Split pins so no layer holds two versions of one package.

    pip-audit refuses two versions of one package in a single requirements
    file, and the lock legitimately pins several (one per Python/platform
    fork). The Nth version of each package goes in layer N; each layer is
    audited separately.
    """
    layers: list[list[str]] = []
    seen: dict[str, int] = {}
    for pin in pins:
        name = pin.split("==", 1)[0]
        depth = seen.get(name, 0)
        seen[name] = depth + 1
        if depth == len(layers):
            layers.append([])
        layers[depth].append(pin)
    return layers


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument(
        "--requirements", type=Path, help="audit this pinned file instead of exporting uv.lock"
    )
    parser.add_argument("--report", type=Path, help="write the pip-audit output here as markdown")
    args = parser.parse_args()

    ids, errors = load_allowlist(args.allowlist, dt.date.today())
    for err in errors:
        print(f"::error::{err}")
    if errors:
        return 2

    source = args.requirements.read_text() if args.requirements else lock_requirements()
    pins = pinned(source)
    if not pins:
        print("::error::no pinned requirements found to audit")
        return 2

    layers = split_layers(pins)

    base_cmd = shlex.split(os.environ.get("PIP_AUDIT", DEFAULT_PIP_AUDIT)) + [
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--progress-spinner",
        "off",
    ]
    for vuln_id in ids:
        base_cmd += ["--ignore-vuln", vuln_id]

    print(
        f"Auditing {len(pins)} pinned packages in {len(layers)} pass(es); "
        f"allowlisted: {', '.join(ids) or 'none'}"
    )
    failed = False
    outputs: list[str] = []
    for n, layer in enumerate(layers, 1):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(layer) + "\n")
            req_file = fh.name
        try:
            result = subprocess.run(base_cmd + ["-r", req_file], capture_output=True, text=True)
        finally:
            Path(req_file).unlink(missing_ok=True)
        text = (result.stdout + result.stderr).strip()
        outputs.append(f"# pass {n}/{len(layers)}: {len(layer)} packages\n{text}")
        failed = failed or result.returncode != 0
    output = "\n\n".join(outputs)
    print(output)

    if args.report:
        args.report.write_text(
            "## pip-audit Results\n\n"
            f"Audited {len(pins)} packages pinned in uv.lock (all extras, all markers; djust itself excluded).\n"
            f"Allowlisted (`.github/security/pip-audit-ignore.txt`): {', '.join(ids) or 'none'}\n\n"
            f"```\n{output}\n```\n"
        )

    if failed:
        print(
            "::error::pip-audit found a known vulnerability that is not allowlisted (or could not audit a "
            "package). Upgrade it in uv.lock, or add a reviewed entry to .github/security/pip-audit-ignore.txt."
        )
        return 1
    print("OK: no known vulnerabilities outside the reviewed allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
