"""A POST key a client chose costs linear work to classify against the form floor (#3830).

``form.data`` is keyed by what the client posted. ``_floor_data_key_readable`` asks the floor about the
whole key and about every ``-``-delimited tail of it, and it sliced and hashed every tail in full:
a key of ``L`` dashes cost ``L`` tails of up to ``L`` characters, so a 32 KB key took ~0.7 s and a
2.5 MB key (Django's ``DATA_UPLOAD_MAX_MEMORY_SIZE``) extrapolated to ~80 minutes of one worker's GIL.

The fix asks a tail longer than the longest name the floor treats specially in the form of its first
``bound + 1`` characters. That is only correct if it returns EXACTLY the verdict the full question
did, and "skip the long tails" would not: the model arm refuses a name that begins with ``_`` and any
name an allowlist does not name, whatever its length. So the first half of this file is a
differential against the OLD function, kept below as the test-only reference, over thousands of
generated keys and every floor shape the form paths build. The second half counts: the number of
questions asked of the floor and the characters in them, never wall-clock time.

Refs #3830, #3816, #3826, #1646, #1468.
"""

from __future__ import annotations

import random
import zlib

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.db import models
from django.forms import modelformset_factory
from django.http import QueryDict
from django.test.utils import isolate_apps

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()


# ---------------------------------------------------------------------------------------------
# The reference: the function as it was before #3830, verbatim. TEST-ONLY.
def reference_readable(floor, key):
    if not isinstance(key, str):
        return True
    if not serialization._floor_field_readable(floor, key):
        return False
    cut = key.find("-")
    while cut != -1:
        if not serialization._floor_field_readable(floor, key[cut + 1 :]):
            return False
        cut = key.find("-", cut + 1)
    return True


# ---------------------------------------------------------------------------------------------
# Floors: every shape the form paths hand to the function.
LONG_PASSWORD_NAME = "a_password_field_with_a_rather_long_name_x"  # 42 chars: past every other name
LONG_DENIED = "d" * 47
LONG_ALLOWED = "w" * 53
LONG_OPTOUT = "o" * 50


class _Pair(forms.MultiWidget):
    def decompress(self, value):
        return [None] * len(self.widgets)


class _PairField(forms.MultiValueField):
    def __init__(self, widget, **kwargs):
        super().__init__(
            fields=[forms.CharField(required=False) for _ in widget.widgets],
            widget=widget,
            require_all_fields=False,
            required=False,
            **kwargs,
        )

    def compress(self, data_list):
        return data_list


def _plain_form():
    class Plain(forms.Form):
        title = forms.CharField(required=False)
        pin = forms.CharField(widget=forms.PasswordInput, required=False)
        pair = _PairField(_Pair(widgets=[forms.TextInput, forms.PasswordInput]))
        keyed = _PairField(_Pair(widgets={"left": forms.TextInput, "right": forms.PasswordInput}))
        empty_keyed = _PairField(_Pair(widgets={"": forms.PasswordInput}))

    Plain.base_fields[LONG_PASSWORD_NAME] = forms.CharField(
        widget=forms.PasswordInput, required=False
    )
    return Plain()


def _user_form(**_):
    class Edit(forms.ModelForm):
        pin = forms.CharField(widget=forms.PasswordInput, required=False)
        pair = _PairField(_Pair(widgets=[forms.TextInput, forms.PasswordInput]))

        class Meta:
            model = User
            fields = ["username"]

    Edit.base_fields[LONG_PASSWORD_NAME] = forms.CharField(
        widget=forms.PasswordInput, required=False
    )
    return Edit(instance=User(username="u"))


def _isolated_model():
    """A model whose column names are longer than every name in the floor, one of them a TYPE-floor
    field (``BinaryField``), one a foreign key (``attname`` ends ``_id``)."""
    with isolate_apps("django.contrib.auth"):

        class Big(models.Model):
            a_binary_column_with_a_name_longer_than_the_rest = models.BinaryField(null=True)
            a_foreign_key_column_with_a_long_name_too = models.ForeignKey(
                User, null=True, on_delete=models.CASCADE, related_name="+"
            )
            plain = models.CharField(max_length=3)

            class Meta:
                app_label = "auth"

    return Big


BIG = _isolated_model()


def _tiny_model():
    with isolate_apps("django.contrib.auth"):

        class Tiny(models.Model):
            a = models.CharField(max_length=3)

            class Meta:
                app_label = "auth"

    return Tiny


