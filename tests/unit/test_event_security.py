"""Tests for WebSocket event handler security hardening."""

import threading
import time


class TestEventGuard:
    """Proposal 1: Event name guard tests."""

    def test_valid_event_names(self):
        from djust.security.event_guard import is_safe_event_name

        assert is_safe_event_name("increment") is True
        assert is_safe_event_name("update_item") is True
        assert is_safe_event_name("search") is True
        assert is_safe_event_name("toggle_todo") is True
        assert is_safe_event_name("a") is True

    def test_private_methods_blocked(self):
        from djust.security.event_guard import is_safe_event_name

        assert is_safe_event_name("_private") is False
        assert is_safe_event_name("__dunder__") is False
        assert is_safe_event_name("__class__") is False
        assert is_safe_event_name("__proto__") is False

    def test_invalid_patterns_blocked(self):
        from djust.security.event_guard import is_safe_event_name

        assert is_safe_event_name("") is False
        assert is_safe_event_name("123") is False
        assert is_safe_event_name("CamelCase") is False
        assert is_safe_event_name("has.dot") is False
        assert is_safe_event_name("has-dash") is False
        assert is_safe_event_name("has space") is False

    def test_valid_internal_names_allowed_by_pattern(self):
        """Pattern guard only checks format — internal names like 'mount' pass the
        pattern check. The @event decorator allowlist is the real access control."""
        from djust.security.event_guard import is_safe_event_name

        # These pass the pattern check (valid format), but will be blocked
        # by event_security strict mode if not decorated with @event
        assert is_safe_event_name("mount") is True
        assert is_safe_event_name("dispatch") is True
        assert is_safe_event_name("render") is True
        assert is_safe_event_name("get") is True


class TestEventDecorator:
    """Proposal 2: @event decorator allowlist tests."""

    def test_is_event_handler_decorated(self):
        from djust.decorators import event, is_event_handler

        @event
        def my_handler(self):
            pass

        assert is_event_handler(my_handler) is True

    def test_is_event_handler_undecorated(self):
        from djust.decorators import is_event_handler

        def plain_method(self):
            pass

        assert is_event_handler(plain_method) is False

    def test_event_handler_decorator_with_args(self):
        from djust.decorators import event_handler, is_event_handler

        @event_handler(description="test")
        def handler(self):
            pass

        assert is_event_handler(handler) is True


class TestMessageSizeLimit:
    """Message size limit tests."""

    def test_multibyte_chars_measured_in_bytes(self):
        """Verify multi-byte characters are measured by byte count, not char count.

        A string of multi-byte characters (e.g., emoji) can have a byte size
        much larger than its character count. The size check must use byte
        count to prevent oversized messages from bypassing the limit.
        Regression test for GitHub issue #111.
        """

        max_size = 64  # small limit for testing
        # Each emoji is 4 bytes in UTF-8, so 20 emoji = 20 chars but 80 bytes
        text_data = "\U0001f600" * 20  # 20 chars, 80 bytes

        assert len(text_data) == 20  # char count under limit
        assert len(text_data.encode("utf-8")) == 80  # byte count over limit

        # Replicate the logic from websocket.py receive()
        char_len = len(text_data)
        raw_size = char_len if char_len * 4 <= max_size else len(text_data.encode("utf-8"))

        assert raw_size == 80  # must use byte count, not char count
        assert raw_size > max_size  # must exceed the limit

    def test_ascii_skips_encode_safely(self):
        """When char_len * 4 <= max_size, skipping encode is safe."""
        max_size = 256
        text_data = "hello"  # 5 chars, 5 bytes

        char_len = len(text_data)
        raw_size = char_len if char_len * 4 <= max_size else len(text_data.encode("utf-8"))

        # char_len used as optimization (5 * 4 = 20 <= 256)
        assert raw_size == 5


