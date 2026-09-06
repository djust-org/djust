"""No test fixture may send itself a signal macOS writes a crash report for.

`test_django_template_suite_2517.py` used to have its crash-isolation child
`os.kill(os.getpid(), signal.SIGSEGV)`. That is a real, deliberate death and
the runner handled it correctly — but on macOS every SIGSEGV/SIGBUS/SIGABRT is
a fatal-exception signal, so ReportCrash writes a full `.ips` report per death
to `~/Library/Logs/DiagnosticReports` (37 of them in one day here). Real
crashes then hide in fixture noise, which is the actual cost.

The runner branches on `returncode < 0` and never inspects which signal
(`scripts/run-django-template-suite.py`), so SIGKILL exercises the identical
path and is not reported. This pins the choice: a future fixture that reaches
for SIGSEGV to "be realistic" buys nothing the runner can see and re-creates
the noise.

Scoped to the SENDING side. Naming these signals in a comparison, a comment,
or a detection set (`test_adr027_characterization_net_2539.py`'s
`CRASH_SIGNALS`) is exactly right and must stay allowed.
"""

from __future__ import annotations

import pathlib
import re

# `os.kill(..., signal.SIGSEGV)` / `.SIGABRT` / `.SIGBUS`, and the raw numbers.
# `[^)]*?` was the first attempt and it could not cross the `)` in
# `os.getpid()` — so it missed the very line this pin exists for. Matched
# within one line instead.
SENDER_RE = re.compile(
    r"os\.kill\s*\(.*?(?:signal\.SIG(?:SEGV|ABRT|BUS)\b|,\s*(?:11|6|10)\s*\))",
)
ROOTS = ("python/tests", "python/djust/tests", "tests", "scripts")


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def test_no_fixture_sends_itself_a_reported_crash_signal() -> None:
    root = _repo_root()
    offenders = []
    for rel in ROOTS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or path.name == pathlib.Path(__file__).name:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if SENDER_RE.search(line):
                    offenders.append(f"{path.relative_to(root)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "these send a signal macOS writes a crash report for; use signal.SIGKILL "
        "— the runner branches on `returncode < 0` and cannot tell the "
        "difference (#2699):\n  " + "\n  ".join(offenders)
    )


def test_the_pin_can_actually_fire() -> None:
    """#1459: the canary must catch the shape it claims to catch."""
    assert SENDER_RE.search("        os.kill(os.getpid(), signal.SIGSEGV)")
    assert SENDER_RE.search("os.kill(pid, signal.SIGABRT)")
    assert SENDER_RE.search("os.kill(pid, 11)")
    # and must not fire on the legitimate non-sending uses
    assert not SENDER_RE.search("CRASH_SIGNALS = {-signal.SIGSEGV, -signal.SIGBUS}")
    assert not SENDER_RE.search("os.kill(pid, signal.SIGKILL)")
    assert not SENDER_RE.search("# a SIGSEGV inside ZSTD_decompressSequencesLong")