TINY = _tiny_model()


def _floors(monkeypatch):
    """``{label: floor tuple}``. ``monkeypatch`` scopes the per-model sets on ``User``."""
    out = {}
    out["plain form"] = serialization._model_form_floor(_plain_form())
    out["user form"] = serialization._model_form_floor(_user_form())

    monkeypatch.setattr(
        User, "djust_serializable_fields", ("username", "email", LONG_ALLOWED), raising=False
    )
    out["user form + allowlist"] = serialization._model_form_floor(_user_form())

    monkeypatch.setattr(User, "djust_serializable_fields", None, raising=False)
    monkeypatch.setattr(User, "djust_exclude_fields", ("first_name", LONG_DENIED), raising=False)
    out["user form + exclude"] = serialization._model_form_floor(_user_form())

    monkeypatch.setattr(User, "djust_exclude_fields", None, raising=False)
    # the opt-out only matters beside an allowlist (it is what lets a name the list omits through)
    monkeypatch.setattr(User, "djust_serializable_fields", ("username",), raising=False)
    monkeypatch.setattr(
        User, "djust_serialize_sensitive_fields", ("password", LONG_OPTOUT), raising=False
    )
    out["user form + optout"] = serialization._model_form_floor(_user_form())

    monkeypatch.setattr(User, "djust_exclude_fields", ("first_name", LONG_DENIED), raising=False)
    monkeypatch.setattr(
        User, "djust_serializable_fields", ("username", "password", LONG_ALLOWED), raising=False
    )
    out["user form + all three"] = serialization._model_form_floor(_user_form())

    monkeypatch.setattr(User, "djust_serializable_fields", None, raising=False)
    monkeypatch.setattr(User, "djust_exclude_fields", None, raising=False)
    monkeypatch.setattr(User, "djust_serialize_sensitive_fields", None, raising=False)
    formset = modelformset_factory(User, fields=["username"])(queryset=User.objects.none())
    out["model formset"] = serialization._formset_floor(formset)

    out["isolated model, long columns"] = serialization._floor_for_model(
        BIG, frozenset({"secret_pin", "secret_pin_0"})
    )
    # every name short: the longest name the floor singles out is a sensitive METHOD name
    out["tiny model, short names"] = serialization._floor_for_model(TINY, frozenset({"p"}))
    out["plain floor tuple"] = serialization._floor_for_model(None, frozenset({"pin", "pair_0"}))
    return out


@pytest.fixture
def floors(monkeypatch):
    return _floors(monkeypatch)


def special_names(floor):
    """Every name the floor can single out, gathered here and not by the code under test."""
    model_class, denied, allowed, optout, passwords = floor
    names = set(
        serialization._FormPasswordNames._names(passwords)
        if isinstance(passwords, serialization._FormPasswordNames)
        else passwords
    )
    names |= set(denied) | set(allowed or ()) | set(optout)
    names |= set(serialization._IDENTITY_KEYS) | set(serialization._SENSITIVE_MODEL_METHODS)
    if model_class is not None:
        for field in model_class._meta.get_fields(include_hidden=True):
            names.add(field.name)
            names.add(getattr(field, "attname", field.name))
    return {n for n in names if isinstance(n, str)}