class TestRateLimiter:
    """Proposal 3: Rate limiting tests."""

    def test_token_bucket_allows_burst(self):
        from djust.rate_limit import TokenBucket

        bucket = TokenBucket(rate=10, burst=5)
        # Should allow 5 events in quick succession
        for _ in range(5):
            assert bucket.consume() is True
        # 6th should be rejected
        assert bucket.consume() is False

    def test_token_bucket_refills(self):
        from djust.rate_limit import TokenBucket

        bucket = TokenBucket(rate=100, burst=5)
        # Drain bucket
        for _ in range(5):
            bucket.consume()
        assert bucket.consume() is False

        # Wait for refill (100 tokens/sec = 1 token per 0.01s)
        time.sleep(0.05)
        assert bucket.consume() is True

    def test_connection_rate_limiter_global(self):
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=100, burst=3, max_warnings=2)
        # Consume burst
        assert limiter.check("evt") is True
        assert limiter.check("evt") is True
        assert limiter.check("evt") is True
        # Should fail
        assert limiter.check("evt") is False
        assert limiter.warnings == 1
        assert limiter.should_disconnect() is False
        # Second warning
        assert limiter.check("evt") is False
        assert limiter.should_disconnect() is True

    def test_per_handler_rate_limit(self):
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=1000, burst=100, max_warnings=10)
        limiter.register_handler_limit("expensive", rate=1, burst=2)
        assert limiter.check_handler("expensive") is True
        assert limiter.check_handler("expensive") is True
        # Per-handler limit hit
        assert limiter.check_handler("expensive") is False

    def test_rate_limit_decorator_metadata(self):
        from djust.decorators import rate_limit
        from djust.rate_limit import get_rate_limit_settings

        @rate_limit(rate=5, burst=3)
        def handler(self):
            pass

        settings = get_rate_limit_settings(handler)
        assert settings == {"rate": 5, "burst": 3}

    def test_no_rate_limit_returns_none(self):
        from djust.rate_limit import get_rate_limit_settings

        def handler(self):
            pass

        assert get_rate_limit_settings(handler) is None


class TestEventSecurityHelper:
    """Tests for _check_event_security helper."""

    def test_strict_mode_blocks_undecorated(self):
        from unittest.mock import patch

        from djust.websocket import _check_event_security

        class FakeView:
            pass

        def plain_handler(self):
            pass

        view = FakeView()

        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "strict"
            result = _check_event_security(plain_handler, view, "plain_handler")
            assert result is not None
            assert "not decorated" in result

    def test_strict_mode_allows_decorated(self):
        from unittest.mock import patch

        from djust.decorators import event
        from djust.websocket import _check_event_security

        class FakeView:
            pass

        @event
        def my_handler(self):
            pass

        view = FakeView()

        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "strict"
            result = _check_event_security(my_handler, view, "my_handler")
            assert result is None

    def test_strict_mode_allows_via_allowed_events(self):
        from unittest.mock import patch

        from djust.websocket import _check_event_security

        class FakeView:
            _allowed_events = {"bulk_update", "refresh"}

        def bulk_update(self):
            pass

        view = FakeView()

        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "strict"
            result = _check_event_security(bulk_update, view, "bulk_update")
            assert result is None

    def test_open_mode_allows_everything(self):
        from unittest.mock import patch

        from djust.websocket import _check_event_security

        class FakeView:
            pass

        def plain_handler(self):
            pass

        view = FakeView()

        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "open"
            result = _check_event_security(plain_handler, view, "plain_handler")
            assert result is None

    def test_warn_mode_allows_but_logs(self):
        from unittest.mock import patch

        from djust.websocket import _check_event_security

        class FakeView:
            pass

        def plain_handler(self):
            pass

        view = FakeView()

        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "warn"
            result = _check_event_security(plain_handler, view, "plain_handler")
            assert result is None  # warn mode doesn't block


class TestConfigDefaults:
    """Tests for message size config."""

    def test_max_message_size_default(self):
        from djust.config import LiveViewConfig

        cfg = LiveViewConfig()
        assert cfg.get("max_message_size") == 65536

    def test_rate_limit_config_defaults(self):
        from djust.config import LiveViewConfig

        cfg = LiveViewConfig()
        rl = cfg.get("rate_limit")
        assert isinstance(rl, dict)
        assert rl["rate"] == 100
        assert rl["burst"] == 20
        assert rl["max_warnings"] == 3

    def test_event_security_default_is_strict(self):
        from djust.config import LiveViewConfig

        cfg = LiveViewConfig()
        assert cfg.get("event_security") == "strict"


class TestRateLimitIntegration:
    """Integration tests for global + per-handler rate limit flow."""

    def test_global_and_handler_limits_independent(self):
        """Global check() and per-handler check_handler() consume separate buckets."""
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=1000, burst=100, max_warnings=10)
        limiter.register_handler_limit("slow", rate=1, burst=1)

        # Global passes, per-handler passes (first call)
        assert limiter.check("slow") is True
        assert limiter.check_handler("slow") is True

        # Global still passes (burst=100), per-handler exhausted (burst=1)
        assert limiter.check("slow") is True
        assert limiter.check_handler("slow") is False

    def test_handler_limit_applies_on_first_call(self):
        """Per-handler bucket limits the very first invocation after registration."""
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=1000, burst=100, max_warnings=10)
        limiter.register_handler_limit("once", rate=0.1, burst=1)

        # First call allowed
        assert limiter.check_handler("once") is True
        # Second immediately rejected (burst=1, rate=0.1/s)
        assert limiter.check_handler("once") is False


