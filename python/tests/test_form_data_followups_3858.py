"""Follow-ups from the independent review of #3857 (#3858): the wizard TOTAL, ``remove_row``, the
member-first order, and ``config.set`` for the three form-data limits.

Every bound here is pinned by an exact count or an exact member total, never a duration, and each new
limit has a test at exactly limit - 1, limit and limit + 1.

Refs #3858, #3857, #3844, #3842, #3834, #1468, #1646.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pytest
from django import forms
from django.forms import formset_factory
from django.test import override_settings

from djust import _form_data_bounds as bounds
from djust.config import DEFAULT_MAX_FORM_FIELDS, DEFAULT_MAX_FORM_MEMBERS, config as djust_config
from djust.formsets import FormSetHelpersMixin, remove_row
from djust.live_view import LiveView
from djust.wizard import WizardMixin

from test_form_data_bounds_3834 import limits as livecfg, mounted
from test_form_data_members_3844 import mutated_bounds, reference_members, reference_size

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]


# ---------------------------------------------------------------------------------------------
# 1. The wizard bounds the TOTAL it keeps across steps, not only each step.
# ---------------------------------------------------------------------------------------------
class OneField(forms.Form):
    first = forms.CharField(required=False)


class FourStepWizard(WizardMixin, LiveView):
    exposure_policy = "legacy"
    wizard_steps = [{"name": n, "title": n, "form_class": OneField} for n in "abcd"]
    template = "<div dj-root></div>"


def held(view) -> Dict[str, Any]:
    return view.wizard_step_data


def total_members(view) -> int:
    return sum(reference_members(data) for data in held(view).values())


def total_size(view) -> int:
    return sum(reference_size(data) for data in held(view).values())


def goto(view, index: int) -> None:
    view.wizard_step_index = index


class TestTheWizardTotalIsBounded:
    """A step's data was bounded alone, so a 4-step wizard of ``required=False`` fields kept up to four
    times the limit (~90,000 members a step against 100,000, 360,000 in all) and every event handed all
    of it to the template engine."""

    @pytest.mark.parametrize("second, accepted", [(17, True), (18, True), (19, False)])
    def test_members_at_limit_minus_one_the_limit_and_one_past(self, second, accepted):
        """Step a holds 10 members (key, list, 8 items); step b's field is ``second + 2``. The limit is
        30, so 29 and 30 are within it and 31 is not, though every step is alone well under 30."""
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=30):
            view.update_step_field(field="a1", value=["x"] * 8)
            goto(view, 1)
            view.update_step_field(field="b1", value=["y"] * second)
        assert ("b1" in held(view).get("b", {})) is accepted
        assert total_members(view) == (30 if second == 18 else (29 if second == 17 else 10))
        assert total_members(view) <= 30
        if not accepted:
            assert "more values" in view.wizard_step_errors["b"]["__all__"][0]

    @pytest.mark.parametrize("second, accepted", [(16, True), (17, True), (18, False)])
    def test_characters_at_limit_minus_one_the_limit_and_one_past(self, second, accepted):
        """Step a: ``a1`` and nine characters are 11. Step b: ``b1`` and ``second`` characters are
        ``2 + second``. The limit is 30, so 29 and 30 are within it and 31 is not."""
        view = mounted(FourStepWizard)
        with livecfg(size=30, max_form_members=0):
            view.update_step_field(field="a1", value="x" * 9)
            goto(view, 1)
            view.update_step_field(field="b1", value="y" * second)
        assert ("b1" in held(view).get("b", {})) is accepted
        assert total_size(view) == (13 + second if accepted else 11)
        assert total_size(view) <= 30
        if not accepted:
            assert "larger" in view.wizard_step_errors["b"]["__all__"][0]

    def test_the_review_scenario_four_steps_cannot_hold_four_times_the_limit(self):
        """Each step is filled with fields of 10 members until it is refused, then the next step:
        what the wizard keeps in all is at most the limit."""
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=100):
            for index in range(4):
                goto(view, index)
                for i in range(20):
                    view.update_step_field(field="f%d" % i, value=["x"] * 8)
        assert total_members(view) == 100  # ten fields of ten members, all in the first step
        assert [len(held(view).get(n, {})) for n in "abcd"] == [10, 0, 0, 0]

    def test_a_field_sent_again_is_a_replacement_in_the_total(self):
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=30):
            view.update_step_field(field="a1", value=["x"] * 8)  # 10
            goto(view, 1)
            view.update_step_field(field="b1", value=["y"] * 18)  # 20: exactly 30 in all
            view.update_step_field(field="b1", value=["y"] * 18)  # again: replaces, still 30
            view.update_step_field(field="b1", value=["y"] * 19)  # 31: refused
        assert total_members(view) == 30
        assert len(held(view)["b"]["b1"]) == 18

    def test_editing_an_earlier_step_is_judged_against_the_total_too(self):
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=30):
            view.update_step_field(field="a1", value=["x"] * 8)  # 10
            goto(view, 1)
            view.update_step_field(field="b1", value=["y"] * 18)  # 20: 30 in all
            goto(view, 0)
            view.update_step_field(field="a1", value=["x"] * 9)  # 11 + 20 = 31: refused
            assert len(held(view)["a"]["a1"]) == 8
            view.update_step_field(field="a1", value=["x"] * 3)  # a shrink is accepted
        assert len(held(view)["a"]["a1"]) == 3

    def test_other_steps_alone_past_the_limit_refuse_every_event(self):
        """A limit lowered after data was stored: the other steps are over it, so nothing more is kept
        (a shrink of the current step cannot bring the total back under it), and nothing raises."""
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=0):
            view.update_step_field(field="a1", value=["x"] * 50)
            goto(view, 1)
            view.update_step_field(field="b1", value="y")
        with livecfg(max_form_members=30):
            view.update_step_field(
                field="b1", value=""
            )  # a replacement that shrinks: still refused
            view.update_step_field(field="b2", value="z")
            assert (
                bounds.refusal({}, {}, (), [held(view)["a"]]) is not None
            )  # not even an empty one
        assert held(view)["b"] == {"b1": "y"}
        assert "more values" in view.wizard_step_errors["b"]["__all__"][0]

    def test_zero_removes_the_total_too(self):
        view = mounted(FourStepWizard)
        with livecfg(max_form_members=0, max_form_data_size=0):
            for index in range(4):
                goto(view, index)
                view.update_step_field(field="f", value=["x"] * 5_000)
        assert total_members(view) > 20_000

    def test_the_total_follows_the_fields_limit_per_step(self):
        """``max_form_fields`` stays per step: a step is a form, and Django's field limit is per form."""
        view = mounted(FourStepWizard)
        with livecfg(fields=2, max_form_members=0, size=0):
            for index in range(2):
                goto(view, index)
                for i in range(5):
                    view.update_step_field(field="f%d" % i, value="x")
        assert sorted(held(view)["a"]) == ["f0", "f1"] and sorted(held(view)["b"]) == ["f0", "f1"]

    def test_the_size_limit_is_a_total_when_the_member_limit_is_off(self):
        view = mounted(FourStepWizard)
        with livecfg(size=40, max_form_members=0):
            view.update_step_field(field="a", value="x" * 20)  # 21
            goto(view, 1)
            view.update_step_field(field="b", value="y" * 20)  # 21: 42, refused
        assert "b" not in held(view).get("b", {})
        assert "larger" in view.wizard_step_errors["b"]["__all__"][0]


