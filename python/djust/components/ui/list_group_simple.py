"""
Simple ListGroup component for djust - stateless, high-performance.

Provides list of items with optional actions, active highlighting, and variants.
This is a stateless Component optimized for performance.
"""

from typing import List, Dict
from ..base import Component


# Try to import Rust implementation (will be added later)
try:
    from djust._rust import RustListGroup

    _RUST_AVAILABLE = True
except (ImportError, AttributeError):
    _RUST_AVAILABLE = False
    RustListGroup = None


class ListGroup(Component):
    """
    Simple, stateless list group component with automatic Rust optimization.

    This component automatically uses pure Rust implementation if available,
    otherwise falls back to Python rendering with loops.

    Performance:
        - Pure Rust (if available): ~1μs per render
        - Pure Python fallback: ~50-100μs per render

    Use Cases:
        - Navigation menus
        - Action lists
        - Content lists
        - Status indicators
        - Settings panels

    Args:
        items: List of item dicts with 'label', 'url', 'active', 'disabled', 'variant' keys
        flush: Remove borders for edge-to-edge styling
        numbered: Display as numbered list

    Item format:
        {
            'label': 'Item Label',
            'url': '/path/to/page',      # Optional - creates link
            'active': False,              # Optional - highlight as active
            'disabled': False,            # Optional - disable interaction
            'variant': 'primary',         # Optional - color variant
            'badge': {'text': '5', 'variant': 'primary'},  # Optional - add badge
        }

    Examples:
        # Simple usage
        list_group = ListGroup(items=[
            {'label': 'Dashboard', 'url': '/dashboard', 'active': True},
            {'label': 'Profile', 'url': '/profile'},
            {'label': 'Settings', 'url': '/settings', 'disabled': True},
        ])
        html = list_group.render()

        # In template
        {{ list_group.render|safe }}

        # Flush variant (no borders)
        list_group = ListGroup(
            items=[
                {'label': 'Item 1', 'url': '/item1'},
                {'label': 'Item 2', 'url': '/item2'},
            ],
            flush=True
        )

        # Numbered list
        list_group = ListGroup(
            items=[
                {'label': 'First step', 'url': '/step1'},
                {'label': 'Second step', 'url': '/step2'},
                {'label': 'Third step', 'url': '/step3'},
            ],
            numbered=True
        )

        # With color variants
        list_group = ListGroup(items=[
            {'label': 'Success item', 'variant': 'success'},
            {'label': 'Warning item', 'variant': 'warning'},
            {'label': 'Danger item', 'variant': 'danger'},
        ])

        # With badges
        list_group = ListGroup(items=[
            {
                'label': 'Messages',
                'url': '/messages',
                'badge': {'text': '5', 'variant': 'primary'}
            },
            {
                'label': 'Notifications',
                'url': '/notifications',
                'badge': {'text': '12', 'variant': 'danger'}
            },
        ])
    """

    # Link to Rust implementation if available
    _rust_impl_class = RustListGroup if _RUST_AVAILABLE else None

    # Note: Not using template because list rendering with loops
    # is more reliable in Python, especially with complex item attributes

    def __init__(self, items: List[Dict[str, any]], flush: bool = False, numbered: bool = False):
        """
        Initialize list group component.

        Args:
            items: List of item dicts with 'label', 'url', 'active', 'disabled', 'variant' keys
            flush: Remove borders for edge-to-edge styling
            numbered: Display as numbered list
        """
        super().__init__(items=items, flush=flush, numbered=numbered)

        # Store for Python rendering
        self.items = items
        self.flush = flush
        self.numbered = numbered

    def get_context_data(self):
        """Context for hybrid rendering (if needed later)"""
        return {
            "items": self.items,
            "flush": self.flush,
            "numbered": self.numbered,
        }

    def _render_custom(self) -> str:
        """
        Custom Python rendering using loops.

        This provides framework-specific rendering for maximum compatibility.
        """
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return self._render_bootstrap()
        elif framework == "tailwind":
            return self._render_tailwind()
        else:
            return self._render_plain()

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 list group"""
        if not self.items:
            return '<ul class="list-group"></ul>'

        list_items = []
        list_class = "list-group"
        if self.flush:
            list_class += " list-group-flush"
        if self.numbered:
            list_class += " list-group-numbered"

        tag = "ol" if self.numbered else "ul"

        for item in self.items:
            label = item.get("label", "")
            url = item.get("url")
            active = item.get("active", False)
            disabled = item.get("disabled", False)
            variant = item.get("variant")
            badge = item.get("badge")

            # Build classes
            item_classes = ["list-group-item"]

            if url and not disabled:
                item_classes.append("list-group-item-action")

            if active:
                item_classes.append("active")

            if disabled:
                item_classes.append("disabled")

            if variant:
                item_classes.append(f"list-group-item-{variant}")

            class_str = " ".join(item_classes)

            # Build content with optional badge
            content = label
            if badge:
                badge_text = badge.get("text", "")
                badge_variant = badge.get("variant", "primary")
                content = f'{label}<span class="badge bg-{badge_variant} rounded-pill float-end">{badge_text}</span>'

            # Build item
            if url and not disabled:
                # Link item
                disabled_attr = ' aria-disabled="true"' if disabled else ""
                aria_current = ' aria-current="true"' if active else ""
                list_items.append(
                    f'  <a href="{url}" class="{class_str}"{disabled_attr}{aria_current}>{content}</a>'
                )
            else:
                # Regular item
                disabled_attr = ' aria-disabled="true"' if disabled else ""
                aria_current = ' aria-current="true"' if active else ""
                list_items.append(
                    f'  <li class="{class_str}"{disabled_attr}{aria_current}>{content}</li>'
                )

        list_html = "\n".join(list_items)

        return f"""<{tag} class="{list_class}">
{list_html}
</{tag}>"""

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS list group"""
        if not self.items:
            return '<ul class="divide-y divide-gray-200 rounded-lg border border-gray-200"></ul>'

        list_items = []

        # Base container classes
        container_classes = ["divide-y", "divide-gray-200"]
        if not self.flush:
            container_classes.extend(["rounded-lg", "border", "border-gray-200"])

        tag = "ol" if self.numbered else "ul"

        for i, item in enumerate(self.items):
            label = item.get("label", "")
            url = item.get("url")
            active = item.get("active", False)
            disabled = item.get("disabled", False)
            variant = item.get("variant")
            badge = item.get("badge")

            # Build classes
            item_classes = ["px-4", "py-3"]

            # Variant colors
            if variant == "primary":
                item_classes.extend(["bg-blue-50", "text-blue-900"])
            elif variant == "success":
                item_classes.extend(["bg-green-50", "text-green-900"])
            elif variant == "danger":
                item_classes.extend(["bg-red-50", "text-red-900"])
            elif variant == "warning":
                item_classes.extend(["bg-yellow-50", "text-yellow-900"])
            elif variant == "info":
                item_classes.extend(["bg-cyan-50", "text-cyan-900"])
            else:
                if active:
                    item_classes.extend(["bg-blue-600", "text-white"])
                else:
                    item_classes.extend(["bg-white", "text-gray-900"])

            if url and not disabled:
                item_classes.extend(["hover:bg-gray-50", "cursor-pointer"])

            if disabled:
                item_classes.extend(["opacity-50", "cursor-not-allowed"])

            class_str = " ".join(item_classes)

            # Build content with optional badge
            content_wrapper = "flex justify-between items-center"
            content = f"<span>{label}</span>"
            if badge:
                badge_text = badge.get("text", "")
                badge_variant = badge.get("variant", "primary")
                badge_color_map = {
                    "primary": "bg-blue-100 text-blue-800",
                    "secondary": "bg-gray-100 text-gray-800",
                    "success": "bg-green-100 text-green-800",
                    "danger": "bg-red-100 text-red-800",
                    "warning": "bg-yellow-100 text-yellow-800",
                    "info": "bg-cyan-100 text-cyan-800",
                }
                badge_colors = badge_color_map.get(badge_variant, badge_color_map["primary"])
                content = f"""<div class="{content_wrapper}">
      <span>{label}</span>
      <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium {badge_colors}">
        {badge_text}
      </span>
    </div>"""

            # Add number prefix if numbered
            if self.numbered:
                number = f'<span class="font-semibold mr-2">{i + 1}.</span>'
                if badge:
                    # Insert number at the beginning of the label span
                    content = content.replace("<span>", f"<span>{number}", 1)
                else:
                    content = f"{number}{content}"

            # Build item
            if url and not disabled:
                # Link item
                list_items.append(f'  <a href="{url}" class="{class_str}">{content}</a>')
            else:
                # Regular item
                list_items.append(f'  <li class="{class_str}">{content}</li>')

        list_html = "\n".join(list_items)
        container_class_str = " ".join(container_classes)

        return f"""<{tag} class="{container_class_str}">
{list_html}
</{tag}>"""

    def _render_plain(self) -> str:
        """Render plain HTML list group"""
        if not self.items:
            return '<ul class="list-group"></ul>'

        list_items = []
        list_class = "list-group"
        if self.flush:
            list_class += " list-group-flush"
        if self.numbered:
            list_class += " list-group-numbered"

        tag = "ol" if self.numbered else "ul"

        for i, item in enumerate(self.items):
            label = item.get("label", "")
            url = item.get("url")
            active = item.get("active", False)
            disabled = item.get("disabled", False)
            variant = item.get("variant")
            badge = item.get("badge")

            # Build classes
            item_classes = ["list-group-item"]

            if active:
                item_classes.append("list-group-item-active")

            if disabled:
                item_classes.append("list-group-item-disabled")

            if variant:
                item_classes.append(f"list-group-item-{variant}")

            class_str = " ".join(item_classes)

            # Build content with optional badge
            content = label
            if badge:
                badge_text = badge.get("text", "")
                badge_variant = badge.get("variant", "primary")
                content = f'{label} <span class="badge badge-{badge_variant}">{badge_text}</span>'

            # Add number prefix if numbered
            if self.numbered:
                content = f"{i + 1}. {content}"

            # Build item
            if url and not disabled:
                # Link item
                list_items.append(f'  <a href="{url}" class="{class_str}">{content}</a>')
            else:
                # Regular item
                list_items.append(f'  <li class="{class_str}">{content}</li>')

        list_html = "\n".join(list_items)

        return f"""<{tag} class="{list_class}">
{list_html}
</{tag}>"""
