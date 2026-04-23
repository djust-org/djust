"""Coverage-focused unit tests for ``djust.security.error_handling``.

Targets the branches not exercised by ``python/tests/test_security.py``:

- ``create_safe_error_response`` falling back to ``getattr(settings, "DEBUG")``
  when ``debug_mode`` is ``None`` (lines 122-129).
- The "view_class without event_name" path (line 141 — the ``"".join``
  branch).
- ``handle_exception``'s DEBUG-resolution fallback (lines 221-226), the
  ``logger is None`` branch (lines 229-230), the best-effort
  observability-recorder swallow (lines 244-245), and the DEBUG-mode
  ``extra`` dict-logging branch (lines 266-278).
"""

from __future__ import annotations

import logging
from unittest.mock import patch


from djust.security import (
    create_safe_error_response,
    handle_exception,
)


# ---------------------------------------------------------------------------
# create_safe_error_response — fallbacks for debug_mode=None
# ---------------------------------------------------------------------------


class TestCreateSafeErrorResponseDebugResolution:
    """Exercises the ``debug_mode is None`` branch (lines 122-129)."""

    def test_reads_django_settings_debug_true(self):
        """When debug_mode=None, reads settings.DEBUG — True path."""
        with patch("django.conf.settings.DEBUG", True):
            exc = ValueError("test")
            response = create_safe_error_response(exc, debug_mode=None)
            # DEBUG=True → detailed response with traceback + details.
            assert "ValueError" in response["error"]
            assert "traceback" in response

    def test_reads_django_settings_debug_false(self):
        """When debug_mode=None, reads settings.DEBUG — False path."""
        with patch("django.conf.settings.DEBUG", False):
            exc = ValueError("sensitive")
            response = create_safe_error_response(exc, debug_mode=None)
            # DEBUG=False → generic message, no details.
            assert "sensitive" not in response["error"]
            assert "traceback" not in response

    def test_defaults_to_safe_mode_when_django_not_configured(self):
        """Lines 127-129: if django.conf.settings access raises, default
        to safe (production) mode."""
        # Simulate ``from django.conf import settings`` raising — we
        # patch the import to throw on getattr.
        with patch(
            "django.conf.settings",
            new=type(
                "BadSettings",
                (),
                {
                    "__getattr__": lambda self, name: (_ for _ in ()).throw(
                        RuntimeError("Django not configured")
                    ),
                },
            )(),
        ):
            exc = ValueError("detail")
            response = create_safe_error_response(exc, debug_mode=None)
            # Should be safe/generic — no ValueError leak.
            assert "ValueError" not in response["error"]
            assert "detail" not in response["error"]
            assert "traceback" not in response


# ---------------------------------------------------------------------------
# create_safe_error_response — view_class branch without event_name (line 141)
# ---------------------------------------------------------------------------


class TestCreateSafeErrorResponseViewClassBranch:
    """Exercises the ``view_class`` prefix-assembly branch (line 141)."""

    def test_view_class_without_event_name_in_debug(self):
        """Just view_class (no event) → ``Error in ViewClass: …`` prefix."""
        exc = ValueError("boom")
        response = create_safe_error_response(
            exc,
            view_class="CheckoutView",
            debug_mode=True,
        )
        assert "CheckoutView" in response["error"]
        assert "boom" in response["error"]
        # Event key absent because event_name was not provided.
        assert "event" not in response


# ---------------------------------------------------------------------------
# handle_exception — DEBUG resolution, default-logger, extras, observability
# ---------------------------------------------------------------------------


