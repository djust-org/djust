"""ADR-038 E1: object-permission hooks are application code.

``enforce_object_permission`` fails closed on any non-``PermissionDenied``
exception from the developer's ``get_object`` / ``has_object_permission`` —
and logged that exception's text, which can carry object or request data, for
any policy. Denial behaviour is unchanged; only the log becomes value-free.
"""

import logging

import pytest
from django.core.exceptions import PermissionDenied

from djust import LiveView
from djust.auth.core import enforce_object_permission

CALLS = []


class ObjectView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    def get_object(self):
        CALLS.append(True)
        raise ValueError("OBJECT_LOOKUP_SENTINEL")


@pytest.mark.parametrize("debug", [False, True])
def test_object_permission_failure_log_is_value_free_for_explicit_views(
    monkeypatch, rf, caplog, settings, debug
):
    CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DEBUG = debug
    view = ObjectView()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PermissionDenied):
            enforce_object_permission(view, rf.get("/o/"))
    assert CALLS, "get_object never ran; the test would be vacuous"
    if debug:
        # The positive control: the lookup failed and was logged with its detail.
        assert "OBJECT_LOOKUP_SENTINEL" in caplog.text
    else:
        assert "OBJECT_LOOKUP_SENTINEL" not in caplog.text
        assert "Protected view operation failed" in caplog.text
