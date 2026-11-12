"""#3966: ``add_row`` / ``remove_row`` keep every argument the formset was built with.

They rebuilt the formset as ``formset_cls(data, prefix=prefix)``, so an inline
formset lost its parent (``instance``), a model formset its ``queryset``, and
``form_kwargs`` / ``initial`` / ``auto_id`` were gone too. The formset is an
inline formset of ``auth.Permission`` under a ``ContentType``, a model formset
of the same rows, and a plain formset with ``initial`` and ``form_kwargs``.
"""

import pytest
from django import forms
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.forms import formset_factory, inlineformset_factory, modelformset_factory

from djust.formsets import FormSetHelpersMixin, add_row, remove_row
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from djust import state

ItemFormSet = inlineformset_factory(
    ContentType, Permission, fields=["name", "codename"], extra=1, can_delete=True
)
PermissionFormSet = modelformset_factory(Permission, fields=["name", "codename"], extra=1)

pytestmark = pytest.mark.django_db


def _order(tag="a"):
    parent = ContentType.objects.create(app_label="fs3966" + tag, model="order")
    for name in ("Alpha", "Beta"):
        Permission.objects.create(content_type=parent, name=name, codename=name.lower())
    return parent


def _names(formset):
    return [form["name"].value() for form in formset.forms]


# --------------------------------------------------------------------------
# The module-level helpers
# --------------------------------------------------------------------------


def test_add_row_keeps_the_inline_formsets_parent_and_existing_rows():
    parent = _order()
    updated = add_row(ItemFormSet, data={}, prefix="items", instance=parent)
    assert updated.instance == parent
    assert updated.initial_form_count() == 2
    assert updated.total_form_count() == 4  # two saved, the page's extra row, and the new one
    assert _names(updated)[:2] == ["Alpha", "Beta"]


def test_the_rebuilt_inline_formset_saves_new_rows_under_the_parent():
    parent = _order()
    updated = add_row(ItemFormSet, data={}, prefix="items", instance=parent)
    data = dict(updated.data)
    data["items-3-name"], data["items-3-codename"] = "Gamma", "gamma"
    saved = ItemFormSet(data, prefix="items", instance=parent)
    assert saved.is_valid(), saved.errors
    saved.save()
    assert sorted(parent.permission_set.values_list("codename", flat=True)) == [
        "alpha",
        "beta",
        "gamma",
    ]


def test_add_row_keeps_the_rows_the_caller_already_bound():
    parent = _order()
    first = add_row(ItemFormSet, data={}, prefix="items", instance=parent)
    data = dict(first.data)
    data["items-0-name"] = "Edited"
    second = add_row(ItemFormSet, data=data, prefix="items", instance=parent)
    assert second.total_form_count() == first.total_form_count() + 1
    assert _names(second)[0] == "Edited"
    assert second.instance == parent


def test_what_the_caller_bound_wins_over_the_formsets_own_rows():
    parent = _order()
    updated = add_row(
        ItemFormSet, data={"items-0-name": "Edited by the caller"}, prefix="items", instance=parent
    )
    assert _names(updated)[:2] == ["Edited by the caller", "Beta"]
    assert updated.total_form_count() == 4


def test_add_row_at_the_cap_still_returns_the_formset_with_its_parent_and_rows():
    parent = _order()
    Capped = inlineformset_factory(
        ContentType, Permission, fields=["name", "codename"], extra=0, max_num=2
    )
    updated = add_row(Capped, data={}, prefix="items", instance=parent)
    assert updated.instance == parent
    assert updated.total_form_count() == 2  # unchanged: already at max_num
    assert _names(updated) == ["Alpha", "Beta"]


def test_add_row_never_changes_the_data_it_is_given():
    parent = _order()
    data: dict = {}
    add_row(ItemFormSet, data=data, prefix="items", instance=parent)
    assert data == {}


def test_without_formset_kwargs_add_row_starts_from_the_data_as_before():
    updated = add_row(formset_factory(forms.Form, extra=1), data={}, prefix="p")
    assert updated.total_form_count() == 1


def test_remove_row_keeps_the_parent_and_marks_the_row_for_deletion():
    parent = _order()
    updated = remove_row(ItemFormSet, "items-0", data={}, prefix="items", instance=parent)
    assert updated.instance == parent
    assert updated.initial_form_count() == 2
    assert updated.is_valid(), updated.errors
    assert [form.instance.codename for form in updated.deleted_forms] == ["alpha"]
    updated.save()
    assert sorted(parent.permission_set.values_list("codename", flat=True)) == ["beta"]


