"""
Component test harness (I27).

Provides ``ComponentTestCase`` — a base class with helpers for rendering
components and asserting structural, class, and accessibility properties
against the component contracts defined in ``djust_theming.contracts``.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=["django.contrib.contenttypes", "djust.theming"],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        STATIC_URL="/static/",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {
                    "context_processors": [],
                },
            }
        ],
    )
    django.setup()

import unittest
from html.parser import HTMLParser
from typing import Optional
from unittest.mock import MagicMock, patch

from djust.theming.contracts import COMPONENT_CONTRACTS


# ---------------------------------------------------------------------------
# Lightweight HTML helpers
# ---------------------------------------------------------------------------


class _ElementCollector(HTMLParser):
    """Collect all opening tags with their attributes from an HTML fragment."""

    def __init__(self):
        super().__init__()
        self.elements: list[tuple[str, dict[str, Optional[str]]]] = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def _parse_elements(html: str) -> list[tuple[str, dict[str, Optional[str]]]]:
    """Return list of (tag, attrs_dict) for every opening tag in *html*."""
    collector = _ElementCollector()
    collector.feed(html)
    return collector.elements


# ---------------------------------------------------------------------------
# ComponentTestCase
# ---------------------------------------------------------------------------


class ComponentTestCase(unittest.TestCase):
    """Base class for component template tests.

    Subclasses get helper methods for rendering component tags and making
    assertions about the resulting HTML.
    """

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _make_mock_request():
        request = MagicMock()
        request.COOKIES = {}
        request.session = {}
        request._djust_theme_manager = None
        return request

    def render_component(self, tag_name: str, **kwargs) -> str:
        """Render a component template tag and return the HTML string.

        Args:
            tag_name: One of ``button``, ``card``, ``badge``, ``alert``, ``input``,
                ``modal``, ``dropdown``, ``tabs``, ``table``, ``pagination``.
            **kwargs: Keyword arguments forwarded to the tag function.

        Returns:
            Rendered HTML as a string.
        """
        from djust.theming.templatetags.theme_components import (
            theme_alert,
            theme_avatar,
            theme_badge,
            theme_breadcrumb,
            theme_button,
            theme_card,
            theme_checkbox,
            theme_dropdown,
            theme_input,
            theme_modal,
            theme_nav,
            theme_nav_group,
            theme_nav_item,
            theme_pagination,
            theme_progress,
            theme_radio,
            theme_select,
            theme_sidebar_nav,
            theme_skeleton,
            theme_table,
            theme_tabs,
            theme_textarea,
            theme_toast,
            theme_tooltip,
        )

        tag_map = {
            "button": theme_button,
            "card": theme_card,
            "badge": theme_badge,
            "alert": theme_alert,
            "input": theme_input,
            "modal": theme_modal,
            "dropdown": theme_dropdown,
            "tabs": theme_tabs,
            "table": theme_table,
            "pagination": theme_pagination,
            "select": theme_select,
            "textarea": theme_textarea,
            "checkbox": theme_checkbox,
            "radio": theme_radio,
            "breadcrumb": theme_breadcrumb,
            "avatar": theme_avatar,
            "toast": theme_toast,
            "progress": theme_progress,
            "skeleton": theme_skeleton,
            "tooltip": theme_tooltip,
            "nav_item": theme_nav_item,
            "nav_group": theme_nav_group,
            "nav": theme_nav,
            "sidebar_nav": theme_sidebar_nav,
        }

        tag_fn = tag_map[tag_name]

        # Allow tests to set request.path for auto-active detection
        request_path = kwargs.pop("request_path", None)
        request = self._make_mock_request()
        if request_path is not None:
            request.path = request_path
        ctx = {"request": request}

        with patch.object(settings, "LIVEVIEW_CONFIG", {}, create=True):
            result = tag_fn(ctx, **kwargs)

        return str(result)

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def assert_has_element(self, html: str, tag: str, attrs: Optional[dict] = None):
        """Assert *html* contains at least one ``<tag>`` with matching *attrs*.

        Args:
            html: Rendered HTML string.
            tag: Expected tag name (e.g. ``"button"``).
            attrs: Optional dict of attribute key/value pairs.  A value of
                ``None`` means the attribute must be present but its value is
                not checked.
        """
        attrs = attrs or {}
        elements = _parse_elements(html)
        for el_tag, el_attrs in elements:
            if el_tag != tag:
                continue
            match = True
            for k, v in attrs.items():
                if k not in el_attrs:
                    match = False
                    break
                if v is not None and el_attrs[k] != v:
                    match = False
                    break
            if match:
                return
        self.fail(f"Expected <{tag}> with attrs {attrs} not found in HTML:\n{html}")

    def assert_has_class(self, html: str, class_name: str):
        """Assert at least one element in *html* has *class_name* in its class list."""
        elements = _parse_elements(html)
        for _, el_attrs in elements:
            classes = el_attrs.get("class", "") or ""
            if class_name in classes.split():
                return
        self.fail(f"Expected class '{class_name}' not found in any element in HTML:\n{html}")

    def assert_contains(self, html: str, text: str):
        """Assert *text* appears anywhere in *html*."""
        if text not in html:
            self.fail(f"Expected text '{text}' not found in HTML:\n{html}")

    def assert_not_contains(self, html: str, text: str):
        """Assert *text* does NOT appear in *html*."""
        if text in html:
            self.fail(f"Unexpected text '{text}' found in HTML:\n{html}")

    def assert_accessible(self, html: str, component_name: str):
        """Validate *html* against the accessibility requirements in the contract.

        Args:
            html: Rendered HTML string.
            component_name: Component name to look up in ``COMPONENT_CONTRACTS``.
        """
        contract = COMPONENT_CONTRACTS[component_name]
        elements = _parse_elements(html)

        for req in contract.accessibility:
            found = False
            for el_tag, el_attrs in elements:
                if req.attr not in el_attrs:
                    continue
                if req.value is not None and el_attrs[req.attr] != req.value:
                    continue
                found = True
                break
            if not found:
                self.fail(
                    f"Accessibility requirement not met for '{component_name}': "
                    f"{req.description} (attr={req.attr}, value={req.value})\n"
                    f"HTML:\n{html}"
                )

    def assert_contract(self, html: str, component_name: str):
        """Validate *html* against the full contract (elements + a11y).

        Args:
            html: Rendered HTML string.
            component_name: Component name to look up in ``COMPONENT_CONTRACTS``.
        """
        contract = COMPONENT_CONTRACTS[component_name]

        # Check required elements
        for req_el in contract.required_elements:
            self.assert_has_element(html, req_el.tag, req_el.attrs or None)

        # Check accessibility
        self.assert_accessible(html, component_name)