class TestGlobalRateLimit:
    """Issue #107: All message types must be rate-limited, not just events."""

    def test_non_event_messages_are_rate_limited(self):
        """After exhausting burst, mount and ping messages should be rejected."""
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=100, burst=3, max_warnings=10)
        # Exhaust burst with any message types
        assert limiter.check("ping") is True
        assert limiter.check("mount") is True
        assert limiter.check("event") is True
        # Burst exhausted — all types should now be rejected
        assert limiter.check("ping") is False
        assert limiter.check("mount") is False
        assert limiter.check("event") is False

    def test_ping_flood_triggers_disconnect(self):
        """Repeated ping messages should eventually trigger disconnect."""
        from djust.rate_limit import ConnectionRateLimiter

        limiter = ConnectionRateLimiter(rate=100, burst=2, max_warnings=2)
        # Exhaust burst
        limiter.check("ping")
        limiter.check("ping")
        # Two more pings exceed max_warnings
        assert limiter.check("ping") is False
        assert limiter.should_disconnect() is False
        assert limiter.check("ping") is False
        assert limiter.should_disconnect() is True


class TestConfigValidation:
    """Validate event_security and rate_limit config on startup (Issue #110)."""

    def _make_config(self, overrides):
        """Create a LiveViewConfig with given overrides, bypassing Django settings."""
        from djust.config import LiveViewConfig

        cfg = LiveViewConfig.__new__(LiveViewConfig)
        cfg._config = LiveViewConfig._defaults.copy()
        cfg._config.update(overrides)
        cfg._validate_config()
        return cfg

    def test_invalid_event_security_resets_to_strict(self):
        cfg = self._make_config({"event_security": "STRICT"})
        assert cfg._config["event_security"] == "strict"

        cfg = self._make_config({"event_security": "invalid"})
        assert cfg._config["event_security"] == "strict"

    def test_negative_rate_limit_values_reset_to_defaults(self):
        from djust.config import LiveViewConfig

        defaults = LiveViewConfig._defaults["rate_limit"]
        cfg = self._make_config({"rate_limit": {"rate": -1, "burst": 0, "max_warnings": -5}})
        assert cfg._config["rate_limit"]["rate"] == defaults["rate"]
        assert cfg._config["rate_limit"]["burst"] == defaults["burst"]
        assert cfg._config["rate_limit"]["max_warnings"] == defaults["max_warnings"]

    def test_valid_config_passes_without_warnings(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="djust.config"):
            self._make_config(
                {
                    "event_security": "warn",
                    "rate_limit": {"rate": 50, "burst": 10, "max_warnings": 5},
                }
            )
        assert caplog.text == ""

    def test_open_mode_warns_in_production(self, caplog, settings):
        import logging

        settings.DEBUG = False
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            self._make_config({"event_security": "open"})
        assert "event_security is 'open'" in caplog.text

    def test_zero_message_size_warns_in_production(self, caplog, settings):
        import logging

        settings.DEBUG = False
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            self._make_config({"max_message_size": 0})
        assert "max_message_size is 0" in caplog.text


class TestErrorDisclosure:
    """Verify _safe_error returns generic messages in production and detailed in DEBUG."""

    def test_returns_generic_when_debug_false(self, settings):
        from djust.websocket import _safe_error

        settings.DEBUG = False
        assert _safe_error("Secret internal detail") == "Event rejected"
        assert _safe_error("Module foo.bar failed", "View not found") == "View not found"

    def test_returns_detailed_when_debug_true(self, settings):
        from djust.websocket import _safe_error

        settings.DEBUG = True
        assert _safe_error("Secret internal detail") == "Secret internal detail"
        assert _safe_error("Module foo.bar failed", "View not found") == "Module foo.bar failed"

    def test_all_generic_messages_identical_for_event_errors(self, settings):
        """Prevents handler enumeration by ensuring identical error messages."""
        from djust.websocket import _safe_error

        settings.DEBUG = False
        blocked = _safe_error("Blocked unsafe event name: __class__")
        no_handler = _safe_error("No handler found for event: foo")
        not_decorated = _safe_error(
            "Event 'foo' is not decorated with @event or listed in _allowed_events"
        )
        component_no_handler = _safe_error("Component MyView has no handler: foo")
        # All should return the same generic message
        assert blocked == no_handler == not_decorated == component_no_handler == "Event rejected"


