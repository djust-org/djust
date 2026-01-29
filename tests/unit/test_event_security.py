"""Tests for WebSocket event handler security hardening."""

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
        assert limiter.check("expensive") is True
        assert limiter.check("expensive") is True
        # Per-handler limit hit
        assert limiter.check("expensive") is False

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


class TestMessageSizeLimit:
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
