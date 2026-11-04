"""A path step asks "is this name a password field's?" by NAME, not by scanning the form (#3816).

#3813 (#3798) put the password-widget floor at ``_model_form_floor``, which built the form's whole
set of password names, scanning every field, for every ``BoundField`` a template walked. A loop over
a 100-field form is 100 walked fields x 100 scanned fields per pass: +35-50% on a loop-heavy render.

The fix is not a memo. A memo keyed on the ``fields`` mapping misses a widget swapped on a live form,
and one that validates itself reads every field's widget, which is the scan again. The question is
instead asked per name: ``_password_name_held(fields, name)`` is a dict lookup per way of splitting
the name into ``<field><suffix>``, and a plain form's bound field never builds the form's floor. So
there is nothing to go stale and nothing to share.

What these tests hold, and the gate-off of each (``scratch`` harness, recorded in the PR):

* ``TestSameAnswerAsTheScan``: the by-name answer equals ``name in _password_field_names(fields)``
  for every name of a grammar of forms (plain, subclass, drawn-as-password, ``MultiWidget`` with
  indexed, keyed and empty-keyed parts, a field NAMED like another field's part).
* ``TestAWidgetSwapOnALiveFormIsFollowed``: text -> password -> text on one live form, read through
  the native engine and through ``form_field_floor_state``, in both directions.
* ``TestNothingIsSharedBetweenForms``: two forms of one class, a copy, a field set, and threads.
* ``TestAStepDoesNotScanTheForm``: the structural claim itself, counted on a ``fields`` mapping that
  records every whole-form read, so the quadratic cannot come back unseen.

Refs #3816, #3798, #3813, #1646, #1468.
"""

from __future__ import annotations

import copy
import itertools
import random
import re
import threading

import pytest
from django import forms
from django.contrib.auth.forms import UserCreationForm

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]


class Strong(forms.PasswordInput):
    """A ``PasswordInput`` subclass under a name nothing special-cases."""


class DrawnPassword(forms.TextInput):
    """Not a ``PasswordInput``, but it draws one."""

    input_type = "password"


def _pair(keyed=None):
    """A ``MultiWidget`` with a password part (``widgets_names`` ``_0, _1`` or ``_<key>``)."""
    parts = keyed if keyed is not None else [forms.TextInput, forms.PasswordInput]

    class Pair(forms.MultiWidget):
        def decompress(self, value):
            return [None] * len(parts)

    return Pair(widgets=parts)


class PairField(forms.MultiValueField):
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


def _fields(**spec):
    """``{name: Field}`` from ``name -> widget`` (None is a plain text field)."""
    out = {}
    for name, widget in spec.items():
        if isinstance(widget, forms.MultiWidget):
            out[name] = PairField(widget)
        else:
            out[name] = forms.CharField(widget=widget or forms.TextInput, required=False)
    return out


def _form(**spec):
    return type("F", (forms.Form,), _fields(**spec))()


def held(form, name):
    return serialization._password_name_held(form.fields, name)


def scanned(form, name):
    return name in serialization._password_field_names(form.fields)