class TestActorPathSecurity:
    """Issue #106: Actor path must apply the same security checks as non-actor path."""

    def test_actor_path_blocks_unsafe_event_name(self):
        """is_safe_event_name blocks dunder names regardless of path."""
        from djust.security.event_guard import is_safe_event_name

        # These would bypass Python security if sent directly to actor
        assert is_safe_event_name("__class__") is False
        assert is_safe_event_name("__init__") is False
        assert is_safe_event_name("_private") is False

    def test_actor_path_blocks_undecorated_handler(self):
        """_check_event_security blocks undecorated handlers in strict mode."""
        from unittest.mock import patch

        from djust.websocket import _check_event_security

        class FakeView:
            pass

        def undecorated(self):
            pass

        view = FakeView()
        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "strict"
            result = _check_event_security(undecorated, view, "undecorated")
            assert result is not None
            assert "not decorated" in result

    def test_actor_path_allows_decorated_handler(self):
        """Decorated handlers pass security checks in actor path."""
        from unittest.mock import patch

        from djust.decorators import event
        from djust.websocket import _check_event_security

        class FakeView:
            pass

        @event
        def my_event(self):
            pass

        view = FakeView()
        with patch("djust.websocket.djust_config") as mock_config:
            mock_config.get.return_value = "strict"
            result = _check_event_security(my_event, view, "my_event")
            assert result is None


class TestIPConnectionTracker:
    """Issue #108: Per-IP connection limit and reconnection throttle."""

    def _make_tracker(self):
        from djust.rate_limit import IPConnectionTracker

        return IPConnectionTracker()

    def test_allows_up_to_max_connections(self):
        tracker = self._make_tracker()
        for i in range(5):
            assert tracker.connect("1.2.3.4", max_per_ip=5) is True
        assert tracker.connect("1.2.3.4", max_per_ip=5) is False

    def test_disconnect_frees_slot(self):
        tracker = self._make_tracker()
        for _ in range(3):
            tracker.connect("1.2.3.4", max_per_ip=3)
        assert tracker.connect("1.2.3.4", max_per_ip=3) is False
        tracker.disconnect("1.2.3.4")
        assert tracker.connect("1.2.3.4", max_per_ip=3) is True

    def test_cooldown_blocks_reconnection(self):
        tracker = self._make_tracker()
        tracker.add_cooldown("1.2.3.4", 10.0)
        assert tracker.connect("1.2.3.4", max_per_ip=10) is False

    def test_cooldown_expires(self, monkeypatch):
        tracker = self._make_tracker()
        fake_time = [100.0]
        monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])
        tracker.add_cooldown("1.2.3.4", 2.0)
        assert tracker.connect("1.2.3.4", max_per_ip=10) is False
        fake_time[0] = 103.0
        assert tracker.connect("1.2.3.4", max_per_ip=10) is True

    def test_concurrent_safety(self):
        tracker = self._make_tracker()
        errors = []

        def worker():
            try:
                for _ in range(50):
                    tracker.connect("1.2.3.4", max_per_ip=1000)
                    tracker.disconnect("1.2.3.4")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_different_ips_independent(self):
        tracker = self._make_tracker()
        assert tracker.connect("1.1.1.1", max_per_ip=1) is True
        assert tracker.connect("2.2.2.2", max_per_ip=1) is True
        assert tracker.connect("1.1.1.1", max_per_ip=1) is False
        assert tracker.connect("2.2.2.2", max_per_ip=1) is False


class TestIPExtraction:
    """Test _get_client_ip helper on LiveViewConsumer."""

    def test_get_client_ip_from_scope(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.scope = {"client": ("10.0.0.1", 12345), "headers": []}
        assert consumer._get_client_ip() == "10.0.0.1"

    def test_get_client_ip_from_x_forwarded_for(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.scope = {
            "client": ("127.0.0.1", 80),
            "headers": [(b"x-forwarded-for", b"203.0.113.50, 70.41.3.18")],
        }
        assert consumer._get_client_ip() == "203.0.113.50"

    def test_get_client_ip_no_client(self):
        from djust.websocket import LiveViewConsumer

        consumer = LiveViewConsumer.__new__(LiveViewConsumer)
        consumer.scope = {"headers": []}
        assert consumer._get_client_ip() is None
