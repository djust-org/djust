#!/usr/bin/env python3
"""Run Django's own ``template_tests`` against djust's Rust engine — #2517.

The v1.2.0 conformance scoreboard. Clones ``django/django`` at the tag
matching the installed ``django.__version__`` into a gitignored cache,
routes every ``Engine`` the suite builds through ``DjustTemplate``, runs the
suite with Django's own ``tests/runtests.py``, and prints one line per test
plus::

    Django test suite passing: NN.NN%
    NNN ERROR / NN FAIL / NNN OK   (N tests exercised the djust engine; N skipped)
    whole label: NN.NN%  (...)
    harness integrity: 0 untouched tests failed
    crashes isolated: N (listed above as ERROR: process crashed)

ERROR (a crash, an unsupported tag, an attribute the adapter cannot express)
and FAIL (wrong output, an exception Django raises and djust does not) are
different work and are counted separately. The headline is the subset of
tests that actually reached the engine; the whole label is printed too.

A segfault in the child kills it mid-suite (the Rust engine does, today, on
a ``DjustTemplate`` placed in a context — #2516). The child records a
flushed JSON line per test, so the outer loop knows the in-flight test id,
records it as ``ERROR … process crashed (signal N)``, adds it plus every
finished id to a skip list, and relaunches. Nothing after a crash is lost.

A crash with NO test in flight is different: the child died in a class or
module ``setUpClass`` / ``tearDownClass`` (Django's runner imports every
module up front, so an import-time crash shows as "before any test
started"). The recorder has no test id to attribute it to, so the loop
does not relaunch — it reports how many tests had finished, exits 2, and
the caller isolates the module with ``--label``.

The tag (``--django-tag``) is validated before any filesystem access — no
``/``, ``..``, whitespace or a leading ``-`` — and the checkout paths are
asserted to lie inside ``--cache-dir``. Labels starting with ``-`` are
refused, and labels are passed after a literal ``--`` to both the child and
``runtests.py``, so a label can never be read as an option.

Usage::

    scripts/run-django-template-suite.py [run] [--label L ...] [--parsed-output F]
                                         [--json F] [--write-baseline] [--gate-off] ...
    scripts/run-django-template-suite.py compare --json RUN.json [--baseline B.json]

Exit codes — ``run``: 0 ran to completion (any percentage: the ratchet is
NOT enforced here); 2 could not run (clone failed, tag mismatch, the child
produced no records, too many restarts). ``compare``: 0 no drop, 1 a drop,
2 a file is missing or unreadable.

``make django-template-suite`` wraps ``run`` with the artifact paths CI
uploads. The thin CLI lives here; the code is importable from
``scripts/lib/django_template_suite/``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.lib.django_template_suite import report  # noqa: E402

CHILD = _REPO_ROOT / "scripts" / "lib" / "django_template_suite" / "child.py"
DEFAULT_BASELINE = _REPO_ROOT / "scripts" / "django-template-suite-baseline.json"
DEFAULT_CACHE = _REPO_ROOT / ".django-src"
DJANGO_GIT = "https://github.com/django/django.git"
STDERR_TAIL_LINES = 25


def _say(message: str, *, quiet: bool = False) -> None:
    if not quiet:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()


# --------------------------------------------------------------------------- #
# the checkout
# --------------------------------------------------------------------------- #


def installed_django_version() -> str:
    import django

    return django.__version__


def checkout_version(django_src: Path) -> str | None:
    """The version a checkout's ``django/__init__.py`` declares, or None."""
    init = django_src / "django" / "__init__.py"
    if not init.is_file():
        return None
    match = re.search(r"^VERSION\s*=\s*\(([^)]*)\)", init.read_text(encoding="utf-8"), re.M)
    if not match:
        return None
    parts = [p.strip().strip("'\"") for p in match.group(1).split(",") if p.strip()]
    try:
        version = (int(parts[0]), int(parts[1]), int(parts[2]), parts[3], int(parts[4]))
    except (IndexError, ValueError):
        return None
    from django.utils.version import get_version

    return get_version(version)