# ---------------------------------------------------------------------------------------------
class TestSameAnswerAsTheScan:
    """The by-name answer IS the scan's answer, over names nobody enumerated in advance."""

    FORMS = {
        "plain": lambda: _form(a=None, b=None),
        "pw": lambda: _form(a=None, pin=forms.PasswordInput, b=None),
        "subclass": lambda: _form(pin=Strong, token=DrawnPassword, note=None),
        "indexed multi": lambda: _form(pair=_pair(), other=None),
        "keyed multi": lambda: _form(
            pair=_pair({"user": forms.TextInput, "secret": forms.PasswordInput})
        ),
        # a "" key gives the part the field's OWN name: suffix "" must not matter
        "empty key": lambda: _form(pair=_pair({"": forms.PasswordInput, "b": forms.TextInput})),
        # a field that is called like another field's part: held by the part's key, not its widget
        "name collides with a part": lambda: _form(
            pair=_pair(), pair_1=None, pair_0=None, pair_2=None
        ),
        "text multi": lambda: _form(pair=_pair([forms.TextInput, forms.TextInput]), pair_1=None),
        "underscored": lambda: _form(a_b=forms.PasswordInput, a=None, a_=None, _b=None),
        "nothing": lambda: _form(),
    }

    FRAGMENTS = [
        "",
        "a",
        "b",
        "pin",
        "pair",
        "token",
        "note",
        "_",
        "_0",
        "_1",
        "_2",
        "_user",
        "_secret",
        "-",
        "u-",
    ]

    def names_for(self, form):
        names = set(form.fields)
        for n in list(form.fields):
            for suffix in ("_0", "_1", "_2", "_user", "_secret", "_", "x", ""):
                names.add(n + suffix)
        names.update(
            "".join(p) for r in (1, 2, 3) for p in itertools.product(self.FRAGMENTS, repeat=r)
        )
        rng = random.Random(3816)
        alphabet = "ab_01-pinr"
        names.update(
            "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 9))) for _ in range(800)
        )
        return sorted(names)

    @pytest.mark.parametrize("which", sorted(FORMS))
    def test_the_answer_equals_the_scan_for_every_name(self, which):
        form = self.FORMS[which]()
        disagreements = [n for n in self.names_for(form) if held(form, n) != scanned(form, n)]
        assert disagreements == []

    def test_not_vacuous_some_forms_hold_names_and_some_do_not(self):
        pair = self.FORMS["indexed multi"]()
        # every part of a MultiWidget with a password part is held (it is submitted under all of them)
        assert held(pair, "pair") and held(pair, "pair_0") and held(pair, "pair_1")
        assert not held(pair, "pair_2")
        assert not held(pair, "other")
        plain = self.FORMS["plain"]()
        assert not any(held(plain, n) for n in ("a", "b", "a_0", ""))

    def test_the_view_answers_like_the_set_it_stands_for(self):
        form = self.FORMS["indexed multi"]()
        view = serialization._FormPasswordNames(form.fields)
        names = serialization._password_field_names(form.fields)
        assert view == names and names == view
        assert set(view) == set(names) and len(view) == len(names)
        assert ("pair_1" in view) and ("pair_2" not in view) and (3 not in view)
        assert bool(view) is True
        assert bool(serialization._FormPasswordNames(self.FORMS["plain"]().fields)) is False
        assert view != serialization._FormPasswordNames(self.FORMS["plain"]().fields)
        with pytest.raises(TypeError):
            hash(view)

    def test_a_fields_object_without_lookup_falls_back_to_the_scanned_set(self):
        class Items:
            def __init__(self, d):
                self.d = d

            def __bool__(self):
                return True

            def items(self):
                return self.d.items()

        names = serialization._form_password_names(Items(_fields(pin=forms.PasswordInput, x=None)))
        assert isinstance(names, frozenset) and names == {"pin"}


# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend({"NAME": "pw-3816", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture(autouse=True)
def _fresh_warning_memory(monkeypatch):
    monkeypatch.setattr(serialization, "_REFUSED_READ_WARNED", set())
    monkeypatch.setattr(serialization, "_EDITABLE_FLOOR_WARNED", set())


def render(engine, src, **context):
    return re.sub(
        r"\s+", " ", str(engine.from_string("{% load djust_components %}" + src).render(context))
    )


SECRET = "Typed-Pw-3816-xyz!"
LOOP = "{% for f in form %}[{{ f.name }}={{ f.value }}]{% endfor %}"


class SwapForm(forms.Form):
    name = forms.CharField()
    pin = forms.CharField(required=False)  # text to begin with
    other = forms.CharField(required=False)


