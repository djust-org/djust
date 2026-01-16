"""
Regression tests for component rendering and JSON serialization.

These tests ensure that components from both old and new locations
can be properly rendered in templates and serialized to JSON.

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
from djust.component import Component as OldComponent, LiveComponent as OldLiveComponent
from djust.components.base import Component as NewComponent, LiveComponent as NewLiveComponent
from djust.components.layout import NavbarComponent, NavItem


def add_session_to_request(request):
    """Add session middleware to request for testing"""
    middleware = SessionMiddleware(lambda r: r)
    middleware.process_request(request)
    request.session.save()
    return request


class DemoOldComponent(OldComponent):
    """Demo component using old Component base class"""

    template = '<div class="old-component">{{ message }}</div>'

    def __init__(self, message="Test"):
        super().__init__()
        self.message = message


class DemoOldLiveComponent(OldLiveComponent):
    """Demo component using old LiveComponent base class"""

    template = '<div class="old-live">{{ count }}</div>'

    def mount(self, count=0):
        self.count = count


class DemoNewComponent(NewComponent):
    """Demo component using new Component base class"""

    template = '<span class="new-component">{{ text }}</span>'

    def __init__(self, text="Hello"):
        super().__init__(text=text)
        self.text = text

    def get_context_data(self):
        return {"text": self.text}


class DemoNewLiveComponent(NewLiveComponent):
    """Demo component using new LiveComponent base class"""

    template_name = "not_used"  # Will be overridden by render()

    def mount(self, label="Click"):
        self.label = label

    def get_context(self):
        return {"label": self.label}

    def render(self):
        """Override render to use template string instead of template_name"""
        from django.utils.safestring import mark_safe
        from djust._rust import render_template

        context = self.get_context()
        html = render_template('<button class="new-live">{{ label }}</button>', context)
        return mark_safe(html)


class ComponentRenderingView(LiveView):
    """Test view that uses components in context"""

    template = """
    <div data-liveview-root>
        {{ old_component }}
        {{ old_live }}
        {{ new_component }}
        {{ new_live }}
        {{ navbar }}
    </div>
    """

    def mount(self, request, **kwargs):
        self.message = "Test message"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Add components from all sources
        context["old_component"] = DemoOldComponent("Old component").render()

        old_live = DemoOldLiveComponent()
        old_live.mount(count=42)
        context["old_live"] = old_live.render()

        context["new_component"] = DemoNewComponent("New component").render()

        new_live = DemoNewLiveComponent()
        new_live.mount(label="Click me")
        context["new_live"] = new_live.render()

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
    """Test that all component types can be JSON serialized"""

    def test_old_component_json_serializable(self):
        """Old Component instances should serialize to HTML string"""
        component = DemoOldComponent("Test message")

        # Should serialize without error
        result = json.dumps(component, cls=DjangoJSONEncoder)

        # Should contain the rendered HTML
        assert "old-component" in result
        assert "Test message" in result

    def test_old_livecomponent_json_serializable(self):
        """Old LiveComponent instances should serialize to HTML string"""
        component = DemoOldLiveComponent()
        component.mount(count=99)

        result = json.dumps(component, cls=DjangoJSONEncoder)

        assert "old-live" in result
        assert "99" in result

    def test_new_component_json_serializable(self):
        """New Component instances should serialize to HTML string"""
        component = DemoNewComponent("New text")

        result = json.dumps(component, cls=DjangoJSONEncoder)

        assert "new-component" in result
        assert "New text" in result

    def test_new_livecomponent_json_serializable(self):
        """New LiveComponent instances should serialize to HTML string"""
        component = DemoNewLiveComponent()
        component.mount(label="Press me")

        result = json.dumps(component, cls=DjangoJSONEncoder)

        assert "new-live" in result
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
            "old": DemoOldComponent("old"),
            "old_live": DemoOldLiveComponent(),
            "new": DemoNewComponent("new"),
            "new_live": DemoNewLiveComponent(),
        }

        # Initialize LiveComponents
        data["old_live"].mount(count=1)
        data["new_live"].mount(label="test")

        # Should serialize without error
        result = json.dumps(data, cls=DjangoJSONEncoder)

        assert "old-component" in result
        assert "old-live" in result
        assert "new-component" in result
        assert "new-live" in result


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
        assert '<div class="old-component">Old component</div>' in html
        assert 'class="old-live">42</div>' in html  # May include data-component-id attribute
        assert '<span class="new-component">New component</span>' in html
        assert '<button class="new-live">Click me</button>' in html

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
        assert "DemoOldComponent" not in html
        assert "DemoNewComponent" not in html
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
        assert isinstance(context["old_component"], str)
        assert isinstance(context["old_live"], str)
        assert isinstance(context["new_component"], str)
        assert isinstance(context["new_live"], str)
        assert isinstance(context["navbar"], str)

        # Should contain HTML, not repr
        assert '<div class="old-component">' in context["old_component"]
        assert "object at 0x" not in context["navbar"]

    def test_component_render_called_explicitly(self):
        """Calling .render() explicitly should return HTML string"""
        old_comp = DemoOldComponent("test")
        old_live = DemoOldLiveComponent()
        old_live.mount(count=1)
        new_comp = DemoNewComponent("test")
        new_live = DemoNewLiveComponent()
        new_live.mount(label="test")

        # All should return HTML strings
        assert isinstance(old_comp.render(), str)
        assert isinstance(old_live.render(), str)
        assert isinstance(new_comp.render(), str)
        assert isinstance(new_live.render(), str)

        # Should contain expected content
        assert "old-component" in old_comp.render()
        assert "old-live" in old_live.render()
        assert "new-component" in new_comp.render()
        assert "new-live" in new_live.render()


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
        assert '<div class="old-component">' in html
        assert '<span class="new-component">' in html
        assert "DemoOldComponent object at" not in html
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
            if k in ["old_component", "old_live", "new_component", "new_live", "navbar"]
        )
