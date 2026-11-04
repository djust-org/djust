"""The review of #3796: what the first fix left (#3790).

``test_form_initial_floor_3790`` holds the paths a template walks to a model form's values. The review
of that fix measured what it did not reach, and each class here is one of those findings:

* ``TestComponentsDoNotPrintAFloorFieldsValue``: djust's own component tags (``dj_form``,
  ``combobox``, ``rich_select``) read ``bound_field.value()`` in Python and drew the hash of a
  ``UserChangeForm`` into a text input. A DISABLED floor field is now drawn without its value; an
  EDITABLE one keeps it, for the reason ``TestWhatPostingBackDoes`` measures, and a component says so
  once in the log.
* ``TestWhatPostingBackDoes``: the facts that choice rests on. A control rendered blank or left out of
  the page and posted back clears a text column and demotes a user whose ``is_staff`` checkbox it was.
* ``TestPrefixedAndQueryDictData``: ``form.data`` is keyed ``u-password``, not ``password``.
* ``TestFormsets``: ``formset.data`` / ``cleaned_data`` / ``initial`` hold the same values the forms do.
* ``TestWhatWidgetsDo``: the widget the form author chose still renders its value. Pinned, so the
  limit is a fact a test states and not a sentence in a doc.
* ``TestTheRefusalIsLoud``: a template that read a floor field's value back is told once, by name.
* ``TestFailClosed`` / ``TestStaleExtension``: a floor that raises drops the value; an extension that
  predates the floor says so at startup.

Refs #3790, #3796, #1468.
"""

from __future__ import annotations

import logging
import re

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.forms import inlineformset_factory, modelformset_factory
from django.http import QueryDict
from django.test import override_settings

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
JOINED = "2020-01-01 00:00:00"


@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "followups-3790", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


@pytest.fixture
def user():
    return User.objects.create_user(
        "floor-user", "secret-3790@example.com", "pw-3790", first_name="Ada", is_staff=True
    )


@pytest.fixture(autouse=True)
def _fresh_warning_memory(monkeypatch):
    monkeypatch.setattr(serialization, "_REFUSED_READ_WARNED", set())
    monkeypatch.setattr(serialization, "_EDITABLE_FLOOR_WARNED", set())


def render(engine, src: str, **context) -> str:
    out = engine.from_string("{% load djust_components %}" + src).render(context)
    return re.sub(r"\s+", " ", str(out))


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name"]


class DisabledEmailForm(ProfileForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].disabled = True