class TestAWidgetSwapOnALiveFormIsFollowed:
    """One form object, its widget swapped between reads, as a view can do in ``get_context_data``.

    A memo keyed on the fields mapping returns the first answer for the rest of the form's life.
    Both directions are read, because "text -> password" is the leak and "password -> text" is the
    over-block, and a stale answer fails each in a different place.
    """

    def form(self):
        return SwapForm({"name": "n", "pin": SECRET, "other": "o"})

    def test_text_to_password_to_text_through_the_native_engine(self, engine):
        form = self.form()
        assert SECRET in render(engine, LOOP, form=form)  # a text field is readable
        form.fields["pin"].widget = forms.PasswordInput()
        assert SECRET not in render(engine, LOOP, form=form)  # now it is the floor
        form.fields["pin"].widget = forms.TextInput()
        assert SECRET in render(engine, LOOP, form=form)  # and readable again

    def test_the_same_through_form_field_floor_state_which_the_component_tags_ask(self):
        form = self.form()
        field = lambda: form["pin"]  # noqa: E731 - a bound field, re-fetched as a tag does
        assert serialization.form_field_floor_state(field()) is None
        form.fields["pin"].widget = forms.PasswordInput()
        assert serialization.form_field_floor_state(field()) == "password"
        form.fields["pin"].widget = forms.TextInput()
        assert serialization.form_field_floor_state(field()) is None

    def test_a_field_replaced_added_or_removed_is_followed_too(self):
        form = self.form()
        assert held(form, "extra") is False
        form.fields["extra"] = forms.CharField(widget=forms.PasswordInput)
        assert held(form, "extra") is True
        form.fields["extra"] = forms.CharField()  # replaced by a text field
        assert held(form, "extra") is False
        form.fields["pin"] = forms.CharField(widget=forms.PasswordInput)  # replaced by a password
        assert held(form, "pin") is True
        del form.fields["pin"]
        assert held(form, "pin") is False

    def test_a_widget_whose_input_type_is_changed_in_place_is_followed(self):
        form = self.form()
        assert held(form, "pin") is False
        form.fields["pin"].widget.input_type = "password"
        assert held(form, "pin") is True

    def test_a_multiwidget_part_swapped_in_place_is_followed(self):
        form = _form(pair=_pair([forms.TextInput, forms.TextInput]))
        assert not held(form, "pair") and not held(form, "pair_1")
        form.fields["pair"].widget.widgets[1] = forms.PasswordInput()
        assert held(form, "pair") and held(form, "pair_1")

    def test_a_floor_tuple_already_in_hand_is_as_current_as_the_form(self):
        """The floor of a ModelForm carries the names as a view, so a floor taken BEFORE the swap
        answers for the form AFTER it (an eager set would keep answering for the form it saw)."""

        class Signup(UserCreationForm):
            pass

        form = Signup({"username": "u", "password1": SECRET, "password2": SECRET})
        floor = serialization._model_form_floor(form)
        assert "nick" not in floor[4]
        form.fields["nick"] = forms.CharField(widget=forms.PasswordInput)
        assert "nick" in floor[4]

    def test_a_cached_proxy_of_a_bound_field_is_not_reused_after_it_stops_being_held(self, engine):
        """``_cached_bound_field_proxy`` rides on the BoundField; a field that stops being held must
        read as itself on the next walk, not as the proxy from when it was held."""
        form = self.form()
        form.fields["pin"].widget = forms.PasswordInput()
        assert SECRET not in render(engine, LOOP, form=form)
        form.fields["pin"].widget = forms.TextInput()
        assert f"[pin={SECRET}]" in render(engine, LOOP, form=form)


