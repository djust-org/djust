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
