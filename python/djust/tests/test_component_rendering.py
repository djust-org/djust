"""
Tests for component rendering and JSON serialization.

These tests ensure that components can be properly rendered in templates
and serialized to JSON.

Regression: Components were not being JSON serialized correctly, causing
TypeError when adding NavbarComponent to context.
"""

# Configure Django settings BEFORE any djust imports
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
        ],
        # Use signed cookie sessions to avoid database dependency
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    django.setup()

import json
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

from djust.live_view import LiveView, DjangoJSONEncoder
from djust.components.base import Component, LiveComponent
from djust.components.layout import NavbarComponent, NavItem


def add_session_to_request(request):
    """Add session middleware to request for testing"""
    middleware = SessionMiddleware(lambda r: r)
    middleware.process_request(request)
    request.session.save()
    return request


class DemoComponent(Component):
    """Demo component using Component base class"""

    template = '<span class="demo-component">{{ text }}</span>'

    def __init__(self, text="Hello"):
        super().__init__(text=text)
        self.text = text

    def get_context_data(self):
        return {"text": self.text}


class DemoLiveComponent(LiveComponent):
    """Demo component using LiveComponent base class"""

    template = '<button class="demo-live-component">{{ label }}</button>'

    def mount(self, label="Click"):
        self.label = label

    def get_context_data(self):
        return {"label": self.label}


class ComponentRenderingView(LiveView):
    """Test view that uses components in context"""

    template = """
    <div data-djust-root>
        {{ component }}
        {{ live_component }}
        {{ navbar }}
    </div>
    """

    def mount(self, request, **kwargs):
        self.message = "Test message"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add components - pre-rendered to HTML strings
        context["component"] = DemoComponent("Test component").render()

        live = DemoLiveComponent()
        live.mount(label="Click me")
        context["live_component"] = live.render()

        # Add NavbarComponent (the original regression case)
        navbar = NavbarComponent(
            brand_name="Test",
            items=[
                NavItem("Home", "/", active=True),
                NavItem("About", "/about/"),
            ],
        )
        context["navbar"] = navbar.render()

        return context


class TestComponentJSONSerialization:
    """Test that component types can be JSON serialized"""

    def test_component_json_serializable(self):
        """Component instances should serialize to HTML string"""
        component = DemoComponent("Test message")

        # Should serialize without error
        result = json.dumps(component, cls=DjangoJSONEncoder)

        # Should contain the rendered HTML
        assert "demo-component" in result
        assert "Test message" in result

    def test_livecomponent_json_serializable(self):
        """LiveComponent instances should serialize to HTML string"""
        component = DemoLiveComponent()
        component.mount(label="Press me")

        result = json.dumps(component, cls=DjangoJSONEncoder)

        assert "demo-live-component" in result
        assert "Press me" in result

    def test_navbar_component_json_serializable(self):
        """NavbarComponent should serialize without error (regression test)"""
        navbar = NavbarComponent(
            brand_name="TestApp",
            brand_logo="/static/logo.png",
            items=[
                NavItem("Home", "/"),
                NavItem("Docs", "/docs/", badge=3),
            ],
        )

        # This was throwing TypeError before the fix
        result = json.dumps(navbar, cls=DjangoJSONEncoder)

        # Should contain navbar HTML
        assert "navbar" in result or "nav" in result

    def test_mixed_components_in_dict(self):
        """Multiple component types in a dict should all serialize"""
        data = {
            "component": DemoComponent("test"),
            "live_component": DemoLiveComponent(),
        }

        # Initialize LiveComponents
        data["live_component"].mount(label="test")

        # Should serialize without error
        result = json.dumps(data, cls=DjangoJSONEncoder)

        assert "demo-component" in result
        assert "demo-live-component" in result


