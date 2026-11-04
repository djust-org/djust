"""A password-widget field's value is on the form floor, by WIDGET TYPE (#3798).

#3796 (#3790) holds a model form's values to the MODEL's serialization floor: a field is on it
because the model names it (``password``, ``is_staff``, ``DJUST_SENSITIVE_FIELDS``...). A bound
``UserCreationForm`` holds what the user TYPED under ``password1`` / ``password2``, which are no
columns of ``User``, so a template could still read the plaintext back under the native engine
(``{{ form.password1.value }}``, ``{{ form.data.password1 }}``, ``{{ form.cleaned_data.password1 }}``)
and so could it from any plain ``Form`` (which has no model floor at all, #3799).

The floor added here is keyed on the WIDGET: a field whose widget is a password input holds a secret
whatever the form is and whatever the field is called. It sits at the existing chokepoint
(``_model_form_floor`` / ``_floor_field_readable``), so the form, bound-field and formset proxies and
the engine's conversion floor all pick it up. The randomized differential in
``test_form_floor_differential_3790.py`` carries the producer x sink cross product for these forms;
this file holds what a grammar cannot say: what a template SHOULD still see.

Measured, not assumed: Django's ``PasswordInput`` never writes a value back into the control
(``render_value=False``), so ``{{ form.password1 }}`` of a typed-but-invalid form is the same bytes
with or without this floor, and a re-save posts what the person types. Nothing is lost by refusing
the READ; ``TestTheControlStillDraws`` pins that, ``TestWhatPostingBackDoes`` the re-save.

Refs #3798, #3799, #3790, #3796, #1646, #1468.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.template import Context, Engine

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
TYPED = "Typed-Pw-3798-xyz!"


class Strong(forms.PasswordInput):
    """A ``PasswordInput`` subclass under a name the component tags do not know."""


class DrawnPassword(forms.TextInput):
    """Not a ``PasswordInput``, but it draws one."""

    input_type = "password"


class SignupForm(forms.Form):
    name = forms.CharField()
    pin = forms.CharField(widget=Strong, required=False)
    token = forms.CharField(widget=DrawnPassword, required=False)
    note = forms.CharField(required=False)


class PlainTextPassword(forms.Form):
    """A field NAMED ``password`` with a plain ``TextInput``: not a password WIDGET (#3799 is open)."""

    password = forms.CharField(required=False)


@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend({"NAME": "pw-3798", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture(autouse=True)
def _fresh_warning_memory(monkeypatch):
    monkeypatch.setattr(serialization, "_REFUSED_READ_WARNED", set())
    monkeypatch.setattr(serialization, "_EDITABLE_FLOOR_WARNED", set())


def native(engine, src: str, **context) -> str:
    out = engine.from_string("{% load djust_components %}" + src).render(context)
    return re.sub(r"\s+", " ", str(out))


def django_(src: str, **context) -> str:
    return re.sub(r"\s+", " ", Engine().from_string(src).render(Context(context)))


def created(**overrides):
    data = {"username": "newbie", "password1": TYPED, "password2": TYPED}
    data.update(overrides)
    form = UserCreationForm(data)
    form.is_valid()  # fills ``cleaned_data`` (and ``errors``) the way a view's POST handler does
    return form


# ---------------------------------------------------------------------------------------------
#: Spellings Django itself renders empty for a ``PasswordInput`` (its subwidget carries no value, a
#: bound form has no ``initial`` for the field): held anyway, but no control to compare against.
DJANGO_PRINTS_NOTHING = {
    "{{ form.password1.initial }}",
    "{{ form.password1.subwidgets.0.data.value }}",
    "{% for w in form.password1 %}{{ w.data.value }}{% endfor %}",
}


class TestTheTypedPasswordIsHeld:
    """The issue's three spellings, and the ones around them, on a bound ``UserCreationForm``."""

    SPELLINGS = [
        "{{ form.password1.value }}",
        "{{ form.password2.value }}",
        "{{ form.data.password1 }}",
        "{{ form.cleaned_data.password1 }}",
        "{{ form.password1.data }}",
        "{{ form.password1.initial }}",
        "{{ form.password1.subwidgets.0.data.value }}",
        "{% for w in form.password1 %}{{ w.data.value }}{% endfor %}",
        "{{ form.password1.as_text }}{{ form.password1.as_textarea }}{{ form.password1.as_hidden }}",
        "{% for f in form %}{{ f.value }}{{ f.data }}{% endfor %}",
        "{% for f in form.visible_fields %}{{ f.value }}{% endfor %}",
        "{{ form.data }}{{ form.cleaned_data }}",
        "{{ form.data|pprint }}{{ form|pprint }}",
        "{% with p=form.password1 %}{{ p.value }}{{ p.form.data }}{% endwith %}",
        "{{ form.get_context.form.data }}",
        "{% for k, v in form.cleaned_data.items %}{{ v }}{% endfor %}",
    ]

    @pytest.mark.parametrize("src", SPELLINGS)
    def test_the_native_engine_does_not_print_it(self, engine, src):
        assert TYPED not in native(engine, src, form=created())

    @pytest.mark.parametrize("src", SPELLINGS)
    def test_not_vacuous_django_prints_it_for_the_same_template(self, src):
        """The control: the Django engine prints the secret for (nearly) every spelling, so the
        assertion above is two different engines, not two empty strings."""
        out = django_(src, form=created())
        if src in DJANGO_PRINTS_NOTHING:
            return
        assert TYPED in out, src

    def test_a_prefixed_form_is_held_through_its_prefixed_data_key(self, engine):
        form = UserCreationForm(
            {"u-username": "newbie", "u-password1": TYPED, "u-password2": TYPED}, prefix="u"
        )
        assert "u-password1" in form.data
        out = native(engine, "{{ form.data }}{{ form.password1.value }}", form=form)
        assert TYPED not in out
        assert "newbie" in out  # the rest of the form still reads

    def test_a_field_not_on_the_floor_still_reads(self, engine):
        """Not over-blocking: the username is plain text, and every reader of it equals Django's."""
        for src in (
            "{{ form.username.value }}",
            "{{ form.data.username }}",
            "{{ form.cleaned_data.username }}",
            "{{ form.username.data }}",
        ):
            assert native(engine, src, form=created()) == django_(src, form=created()), src
            assert "newbie" in native(engine, src, form=created()), src


class TestAPlainFormHasNoModelFloorButHasTheWidgetFloor:
    """#3799 option (3): the widget-type floor covers a plain ``Form`` without touching names."""

    def test_a_plain_form_password_widget_is_held(self, engine):
        form = SignupForm({"name": "n", "pin": TYPED, "token": TYPED + "-t"})
        for src in (
            "{{ form.pin.value }}",
            "{{ form.data.pin }}",
            "{{ form.cleaned_data.pin }}",
            "{{ form.token.value }}",
            "{{ form.cleaned_data.token }}",
        ):
            out = native(engine, src, form=form)
            assert TYPED not in out, src

    def test_a_subclass_and_a_widget_that_draws_a_password_are_both_password_fields(self):
        form = SignupForm()
        assert serialization.field_has_password_widget(form.fields["pin"])
        assert serialization.field_has_password_widget(form.fields["token"])
        assert not serialization.field_has_password_widget(form.fields["name"])
        assert not serialization.field_has_password_widget(form.fields["note"])

    def test_the_other_fields_of_that_form_still_read(self, engine):
        form = SignupForm({"name": "Ada", "pin": TYPED, "note": "hello"})
        for src in (
            "{{ form.name.value }}",
            "{{ form.data.note }}",
            "{{ form.cleaned_data.note }}",
        ):
            assert native(engine, src, form=form) == django_(src, form=form), src

    def test_the_initial_of_an_unbound_form_is_held_too(self, engine):
        """``BoundField.value()`` of an unbound form is its ``initial``."""
        form = SignupForm(initial={"name": "Ada", "pin": TYPED})
        assert TYPED in django_("{{ form.pin.value }}{{ form.initial.pin }}", form=form)
        out = native(
            engine, "{{ form.pin.value }}{{ form.initial.pin }}{{ form.initial }}", form=form
        )
        assert TYPED not in out
        assert "Ada" in out

    def test_a_field_named_password_with_a_text_widget_is_NOT_held_here(self, engine):
        """The floor is the WIDGET's, not a name's. A plain form's ``password`` text field is #3799
        option (2), a product decision this change does not take: pinned as a stated limit."""
        form = PlainTextPassword({"password": "visible-text"})
        out = native(engine, "{{ form.password.value }}", form=form)
        assert "visible-text" in out

    def test_a_plain_form_with_no_password_field_is_returned_unchanged(self):
        """No floor, so no proxy: the engine's walk is not slowed or wrapped for a form with nothing
        to hold."""
        form = forms.Form()
        assert serialization._protect_sidecar_walk(form) is form
        plain = PlainTextPassword({"password": "x"})
        assert serialization._protect_sidecar_walk(plain) is plain
        assert serialization._protect_sidecar_walk(plain["password"]) is plain["password"]


class TestTheControlStillDraws:
    """What a template SHOULD still see: the widget the form author chose, byte for byte as Django."""

    @pytest.mark.parametrize(
        "src",
        [
            "{{ form.password1 }}",
            "{{ form.password2 }}",
            "{{ form }}",
            "{{ form.as_p }}",
            "{{ form.as_div }}",
            "{% for f in form %}{{ f.label_tag }}{{ f }}{{ f.errors }}{% endfor %}",
            "{{ form.password1.label }}|{{ form.password1.html_name }}|{{ form.password1.name }}",
            "{{ form.password1.id_for_label }}|{{ form.password1.help_text }}",
            "{{ form.password1.field.required }}|{{ form.password1.field.widget.input_type }}",
            "{{ form.password1.is_hidden }}|{{ form.password1.css_classes }}",
        ],
    )
    def test_same_bytes_as_django(self, engine, src):
        assert native(engine, src, form=created()) == django_(src, form=created())

    def test_a_typed_but_invalid_form_rerenders_with_no_password_value_and_its_errors(self, engine):
        form = created(password2="does-not-match")
        assert not form.is_valid()
        out = native(engine, "{{ form }}{{ form.errors }}", form=form)
        ref = django_("{{ form }}{{ form.errors }}", form=form)
        assert out == ref
        assert TYPED not in out and "does-not-match" not in out
        assert "newbie" in out  # the other field is re-rendered with its value
        assert "didn" in ref  # Django's own "two password fields didn't match" error is in both

    def test_the_error_messages_of_the_field_are_readable(self, engine):
        form = created(password2="does-not-match")
        src = "{{ form.password2.errors }}{{ form.non_field_errors }}{{ form.errors.password2 }}"
        assert native(engine, src, form=form) == django_(src, form=form)
        assert native(engine, src, form=form).strip()

    def test_render_value_true_is_the_authors_choice_and_still_draws(self, engine):
        """``PasswordInput(render_value=True)`` writes the value into the control: the form's own
        rendering is the widget the view author chose, and is not refused (the same as every floor
        field since #3796). The template READS of the value are still refused."""

        class Echo(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput(render_value=True))

        form = Echo({"pw": TYPED})
        assert native(engine, "{{ form.pw }}", form=form) == django_("{{ form.pw }}", form=form)
        assert TYPED in native(engine, "{{ form.pw }}", form=form)
        assert TYPED not in native(engine, "{{ form.pw.value }}{{ form.data.pw }}", form=form)


class TestWhatTheRefusalLooksLike:
    """A refused read is the empty string, which is what ``{{ obj.password }}`` is on a model."""

    def test_a_refused_value_is_falsy_and_defaults_apply(self, engine):
        src = "{% if form.password1.value %}typed{% else %}none{% endif %}|{{ form.password1.value|default:'d' }}"
        assert native(engine, src, form=created()) == "none|d"
        assert django_(src, form=created()) == "typed|" + TYPED

    def test_the_refusal_is_reported_once_by_name_and_not_the_value(self, engine, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            for _ in range(3):
                native(engine, "{{ form.password1.value }}", form=created())
        records = [r for r in caplog.records if "password input" in r.getMessage()]
        assert len(records) == 1
        message = records[0].getMessage()
        assert "password1" in message and "value" in message
        assert TYPED not in message

    def test_a_model_floor_field_keeps_the_model_message(self, engine, caplog):
        """``UserCreationForm`` is a ``ModelForm`` over ``User``: ``username`` is no floor field, but
        a ``password`` COLUMN is, and its message is still the #3790 one."""
        from django.contrib.auth.forms import UserChangeForm

        user = User.objects.create_user("floor-user", "a@b.c", "pw")
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            native(engine, "{{ form.password.value }}", form=UserChangeForm(instance=user))
        assert any("#3790" in r.getMessage() for r in caplog.records)


class TestWhatPostingBackDoes:
    """The measurement behind 'nothing is lost': a re-save posts what the person types."""

    def test_the_rendered_control_carries_no_value_so_a_repost_cannot_carry_one(self, engine):
        form = created(password2="does-not-match")
        out = native(engine, "{{ form.password1 }}{{ form.password2 }}", form=form)
        assert 'type="password"' in out
        assert "value=" not in out

    def test_the_view_still_reads_the_real_values_and_saves(self):
        """The floor is the TEMPLATE walk's. The view reads ``cleaned_data`` and saves a hashed
        password; the proxy never replaces anything on the form."""
        form = created()
        assert form.is_valid()
        assert form.cleaned_data["password1"] == TYPED
        user = form.save()
        assert user.check_password(TYPED)

    def test_rendering_does_not_mutate_the_form(self, engine):
        form = created()
        before = (dict(form.data), dict(form.initial), set(form.fields))
        assert form.is_valid()
        cleaned = dict(form.cleaned_data)
        native(
            engine, "{{ form.data }}{{ form.cleaned_data }}{{ form.password1.value }}", form=form
        )
        assert (dict(form.data), dict(form.initial), set(form.fields)) == before
        assert form.cleaned_data == cleaned
        assert form.password1.value() == TYPED if hasattr(form, "password1") else True
        assert form["password1"].value() == TYPED


class TestComponents:
    """djust's own tags draw a password field blank, editable or not, whatever its widget class is."""

    def test_dj_form_draws_a_password_subclass_without_its_value(self, engine, caplog):
        """``Strong`` is a ``PasswordInput`` the component's ``_get_field_type`` does not know by
        name, so it fell to the text branch and printed ``value="..."``."""
        form = SignupForm({"name": "Ada", "pin": TYPED, "token": TYPED + "-t", "note": "n"})
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            out = native(engine, "{% dj_form form %}", form=form)
        assert "Ada" in out
        assert TYPED not in out
        assert not [r for r in caplog.records if "EDITABLE" in r.getMessage()]

    def test_dj_form_draws_a_usercreationform_without_the_typed_password(self, engine):
        out = native(engine, "{% dj_form form %}", form=created(password2="nope"))
        assert TYPED not in out and "nope" not in out
        assert "newbie" in out

    def test_the_state_is_password_for_a_password_field_and_none_for_the_rest(self):
        form = SignupForm({"name": "Ada", "pin": TYPED})
        assert serialization.form_field_floor_state(form["pin"]) == "password"
        assert serialization.form_field_floor_state(form["token"]) == "password"
        assert serialization.form_field_floor_state(form["name"]) is None
        assert serialization.form_field_floor_state(form["note"]) is None

    def test_a_disabled_password_field_is_still_a_password_field(self):
        class Locked(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput, disabled=True, initial=TYPED)

        assert serialization.form_field_floor_state(Locked()["pw"]) == "password"


class _Pair(forms.MultiWidget):
    def __init__(self, attrs=None):
        super().__init__([forms.PasswordInput, forms.PasswordInput], attrs)

    def decompress(self, value):
        return value or [None, None]


class _PairField(forms.MultiValueField):
    widget = _Pair

    def __init__(self, **kwargs):
        super().__init__(fields=(forms.CharField(), forms.CharField()), **kwargs)

    def compress(self, data_list):
        return "|".join(data_list)


class PairForm(forms.Form):
    pair = _PairField(required=False)


class LateForm(forms.Form):
    """Adds its password field in ``__init__``: it is in no ``base_fields``."""

    name = forms.CharField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["late"] = forms.CharField(widget=forms.PasswordInput, required=False)


class TestMultiWidgetAndLateFields:
    """Two shapes the first cut missed (found by probing, then pinned): a password INSIDE a
    ``MultiWidget`` is submitted under one key per part (``pair_0``), and a field added in
    ``__init__`` is in no class-level ``base_fields``, which a formset's own lists were read from."""

    def test_a_multiwidget_with_a_password_part_is_a_password_field(self):
        assert serialization.field_has_password_widget(PairForm().fields["pair"])

    def test_its_value_and_its_per_part_data_keys_are_held(self, engine):
        form = PairForm({"pair_0": TYPED, "pair_1": TYPED + "-2"})
        assert form.is_valid()
        src = "{{ form.pair.value }}|{{ form.data }}|{{ form.cleaned_data }}|{{ form.data.pair_0 }}"
        assert TYPED in django_(src, form=form)
        assert TYPED not in native(engine, src, form=form)

    def test_the_prefixed_per_part_key_is_held(self, engine):
        form = PairForm({"u-pair_0": TYPED, "u-pair_1": TYPED}, prefix="u")
        assert TYPED not in native(engine, "{{ form.data }}{{ form.data.items }}", form=form)

    def test_a_field_added_in_init_is_held_on_the_form(self, engine):
        form = LateForm({"name": "n", "late": TYPED})
        assert form.is_valid()
        src = "{{ form.late.value }}{{ form.cleaned_data.late }}{{ form.data.late }}"
        assert TYPED in django_(src, form=form)
        assert TYPED not in native(engine, src, form=form)

    def test_and_in_the_lists_of_a_formset_of_such_forms(self, engine):
        from django.forms import formset_factory

        fs = formset_factory(LateForm, extra=0)(
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "9",
                "form-0-name": "n",
                "form-0-late": TYPED,
            }
        )
        assert fs.is_valid()
        assert "late" not in fs.form.base_fields
        src = "{{ fs.cleaned_data }}{{ fs.data }}{{ fs.0.late.value }}{{ fs.forms.0.cleaned_data }}"
        assert TYPED in django_(src, fs=fs)
        assert TYPED not in native(engine, src, fs=fs)


class TestTheFormsetHasTheSameFloor:
    def test_a_plain_form_formset_holds_its_password_in_every_list_it_exposes(self, engine):
        from django.forms import formset_factory

        data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-name": "n",
            "form-0-pin": TYPED,
        }
        fs = formset_factory(SignupForm, extra=0)(data)
        assert fs.is_valid(), fs.errors
        src = "{{ fs.data }}{{ fs.cleaned_data }}{{ fs.management_form.data }}{{ fs.0.pin.value }}"
        assert TYPED in django_(src, fs=fs)
        assert TYPED not in native(engine, src, fs=fs)
        out = native(engine, "{{ fs.cleaned_data }}{{ fs.0.name.value }}", fs=fs)
        assert "'n'" in out or "n" in out  # the non-password field of the formset is not blanked
        assert "pin" not in out

    def test_the_initial_list_of_a_formset_is_held(self, engine):
        from django.forms import formset_factory

        fs = formset_factory(SignupForm, extra=0)(initial=[{"name": "n", "pin": TYPED}])
        src = "{{ fs.initial }}{{ fs.0.pin.value }}{{ fs.0.initial }}"
        assert TYPED in django_(src, fs=fs)
        out = native(engine, src, fs=fs)
        assert TYPED not in out
        assert "n" in out

    def test_the_formset_floor_is_none_when_nothing_is_on_it(self):
        from django.forms import formset_factory

        assert serialization._formset_floor(formset_factory(PlainTextPassword)()) is None


class TestOneAuthority:
    """``is_sensitive_form_field`` (``persisted_form_input``) and the template floor ask the same
    question, so they cannot drift (#1646)."""

    def test_the_widget_half_of_is_sensitive_form_field_is_the_floors(self):
        from djust.forms import is_sensitive_form_field

        form = SignupForm()
        for name, field in form.fields.items():
            assert is_sensitive_form_field("zzz-not-a-name", field) == (
                serialization.field_has_password_widget(field)
            ), name

    def test_is_sensitive_form_field_calls_the_one_function(self):
        import djust.forms as djust_forms

        tree = ast.parse(Path(djust_forms.__file__).read_text())
        fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "is_sensitive_form_field"
        )
        called = {
            n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and hasattr(n.func, "id")
        }
        assert "field_has_password_widget" in called
        # and no second statement of the widget test
        assert "PasswordInput" not in ast.unparse(fn.body[-1])

    def test_a_name_in_the_sensitive_list_is_still_sensitive_for_persistence(self):
        from djust.forms import is_sensitive_form_field

        assert is_sensitive_form_field("password", forms.CharField())
        assert not is_sensitive_form_field("note", forms.CharField())
