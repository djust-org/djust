"""The holder fence under the row lock, per database (#4164 item 8).

A late fenced writer that pauses between its read of the session and its
write, while a newer holder takes the tab (``_row_lock_race``):

* on SQLite (``db``, ``cached_db``) nothing locks the row: the late write
  lands every time, the documented gap in ``save-frequency.md``. This pins
  that the race in the script is real, so a zero elsewhere means something;
* on PostgreSQL the read is ``SELECT ... FOR UPDATE`` inside the merge's
  transaction: the newer holder's save waits for the late one, and the late
  one never lands. Runs when ``DJUST_PG_RACE_DATABASE`` holds a Django
  ``DATABASES`` entry as JSON (and the driver is importable); skipped
  otherwise. ``DJUST_MYSQL_RACE_DATABASE`` does the same for MySQL. On
  ``cached_db`` the database row is checked; its cache is written after the
  commit, outside the lock, and can be left with the older copy.

Each run is a separate process with its own settings, so the race uses the
database a deployment would, never the suite's test database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from djust import _session_merge

_PYTHON_ROOT = str(Path(__file__).resolve().parents[2])


def _race(database: Dict[str, Any], rounds: int, engine: str) -> Dict[str, Any]:
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    extra = env.get("DJUST_RACE_PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_PYTHON_ROOT, extra) if p)
    done = subprocess.run(  # noqa: S603 — a fixed module of this package
        [
            sys.executable,
            "-m",
            "djust.tests._row_lock_race",
            json.dumps(database),
            str(rounds),
            engine,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("engine", ["db", "cached_db"])
def test_on_sqlite_nothing_locks_the_row_and_the_late_write_lands(tmp_path, engine):
    out = _race(
        {"ENGINE": "django.db.backends.sqlite3", "NAME": str(tmp_path / "s.sqlite3")}, 3, engine
    )
    assert out["row_lock"] is False
    assert out["violations"] == 3, out


def _configured(name: str) -> Dict[str, Any]:
    raw = os.environ.get(name)
    if not raw:
        pytest.skip("%s is not set: no server to race against" % name)
    return json.loads(raw)


@pytest.mark.parametrize("engine", ["db", "cached_db"])
@pytest.mark.parametrize("server", ["DJUST_PG_RACE_DATABASE", "DJUST_MYSQL_RACE_DATABASE"])
def test_under_the_row_lock_the_late_write_never_lands(server, engine):
    out = _race(_configured(server), int(os.environ.get("DJUST_RACE_ROUNDS", "20")), engine)
    assert out["row_lock"] is True
    assert out["db_violations"] == 0, out
    if engine == "db":
        assert out["violations"] == 0, out
    # cached_db: the database row is right; the cache write runs after the
    # commit, outside the lock (test_session_merge_3347 pins that), so a late
    # writer's cache write can land after the newer one's. Recorded in
    # save-frequency.md, not asserted.
    assert out["errors"] == [], out
    # The newer holder waited for the late writer's transaction.
    assert out["b_wait_avg_s"] > 0.2, out


@pytest.mark.django_db(transaction=True)
def test_where_the_database_locks_rows_the_read_and_write_are_one_transaction(monkeypatch):
    """On the suite's SQLite, with the lock forced on: the fenced merge opens
    one ``atomic`` block, and both its read (``select_for_update``) and its
    write happen inside it. Structural; the real lock is the test above."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.db import transaction

    seen: Dict[str, Any] = {"open": 0, "reads_inside": 0, "saves_inside": 0}
    real_atomic = transaction.atomic
    real_read = _session_merge._read_stored
    real_save = SessionStore.save

    class _Counting:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __enter__(self) -> Any:
            seen["open"] += 1
            return self.inner.__enter__()

        def __exit__(self, *exc: Any) -> Any:
            seen["open"] -= 1
            return self.inner.__exit__(*exc)

    def atomic(*args: Any, **kwargs: Any) -> Any:
        return _Counting(real_atomic(*args, **kwargs))

    def read(backend: type, key: str) -> Any:
        seen["reads_inside"] += bool(seen["open"])
        return real_read(backend, key)

    def save(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen["saves_inside"] += bool(seen["open"])
        return real_save(self, *args, **kwargs)

    first = SessionStore()
    first.create()
    key = first.session_key
    late = SessionStore(key)
    _session_merge.track_session(late)
    late["_djust_auto_x"] = {"value": "late"}
    late["_djust_hold_x"] = {"token": "a", "expires": 4102444800}
    late._session_cache.fence("_djust_auto_x", "_djust_hold_x", "a")

    monkeypatch.setattr(_session_merge, "_supports_row_lock", lambda backend: True)
    monkeypatch.setattr(transaction, "atomic", atomic)
    monkeypatch.setattr(_session_merge, "_read_stored", read)
    monkeypatch.setattr(SessionStore, "save", save)
    _session_merge.save_merged(late, key)

    assert seen["reads_inside"] >= 1, seen
    assert seen["saves_inside"] >= 1, seen
    assert seen["open"] == 0
