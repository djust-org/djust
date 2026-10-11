"""The admin change list filters and sorts only on fields the ModelAdmin declares.

``apply_filter`` accepts the names in ``list_filter`` (``get_list_filter``),
``sort_by`` accepts the sortable ``list_display`` columns and the fields named
in ``ordering`` (``get_ordering``). ``get_queryset`` checks both again, so a
name stored in the view state by any other route is ignored as well. Anything
else (another lookup, a path through a relation, an expression) is a no-op.
"""

import pytest
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.admin_ext.views import ModelListView

pytestmark = pytest.mark.admin

SITE_NAME = "listlookups_admin"
site = DjustAdminSite(name=SITE_NAME)
site.register(Group, DjustModelAdmin)


class DeclaredGroupAdmin(DjustModelAdmin):
    list_display = ["name"]
    list_filter = ["name", "permissions__codename"]
    ordering = ["-id"]


DECLARED_SITE_NAME = "listlookups_declared_admin"
declared_site = DjustAdminSite(name=DECLARED_SITE_NAME)
declared_site.register(Group, DeclaredGroupAdmin)


class HookGroupAdmin(DjustModelAdmin):
    list_display = ["name"]

    def get_list_filter(self, request):
        return ["name"]


HOOK_SITE_NAME = "listlookups_hook_admin"
hook_site = DjustAdminSite(name=HOOK_SITE_NAME)
hook_site.register(Group, HookGroupAdmin)

urlpatterns = [
    path("listlookups/", site.urls),
    path("listlookups-declared/", declared_site.urls),
    path("listlookups-hook/", hook_site.urls),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__


def _viewer(django_user_model):
    """A staff user who may only view groups."""
    user = django_user_model.objects.create_user("viewer", password="pw", is_staff=True)
    user.user_permissions.set(
        Permission.objects.filter(content_type__app_label="auth", codename="view_group")
    )
    return django_user_model.objects.get(pk=user.pk)


def _list_view(user, site_name=SITE_NAME):
    request = RequestFactory().get("/")
    request.user = user
    request.session = {}
    view = ModelListView()
    view._view_registry_id = f"{site_name}_auth_group_list"
    view.request = request
    view.mount(request)
    return view


@pytest.fixture
def groups(db, django_user_model):
    root = django_user_model.objects.create_user("root", password="s3cret-root")
    admins = Group.objects.create(name="admins")
    editors = Group.objects.create(name="editors")
    root.groups.add(admins)
    return root, admins, editors


def _names(view):
    return [g.name for g in view.get_queryset()]


# ── undeclared names are refused ──


def test_filter_through_a_relation_gives_no_answer(groups, django_user_model):
    root, _, _ = groups
    view = _list_view(_viewer(django_user_model))
    prefix = root.password[:12]

    view.apply_filter("user__password__startswith", prefix)
    assert view.active_filters == {}
    hit = _names(view)
    view.apply_filter("user__password__startswith", prefix + "ZZZ")
    miss = _names(view)

    assert set(hit) == set(miss) == {"admins", "editors"}
    assert "password" not in str(view.get_queryset().query)


def test_sort_through_a_relation_is_refused(groups, django_user_model):
    view = _list_view(_viewer(django_user_model))
    for field in ("user__password", "-user__password", "user__password__length", "?", "pk"):
        view.sort_by(field)
        assert view.ordering is None, field
    assert "password" not in str(view.get_queryset().query)


@pytest.mark.parametrize(
    "field",
    [
        "name__startswith",  # a lookup list_filter does not declare
        "name__regex",
        "permissions__name",  # a different path than the declared one
        "permissions__codename__startswith",
        "user__password",
        "-name",
        "",
    ],
)
def test_undeclared_filter_on_a_declared_admin_is_refused(groups, django_user_model, field):
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.apply_filter(field, "a")
    assert view.active_filters == {}
    assert set(_names(view)) == {"admins", "editors"}


@pytest.mark.parametrize(
    "field", ["-name", "--name", "name__length", "user__password", "-user__password", "?"]
)
def test_undeclared_sort_on_a_declared_admin_is_refused(groups, django_user_model, field):
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.sort_by(field)
    assert view.ordering is None


def test_names_stored_in_state_are_ignored_by_the_queryset(groups, django_user_model):
    root, _, _ = groups
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.active_filters = {"user__password__startswith": root.password[:12]}
    view.ordering = "user__password"
    query = str(view.get_queryset().query)
    assert "password" not in query
    assert set(_names(view)) == {"admins", "editors"}

    view.ordering = "-user__password"
    assert "password" not in str(view.get_queryset().query)


# ── declared names keep working ──


def test_declared_filter_applies(groups, django_user_model):
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.apply_filter("name", "editors")
    assert view.active_filters == {"name": "editors"}
    assert _names(view) == ["editors"]
    view.apply_filter("name", "")
    assert view.active_filters == {}
    assert set(_names(view)) == {"admins", "editors"}


def test_declared_relation_filter_applies(groups, django_user_model):
    _, admins, _ = groups
    admins.permissions.add(Permission.objects.get(codename="view_group"))
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.apply_filter("permissions__codename", "view_group")
    assert _names(view) == ["admins"]


def test_declared_sort_cycles(groups, django_user_model):
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.sort_by("name")
    assert view.ordering == "name"
    assert _names(view) == ["admins", "editors"]
    view.sort_by("name")
    assert view.ordering == "-name"
    assert _names(view) == ["editors", "admins"]
    view.sort_by("name")
    assert view.ordering is None


def test_ordering_fields_are_sortable(groups, django_user_model):
    _, admins, editors = groups
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    view.sort_by("id")
    assert view.ordering == "id"
    assert _names(view) == [g.name for g in sorted([admins, editors], key=lambda g: g.pk)]


def test_get_list_filter_hook_is_honoured(groups, django_user_model):
    view = _list_view(_viewer(django_user_model), HOOK_SITE_NAME)
    view.apply_filter("name", "admins")
    assert _names(view) == ["admins"]
    context = view.get_context_data()
    assert [f["name"] for f in context["filters"]] == ["name"]


def test_change_list_context_offers_only_accepted_names(groups, django_user_model):
    view = _list_view(_viewer(django_user_model), DECLARED_SITE_NAME)
    context = view.get_context_data()
    sortable = [c["name"] for c in context["columns"] if c["sortable"]]
    for name in sortable:
        view.ordering = None
        view.sort_by(name)
        assert view.ordering == name
    for entry in context["filters"]:
        view.apply_filter(entry["name"], "x")
        assert entry["name"] in view.active_filters


# ── auth admin: social accounts list ──


def test_social_accounts_sort_accepts_only_its_columns():
    from djust.auth.admin_views import SocialAccountsView

    view = SocialAccountsView()
    view.ordering = "-date_joined"
    for field in ("user__password", "-user__password", "user__password__length", "?"):
        view.sort_by(field)
        assert view.ordering == "-date_joined", field
    view.sort_by("provider")
    assert view.ordering == "provider"
    view.sort_by("provider")
    assert view.ordering == "-provider"


@pytest.mark.skipif(
    not apps.is_installed("allauth.socialaccount"), reason="allauth.socialaccount not installed"
)
def test_social_accounts_queryset_ignores_an_undeclared_ordering(db):
    from djust.auth.admin_views import SocialAccountsView

    view = SocialAccountsView()
    view.ordering = "user__password"
    assert "ORDER BY" not in str(view._get_queryset().query)
    view.ordering = "provider"
    assert "provider" in str(view._get_queryset().query).split("ORDER BY")[1]
