"""DataTableMixin sorts, filters, groups and expression-filters only on the
columns the view declares in ``table_columns``.

Sort honours the per-column ``sortable`` flag (default true, as the header
renders it); filter honours ``filterable`` (default false). Undeclared column
names sent by the client are ignored, both in the event handlers and in the
queryset pipeline itself.
"""

import pytest
from django.contrib.auth.models import Group, User

from djust.components.mixins.data_table import DataTableMixin


class GroupTable(DataTableMixin):
    table_model = Group
    table_page_size = 0
    table_columns = [
        {"key": "name", "label": "Name", "sortable": True, "filterable": True},
        {"key": "id", "label": "ID", "sortable": False},
    ]


@pytest.fixture
def groups(db):
    admins = Group.objects.create(name="admins")
    others = Group.objects.create(name="others")
    User.objects.create_user("root", password="s3cret").groups.add(admins)
    User.objects.create_user("bob", password="zzz").groups.add(others)
    return admins, others


@pytest.fixture
def view(groups):
    v = GroupTable()
    v.init_table_state()
    v.refresh_table()
    return v


def _names(view):
    return [r["name"] for r in view.table_rows]


# ── filter ──


def test_filter_on_declared_filterable_column_applies(view):
    view.on_table_filter(value="adm", column="name")
    assert _names(view) == ["admins"]
    assert view.table_filters == {"name": "adm"}


def test_filter_on_undeclared_related_column_is_ignored(view):
    view.on_table_filter(value="md5$", column="user__password")
    assert "user__password" not in view.table_filters
    assert sorted(_names(view)) == ["admins", "others"]


def test_filter_on_declared_but_not_filterable_column_is_ignored(view):
    view.on_table_filter(value="1", column="id")
    assert view.table_filters == {}


def test_filter_pipeline_skips_undeclared_keys_set_directly(view):
    view.table_filters = {"user__username": "root", "name": "o"}
    view.refresh_table()
    # Only the declared "name" filter runs; "user__username" is skipped.
    assert _names(view) == ["others"]


def test_filter_accepts_data_column_kwarg(view):
    view.on_table_filter(value="oth", **{"data-column": "name"})
    assert _names(view) == ["others"]


# ── sort ──


def test_sort_on_declared_sortable_column_applies(view):
    view.on_table_sort(value="name")
    assert view.table_sort_by == "name"
    assert _names(view) == ["admins", "others"]
    view.on_table_sort(value="name")
    assert view.table_sort_desc is True
    assert _names(view) == ["others", "admins"]


def test_sort_on_undeclared_related_column_is_ignored(view):
    view.on_table_sort(value="user__password")
    assert view.table_sort_by == ""


def test_sort_on_column_marked_not_sortable_is_ignored(view):
    view.on_table_sort(value="id")
    assert view.table_sort_by == ""


def test_sort_pipeline_skips_undeclared_value_set_directly(view):
    view.table_sort_by = "user__password"
    qs = view._apply_table_sort(Group.objects.all())
    assert not qs.query.order_by


def test_sort_column_without_flag_defaults_to_sortable(groups):
    class V(GroupTable):
        table_columns = [{"key": "name", "label": "Name"}]

    v = V()
    v.init_table_state()
    v.on_table_sort(value="name")
    assert v.table_sort_by == "name"


def test_default_sort_is_accepted_even_if_not_a_column(groups):
    class V(GroupTable):
        table_default_sort = "id"

    v = V()
    v.init_table_state()
    v.refresh_table()
    qs = v._apply_table_sort(Group.objects.all())
    assert list(qs.query.order_by) == ["id"]


def test_string_columns_are_sortable_not_filterable(groups):
    class V(GroupTable):
        table_columns = ["name"]

    v = V()
    v.init_table_state()
    v.on_table_sort(value="name")
    assert v.table_sort_by == "name"
    v.on_table_filter(value="adm", column="name")
    assert v.table_filters == {}


# ── group / expression ──


def test_group_by_declared_column_applies_and_undeclared_is_ignored(view):
    view.on_table_group(value="name")
    assert view.table_current_group_by == "name"
    view.on_table_group(value="password")
    assert view.table_current_group_by == "name"
    view.on_table_group(value="")
    assert view.table_current_group_by == ""


def test_expression_on_undeclared_column_is_ignored(view):
    view.on_table_expression(value={"column": "password", "expression": 'contains "md5"'})
    assert view.table_active_expressions == {}
    view.on_table_expression(value={"column": "name", "expression": 'contains "adm"'})
    assert view.table_active_expressions == {"name": 'contains "adm"'}


def test_apply_expression_filters_skips_undeclared_keys(view):
    view.table_active_expressions = {"secret": 'contains "x"', "name": 'contains "oth"'}
    rows = [{"name": "admins", "secret": "x"}, {"name": "others", "secret": "y"}]
    assert view.apply_expression_filters(rows) == [{"name": "others", "secret": "y"}]
