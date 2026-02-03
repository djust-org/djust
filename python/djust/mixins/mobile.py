"""
MobileMixin - Device detection and mobile viewport helpers for LiveView.

Provides device detection based on User-Agent parsing and responsive
breakpoint helpers for conditional server-side rendering.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# User-Agent patterns for device detection
MOBILE_PATTERNS = [
    r"Android.*Mobile",
    r"iPhone",
    r"iPod",
    r"BlackBerry",
    r"IEMobile",
    r"Opera Mini",
    r"Windows\s*Phone",
    r"webOS",
    r"Mobile Safari",
    r"Mobile.*Firefox",
]

TABLET_PATTERNS = [
    r"iPad",
    r"Android(?!.*Mobile)",  # Android without Mobile = tablet
    r"Tablet",
    r"PlayBook",
    r"Kindle",
    r"Silk",
]

# Compiled regex patterns for performance
_MOBILE_RE = re.compile("|".join(MOBILE_PATTERNS), re.IGNORECASE)
_TABLET_RE = re.compile("|".join(TABLET_PATTERNS), re.IGNORECASE)


def parse_user_agent(user_agent: str) -> dict:
    """
    Parse User-Agent string to extract device information.
    
    Returns:
        dict with keys:
            - is_mobile: bool
            - is_tablet: bool
            - is_desktop: bool (neither mobile nor tablet)
            - is_touch: bool (mobile or tablet)
            - device_type: "mobile" | "tablet" | "desktop"
    """
    if not user_agent:
        return {
            "is_mobile": False,
            "is_tablet": False,
            "is_desktop": True,
            "is_touch": False,
            "device_type": "desktop",
        }
    
    # Check mobile first - mobile patterns take precedence
    # (e.g., Windows Phone with Android should be mobile, not tablet)
    is_mobile = bool(_MOBILE_RE.search(user_agent))
    
    # Only check tablet if not already detected as mobile
    is_tablet = not is_mobile and bool(_TABLET_RE.search(user_agent))
    
    is_desktop = not is_mobile and not is_tablet
    
    if is_mobile:
        device_type = "mobile"
    elif is_tablet:
        device_type = "tablet"
    else:
        device_type = "desktop"
    
    return {
        "is_mobile": is_mobile,
        "is_tablet": is_tablet,
        "is_desktop": is_desktop,
        "is_touch": is_mobile or is_tablet,
        "device_type": device_type,
    }


class MobileMixin:
    """
    Adds mobile device detection and viewport helpers to a LiveView.
    
    Properties:
        is_mobile: True if the client is a mobile phone
        is_tablet: True if the client is a tablet
        is_desktop: True if the client is a desktop browser
        is_touch: True if the client likely supports touch (mobile or tablet)
        device_type: "mobile" | "tablet" | "desktop"
        user_agent: The raw User-Agent string
    
    Usage:
        class MyView(MobileMixin, LiveView):
            template_name = "my_view.html"
            
            def mount(self, request, **kwargs):
                if self.is_mobile:
                    self.items_per_page = 10
                else:
                    self.items_per_page = 25
    
    In templates:
        {% if is_mobile %}
            <nav class="mobile-nav">...</nav>
        {% else %}
            <nav class="desktop-nav">...</nav>
        {% endif %}
    """
    
    # Device detection state (set during mount)
    _user_agent: Optional[str] = None
    _device_info: Optional[dict] = None
    
    def _init_mobile(self, request) -> None:
        """
        Initialize mobile detection from request. Called automatically in mount.
        
        Args:
            request: Django HTTP request object
        """
        self._user_agent = request.META.get("HTTP_USER_AGENT", "")
        self._device_info = parse_user_agent(self._user_agent)
        
        logger.debug(
            f"[MobileMixin] Device detected: {self._device_info['device_type']} "
            f"(UA: {self._user_agent[:50]}...)" if len(self._user_agent) > 50 
            else f"[MobileMixin] Device detected: {self._device_info['device_type']}"
        )
    
    @property
    def is_mobile(self) -> bool:
        """True if the client is a mobile phone."""
        if self._device_info is None:
            return False
        return self._device_info["is_mobile"]
    
    @property
    def is_tablet(self) -> bool:
        """True if the client is a tablet."""
        if self._device_info is None:
            return False
        return self._device_info["is_tablet"]
    
    @property
    def is_desktop(self) -> bool:
        """True if the client is a desktop browser."""
        if self._device_info is None:
            return True
        return self._device_info["is_desktop"]
    
    @property
    def is_touch(self) -> bool:
        """True if the client likely supports touch (mobile or tablet)."""
        if self._device_info is None:
            return False
        return self._device_info["is_touch"]
    
    @property
    def device_type(self) -> str:
        """Returns "mobile", "tablet", or "desktop"."""
        if self._device_info is None:
            return "desktop"
        return self._device_info["device_type"]
    
    @property
    def user_agent(self) -> str:
        """The raw User-Agent string."""
        return self._user_agent or ""
    
    def get_context_data(self, **kwargs):
        """
        Add device detection variables to template context.
        
        Adds: is_mobile, is_tablet, is_desktop, is_touch, device_type
        """
        context = super().get_context_data(**kwargs) if hasattr(super(), 'get_context_data') else {}
        
        # Add device detection to context for templates
        context.update({
            "is_mobile": self.is_mobile,
            "is_tablet": self.is_tablet,
            "is_desktop": self.is_desktop,
            "is_touch": self.is_touch,
            "device_type": self.device_type,
        })
        
        return context
    
    # ========================================================================
    # Responsive Breakpoint Helpers
    # ========================================================================
    
    def for_mobile(self, mobile_value, default_value=None):
        """
        Return mobile_value if on mobile, otherwise default_value.
        
        Example:
            self.columns = self.for_mobile(1, 3)  # 1 column mobile, 3 desktop
        """
        return mobile_value if self.is_mobile else default_value
    
    def for_tablet(self, tablet_value, default_value=None):
        """
        Return tablet_value if on tablet, otherwise default_value.
        
        Example:
            self.columns = self.for_tablet(2, 3)  # 2 columns tablet, 3 desktop
        """
        return tablet_value if self.is_tablet else default_value
    
    def for_touch(self, touch_value, default_value=None):
        """
        Return touch_value if on touch device (mobile or tablet), otherwise default_value.
        
        Example:
            self.show_swipe_hint = self.for_touch(True, False)
        """
        return touch_value if self.is_touch else default_value
    
    def responsive(self, mobile=None, tablet=None, desktop=None):
        """
        Return the appropriate value based on device type.
        
        Example:
            self.items_per_page = self.responsive(mobile=10, tablet=20, desktop=50)
            self.layout = self.responsive(mobile="stack", desktop="grid")
        
        Falls back: mobile < tablet < desktop
        """
        if self.is_mobile:
            return mobile if mobile is not None else tablet if tablet is not None else desktop
        elif self.is_tablet:
            return tablet if tablet is not None else desktop if desktop is not None else mobile
        else:
            return desktop if desktop is not None else tablet if tablet is not None else mobile