# ---------------------------------------------------------------------------------------------
class TestNothingIsSharedBetweenForms:
    def test_two_forms_of_one_class_answer_for_themselves(self):
        a, b = SwapForm(), SwapForm()
        a.fields["pin"].widget = forms.PasswordInput()
        assert held(a, "pin") is True
        assert held(b, "pin") is False  # a class-keyed answer would say True here
        b.fields["name"].widget = forms.PasswordInput()
        assert held(b, "name") is True and held(a, "name") is False

    def test_the_fields_of_a_class_are_not_the_answer_for_its_instances(self):
        form = SwapForm()
        assert serialization._password_name_held(SwapForm.base_fields, "pin") is False
        form.fields["pin"].widget = forms.PasswordInput()
        assert (
            serialization._password_name_held(SwapForm.base_fields, "pin") is False
        )  # not shared back
        assert held(form, "pin") is True

    def test_a_copy_of_a_form_does_not_inherit_the_original_s_answer(self):
        form = SwapForm()
        form.fields["pin"].widget = forms.PasswordInput()
        clone = copy.deepcopy(form)
        clone.fields["pin"].widget = forms.TextInput()
        assert held(form, "pin") is True and held(clone, "pin") is False

    def test_nothing_is_written_to_the_form_or_to_module_state(self):
        before = set(vars(serialization))
        form = SwapForm({"name": "n"})
        attrs = dict(vars(form))
        for bf in form:
            serialization._protect_sidecar_walk(bf)
            serialization.form_field_floor_state(bf)
        assert dict(vars(form)) == attrs
        assert set(vars(serialization)) == before
        assert not any(k.startswith("_djust") for k in vars(form))

    def test_threads_swapping_their_own_forms_each_read_their_own_answer(self):
        errors = []
        barrier = threading.Barrier(8)

        def worker(i):
            try:
                form = SwapForm()
                barrier.wait(timeout=10)
                for round_ in range(300):
                    want = (round_ + i) % 2 == 0
                    form.fields["pin"].widget = forms.PasswordInput() if want else forms.TextInput()
                    if held(form, "pin") is not want:
                        errors.append((i, round_, want))
                        return
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
        assert not any(t.is_alive() for t in threads)
        assert errors == []

    def test_threads_reading_one_form_while_another_form_is_swapped_agree(self):
        shared = SwapForm()
        shared.fields["pin"].widget = forms.PasswordInput()
        other = SwapForm()
        stop = threading.Event()
        bad = []

        def reader():
            while not stop.is_set():
                if not held(shared, "pin") or held(shared, "name"):
                    bad.append("shared form changed under a reader")
                    return

        def swapper():
            for i in range(2000):
                other.fields["pin"].widget = forms.PasswordInput() if i % 2 else forms.TextInput()
            stop.set()

        threads = [threading.Thread(target=reader) for _ in range(4)] + [
            threading.Thread(target=swapper)
        ]
        [t.start() for t in threads]
        [t.join(timeout=60) for t in threads]
        assert not any(t.is_alive() for t in threads)
        assert bad == []


