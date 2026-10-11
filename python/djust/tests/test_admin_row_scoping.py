"""The djust admin loads rows through the ModelAdmin's ``get_queryset(request)``.

A ModelAdmin that scopes rows in ``get_queryset`` (a multi-tenant admin: each
staff user sees their own rows) used to be honoured by the change list only.
``run_action`` built its queryset from ``Model.objects`` and the selected pks,
which the client chooses, and the change and delete views loaded their object
with ``Model.objects.get``. Like Django's admin, they now go through the
ModelAdmin: an action runs on the selected rows its queryset holds (others are
ignored), and the change and delete views of a row it leaves out are a 404.
"""

import pytest
from django.contrib.auth.models import Group, Permission
from django.http import Http404
from django.test import RequestFactory
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.admin_ext.views import ModelDeleteView, ModelDetailView, ModelListView

pytestmark = pytest.mark.admin

ACTED: list = []


def record_rows(request, queryset):
    ACTED.append(sorted(queryset.values_list("name", flat=True)))


record_rows.short_description = "Record rows"


class ScopedGroupAdmin(DjustModelAdmin):
    """Each user sees the groups they belong to."""

    list_display = ["name"]
    actions = ["delete_selected", record_rows]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(user=request.user)


class OpenGroupAdmin(DjustModelAdmin):
    list_display = ["name"]
    actions = ["delete_selected", record_rows]


SCOPED = "rowscope_admin"
scoped_site = DjustAdminSite(name=SCOPED)
scoped_site.register(Group, ScopedGroupAdmin)
OPEN = "rowscope_open_admin"
open_site = DjustAdminSite(name=OPEN)
open_site.register(Group, OpenGroupAdmin)

urlpatterns = [path("rowscope/", scoped_site.urls), path("rowscope-open/", open_site.urls)]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__
    ACTED.clear()


@pytest.fixture
def rows(db, django_user_model):
    staff = django_user_model.objects.create_user("staff", password="pw", is_staff=True)
    staff.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label="auth",
            codename__in=["view_group", "change_group", "delete_group"],
        )
    )
    staff = django_user_model.objects.get(pk=staff.pk)
    mine = Group.objects.create(name="mine")
    theirs = Group.objects.create(name="theirs")
    staff.groups.add(mine)
    return staff, mine, theirs


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    return request


def _mounted(cls, user, site, suffix, **kwargs):
    request = _request(user)
    view = cls()
    view._view_registry_id = f"{site}_auth_group_{suffix}"
    view.request = request
    view.mount(request, **kwargs)
    return view


# ── run_action ──


def test_an_action_on_a_row_the_queryset_leaves_out_is_a_no_op(rows):
    staff, mine, theirs = rows
    view = _mounted(ModelListView, staff, SCOPED, "list")
    assert [g.name for g in view.get_queryset()] == ["mine"]

    view.toggle_select(theirs.pk)
    view.run_action("record_rows")
    assert ACTED == [[]]

    view.toggle_select(theirs.pk)
    view.run_action("delete_selected")
    assert Group.objects.filter(pk=theirs.pk).exists()


def test_an_action_on_a_mixed_selection_runs_on_the_scoped_rows_only(rows):
    staff, mine, theirs = rows
    view = _mounted(ModelListView, staff, SCOPED, "list")
    view.toggle_select(mine.pk)
    view.toggle_select(theirs.pk)
    view.run_action("record_rows")
    assert ACTED == [["mine"]]

    view.toggle_select(mine.pk)
    view.toggle_select(theirs.pk)
    view.run_action("delete_selected")
    assert list(Group.objects.values_list("name", flat=True)) == ["theirs"]


# ── change and delete views ──


@pytest.mark.parametrize("cls, suffix", [(ModelDetailView, "change"), (ModelDeleteView, "delete")])
def test_the_change_and_delete_views_of_a_row_left_out_are_a_404(rows, cls, suffix):
    staff, mine, theirs = rows
    for object_id in (theirs.pk, str(theirs.pk), 999999, "not-a-pk"):
        with pytest.raises(Http404):
            _mounted(cls, staff, SCOPED, suffix, object_id=object_id)
    view = _mounted(cls, staff, SCOPED, suffix, object_id=mine.pk)
    assert view.object == mine


def test_a_row_left_out_cannot_be_deleted_through_the_delete_view(rows):
    staff, mine, theirs = rows
    with pytest.raises(Http404):
        _mounted(ModelDeleteView, staff, SCOPED, "delete", object_id=theirs.pk)
    view = _mounted(ModelDeleteView, staff, SCOPED, "delete", object_id=mine.pk)
    view.confirm_delete()
    assert list(Group.objects.values_list("name", flat=True)) == ["theirs"]


# ── a ModelAdmin that does not scope rows is unchanged ──


def test_without_scoping_every_row_is_reachable_as_before(rows):
    staff, mine, theirs = rows
    view = _mounted(ModelListView, staff, OPEN, "list")
    view.toggle_select(mine.pk)
    view.toggle_select(theirs.pk)
    view.run_action("record_rows")
    assert ACTED == [["mine", "theirs"]]
    for cls, suffix in ((ModelDetailView, "change"), (ModelDeleteView, "delete")):
        assert _mounted(cls, staff, OPEN, suffix, object_id=theirs.pk).object == theirs
    detail = _mounted(ModelDetailView, staff, OPEN, "change", object_id=theirs.pk)
    assert detail.form_data["name"] == "theirs"


def test_get_object_returns_none_outside_the_queryset(rows):
    staff, mine, theirs = rows
    admin = ScopedGroupAdmin(Group, scoped_site)
    request = _request(staff)
    assert admin.get_object(request, mine.pk) == mine
    assert admin.get_object(request, theirs.pk) is None
    assert admin.get_object(request, "x") is None


# ── over HTTP ──


@pytest.mark.parametrize("page", ["change", "delete"])
def test_the_pages_of_a_row_left_out_are_a_404_over_http(rows, settings, page):
    from django.test import Client

    staff, mine, theirs = rows
    settings.ALLOWED_HOSTS = ["testserver"]
    client = Client()
    client.force_login(staff)
    response = client.get(f"/rowscope/auth/group/{theirs.pk}/{page}/")
    assert response.status_code == 404
    assert b"theirs" not in response.content
