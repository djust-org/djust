"""``form.data`` is put to the serialization floor once, not once per read (#3834).

``_SidecarFormProxy.data`` filtered the whole submitted mapping on EVERY attribute access, and a template
path wraps a fresh proxy around the form at every step it takes, so ``{{ form.data.a }}{{ form.data.b }}``
put every key to the floor twice. The keys are what a client posted: 100,000 dashed keys were ~1 s per
read. The verdict is now kept on the form (``_floor_filtered_data``) and trusted only while it is an
answer to the same question.

A cache is only as good as its invalidation, and a stale verdict here is not a slow page, it is a
password key a template can read. So the first half of this file is a DIFFERENTIAL: it drives forms
through generated sequences of mutations (keys added, removed, replaced under the same length; values
changed in place; the data object swapped; a widget swapped between text and password; the model's
floor changed; ``DJUST_SENSITIVE_FIELDS`` changed) and asserts, after EVERY step, that the cached read
equals the uncached function it replaces, ``_without_floor_keys(data, floor, _floor_data_key_readable)``.
The second half counts: how many questions are put to the floor, never wall-clock time.

Refs #3834, #3830, #3816, #1646, #1468, #1200.
"""

from __future__ import annotations

import random

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.test import override_settings

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()


# ---------------------------------------------------------------------------------------------
# The forms. Each builder returns ``(form, data_setter)``-less plain objects: the tests mutate
# ``form.data`` directly, as a view that keeps a dict and hands it to the form would.
class Plain(forms.Form):
    title = forms.CharField(required=False)
    pin = forms.CharField(widget=forms.PasswordInput, required=False)
    note = forms.CharField(required=False)


class UserEdit(forms.ModelForm):
    pin = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username"]


class Row(forms.Form):
    pw = forms.CharField(widget=forms.PasswordInput, required=False)
    name = forms.CharField(required=False)


def plain(data, prefix=None):
    return Plain(data, prefix=prefix)


def user_form(data, prefix=None):
    return UserEdit(data, prefix=prefix, instance=User(username="u"))


def formset(data, prefix=None):
    return forms.formset_factory(Row)(data)


BUILDERS = {"plain form": plain, "model form": user_form, "formset": formset}
#: The key each builder's floor refuses out of ``posted()``.
SECRET = {"plain form": "pin", "model form": "pin", "formset": "form-0-pw"}


def proxy_of(holder):
    """What a template path wraps around the holder at EVERY step: a fresh proxy."""
    return serialization._protect_sidecar_walk(holder)


def uncached(holder):
    """The function the memo replaces, asked of a floor built NOW."""
    floor = (
        serialization._formset_floor(holder)
        if hasattr(holder, "forms")
        else serialization._model_form_floor(holder)
    )
    if floor is None:  # nothing on the floor: the walk leaves the form unproxied
        return holder.data
    return serialization._without_floor_keys(
        holder.data, floor, serialization._floor_data_key_readable
    )


def same_mapping(a, b):
    """Equal in type, content (lists too, for a QueryDict) and mutability."""
    if type(a) is not type(b):
        return False
    if isinstance(a, QueryDict):
        return list(a.lists()) == list(b.lists()) and a._mutable == b._mutable
    return list(a.items()) == list(b.items())


# ---------------------------------------------------------------------------------------------
# Counting: reads 2..N put nothing to the floor.
class Counting:
    """Records how many questions are put to ``_floor_field_readable``."""

    def __init__(self, monkeypatch):
        self.calls = 0
        original = serialization._floor_field_readable

        def counted(floor, name):
            self.calls += 1
            return original(floor, name)

        monkeypatch.setattr(serialization, "_floor_field_readable", counted)


def posted(count=40):
    data = {"title": "t", "u-pin": "typed", "pin": "typed", "form-0-pw": "typed"}
    for i in range(count):
        data["-".join(["k%d" % i] + ["x%d" % j for j in range(8)])] = "v"
    return data