def validate_tag(tag: str) -> str | None:
    """Why ``tag`` is unusable as a checkout directory name, or None if it is fine.

    Runs BEFORE any filesystem access: ``ensure_checkout`` joins the tag onto
    the cache dir and removes ``<tag>.partial``, so a tag such as ``../../x``
    would delete outside the cache before the clone even failed.
    """
    if not tag:
        return "empty tag"
    if tag.startswith("-"):
        return "tag %r starts with '-' (would be read as a git option)" % tag
    if "/" in tag or "\\" in tag or ".." in tag:
        return "tag %r contains a path separator or '..'" % tag
    if any(ch.isspace() for ch in tag):
        return "tag %r contains whitespace" % tag
    return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def ensure_checkout(tag: str, cache_dir: Path, *, quiet: bool) -> Path | None:
    """``<cache>/<tag>`` with a ``tests/runtests.py``; cloned shallow if absent."""
    problem = validate_tag(tag)
    if problem is not None:
        _say("refusing to check out: %s" % problem)
        return None
    dest = cache_dir / tag
    partial = cache_dir / ("%s.partial" % tag)
    if not (_inside(dest, cache_dir) and _inside(partial, cache_dir)):
        _say("refusing to check out: %s would land outside %s" % (dest, cache_dir))
        return None
    if (dest / "tests" / "runtests.py").is_file():
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    _say("cloning django/django at tag %s into %s ..." % (tag, dest), quiet=quiet)
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 — list argv, no shell; the tag is validated above
        ["git", "clone", "--quiet", "--depth", "1", "--branch", tag, DJANGO_GIT, str(partial)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        shutil.rmtree(partial, ignore_errors=True)
        _say("clone failed (rc=%d):\n%s" % (proc.returncode, proc.stderr.strip()))
        return None
    partial.rename(dest)
    _say("cloned in %.1f s" % (time.perf_counter() - started), quiet=quiet)
    return dest


# --------------------------------------------------------------------------- #
# the crash / timeout loop
# --------------------------------------------------------------------------- #


def _tail(path: Path, lines: int = STDERR_TAIL_LINES) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def _child_argv(args: argparse.Namespace, django_src: Path | None) -> list[str]:
    argv = [sys.executable, "-X", "faulthandler", str(CHILD)]
    if args.discover_root is not None:
        argv += ["discover", "--root", str(args.discover_root)]
    else:
        argv += ["runtests", "--django-src", str(django_src)]
    if args.gate_off:
        argv.append("--gate-off")
    # After ``--`` argparse reads everything as positional: a label can never
    # be smuggled in as an option (``--label=--gate-off``).
    argv += ["--", *args.labels]
    return argv


def run_children(
    args: argparse.Namespace, django_src: Path | None, run_root: Path
) -> tuple[list[dict], int, int]:
    """Launch the child until it exits normally; resume per test on a crash.

    Returns ``(records, restarts, status)`` where status is 0 or 2.
    """
    out_path = run_root / "records.jsonl"
    skip_path = run_root / "skip-ids.txt"
    restarts = 0
    victims: list[str] = []
    env = dict(os.environ)
    env["DJUST_SUITE_OUT"] = str(out_path)
    env["DJUST_SUITE_SKIP_IDS"] = str(skip_path)
    env["TMPDIR"] = str(run_root)
    env["PYTHONUNBUFFERED"] = "1"
    if django_src is not None:
        # The recorder rewrites this root to ``<django-src>`` in messages so
        # two runs from different checkouts (CI, a laptop) diff cleanly.
        env["DJUST_SUITE_SRC"] = str(django_src)
    argv = _child_argv(args, django_src)

    while True:
        log_path = run_root / ("child-%d.log" % (restarts + 1))
        _say("child #%d: %s" % (restarts + 1, " ".join(argv[3:])), quiet=args.quiet)
        timed_out = False
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    argv,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=str(_REPO_ROOT),
                    timeout=args.timeout,
                    check=False,
                )
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                returncode = None

        records = report.load_records(out_path)
        finished = set(report.results_by_id(records))
        in_flight = report.in_flight_ids(records)
        victim = in_flight[-1] if in_flight else None

        if not timed_out and returncode is not None and returncode >= 0:
            _say("child #%d exited rc=%d" % (restarts + 1, returncode), quiet=args.quiet)
            if victim is not None:
                # A normal exit mid-test is not a crash (SystemExit from a
                # test, a runner bug); record it and stop rather than loop.
                _say(
                    "WARNING: child exited rc=%d with %s in flight; recorded as ERROR, not relaunching"
                    % (returncode, victim)
                )
                records.append(
                    _crash_record(victim, "process exited (rc %d) mid-test" % returncode, log_path)
                )
            return records, restarts, 0

        if timed_out:
            reason = "process timed out after %d s" % args.timeout
        else:
            reason = "process crashed (signal %d)" % -returncode
        if victim is None:
            if not finished:
                _say(
                    "child #%d died (%s) before any test started:\n%s"
                    % (restarts + 1, reason, _tail(log_path))
                )
            else:
                # Django's runner imports every module up front, so with N
                # tests finished the crash is in a class/module setUp or
                # tearDown — nothing the recorder can attribute to a test id.
                _say(
                    "child #%d died (%s) between tests after %d finished; the crash is in "
                    "class/module setup or teardown, which the recorder cannot attribute "
                    "to a test; stopping — rerun with --label <module> to isolate:\n%s"
                    % (restarts + 1, reason, len(finished), _tail(log_path))
                )
            return records, restarts, 2
        if victim in victims:
            _say(
                "child #%d died on %s again — the skip list was not honoured"
                % (restarts + 1, victim)
            )
            return records, restarts, 2
        _say("child #%d died: %s, in flight: %s" % (restarts + 1, reason, victim), quiet=args.quiet)
        victims.append(victim)
        records.append(_crash_record(victim, reason, log_path))
        with open(out_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(records[-1]) + "\n")
        with open(skip_path, "w", encoding="utf-8") as handle:
            for test_id in sorted(finished | set(victims)):
                handle.write(test_id + "\n")
        restarts += 1
        if restarts > args.max_restarts:
            _say("max restarts (%d) reached; giving up" % args.max_restarts)
            return records, restarts, 2