class HiddenPasswordForm(UserChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget = forms.HiddenInput()


class PasswordTextForm(forms.ModelForm):
    """The view author put the password column in the form as a plain text box."""

    class Meta:
        model = User
        fields = ["username", "password"]


def _change_data(user, **extra) -> dict:
    data = {"username": user.username, "date_joined": JOINED}
    data.update(extra)
    return data


# ---------------------------------------------------------------------------------------------
# HIGH: djust's own components.


class TestComponentsDoNotPrintAFloorFieldsValue:
    @pytest.mark.parametrize(
        "src",
        [
            "{% dj_form form %}",
            "{% combobox field=form.password %}",
            "{% rich_select field=form.password %}",
            "{% with f=form %}{% dj_form f %}{% endwith %}",
            "{% with f=form.password %}{% combobox field=f %}{% endwith %}",
            "{% for x in form %}{% if x.name == 'password' %}{% rich_select field=x %}{% endif %}{% endfor %}",
        ],
    )
    def test_a_disabled_floor_field_is_drawn_without_its_value(self, engine, user, src):
        out = render(engine, src, form=UserChangeForm(instance=user))
        assert user.password not in out, out
        assert "pbkdf2_sha256" not in out, out
        assert "password" in out, "the control is still drawn"

    def test_the_other_fields_of_the_same_form_keep_their_values(self, engine, user):
        out = render(engine, "{% dj_form form %}", form=UserChangeForm(instance=user))
        assert 'value="floor-user"' in out
        assert "secret-3790@example.com" in out

    def test_a_hidden_disabled_floor_field_is_drawn_without_its_value(self, engine, user):
        out = render(engine, "{% dj_form form %}", form=HiddenPasswordForm(instance=user))
        assert user.password not in out, out
        assert 'name="password"' in out and 'value=""' in out

    def test_a_disabled_sensitive_field_is_stripped_the_same_way(self, engine, user):
        with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
            out = render(
                engine,
                "{% dj_form form %}{% combobox field=form.email %}",
                form=DisabledEmailForm(instance=user),
            )
        assert "secret-3790@example.com" not in out, out

    def test_an_editable_floor_field_keeps_its_value_and_the_component_says_so(
        self, engine, user, caplog
    ):
        """Stripping it would clear it on the next save (TestWhatPostingBackDoes). The component
        reports that it rendered one, naming the field, so the choice is never silent."""
        with (
            override_settings(DJUST_SENSITIVE_FIELDS=["email"]),
            caplog.at_level(logging.WARNING, logger="djust.serialization"),
        ):
            out = render(engine, "{% dj_form form %}", form=ProfileForm(instance=user))
        assert "secret-3790@example.com" in out
        messages = [r.getMessage() for r in caplog.records]
        assert any("User.email" in m and "EDITABLE" in m for m in messages), messages

    def test_an_editable_floor_field_is_reported_once_not_per_render(self, engine, user, caplog):
        with (
            override_settings(DJUST_SENSITIVE_FIELDS=["email"]),
            caplog.at_level(logging.WARNING, logger="djust.serialization"),
        ):
            for _ in range(3):
                render(engine, "{% dj_form form %}", form=ProfileForm(instance=user))
        assert len([r for r in caplog.records if "User.email" in r.getMessage()]) == 1

    def test_a_form_with_no_floor_field_is_unchanged_and_silent(self, engine, user, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            out = render(engine, "{% dj_form form %}", form=ProfileForm(instance=user))
        assert "floor-user" in out and "secret-3790@example.com" in out
        assert not caplog.records

    def test_a_plain_form_is_unchanged(self, engine):
        class Plain(forms.Form):
            password = forms.CharField(required=False)

        out = render(engine, "{% dj_form form %}", form=Plain(initial={"password": "typed-value"}))
        assert "typed-value" in out

    def test_every_reader_goes_through_one_function(self):
        """`_render_field`, the hidden-field loop and `_from_bound_field` call `_bound_field_value`;
        a fourth reader that calls `.value()` itself would be the next review finding."""
        import ast
        from pathlib import Path

        import djust.components.templatetags as templatetags

        direct = []
        for path in Path(templatetags.__file__).parent.glob("*.py"):
            tree = ast.parse(path.read_text())
            owner = {}
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for node in ast.walk(fn):
                        owner[id(node)] = fn.name
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "value"
                    and not node.args
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in ("bf", "bound_field", "field")
                ):
                    direct.append((path.name, owner.get(id(node))))
        # The single permitted direct call is the one inside `_bound_field_value` itself.
        assert direct == [("_forms.py", "_bound_field_value")], direct


# ---------------------------------------------------------------------------------------------
# The facts the editable/disabled choice rests on.


class TestWhatPostingBackDoes:
    """What a browser posts when a control is drawn blank (the field is posted empty) or left out of
    the page / drawn ``disabled`` (the field is not posted at all), saved through the same form."""

    @staticmethod
    def _save(form_class, user, data):
        form = form_class(data, instance=user)
        valid = form.is_valid()
        if valid:
            form.save()
        user.refresh_from_db()
        return valid

    @pytest.mark.parametrize("posted", ["blank", "omitted"])
    def test_a_disabled_field_cannot_be_changed_by_either(self, user, posted):
        before = user.password
        data = _change_data(user, is_staff="on")
        if posted == "blank":
            data["password"] = ""
        assert self._save(UserChangeForm, user, data)
        assert user.password == before

    @pytest.mark.parametrize("posted", ["blank", "omitted"])
    def test_an_editable_text_field_is_cleared_by_either(self, user, posted):
        data = {"username": user.username, "first_name": "Ada"}
        if posted == "blank":
            data["email"] = ""
        assert self._save(ProfileForm, user, data)
        assert user.email == ""

    @pytest.mark.parametrize("posted", ["blank", "omitted"])
    def test_an_unchecked_checkbox_demotes_the_user(self, user, posted):
        assert user.is_staff
        data = _change_data(user)
        if posted == "blank":
            data["is_staff"] = ""
        assert self._save(UserChangeForm, user, data)
        assert user.is_staff is False

    @pytest.mark.parametrize("posted", ["blank", "omitted"])
    def test_a_required_field_is_rejected_by_either(self, user, posted):
        """Nothing is saved, so nothing is clobbered, but the page cannot be saved: the user is shown
        an error for a control they were never given a value in."""

        class JoinedForm(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "date_joined"]

        joined = user.date_joined
        data = {"username": user.username}
        if posted == "blank":
            data["date_joined"] = ""
        assert not self._save(JoinedForm, user, data)
        assert user.date_joined == joined


# ---------------------------------------------------------------------------------------------
# MEDIUM: prefixed data and QueryDict.


class TestPrefixedAndQueryDictData:
    SPELLINGS = [
        "{{ form.data }}",
        "{{ form.data.items }}",
        "{% for k, v in form.data.items %}{{ k }}={{ v }};{% endfor %}",
        "{{ form.data|pprint }}",
        "{{ form.data|json_script:'d' }}",
        "{{ form.data.values }}",
        "{{ form.data.keys }}",
    ]

    @pytest.mark.parametrize("src", SPELLINGS)
    def test_a_prefixed_forms_data_prints_no_floor_value(self, engine, user, src):
        form = UserChangeForm(
            {"u-username": "floor-user", "u-password": user.password, "u-date_joined": JOINED},
            instance=user,
            prefix="u",
        )
        out = render(engine, src, form=form)
        assert user.password not in out, out
        assert "u-password" not in out, out
        assert "u-username" in out or "floor-user" in out or "keys" in src, out

    @pytest.mark.parametrize("src", SPELLINGS)
    def test_the_control_prints_it(self, engine, user, src):
        """Django prints the hash for every one, so the comparison is not two empty strings."""
        from django.template import Context, Engine

        form = UserChangeForm(
            {"u-username": "x", "u-password": user.password}, instance=user, prefix="u"
        )
        out = Engine().from_string(src).render(Context({"form": form}))
        assert user.password in out or "u-password" in out

    def test_a_querydict_keeps_its_type_as_the_template_sees_it(self, engine, user):
        data = QueryDict("", mutable=True)
        data.setlist("u-username", ["floor-user"])
        data.setlist("u-password", [user.password])
        data.setlist("u-extra", ["a", "b"])
        data._mutable = False
        form = UserChangeForm(data, instance=user, prefix="u")
        out = render(
            engine,
            "{% for k, v in form.data.lists %}{{ k }}:{{ v|join:'+' }};{% endfor %}"
            "|{{ form.data.urlencode }}|{{ form.data|length }}",
            form=form,
        )
        assert user.password not in out and "u-password" not in out, out
        # `lists` still yields every value of a repeated key, `urlencode` is the QueryDict's own,
        # and the refused key is the only entry gone.
        assert "u-extra:a+b;" in out
        assert "u-extra=a&amp;u-extra=b" in out or "u-extra=a&u-extra=b" in out
        assert out.rstrip().endswith("|2"), out

    def test_a_dict_subclass_that_is_not_a_querydict_is_filtered_as_a_dict(self, engine, user):
        form = UserChangeForm(
            {"u-username": "x", "u-password": user.password}, instance=user, prefix="u"
        )
        assert "u-password" not in render(engine, "{{ form.data }}", form=form)

    def test_an_unprefixed_key_still_passes_through_a_prefixed_filter(self, engine, user):
        form = UserChangeForm({"username": "plain", "u-username": "pre"}, instance=user, prefix="u")
        out = render(
            engine, "{% for k, v in form.data.items %}{{ k }}={{ v }};{% endfor %}", form=form
        )
        assert "username=plain;" in out and "u-username=pre;" in out


# ---------------------------------------------------------------------------------------------
# MEDIUM: formsets.


def _formset_data(user, prefix: str = "form", **fields) -> dict:
    data = {
        f"{prefix}-TOTAL_FORMS": "1",
        f"{prefix}-INITIAL_FORMS": "1",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
        f"{prefix}-0-id": str(user.pk),
        f"{prefix}-0-username": user.username,
        f"{prefix}-0-password": user.password,
        f"{prefix}-0-date_joined": JOINED,
    }
    data.update(fields)
    return data


class TestFormsets:
    SPELLINGS = [
        "{{ fs.cleaned_data }}",
        "{{ fs.data }}",
        "{{ fs.data|pprint }}",
        "{{ fs.initial }}",
        "{% for d in fs.cleaned_data %}{{ d.password }}{% endfor %}",
        "{{ fs.forms.0.initial.password }}",
        "{% for f in fs %}{{ f.cleaned_data.password }}{{ f.password.value }}{% endfor %}",
        "{{ fs.initial_forms.0.cleaned_data.password }}",
        # The management form is a plain Form that holds the formset's WHOLE submitted data.
        "{{ fs.management_form.data }}",
        "{{ fs.management_form.data.items }}",
        "{{ fs.management_form.data|pprint }}",
        "{% for k, v in fs.management_form.data.items %}{{ k }}={{ v }};{% endfor %}",
        "{{ fs.empty_form.initial.password }}{{ fs.empty_form.data }}",
    ]

    def _bound(self, user):
        FormSet = modelformset_factory(User, form=UserChangeForm, extra=0)
        fs = FormSet(_formset_data(user), queryset=User.objects.filter(pk=user.pk))
        assert fs.is_valid(), fs.errors
        return fs

    @pytest.mark.parametrize("src", SPELLINGS)
    def test_a_bound_model_formset_prints_no_floor_value(self, engine, user, src):
        out = render(engine, src, fs=self._bound(user))
        assert user.password not in out, out
        assert "form-0-password" not in out, out

    @pytest.mark.parametrize("src", SPELLINGS[:3])
    def test_the_control_prints_it(self, user, src):
        from django.template import Context, Engine

        out = Engine().from_string(src).render(Context({"fs": self._bound(user)}))
        assert user.password in out.replace("&#x27;", "'"), out

    def test_a_plain_formset_of_a_model_form_filters_its_initial_list(self, engine, user):
        from django.forms import formset_factory, model_to_dict

        FormSet = formset_factory(UserChangeForm, extra=0)
        fs = FormSet(initial=[model_to_dict(user)])
        out = render(engine, "{{ fs.initial }}", fs=fs)
        assert "floor-user" in out, "the other keys of the initial dict are still there"
        assert user.password not in out and "&#x27;password&#x27;" not in out, out

    def test_the_benign_reads_are_djangos(self, engine, user):
        from django.template import Context, Engine

        fs = self._bound(user)
        for src in (
            "{{ fs.total_form_count }}",
            "{{ fs.management_form.TOTAL_FORMS.value }}",
            "{{ fs.is_bound }}",
            "{{ fs.forms|length }}",
            "{% for f in fs %}{{ f.username.value }}{% endfor %}",
            "{{ fs.data.TOTAL_FORMS }}{{ fs.data|length }}",
        ):
            assert render(engine, src, fs=fs) == re.sub(
                r"\s+", " ", Engine().from_string(src).render(Context({"fs": fs}))
            ).replace("\n", " ") or src.endswith("|length }}"), src

    def test_a_formset_is_iterated_and_indexed_like_one(self, engine, user):
        fs = self._bound(user)
        assert (
            render(engine, "{{ fs|length }}[{{ fs.0.username.value }}]", fs=fs) == "1[floor-user]"
        )

    def test_an_inline_formset_holds_its_models_floor(self, engine, user):
        from django.contrib.admin.models import LogEntry

        FormSet = inlineformset_factory(User, LogEntry, fields=["object_repr"], extra=0)
        data = {
            "logentry_set-TOTAL_FORMS": "1",
            "logentry_set-INITIAL_FORMS": "0",
            "logentry_set-MIN_NUM_FORMS": "0",
            "logentry_set-MAX_NUM_FORMS": "1000",
            "logentry_set-0-object_repr": "secret-repr-3790",
        }
        fs = FormSet(data, instance=user)
        with override_settings(DJUST_SENSITIVE_FIELDS=["object_repr"]):
            out = render(
                engine, "{{ fs.data }}|{{ fs.instance.password }}|{{ fs.instance.username }}", fs=fs
            )
        assert "secret-repr-3790" not in out and "object_repr" not in out, out
        assert user.password not in out
        assert out.endswith("||floor-user"), out
        assert "TOTAL_FORMS" in out

    def test_a_plain_formset_over_a_plain_form_has_no_model_floor(self, engine):
        class Plain(forms.Form):
            password = forms.CharField(required=False)

        FormSet = forms.formset_factory(Plain, extra=0)
        fs = FormSet(
            {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-password": "typed"}
        )
        assert "typed" in render(engine, "{{ fs.data }}", fs=fs)

    def test_the_management_form_still_carries_its_counts(self, engine, user):
        fs = self._bound(user)
        out = render(
            engine,
            "{{ fs.management_form.TOTAL_FORMS.value }}|{{ fs.management_form.data|length }}"
            "|{{ fs.management_form.cleaned_data.TOTAL_FORMS }}",
            fs=fs,
        )
        # `length` counts what the form was given: the filtered copy has one key fewer (the password).
        assert out == "1|%d|1" % (len(_formset_data(user)) - 1)


class TestAFloorFieldIsTheSameFieldEachTimeItIsReached:
    """The engine decides what ``==`` means from the object it converts (a ``BoundField`` has the
    default ``__eq__`` and ``__repr__``, so it compares by identity). A fresh proxy per walk made
    ``{% if form.is_staff == form.is_staff %}`` false; it is true on main."""

    @pytest.mark.parametrize(
        "src,expected",
        [
            ("{% if form.is_staff == form.is_staff %}T{% else %}F{% endif %}", "T"),
            ("{% if form.is_staff != form.is_staff %}T{% else %}F{% endif %}", "F"),
            ("{% if form.is_staff == form.username %}T{% else %}F{% endif %}", "F"),
            ("{% if form.is_staff != form.username %}T{% else %}F{% endif %}", "T"),
            (
                "{% with f=form.is_staff %}{% if f == form.is_staff %}T{% else %}F{% endif %}{% endwith %}",
                "T",
            ),
            ("{% if form.username == form.username %}T{% else %}F{% endif %}", "T"),
            ("{% if form.username in form %}T{% else %}F{% endif %}", "T"),
        ],
    )
    def test_it_compares_as_it_does_on_the_django_engine(self, engine, user, src, expected):
        from django.template import Context, Engine

        form = UserChangeForm(instance=user)
        assert render(engine, src, form=form) == expected
        control = Engine().from_string(src).render(Context({"form": form}))
        assert control == expected, "the expectation is Django's own answer"

    def test_a_floor_field_is_not_found_by_in_a_known_limit(self, engine, user):
        """Django says True. The left operand is a floor proxy and ``in`` compares it with the real
        fields the form's handle yields (see the comment in ``serialization``; #3802)."""
        form = UserChangeForm(instance=user)
        src = "{% if form.is_staff in form %}T{% else %}F{% endif %}"
        assert render(engine, src, form=form) == "F"

    def test_one_proxy_per_bound_field_and_floor(self, user):
        from djust.serialization import _protect_sidecar_walk

        form = UserChangeForm(instance=user)
        first = _protect_sidecar_walk(form["is_staff"])
        assert first is not form["is_staff"]
        assert _protect_sidecar_walk(form["is_staff"]) is first
        assert _protect_sidecar_walk(form["password"]) is not first
        with override_settings(DJUST_SENSITIVE_FIELDS=["username"]):
            assert _protect_sidecar_walk(form["is_staff"]) is not first, (
                "a changed floor is a new proxy"
            )

    def test_the_proxy_keeps_the_real_fields_class_of_equality(self, user):
        from djust.serialization import _SidecarBoundFieldProxy

        assert _SidecarBoundFieldProxy.__eq__ is object.__eq__
        assert _SidecarBoundFieldProxy.__repr__ is object.__repr__


# ---------------------------------------------------------------------------------------------
# MEDIUM: the widget the form author chose.


class TestWhatWidgetsDo:
    """The limit, as facts. The floor guards what a template can READ BACK; a widget is the form
    rendering itself, drawn by the view author's own choice of widget, so it is not blanked (blanking
    an editable control clears it on save: ``TestWhatPostingBackDoes``)."""

    def test_the_read_only_password_widget_shows_a_summary_never_the_hash(self, engine, user):
        form = UserChangeForm(instance=user)
        for src in (
            "{{ form.password }}",
            "{{ form.as_p }}",
            "{{ form }}",
            "{% for x in form %}{{ x }}{% endfor %}",
        ):
            out = render(engine, src, form=form)
            assert user.password not in out, src
            assert "algorithm" in out, src

    def test_a_text_input_over_the_password_column_renders_its_value(self, engine, user):
        form = PasswordTextForm(instance=user)
        for src in (
            "{{ form.password }}",
            "{{ form.as_p }}",
            "{{ form }}",
            "{% for x in form %}{{ x }}{% endfor %}",
        ):
            assert user.password in render(engine, src, form=form), src

    def test_a_configured_sensitive_field_renders_its_value_in_its_widget(self, engine, user):
        with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
            out = render(
                engine, "{{ form.email }}|{{ form.as_p }}", form=ProfileForm(instance=user)
            )
        assert out.count("secret-3790@example.com") == 2

    def test_the_value_readers_of_the_same_forms_stay_refused(self, engine, user):
        form = PasswordTextForm(instance=user)
        out = render(
            engine,
            "{{ form.password.value }}{{ form.initial.password }}{{ form.password.as_text }}",
            form=form,
        )
        assert out == ""


# ---------------------------------------------------------------------------------------------
# MEDIUM: the breaking change.


class TestTheRefusalIsLoud:
    def test_reading_a_floor_fields_value_names_the_field_once(self, engine, user, caplog):
        form = UserChangeForm(instance=user)
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            for _ in range(3):
                render(engine, "{% if form.is_staff.value %}checked{% endif %}", form=form)
        messages = [r.getMessage() for r in caplog.records if "is_staff" in r.getMessage()]
        assert len(messages) == 1, messages
        assert "User.is_staff" in messages[0] and ".value" in messages[0]
        assert "get_context_data" in messages[0], "the migration is in the message"

    def test_each_refused_reader_is_reported(self, engine, user, caplog):
        form = UserChangeForm(instance=user)
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            render(
                engine,
                "{{ form.password.value }}{{ form.password.initial }}{{ form.password.data }}",
                form=form,
            )
        attrs = sorted(re.search(r"via \.(\w+)", r.getMessage()).group(1) for r in caplog.records)
        assert attrs == ["data", "initial", "value"]

    def test_the_rendered_checkbox_is_the_documented_break(self, engine, user):
        """`{% if form.is_staff.value %}checked{% endif %}` is empty now: the migration is to read the
        value in the view and pass it, or to render `{{ form.is_staff }}`."""
        form = UserChangeForm(instance=user)
        assert render(engine, "{% if form.is_staff.value %}checked{% endif %}", form=form) == ""
        assert "checked" in render(engine, "{{ form.is_staff }}", form=form)


# ---------------------------------------------------------------------------------------------
# LOW: fail closed, stale extension.


class TestFailClosed:
    @pytest.mark.parametrize(
        "src",
        [
            "{{ form.initial.password }}",
            "{{ form.password.value }}",
            "{{ form.cleaned_data.password }}",
            # An alias is resolved back to its path by the LEGACY sidecar walk (`Context::protect_sidecar`),
            # the one that used to answer `Err(_) => obj`: the first three never reach it.
            "{% with d=form.initial %}{{ d.password }}{% endwith %}",
            "{% with d=form.cleaned_data %}{{ d.password }}{% endwith %}",
        ],
    )
    def test_a_floor_that_raises_drops_the_value_it_does_not_hand_the_raw_form_on(
        self, engine, user, monkeypatch, src
    ):
        def boom(value):
            raise RuntimeError("the floor failed")

        monkeypatch.setattr(serialization, "_protect_sidecar_walk", boom)
        form = UserChangeForm(
            {"username": "floor-user", "password": user.password, "date_joined": JOINED},
            instance=user,
        )
        form.is_valid()
        out = render(engine, src, form=form)
        assert user.password not in out, out

    def test_the_control_without_the_failure_is_the_issue(self, engine, user, monkeypatch):
        """The leaf floor alone (a floor that is not asked) prints it: the test above is not vacuous."""
        monkeypatch.setattr(
            serialization, "_protect_sidecar_walk", serialization._protect_sidecar_value
        )
        form = UserChangeForm(instance=user)
        assert user.password in render(engine, "{{ form.initial.password }}", form=form)


class TestStaleExtension:
    def test_the_built_extension_carries_the_marker(self):
        from djust import _rust
        from djust.apps import _check_form_floor_extension

        assert _rust.WALK_FLOOR_COVERS_FORMS is True
        assert _check_form_floor_extension() is True

    def test_an_extension_without_it_is_reported_loudly(self, monkeypatch, caplog):
        from djust import _rust
        from djust.apps import _check_form_floor_extension

        monkeypatch.delattr(_rust, "WALK_FLOOR_COVERS_FORMS")
        with caplog.at_level(logging.ERROR, logger="djust"):
            assert _check_form_floor_extension() is False
        assert any(
            "predates the model-form serialization floor" in r.getMessage() for r in caplog.records
        )

    def test_ready_runs_the_check(self):
        """A call to it inside `DjustConfig.ready` itself (not merely the name somewhere in the file)."""
        import ast
        from pathlib import Path

        import djust.apps as apps

        tree = ast.parse(Path(apps.__file__).read_text())
        ready = next(
            node
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef)
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "ready"
        )
        calls = [
            n.func.id
            for n in ast.walk(ready)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "_check_form_floor_extension" in calls, calls


class TestATagIsHandedWhatIsBehindEveryProxy:
    def test_unwrap_returns_the_real_object_for_each_proxy(self, user):
        from djust.serialization import _protect_sidecar_walk, unwrap_form_proxy

        form = UserChangeForm(instance=user)
        FormSet = modelformset_factory(User, form=UserChangeForm, extra=0)
        fs = FormSet(queryset=User.objects.filter(pk=user.pk))
        for real in (form, form["password"], fs):
            proxy = _protect_sidecar_walk(real)
            assert proxy is not real
            assert unwrap_form_proxy(proxy) is real
        assert unwrap_form_proxy(form["username"]) is form["username"]

    def test_the_bridge_releases_the_real_object_at_the_end_of_a_walk(self, user):
        from djust.template_bridge_floor import release, wrap

        FormSet = modelformset_factory(User, form=UserChangeForm, extra=0)
        fs = FormSet(queryset=User.objects.filter(pk=user.pk))
        form = UserChangeForm(instance=user)
        assert release(wrap(fs)) is fs
        assert release(wrap(form)) is form
