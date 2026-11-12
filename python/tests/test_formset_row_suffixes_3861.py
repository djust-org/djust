"""#3861: the names ``remove_row`` exempts from ``max_form_fields`` are the fields a row REALLY has.

#3860 derived them from ``formset_cls.form.base_fields`` (+ the inline foreign key). A form that adds
fields in its ``__init__`` has more, and every one of their seeded row keys counted toward the limit, so
a legitimate large formset could not mark a single row for deletion. They now come from
``formset_cls().empty_form.fields``, computed once per class and never from the client's data.

The 3858 reproducers (invented suffix, zero-padded, Arabic-Indic, full-width, ``+0``, negative, forged
huge ``TOTAL_FORMS``) are ``test_form_data_followups_3858.py`` and are unchanged; the cases here are the
ones that file could not state.
"""

from __future__ import annotations

import gc
import logging
import weakref
from typing import Any, Dict, Optional

from django import forms
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.forms import ModelForm, formset_factory, inlineformset_factory, modelformset_factory
from django.test.utils import CaptureQueriesContext
from django.db import connection

import pytest

from djust import formsets
from djust.config import DEFAULT_MAX_FORM_FIELDS
from djust.formsets import FormSetHelpersMixin
from djust.live_view import LiveView

from test_form_data_bounds_3834 import limits as livecfg


class AddsInInit(forms.Form):
    """Declares one field and adds three more in ``__init__`` (the review's reproducer)."""

    street = forms.CharField(required=False)

    #: how many forms of this class were built; the cache must keep it flat across removals
    built = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        type(self).built += 1
        for k in (1, 2, 3):
            self.fields["d%d" % k] = forms.CharField(required=False)


AddsFormSet = formset_factory(AddsInInit, extra=0)


class View(FormSetHelpersMixin, LiveView):
    exposure_policy = "explicit"
    formset_classes: Dict[str, Any] = {}

    def mount(self, request=None, **kwargs):
        self._formset_data = {}


def view_for(cls, name: str = "fs") -> View:
    view = View()
    view.formset_classes = {name: cls}
    view.mount()
    return view


def seed(view: View, rows: int, fields, name: str = "fs", total: Optional[int] = None) -> None:
    data = view._formset_data
    data["%s-TOTAL_FORMS" % name] = str(rows if total is None else total)
    data["%s-INITIAL_FORMS" % name] = "0"
    data["%s-MIN_NUM_FORMS" % name] = "0"
    data["%s-MAX_NUM_FORMS" % name] = "9999"
    for i in range(rows):
        for field in fields:
            data["%s-%d-%s" % (name, i, field)] = "v"


def remove_all(view: View, rows: int, name: str = "fs") -> int:
    """Mark every row for deletion through the handler; the number whose ``-DELETE`` was stored."""
    for i in range(rows):
        view.remove_row(formset=name, prefix="%s-%d" % (name, i))
    return sum(1 for i in range(rows) if view._formset_data.get("%s-%d-DELETE" % (name, i)) == "on")


