import logging

from django.test import override_settings

from djust.utils import BackendRegistry


@override_settings(DEBUG=False)
def test_warn_on_default_false_is_silent(caplog):
    reg = BackendRegistry(
        "ACCOUNTS", "django", lambda t, c: object(), name="accounts", warn_on_default=False
    )
    with caplog.at_level(logging.WARNING):
        reg.get()
    assert "Falling back to in-memory" not in caplog.text


@override_settings(DEBUG=False)
def test_warn_on_default_true_keeps_todays_warning(caplog):
    reg = BackendRegistry("PRESENCE_BACKEND", "memory", lambda t, c: object(), name="presence")
    with caplog.at_level(logging.WARNING):
        reg.get()
    assert "Falling back to in-memory" in caplog.text
