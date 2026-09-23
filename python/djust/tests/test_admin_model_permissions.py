"""The admin_ext CRUD views apply the DjustModelAdmin permission hooks.

* The default hooks match ``django.contrib.admin.ModelAdmin``: they ask
  ``request.user.has_perm("<app>.<action>_<model>")``, and view access is
  granted by the view or the change permission.
* The list and add pages check the hooks before mount (``check_permissions``),
  the change and delete pages check them per object after mount
  (``get_object`` / ``has_object_permission``), so every transport applies
  them. ``save``/``form_valid``, ``confirm_delete`` and the ``delete_selected``
  bulk action check them again before writing.
"""

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.admin_ext.views import (
    ModelCreateView,
    ModelDeleteView,
    ModelDetailView,
    ModelListView,
)
from djust.auth.core import check_object_permission, check_view_auth

pytestmark = pytest.mark.admin

SITE_NAME = "permhooks_admin"
site = DjustAdminSite(name=SITE_NAME)
site.register(Group, DjustModelAdmin)


class NoDeleteAdmin(DjustModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


NO_DELETE_SITE_NAME = "permhooks_nodelete_admin"
no_delete_site = DjustAdminSite(name=NO_DELETE_SITE_NAME)
no_delete_site.register(Group, NoDeleteAdmin)

urlpatterns = [
    path("permhooks/", site.urls),
    path("permhooks-nodelete/", no_delete_site.urls),
]


def _registry_id(kind):
    return f"{SITE_NAME}_auth_group_{kind}"


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__


@pytest.fixture(autouse=True)
def _fresh_template_dirs():
    # The HTTP GET tests render djust_admin templates; a directory list cached
    # by an earlier test (e.g. one that overrode TEMPLATES) would hide them.
    from djust.utils import clear_template_dirs_cache

    clear_template_dirs_cache()
    yield
    clear_template_dirs_cache()


@pytest.fixture
def group(db):
    return Group.objects.create(name="editors")


def _staff(django_user_model, name, *codenames):
    user = django_user_model.objects.create_user(name, password="pw", is_staff=True)
    if codenames:
        user.user_permissions.set(
            Permission.objects.filter(content_type__app_label="auth", codename__in=codenames)
        )
    # Re-fetch so the permission cache reflects the assigned permissions.
    return django_user_model.objects.get(pk=user.pk)


def _request(user, method="get"):
    req = getattr(RequestFactory(), method)("/")
    req.user = user
    req.session = {}
    return req


def _view(cls, kind, request):
    view = cls()
    view._view_registry_id = _registry_id(kind)
    view.request = request
    return view


def _model_admin():
    return site._registry[Group]


# ── default hooks ──


def test_default_hooks_require_model_permissions(db, django_user_model):
    req = _request(_staff(django_user_model, "plain"))
    admin = _model_admin()
    assert admin.has_add_permission(req) is False
    assert admin.has_change_permission(req) is False
    assert admin.has_delete_permission(req) is False
    assert admin.has_view_permission(req) is False


def test_default_hooks_grant_assigned_permissions(db, django_user_model):
    req = _request(_staff(django_user_model, "editor", "add_group", "change_group"))
    admin = _model_admin()
    assert admin.has_add_permission(req) is True
    assert admin.has_change_permission(req) is True
    # change implies view, as in Django's ModelAdmin
    assert admin.has_view_permission(req) is True
    assert admin.has_delete_permission(req) is False


def test_default_hooks_grant_superuser(db, django_user_model):
    su = django_user_model.objects.create_superuser("root", password="pw")
    req = _request(su)
    admin = _model_admin()
    assert admin.has_add_permission(req)
    assert admin.has_change_permission(req)
    assert admin.has_delete_permission(req)
    assert admin.has_view_permission(req)


def test_app_list_only_shows_models_with_permissions(db, django_user_model):
    assert site.get_app_list(_request(_staff(django_user_model, "plain"))) == []
    viewer = _staff(django_user_model, "viewer", "view_group")
    names = [m["object_name"] for app in site.get_app_list(_request(viewer)) for m in app["models"]]
    assert names == ["Group"]


# ── pre-mount checks (list / add) ──


def test_list_view_requires_view_permission(db, django_user_model):
    req = _request(_staff(django_user_model, "plain"))
    # check_permissions denials come back as a redirect URL (not None).
    assert check_view_auth(_view(ModelListView, "list", req), req) is not None


def test_list_view_allows_view_permission(db, django_user_model):
    req = _request(_staff(django_user_model, "viewer", "view_group"))
    assert check_view_auth(_view(ModelListView, "list", req), req) is None


def test_create_view_requires_add_permission(db, django_user_model):
    req = _request(_staff(django_user_model, "viewer", "view_group"))
    assert check_view_auth(_view(ModelCreateView, "add", req), req) is not None
    req = _request(_staff(django_user_model, "adder", "add_group"))
    assert check_view_auth(_view(ModelCreateView, "add", req), req) is None


# ── per-object checks after mount (change / delete) ──


def test_delete_view_object_check_requires_delete_permission(group, django_user_model):
    req = _request(_staff(django_user_model, "editor", "change_group"))
    view = _view(ModelDeleteView, "delete", req)
    view.mount(req, object_id=group.pk)
    with pytest.raises(PermissionDenied):
        check_object_permission(view, req)

    req = _request(_staff(django_user_model, "deleter", "delete_group"))
    view = _view(ModelDeleteView, "delete", req)
    view.mount(req, object_id=group.pk)
    check_object_permission(view, req)


def test_detail_view_object_check_requires_view_or_change(group, django_user_model):
    req = _request(_staff(django_user_model, "adder", "add_group"))
    view = _view(ModelDetailView, "change", req)
    view.mount(req, object_id=group.pk)
    with pytest.raises(PermissionDenied):
        check_object_permission(view, req)

    req = _request(_staff(django_user_model, "viewer", "view_group"))
    view = _view(ModelDetailView, "change", req)
    view.mount(req, object_id=group.pk)
    check_object_permission(view, req)


def test_delete_page_http_get_is_forbidden_without_delete_permission(
    group, django_user_model, client
):
    client.force_login(_staff(django_user_model, "editor", "change_group"))
    resp = client.get(f"/permhooks/auth/group/{group.pk}/delete/")
    assert resp.status_code == 403
    assert Group.objects.filter(pk=group.pk).exists()


def test_delete_page_http_get_renders_with_delete_permission(group, django_user_model, client):
    client.force_login(_staff(django_user_model, "deleter", "delete_group"))
    resp = client.get(f"/permhooks/auth/group/{group.pk}/delete/")
    assert resp.status_code == 200
    assert b"confirm_delete" in resp.content


@pytest.mark.parametrize("url", ["/permhooks/auth/group/", "/permhooks/auth/group/add/"])
def test_list_and_add_pages_http_get_are_refused_without_permission(
    db, django_user_model, client, url
):
    client.force_login(_staff(django_user_model, "plain"))
    resp = client.get(url)
    assert resp.status_code in (302, 403)
    assert b"editors" not in resp.content


# ── handlers ──


def test_confirm_delete_requires_delete_permission(group, django_user_model):
    req = _request(_staff(django_user_model, "editor", "change_group"), "post")
    view = _view(ModelDeleteView, "delete", req)
    view.mount(req, object_id=group.pk)
    with pytest.raises(PermissionDenied):
        view.confirm_delete()
    assert Group.objects.filter(pk=group.pk).exists()


def test_confirm_delete_honours_overridden_hook(group, django_user_model):
    su = django_user_model.objects.create_superuser("root", password="pw")
    req = _request(su, "post")
    view = ModelDeleteView()
    view._view_registry_id = f"{NO_DELETE_SITE_NAME}_auth_group_delete"
    view.request = req
    view.mount(req, object_id=group.pk)
    with pytest.raises(PermissionDenied):
        check_object_permission(view, req)
    with pytest.raises(PermissionDenied):
        view.confirm_delete()
    assert Group.objects.filter(pk=group.pk).exists()


def test_confirm_delete_with_delete_permission_deletes(group, django_user_model):
    req = _request(_staff(django_user_model, "deleter", "delete_group"), "post")
    view = _view(ModelDeleteView, "delete", req)
    view.mount(req, object_id=group.pk)
    view.confirm_delete()
    assert not Group.objects.filter(pk=group.pk).exists()


def test_save_on_change_page_requires_change_permission(group, django_user_model):
    req = _request(_staff(django_user_model, "viewer", "view_group"), "post")
    view = _view(ModelDetailView, "change", req)
    view.mount(req, object_id=group.pk)
    view.form_data["name"] = "renamed"
    with pytest.raises(PermissionDenied):
        view.save()
    with pytest.raises(PermissionDenied):
        view.submit_form()
    group.refresh_from_db()
    assert group.name == "editors"


def test_save_on_change_page_with_change_permission_saves(group, django_user_model):
    req = _request(_staff(django_user_model, "editor", "change_group"), "post")
    view = _view(ModelDetailView, "change", req)
    view.mount(req, object_id=group.pk)
    view.form_data["name"] = "renamed"
    view.save(redirect=False)
    group.refresh_from_db()
    assert group.name == "renamed"


def test_save_on_add_page_requires_add_permission(db, django_user_model):
    req = _request(_staff(django_user_model, "editor", "change_group"), "post")
    view = _view(ModelCreateView, "add", req)
    view.mount(req)
    view.form_data["name"] = "new"
    with pytest.raises(PermissionDenied):
        view.save()
    with pytest.raises(PermissionDenied):
        view.submit_form()
    assert not Group.objects.filter(name="new").exists()


def test_save_on_add_page_with_add_permission_creates(db, django_user_model):
    req = _request(_staff(django_user_model, "adder", "add_group"), "post")
    view = _view(ModelCreateView, "add", req)
    view.mount(req)
    view.form_data["name"] = "new"
    view.save(redirect=False)
    assert Group.objects.filter(name="new").exists()


def test_bulk_delete_selected_requires_delete_permission(group, django_user_model):
    req = _request(_staff(django_user_model, "editor", "view_group", "change_group"), "post")
    view = _view(ModelListView, "list", req)
    view.mount(req)
    view.selected_ids = [group.pk]
    with pytest.raises(PermissionDenied):
        view.run_action("delete_selected")
    assert Group.objects.filter(pk=group.pk).exists()


def test_bulk_delete_selected_with_delete_permission_deletes(group, django_user_model):
    req = _request(_staff(django_user_model, "deleter", "view_group", "delete_group"), "post")
    view = _view(ModelListView, "list", req)
    view.mount(req)
    view.selected_ids = [group.pk]
    view.run_action("delete_selected")
    assert not Group.objects.filter(pk=group.pk).exists()
