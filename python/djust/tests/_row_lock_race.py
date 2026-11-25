"""The holder fence under a paused late writer, on one database (#4164 item 8).

Run as a script by ``test_holder_fence_row_lock_4164.py``, in its own process
with its own settings, so the race runs against the database a deployment
uses (SQLite, PostgreSQL, MySQL) instead of the test suite's::

    python -m djust.tests._row_lock_race '{"ENGINE": "django.db.backends.postgresql", ...}' 10 db

Each round is the race the fence exists for, made deterministic:

* A is a socket that held the tab (token ``a``) and writes late: its fenced
  save reads the session, then PAUSES (up to ``PAUSE_S``) before writing;
* B takes the tab while A is paused: a fallback request that writes its
  state and its own holder record (token ``b``), unfenced, as one does.

Where the database locks the row (``SELECT ... FOR UPDATE`` inside the merge's
transaction: PostgreSQL, MySQL), B's read waits until A has written, then B
writes: B's state and token are stored. Where it cannot (SQLite), B writes
during A's pause and A's write, built on what it read before, puts A's state
and token back: a violation. Prints one JSON line: rounds, violations, whether
the connection says it can lock rows, and how long B waited on average.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any, Dict

KEY = "_djust_auto_row_lock_race"
HOLDER = "_djust_hold_row_lock_race"
FAR = 4102444800
PAUSE_S = 0.3
PAUSE_CACHE = os.environ.get("DJUST_RACE_PAUSE_CACHE") == "1"


def _configure(database: Dict[str, Any], engine: str) -> None:
    import django
    from django.conf import settings

    settings.configure(
        DEBUG=False,
        SECRET_KEY="row-lock-race",
        USE_TZ=True,
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.sessions"],
        DATABASES={"default": database},
        SESSION_ENGINE="django.contrib.sessions.backends.%s" % engine,
    )
    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0, interactive=False)


def _round() -> Dict[str, Any]:
    from importlib import import_module

    from django.conf import settings
    from django.db import connections

    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore

    from djust import _session_merge
    from djust._session_merge import save_merged, track_session

    first = SessionStore()
    first.create()
    key = first.session_key
    seed = SessionStore(key)
    track_session(seed)
    seed[HOLDER] = {"token": "a", "expires": FAR}
    save_merged(seed, key)

    a = SessionStore(key)
    track_session(a)
    a[KEY] = {"value": "a"}
    a[HOLDER] = {"token": "a", "expires": FAR}
    a._session_cache.fence(KEY, HOLDER, "a")
    b = SessionStore(key)
    track_session(b)
    b[KEY] = {"value": "b"}
    b[HOLDER] = {"token": "b", "expires": FAR}

    a_read = threading.Event()
    b_done = threading.Event()
    a_thread: Dict[str, Any] = {}
    real_read = _session_merge._read_stored

    def paused_read(backend: type, session_key: str) -> Any:
        stored = real_read(backend, session_key)
        if threading.current_thread() is a_thread.get("thread"):
            a_read.set()
            b_done.wait(PAUSE_S)
        return stored

    real_cache_write = _session_merge._cache_write

    def paused_cache_write(writer: Any) -> None:
        # ``cached_db``: A's cache write runs after its commit, outside the
        # lock. With PAUSE_CACHE, it lands after B's.
        if PAUSE_CACHE and threading.current_thread() is a_thread.get("thread"):
            b_done.wait(PAUSE_S * 4)
        real_cache_write(writer)

    _session_merge._read_stored = paused_read
    _session_merge._cache_write = paused_cache_write
    errors: list = []
    waited: Dict[str, float] = {}

    def run_a() -> None:
        try:
            save_merged(a, key)
        except Exception as exc:  # noqa: BLE001 — a refusal is an answer, recorded
            errors.append(type(exc).__name__)
        finally:
            connections.close_all()

    def run_b() -> None:
        started = time.monotonic()
        try:
            save_merged(b, key)
        except Exception as exc:  # noqa: BLE001
            errors.append("b:" + type(exc).__name__)
        finally:
            waited["b"] = time.monotonic() - started
            b_done.set()
            connections.close_all()

    try:
        thread_a = threading.Thread(target=run_a)
        a_thread["thread"] = thread_a
        thread_a.start()
        assert a_read.wait(10), "A never read the session"
        thread_b = threading.Thread(target=run_b)
        thread_b.start()
        thread_a.join(30)
        thread_b.join(30)
    finally:
        _session_merge._read_stored = real_read
        _session_merge._cache_write = real_cache_write
    stored = dict(SessionStore(key).load())
    value = (stored.get(KEY) or {}).get("value")
    token = (stored.get(HOLDER) or {}).get("token")
    from django.contrib.sessions.backends.db import SessionStore as DbStore

    in_db = dict(DbStore(key).load())
    db_late = (in_db.get(KEY) or {}).get("value") == "a" or (in_db.get(HOLDER) or {}).get(
        "token"
    ) == "a"
    return {
        "violation": value == "a" or token == "a",
        "db_violation": db_late,
        "value": value,
        "token": token,
        "b_wait": waited.get("b", 0.0),
        "errors": errors,
    }


def main(argv: list) -> int:
    database = json.loads(argv[0])
    rounds = int(argv[1]) if len(argv) > 1 else 10
    engine = argv[2] if len(argv) > 2 else "db"
    _configure(database, engine)
    from importlib import import_module

    from django.conf import settings
    from django.db import connection

    from djust._session_merge import _supports_row_lock

    SessionStore = import_module(settings.SESSION_ENGINE).SessionStore

    results = [_round() for _ in range(rounds)]
    out = {
        "vendor": connection.vendor,
        "engine": engine,
        "row_lock": _supports_row_lock(SessionStore),
        "rounds": rounds,
        "violations": sum(1 for r in results if r["violation"]),
        # The database row alone (for cached_db, the cache can differ).
        "db_violations": sum(1 for r in results if r["db_violation"]),
        "b_wait_avg_s": round(sum(r["b_wait"] for r in results) / rounds, 3),
        "errors": sorted({e for r in results for e in r["errors"]}),
    }
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    raise SystemExit(main(sys.argv[1:]))
