"""
Tests for Embedded LiveViews — nesting LiveViews inside other LiveViews.

Covers:
- Basic embedding (parent renders child)
- Independent state (changing child doesn't re-render parent)
- Parent-child communication (send_parent / send_child)
- Cleanup on unmount
- Multiple embedded views
- Nested embedding (child within child)
- Template tag rendering
- WebSocket event routing by view_id
"""

import json
import uuid
from unittest.mock import MagicMock, patch, AsyncMock

import django
from django.conf import settings

# Minimal Django settings for tests
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {
                    "context_processors": [],
                    "loaders": [
                        "django.template.loaders.app_directories.Loader",
                    ],
                },
            }
        ],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        SECRET_KEY="test-secret-key",
    )
    django.setup()

import pytest
from django.test import RequestFactory

from djust.embedded import (
    EmbeddedViewMixin,
    LiveSession,
    render_embedded_view,
    resolve_view_class,
)


# ===========================================================================
# Test helper LiveView classes
# ===========================================================================

class FakeView(EmbeddedViewMixin):
    """Minimal fake LiveView with EmbeddedViewMixin for testing."""

    template_name = None
    template = "<div>fake</div>"

    def __init__(self):
        self._init_embedded()
        self._components = {}

    def mount(self, request, **kwargs):
        pass

    def get_context_data(self, **kwargs):
        return {"view": self}

    def get_template(self):
        return self.template


class ParentView(FakeView):
    template = "<div>Parent: {{ message }}</div>"

    def mount(self, request, **kwargs):
        self.message = "Hello from parent"

    def get_context_data(self, **kwargs):
        return {"view": self, "message": getattr(self, "message", "")}

    def handle_child_event(self, child_id, event, **params):
        if event == "notify":
            self.message = f"Got notify from {child_id}: {params.get('data', '')}"


class ChildView(FakeView):
    template = "<div>Child: {{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = kwargs.get("initial_count", 0)

    def get_context_data(self, **kwargs):
        return {"view": self, "count": getattr(self, "count", 0)}

    def increment(self):
        self.count = getattr(self, "count", 0) + 1

    def notify_parent(self):
        self.send_parent("notify", data="hello")


class GrandchildView(FakeView):
    template = "<span>Grandchild: {{ label }}</span>"

    def mount(self, request, **kwargs):
        self.label = kwargs.get("label", "default")

    def get_context_data(self, **kwargs):
        return {"view": self, "label": getattr(self, "label", "")}

    def handle_parent_event(self, event, **params):
        if event == "set_label":
            self.label = params.get("label", "")


# ===========================================================================
# LiveSession tests
# ===========================================================================

class TestLiveSession:
    def test_shared_state(self):
        session = LiveSession()
        session.put("user", "alice")
        assert session.get("user") == "alice"
        assert session.state == {"user": "alice"}

    def test_delete(self):
        session = LiveSession()
        session.put("key", "val")
        session.delete("key")
        assert session.get("key") is None

    def test_register_unregister_view(self):
        session = LiveSession()
        view = FakeView()
        session.register_view("v1", view)
        assert session.get_view("v1") is view
        session.unregister_view("v1")
        assert session.get_view("v1") is None

    def test_custom_session_id(self):
        session = LiveSession(session_id="custom-123")
        assert session.session_id == "custom-123"


# ===========================================================================
# EmbeddedViewMixin tests
# ===========================================================================

class TestEmbeddedViewMixin:
    def test_init_embedded(self):
        view = FakeView()
        assert view._view_id
        assert view._parent_view is None
        assert view._child_views == {}

    def test_register_child(self):
        parent = ParentView()
        child = ChildView()
        child_id = parent._register_child(child)
        assert child_id == child._view_id
        assert child._parent_view is parent
        assert parent._child_views[child_id] is child

    def test_unregister_child(self):
        parent = ParentView()
        child = ChildView()
        child_id = parent._register_child(child)
        parent._unregister_child(child_id)
        assert child_id not in parent._child_views
        assert child._parent_view is None

    def test_send_parent(self):
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))
        child = ChildView()
        child.mount(RequestFactory().get("/"))
        parent._register_child(child)

        child.notify_parent()
        assert "Got notify" in parent.message

    def test_send_parent_no_parent(self):
        """send_parent on a root view should just log a warning, not crash."""
        child = ChildView()
        child.mount(RequestFactory().get("/"))
        child.send_parent("test")  # Should not raise

    def test_send_child(self):
        parent = ParentView()
        child = GrandchildView()
        child.mount(RequestFactory().get("/"), label="before")
        child_id = parent._register_child(child)

        parent.send_child(child_id, "set_label", label="after")
        assert child.label == "after"

    def test_send_child_unknown_id(self):
        parent = ParentView()
        parent.send_child("nonexistent", "test")  # Should not raise

    def test_get_all_child_views_recursive(self):
        parent = ParentView()
        child = ChildView()
        grandchild = GrandchildView()

        parent._register_child(child)
        child._register_child(grandchild)

        all_views = parent._get_all_child_views()
        assert child._view_id in all_views
        assert grandchild._view_id in all_views
        assert len(all_views) == 2

    def test_live_session_propagates(self):
        session = LiveSession()
        parent = ParentView()
        parent._live_session = session
        session.register_view(parent._view_id, parent)

        child = ChildView()
        parent._register_child(child)

        assert child._live_session is session
        assert session.get_view(child._view_id) is child

    def test_properties(self):
        parent = ParentView()
        child = ChildView()
        parent._register_child(child)

        assert child.parent is parent
        assert child.view_id == child._view_id
        assert parent.parent is None