def _crash_record(test_id: str, reason: str, log_path: Path) -> dict:
    return {
        "event": "result",
        "id": test_id,
        "status": "ERROR",
        "message": reason,
        # A crash is attributed to the engine by construction: only native
        # code (the Rust extension) can kill the process mid-test, and a
        # test that never reached it cannot segfault. Recording it as
        # touched puts it in the headline bucket and keeps it out of the
        # "untouched tests failed" harness-integrity check.
        "touched": True,
        "ms": 0.0,
        "crash": True,
        "detail": _tail(log_path),
    }


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def baseline_refusal(*, gate_off: bool, discover: bool, engine_ran: int) -> str | None:
    """Why ``--write-baseline`` must not write from this run, or None."""
    if gate_off or discover:
        return "refusing to write a baseline from a --gate-off or --discover-root run"
    if engine_ran == 0:
        return (
            "refusing to write a baseline: no test reached the djust engine (ran == 0) — "
            "the adapter installed but measured nothing"
        )
    return None


def cmd_run(args: argparse.Namespace) -> int:
    bad_labels = [label for label in args.labels if label.startswith("-")]
    if bad_labels:
        _say("refusing label(s) that look like options: %s" % ", ".join(bad_labels))
        return 2
    django_version = installed_django_version()
    tag = args.django_tag or django_version
    problem = validate_tag(tag)
    if problem is not None:
        _say("refusing to run: %s" % problem)
        return 2
    django_src: Path | None = None

    if args.discover_root is None:
        if args.django_src is not None:
            django_src = args.django_src
            if not (django_src / "tests" / "runtests.py").is_file():
                _say("no tests/runtests.py under %s" % django_src)
                return 2
        else:
            django_src = ensure_checkout(tag, args.cache_dir, quiet=args.quiet)
            if django_src is None:
                return 2
        found = checkout_version(django_src)
        if found != django_version:
            message = "checkout at %s is Django %s; the installed Django is %s" % (
                django_src,
                found,
                django_version,
            )
            if args.django_tag is None and args.django_src is None:
                _say("ERROR: %s" % message)
                return 2
            _say("WARNING: %s (untouched tests measure the installed Django)" % message)
        if not args.labels:
            args.labels = ["template_tests"]
    elif not args.labels:
        _say("--discover-root needs at least one --label")
        return 2

    run_root = Path(tempfile.mkdtemp(prefix="djust-suite-run-"))
    started = time.perf_counter()
    try:
        records, restarts, status = run_children(args, django_src, run_root)
    finally:
        keep = os.environ.get("DJUST_SUITE_KEEP_TEMP")
        if not keep:
            shutil.rmtree(run_root, ignore_errors=True)
    wall = time.perf_counter() - started

    results = report.results_by_id(records)
    if not results:
        _say("the child produced no test records; nothing to report")
        return 2

    summary = report.summarize(records)
    per_test = [report.format_per_test_line(results[test_id]) for test_id in sorted(results)]
    summary_lines = report.format_summary(summary)
    _say("%d tests, %d restarts, %.1f s" % (len(results), restarts, wall), quiet=args.quiet)

    if args.parsed_output is not None:
        args.parsed_output.parent.mkdir(parents=True, exist_ok=True)
        args.parsed_output.write_text(
            "\n".join([*per_test, "", *summary_lines]) + "\n", encoding="utf-8"
        )
    if not args.quiet:
        for line in per_test:
            print(line)
        print()
    for line in summary_lines:
        print(line)

    result = report.build_result(summary, django_version=django_version, tag=tag)
    if args.json is not None:
        run_data = dict(result)
        run_data.update(
            {
                "labels": list(args.labels),
                "gate_off": bool(args.gate_off),
                "restarts": restarts,
                "wall_seconds": round(wall, 1),
                "tests": [
                    {
                        "id": test_id,
                        "status": rec["status"],
                        "message": rec.get("message", ""),
                        "touched": bool(rec.get("touched")),
                        "ms": rec.get("ms", 0.0),
                        "crash": bool(rec.get("crash")),
                    }
                    for test_id, rec in sorted(results.items())
                ],
            }
        )
        report.write_json(args.json, run_data)
    if args.write_baseline:
        refusal = baseline_refusal(
            gate_off=bool(args.gate_off),
            discover=args.discover_root is not None,
            engine_ran=int(result["ran"]),
        )
        if refusal is not None:
            _say(refusal)
            return 2
        report.write_json(args.baseline, result)
        _say("baseline written to %s" % args.baseline, quiet=args.quiet)
    return status