def test_a_model_formsets_queryset_still_scopes_the_rows_after_add_row():
    mine, other = _order("m"), _order("o")
    foreign = other.permission_set.get(codename="alpha")
    scoped = Permission.objects.filter(content_type=mine)
    updated = add_row(PermissionFormSet, data={}, prefix="items", queryset=scoped)
    assert updated.initial_form_count() == 2
    assert _names(updated)[:2] == ["Alpha", "Beta"]
    # A posted row id from outside the scope changes nothing: without the queryset the
    # rebuilt formset falls back to every Permission, and saving it would update the
    # foreign row (a model formset trusts the ``id`` it is posted, within its queryset).
    data = dict(updated.data)
    data["items-0-id"] = str(foreign.pk)
    data["items-0-name"] = "Hijacked"
    again = add_row(PermissionFormSet, data=data, prefix="items", queryset=scoped)
    assert again.is_valid(), again.errors
    assert again.save() == []
    foreign.refresh_from_db()
    assert foreign.name == "Alpha"


def test_form_kwargs_initial_and_auto_id_reach_the_rebuilt_formset():
    class Tagged(forms.Form):
        street = forms.CharField(required=False)

        def __init__(self, *args, tag="", **kwargs):
            super().__init__(*args, **kwargs)
            self.tag = tag

    Plain = formset_factory(Tagged, extra=0)
    updated = add_row(
        Plain,
        data={},
        prefix="p",
        initial=[{"street": "Main"}],
        form_kwargs={"tag": "x"},
        auto_id="id_%s",
    )
    assert updated.total_form_count() == 2
    assert _names_of(updated, "street")[0] == "Main"
    assert all(form.tag == "x" for form in updated.forms)
    assert updated.forms[0]["street"].auto_id.startswith("id_")


def _names_of(formset, field):
    return [form[field].value() for form in formset.forms]


# --------------------------------------------------------------------------
# The mixin, through the events the template sends
# --------------------------------------------------------------------------


class OrderView(FormSetHelpersMixin, LiveView):
    exposure_policy = "explicit"
    order = state()
    items = state()
    template = "<div dj-root></div>"
    formset_classes = {"items": ItemFormSet}
    parent_pk = None  # the test's lever: the order the view opens

    def get_formset_kwargs(self, name):
        return {"instance": self.order}

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.order = ContentType.objects.get(pk=type(self).parent_pk)
        self.items = ItemFormSet(prefix="items", **self.get_formset_kwargs("items"))
        self._formset_data = {}


def _open(parent):
    OrderView.parent_pk = parent.pk
    client = LiveViewTestClient(OrderView)
    client.mount()
    return client


def test_the_mixins_add_row_event_keeps_the_instance_and_the_existing_rows():
    parent = _order()
    client = _open(parent)
    result = client.send_event("add_row", formset="items")
    assert result["success"], result
    items = client.view_instance.items
    assert items.instance == parent
    assert _names(items)[:2] == ["Alpha", "Beta"]
    assert items.total_form_count() == 4


def test_the_mixins_remove_row_event_keeps_the_instance():
    parent = _order()
    client = _open(parent)
    result = client.send_event("remove_row", formset="items", prefix="items-1")
    assert result["success"], result
    items = client.view_instance.items
    assert items.instance == parent
    assert items.is_valid(), items.errors
    assert [form.instance.codename for form in items.deleted_forms] == ["beta"]


def test_a_view_without_get_formset_kwargs_behaves_as_before():
    class Legacy(FormSetHelpersMixin, LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"
        formset_classes = {"rows": formset_factory(forms.Form, extra=1)}

        def mount(self, request, **kwargs):
            super().mount(request, **kwargs)
            self._formset_data = {}

    client = LiveViewTestClient(Legacy)
    client.mount()
    assert FormSetHelpersMixin().get_formset_kwargs("rows") == {}
    client.send_event("add_row", formset="rows")
    assert client.view_instance.rows.total_form_count() == 1


@pytest.mark.parametrize("reserved", ["data", "prefix"])
def test_get_formset_kwargs_may_not_set_the_data_or_the_prefix(reserved):
    class Bad(OrderView):
        def get_formset_kwargs(self, name):
            return {reserved: "x"}

    with pytest.raises(ValueError, match="data and prefix"):
        Bad()._formset_kwargs("items")
