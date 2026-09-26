"""#3126: LiveViewSmokeTest names reachable handlers it does not fuzz.

ADR-037 made the smoke test fuzz exactly the decorated handlers. Under
``event_security = "warn"``/``"open"`` dispatch still calls an undecorated
public method, so the smoke test warns once per view instead of silently
dropping that coverage. The ruling (fuzz them vs warn): warn. Dispatch under
warn/open reaches *every* public callable, framework methods included, so no
"undecorated method" list would match what dispatch allows; guessing one was
the second discovery ADR-037 D1 retired.
"""

import warnings
from unittest.mock import patch

import pytest

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.testing import LiveViewSmokeTest, _UNFUZZED_WARNED, _unfuzzed_reachable_methods


class _Mixed(LiveView):
    template = "<div dj-root>{{ n }}</div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @event_handler
    def bump(self, **kwargs):
        self.n += 1

    def reset_counter(self, value: str = ""):
        self.n = 0

    @staticmethod
    def helper():
        return 1


class _DecoratedOnly(LiveView):
    template = "<div dj-root></div>"

    @event_handler
    def bump(self, **kwargs):
        pass


def _mode(monkeypatch, mode):
    real = config.get
    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None: mode if key == "event_security" else real(key, default),
    )


def _run_fuzz(views):
    _UNFUZZED_WARNED.clear()
    suite = type("Smoke", (LiveViewSmokeTest,), {})()
    with patch("djust.testing._discover_views", return_value=views):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for run in (
                suite.test_fuzz_xss,
                suite.test_fuzz_no_unhandled_crash,
                suite.test_fuzz_handlers_succeed,
            ):
                try:
                    run()
                except AssertionError:
                    pass  # fuzz findings on the decorated handler are not what this tests
    return [w for w in caught if "LiveViewSmokeTest does not fuzz" in str(w.message)]


@pytest.mark.parametrize("mode", ["warn", "open"])
def test_reachable_undecorated_method_warns_once_per_view(monkeypatch, mode):
    _mode(monkeypatch, mode)
    found = _run_fuzz([_Mixed])
    assert len(found) == 1
    assert "reset_counter is not decorated" in str(found[0].message)


def test_strict_mode_does_not_warn(monkeypatch):
    _mode(monkeypatch, "strict")
    assert _run_fuzz([_Mixed]) == []
    assert _unfuzzed_reachable_methods(_Mixed) == []


def test_decorated_only_view_does_not_warn(monkeypatch):
    _mode(monkeypatch, "warn")
    assert _run_fuzz([_DecoratedOnly]) == []


def test_lists_exactly_the_app_methods(monkeypatch):
    """Framework lifecycle overrides, decorated handlers and static helpers
    are not named; only the undecorated app method is."""
    _mode(monkeypatch, "warn")
    assert _unfuzzed_reachable_methods(_Mixed) == ["reset_counter"]