# ---------------------------------------------------------------------------------------------
# 2. FormSetHelpersMixin.remove_row asks refusal() before it stores a client-named key.
# ---------------------------------------------------------------------------------------------
class RowForm(forms.Form):
    street = forms.CharField(required=False)


RowFormSet = formset_factory(RowForm, extra=0)


class RowsView(FormSetHelpersMixin, LiveView):
    exposure_policy = "legacy"
    formset_classes = {"rows": RowFormSet}

    def mount(self, request=None, **kwargs):
        self._formset_data = {}
        self.rows = RowFormSet(prefix="rows")


def rows_view() -> RowsView:
    view = RowsView()
    view.mount()
    return view


class TestRemoveRowIsBounded:
    def test_the_review_scenario_3000_prefixes_stop_at_the_default_limit(self):
        view = rows_view()
        for i in range(3000):
            view.remove_row(formset="rows", prefix="x%d" % i)
        assert len(view._formset_data) == DEFAULT_MAX_FORM_FIELDS

    @pytest.mark.parametrize("stored, kept", [(4, True), (5, True), (6, False)])
    def test_at_limit_minus_one_the_limit_and_one_past(self, stored, kept):
        """The limit is 5: a sixth distinct key is refused; the fifth (limit) and fourth are not."""
        view = rows_view()
        with livecfg(fields=5):
            for i in range(stored - 1):
                view.remove_row(formset="rows", prefix="x%d" % i)
            view.remove_row(formset="rows", prefix="last")
        assert ("last-DELETE" in view._formset_data) is kept
        assert len(view._formset_data) == min(stored, 5)

    def test_a_row_marked_again_is_a_replacement(self):
        view = rows_view()
        with livecfg(fields=2):
            for _ in range(10):
                view.remove_row(formset="rows", prefix="x0")
            view.remove_row(formset="rows", prefix="x1")
            view.remove_row(formset="rows", prefix="x2")
        assert sorted(view._formset_data) == ["x0-DELETE", "x1-DELETE"]

    def test_a_refusal_changes_nothing_and_is_logged_without_the_key(self, caplog):
        view = rows_view()
        with caplog.at_level(logging.WARNING):
            with livecfg(fields=1):
                view.remove_row(formset="rows", prefix="first")
                before = dict(view._formset_data)
                view.remove_row(formset="rows", prefix="secret-row")
        assert view._formset_data == before
        assert "secret" not in " ".join(r.getMessage() for r in caplog.records)
        assert any("more fields" in r.getMessage() for r in caplog.records)

    def test_the_rows_the_formset_has_are_not_the_clients_to_add(self):
        """Marking each row of a formset of 4 for deletion is not 'adding fields': the management keys
        and the rows' own keys (here seeded, as a project does) are the formset's, not the client's."""
        view = rows_view()
        for _ in range(4):
            view.add_row(formset="rows")
        for i in range(4):
            view._formset_data["rows-%d-street" % i] = "s"
        with livecfg(fields=1):
            for i in range(4):
                view.remove_row(formset="rows", prefix="rows-%d" % i)
            view.remove_row(formset="rows", prefix="bogus-1")  # the one key the client names
            view.remove_row(formset="rows", prefix="bogus-2")  # a second: refused
        assert all(view._formset_data["rows-%d-DELETE" % i] == "on" for i in range(4))
        assert "bogus-1-DELETE" in view._formset_data
        assert "bogus-2-DELETE" not in view._formset_data

    @pytest.mark.parametrize("bogus", ["rows-4", "rows-99", "rows-abc", "rows-", "rows--x"])
    def test_a_row_the_formset_does_not_have_is_the_clients_even_under_its_prefix(self, bogus):
        """Four rows exist (0-3): ``rows-4`` is the first that does not, and a prefix of the formset's
        own spelling is no exemption. The one key the limit allows is spent on the first."""
        view = rows_view()
        for _ in range(4):
            view.add_row(formset="rows")
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="first-bogus")
            view.remove_row(formset="rows", prefix=bogus)
        assert "%s-DELETE" % bogus not in view._formset_data
        assert "first-bogus-DELETE" in view._formset_data

    def test_the_last_row_the_formset_has_is_the_formsets_own(self):
        view = rows_view()
        for _ in range(4):
            view.add_row(formset="rows")
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="first-bogus")
            view.remove_row(formset="rows", prefix="rows-3")  # TOTAL_FORMS is 4: rows 0-3 exist
        assert view._formset_data["rows-3-DELETE"] == "on"

    # -- F1/F2 of the review of #3860: the suffix is the client's too -------------------------

    @staticmethod
    def _with_rows(rows: int) -> "RowsView":
        view = rows_view()
        for _ in range(rows):
            view.add_row(formset="rows")
        return view

    def test_the_review_reproducer_invented_suffixes_under_a_real_row_are_counted(self):
        """``rows-0-f<i>-DELETE``: row 0 exists, but ``f<i>`` is not one of its fields. 1,500 of them
        stored 1,500 keys against a limit of 1,000."""
        view = self._with_rows(1)
        for i in range(1500):
            view.remove_row(formset="rows", prefix="rows-0-f%d" % i)
        assert (
            len(view._formset_data) == 4 + DEFAULT_MAX_FORM_FIELDS
        )  # the 4 management keys + 1000

    @pytest.mark.parametrize("invented, kept", [(4, True), (5, True), (6, False)])
    def test_invented_suffixes_at_limit_minus_one_the_limit_and_one_past(self, invented, kept):
        view = self._with_rows(1)
        with livecfg(fields=5):
            for i in range(invented - 1):
                view.remove_row(formset="rows", prefix="rows-0-f%d" % i)
            view.remove_row(formset="rows", prefix="rows-0-last")
        assert ("rows-0-last-DELETE" in view._formset_data) is kept

    @pytest.mark.parametrize(
        "bogus",
        ["rows-00", "rows-000", "rows-01", "rows-٠", "rows-١", "rows-０", "rows-+0"],
    )
    def test_a_row_index_that_is_not_canonical_ascii_is_counted(self, bogus):
        """Zero-padded, Arabic-Indic, full-width: ``int()`` reads them as a row that exists, the formset
        does not."""
        view = self._with_rows(3)
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="first-bogus")
            view.remove_row(formset="rows", prefix=bogus)
        assert "%s-DELETE" % bogus not in view._formset_data

    @pytest.mark.parametrize("bogus", ["rows-²1", "rows-²", "rows-" + "9" * 5000])
    def test_an_index_int_cannot_parse_fails_closed_without_raising(self, bogus):
        """``"²".isdigit()`` is true and ``int("²")`` raises; so does a 5,000-digit index."""
        view = self._with_rows(3)
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="first-bogus")
            view.remove_row(formset="rows", prefix=bogus)  # must not raise
        assert "%s-DELETE" % bogus not in view._formset_data
        view = self._with_rows(3)
        view.remove_row(
            formset="rows", prefix=bogus
        )  # inside the limit it is stored, still no error
        assert "%s-DELETE" % bogus in view._formset_data

    @pytest.mark.parametrize("suffix", ["street", "DELETE", "id", "ORDER"])
    @pytest.mark.parametrize("row, exempt", [(0, True), (2, True), (3, False), (4, False)])
    def test_only_the_forms_own_fields_of_a_row_that_exists_are_exempt(self, suffix, row, exempt):
        """TOTAL_FORMS is 3 (rows 0-2). A seeded ``rows-<n>-<field>`` is the formset's for a real field
        and ``n <= 2``, and counts at ``n = 3``. The limit is 1 and one counted key is spent on the
        removal itself, so a counted seed leaves no room for it."""
        view = self._with_rows(3)
        view._formset_data["rows-%d-%s" % (row, suffix)] = "v"
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="bogus-1")
        assert ("bogus-1-DELETE" in view._formset_data) is exempt

    @pytest.mark.parametrize("seeded, kept", [(0, True), (1, True), (2, False)])
    def test_an_invented_field_under_an_existing_row_in_the_data_is_counted(self, seeded, kept):
        """The suffix check, alone: ``rows-0-zzz<i>`` is under row 0 (which exists) but no field of the
        form. The limit is 2, so ``seeded`` of them plus the removal's own key fit up to ``seeded = 1``."""
        view = self._with_rows(1)
        for i in range(seeded):
            view._formset_data["rows-0-zzz%d" % i] = "v"
        with livecfg(fields=2):
            view.remove_row(formset="rows", prefix="bogus-1")
        assert ("bogus-1-DELETE" in view._formset_data) is kept

    @pytest.mark.parametrize("junk", ["abc", "²", "9" * 5000, "-1", "03", "٣", "1.5", ""])
    def test_a_total_forms_that_is_not_a_canonical_count_is_no_rows_and_does_not_raise(self, junk):
        """A project-seeded ``TOTAL_FORMS`` that is not canonical ASCII digits counts as no rows, so the
        row keys under it are the client's to be counted, and nothing raises."""
        view = rows_view()
        view._formset_data = {"rows-TOTAL_FORMS": junk, "rows-0-street": "s"}
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="bogus-1")  # rows-0-street is counted: no room
        assert "bogus-1-DELETE" not in view._formset_data

    def test_a_huge_total_forms_a_project_seeded_does_not_widen_the_exemption(self):
        """Nothing here stores a client's ``TOTAL_FORMS`` (``remove_row`` only writes ``-DELETE`` keys and
        ``add_row`` stops at the formset's cap), but a huge one a project seeded is clamped to that cap."""
        view = rows_view()
        view._formset_data = {"rows-TOTAL_FORMS": "999999999"}
        with livecfg(fields=1):
            view.remove_row(formset="rows", prefix="first-bogus")
            view.remove_row(formset="rows", prefix="rows-5000")
        assert "rows-5000-DELETE" not in view._formset_data

    def test_the_function_form_is_bounded_too(self):
        """``remove_row()`` is also called directly from a project's handler with the client's prefix."""
        data: Dict[str, Any] = {}
        with livecfg(fields=3):
            for i in range(10):
                data = dict(remove_row(RowFormSet, "x%d" % i, data=data, prefix="rows").data)
        assert len(data) == 3

    @pytest.mark.parametrize("size, kept", [(20, False), (21, True), (22, True)])
    def test_the_size_limit_applies_at_its_boundary(self, size, kept):
        """``{"s": "x" * 10}`` is 11 characters; ``a-DELETE`` and ``on`` add 10: 21 in all."""
        view = rows_view()
        view._formset_data = {"s": "x" * 10}
        with livecfg(fields=0, size=size, max_form_members=0):
            view.remove_row(formset="rows", prefix="a")
        assert ("a-DELETE" in view._formset_data) is kept

    @pytest.mark.parametrize("limit, kept", [(3, False), (4, True), (5, True)])
    def test_the_member_limit_applies_at_its_boundary(self, limit, kept):
        view = rows_view()
        view._formset_data = {"s": "x"}  # 2 members, and the key and value we add are 2 more
        with livecfg(fields=0, size=0, max_form_members=limit):
            view.remove_row(formset="rows", prefix="a")
        assert ("a-DELETE" in view._formset_data) is kept

    def test_add_row_names_no_key(self):
        """``add_row`` keys are the management keys of a formset the project declared (an unknown name
        raises): the client names none, so there is nothing for ``refusal`` to judge."""
        view = rows_view()
        for _ in range(5):
            view.add_row(formset="rows")
        assert sorted(view._formset_data) == [
            "rows-INITIAL_FORMS",
            "rows-MAX_NUM_FORMS",
            "rows-MIN_NUM_FORMS",
            "rows-TOTAL_FORMS",
        ]


