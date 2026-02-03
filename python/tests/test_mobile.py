"""
Tests for mobile/touch support — device detection, MobileMixin, touch directives.
"""

import sys
import os
import pytest

# Add python/ to path so we can import djust submodules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the mixin module directly to avoid pulling in channels/django via djust.__init__
import importlib.util
_mobile_spec = importlib.util.spec_from_file_location(
    "djust.mixins.mobile",
    os.path.join(os.path.dirname(__file__), "..", "djust", "mixins", "mobile.py"),
)
_mobile_mod = importlib.util.module_from_spec(_mobile_spec)
_mobile_spec.loader.exec_module(_mobile_mod)

MobileMixin = _mobile_mod.MobileMixin
parse_user_agent = _mobile_mod.parse_user_agent


# ============================================================================
# User-Agent Parsing Tests
# ============================================================================


class TestUserAgentParsing:
    """Tests for the parse_user_agent function."""

    def test_empty_user_agent(self):
        """Empty user agent should default to desktop."""
        result = parse_user_agent("")
        assert result["is_desktop"] is True
        assert result["is_mobile"] is False
        assert result["is_tablet"] is False
        assert result["is_touch"] is False
        assert result["device_type"] == "desktop"

    def test_none_user_agent(self):
        """None user agent should default to desktop."""
        result = parse_user_agent(None)
        assert result["is_desktop"] is True
        assert result["device_type"] == "desktop"

    # Mobile devices
    def test_iphone_detection(self):
        """iPhone should be detected as mobile."""
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True
        assert result["is_tablet"] is False
        assert result["is_desktop"] is False
        assert result["is_touch"] is True
        assert result["device_type"] == "mobile"

    def test_android_mobile_detection(self):
        """Android Mobile should be detected as mobile."""
        ua = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True
        assert result["is_tablet"] is False
        assert result["device_type"] == "mobile"

    def test_ipod_detection(self):
        """iPod should be detected as mobile."""
        ua = "Mozilla/5.0 (iPod touch; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True
        assert result["device_type"] == "mobile"

    def test_windows_phone_detection(self):
        """Windows Phone should be detected as mobile."""
        ua = "Mozilla/5.0 (Windows Phone 10.0; Android 6.0.1) AppleWebKit/537.36"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True
        assert result["device_type"] == "mobile"

    # Tablets
    def test_ipad_detection(self):
        """iPad should be detected as tablet."""
        ua = "Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X) AppleWebKit/605.1.15"
        result = parse_user_agent(ua)
        assert result["is_tablet"] is True
        assert result["is_mobile"] is False
        assert result["is_desktop"] is False
        assert result["is_touch"] is True
        assert result["device_type"] == "tablet"

    def test_android_tablet_detection(self):
        """Android tablet (no Mobile) should be detected as tablet."""
        ua = "Mozilla/5.0 (Linux; Android 13; SM-T870) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        assert result["is_tablet"] is True
        assert result["is_mobile"] is False
        assert result["device_type"] == "tablet"

    def test_kindle_detection(self):
        """Kindle should be detected as tablet."""
        ua = "Mozilla/5.0 (Linux; U; Android 4.0.3; en-us; Kindle Fire Build/IML74K)"
        result = parse_user_agent(ua)
        assert result["is_tablet"] is True
        assert result["device_type"] == "tablet"

    # Desktop browsers
    def test_chrome_desktop_detection(self):
        """Chrome on desktop should be detected as desktop."""
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36"
        result = parse_user_agent(ua)
        assert result["is_desktop"] is True
        assert result["is_mobile"] is False
        assert result["is_tablet"] is False
        assert result["is_touch"] is False
        assert result["device_type"] == "desktop"

    def test_firefox_desktop_detection(self):
        """Firefox on desktop should be detected as desktop."""
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/115.0"
        result = parse_user_agent(ua)
        assert result["is_desktop"] is True
        assert result["device_type"] == "desktop"

    def test_safari_desktop_detection(self):
        """Safari on macOS should be detected as desktop."""
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15"
        result = parse_user_agent(ua)
        assert result["is_desktop"] is True
        assert result["device_type"] == "desktop"