class TestReadsAreAskedOnce:
    @pytest.mark.parametrize("label", sorted(BUILDERS))
    def test_ten_reads_through_ten_fresh_proxies_filter_once(self, label, monkeypatch):
        holder = BUILDERS[label](posted())
        counting = Counting(monkeypatch)
        first = proxy_of(holder).data
        after_first = counting.calls
        assert after_first >= len(posted())  # the first read does ask every key
        for _ in range(9):
            again = proxy_of(holder).data
            assert same_mapping(again, first)
        assert counting.calls == after_first, "reads 2..10 put %d more questions to the floor" % (
            counting.calls - after_first
        )
        assert SECRET[label] not in first and SECRET[label] in posted()

    @pytest.mark.parametrize("make", [dict, lambda d: QueryDict("", mutable=True)])
    def test_a_form_that_refuses_nothing_hands_back_its_own_mapping_every_time(
        self, make, monkeypatch
    ):
        data = make({})
        data.update({"title": "t", "k-a-b": "v"})
        form = plain(data)
        counting = Counting(monkeypatch)
        assert proxy_of(form).data is data
        first = counting.calls
        assert proxy_of(form).data is data
        assert counting.calls == first

    def test_the_management_form_of_a_formset_is_memoised_too(self, monkeypatch):
        fs = formset(
            {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-pw": "typed", **posted()}
        )
        counting = Counting(monkeypatch)
        proxy_of(fs).management_form.data
        first = counting.calls
        for _ in range(5):
            assert "form-0-pw" not in proxy_of(fs).management_form.data
        assert counting.calls == first

    def test_the_memo_lives_on_the_form_not_on_the_proxy(self):
        form = plain(posted())
        assert proxy_of(form) is not proxy_of(form)  # a walk step wraps a new proxy each time
        proxy_of(form).data
        assert serialization._DATA_FLOOR_MEMO_ATTR in form.__dict__

    def test_initial_and_cleaned_data_are_not_memoised(self, monkeypatch):
        """They are keyed by field NAMES (never client-chosen, so no cost to bound) and they hold the
        values the view computed, which a view may change in place: they stay a fresh copy."""
        form = user_form({"username": "u"})
        form.is_valid()
        counting = Counting(monkeypatch)
        proxy_of(form).initial
        first = counting.calls
        proxy_of(form).initial
        assert counting.calls == 2 * first


class TestSiblingsTracedInTheIssue:
    """The two readers #3834 named as untraced, now traced (see the PR): neither is a per-key scan."""

    def test_form_files_is_not_put_to_the_floor(self, monkeypatch):
        """``files`` holds uploads, not what was typed into a password widget, and the floor's
        question is about typed values; the proxy hands the mapping through untouched."""
        files = QueryDict("", mutable=True)
        for i in range(500):
            files["-".join(["f%d" % i] + ["x"] * 10)] = "upload"
        form = Plain({}, files=files)
        counting = Counting(monkeypatch)
        assert proxy_of(form).files is files
        assert counting.calls == 0

    def test_the_context_walk_over_a_querydict_asks_the_floor_nothing(self, monkeypatch):
        """``_protect_multi_value_dict`` walks ``.lists()`` once per render and asks about the VALUES
        (a model / queryset / manager), never the keys: no verdict per key, so nothing to cache."""
        qd = QueryDict("", mutable=True)
        for i in range(500):
            qd["-".join(["q%d" % i] + ["x"] * 10)] = "v"
        counting = Counting(monkeypatch)
        assert serialization._protect_sidecar_tree(qd) is qd
        assert counting.calls == 0


# ---------------------------------------------------------------------------------------------
# Invalidation: every way the answer can stop being the answer.
class TestInvalidation:
    def test_a_key_added_in_place_is_judged(self):
        data = {"title": "t"}
        form = plain(data)
        assert dict(proxy_of(form).data) == {"title": "t"}
        data["pin"] = "typed"
        assert "pin" not in proxy_of(form).data
        data["note"] = "n"
        assert dict(proxy_of(form).data) == {"title": "t", "note": "n"}

    def test_a_key_replaced_under_the_same_length_is_judged(self):
        """len() would not notice this: the count is unchanged and the keys are not."""
        data = {"title": "t", "other": "o"}
        form = plain(data)
        assert "other" in proxy_of(form).data
        del data["other"]
        data["pin"] = "typed"
        assert len(data) == 2
        assert set(proxy_of(form).data) == {"title"}

    def test_a_key_removed_is_not_resurrected(self):
        data = {"title": "t", "pin": "typed"}
        form = plain(data)
        assert set(proxy_of(form).data) == {"title"}
        del data["pin"]
        assert proxy_of(form).data is data

    def test_a_value_changed_in_place_is_read_as_it_is_now(self):
        data = {"title": "old", "pin": "typed"}
        form = plain(data)
        assert proxy_of(form).data["title"] == "old"
        data["title"] = "new"
        assert proxy_of(form).data["title"] == "new"

    def test_the_data_object_replaced_is_judged_again(self):
        form = plain({"title": "t"})
        assert proxy_of(form).data == {"title": "t"}
        form.data = {"title": "t", "pin": "typed"}
        assert set(proxy_of(form).data) == {"title"}

    def test_a_widget_swapped_on_a_live_form_is_followed_both_ways(self):
        form = plain({"title": "t", "note": "n", "pin": "typed"})
        assert set(proxy_of(form).data) == {"title", "note"}
        form.fields["note"].widget = forms.PasswordInput()  # now a password field
        assert set(proxy_of(form).data) == {"title"}
        form.fields["pin"].widget = forms.TextInput()  # no longer one
        form.fields["note"].widget = forms.TextInput()
        assert set(proxy_of(form).data) == {"title", "note", "pin"}

    def test_the_models_floor_changing_is_followed(self, monkeypatch):
        form = user_form({"username": "u", "first_name": "f", "email": "e@x.y"})
        assert "first_name" in proxy_of(form).data
        monkeypatch.setattr(User, "djust_exclude_fields", ("first_name",), raising=False)
        assert "first_name" not in proxy_of(form).data
        monkeypatch.setattr(User, "djust_exclude_fields", None, raising=False)
        assert "first_name" in proxy_of(form).data

    def test_djust_sensitive_fields_changing_is_followed(self):
        form = user_form({"username": "u", "first_name": "f"})
        assert "first_name" in proxy_of(form).data
        with override_settings(DJUST_SENSITIVE_FIELDS=["first_name"]):
            assert "first_name" not in proxy_of(form).data
        assert "first_name" in proxy_of(form).data

    def test_an_allowlist_set_on_the_model_is_followed(self, monkeypatch):
        form = user_form({"username": "u", "first_name": "f"})
        assert "first_name" in proxy_of(form).data
        monkeypatch.setattr(User, "djust_serializable_fields", ("username",), raising=False)
        assert set(proxy_of(form).data) == {"username"}

    def test_a_set_mutated_in_place_cannot_make_two_floors_look_equal(self):
        """The fingerprint holds COPIES: a floor whose own set is edited after the fact is a
        different floor from the one the memo was made under."""
        user = user_form({"username": "u", "first_name": "f"})
        floor = serialization._model_form_floor(user)
        edited = (floor[0], set(floor[1]), floor[2], floor[3], floor[4])
        assert "first_name" in serialization._floor_filtered_data(user, user.data, edited)
        edited[1].add("first_name")
        assert "first_name" not in serialization._floor_filtered_data(user, user.data, edited)

    def test_a_mapping_that_is_not_a_dict_is_asked_in_full(self):
        form = plain(None)
        sentinel = [("pin", 1)]
        floor = serialization._model_form_floor(form)
        assert serialization._floor_filtered_data(form, sentinel, floor) is sentinel
        assert serialization._DATA_FLOOR_MEMO_ATTR not in form.__dict__

    def test_a_holder_that_cannot_carry_the_memo_still_answers(self):
        class Slotted:
            __slots__ = ()

        data = {"title": "t", "pin": "typed"}
        floor = serialization._model_form_floor(plain(data))
        assert dict(serialization._floor_filtered_data(Slotted(), data, floor)) == {"title": "t"}

    def test_a_key_added_by_another_thread_mid_read_is_not_returned_unjudged(self):
        """The keys judged and the pairs returned come from ONE snapshot of a plain dict, so a key
        that arrives after the snapshot is not in the copy (it is judged on the next read)."""

        class Racing(dict):
            def items(self):
                snapshot = list(super().items())
                self["u-pin"] = "typed"  # arrives right after the snapshot
                return iter(snapshot)

        data = Racing(title="t", pin="typed")
        form = plain(data)
        floor = serialization._model_form_floor(form)
        first = serialization._floor_filtered_data(form, data, floor)
        assert set(first) == {"title"}
        assert set(serialization._floor_filtered_data(form, data, floor)) == {"title"}


# ---------------------------------------------------------------------------------------------
# The differential: cached == uncached after every step of a generated mutation sequence.
NAMES = [
    "title",
    "note",
    "pin",
    "password",
    "is_staff",
    "first_name",
    "username",
    "email",
    "_private",
    "get_session_auth_hash",
    "form-0-pw",
    "form-0-name",
    "u-pin",
    "u-title",
    "a-b-c-pin",
    "a-b-pin-c",
    "-",
    "--pin",
    "pin-",
]


def random_key(rng):
    if rng.random() < 0.55:
        return rng.choice(NAMES)
    if rng.random() < 0.5:
        return "-".join(
            rng.choice(["a", "b", "pin", "x", "password", "note"]) for _ in range(rng.randint(1, 6))
        )
    return "".join(rng.choice("ab-_") for _ in range(rng.randint(1, 60)))


def apply_dict_step(rng, form, monkeypatch):
    """One mutation of ``form``; returns a label."""
    data = form.data
    op = rng.choice(
        [
            "add",
            "add",
            "add",
            "remove",
            "replace",
            "value",
            "swap-data",
            "widget-pin",
            "widget-note",
            "settings",
            "model-floor",
            "noop",
            "noop",
        ]
    )
    if op == "add":
        data[random_key(rng)] = rng.choice(["v", "", "typed"])
    elif op == "remove" and data:
        del data[rng.choice(sorted(data))]
    elif op == "replace" and data:
        del data[rng.choice(sorted(data))]
        data[random_key(rng)] = "r"
    elif op == "value" and data:
        data[rng.choice(sorted(data))] = "changed-%d" % rng.randint(0, 9)
    elif op == "swap-data":
        form.data = dict(data)  # an equal copy: a different OBJECT
    elif op == "widget-pin" and "pin" in getattr(form, "fields", ()):
        form.fields["pin"].widget = rng.choice([forms.PasswordInput(), forms.TextInput()])
    elif op == "widget-note" and "note" in getattr(form, "fields", ()):
        form.fields["note"].widget = rng.choice([forms.PasswordInput(), forms.TextInput()])
    elif op == "settings":
        # applied for ONE read by the caller via override_settings; here: recorded on the form
        form._sensitive = rng.choice([[], ["first_name"], ["email", "note"]])
    elif op == "model-floor":
        monkeypatch.setattr(
            User,
            "djust_exclude_fields",
            rng.choice([None, ("first_name",), ("email", "title")]),
            raising=False,
        )
    return op


def apply_querydict_step(rng, form, monkeypatch):
    data = form.data
    op = rng.choice(["add", "add", "remove", "mutable-add", "swap-data", "widget-pin", "noop"])
    if op == "add":
        data._mutable = True
        data.appendlist(random_key(rng), "v")
        data._mutable = False
    elif op == "remove" and list(data):
        data._mutable = True
        del data[rng.choice(sorted(data))]
        data._mutable = False
    elif op == "mutable-add":
        data._mutable = True
        data.setlist(random_key(rng), ["a", "b"])
    elif op == "swap-data":
        copy = data.copy()
        copy._mutable = rng.choice([True, False])
        form.data = copy
    elif op == "widget-pin" and "pin" in getattr(form, "fields", ()):
        form.fields["pin"].widget = rng.choice([forms.PasswordInput(), forms.TextInput()])
    return op


def initial_data(rng):
    return {random_key(rng): "v" for _ in range(rng.randint(0, 12))}


class TestCachedEqualsUncachedAfterEveryStep:
    STEPS = 60
    SEEDS = 60

    def _drive(self, label, seed, monkeypatch, querydict):
        rng = random.Random("%s/%s/%s" % (label, seed, querydict))
        build = BUILDERS[label]
        if querydict:
            data = QueryDict("", mutable=True)
            for key in ["title", *initial_data(rng)]:  # never empty: a formset swaps {} in for it
                data.appendlist(key, "v")
            data._mutable = False
        else:
            data = {"title": "v", **initial_data(rng)}  # never empty: a formset swaps {} in for it
        holder = build(data)
        if hasattr(holder, "forms"):  # a formset has no widget of its own to swap
            holder.forms
        compared = reused = 0
        for _ in range(self.STEPS):
            step = apply_querydict_step if querydict else apply_dict_step
            step(rng, holder, monkeypatch)
            sensitive = getattr(holder, "_sensitive", [])
            with override_settings(DJUST_SENSITIVE_FIELDS=sensitive):  # [] = none configured
                memo_before = holder.__dict__.get(serialization._DATA_FLOOR_MEMO_ATTR)
                got = proxy_of(holder).data
                want = uncached(holder)
                memo_after = holder.__dict__.get(serialization._DATA_FLOOR_MEMO_ATTR)
            assert same_mapping(got, want), (label, seed, "cached != uncached")
            if memo_before is not None and memo_after is memo_before:
                reused += 1
            if want is holder.data:
                assert got is holder.data  # nothing refused: the form's own object, as before
            compared += 1
        return compared, reused

    @pytest.mark.parametrize("label", sorted(BUILDERS))
    @pytest.mark.parametrize("querydict", [False, True])
    def test_dict_and_querydict_data(self, label, querydict, monkeypatch):
        total = reused = 0
        for seed in range(self.SEEDS):
            c, r = self._drive(label, seed, monkeypatch, querydict)
            total += c
            reused += r
        assert total == self.SEEDS * self.STEPS
        # non-vacuous: the memo was REUSED on a real share of the steps (otherwise this compares
        # an uncached function with itself), and was also invalidated on others.
        assert 0 < reused < total, (reused, total)


class TestTheDifferentialCatchesAStaleMemo:
    """The harness itself must be able to fail: a memo that trusts only the data object (and not its
    keys, or not the floor) is wrong, and these are the cases that say so."""

    def test_trusting_only_the_object_would_leak_an_added_password_key(self, monkeypatch):
        data = {"title": "t"}
        form = plain(data)
        proxy_of(form).data
        memo = form.__dict__[serialization._DATA_FLOOR_MEMO_ATTR]
        data["pin"] = "typed"
        # what a keys-blind memo would answer: the stored (empty) refused set
        assert memo.refused == frozenset()
        assert "pin" not in proxy_of(form).data  # the real memo notices, and refuses it
        assert uncached(form) == {"title": "t"}
