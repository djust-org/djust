"""Deploy-path integration test for `djust new --with-db` (#1655, from #1637).

#1637's unit tests pin the command *sequence* (makemigrations before migrate)
and that `migrations/__init__.py` ships. This goes one level deeper — the gap
the #1637 retro flagged: exercise the actual DEPLOY path. Generate a `--with-db`
project, run `makemigrations` + a PLAIN `migrate` (no `--run-syncdb`, as a deploy
does) against a fresh SQLite DB via subprocess, and assert the demo model's
table is actually created.

The dev path masked the bug with `--run-syncdb`; only the plain-`migrate` path
(this test) proves the generated migrations are sufficient on a target.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from djust.scaffolding import generator


def _run(cmd, cwd, env):
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)


@pytest.mark.slow
def test_with_db_scaffold_creates_tables_on_plain_migrate(tmp_path):
    import os

    proj = generator.generate_project(
        "deployapp1655", target_dir=str(tmp_path), with_db=True, auto_setup=False
    )
    app_dir = proj / "deployapp1655"
    manage = proj / "manage.py"
    assert manage.exists()

    # Run against the CURRENT interpreter/venv (it has djust + Django). If the
    # generated project imports a dependency this env lacks (channels/daphne in
    # a minimal install), skip rather than fail — the invariant under test is
    # "plain migrate creates the tables", not "this CI env has every extra".
    env = dict(os.environ)
    env["DJANGO_SETTINGS_MODULE"] = "deployapp1655.settings"
    # Force a local sqlite db inside the project dir regardless of scaffold default.
    db_path = proj / "deploy_test.sqlite3"
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    mm = _run(
        [
            sys.executable,
            "manage.py",
            "makemigrations",
            "deployapp1655",
            "--noinput",
            "--skip-checks",
        ],
        proj,
        env,
    )
    if mm.returncode != 0:
        if "ModuleNotFoundError" in mm.stderr or "No module named" in mm.stderr:
            pytest.skip(
                f"generated project deps not installed in this env: {mm.stderr.strip()[-200:]}"
            )
        pytest.fail(f"makemigrations failed:\n{mm.stderr}")

    # The migrations package + an initial migration must now exist.
    assert (app_dir / "migrations" / "__init__.py").exists()
    initial = list((app_dir / "migrations").glob("0001_*.py"))
    assert initial, "makemigrations must produce an initial migration for the model app"

    # PLAIN migrate — no --run-syncdb (the deploy path).
    mig = _run([sys.executable, "manage.py", "migrate", "--noinput", "--skip-checks"], proj, env)
    assert mig.returncode == 0, f"plain migrate failed:\n{mig.stderr}"

    # Find the generated SQLite DB (scaffold default is db.sqlite3 in the project).
    candidates = [db_path, proj / "db.sqlite3"] + list(proj.glob("*.sqlite3"))
    db = next((c for c in candidates if Path(c).exists()), None)
    assert db is not None, f"no sqlite db produced; tried {candidates}"

    conn = sqlite3.connect(str(db))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    # At least one demo model table for the app must exist (deployapp1655_*).
    app_tables = [t for t in tables if t.startswith("deployapp1655_")]
    assert app_tables, (
        "plain `migrate` (no --run-syncdb) created NO tables for the --with-db "
        f"app — the #1637 regression. Tables present: {sorted(tables)}"
    )
