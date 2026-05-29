"""Regression: `djust new` must produce a reproducible-on-any-target schema (#1637).

`generate_project` ran `migrate --run-syncdb` but never `makemigrations`, so a
`--with-db` app shipped no migrations. `--run-syncdb` masks that on the dev
machine, but a deploy runs `migrate` WITHOUT `--run-syncdb`, so the app's tables
are never created → 500 on first query. Two fixes:

1. `_run_auto_setup` runs `makemigrations` before `migrate` (creates the
   migrations package + 0001_initial on the dev machine, which then ship to the
   target).
2. A `--with-db` scaffold explicitly ships `<app>/migrations/__init__.py` so the
   migrations dir is a real package (not a namespace package the loader skips),
   even when `auto_setup=False`.
"""

from __future__ import annotations

from unittest.mock import patch

from djust.scaffolding import generator


def test_with_db_scaffold_ships_migrations_init(tmp_path):
    proj = generator.generate_project(
        "myapp1637db", target_dir=str(tmp_path), with_db=True, auto_setup=False
    )
    init = proj / "myapp1637db" / "migrations" / "__init__.py"
    assert init.exists(), (
        "a --with-db scaffold must ship <app>/migrations/__init__.py so the "
        "deploy-time `migrate` (no --run-syncdb) creates the app's tables (#1637)"
    )


def test_no_db_scaffold_writes_no_models(tmp_path):
    """Control: an in-memory (no --with-db) scaffold writes no models, so it
    needs no migrations package — the fix is scoped to model-bearing apps."""
    proj = generator.generate_project(
        "myapp1637nodb", target_dir=str(tmp_path), with_db=False, auto_setup=False
    )
    assert not (proj / "myapp1637nodb" / "models.py").exists()


def test_auto_setup_runs_makemigrations_before_migrate(tmp_path):
    """The setup sequence must run `makemigrations` BEFORE `migrate`, so the
    generated app's migrations exist (and ship) rather than relying on
    `--run-syncdb`, which a deploy doesn't use."""
    proj = tmp_path / "proj"
    proj.mkdir()

    calls = []

    def _fake_run_cmd(cmd, cwd):
        calls.append(list(cmd))

    # shutil.which(None) → take the python -m venv / pip branch; doesn't matter,
    # both branches go through _run_cmd which we capture.
    with (
        patch.object(generator, "_run_cmd", side_effect=_fake_run_cmd),
        patch.object(generator.shutil, "which", return_value=None),
    ):
        generator._run_auto_setup(proj)

    def _first_index(token):
        for i, c in enumerate(calls):
            if "manage.py" in c and token in c:
                return i
        return -1

    mm = _first_index("makemigrations")
    mig = _first_index("migrate")
    assert mm != -1, f"`makemigrations` must run during auto-setup; commands: {calls}"
    assert mig != -1, f"`migrate` must run during auto-setup; commands: {calls}"
    assert mm < mig, (
        f"`makemigrations` must run BEFORE `migrate` so migrations exist "
        f"before they're applied; commands: {calls}"
    )