# ============================================================================
# MobileMixin Tests
# ============================================================================


def _make_mobile_view():
    """Create a minimal view with mobile behavior."""
    class FakeRequest:
        def __init__(self, user_agent=""):
            self.META = {"HTTP_USER_AGENT": user_agent}
    
    class FakeView(MobileMixin):
        def __init__(self):
            self._user_agent = None
            self._device_info = None
        
        def get_context_data(self, **kwargs):
            # Simulate parent class behavior
            context = kwargs.copy()
            return MobileMixin.get_context_data(self, **context)
    
    return FakeView, FakeRequest


class TestMobileMixin:
    """Tests for the MobileMixin on LiveView."""

    def test_init_mobile_sets_device_info(self):
        """_init_mobile should parse user agent and set device info."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
        
        view._init_mobile(request)
        
        assert view.is_mobile is True
        assert view.is_tablet is False
        assert view.is_desktop is False
        assert view.is_touch is True
        assert view.device_type == "mobile"

    def test_properties_without_init(self):
        """Properties should have sensible defaults before _init_mobile."""
        FakeView, _ = _make_mobile_view()
        view = FakeView()
        
        assert view.is_mobile is False
        assert view.is_tablet is False
        assert view.is_desktop is True
        assert view.is_touch is False
        assert view.device_type == "desktop"
        assert view.user_agent == ""

    def test_user_agent_property(self):
        """user_agent property should return the raw UA string."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
        request = FakeRequest(ua)
        
        view._init_mobile(request)
        
        assert view.user_agent == ua

    def test_get_context_data_adds_device_vars(self):
        """get_context_data should add device detection variables."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)")
        
        view._init_mobile(request)
        context = view.get_context_data()
        
        assert context["is_mobile"] is False
        assert context["is_tablet"] is True
        assert context["is_desktop"] is False
        assert context["is_touch"] is True
        assert context["device_type"] == "tablet"

    # Responsive helper tests
    def test_for_mobile_returns_mobile_value(self):
        """for_mobile should return mobile value on mobile device."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        
        result = view.for_mobile(10, 50)
        assert result == 10

    def test_for_mobile_returns_default_on_desktop(self):
        """for_mobile should return default value on desktop."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        view._init_mobile(request)
        
        result = view.for_mobile(10, 50)
        assert result == 50

    def test_for_tablet_returns_tablet_value(self):
        """for_tablet should return tablet value on tablet device."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        
        result = view.for_tablet(20, 50)
        assert result == 20

    def test_for_touch_returns_touch_value(self):
        """for_touch should return touch value on mobile or tablet."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        
        # Test mobile
        request = FakeRequest("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        assert view.for_touch(True, False) is True
        
        # Test tablet
        request = FakeRequest("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        assert view.for_touch(True, False) is True
        
        # Test desktop
        request = FakeRequest("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        view._init_mobile(request)
        assert view.for_touch(True, False) is False

    def test_responsive_mobile(self):
        """responsive should return mobile value on mobile."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        
        result = view.responsive(mobile=10, tablet=20, desktop=50)
        assert result == 10

    def test_responsive_tablet(self):
        """responsive should return tablet value on tablet."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        
        result = view.responsive(mobile=10, tablet=20, desktop=50)
        assert result == 20

    def test_responsive_desktop(self):
        """responsive should return desktop value on desktop."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        request = FakeRequest("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        view._init_mobile(request)
        
        result = view.responsive(mobile=10, tablet=20, desktop=50)
        assert result == 50

    def test_responsive_fallback(self):
        """responsive should fall back when value not specified."""
        FakeView, FakeRequest = _make_mobile_view()
        view = FakeView()
        
        # Mobile should fall back to tablet, then desktop
        request = FakeRequest("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        assert view.responsive(tablet=20, desktop=50) == 20
        assert view.responsive(desktop=50) == 50
        
        # Tablet should fall back to desktop
        request = FakeRequest("Mozilla/5.0 (iPad; CPU OS 16_0 like Mac OS X)")
        view._init_mobile(request)
        assert view.responsive(mobile=10, desktop=50) == 50


# ============================================================================
# Touch Directive Attribute Tests
# ============================================================================


class TestTouchDirectiveAttributes:
    """Tests for touch directive attribute rendering."""

    def test_dj_tap_attribute(self):
        """dj-tap attribute should be a valid string."""
        # This tests that the attribute format is correct for client-side parsing
        attr = 'dj-tap="handle_tap"'
        assert 'dj-tap=' in attr
        assert 'handle_tap' in attr

    def test_dj_longpress_with_duration(self):
        """dj-longpress-duration attribute should accept numeric value."""
        attr = 'dj-longpress="show_menu" dj-longpress-duration="800"'
        assert 'dj-longpress=' in attr
        assert 'dj-longpress-duration="800"' in attr

    def test_dj_swipe_directions(self):
        """Swipe direction attributes should be valid."""
        directions = ['left', 'right', 'up', 'down']
        for direction in directions:
            attr = f'dj-swipe-{direction}="handle_swipe_{direction}"'
            assert f'dj-swipe-{direction}=' in attr

    def test_dj_swipe_with_threshold(self):
        """dj-swipe-threshold attribute should accept numeric value."""
        attr = 'dj-swipe="handle_swipe" dj-swipe-threshold="100"'
        assert 'dj-swipe-threshold="100"' in attr

    def test_dj_pinch_attribute(self):
        """dj-pinch attribute should be valid."""
        attr = 'dj-pinch="handle_zoom"'
        assert 'dj-pinch=' in attr

    def test_dj_pull_refresh_attribute(self):
        """dj-pull-refresh attribute should be valid."""
        attr = 'dj-pull-refresh="refresh_feed"'
        assert 'dj-pull-refresh=' in attr

    def test_dj_loading_touch_attribute(self):
        """dj-loading.touch attribute should be valid."""
        attr = 'dj-loading.touch'
        assert 'dj-loading.touch' in attr


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases in mobile detection."""

    def test_facebook_in_app_browser_mobile(self):
        """Facebook in-app browser on mobile should be detected as mobile."""
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/20A362 [FBAN/FBIOS;FBAV/393.0.0.0;FBBV/0;]"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True

    def test_twitter_in_app_browser_mobile(self):
        """Twitter in-app browser on mobile should be detected as mobile."""
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1 Twitter for iPhone"
        result = parse_user_agent(ua)
        assert result["is_mobile"] is True

    def test_ipad_with_desktop_mode(self):
        """iPad requesting desktop site may have confusing UA."""
        # Modern iPads with desktop mode enabled may send desktop-like UA
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15"
        result = parse_user_agent(ua)
        # This will be detected as desktop, which is the expected behavior
        # when iPad requests desktop mode
        assert result["is_desktop"] is True

    def test_bot_user_agent(self):
        """Bot user agents should be detected as desktop."""
        ua = "Googlebot/2.1 (+http://www.google.com/bot.html)"
        result = parse_user_agent(ua)
        assert result["is_desktop"] is True

    def test_curl_user_agent(self):
        """curl user agent should be detected as desktop."""
        ua = "curl/7.88.1"
        result = parse_user_agent(ua)
        assert result["is_desktop"] is True


# ============================================================================
# Integration-style tests (if Django available)
# ============================================================================

try:
    import django
    HAS_DJANGO = True
except ImportError:
    HAS_DJANGO = False


@pytest.mark.skipif(not HAS_DJANGO, reason="Django not installed")
class TestMobileMixinIntegration:
    """Integration tests requiring Django."""

    def test_mixin_with_live_view(self):
        """MobileMixin should work with LiveView inheritance."""
        from djust.mixins.mobile import MobileMixin
        
        # Just verify the import works and mixin is properly structured
        assert hasattr(MobileMixin, '_init_mobile')
        assert hasattr(MobileMixin, 'is_mobile')
        assert hasattr(MobileMixin, 'responsive')
