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

    def test_blocklist_methods_rejected(self):
        from djust.security.event_guard import is_safe_event_name

        blocked = [
            "dispatch",
            "setup",
            "get",
            "post",
            "put",
            "patch",
            "delete",
            "head",
            "options",
            "trace",
            "http_method_not_allowed",
            "as_view",
            "mount",
            "render",
            "render_full_template",
            "render_with_diff",
            "get_context_data",
            "get_template",
            "get_debug_info",
            "handle_component_event",
            "update_component",
            "stream",
            "stream_insert",
            "stream_delete",
            "stream_reset",
            "update",
        ]
        for name in blocked:
            assert is_safe_event_name(name) is False, f"{name} should be blocked"


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
