"""The subprocess entry point — the only process the adapter is installed in.

Two modes::

    child.py runtests --django-src DIR [--gate-off] [LABEL ...]
    child.py discover --root DIR [--gate-off] LABEL [LABEL ...]

``runtests`` puts the checkout's ``tests/`` and the repo root on ``sys.path``,
installs the adapter (unless ``--gate-off``), and hands off to the checkout's
own ``tests/runtests.py`` with ``--settings`` pointing at this package's
settings module. Going through ``runtests.py`` rather than reimplementing it
keeps ``ALWAYS_INSTALLED_APPS``, ``ROOT_URLCONF``, the warnings-as-errors
filters and the ``TMPDIR`` handling in step with whatever the tag ships.

``discover`` configures minimal settings and runs ``DjustSuiteRunner``
directly over a synthetic test package — for the tool's own tests, no
checkout needed.

Both modes honour ``$DJUST_SUITE_OUT`` (the JSON-lines sink) and
``$DJUST_SUITE_SKIP_IDS`` (ids to drop from the suite), set by the outer
loop in ``scripts/run-django-template-suite.py``.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SETTINGS_MODULE = "scripts.lib.django_template_suite.settings"


def _ensure_repo_on_path() -> None:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))


def _install_unless_gate_off(gate_off: bool) -> None:
    if gate_off:
        return
    from scripts.lib.django_template_suite import adapter

    adapter.install()


def run_runtests(django_src: Path, labels: list[str], *, gate_off: bool) -> int:
    """Hand off to the checkout's ``tests/runtests.py``; returns its exit code."""
    tests_dir = django_src / "tests"
    runtests = tests_dir / "runtests.py"
    if not runtests.is_file():
        sys.stderr.write("no runtests.py under %s\n" % tests_dir)
        return 2
    sys.path.insert(0, str(tests_dir))
    _ensure_repo_on_path()
    _install_unless_gate_off(gate_off)
    sys.argv = [
        str(runtests),
        "--settings=%s" % _SETTINGS_MODULE,
        "--parallel=1",
        "--noinput",
        "-v",
        "1",
        *(labels or ["template_tests"]),
    ]
    try:
        runpy.run_path(str(runtests), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


def run_discover(root: Path, labels: list[str], *, gate_off: bool) -> int:
    """Run ``DjustSuiteRunner`` over a test package under ``root``."""
    sys.path.insert(0, str(root))
    _ensure_repo_on_path()
    import django
    from django.conf import settings

    settings.configure(
        DEBUG=False,
        SECRET_KEY="django-template-suite",
        INSTALLED_APPS=[],
        TEMPLATES=[],
        DATABASES={},
        LIVEVIEW_CONFIG={},
    )
    django.setup()
    _install_unless_gate_off(gate_off)
    from scripts.lib.django_template_suite.recorder import DjustSuiteRunner

    runner = DjustSuiteRunner(verbosity=1, interactive=False, parallel=1)
    failures = runner.run_tests(labels)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    runtests = sub.add_parser("runtests")
    runtests.add_argument("--django-src", required=True, type=Path)
    runtests.add_argument("--gate-off", action="store_true")
    runtests.add_argument("labels", nargs="*")

    discover = sub.add_parser("discover")
    discover.add_argument("--root", required=True, type=Path)
    discover.add_argument("--gate-off", action="store_true")
    discover.add_argument("labels", nargs="+")

    args = parser.parse_args(argv)
    if args.mode == "runtests":
        return run_runtests(args.django_src, args.labels, gate_off=args.gate_off)
    return run_discover(args.root, args.labels, gate_off=args.gate_off)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
