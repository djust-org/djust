"""``tests/local_load_guard.py``: outside CI a run is held to a few workers and to a
targeted size, unless the full gate is asked for on purpose.

The behaviour tests run a real inner pytest in a subprocess with a fully controlled
environment (``CI`` and the ``DJUST_*`` knobs removed first), because the guard is
about how a pytest process starts and what it collects, which cannot be observed from
inside one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import local_load_guard as guard

REPO = Path(__file__).resolve().parent.parent


class TestTheKnobs:
    @pytest.mark.parametrize(
        "env, expected",
        [
            ({}, True),
            ({"CI": "true"}, False),
            ({"CI": "1"}, False),
            ({"CI": "false"}, True),
            ({"DJUST_FULL_LOCAL": "1"}, False),
            ({"DJUST_FULL_LOCAL": "0"}, True),
            ({"DJUST_FULL_LOCAL": "yes", "CI": ""}, False),
        ],
    )
    def test_active(self, env, expected):
        assert guard.active(env) is expected

    @pytest.mark.parametrize(
        "raw, expected",
        [(None, 2), ("4", 4), ("0", 2), ("-3", 2), ("abc", 2), ("", 2), (" 3 ", 3)],
    )
    def test_the_worker_cap_falls_back_to_the_default(self, raw, expected):
        env = {} if raw is None else {"DJUST_LOCAL_MAX_WORKERS": raw}
        assert guard.max_workers(env) == expected

    @pytest.mark.parametrize(
        "raw, expected", [(None, 5000), ("100", 100), ("0", 5000), ("x", 5000)]
    )
    def test_the_item_limit_falls_back_to_the_default(self, raw, expected):
        env = {} if raw is None else {"DJUST_LOCAL_MAX_ITEMS": raw}
        assert guard.max_items(env) == expected

    def test_the_message_names_the_way_out(self):
        text = guard.refusal_message(16000, 5000)
        for needle in ("16000", "5000", "make test-selected", "DJUST_FULL_LOCAL=1", "gh pr checks"):
            assert needle in text


def _inner(tmp_path: Path, *args: str, env: dict | None = None, tests: int = 12):
    """Run a fresh pytest over ``tests`` trivial tests, with the guard loaded."""
    # Register the guard the way the repo does, from a conftest's ``pytest_plugins``: the order
    # it is registered in relative to xdist is what makes its hook run first.
    (tmp_path / "conftest.py").write_text('pytest_plugins = ["tests.local_load_guard"]\n')
    (tmp_path / "test_many.py").write_text(
        textwrap.dedent(
            f"""
            import os, pytest

            @pytest.mark.parametrize("i", range({tests}))
            def test_n(i):
                # xdist does not forward a worker's stdout, so leave the observation on disk.
                with open("workers.txt", "a") as fh:
                    fh.write(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "none") + "\\n")
            """
        )
    )
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in {"CI", "DJANGO_SETTINGS_MODULE", "PYTEST_ADDOPTS"} and not k.startswith("DJUST_")
    }
    clean["PYTHONPATH"] = str(REPO)
    clean.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:django",
         "-q", "-s", "-o", "addopts=", *args],
        cwd=tmp_path, env=clean, capture_output=True, text=True, timeout=180,
    )


def _workers(tmp_path: Path) -> set[str]:
    seen = tmp_path / "workers.txt"
    return set(seen.read_text().split()) if seen.exists() else set()


class TestTheSizeLimit:
    def test_a_run_over_the_limit_stops_before_running_anything(self, tmp_path):
        r = _inner(tmp_path, env={"DJUST_LOCAL_MAX_ITEMS": "5"})
        assert r.returncode != 0
        assert "over the local limit of 5" in (r.stdout + r.stderr)
        assert not _workers(tmp_path), "a test ran although the run was refused"

    def test_the_same_refusal_under_xdist_without_a_traceback(self, tmp_path):
        r = _inner(tmp_path, "-n", "2", env={"DJUST_LOCAL_MAX_ITEMS": "5"})
        out = r.stdout + r.stderr
        assert r.returncode != 0
        assert "over the local limit of 5" in out
        assert "INTERNALERROR" not in out and "Traceback" not in out, out
        assert not _workers(tmp_path), "a test ran although the run was refused"

    def test_selecting_down_with_k_is_allowed(self, tmp_path):
        r = _inner(tmp_path, "-k", "n and (0 or 1)", env={"DJUST_LOCAL_MAX_ITEMS": "5"})
        assert r.returncode == 0, r.stdout + r.stderr

    def test_the_explicit_full_gate_is_allowed(self, tmp_path):
        r = _inner(tmp_path, env={"DJUST_LOCAL_MAX_ITEMS": "5", "DJUST_FULL_LOCAL": "1"})
        assert r.returncode == 0, r.stdout + r.stderr

    def test_ci_is_never_limited(self, tmp_path):
        r = _inner(tmp_path, env={"DJUST_LOCAL_MAX_ITEMS": "5", "CI": "true"})
        assert r.returncode == 0, r.stdout + r.stderr


class TestTheWorkerCap:
    def test_an_explicit_n_is_held_to_the_cap(self, tmp_path):
        r = _inner(tmp_path, "-n", "4")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _workers(tmp_path) == {"2"}

    def test_n_auto_is_held_to_the_cap(self, tmp_path):
        r = _inner(tmp_path, "-n", "auto")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _workers(tmp_path) == {"2"}

    def test_the_cap_can_be_raised_on_purpose(self, tmp_path):
        r = _inner(tmp_path, "-n", "4", env={"DJUST_LOCAL_MAX_WORKERS": "3"})
        assert _workers(tmp_path) == {"3"}

    def test_the_full_gate_keeps_the_workers_it_asked_for(self, tmp_path):
        r = _inner(tmp_path, "-n", "4", env={"DJUST_FULL_LOCAL": "1"})
        assert _workers(tmp_path) == {"4"}

    def test_ci_keeps_the_workers_it_asked_for(self, tmp_path):
        r = _inner(tmp_path, "-n", "4", env={"CI": "true"})
        assert _workers(tmp_path) == {"4"}