# ---------------------------------------------------------------------------------------------
# 1. Fields a form adds in __init__
# ---------------------------------------------------------------------------------------------
class TestFieldsAddedInInitAreTheFormsetsOwn:
    def test_the_review_reproducer_600_rows_each_delete_is_stored(self):
        """600 rows x 3 added fields = 1,800 seeded keys against a limit of 1,000: 0 of 600 deletes were
        stored before (every one refused); each is the formset's own key now."""
        view = view_for(AddsFormSet)
        seed(view, 600, ("d1", "d2", "d3"))
        assert remove_all(view, 600) == 600

    def test_the_declared_field_and_the_added_ones_are_all_exempt(self):
        view = view_for(AddsFormSet)
        seed(view, 600, ("street", "d1", "d2", "d3"))
        assert remove_all(view, 600) == 600

    def test_the_deletions_themselves_are_the_formsets_own_even_when_it_cannot_delete(self):
        """``can_delete`` is False, so ``DELETE`` is not a field of the form, yet ``remove_row`` writes
        one ``-DELETE`` key per row. 1,100 of them are not 1,100 keys the client added."""
        assert AddsFormSet.can_delete is False
        view = view_for(AddsFormSet)
        seed(view, 1100, ())
        assert remove_all(view, 1100) == 1100

    def test_the_names_are_derived_once_not_per_removal(self):
        """``remove_row`` runs per event; building the formset's form on each would be a form per call."""
        view = view_for(AddsFormSet)
        seed(view, 50, ("d1",))
        remove_all(view, 1)  # the first call derives them
        before = AddsInInit.built
        remove_all(view, 50)
        assert AddsInInit.built == before

    def test_one_derivation_serves_every_view_of_the_class(self):
        formsets._row_suffixes(AddsFormSet)
        before = AddsInInit.built
        for _ in range(20):
            view_for(AddsFormSet)
            formsets._row_suffixes(AddsFormSet)
        assert AddsInInit.built == before

    def test_an_invented_suffix_under_a_real_row_is_still_counted(self):
        """The exemption is the form's fields, not 'anything under a row': 3858's F1, with the
        __init__-added form."""
        view = view_for(AddsFormSet)
        seed(view, 1, ())
        for i in range(1500):
            view.remove_row(formset="fs", prefix="fs-0-zz%d" % i)
        assert len(view._formset_data) == 4 + DEFAULT_MAX_FORM_FIELDS

    def test_a_field_a_form_adds_only_for_bound_data_is_not_exempt(self):
        """The set is built from the UNBOUND form. A form that adds a field only when it holds data (the
        client's data, in the end) does not get that field exempted: the client cannot make a form add
        a field."""

        class AddsFromData(forms.Form):
            street = forms.CharField(required=False)

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                if self.data:
                    self.fields["only_when_bound"] = forms.CharField(required=False)

        fs = formset_factory(AddsFromData, extra=0)
        assert "only_when_bound" not in formsets._row_suffixes(fs)
        view = view_for(fs)
        seed(view, 1, ("only_when_bound",))
        with livecfg(fields=1):
            view.remove_row(formset="fs", prefix="bogus-1")
        assert "bogus-1-DELETE" not in view._formset_data  # the seeded key is counted: no room

    def test_a_form_that_cannot_be_built_without_arguments_falls_back_to_its_declared_fields(
        self, caplog
    ):
        """A form whose ``__init__`` needs a ``form_kwargs`` entry cannot be built by the class alone:
        the declared fields stay exempt (as in 3858), the field it adds counts, nothing raises, and the
        fallback is logged once."""

        class NeedsKwarg(forms.Form):
            street = forms.CharField(required=False)

            def __init__(self, *args: Any, mode: str, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.fields["added"] = forms.CharField(required=False)

        fs = formset_factory(NeedsKwarg, extra=0)
        with caplog.at_level(logging.WARNING, logger=formsets.logger.name):
            names = formsets._row_suffixes(fs)
            assert formsets._row_suffixes(fs) is names  # cached: the failure is not retried
        assert names == frozenset({"street", "DELETE", "ORDER", "id"})
        assert (
            sum("cannot be built without arguments" in r.getMessage() for r in caplog.records) == 1
        )
        view = view_for(fs)
        seed(view, 5, ("street",))
        assert remove_all(view, 5) == 5  # declared fields: exempt
        view = view_for(fs)
        seed(view, 3, ("added",))
        with livecfg(fields=1):
            view.remove_row(formset="fs", prefix="bogus-1")
        assert "bogus-1-DELETE" not in view._formset_data  # the added one is counted

    def test_a_formset_class_that_is_dropped_leaves_the_cache(self):
        fs = formset_factory(AddsInInit, extra=0)
        formsets._row_suffixes(fs)
        assert fs in formsets._SUFFIX_CACHE
        ref = weakref.ref(fs)
        del fs
        gc.collect()
        assert ref() is None  # the cache did not keep it alive


# ---------------------------------------------------------------------------------------------
# 2. The inline foreign key, with a real model
# ---------------------------------------------------------------------------------------------
class TestTheInlineForeignKeyIsExempt:
    """``inlineformset_factory(ContentType, Permission)``: ``fk.name == "content_type"`` is not a field
    the form class declares (``fields=("name", "codename")``)."""

    @staticmethod
    def _fs(**kwargs: Any):
        return inlineformset_factory(
            ContentType, Permission, fields=("name", "codename"), extra=0, **kwargs
        )

    def test_the_fk_is_not_in_the_form_classs_declared_fields(self):
        fs = self._fs()
        assert fs.fk.name == "content_type"
        assert "content_type" not in fs.form.base_fields

    def test_1300_rows_of_4_keys_are_all_deletable(self):
        """5,200 seeded keys against a limit of 1,000. Without the foreign key exempt, its 1,300 keys
        alone are past the limit and no row can be removed."""
        fs = self._fs(max_num=2000)
        assert "content_type" in formsets._row_suffixes(fs)
        view = view_for(fs)
        seed(view, 1300, ("name", "codename", "content_type", "id"))
        assert remove_all(view, 1300) == 1300

    def test_the_fk_is_exempt_when_the_formset_cannot_be_built(self):
        """The class-declared fallback keeps it: a model form that needs an argument still exempts
        the foreign key."""

        class NeedsKwarg(ModelForm):
            class Meta:
                model = Permission
                fields = ("name", "codename")

            def __init__(self, *args: Any, mode: str, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)

        fs = inlineformset_factory(ContentType, Permission, form=NeedsKwarg, extra=0, max_num=2000)
        assert formsets._row_suffixes(fs) == frozenset(
            {"DELETE", "ORDER", "id", "name", "codename", "content_type"}
        )
        view = view_for(fs)
        seed(view, 1300, ("name", "codename", "content_type", "id"))
        assert remove_all(view, 1300) == 1300

    def test_an_invented_suffix_under_an_inline_row_is_still_counted(self):
        view = view_for(self._fs())
        seed(view, 1, ())
        for i in range(1500):
            view.remove_row(formset="fs", prefix="fs-0-zz%d" % i)
        assert len(view._formset_data) == 4 + DEFAULT_MAX_FORM_FIELDS


# ---------------------------------------------------------------------------------------------
# 2b. Deriving the names reads no rows (review of #3881)
# ---------------------------------------------------------------------------------------------
@pytest.mark.django_db
class TestDerivingTheNamesRunsNoQuery:
    """A model formset's default queryset is the model's whole table. Building ``empty_form`` for the
    exempt names evaluated it once per class: one unbounded ``SELECT``, blocking that event on a large
    table. The formset is built over ``Model._default_manager.none()``."""

    @staticmethod
    def _model_fs():
        return modelformset_factory(Permission, fields=("name", "codename"), extra=0)

    @staticmethod
    def _inline_fs():
        return inlineformset_factory(ContentType, Permission, fields=("name", "codename"), extra=0)

    @pytest.mark.parametrize("make", ["_model_fs", "_inline_fs"])
    def test_the_first_remove_row_for_a_model_formset_runs_zero_queries(self, make):
        fs = getattr(self, make)()
        view = view_for(fs)
        seed(view, 3, ("name", "codename", "id"))
        with CaptureQueriesContext(connection) as queries:
            view.remove_row(formset="fs", prefix="fs-0")
        assert len(queries) == 0, [q["sql"] for q in queries]
        assert view._formset_data["fs-0-DELETE"] == "on"  # and it still did its work

    def test_the_names_are_the_same_as_for_the_default_queryset(self):
        fs = self._model_fs()
        assert formsets._row_suffixes(fs) == frozenset(
            {"DELETE", "ORDER", "id", "name", "codename"}
        )

    def test_a_plain_formset_is_built_as_before(self):
        """No model, no ``queryset`` argument (a plain ``BaseFormSet`` takes none)."""
        assert "street" in formsets._row_suffixes(AddsFormSet)


# ---------------------------------------------------------------------------------------------
# 3. The TOTAL_FORMS clamp is max(cap, absolute_max), not min
# ---------------------------------------------------------------------------------------------
class OneField(forms.Form):
    street = forms.CharField(required=False)


class TestTheTotalFormsClampIsTheWiderOfCapAndAbsoluteMax:
    """``min`` and ``max`` agree whenever ``TOTAL_FORMS`` is within ``max_num`` (every row ``add_row``
    makes). They differ for a formset that holds more rows than ``max_num``: Django accepts up to
    ``absolute_max`` of them (``total_form_count``), and a project that loads existing rows seeds
    ``TOTAL_FORMS`` for all of them."""

    @staticmethod
    def _refused(fs, total: int, row: int) -> bool:
        """Whether a seeded ``fs-<row>-street`` is COUNTED (limit 1: it leaves no room for the removal)."""
        view = view_for(fs)
        seed(view, 0, (), total=total)
        view._formset_data["fs-%d-street" % row] = "v"
        with livecfg(fields=1):
            view.remove_row(formset="fs", prefix="bogus-1")
        return "bogus-1-DELETE" not in view._formset_data

    def test_a_formset_holding_more_rows_than_max_num_has_them_up_to_absolute_max(self):
        fs = formset_factory(OneField, extra=0, max_num=5)
        assert (fs.max_num, fs.absolute_max) == (5, 1005)
        assert not self._refused(fs, total=100, row=99)  # row 99 exists: exempt
        assert self._refused(fs, total=100, row=100)  # row 100 does not: counted

    def test_the_clamp_is_absolute_max_not_the_forged_total(self):
        fs = formset_factory(OneField, extra=0, max_num=5)
        assert not self._refused(fs, total=10**8, row=1004)
        assert self._refused(fs, total=10**8, row=1005)

    def test_a_formset_whose_max_num_is_past_absolute_max_has_rows_up_to_max_num(self):
        """Not a ``formset_factory`` product (it refuses ``absolute_max < max_num``) but a subclass can
        say it: ``add_row`` caps at ``max_num``, so the rows it makes are all the formset's."""

        Wide = type(
            "Wide", (formset_factory(OneField, extra=0, max_num=3000),), {"absolute_max": 2000}
        )
        assert (Wide.max_num, Wide.absolute_max) == (3000, 2000)

        assert not self._refused(Wide, total=2500, row=2499)  # between the two
        assert self._refused(Wide, total=2500, row=3000)
        assert self._refused(Wide, total=10**8, row=3000)