class TestComponentRendering:
    """Test that components render correctly in LiveView templates"""

    @pytest.mark.django_db
    def test_component_renders_in_template(self):
        """Components should render to HTML when used in templates"""
        factory = RequestFactory()
        request = add_session_to_request(factory.get("/"))

        view = ComponentRenderingView()
        view.setup(request)
        view.mount(request)

        # Get rendered HTML
        response = view.get(request)
        html = response.content.decode("utf-8")

        # All components should be rendered as HTML
        assert '<span class="demo-component">Test component</span>' in html
        assert "demo-live-component" in html
        assert "Click me" in html

        # Navbar should render
        assert "navbar" in html.lower()
        assert "Home" in html
        assert "About" in html

    @pytest.mark.django_db
    def test_component_not_repr_in_template(self):
        """Components should NOT render as Python repr strings"""
        factory = RequestFactory()
        request = add_session_to_request(factory.get("/"))

        view = ComponentRenderingView()
        view.setup(request)
        view.mount(request)

        response = view.get(request)
        html = response.content.decode("utf-8")

        # Should NOT contain Python object representations
        assert "object at 0x" not in html
        assert "DemoComponent" not in html
        assert "DemoLiveComponent" not in html
        assert "NavbarComponent" not in html

    def test_navbar_renders_with_badge(self):
        """NavbarComponent with badge should render correctly (regression)"""
        navbar = NavbarComponent(
            brand_name="App",
            items=[
                NavItem("Notifications", "/notifications/", badge=5, badge_variant="danger"),
            ],
        )

        html = navbar.render()

        # Should render badge
        assert "badge" in html.lower()
        assert "5" in html


class TestComponentContextData:
    """Test that components work correctly in get_context_data"""

    @pytest.mark.django_db
    def test_rendered_component_in_context(self):
        """Pre-rendered component HTML should work in context"""
        factory = RequestFactory()
        request = add_session_to_request(factory.get("/"))

        view = ComponentRenderingView()
        view.setup(request)
        view.mount(request)

        context = view.get_context_data()

        # All context items should be HTML strings
        assert isinstance(context["component"], str)
        assert isinstance(context["live_component"], str)
        assert isinstance(context["navbar"], str)

        # Should contain HTML, not repr
        assert '<span class="demo-component">' in context["component"]
        assert "object at 0x" not in context["navbar"]

    def test_component_render_called_explicitly(self):
        """Calling .render() explicitly should return HTML string"""
        comp = DemoComponent("test")
        live = DemoLiveComponent()
        live.mount(label="test")

        # All should return HTML strings
        assert isinstance(comp.render(), str)
        assert isinstance(live.render(), str)

        # Should contain expected content
        assert "demo-component" in comp.render()
        assert "demo-live-component" in live.render()


class TestRustTemplateRenderer:
    """Test that Rust template renderer handles component HTML correctly"""

    @pytest.mark.django_db
    def test_rust_renders_component_html_not_repr(self):
        """Rust template engine should render HTML strings, not Python repr"""
        factory = RequestFactory()
        request = add_session_to_request(factory.get("/"))

        view = ComponentRenderingView()
        view.setup(request)
        view.mount(request)

        # This uses Rust template rendering
        response = view.get(request)
        html = response.content.decode("utf-8")

        # Rust should render the HTML string, not the Python object
        assert '<span class="demo-component">' in html
        assert "DemoComponent object at" not in html
        assert "NavbarComponent object at" not in html

    @pytest.mark.django_db
    def test_component_str_method_not_called_by_rust(self):
        """Rust renderer doesn't call __str__, so we pre-render components"""
        # This test documents the behavior that led to the bug:
        # Rust template renderer uses variable values as-is, doesn't call __str__()

        factory = RequestFactory()
        request = add_session_to_request(factory.get("/"))

        view = ComponentRenderingView()
        view.setup(request)
        view.mount(request)

        context = view.get_context_data()

        # We explicitly call .render() in get_context_data()
        # so context contains HTML strings, not component objects
        assert all(
            isinstance(v, str)
            for k, v in context.items()
            if k in ["component", "live_component", "navbar"]
        )