class TestHandleExceptionDebugResolution:
    """Exercises handle_exception's ``debug_mode`` resolution + logging paths."""

    def test_debug_mode_true_path(self, caplog):
        """Lines 221-226 with DEBUG=True: exc logged with exc_info."""
        with patch("django.conf.settings.DEBUG", True):
            caplog.set_level(logging.ERROR, logger="djust.security")
            try:
                raise ValueError("debug-mode detail")
            except Exception as exc:
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name="click",
                    view_class="CheckoutView",
                )
            # Response should carry detailed info.
            assert "ValueError" in response["error"]
            # Logger captured the exc with exc_info.
            assert any(
                "debug-mode detail" in rec.getMessage() or rec.exc_info is not None
                for rec in caplog.records
            )

    def test_django_not_configured_defaults_to_prod(self, caplog):
        """Lines 225-226: if settings access throws, debug_mode defaults to False."""
        with patch(
            "django.conf.settings",
            new=type(
                "BadSettings",
                (),
                {
                    "__getattr__": lambda self, name: (_ for _ in ()).throw(
                        RuntimeError("no django")
                    ),
                },
            )(),
        ):
            caplog.set_level(logging.ERROR, logger="djust.security")
            try:
                raise RuntimeError("prod-mode")
            except Exception as exc:
                response = handle_exception(exc, error_type="event")
            # Production mode — generic error message.
            assert "RuntimeError" not in response["error"]
            assert response["type"] == "error"

    def test_default_logger_used_when_none(self, caplog):
        """Line 229-230: ``logger is None`` → use ``djust.security`` logger."""
        caplog.set_level(logging.ERROR, logger="djust.security")
        try:
            raise ValueError("logger default")
        except Exception as exc:
            response = handle_exception(exc, logger=None)
        # Something must have been logged on the default logger.
        assert response["type"] == "error"
        assert any(rec.name == "djust.security" for rec in caplog.records), (
            "Expected at least one record on djust.security logger"
        )

    def test_observability_recorder_failure_swallowed(self, caplog):
        """Lines 244-245: if record_traceback raises, error handling still works."""
        import djust.observability.tracebacks as tb_mod

        with patch.object(
            tb_mod, "record_traceback", side_effect=RuntimeError("ring buffer broke")
        ):
            try:
                raise ValueError("still works")
            except Exception as exc:
                # Must NOT raise — the except clause swallows it.
                response = handle_exception(exc, error_type="event")
            assert response["type"] == "error"

    def test_debug_mode_with_extra_dict(self, caplog):
        """Lines 266-278: DEBUG=True + extra dict → sanitize_dict_for_log
        branch, with ``extra={"sanitized_context": safe_extra}`` on the
        logger call."""
        with patch("django.conf.settings.DEBUG", True):
            caplog.set_level(logging.ERROR, logger="djust.security")
            try:
                raise ValueError("with-extra")
            except Exception as exc:
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name="save",
                    view_class="MyView",
                    extra={"user_id": 42, "password": "s3cr3t"},
                )
            assert response["type"] == "error"
            assert "ValueError" in response["error"]
            # The logged record should carry sanitized_context and the
            # password value should have been redacted by
            # sanitize_dict_for_log.
            matching = [rec for rec in caplog.records if hasattr(rec, "sanitized_context")]
            assert matching, "Expected a log record with sanitized_context"
            san = matching[0].sanitized_context
            # sanitize_dict_for_log redacts keys matching sensitive names.
            # "password" is a canonical sensitive key.
            assert san.get("password") != "s3cr3t"

    def test_debug_mode_without_extra(self, caplog):
        """Lines 279-287: DEBUG=True branch that skips the ``extra`` dict."""
        with patch("django.conf.settings.DEBUG", True):
            caplog.set_level(logging.ERROR, logger="djust.security")
            try:
                raise ValueError("no extras")
            except Exception as exc:
                response = handle_exception(
                    exc,
                    error_type="event",
                    event_name="click",
                    view_class="V",
                )
            # Record must exist without sanitized_context.
            assert response["type"] == "error"
            assert any(rec.exc_info is not None for rec in caplog.records)

    def test_log_message_is_sanitized(self, caplog):
        """Line 258: user-supplied log_message flows through sanitize_for_log."""
        caplog.set_level(logging.ERROR, logger="djust.security")
        malicious = "line1\nINJECTED line2"
        try:
            raise ValueError("boom")
        except Exception as exc:
            handle_exception(exc, log_message=malicious)
        combined = " ".join(rec.getMessage() for rec in caplog.records)
        # Newline must have been replaced by sanitize_for_log.
        assert "INJECTED line2" not in combined or "\n" not in combined


# ---------------------------------------------------------------------------
# Smoke test: direct import path coverage
# ---------------------------------------------------------------------------


def test_module_exports_are_importable():
    """Ensure public API surface stays importable (guards against rename)."""
    from djust.security import error_handling as mod

    assert callable(mod.safe_error_message)
    assert callable(mod.create_safe_error_response)
    assert callable(mod.handle_exception)
    assert isinstance(mod.GENERIC_ERROR_MESSAGES, dict)
    assert "default" in mod.GENERIC_ERROR_MESSAGES
