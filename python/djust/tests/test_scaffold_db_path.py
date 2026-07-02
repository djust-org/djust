"""Regression: `djust new` settings must read a host-injected writable DB path.

The scaffold hardcoded ``DATABASES['default']['NAME'] = BASE_DIR / "db.sqlite3"``.
A PaaS app rootfs (e.g. djustlive) is read-only, so the first DB write (migrate
at boot, or a session write) 500s with ``OperationalError: readonly database``.
The fix reads ``DJUST_SQLITE_PATH`` (the writable path hosts like djustlive
export) with a BASE_DIR fallback for local development, so the canonical
``djust new`` -> ``djust deploy`` flow works out of the box.
"""

from __future__ import annotations

from djust.scaffolding import generator


def test_settings_db_name_reads_writable_path_env(tmp_path):
    proj = generator.generate_project("myappdbpath", target_dir=str(tmp_path), auto_setup=False)
    settings = (proj / "myappdbpath" / "settings.py").read_text()
    # Host-injected writable path is read at runtime ...
    assert 'os.environ.get("DJUST_SQLITE_PATH")' in settings
    # ... with the local-development fallback retained.
    assert 'BASE_DIR / "db.sqlite3"' in settings
    # And the generated settings must still be valid Python.
    compile(settings, "settings.py", "exec")