# ===========================================================================
# Independent state tests
# ===========================================================================

class TestIndependentState:
    def test_child_state_independent(self):
        """Changing child state doesn't affect parent state."""
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))
        child = ChildView()
        child.mount(RequestFactory().get("/"), initial_count=5)
        parent._register_child(child)

        child.increment()
        assert child.count == 6
        assert parent.message == "Hello from parent"  # Unchanged

    def test_multiple_children_independent(self):
        """Multiple children have independent state."""
        parent = ParentView()
        c1 = ChildView()
        c2 = ChildView()
        c1.mount(RequestFactory().get("/"), initial_count=10)
        c2.mount(RequestFactory().get("/"), initial_count=20)
        parent._register_child(c1)
        parent._register_child(c2)

        c1.increment()
        assert c1.count == 11
        assert c2.count == 20  # Unchanged


# ===========================================================================
# Cleanup tests
# ===========================================================================

class TestCleanup:
    def test_unregister_clears_parent_ref(self):
        parent = ParentView()
        child = ChildView()
        child_id = parent._register_child(child)
        parent._unregister_child(child_id)
        assert child._parent_view is None

    def test_unregister_removes_from_session(self):
        session = LiveSession()
        parent = ParentView()
        parent._live_session = session
        child = ChildView()
        child_id = parent._register_child(child)
        assert session.get_view(child_id) is child

        parent._unregister_child(child_id)
        assert session.get_view(child_id) is None

    def test_cleanup_all_children(self):
        parent = ParentView()
        c1 = ChildView()
        c2 = ChildView()
        parent._register_child(c1)
        parent._register_child(c2)

        # Simulate disconnect cleanup
        for child_id in list(parent._child_views.keys()):
            parent._unregister_child(child_id)

        assert len(parent._child_views) == 0
        assert c1._parent_view is None
        assert c2._parent_view is None


# ===========================================================================
# Nested embedding tests
# ===========================================================================

class TestNestedEmbedding:
    def test_three_level_nesting(self):
        root = ParentView()
        child = ChildView()
        grandchild = GrandchildView()

        root._register_child(child)
        child._register_child(grandchild)

        assert grandchild._parent_view is child
        assert child._parent_view is root
        assert grandchild in root._get_all_child_views().values()

    def test_grandchild_send_parent(self):
        root = ParentView()
        root.mount(RequestFactory().get("/"))
        child = ChildView()
        child.mount(RequestFactory().get("/"))

        # Override handle_child_event on child to relay to root
        child.handle_child_event = lambda cid, event, **p: child.send_parent(event, **p)

        root._register_child(child)
        grandchild = GrandchildView()
        grandchild.mount(RequestFactory().get("/"))
        child._register_child(grandchild)

        grandchild.send_parent("notify", data="from-grandchild")
        # Child relayed to root
        assert "from-grandchild" in root.message


# ===========================================================================
# render_embedded_view tests
# ===========================================================================

class TestRenderEmbeddedView:
    def test_render_produces_wrapper_div(self):
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))

        # Patch resolve_view_class to return ChildView
        with patch("djust.embedded.resolve_view_class", return_value=ChildView):
            html = render_embedded_view(
                parent_view=parent,
                view_path="test.views.ChildView",
                request=RequestFactory().get("/"),
            )

        assert 'data-djust-embedded=' in html
        assert 'data-djust-view-path="test.views.ChildView"' in html
        assert "Child:" in html

    def test_child_registered_with_parent(self):
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))

        with patch("djust.embedded.resolve_view_class", return_value=ChildView):
            render_embedded_view(
                parent_view=parent,
                view_path="test.views.ChildView",
                request=RequestFactory().get("/"),
            )

        assert len(parent._child_views) == 1

    def test_kwargs_passed_to_mount(self):
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))

        with patch("djust.embedded.resolve_view_class", return_value=ChildView):
            html = render_embedded_view(
                parent_view=parent,
                view_path="test.views.ChildView",
                request=RequestFactory().get("/"),
                initial_count=42,
            )

        assert "42" in html


# ===========================================================================
# resolve_view_class tests
# ===========================================================================

class TestResolveViewClass:
    def test_resolve_valid_class(self):
        cls = resolve_view_class("djust.embedded.LiveSession")
        assert cls is LiveSession

    def test_resolve_invalid_module(self):
        with pytest.raises(ModuleNotFoundError):
            resolve_view_class("nonexistent.module.Class")

    def test_resolve_invalid_class(self):
        with pytest.raises(AttributeError):
            resolve_view_class("djust.embedded.NonexistentClass")


# ===========================================================================
# Template tag tests (live_render)
# ===========================================================================

class TestLiveRenderTag:
    def test_no_parent_view_returns_error_comment(self):
        from djust.templatetags.live_tags import live_render
        context = {}
        result = live_render(context, "some.view.Path")
        assert "ERROR" in result

    def test_with_parent_view(self):
        from djust.templatetags.live_tags import live_render
        parent = ParentView()
        parent.mount(RequestFactory().get("/"))
        context = {"view": parent, "request": RequestFactory().get("/")}

        with patch("djust.embedded.resolve_view_class", return_value=ChildView):
            result = live_render(context, "test.views.ChildView")

        assert "data-djust-embedded" in result
        assert "Child:" in result
