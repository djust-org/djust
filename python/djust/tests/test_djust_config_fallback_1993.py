"""#1993 — LiveView runtime keys set in ``DJUST_CONFIG`` are honored as a fallback.

``LiveViewConfig._load_from_settings()`` only read ``LIVEVIEW_CONFIG``, so a
``max_message_size`` / ``rate_limit`` / ``event_security`` set in the
similarly-named ``DJUST_CONFIG`` dict (which already backs tenancy / presence /
state-backend) was a SILENT no-op — the default kept applying with no error or
warning. It now falls back to ``DJUST_CONFIG`` for keys that are genuine
LiveView config keys, with ``LIVEVIEW_CONFIG`` winning on a collision.

Gate-off (#1468): ``test_djust_config_key_applied_as_fallback`` IS the
sentinel — without the fallback the value is the default (65536), not the
262144 set in ``DJUST_CONFIG``.
"""

from django.test import override_settings

from djust.config import LiveViewConfig


class TestDjustConfigFallback1993:
    @override_settings(LIVEVIEW_CONFIG={}, DJUST_CONFIG={"max_message_size": 262_144})
    def test_djust_config_key_applied_as_fallback(self):
        # __init__ calls _load_from_settings(). Without the #1993 fallback this
        # would be the default 65536 (LIVEVIEW_CONFIG is empty).
        cfg = LiveViewConfig()
        assert cfg.get("max_message_size") == 262_144

    @override_settings(
        LIVEVIEW_CONFIG={"max_message_size": 131_072},
        DJUST_CONFIG={"max_message_size": 262_144},
    )
    def test_liveview_config_wins_on_collision(self):
        # LIVEVIEW_CONFIG is the documented home — it must win over DJUST_CONFIG.
        cfg = LiveViewConfig()
        assert cfg.get("max_message_size") == 131_072

    @override_settings(
        LIVEVIEW_CONFIG={},
        DJUST_CONFIG={"TENANT_RESOLVER": "some.path", "totally_made_up_key": 1},
    )
    def test_non_liveview_djust_config_keys_not_pulled_in(self):
        # Only keys present in the LiveView defaults are adopted, so unrelated
        # tenancy / arbitrary keys don't pollute the LiveView config.
        cfg = LiveViewConfig()
        assert cfg.get("TENANT_RESOLVER") is None
        assert cfg.get("totally_made_up_key") is None

    @override_settings(LIVEVIEW_CONFIG={}, DJUST_CONFIG={"css_framework": "tailwind"})
    def test_djust_config_other_liveview_keys_too(self):
        # Not just max_message_size — any LiveView config key works as a fallback.
        # css_framework has an unambiguous non-default ("bootstrap5"), so this
        # stays gate-off-sensitive (event_security defaults to "strict" via
        # validation and would be tautological here).
        cfg = LiveViewConfig()
        assert cfg.get("css_framework") == "tailwind"
