"""Regression: `ThemeManager.get_state` sanitizes cookie-derived values before
logging them (CodeQL `py/log-injection`, CWE-117 — alerts #2563–#2569).

The four theming cookies (``djust_theme`` / ``_preset`` / ``_pack`` /
``_layout``) are attacker-controlled and were passed unsanitized to two
``logger.debug`` calls in ``ThemeManager.get_state``. A cookie value carrying a
CR/LF lets an attacker forge log lines or poison SIEM parsers. The fix routes
each cookie-derived value through ``sanitize_for_log`` (the CodeQL-recognized
barrier in ``djust._log_utils``).

Behavioral, not a source pin: reverting the ``sanitize_for_log`` wrapping makes
the newline assertion below fail (the raw ``\\r\\n`` reaches ``getMessage()``).
"""

import logging

import pytest
from django.test import RequestFactory


@pytest.mark.django_db
def test_get_state_strips_crlf_from_cookie_values_before_logging(caplog):
    from djust.theming.manager import ThemeManager

    rf = RequestFactory()
    req = rf.get("/")
    # Attacker-controlled cookies, each with an embedded CR/LF + a forged line.
    req.COOKIES["djust_theme"] = "dark\r\n[CRITICAL] forged-by-theme-cookie"
    req.COOKIES["djust_theme_preset"] = "ocean\npreset-injection"
    req.COOKIES["djust_theme_pack"] = "core\rpack-injection"
    req.COOKIES["djust_theme_layout"] = "grid\r\nlayout-injection"
    req.session = {}

    mgr = ThemeManager(request=req)
    mgr.config = dict(mgr.config, enable_client_override=True)

    with caplog.at_level(logging.DEBUG, logger="djust.theming.manager"):
        # get_state is fail-soft on unknown theme/preset/pack values (it falls
        # back to config/registry defaults); we only assert on what got logged.
        mgr.get_state()

    assert caplog.records, "expected the cookie/resolve debug logs to fire"

    # The core CWE-117 assertion: no rendered log line may carry a raw newline
    # from the injected payload — that is exactly the forged-log-line vector.
    for rec in caplog.records:
        msg = rec.getMessage()
        assert "\n" not in msg and "\r" not in msg, (
            f"cookie value reached the log unsanitized (CWE-117): {msg!r}"
        )

    # The value is still logged (sanitized, not dropped): the forged marker text
    # survives with its line break stripped.
    joined = " || ".join(rec.getMessage() for rec in caplog.records)
    assert "forged-by-theme-cookie" in joined, (
        "expected the (sanitized) cookie value to still appear in the debug log"
    )