def key_corpus(floor, count, seed):
    rng = random.Random(seed)
    specials = sorted(special_names(floor))
    bound = serialization._floor_special_name_bound(floor)
    if bound is None:
        bound = 50
    fillers = [
        "x" * k
        for k in sorted({max(bound - 1, 0), bound, bound + 1, bound + 2, 2 * bound + 3, 300})
    ]
    tokens = specials + [
        "", "_", "_0", "_1", "_2", "u", "form", "0", "1", "x", "é", "ñé",
        "get_next_by_", "get_previous_by_created", "get_next_by_x", "__init__", "_private",
        "ＡＢ",
    ]  # fmt: skip
    seps = ["-", "-", "-", "--", "", "_", "-_", "_-"]
    keys = []
    # Deterministic: every special name, bare, wrapped, bent by one character, behind a long run.
    for name in specials:
        for variant in (
            name,
            name + "-",
            "-" + name,
            "u-" + name,
            "form-0-" + name,
            "form-0-" + name + "-",
            name[:-1],
            name + "x",
            name + "_0",
            name + "_1",
            "_" + name,
            name.upper(),
            "x-" + name + "x",
            "-" * (bound + 1) + name,
            "-" * (bound + 2) + "-" + name,
            "x" * (bound + 3) + "-" + name,
            name + "-" + "x" * (bound + 3),
            name + "-" + "_" * (bound + 3),
            "-".join([name] * 3),
            "u-form-0-" + name + "-z",
        ):
            keys.append(variant)  # fmt: skip
    for run in range(0, 3 * bound + 5):
        keys.extend(["-" * run, "-" * run + "x", "x" + "-" * run, "_" + "-" * run, "-" * run + "_"])
        keys.append("-" * run + "get_next_by_" + "-" * run)
        keys.append("-" + "g" * run)
    # Random compositions.
    for _ in range(count):
        parts = [
            rng.choice(tokens) if rng.random() < 0.8 else rng.choice(fillers)
            for _ in range(rng.randint(1, 7))
        ]
        key = "".join(p + rng.choice(seps) for p in parts[:-1]) + parts[-1]
        if rng.random() < 0.15:
            key = "-" + key
        if rng.random() < 0.15:
            key += "-"
        keys.append(key)
    return keys


# ---------------------------------------------------------------------------------------------
class TestSameVerdictAsTheOldFunction:
    """The new classification is a re-derivation of the old one, so the old one is the oracle."""

    PER_FLOOR = 2500

    def test_every_floor_shape_agrees_on_every_generated_key(self, floors):
        total = 0
        for label, floor in floors.items():
            keys = key_corpus(floor, self.PER_FLOOR, seed=zlib.crc32(label.encode()))
            verdicts = {True: 0, False: 0}
            for key in keys:
                want = reference_readable(floor, key)
                got = serialization._floor_data_key_readable(floor, key)
                assert got == want, (label, key[:90], len(key), "old:", want, "new:", got)
                verdicts[want] += 1
            # Not vacuous: the corpus reaches both answers on every floor.
            assert verdicts[True] and verdicts[False], (label, verdicts)
            total += len(keys)
        assert total >= 10_000, total

    def test_a_whole_mapping_is_filtered_like_the_old_function_would(self, floors):
        for label, floor in floors.items():
            keys = list(dict.fromkeys(key_corpus(floor, 800, seed=7)))
            data = QueryDict(mutable=True)
            for key in keys:
                data[key] = "v"
            kept = serialization._without_floor_keys(
                data, floor, serialization._floor_data_key_readable
            )
            assert set(kept) == {k for k in keys if reference_readable(floor, k)}, label

    def test_non_string_keys_are_readable(self, floors):
        floor = floors["user form"]
        for key in (None, 0, 1.5, ("a", "-"), b"-"):
            assert serialization._floor_data_key_readable(floor, key) is True

    def test_the_old_function_is_not_the_skip_the_long_tails_shortcut(self, floors):
        """The tempting fix is to ignore a tail longer than every special name. It is wrong: the
        model arm refuses a name that begins with ``_`` (and, under an allowlist, every name it does
        not list) whatever its length. This pins that the answer for such a tail is kept."""
        long_private = "x-_" + "z" * 300
        floor = floors["user form"]
        assert reference_readable(floor, long_private) is False
        assert serialization._floor_data_key_readable(floor, long_private) is False
        allow = floors["user form + allowlist"]
        long_unlisted = "u-" + "z" * 300
        assert reference_readable(allow, long_unlisted) is False
        assert serialization._floor_data_key_readable(allow, long_unlisted) is False
        assert serialization._floor_data_key_readable(floors["user form"], long_unlisted) is True

    def test_a_floor_it_cannot_bound_is_asked_in_full_and_agrees(self, monkeypatch):
        monkeypatch.setattr(User, "djust_exclude_fields", (5, "first_name"), raising=False)
        floor = serialization._model_form_floor(_user_form())
        assert serialization._floor_special_name_bound(floor) is None
        for key in key_corpus(floor, 600, seed=3):
            assert serialization._floor_data_key_readable(floor, key) == reference_readable(
                floor, key
            ), key[:80]

    def test_the_bound_covers_every_name_the_floor_can_single_out(self, floors):
        for label, floor in floors.items():
            bound = serialization._floor_special_name_bound(floor)
            assert bound is not None, label
            # a plain Form has no model rule: only its password names are special to it
            names = special_names(floor) if floor[0] is not None else set(floor[4])
            assert bound >= max(len(n) for n in names), label
            if floor[0] is not None:
                assert bound >= max(len(p) for p in serialization._SENSITIVE_MODEL_METHOD_PREFIXES)


