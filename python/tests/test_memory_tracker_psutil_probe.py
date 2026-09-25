"""``MemoryTracker`` checks for psutil once, at module import.

A ``PerformanceTracker`` (and so a ``MemoryTracker``) is built for every
event. The tracker used to run ``import psutil`` in ``__init__``; with psutil
absent, every failed import re-scanned ``sys.path`` (about 42 us per event,
3 % of the event-loop thread in the #3074 profile).
"""

from __future__ import annotations

import builtins
import importlib.util

from djust import performance
from djust.performance import MemoryTracker


def _count_psutil_imports(monkeypatch) -> list:
    seen: list = []
    real_import = builtins.__import__

    def counting_import(name, *args, **kwargs):
        if name == "psutil" or name.startswith("psutil."):
            seen.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", counting_import)
    return seen


def test_constructing_a_tracker_never_imports_psutil(monkeypatch):
    seen = _count_psutil_imports(monkeypatch)
    for _ in range(50):
        MemoryTracker()
    assert seen == []


def test_enabled_follows_the_import_time_probe(monkeypatch):
    monkeypatch.setattr(performance, "_PSUTIL_AVAILABLE", False)
    assert MemoryTracker().enabled is False
    monkeypatch.setattr(performance, "_PSUTIL_AVAILABLE", True)
    assert MemoryTracker().enabled is True


def test_probe_matches_the_environment():
    assert performance._PSUTIL_AVAILABLE is (importlib.util.find_spec("psutil") is not None)


def test_disabled_tracker_reports_nothing(monkeypatch):
    monkeypatch.setattr(performance, "_PSUTIL_AVAILABLE", False)
    tracker = MemoryTracker()
    tracker.start_tracking()
    tracker.update_peak()
    assert tracker.stop_tracking() == {}
