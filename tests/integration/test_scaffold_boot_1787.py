"""Boot integration test for `djust new` (#1787).

A scaffolded project must actually boot:

1. ``<app>.asgi`` must import without raising and expose a real ASGI
   application. The OLD template did ``application = live_session()`` —
   ``live_session`` is a URL-pattern helper, not an ASGI app, so the import
   raised ``TypeError: live_session() missing 2 required positional
   arguments`` (the #1787 symptom).

2. ``manage.py check`` must exit 0 — no ERRORS. The OLD template tripped two
   blocking errors: ``djust.A014`` (the scaffold ran in production mode because
   settings.py never loaded the generated ``.env``, so ``DEBUG`` was False and
   the ``django-insecure-`` SECRET_KEY was flagged) and ``admin.E403`` (no
   ``DjangoTemplates`` backend was configured for the admin).

These are real gates: they fail against the pre-#1787 templates (which used
``live_session()`` in asgi.py, had no ``.env`` loader, wrote no ``.env`` file,
and configured only the djust template backend).

Run against the CURRENT interpreter/venv (which has djust + Django + channels).
If the generated project imports a dependency this env lacks, skip rather than
fail — the invariant under test is "the scaffold boots", not "this CI env has
every extra".
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from djust.scaffolding import generator

APP = "bootapp1787"


def _run(cmd, cwd, env):
    return subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)


def _missing_deps(stderr: str) -> bool:
    return "ModuleNotFoundError" in stderr or "No module named" in stderr


@pytest.mark.slow
def test_scaffold_asgi_imports_and_check_passes(tmp_path):
    proj = generator.generate_project(APP, target_dir=str(tmp_path), auto_setup=False)

    # The generated asgi.py must NOT use the old live_session() shape and MUST
    # build a ProtocolTypeRouter (the lifted demo pattern).
    asgi_src = (proj / APP / "asgi.py").read_text()
    assert "application = live_session()" not in asgi_src, (
        "asgi.py still uses the broken live_session() shape (#1787 regression)"
    )
    assert "ProtocolTypeRouter" in asgi_src
    assert "ASGIStaticFilesHandler" in asgi_src

    # The scaffold must write a working .env (so settings.py loads DEBUG=True).
    env_file = proj / ".env"
    assert env_file.exists(), "scaffold must write a .env (not only .env.example)"
    env_text = env_file.read_text()
    assert "DEBUG=True" in env_text
    assert "SECRET_KEY=" in env_text

    # The scaffold must not depend on / reference whitenoise anywhere.
    for path in proj.rglob("*"):
        if path.is_file():
            assert "whitenoise" not in path.read_text(errors="ignore").lower(), (
                f"unexpected whitenoise reference in {path}"
            )

    base_env = dict(os.environ)
    base_env["DJANGO_SETTINGS_MODULE"] = f"{APP}.settings"
    # Ensure the project package is importable for the subprocess.
    base_env["PYTHONPATH"] = os.pathsep.join([str(proj), base_env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )

    # (a) asgi.py imports without raising and yields a callable ASGI app.
    imp = _run(
        [
            sys.executable,
            "-c",
            (
                f"import {APP}.asgi as m; "
                "assert callable(m.application), 'application is not a callable ASGI app'; "
                "print(type(m.application).__name__)"
            ),
        ],
        proj,
        base_env,
    )
    if imp.returncode != 0 and _missing_deps(imp.stderr):
        pytest.skip(
            f"generated project deps not installed in this env: {imp.stderr.strip()[-200:]}"
        )
    assert imp.returncode == 0, (
        "scaffolded asgi.py failed to import (#1787 regression):\n"
        f"STDOUT:\n{imp.stdout}\nSTDERR:\n{imp.stderr}"
    )
    assert "ProtocolTypeRouter" in imp.stdout

    # (b) manage.py check exits 0 — no ERRORS.
    chk = _run([sys.executable, "manage.py", "check"], proj, base_env)
    if chk.returncode != 0 and _missing_deps(chk.stderr):
        pytest.skip(
            f"generated project deps not installed in this env: {chk.stderr.strip()[-200:]}"
        )
    combined = chk.stdout + chk.stderr
    assert chk.returncode == 0, (
        f"scaffolded `manage.py check` did not exit 0 (#1787 regression):\n{combined}"
    )
    # The two errors the OLD scaffold tripped must be gone explicitly.
    assert "djust.A014" not in combined, (
        "A014 fired — the scaffold ran in production mode (.env not loaded)"
    )
    assert "admin.E403" not in combined, (
        "admin.E403 fired — no DjangoTemplates backend configured for admin"
    )