# ---------------------------------------------------------------------------------------------
class Counting:
    """Replaces ``_floor_field_readable`` and records how much it was asked."""

    def __init__(self, monkeypatch):
        self.calls = 0
        self.chars = 0
        original = serialization._floor_field_readable

        def counted(floor, name):
            self.calls += 1
            self.chars += len(name) if isinstance(name, str) else 0
            return original(floor, name)

        monkeypatch.setattr(serialization, "_floor_field_readable", counted)


def dashes(key):
    return key.count("-")


def random_dashed(length, seed):
    rng = random.Random(seed)
    return "".join(rng.choice("ab-") for _ in range(length))


KEYS_32K = {
    "dashes": lambda: "-" * 32_000,
    "dashed words": lambda: "ab-" * 10_667,
    "prefixed": lambda: "u-" + "a" * 32_000,
    "ends in a password field": lambda: "-" * 32_000 + "-pin",
    "private tail": lambda: "u-_" + "-" * 32_000,
    "random dashes": lambda: random_dashed(32_000, 5),
}


class TestWorkIsLinearInTheKeyNotQuadratic:
    """Counted, never timed: wall-clock would flake under load. A key of ``L`` characters may cost
    the floor ``O(L)`` characters of questions in all, however many dashes it has."""

    @pytest.mark.parametrize("label", sorted(KEYS_32K))
    def test_a_32kb_dashed_key_asks_the_floor_a_linear_number_of_characters(
        self, floors, monkeypatch, label
    ):
        floor = floors["user form"]
        key = KEYS_32K[label]()
        bound = serialization._floor_special_name_bound(floor)
        counting = Counting(monkeypatch)
        serialization._floor_data_key_readable(floor, key)
        # one whole-key question (the key's own length), and every tail at most bound + 1 characters
        assert counting.chars <= len(key) + (bound + 1) * counting.calls, (
            counting.chars,
            counting.calls,
        )
        assert counting.calls <= dashes(key) + 1, (counting.calls, dashes(key))
        # The OLD function asked ~L^2/2 characters for the same key (512,016,016 for 32,000 dashes):
        assert counting.chars < 20 * len(key) + 100_000, counting.chars

    def test_a_run_of_dashes_costs_a_constant_number_of_questions(self, floors, monkeypatch):
        floor = floors["user form"]
        bound = serialization._floor_special_name_bound(floor)
        calls = {}
        for length in (4_000, 32_000, 128_000):
            counting = Counting(monkeypatch)
            assert serialization._floor_data_key_readable(floor, "-" * length) is True
            calls[length] = counting.calls
        # the tails short enough to be special are at most bound + 1; the rest share one answer
        assert calls[32_000] == calls[128_000] == calls[4_000], calls
        assert calls[128_000] <= 3 * (bound + 2), (calls, bound)

    def test_doubling_a_random_key_at_most_doubles_the_characters_asked(self, floors, monkeypatch):
        floor = floors["user form"]
        chars = {}
        for length in (16_000, 32_000, 64_000):
            counting = Counting(monkeypatch)
            serialization._floor_data_key_readable(floor, random_dashed(length, 11))
            chars[length] = counting.chars
        assert chars[32_000] <= 2.3 * chars[16_000], chars
        assert chars[64_000] <= 2.3 * chars[32_000], chars

    def test_a_2_5mb_body_of_dashed_keys_is_linear_in_the_body(self, floors, monkeypatch):
        floor = floors["user form"]
        bound = serialization._floor_special_name_bound(floor)
        data = QueryDict(mutable=True)
        for i in range(5_000):  # 5,000 keys x ~500 dashes: ~2.5 MB, the issue's second shape
            data[f"{i:05d}" + "-" * 500] = "1"
        data["username"] = "u"
        data["u-pin"] = "typed"
        body = sum(len(k) for k in data)
        counting = Counting(monkeypatch)
        kept = serialization._without_floor_keys(
            data, floor, serialization._floor_data_key_readable
        )
        assert "username" in kept and "u-pin" not in kept and len(kept) == 5_001
        assert counting.chars <= body + (bound + 1) * counting.calls, (counting.chars, body)
        assert counting.calls <= len(data) * (bound + 3), (counting.calls, len(data), bound)

    def test_a_body_of_random_dashed_keys_is_linear_in_the_body(self, floors, monkeypatch):
        floor = floors["user form"]
        bound = serialization._floor_special_name_bound(floor)
        data = QueryDict(mutable=True)
        for i in range(600):
            data[f"{i:04d}" + random_dashed(500, i)] = "1"
        body = sum(len(k) for k in data)
        counting = Counting(monkeypatch)
        serialization._without_floor_keys(data, floor, serialization._floor_data_key_readable)
        assert counting.chars <= body + (bound + 1) * counting.calls, (counting.chars, body)
        assert counting.calls <= body // 2, (
            counting.calls,
            body,
        )  # at most one per dash, and dashes are < half

    def test_the_reader_is_set_up_once_per_mapping_not_per_key(self, floors, monkeypatch):
        floor = floors["user form"]
        built = []
        original = serialization._floor_special_name_bound
        monkeypatch.setattr(
            serialization,
            "_floor_special_name_bound",
            lambda fl: built.append(1) or original(fl),
        )
        names_read = []
        original_names = serialization._FormPasswordNames._names
        monkeypatch.setattr(
            serialization._FormPasswordNames,
            "_names",
            lambda self: names_read.append(1) or original_names(self),
        )
        data = QueryDict(mutable=True)
        for i in range(3_000):
            data[f"k{i}-a-b"] = "1"
        serialization._without_floor_keys(data, floor, serialization._floor_data_key_readable)
        assert len(built) == 1, len(built)
        assert len(names_read) <= 2, len(names_read)  # the form is read, not asked per key