# ---------------------------------------------------------------------------------------------
# 3. Hygiene: member-first reporting, config.set(), the messages and the production warning.
# ---------------------------------------------------------------------------------------------
class TestMembersAreJudgedBeforeCharacters:
    def test_both_limits_passed_reports_the_members(self, monkeypatch):
        monkeypatch.setattr(bounds, "limits", lambda: (0, 10))
        monkeypatch.setattr(bounds, "member_limit", lambda: 10)
        message = bounds.refusal({}, {"k": [""] * 50}, ())
        assert message is not None and "more values" in message

    def test_a_flood_of_tiny_members_is_refused_in_o_max_members(self, monkeypatch):
        """Counted work: with 2.6 million characters allowed and 1,000 members, an endless list of empty
        strings must be abandoned after the member budget, not after the character budget. Swapping the
        two checks pulls ~2.6 million members instead of ~16,000 (review mutation M6)."""
        pulled = []

        def endless():
            for i in range(10**7):
                pulled.append(i)
                yield ""

        class Endless(list):
            def __len__(self):
                return 10**7

            def __iter__(self):
                return endless()

        monkeypatch.setattr(bounds, "limits", lambda: (0, 2_621_440))
        monkeypatch.setattr(bounds, "member_limit", lambda: 1_000)
        message = bounds.refusal({}, {"k": Endless()}, ())
        assert message is not None and "more values" in message
        assert len(pulled) <= 16_384, len(pulled)

    def test_the_size_check_does_not_run_when_the_members_already_refused(self, monkeypatch):
        calls = []
        real = bounds._over

        def spy(current, incoming, limit, count, others=()):
            calls.append(count)
            return real(current, incoming, limit, count, others)

        monkeypatch.setattr(bounds, "limits", lambda: (0, 10))
        monkeypatch.setattr(bounds, "member_limit", lambda: 10)
        monkeypatch.setattr(bounds, "_over", spy)
        assert bounds.refusal({}, {"k": [""] * 50}, ()) is not None
        assert calls == [True]

    def test_swapping_the_two_checks_is_caught(self):
        """The review's mutation M6, applied to the source and run: the mutant reports the size."""
        old = (
            "    if max_members and _over(current, incoming, max_members, True, others):\n"
            "        return _too_many_members(max_members)\n"
            "    if max_size and _over(current, incoming, max_size, False, others):\n"
            "        return _too_large(max_size)\n"
        )
        new = (
            "    if max_size and _over(current, incoming, max_size, False, others):\n"
            "        return _too_large(max_size)\n"
            "    if max_members and _over(current, incoming, max_members, True, others):\n"
            "        return _too_many_members(max_members)\n"
        )
        mutant = mutated_bounds(old, new)
        mutant.limits = lambda: (0, 10)
        mutant.member_limit = lambda: 10
        assert "larger" in mutant.refusal({}, {"k": [""] * 50}, ())  # the mutant reports the size
        real = mutated_bounds(old, old)
        real.limits = lambda: (0, 10)
        real.member_limit = lambda: 10
        assert "more values" in real.refusal({}, {"k": [""] * 50}, ())


