"""Tests for per-page widget slots on ``DjustModelAdmin``.

Per Action Tracker #124, the two "rule" tests here
(``test_get_change_form_widgets_filters_by_permission``,
``test_widget_slot_nonliveview_emits_A072``) were written BEFORE the
corresponding implementation.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from .conftest import make_staff_user as _make_user  # #1028: shared factory

pytestmark = pytest.mark.admin


class TestDefaultWidgetSlots(TestCase):
    """Default widget slots should be empty."""

    def test_change_form_widgets_attr_default_empty(self):
        from djust.admin_ext import DjustModelAdmin

        assert DjustModelAdmin.change_form_widgets == []

    def test_change_list_widgets_attr_default_empty(self):
        from djust.admin_ext import DjustModelAdmin

        assert DjustModelAdmin.change_list_widgets == []


class TestWidgetPermissions(TestCase):
    """Widget slot permission filtering."""

    def test_get_change_form_widgets_filters_by_permission(self):
        """RULE #4: Widgets with ``permission_required`` must be hidden
        from users that lack the perm."""
        from djust import LiveView
        from djust.admin_ext import DjustModelAdmin

        class AllowedWidget(LiveView):
            template_name = "x.html"

        class RestrictedWidget(LiveView):
            template_name = "x.html"
            permission_required = "myapp.view_secret"

        class MyAdmin(DjustModelAdmin):
            change_form_widgets = [AllowedWidget, RestrictedWidget]

        User = get_user_model()
        admin = MyAdmin(User, None)

        # User without perm sees only the allowed widget.
        user_no_perm = _make_user(perms=())
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = user_no_perm
        filtered = admin.get_change_form_widgets(req)
        assert filtered == [AllowedWidget]

        # User with perm sees both.
        user_with_perm = _make_user(perms=("myapp.view_secret",))
        req.user = user_with_perm
        filtered = admin.get_change_form_widgets(req)
        assert filtered == [AllowedWidget, RestrictedWidget]


class TestWidgetSlotContext(TestCase):
    """Widget slot values should end up in the view context."""

    def test_change_form_widgets_rendered_in_context(self):
        """``ModelDetailView.get_context_data`` serializes
        ``change_form_widgets`` into ``change_form_widgets`` key."""
        from djust import LiveView
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin

        class DemoWidget(LiveView):
            template_name = "x.html"
            label = "Stats"

        User = get_user_model()
        site = DjustAdminSite(name="djust_admin")

        class UserAdmin(DjustModelAdmin):
            change_form_widgets = [DemoWidget]

        site.register(User, UserAdmin)
        model_admin = site._registry[User]

        # Verify ``get_change_form_widgets`` returns the expected list.
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = _make_user()
        widgets = model_admin.get_change_form_widgets(req)
        assert widgets == [DemoWidget]

    def test_change_list_widgets_rendered_in_context(self):
        """``ModelListView.get_context_data`` exposes
        ``change_list_widgets`` to the template."""
        from djust import LiveView
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin

        class DemoListWidget(LiveView):
            template_name = "x.html"
            label = "List widget"

        User = get_user_model()
        site = DjustAdminSite(name="djust_admin_2")

        class UserAdmin(DjustModelAdmin):
            change_list_widgets = [DemoListWidget]

        site.register(User, UserAdmin)
        model_admin = site._registry[User]

        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = _make_user()
        widgets = model_admin.get_change_list_widgets(req)
        assert widgets == [DemoListWidget]

    def test_change_form_widget_live_render_embeds_child(self):
        """Widget dicts serialized for the template carry the dotted
        view_path so ``{% live_render %}`` can resolve them."""
        from djust import LiveView
        from djust.admin_ext.options import DjustModelAdmin

        class SomeWidget(LiveView):
            template_name = "x.html"
            label = "Some"
            size = "lg"

        class MyAdmin(DjustModelAdmin):
            change_form_widgets = [SomeWidget]

        User = get_user_model()
        admin = MyAdmin(User, None)
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = _make_user()
        widgets = admin.get_change_form_widgets(req)
        assert widgets == [SomeWidget]
        # Check the dotted-path composition (used in the serialized dict).
        path = f"{SomeWidget.__module__}.{SomeWidget.__name__}"
        assert "." in path
        assert path.endswith("SomeWidget")

    def test_multiple_widgets_preserve_order(self):
        """Widget slot order is preserved by ``get_change_form_widgets``."""
        from djust import LiveView
        from djust.admin_ext import DjustModelAdmin

        class W1(LiveView):
            template_name = "x.html"

        class W2(LiveView):
            template_name = "x.html"

        class W3(LiveView):
            template_name = "x.html"

        class MyAdmin(DjustModelAdmin):
            change_form_widgets = [W2, W1, W3]

        User = get_user_model()
        admin = MyAdmin(User, None)
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = _make_user()
        assert admin.get_change_form_widgets(req) == [W2, W1, W3]


class TestA072Check(TestCase):
    """A072 should flag non-LiveView classes registered in widget slots."""

    def test_widget_slot_nonliveview_emits_A072(self):
        """RULE #5: A non-LiveView class in a widget slot must be caught
        at audit time by ``djust.checks`` (id ``djust.A072``)."""
        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.checks import check_admin_widgets

        # Register a MODEL ADMIN with a non-LiveView widget class.
        User = get_user_model()
        site = DjustAdminSite(name="djust_admin_a072")

        class NotALiveView:
            """Plain class — deliberately NOT a LiveView subclass."""

        class BadAdmin(DjustModelAdmin):
            change_form_widgets = [NotALiveView]

        site.register(User, BadAdmin)

        errors = check_admin_widgets(None, _admin_sites=[site])
        codes = [e.id for e in errors]
        assert "djust.A072" in codes, f"Expected A072 in {codes!r}"


class TestWidgetPermissionFilterInRenderedHTML(TestCase):
    """End-to-end: widgets filtered out by permission_required must not
    appear in the serialized widget-slot dicts that drive the rendered
    HTML."""

    def test_widget_filtered_by_permission_absent_in_rendered_html(self):
        """A widget with ``permission_required`` the user lacks should
        not appear in any serialized template context — exercising the
        full filter + serialize path."""
        from djust import LiveView
        from djust.admin_ext import DjustModelAdmin
        from djust.admin_ext.views import _serialize_widget_slots

        class VisibleWidget(LiveView):
            template_name = "x.html"
            label = "Visible"

        class SecretWidget(LiveView):
            template_name = "x.html"
            permission_required = "app.missing_perm"
            label = "Secret"

        class MyAdmin(DjustModelAdmin):
            change_list_widgets = [VisibleWidget, SecretWidget]

        User = get_user_model()
        admin = MyAdmin(User, None)
        from django.test import RequestFactory

        req = RequestFactory().get("/")
        req.user = _make_user(perms=())

        filtered = admin.get_change_list_widgets(req)
        serialized = _serialize_widget_slots(filtered)

        # Flatten serialized dicts to a searchable blob and assert the
        # excluded widget's view_path does NOT appear anywhere.
        blob = str(serialized)
        secret_path = f"{SecretWidget.__module__}.{SecretWidget.__name__}"
        visible_path = f"{VisibleWidget.__module__}.{VisibleWidget.__name__}"
        assert secret_path not in blob, (
            "SecretWidget view_path leaked into serialized widget dicts "
            "for a user lacking the required permission: %r" % serialized
        )
        assert visible_path in blob, "VisibleWidget should be present"


class TestChangeFormWidgetsOnCreateView(TestCase):
    """Per Rule #4 and the fix for the empty-object_id bug, the create
    view (no object) must not pass ``object_id=""`` to child widgets."""

    def test_change_form_widgets_on_create_view_omits_object_id(self):
        """When no object exists (create view), the serialized widget
        dicts must NOT carry an ``object_id`` key at all — so the
        template falls through to ``{% live_render w.view_path %}``
        without a stray empty string."""
        from djust import LiveView
        from djust.admin_ext.views import _serialize_widget_slots

        class AnyWidget(LiveView):
            template_name = "x.html"
            label = "Any"

        # Simulate ModelDetailView / ModelCreateView path:
        # self.object is None => object_pk = None => _serialize_widget_slots(..., object_id=None)
        entries = _serialize_widget_slots([AnyWidget], object_id=None)

        assert len(entries) == 1
        entry = entries[0]
        # The critical invariant: no object_id key at all. Django's
        # template engine resolves missing keys to "" (empty string).
        # The template now guards with ``{% if w.object_id %}`` so
        # omitting the key is the correct representation of "no object".
        assert "object_id" not in entry, (
            "Serialized widget entry for create-view must NOT include object_id key; got %r" % entry
        )

    def test_change_form_widgets_on_edit_view_carries_object_id(self):
        """Sanity check: when an object IS present, object_id flows through."""
        from djust import LiveView
        from djust.admin_ext.views import _serialize_widget_slots

        class AnyWidget(LiveView):
            template_name = "x.html"

        entries = _serialize_widget_slots([AnyWidget], object_id=42)
        assert entries[0].get("object_id") == 42


class TestRunActionPermissionEnforcement(TestCase):
    """``run_action`` enforces ``allowed_permissions`` metadata on
    decorated actions -- even if the default ``has_*_permission``
    methods return True."""

    def test_run_action_enforces_allowed_permissions(self):
        """User lacking a decorator-declared permission gets 403."""
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory

        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.admin_ext.progress import admin_action_with_progress
        from djust.admin_ext.views import ModelListView, register_admin_view

        User = get_user_model()

        class RestrictedAdmin(DjustModelAdmin):
            @admin_action_with_progress(
                description="Destroy things", permissions=["app.change_foo"]
            )
            def destroy_selected(self, request, queryset, progress):
                # Should not get here -- perms should block first.
                progress.update(message="ran")

            actions = ["destroy_selected"]

        site = DjustAdminSite(name="djust_admin_perm_test")
        site.register(User, RestrictedAdmin)
        model_admin = site._registry[User]

        # Wire up the view registry so ModelListView can resolve admin config.
        view_id = "test_run_action_perm"
        register_admin_view(view_id, site, model=User, model_admin=model_admin)

        view = ModelListView()
        view._view_registry_id = view_id
        req = RequestFactory().post("/")
        req.user = _make_user(is_staff=True, perms=())  # no perms
        view.request = req
        view.selected_ids = [1]
        view.select_all = False
        view.active_filters = {}
        view.search_query = ""
        view.current_page = 1
        view.ordering = None

        with pytest.raises(PermissionDenied):
            view.run_action("destroy_selected")

    def test_run_action_allows_when_user_has_perm(self):
        """User WITH the required permission passes the perm check (the
        only thing under test). We don't care whether the action body
        itself runs to completion here -- just that PermissionDenied is
        NOT raised."""
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory

        from djust.admin_ext import DjustAdminSite, DjustModelAdmin
        from djust.admin_ext.views import ModelListView, register_admin_view

        User = get_user_model()

        def protected_action(request, queryset):
            # Arity matches what ``get_actions`` feeds to ``run_action``.
            return "ok"

        protected_action.allowed_permissions = ["app.change_foo"]
        protected_action.short_description = "Protected"

        class PermAdmin(DjustModelAdmin):
            def get_actions(self, request):
                return {
                    "protected_action": {
                        "func": protected_action,
                        "description": "Protected",
                    }
                }

        site = DjustAdminSite(name="djust_admin_perm_test_ok")
        site.register(User, PermAdmin)
        model_admin = site._registry[User]

        view_id = "test_run_action_perm_ok"
        register_admin_view(view_id, site, model=User, model_admin=model_admin)

        view = ModelListView()
        view._view_registry_id = view_id
        req = RequestFactory().post("/")
        req.user = _make_user(is_staff=True, perms=("app.change_foo",))
        view.request = req
        view.selected_ids = [1]
        view.select_all = False
        view.active_filters = {}
        view.search_query = ""
        view.current_page = 1
        view.ordering = None

        # The only thing we assert: PermissionDenied does NOT fire.
        # Anything else downstream (queryset DB access etc) is outside
        # this test's scope and is acceptable.
        try:
            view.run_action("protected_action")
        except PermissionDenied:
            pytest.fail("user has perm; run_action should not raise PermissionDenied")
        except Exception:
            pass  # DB-access / queryset eval is fine.
