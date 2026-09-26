"""djust's config singleton follows ``override_settings`` (#3217).

``test_event_reauth_1777`` called ``config.reset()`` inside
``@override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})``. The reset
re-read the override into the process-wide cache, and nothing re-read it when
the override exited. ``reauth_on_event=True`` then closed the socket on the
first event of ``test_model_form_acceptance_adr035``'s reconnect test whenever
the two ran in that order on one worker:

    pytest -p no:randomly \\
      "python/djust/tests/test_event_reauth_1777.py::test_reauth_on_closes_socket_when_user_logged_out_midsession" \\
      python/djust/tests/test_model_form_acceptance_adr035.py::test_reconnect_keeps_unsaved_input_and_revalidates_it
"""

from __future__ import annotations

from django.test import override_settings

from djust.config import config


def test_a_reset_inside_an_override_does_not_outlive_it():
    """The polluter's exact shape, in-process."""
    before = config.get("reauth_on_event")
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": not before}):
        config.reset()
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_an_override_takes_effect_without_a_manual_reset():
    before = config.get("reauth_on_event")
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": not before}):
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_djust_config_is_followed_too():
    before = config.get("reauth_on_event")
    with override_settings(DJUST_CONFIG={"reauth_on_event": not before}):
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_an_unrelated_setting_change_does_not_reload_the_config():
    """Only the settings the config reads trigger a reload, so a value set
    programmatically survives other tests' ``override_settings``."""
    config.set("probe_3217", "kept")
    try:
        with override_settings(LIVEVIEW_ALLOWED_MODULES=["x"]):
            assert config.get("probe_3217") == "kept"
        assert config.get("probe_3217") == "kept"
    finally:
        config.reset()


# --- #3213 re-review: code-set values survive reloads -----------------------


def test_a_code_set_value_survives_an_override_of_liveview_config():
    """The re-review probe: ``config.set`` stands in for a project's
    ``AppConfig.ready()``. One override of LIVEVIEW_CONFIG itself must not
    drop it, neither inside nor after."""
    config.set("css_framework", "tailwind_probe")
    try:
        with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": False}):
            assert config.get("css_framework") == "tailwind_probe"
        assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_an_override_that_sets_the_key_wins_inside_and_the_code_value_returns():
    config.set("css_framework", "tailwind_probe")
    try:
        with override_settings(LIVEVIEW_CONFIG={"css_framework": "plain"}):
            assert config.get("css_framework") == "plain"
        assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_an_override_copying_the_base_settings_keeps_the_code_value():
    """``{**settings.LIVEVIEW_CONFIG, other: x}`` repeats the base value for
    the key; that is not the override setting it."""
    from django.conf import settings

    config.set("css_framework", "tailwind_probe")
    try:
        base = dict(getattr(settings, "LIVEVIEW_CONFIG", None) or {})
        with override_settings(LIVEVIEW_CONFIG={**base, "reauth_on_event": True}):
            assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_update_and_dotted_set_survive_too():
    config.update({"render_labels": False})
    config.set("bootstrap5.field_class", "probe-control")
    try:
        with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
            assert config.get("render_labels") is False
            assert config.get("bootstrap5.field_class") == "probe-control"
        assert config.get("render_labels") is False
        assert config.get("bootstrap5.field_class") == "probe-control"
    finally:
        config.reset()


def test_an_explicit_reset_still_discards_code_set_values():
    before = config.get("css_framework")
    config.set("css_framework", "tailwind_probe")
    config.reset()
    assert config.get("css_framework") == before
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        assert config.get("css_framework") == before


def test_every_setting_the_config_reads_triggers_a_reload():
    """The key set is derived from the loader's source, not restated: a new
    ``settings.X`` read that is not in ``_CONFIG_SETTINGS`` fails here."""
    import inspect
    import re

    from djust import config as config_module

    source = inspect.getsource(config_module.LiveViewConfig._apply_settings)
    read = set(re.findall(r"(?:getattr|hasattr)\(settings, \"([A-Z_]+)\"", source))
    read |= set(re.findall(r"settings\.([A-Z][A-Z_]+)", source))
    read |= {name for name, _, _ in config_module._SERVICE_WORKER_ALIASES}
    assert read == config_module._CONFIG_SETTINGS


def test_a_flat_alias_override_reaches_the_config_and_is_undone():
    before = config.get("websocket_compression")
    with override_settings(DJUST_WS_COMPRESSION=not before):
        assert config.get("websocket_compression") is (not before)
    assert config.get("websocket_compression") == before
    ttl = config.get("service_worker.vdom_cache_ttl_seconds")
    with override_settings(DJUST_VDOM_CACHE_TTL_SECONDS=12345):
        assert config.get("service_worker.vdom_cache_ttl_seconds") == 12345
    assert config.get("service_worker.vdom_cache_ttl_seconds") == ttl


def test_a_reload_swaps_the_config_in_one_step():
    """Readers on other threads must never see defaults mid-reload: while the
    new config is being built, the live one still holds the old values."""
    from unittest.mock import patch

    from djust.config import LiveViewConfig

    seen = []
    real = LiveViewConfig._apply_settings

    def spy(self, target):
        seen.append(config.get("reauth_on_event"))
        return real(self, target)

    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        with patch.object(LiveViewConfig, "_apply_settings", spy):
            config.reset()  # inside the override the live value is True
    assert seen == [True]


def test_debug_vdom_trace_env_var_is_undone_when_the_override_exits(monkeypatch):
    import os

    monkeypatch.delenv("DJUST_VDOM_TRACE", raising=False)
    config.reset()
    with override_settings(LIVEVIEW_CONFIG={"debug_vdom": True}):
        assert os.environ.get("DJUST_VDOM_TRACE") == "1"
    assert "DJUST_VDOM_TRACE" not in os.environ


def test_debug_vdom_trace_set_by_the_environment_is_left_alone(monkeypatch):
    import os

    monkeypatch.setenv("DJUST_VDOM_TRACE", "1")
    with override_settings(LIVEVIEW_CONFIG={"debug_vdom": True}):
        pass
    assert os.environ.get("DJUST_VDOM_TRACE") == "1"
