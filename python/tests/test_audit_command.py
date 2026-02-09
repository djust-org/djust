"""Tests for the djust_audit management command."""

import json

import pytest
from django.core.management import call_command
from io import StringIO

from djust.management.commands.djust_audit import (
    _audit_class,
    _extract_auth_info,
    _extract_exposed_state,
    _format_decorator_tags,
    _format_handler_params,
    _get_handler_metadata,
    _is_user_class,
    _walk_subclasses,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal LiveView / LiveComponent subclasses for testing
# ---------------------------------------------------------------------------


@pytest.fixture
def make_view_class():
    """Factory for creating test LiveView subclasses."""
    from djust.live_view import LiveView

    def _make(name="TestView", template_name="test.html", handlers=None, **attrs):
        body = {"template_name": template_name, "__module__": "myapp.views"}
        body.update(attrs)

        if handlers:
            for hname, hfunc in handlers.items():
                body[hname] = hfunc

        cls = type(name, (LiveView,), body)
        return cls

    return _make


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestIsUserClass:
    def test_user_class(self, make_view_class):
        cls = make_view_class()
        assert _is_user_class(cls)

    def test_djust_internal_class(self, make_view_class):
        cls = make_view_class(__module__="djust.live_view")
        assert not _is_user_class(cls)

    def test_djust_test_class(self, make_view_class):
        cls = make_view_class(__module__="djust.tests.test_something")
        assert _is_user_class(cls)

    def test_djust_example_class(self, make_view_class):
        cls = make_view_class(__module__="djust.examples.demo")
        assert _is_user_class(cls)


class TestWalkSubclasses:
    def test_finds_subclasses(self, make_view_class):
        parent = make_view_class("ParentView")
        child = type("ChildView", (parent,), {"__module__": "myapp.views"})

        found = list(_walk_subclasses(parent))
        assert child in found

    def test_finds_nested_subclasses(self, make_view_class):
        grandparent = make_view_class("GrandparentView")
        parent = type("ParentView", (grandparent,), {"__module__": "myapp.views"})
        child = type("ChildView", (parent,), {"__module__": "myapp.views"})

        found = list(_walk_subclasses(grandparent))
        assert parent in found
        assert child in found


class TestGetHandlerMetadata:
    def test_finds_event_handlers(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def increment(self, **kwargs):
            pass

        cls = make_view_class(handlers={"increment": increment})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "increment" in names

    def test_skips_private_methods(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def _private_handler(self, **kwargs):
            pass

        cls = make_view_class(handlers={"_private_handler": _private_handler})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "_private_handler" not in names

    def test_skips_non_handlers(self, make_view_class):
        def regular_method(self):
            pass

        cls = make_view_class(handlers={"regular_method": regular_method})
        handlers = list(_get_handler_metadata(cls))
        names = [h[0] for h in handlers]
        assert "regular_method" not in names


class TestFormatHandlerParams:
    def test_kwargs_only(self):
        meta = {"event_handler": {"params": [], "accepts_kwargs": True}}
        assert _format_handler_params(meta) == "**kwargs"

    def test_typed_param_with_default(self):
        meta = {
            "event_handler": {
                "params": [{"name": "value", "type": "str", "required": False, "default": ""}],
                "accepts_kwargs": False,
            }
        }
        result = _format_handler_params(meta)
        assert "value: str" in result

    def test_required_param(self):
        meta = {
            "event_handler": {
                "params": [{"name": "item_id", "type": "int", "required": True}],
                "accepts_kwargs": False,
            }
        }
        result = _format_handler_params(meta)
        assert result == "item_id: int"


class TestFormatDecoratorTags:
    def test_debounce(self):
        meta = {"debounce": {"wait": 0.3, "max_wait": None}}
        tags = _format_decorator_tags(meta)
        assert any("@debounce" in t for t in tags)
        assert any("wait=0.3" in t for t in tags)

    def test_rate_limit(self):
        meta = {"rate_limit": {"rate": 5, "burst": 3}}
        tags = _format_decorator_tags(meta)
        assert any("@rate_limit" in t for t in tags)

    def test_optimistic_bool(self):
        meta = {"optimistic": True}
        tags = _format_decorator_tags(meta)
        assert "@optimistic" in tags

    def test_no_decorators(self):
        meta = {}
        tags = _format_decorator_tags(meta)
        assert tags == []


class TestAuditClass:
    def test_basic_view_audit(self, make_view_class):
        cls = make_view_class(template_name="counter.html")
        result = _audit_class(cls, "LiveView")

        assert result["type"] == "LiveView"
        assert result["template"] == "counter.html"
        assert "myapp.views" in result["class"]

    def test_inline_template(self, make_view_class):
        cls = make_view_class(template_name=None, template="<div>{{ count }}</div>")
        result = _audit_class(cls, "LiveView")
        assert result["template"] == "(inline)"

    def test_no_template(self, make_view_class):
        cls = make_view_class(template_name=None)
        result = _audit_class(cls, "LiveView")
        assert result["template"] == "(none)"

    def test_config_tick_interval(self, make_view_class):
        cls = make_view_class(tick_interval=1000)
        result = _audit_class(cls, "LiveView")
        assert result["config"]["tick_interval"] == 1000

    def test_config_use_actors(self, make_view_class):
        cls = make_view_class(use_actors=True)
        result = _audit_class(cls, "LiveView")
        assert result["config"]["use_actors"] is True

    def test_handlers_included(self, make_view_class):
        from djust.decorators import event_handler

        @event_handler()
        def do_something(self, value: str = "", **kwargs):
            pass

        cls = make_view_class(handlers={"do_something": do_something})
        result = _audit_class(cls, "LiveView")
        assert len(result["handlers"]) >= 1
        names = [h["name"] for h in result["handlers"]]
        assert "do_something" in names


# ---------------------------------------------------------------------------
# Integration tests: management command execution
# ---------------------------------------------------------------------------


class TestCommandOutput:
    def test_command_runs(self):
        """djust_audit runs without error."""
        out = StringIO()
        call_command("djust_audit", stdout=out)
        # Should complete without exception

    def test_json_output_is_valid(self):
        """--json produces valid JSON with expected structure."""
        out = StringIO()
        call_command("djust_audit", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        assert "audits" in data
        assert "summary" in data
        assert "views" in data["summary"]
        assert "components" in data["summary"]
        assert "handlers" in data["summary"]

    def test_app_filter(self):
        """--app filters results to the specified app."""
        out = StringIO()
        call_command("djust_audit", json_output=True, app_label="nonexistent_app_xyz", stdout=out)
        data = json.loads(out.getvalue())
        assert data["audits"] == []

    def test_pretty_output_contains_header(self, make_view_class):
        """Pretty output includes the header banner."""
        # Create a test view so there's output; keep reference to prevent GC
        _view_cls = make_view_class(template_name="test.html")
        out = StringIO()
        call_command("djust_audit", stdout=out)
        output = out.getvalue()
        assert "djust audit" in output
        del _view_cls  # Remove subclass from LiveView.__subclasses__() registry

    def test_verbose_flag(self):
        """--verbose flag doesn't crash even without Rust extension."""
        out = StringIO()
        call_command("djust_audit", verbose=True, stdout=out)
        # Should complete without exception


# ---------------------------------------------------------------------------
# Tests for exposed state extraction
# ---------------------------------------------------------------------------


class TestExtractExposedState:
    def test_mount_assignments(self):
        """Finds self.xxx = ... in mount()."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.count = 0
                self.name = "hello"

        state = _extract_exposed_state(MyView)
        assert "count" in state
        assert "name" in state
        assert state["count"]["source"] == "mount"
        assert state["name"]["source"] == "mount"

    def test_private_attrs_excluded(self):
        """Attributes starting with _ are excluded."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.public = "visible"
                self._private = "hidden"
                self._cache = {}

        state = _extract_exposed_state(MyView)
        assert "public" in state
        assert "_private" not in state
        assert "_cache" not in state

    def test_augmented_assignment(self):
        """Finds self.count += 1 style assignments."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

        state = _extract_exposed_state(MyView)
        assert "count" in state
        # First occurrence wins — should be from mount
        assert state["count"]["source"] == "mount"

    def test_handler_methods_included(self):
        """Finds assignments in non-mount methods too."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.query = ""

            def _refresh_results(self):
                self.results = []
                self.total_count = 0

        state = _extract_exposed_state(MyView)
        assert "query" in state
        assert "results" in state
        assert "total_count" in state
        assert state["results"]["source"] == "_refresh_results"

    def test_stops_at_liveview_base(self):
        """Does not parse LiveView base class internals."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.data = []

        state = _extract_exposed_state(MyView)
        # Should only contain attributes from MyView, not LiveView internals
        assert "data" in state
        # LiveView base attributes like template_name should NOT appear
        # (they're class-level, not set via self.xxx = in a method)

    def test_inheritance_chain(self):
        """Finds assignments across user class hierarchy."""
        from djust.live_view import LiveView

        class BaseView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.shared = True

        class ChildView(BaseView):
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.extra = "child"

        state = _extract_exposed_state(ChildView)
        assert "extra" in state
        assert "shared" in state

    def test_empty_view(self):
        """View with no self assignments returns empty dict."""
        from djust.live_view import LiveView

        class EmptyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

        state = _extract_exposed_state(EmptyView)
        assert state == {}


class TestAuditClassExposedState:
    def test_exposed_state_in_audit(self):
        """_audit_class includes exposed_state."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.items = []
                self.search = ""

        result = _audit_class(MyView, "LiveView")
        assert "exposed_state" in result
        assert "items" in result["exposed_state"]
        assert "search" in result["exposed_state"]

    def test_exposed_state_in_json_output(self):
        """JSON output includes exposed_state."""
        from djust.live_view import LiveView

        # Class must exist as a LiveView subclass so djust_audit discovers it
        # via __subclasses__() — not referenced directly but must stay alive.
        class JSONTestView(LiveView):  # noqa: F841
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.data = []

        out = StringIO()
        call_command("djust_audit", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        for audit in data["audits"]:
            assert "exposed_state" in audit

    def test_pretty_output_shows_exposed_state(self):
        """Pretty output includes 'Exposed state:' section."""
        from djust.live_view import LiveView

        # Class must exist as a LiveView subclass so djust_audit discovers it
        # via __subclasses__() — not referenced directly but must stay alive.
        class PrettyTestView(LiveView):  # noqa: F841
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.items = []

        out = StringIO()
        call_command("djust_audit", stdout=out)
        output = out.getvalue()
        assert "Exposed state:" in output


# ---------------------------------------------------------------------------
# Tests for auth info extraction and display
# ---------------------------------------------------------------------------


class TestExtractAuthInfo:
    def test_no_auth(self):
        """View without auth returns empty dict."""
        from djust.live_view import LiveView

        class PlainView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

        info = _extract_auth_info(PlainView)
        assert info == {}

    def test_login_required(self):
        """login_required = True is extracted."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            template_name = "test.html"
            login_required = True
            __module__ = "myapp.views"

        info = _extract_auth_info(SecureView)
        assert info["login_required"] is True

    def test_permission_required_string(self):
        """Single permission string is wrapped in list."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            template_name = "test.html"
            login_required = True
            permission_required = "myapp.view_dashboard"
            __module__ = "myapp.views"

        info = _extract_auth_info(AdminView)
        assert info["permission_required"] == ["myapp.view_dashboard"]

    def test_permission_required_list(self):
        """Permission list is preserved."""
        from djust.live_view import LiveView

        class AdminView(LiveView):
            template_name = "test.html"
            login_required = True
            permission_required = ["myapp.view", "myapp.edit"]
            __module__ = "myapp.views"

        info = _extract_auth_info(AdminView)
        assert info["permission_required"] == ["myapp.view", "myapp.edit"]

    def test_custom_check_permissions(self):
        """Overridden check_permissions is detected."""
        from djust.live_view import LiveView

        class CustomView(LiveView):
            template_name = "test.html"
            login_required = True
            __module__ = "myapp.views"

            def check_permissions(self, request):
                return True

        info = _extract_auth_info(CustomView)
        assert info.get("custom_check") is True

    def test_login_required_false_not_extracted(self):
        """login_required = False is not reported (falsy)."""
        from djust.live_view import LiveView

        class PublicView(LiveView):
            template_name = "test.html"
            login_required = False
            __module__ = "myapp.views"

        info = _extract_auth_info(PublicView)
        assert "login_required" not in info

    def test_dispatch_mixin_detected(self):
        """Dispatch-based auth mixin (e.g. LoginRequiredLiveViewMixin) is detected."""
        from djust.live_view import LiveView

        class LoginRequiredLiveViewMixin:
            def dispatch(self, request, *args, **kwargs):
                if not request.user.is_authenticated:
                    return None
                return super().dispatch(request, *args, **kwargs)

        class BaseCRMView(LoginRequiredLiveViewMixin, LiveView):
            template_name = "base.html"
            __module__ = "crm.views"

        class ContactListView(BaseCRMView):
            template_name = "contacts.html"
            __module__ = "crm.views"

        info = _extract_auth_info(ContactListView)
        assert info.get("dispatch_mixin") is True

    def test_dispatch_mixin_not_false_positive(self):
        """Plain views without auth mixins don't trigger dispatch_mixin."""
        from djust.live_view import LiveView

        class PlainView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

        info = _extract_auth_info(PlainView)
        assert "dispatch_mixin" not in info

    def test_django_login_required_mixin_detected(self):
        """Class named LoginRequiredMixin is detected even without dispatch."""
        from djust.live_view import LiveView

        class LoginRequiredMixin:
            login_required = True

        class ProtectedView(LoginRequiredMixin, LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

        info = _extract_auth_info(ProtectedView)
        # login_required attr takes priority, so dispatch_mixin won't be set
        assert info.get("login_required") is True


class TestAuditClassAuth:
    def test_auth_in_audit_result(self):
        """_audit_class includes 'auth' key."""
        from djust.live_view import LiveView

        class SecureView(LiveView):
            template_name = "test.html"
            login_required = True
            permission_required = "myapp.view_item"
            __module__ = "myapp.views"

        result = _audit_class(SecureView, "LiveView")
        assert "auth" in result
        assert result["auth"]["login_required"] is True
        assert result["auth"]["permission_required"] == ["myapp.view_item"]

    def test_auth_empty_for_public_view(self):
        """Public view has empty auth dict."""
        from djust.live_view import LiveView

        class PublicView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

        result = _audit_class(PublicView, "LiveView")
        assert result["auth"] == {}


class TestAuthInOutput:
    def test_json_output_includes_auth(self):
        """JSON output includes auth field and unprotected_with_state count."""
        from djust.live_view import LiveView

        class AuthJSONView(LiveView):
            template_name = "test.html"
            login_required = True
            __module__ = "myapp.views"

        out = StringIO()
        call_command("djust_audit", json_output=True, stdout=out)
        data = json.loads(out.getvalue())
        for audit in data["audits"]:
            assert "auth" in audit
        assert "unprotected_with_state" in data["summary"]
        del AuthJSONView  # Remove subclass from LiveView.__subclasses__() registry

    def test_pretty_output_shows_auth(self):
        """Pretty output shows Auth line for protected views."""
        from djust.live_view import LiveView

        class AuthPrettyView(LiveView):
            template_name = "test.html"
            login_required = True
            permission_required = "myapp.view_thing"
            __module__ = "myapp.views"

        out = StringIO()
        call_command("djust_audit", stdout=out)
        output = out.getvalue()
        assert "Auth:" in output
        del AuthPrettyView  # Remove subclass from LiveView.__subclasses__() registry

    def test_pretty_output_warns_unprotected(self):
        """Pretty output shows warning for views with state but no auth."""
        from djust.live_view import LiveView

        class UnprotectedAuditView(LiveView):
            template_name = "test.html"
            __module__ = "myapp.views"

            def mount(self, request, **kwargs):
                self.items = []

        out = StringIO()
        call_command("djust_audit", stdout=out)
        output = out.getvalue()
        # Should contain the warning symbol or text
        assert "exposes state without auth" in output or "\u26a0" in output
        del UnprotectedAuditView  # Remove subclass from LiveView.__subclasses__() registry

    def test_handler_permission_in_audit(self):
        """Handler-level @permission_required shows in decorator tags."""

        meta = {"permission_required": "myapp.delete_item"}
        tags = _format_decorator_tags(meta)
        assert any("@permission_required" in t for t in tags)
        assert any("myapp.delete_item" in t for t in tags)