class TestConfigSetValidatesTheLimits:
    KEYS = ("max_form_fields", "max_form_data_size", "max_form_members")

    @pytest.fixture(autouse=True)
    def _restore(self):
        saved = (djust_config._config, dict(djust_config._programmatic))
        yield
        djust_config._config, djust_config._programmatic = saved

    @pytest.mark.parametrize("bad", ["abc", -5, 3.7, True, None, [1], "1000"])
    @pytest.mark.parametrize("key", KEYS)
    def test_set_with_a_value_that_is_not_a_non_negative_integer_is_the_default(self, key, bad):
        djust_config.set(key, bad)
        assert djust_config.get(key) == djust_config._defaults[key]

    @pytest.mark.parametrize("bad", ["abc", -5, 3.7, True])
    @pytest.mark.parametrize("key", KEYS)
    def test_update_too(self, key, bad):
        djust_config.update({key: bad, "css_framework": "bootstrap5"})
        assert djust_config.get(key) == djust_config._defaults[key]

    @pytest.mark.parametrize("key", KEYS)
    @pytest.mark.parametrize("good", [0, 1, 7, 10**9])
    def test_a_good_value_is_kept(self, key, good):
        djust_config.set(key, good)
        assert djust_config.get(key) == good

    def test_a_bad_set_does_not_stop_a_form_event(self):
        """'abc' used to make every form event raise TypeError (an int compared to a str)."""
        view = mounted()
        djust_config.set("max_form_members", "abc")
        view.validate_field(field="a", value="x")
        assert view.form_data["a"] == "x"

    def test_a_rejected_value_is_reported(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            djust_config.set("max_form_members", "abc")
        assert any(
            "max_form_members" in r.getMessage() and "'abc'" in r.getMessage()
            for r in caplog.records
        )

    def test_the_default_survives_a_reload(self):
        djust_config.set("max_form_members", -5)
        djust_config.reload_from_settings()
        assert djust_config.get("max_form_members") == DEFAULT_MAX_FORM_MEMBERS


class TestTheMessagesNameTheSetting:
    def test_each_refusal_names_its_setting(self):
        assert "max_form_fields" in bounds._too_many(5)
        assert "max_form_members" in bounds._too_many_members(5)
        assert "max_form_data_size" in bounds._too_large(5)

    @pytest.mark.parametrize("key", ["max_form_fields", "max_form_data_size", "max_form_members"])
    def test_the_production_warning_names_which_one_is_zero(self, key, caplog):
        others = {k: 10 for k in TestConfigSetValidatesTheLimits.KEYS if k != key}
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(DEBUG=False, LIVEVIEW_CONFIG={**others, key: 0}):
                pass
        text = [r.getMessage() for r in caplog.records if "no limit" in r.getMessage()]
        assert text, caplog.records
        assert all(key in line for line in text)
        assert not any(other in line for line in text for other in others)

    def test_two_zero_names_both(self, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.config"):
            with override_settings(
                DEBUG=False,
                LIVEVIEW_CONFIG={"max_form_fields": 0, "max_form_members": 0},
            ):
                pass
        line = " ".join(r.getMessage() for r in caplog.records if "no limit" in r.getMessage())
        assert "max_form_fields" in line and "max_form_members" in line
        assert "max_form_data_size" not in line


def test_every_field_counts_as_two_members():
    """A key and a value: the doc says raising ``max_form_fields`` past ~50,000 needs the member limit
    raised with it (50,000 fields are 100,000 members, the default)."""
    assert reference_members({"k%d" % i: "v" for i in range(1000)}) == 2000
    assert DEFAULT_MAX_FORM_MEMBERS == 2 * 50_000