# ---------------------------------------------------------------------------------------------
class Counting(dict):
    """A ``fields`` mapping that counts every WHOLE-form read (a scan) and every lookup by name."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scans = 0
        self.gets = 0

    def get(self, key, default=None):
        self.gets += 1
        return super().get(key, default)

    def items(self):
        self.scans += 1
        return super().items()

    def values(self):
        self.scans += 1
        return super().values()

    def keys(self):
        self.scans += 1
        return super().keys()

    def __iter__(self):
        self.scans += 1
        return super().__iter__()


def counted(form):
    form.fields = Counting(form.fields)
    return form


WIDE = 100


def wide_form_class(width):
    """``pw`` (a password field) and ``width`` text fields."""
    attrs = {"pw": forms.CharField(widget=forms.PasswordInput, required=False)}
    attrs.update({f"f{i}": forms.CharField(required=False) for i in range(width)})
    return type("Wide%d" % width, (forms.Form,), attrs)


Wide = wide_form_class(WIDE)


class TestAStepDoesNotScanTheForm:
    """The quadratic, stated structurally: a step reads ONE name of a 100-field form, never the form.

    A timing assertion would flake under load (#1795); a count of whole-form reads cannot. A field
    that IS held (``pw``) builds the form's floor, which scans once: that is the rare field, and it is
    the same cost as before. Every other field, the ones a wide form is made of, must scan nothing.
    """

    def test_the_walk_of_a_bound_field_of_a_plain_form_scans_nothing(self):
        form = counted(Wide())
        for bf in form:  # iterating the FORM yields bound fields: that is Django's, uncounted
            form.fields.scans = form.fields.gets = 0
            serialization._protect_sidecar_walk(bf)
            if bf.name == "pw":
                assert form.fields.scans >= 1  # the held field gets its floor, not a shortcut
                continue
            assert form.fields.scans == 0, bf.name
            assert form.fields.gets <= len(bf.name) + 1, bf.name

    def test_the_component_readers_scan_nothing_either(self):
        form = counted(Wide())
        states = {}
        for bf in form:
            form.fields.scans = 0
            states[bf.name] = serialization.form_field_floor_state(bf)
            if bf.name != "pw":
                assert form.fields.scans == 0, bf.name
        assert states["pw"] == "password" and {v for k, v in states.items() if k != "pw"} == {None}

    def test_the_walk_of_a_bound_field_of_a_model_form_scans_nothing(self):
        """A ``ModelForm`` is on the floor whatever its fields are, so even its password fields
        (``password1``) need no scan: the names ride as a view."""
        form = counted(UserCreationForm())
        form.fields.update({f"f{i}": forms.CharField(required=False) for i in range(WIDE)})
        for bf in form:
            form.fields.scans = form.fields.gets = 0
            serialization._protect_sidecar_walk(bf)
            serialization.form_field_floor_state(bf)
            assert form.fields.scans == 0, bf.name

    def test_not_vacuous_the_scan_is_counted_and_the_held_field_still_gets_its_floor(self):
        form = counted(Wide())
        form.fields.scans = 0
        serialization._password_field_names(form.fields)
        assert form.fields.scans >= 1  # the counter sees a scan when there is one
        # and a held field is not skipped by the cheap path: it is proxied, off the floor's rules
        proxied = serialization._protect_sidecar_walk(form["pw"])
        assert isinstance(proxied, serialization._SidecarBoundFieldProxy)
        assert serialization._protect_sidecar_walk(form["f0"]) is form["f0"]

    def test_the_whole_form_reads_of_a_native_loop_do_not_grow_with_the_width(self, engine):
        """The number of scans a template loop makes is the same for 10 fields and for 100: it is not
        a function of how many steps the loop takes (which is the width)."""

        def scans_for(width):
            form = counted(wide_form_class(width)({"f0": "x", "pw": SECRET}))
            out = render(engine, LOOP, form=form)
            assert SECRET not in out and "[f0=x]" in out
            return form.fields.scans

        narrow, wide = scans_for(10), scans_for(WIDE)
        assert wide == narrow, (narrow, wide)


# ---------------------------------------------------------------------------------------------
class TestWhatTheCheapPathMustNotChange:
    """Fast path = same answer. The shapes where a shortcut tends to be wrong."""

    def test_a_pinned_management_form_keeps_its_pinned_floor(self):
        from django.forms import formset_factory

        class Row(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput, required=False)

        formset = formset_factory(Row)(
            {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-pw": SECRET}
        )
        proxy = serialization._protect_sidecar_walk(formset)
        management = proxy.management_form
        assert SECRET not in str(getattr(management, "data", ""))

    def test_a_pinned_floor_governs_the_bound_fields_of_the_form_it_is_pinned_on(self):
        """The pin is the formset's way to hand out a form under ITS floor. The shortcut asks the
        form's own fields, which would not name a field the pin refuses; so it must step aside."""
        from django.forms import formset_factory

        class Row(forms.Form):
            x = forms.CharField(required=False)

        management = formset_factory(Row)().management_form
        assert (
            serialization._bound_field_floor(management["TOTAL_FORMS"]) is None
        )  # control: no pin
        pinned = serialization._floor_for_model(None, frozenset({"TOTAL_FORMS"}))
        management._djust_floor = pinned
        assert serialization._bound_field_floor(management["TOTAL_FORMS"]) is pinned
        walked = serialization._protect_sidecar_walk(management["TOTAL_FORMS"])
        assert isinstance(walked, serialization._SidecarBoundFieldProxy)
        assert (
            serialization._protect_sidecar_walk(management["INITIAL_FORMS"])
            is management["INITIAL_FORMS"]
        )

    def test_a_model_form_field_is_decided_by_the_model_floor_not_only_by_its_widget(self):
        form = UserCreationForm({"username": "u", "password1": SECRET, "password2": SECRET})
        assert serialization.form_field_floor_state(form["password1"]) == "password"
        assert serialization.form_field_floor_state(form["username"]) is None

    def test_a_plain_form_field_named_password_with_a_text_widget_is_not_on_the_floor(self):
        """#3799 is open: the widget floor is by TYPE. A shortcut that guessed from the name would
        move it."""

        class Plain(forms.Form):
            password = forms.CharField(required=False)

        assert serialization.form_field_floor_state(Plain({"password": "x"})["password"]) is None
