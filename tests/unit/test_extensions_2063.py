"""Official adapter extensions — dj-chart pilot (#2063, ADR-025 milestone C).

Covers the Python half of the feature:
- ``DJUST_CONFIG['extensions']`` resolution (unset / empty / valid / unknown).
- Script injection: a tag is emitted only when opted in, and it must land
  AFTER client.js (the adapter registers into ``window.djust.*``, which
  client.js creates — ``defer`` preserves document order, so tag order is
  the whole contract).
- System check C015 makes an unknown adapter name loud instead of a silent
  no-op.
"""

from __future__ import annotations

import re

from django.test import override_settings

from djust.checks.configuration import _check_unknown_extensions
from djust.extensions import (
    AVAILABLE_EXTENSIONS,
    get_configured_extensions,
    get_enabled_extensions,
    get_extension_static_paths,
)
from djust.mixins.post_processing import PostProcessingMixin


class _FakeView(PostProcessingMixin):
    """Minimal mixin host — only the bits _inject_client_script uses."""

    def get_debug_info(self) -> dict:
        return {}


def _script_srcs(html: str) -> list[str]:
    """All <script src=...> values, in document order."""
    return re.findall(r'<script src="([^"]+)"', html)


def _inject() -> str:
    return _FakeView()._inject_client_script("<html><body></body></html>")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_chart_is_the_only_shipped_adapter():
    """The pilot ships exactly ONE adapter (issue #2063 scope guard)."""
    assert list(AVAILABLE_EXTENSIONS) == ["chart"]


def test_no_config_means_no_extensions():
    with override_settings(DJUST_CONFIG={}):
        assert get_enabled_extensions() == []
        assert get_extension_static_paths() == []


def test_enabled_extension_resolves_to_static_path():
    with override_settings(DJUST_CONFIG={"extensions": ["chart"]}):
        assert get_enabled_extensions() == ["chart"]
        assert get_extension_static_paths() == ["djust/ext/dj-chart.js"]


def test_unknown_name_is_dropped_not_raised():
    """A typo must not 500 every page — the system check is what shouts."""
    with override_settings(DJUST_CONFIG={"extensions": ["charts", "chart"]}):
        assert get_configured_extensions() == ["charts", "chart"]
        assert get_enabled_extensions() == ["chart"]


def test_non_list_config_yields_no_extensions():
    with override_settings(DJUST_CONFIG={"extensions": "chart"}):
        assert get_enabled_extensions() == []


# ---------------------------------------------------------------------------
# Injection
# ---------------------------------------------------------------------------


@override_settings(DEBUG=False, DJUST_CONFIG={})
def test_no_adapter_tag_when_not_opted_in():
    """Zero-cost when unused: not a single byte for users who never enable it."""
    assert "dj-chart.js" not in _inject()


@override_settings(DEBUG=False, DJUST_CONFIG={"extensions": ["chart"]})
def test_adapter_tag_emitted_when_enabled():
    srcs = _script_srcs(_inject())
    assert any(s.endswith("djust/ext/dj-chart.js") for s in srcs), srcs


@override_settings(DEBUG=False, DJUST_CONFIG={"extensions": ["chart"]})
def test_adapter_tag_comes_after_client_js():
    """Load order IS the contract: the adapter needs window.djust.* to exist.

    `defer` scripts execute in document order, so emitting the adapter tag
    after the client tag is what makes `djust.commands.register` reachable.
    """
    srcs = _script_srcs(_inject())
    client_idx = next(i for i, s in enumerate(srcs) if re.search(r"djust/client(\.min)?\.js$", s))
    ext_idx = next(i for i, s in enumerate(srcs) if s.endswith("dj-chart.js"))
    assert ext_idx > client_idx, f"adapter must load after client.js; got {srcs}"


@override_settings(DEBUG=False, DJUST_CONFIG={"extensions": ["charts"]})
def test_unknown_name_emits_no_tag():
    assert "dj-chart.js" not in _inject()


@override_settings(DEBUG=False, DJUST_CONFIG={"extensions": ["chart"]})
def test_adapter_tag_is_deferred():
    """Without defer the adapter could run before client.js finishes."""
    html = _inject()
    m = re.search(r'<script src="[^"]*dj-chart\.js"([^>]*)>', html)
    assert m and "defer" in m.group(1), html


# ---------------------------------------------------------------------------
# System check C015
# ---------------------------------------------------------------------------


def _run_check() -> list:
    errors: list = []
    _check_unknown_extensions(errors)
    return errors


@override_settings(DJUST_CONFIG={"extensions": ["chart"]})
def test_check_silent_for_valid_name():
    assert _run_check() == []


@override_settings(DJUST_CONFIG={})
def test_check_silent_when_unconfigured():
    assert _run_check() == []


@override_settings(DJUST_CONFIG={"extensions": ["charts"]})
def test_check_reports_unknown_name():
    errors = _run_check()
    assert len(errors) == 1
    assert errors[0].id == "djust.C015"
    assert "charts" in errors[0].msg
    # The hint must name what IS available, or the user is left guessing.
    assert "chart" in errors[0].hint


@override_settings(DJUST_CONFIG={"extensions": "chart"})
def test_check_reports_non_list_config():
    errors = _run_check()
    assert len(errors) == 1
    assert errors[0].id == "djust.C015"
    assert "list" in errors[0].msg.lower()


@override_settings(DJUST_CONFIG={"extensions": ["charts"], "suppress_checks": ["C015"]})
def test_check_is_suppressible():
    assert _run_check() == []


@override_settings(DJUST_CONFIG={"extensions": ["charts"]})
def test_c015_is_wired_into_the_real_aggregate_check():
    """C015 must fire through ``manage.py check``, not just when called directly.

    Every other test here calls ``_check_unknown_extensions`` directly, so all
    of them stay green if the call site is dropped from ``check_configuration``
    — the check would be dead in production with a fully green suite. That is
    the decorative-pin class (#1859): this test is the one that goes red.
    """
    from djust.checks.configuration import check_configuration

    ids = [getattr(m, "id", None) for m in check_configuration(app_configs=None)]
    assert "djust.C015" in ids, (
        "C015 did not fire through check_configuration() — is the "
        "_check_unknown_extensions(errors) call site still present?"
    )


@override_settings(DJUST_CONFIG={"extensions": ["chart"]})
def test_c015_silent_through_aggregate_for_valid_name():
    from djust.checks.configuration import check_configuration

    ids = [getattr(m, "id", None) for m in check_configuration(app_configs=None)]
    assert "djust.C015" not in ids