# ---------------------------------------------------------------------------------------------
class TestSiblingPathsHaveNoSuchLoop:
    """The same shape elsewhere: a loop that slices an attacker-chosen key at every dash (#3830)."""

    def test_the_bare_name_filter_asks_once_per_key_whatever_its_length(self, floors, monkeypatch):
        """``initial`` / ``cleaned_data`` are keyed by field name; ``_floor_field_readable`` is one
        hash of the name, so a long key is linear in its own length (no tails)."""
        floor = floors["user form"]
        data = {"-" * 32_000: 1, "u" * 32_000: 2, "username": 3}
        counting = Counting(monkeypatch)
        serialization._without_floor_keys(data, floor)
        assert counting.calls == len(data)
        assert counting.chars == sum(len(k) for k in data)

    def test_a_list_of_mappings_is_one_question_per_key(self, floors, monkeypatch):
        floor = floors["model formset"]
        rows = [{"username": "a", "-" * 5_000: 1} for _ in range(40)]
        counting = Counting(monkeypatch)
        serialization._without_floor_keys(rows, floor)
        assert counting.calls == 80


# ---------------------------------------------------------------------------------------------
class TestThroughTheProxiesATemplateReaches:
    """The places ``form.data`` is read by a template path, end to end (no native engine)."""

    LONG = "-" * 32_000

    def posted(self):
        data = QueryDict(mutable=True)
        data["username"] = "u"
        data["u-pin"] = "typed"
        data["pin"] = "typed"
        data[self.LONG] = "1"
        return data

    def test_form_data_keeps_the_long_key_and_drops_the_password_keys(self, monkeypatch):
        form = _user_form().__class__(self.posted(), prefix="u")
        proxy = serialization._protect_sidecar_walk(form)
        counting = Counting(monkeypatch)
        data = proxy.data
        assert set(data) == {"username", self.LONG}
        assert counting.chars <= 5 * len(self.LONG), counting.chars  # was ~512,000,000

    def test_formset_data_and_its_management_form_data(self, monkeypatch):
        class Row(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput, required=False)

        data = QueryDict(mutable=True)
        data.update({"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-pw": "typed"})
        data[self.LONG] = "1"
        formset = forms.formset_factory(Row)(data)
        proxy = serialization._protect_sidecar_walk(formset)
        counting = Counting(monkeypatch)
        assert "form-0-pw" not in proxy.data and self.LONG in proxy.data
        assert "form-0-pw" not in proxy.management_form.data
        assert self.LONG in proxy.management_form.data
        assert counting.chars <= 10 * len(self.LONG), counting.chars