def _load_json(path: Path, label: str) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _say("cannot read %s %s: %s" % (label, path, exc))
        return None


def cmd_compare(args: argparse.Namespace) -> int:
    baseline = _load_json(args.baseline, "baseline")
    current = _load_json(args.json, "run")
    if baseline is None or current is None:
        return 2
    code, lines = report.compare(baseline, current)
    for line in lines:
        print(line)
    return code


# --------------------------------------------------------------------------- #
# argv
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run-django-template-suite.py",
        description="Run Django's own template_tests against djust's Rust engine (#2517).",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run the suite (the default command)")
    run.add_argument(
        "--django-tag", help="Django tag to check out (default: the installed version)"
    )
    run.add_argument(
        "--django-src", type=Path, help="an existing Django checkout (skips the clone)"
    )
    run.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="checkout cache (default: .django-src/)",
    )
    run.add_argument(
        "--label",
        dest="labels",
        action="append",
        default=[],
        help="test label (repeatable; default template_tests)",
    )
    run.add_argument("--parsed-output", type=Path, help="write the per-test lines + summary here")
    run.add_argument("--json", type=Path, help="write the run's summary + per-test records here")
    run.add_argument(
        "--baseline", type=Path, default=DEFAULT_BASELINE, help="baseline path for --write-baseline"
    )
    run.add_argument(
        "--write-baseline", action="store_true", help="write the baseline file from this run"
    )
    run.add_argument(
        "--timeout", type=int, default=600, help="seconds per child process (default 600)"
    )
    run.add_argument(
        "--max-restarts",
        type=int,
        default=50,
        help="crash relaunches before giving up (default 50)",
    )
    run.add_argument(
        "--gate-off", action="store_true", help="do not install the adapter: Django against itself"
    )
    run.add_argument(
        "--discover-root",
        type=Path,
        help="run a synthetic test package under this dir instead of the checkout",
    )
    run.add_argument("--quiet", action="store_true", help="only print the summary")
    run.set_defaults(func=cmd_run)

    compare = sub.add_parser(
        "compare", help="the ratchet: compare a run's --json against the baseline"
    )
    compare.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="the stored baseline to ratchet against (default: %s)"
        % DEFAULT_BASELINE.relative_to(_REPO_ROOT),
    )
    compare.add_argument("--json", type=Path, required=True, help="a run's --json output")
    compare.set_defaults(func=cmd_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"run", "compare", "-h", "--help"}:
        argv.insert(0, "run")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
