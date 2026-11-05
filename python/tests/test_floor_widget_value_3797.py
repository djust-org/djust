"""A floor field NEVER renders its stored value into HTML, whatever widget the author chose (#3797).

#3796/#3798 refuse every way a TEMPLATE can READ a model form's floor-field value back
(``form.initial.password``, ``form.password.value``, ``cleaned_data``, ``data``, ``subwidgets``). They
did not touch what the form RENDERS: ``{{ form.password }}``, ``{{ form.as_p }}``, ``{{ form }}`` and
``{% for x in form %}{{ x }}{% endfor %}`` printed ``value="..."`` for a floor field whose widget was a
plain ``TextInput`` (and the body of a ``Textarea``, the ``selected`` option of a ``Select``, the
``checked`` of a checkbox, the parts of a ``MultiWidget``). The maintainer's decision: the value is
never rendered; a floor field's control is drawn the way Django draws a field that has none.

The ORACLE is Django itself, twice:

* a floor field draws EXACTLY what Django draws for the SAME form with the floor columns empty (the
  "twin"): not "the secret is absent" alone, which a control that vanished would also satisfy;
* a field that is not on the floor draws byte-for-byte what Django draws for the real form.

The routes (``ROUTES``) are the cross product of how a field is PRODUCED and how it is RENDERED; the
shapes (``Shapes``) are the widgets. Every secret is also an injection payload, so a value that
escaped through an attribute or a body would show up whether or not it was HTML-escaped.

Refs #3797, #3790, #3796, #3798, #1646, #1468.
"""

from __future__ import annotations

import logging
import re

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.forms import modelformset_factory
from django.template import Context, Engine
from django.test import override_settings

from djust import serialization

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()

#: Every secret contains ``SECRET-3797`` so one substring finds it raw, HTML-escaped or inside an
#: attribute, and carries a quote and a tag so that a leak through an attribute or a body is visible.
HASH = '"><i>SECRET-3797-hash</i>'
EMAIL = "SECRET-3797-email@example.com"
FIRST = "<b>SECRET-3797-first</b>"
#: a CHOICE of the Select: its option value is the author's own; what must not happen is the stored
#: value being marked ``selected`` (the comparison with the twin catches that)
LAST = "choice-3797-last"
TOKEN = "SECRET-3797-token'onfocus='x()"
SHH = "SECRET-3797-hidden"
MARKER = "SECRET-3797"

FLOOR_SETTING = ["email", "first_name", "last_name", "api_token", "secret", "date_joined"]
CHOICES = [("", "---"), (LAST, "The last"), ("other", "Other")]


class SplitWhen(forms.SplitDateTimeField):
    pass


class Shapes(forms.ModelForm):
    """Every widget shape on one form; each of the six is a floor field (name or model floor).

    ``password`` TextInput (the model column); ``email`` EmailInput (``DJUST_SENSITIVE_FIELDS``);
    ``first_name`` Textarea; ``last_name`` Select; ``is_staff`` CheckboxInput (model floor);
    ``date_joined`` ``SplitDateTimeWidget`` (a ``MultiWidget``); ``api_token`` a declared field that is
    no column of the model (named in the setting); ``secret`` a ``HiddenInput``; ``username`` and
    ``note`` are NOT on the floor.
    """

    first_name = forms.CharField(widget=forms.Textarea, required=False)
    last_name = forms.ChoiceField(choices=CHOICES, required=False)
    date_joined = SplitWhen(required=False)
    api_token = forms.CharField(required=False)
    secret = forms.CharField(widget=forms.HiddenInput, required=False)
    note = forms.CharField(required=False)

    class Meta:
        model = User
        fields = ["username", "password", "email", "first_name", "last_name", "is_staff"]


class RadioShapes(forms.ModelForm):
    """The choice widgets that draw one control per option."""

    last_name = forms.ChoiceField(choices=CHOICES, widget=forms.RadioSelect, required=False)
    first_name = forms.MultipleChoiceField(
        choices=[("choice-3797-first", "f"), ("z", "z")],
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model = User
        fields = ["username"]


class LeakyWidget(forms.TextInput):
    """A third-party widget that prints the value in two attributes and in the body."""

    def render(self, name, value, attrs=None, renderer=None):
        from django.utils.html import format_html

        return format_html('<i data-copy="{}" title="{}">{}</i>', value or "", name, value or "")


class WithLeaky(forms.ModelForm):
    first_name = forms.CharField(widget=LeakyWidget, required=False)

    class Meta:
        model = User
        fields = ["username"]


class HashedUser(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        fields = ["username", "password", "email"]


@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "widget-3797", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


@pytest.fixture(autouse=True)
def _floor(monkeypatch):
    monkeypatch.setattr(serialization, "_REFUSED_READ_WARNED", set())
    monkeypatch.setattr(serialization, "_EDITABLE_FLOOR_WARNED", set())
    with override_settings(DJUST_SENSITIVE_FIELDS=FLOOR_SETTING):
        yield


def native(engine, src: str, **context) -> str:
    out = engine.from_string("{% load djust_components %}" + src).render(context)
    return re.sub(r"\s+", " ", str(out))


def django_(src: str, **context) -> str:
    return re.sub(r"\s+", " ", Engine().from_string(src).render(Context(context)))


def row(secret: bool = True):
    """A ``User`` instance: with its secrets, or the same row with every floor column empty."""
    return User(
        username="bob",
        password=HASH if secret else "",
        email=EMAIL if secret else "",
        first_name=FIRST if secret else "",
        last_name=LAST if secret else "",
        is_staff=secret,
        date_joined=None,
    )


def shapes(secret: bool = True, **kwargs):
    initial = {"api_token": TOKEN, "secret": SHH, "date_joined": None} if secret else {}
    if secret:
        from datetime import datetime

        initial["date_joined"] = datetime(2020, 1, 2, 3, 4, 5)
    return Shapes(instance=row(secret), initial=initial, **kwargs)


FLOOR_FIELDS = ["password", "email", "first_name", "last_name", "is_staff", "api_token", "secret"]
SPLIT = "date_joined"


def _fields_src(template: str, names) -> str:
    return "|".join(template.replace("FIELD", n) for n in names)


# ---------------------------------------------------------------------------------------------
# the reproducer: before the change every one of these printed the stored value.

#: how a single field is produced and rendered. ``FIELD`` is the field name.
FIELD_ROUTES = [
    "{{ form.FIELD }}",
    "{{ form.FIELD|safe }}",
    "{{ form.FIELD.as_widget }}",
    "{{ form.FIELD.as_field_group }}",
    "{{ form.FIELD.render }}",
    "{% with f=form.FIELD %}{{ f }}{% endwith %}",
    "{% for f in form %}{% if f.name == 'FIELD' %}{{ f }}{% endif %}{% endfor %}",
    "{% for f in form.visible_fields %}{% if f.name == 'FIELD' %}{{ f }}{% endif %}{% endfor %}",
    "{% for f in form.hidden_fields %}{% if f.name == 'FIELD' %}{{ f }}{% endif %}{% endfor %}",
    "{% for f in form %}{% if f.name == 'FIELD' %}{{ f.as_widget }}{% endif %}{% endfor %}",
    "{% for f in form %}{% if f.name == 'FIELD' %}{{ f.as_field_group }}{% endif %}{% endfor %}",
    "{{ form.FIELD.get_context.field }}",
]

#: whole-form routes: the form's own renderers.
FORM_ROUTES = [
    "{{ form }}",
    "{{ form.as_p }}",
    "{{ form.as_table }}",
    "{{ form.as_ul }}",
    "{{ form.as_div }}",
    "{{ form.render }}",
    "{% for f in form %}{{ f.label_tag }}{{ f }}{{ f.errors }}{% endfor %}",
    "{% for f in form.visible_fields %}{{ f }}{% endfor %}{% for f in form.hidden_fields %}{{ f }}{% endfor %}",
]


class TestTheRoutes:
    @pytest.mark.parametrize("src", FIELD_ROUTES)
    @pytest.mark.parametrize("name", FLOOR_FIELDS + [SPLIT])
    def test_a_field_draws_as_django_draws_the_same_field_with_no_value(self, engine, name, src):
        route = src.replace("FIELD", name)
        out = native(engine, route, form=shapes(True))
        twin = django_(route, form=shapes(False))
        assert MARKER not in out, out
        assert "x()" not in out, out
        assert out == twin, (out, twin)

    @pytest.mark.parametrize("src", FORM_ROUTES)
    def test_the_form_draws_as_django_draws_the_twin(self, engine, src):
        out = native(engine, src, form=shapes(True))
        twin = django_(src, form=shapes(False))
        assert MARKER not in out, out
        assert out == twin, (out, twin)

    @pytest.mark.parametrize("src", FIELD_ROUTES + FORM_ROUTES)
    def test_not_vacuous_django_prints_the_secret_for_the_same_route(self, src):
        """Without this a route that renders nothing on BOTH engines would pass as protection."""
        route = src.replace("FIELD", "secret" if "hidden_fields" in src else "password")
        assert MARKER in django_(route, form=shapes(True)), route

    def test_the_fields_that_are_not_on_the_floor_draw_exactly_as_django(self, engine):
        for src in ("{{ form.username }}", "{{ form.note }}", "{{ form.username.label_tag }}"):
            form = shapes(True)
            assert native(engine, src, form=form) == django_(src, form=form)

    def test_the_whole_form_keeps_the_values_of_the_fields_that_are_not_on_the_floor(self, engine):
        out = native(engine, "{{ form.as_p }}", form=shapes(True))
        assert 'name="username" value="bob"' in out

    def test_the_view_still_reads_the_real_value_and_the_form_is_not_changed(self, engine):
        form = shapes(True)
        native(engine, "{{ form }}{{ form.as_p }}{{ form.password }}", form=form)
        assert form["password"].value() == HASH
        assert form["is_staff"].value() is True
        assert "SECRET-3797-hash" in str(form["password"]), (
            "the real BoundField still draws its value"
        )
        assert form.initial["password"] == HASH


class TestRefusedStaysRefused:
    """What was refused before is still refused: the change is the rendering, not the reads."""

    @pytest.mark.parametrize(
        "src",
        [
            "{{ form.password.value }}",
            "{{ form.password.initial }}",
            "{{ form.password.data }}",
            "{{ form.initial.password }}",
            "{{ form.api_token.value }}",
            "{{ form.initial.api_token }}",
            "{% for w in form.password %}{{ w }}{% endfor %}",
            "{{ form.password.subwidgets }}",
            "{{ form.password.as_text }}{{ form.password.as_textarea }}{{ form.password.as_hidden }}",
        ],
    )
    def test_reads_and_value_printing_renderers(self, engine, src):
        assert MARKER not in native(engine, src, form=shapes(True))


class TestBound:
    """A typed-but-invalid form re-renders with what was posted: the POSTED value is a floor value too."""

    DATA = {
        "username": "bob",
        "password": HASH,
        "email": EMAIL,
        "first_name": FIRST,
        "last_name": LAST,
        "is_staff": "on",
        "api_token": TOKEN,
        "secret": SHH,
        "date_joined_0": "2020-01-02",
        "date_joined_1": "03:04:05",
        "note": "n",
    }

    def bound(self, **extra):
        data = dict(self.DATA, **extra)
        return Shapes(data, instance=row(True), initial={"api_token": TOKEN})

    @pytest.mark.parametrize("src", FORM_ROUTES)
    def test_the_form_routes(self, engine, src):
        out = native(engine, src, form=self.bound())
        assert MARKER not in out, out
        assert "x()" not in out

    @pytest.mark.parametrize("src", FIELD_ROUTES)
    @pytest.mark.parametrize("name", FLOOR_FIELDS + [SPLIT])
    def test_the_field_routes(self, engine, name, src):
        out = native(engine, src.replace("FIELD", name), form=self.bound())
        assert MARKER not in out, out

    def test_an_invalid_form_keeps_its_errors_and_the_other_values(self, engine):
        form = self.bound(username="")
        out = native(engine, "{{ form.as_p }}{{ form.errors }}", form=form)
        assert "This field is required" in out
        assert 'name="note" value="n"' in out
        assert MARKER not in out

    def test_a_disabled_floor_field(self, engine):
        class D(Shapes):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.fields["email"].disabled = True

        form = D(instance=row(True))
        out = native(engine, "{{ form.email }}{{ form.as_p }}", form=form)
        assert MARKER not in out
        assert "disabled" in out

    def test_a_prefixed_form(self, engine):
        data = {"u-" + k: v for k, v in self.DATA.items()}
        form = Shapes(data, instance=row(True), prefix="u")
        out = native(engine, "{{ form.as_p }}{{ form.password }}{{ form.secret }}", form=form)
        assert MARKER not in out, out

    def test_hidden_initial_is_not_propagated_from_posted_data(self, engine):
        """Django 5 renders the hidden-initial control from ``form.data`` (``as_widget(only_initial=True)``),
        not from ``value()``: a second route to the same value."""

        class H(forms.ModelForm):
            first_name = forms.CharField(show_hidden_initial=True, required=False)

            class Meta:
                model = User
                fields = ["username"]

        form = H(
            {"username": "b", "first_name": FIRST, "initial-first_name": FIRST}, instance=row(True)
        )
        reference = django_("{{ form.first_name }}", form=form)
        assert MARKER in reference, "not vacuous: Django propagates it"
        out = native(engine, "{{ form.first_name }}{{ form.as_p }}", form=form)
        assert MARKER not in out, out
        assert 'name="initial-first_name"' in out, "the hidden-initial control is still drawn"


class TestWidgetShapes:
    def test_choice_widgets_draw_every_option_and_select_none(self, engine):
        form = RadioShapes(
            instance=row(True), initial={"first_name": ["choice-3797-first"], "last_name": LAST}
        )
        out = native(engine, "{{ form.last_name }}{{ form.first_name }}", form=form)
        # Django selects the EMPTY choice of a field that has no value; it must not select the stored one
        assert 'value="choice-3797-last" id="id_last_name_1" checked' not in out, out
        assert 'value="choice-3797-first" id="id_first_name_0" checked' not in out, out
        assert out.count("<input") == 3 + 2, out
        twin = RadioShapes(instance=row(False), initial={})
        # the option VALUES are the author's choices and are drawn; the stored value is not "checked"
        assert out == django_("{{ form.last_name }}{{ form.first_name }}", form=twin)

    def test_a_select_selects_nothing_and_still_lists_its_options(self, engine):
        out = native(engine, "{{ form.last_name }}", form=shapes(True))
        assert 'value="choice-3797-last" selected' not in out
        assert out.count("<option") == 3

    def test_a_checkbox_is_unchecked(self, engine):
        out = native(engine, "{{ form.is_staff }}", form=shapes(True))
        assert "checked" not in out

    def test_a_textarea_has_no_body(self, engine):
        assert native(engine, "{{ form.first_name }}", form=shapes(True)).endswith("> </textarea>")

    def test_a_multiwidget_draws_empty_parts(self, engine):
        out = native(engine, "{{ form.date_joined }}", form=shapes(True))
        assert out.count("<input") == 2 and "2020" not in out and "03:04" not in out

    def test_a_third_party_widget_that_prints_the_value_never_gets_one(self, engine):
        form = WithLeaky(instance=row(True))
        out = native(engine, "{{ form.first_name }}{{ form }}", form=form)
        assert MARKER not in out, out


class TestWhatKeepsDrawing:
    def test_a_password_widget_is_byte_identical_to_django(self, engine):
        class P(forms.ModelForm):
            password = forms.CharField(widget=forms.PasswordInput, required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        form = P(instance=row(True))
        for src in ("{{ form.password }}", "{{ form }}", "{{ form.as_p }}"):
            assert native(engine, src, form=form) == django_(src, form=form)

    def test_render_value_true_is_the_authors_explicit_echo(self, engine):
        """``PasswordInput(render_value=True)``: pinned by #3798 as the form author's own choice."""

        class Echo(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput(render_value=True))

        form = Echo({"pw": "echo-3797-pw"})
        assert "echo-3797-pw" in native(engine, "{{ form.pw }}", form=form)

    def test_the_model_opt_out_is_the_way_to_echo_a_value(self, engine, monkeypatch):
        """Documented: ``djust_serialize_sensitive_fields`` takes a field off the floor, so it is drawn
        as Django draws it; a field that is not opted out stays blank."""
        monkeypatch.setattr(User, "djust_serialize_sensitive_fields", ["email"], raising=False)
        form = shapes(True)
        assert EMAIL in native(engine, "{{ form.email }}{{ form.as_p }}", form=form)
        assert "SECRET-3797-first" not in native(engine, "{{ form.first_name }}", form=form)

    def test_the_readonly_password_hash_summary_is_drawn_not_blanked(self, engine):
        user = User.objects.create_user("hashed", "h@example.com", "pw-3797-xyz")
        form = HashedUser(instance=user)
        out = native(engine, "{{ form.password }}", form=form)
        assert out == django_("{{ form.password }}", form=form)
        assert "No password set" not in out
        assert user.password not in out, "the summary shows a masked part, never the whole hash"

    def test_the_hash_widget_subclass_is_not_trusted(self, engine):
        from django.contrib.auth.forms import ReadOnlyPasswordHashWidget

        class Mine(ReadOnlyPasswordHashWidget):
            def render(self, name, value, attrs=None, renderer=None):
                return "<i>%s</i>" % value

        class M(forms.ModelForm):
            password = forms.CharField(widget=Mine, required=False)

            class Meta:
                model = User
                fields = ["username"]

        form = M(instance=row(True), initial={"password": HASH})
        assert MARKER in str(form["password"]), "not vacuous: the subclass prints the value"
        out = native(engine, "{{ form.password }}", form=form)
        assert MARKER not in out, out


class TestACustomFormTemplate:
    """A project's own form-renderer template reads the ``BoundField`` it is handed (``field.value``,
    ``field.initial``, ``field.data``): the copy a floor field is drawn from answers none of them."""

    TEMPLATE = (
        "{% for field, errors in fields %}[{{ field.name }}|{{ field.value }}|{{ field.initial }}"
        "|{{ field.data }}|{{ field }}|{% for w in field %}{{ w.data.value }}{% endfor %}]{% endfor %}"
    )

    def render(self, engine, **kwargs):
        from django.forms.renderers import DjangoTemplates

        template = self.TEMPLATE

        class StringRenderer(DjangoTemplates):
            def get_template(self, template_name):
                return self.engine.from_string(template)

        class Form(Shapes):
            template_name = "inline-3797"

        form = Form(renderer=StringRenderer(), **kwargs)
        for bound_field in form:
            bound_field.subwidgets  # noqa: B018 - the view's own code touched them first (cached)
        assert MARKER in form.as_div(), "not vacuous: the real form's own rendering prints it"
        return form, native(engine, "{{ form.as_div }}{{ form }}", form=form)

    def test_unbound(self, engine):
        _, out = self.render(
            engine, instance=row(True), initial={"api_token": TOKEN, "secret": SHH}
        )
        assert MARKER not in out, out
        assert "[username|bob|bob|None|" in out, "a field that is not on the floor reads as always"

    def test_bound(self, engine):
        _, out = self.render(engine, data=TestBound.DATA, instance=row(True))
        assert MARKER not in out, out
        assert "[username|bob|" in out
        assert "2020-01-02" not in out and "03:04:05" not in out, "the parts of a MultiWidget"

    def test_an_uploaded_file_of_a_floor_field_is_not_readable_as_data(self, engine):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.forms.renderers import DjangoTemplates

        class StringRenderer(DjangoTemplates):
            def get_template(self, template_name):
                return self.engine.from_string("{% for f, e in fields %}[{{ f.data }}]{% endfor %}")

        class Upload(forms.ModelForm):
            api_token = forms.FileField(required=False)
            template_name = "inline-3797"

            class Meta:
                model = User
                fields = ["username"]

        form = Upload(
            {"username": "u"},
            {"api_token": SimpleUploadedFile("SECRET-3797-file.txt", b"x")},
            instance=row(False),
            renderer=StringRenderer(),
        )
        assert MARKER in form.as_div(), "not vacuous"
        assert MARKER not in native(engine, "{{ form.as_div }}", form=form)


class TestTheRealEntries:
    """The same render through the entries a user hits: a LiveView held form (the native engine)."""

    SRC = (
        "<div dj-root>{{ form.password }}|{{ form.email }}|{{ form.as_p }}|"
        "{% for f in form %}{{ f }}{% endfor %}|{{ form.username }}</div>"
    )

    def view(self):
        from djust.live_view import LiveView

        class V(LiveView):
            template = self.SRC

            def mount(self, request, **kwargs):
                self.form = shapes(True)

        return V

    def test_liveview_test_client(self):
        from djust.testing import LiveViewTestClient

        client = LiveViewTestClient(self.view())
        client.mount()
        html = client.render()
        assert MARKER not in html, html
        assert 'value="bob"' in html

    def test_http_get(self):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = User.objects.create_user("viewer", "v@example.com", "pw")
        request.session = SessionStore()
        html = self.view().as_view()(request).content.decode()
        assert MARKER not in html, html
        assert 'value="bob"' in html


class TestTheProxiesRenderThemselves:
    """``str()`` and ``__html__()`` of the three proxies are what an engine may call on one; neither
    may be the real object's rendering."""

    def proxies(self):
        form = shapes(True)
        floor = serialization._model_form_floor(form)
        FormSet = modelformset_factory(User, form=Shapes, extra=0)
        user = User.objects.create_user("fs-direct", EMAIL, "pw", first_name=FIRST)
        formset = FormSet(queryset=User.objects.filter(pk=user.pk))
        return [
            serialization._SidecarFormProxy(form, floor),
            serialization._SidecarBoundFieldProxy(form["password"], floor),
            serialization._SidecarFormSetProxy(formset, serialization._formset_floor(formset)),
        ]

    @pytest.mark.parametrize("which", [0, 1, 2])
    @pytest.mark.parametrize("call", ["str", "html"])
    def test_str_and_html(self, which, call):
        proxy = self.proxies()[which]
        out = str(proxy) if call == "str" else proxy.__html__()
        assert MARKER not in out, out
        assert "<input" in out or "<div" in out or "<p" in out


class TestFormsets:
    def formset(self):
        FormSet = modelformset_factory(User, form=Shapes, extra=0)
        user = User.objects.create_user("fs-user", EMAIL, "pw", first_name=FIRST, last_name=LAST)
        user.password = HASH
        user.save()
        return FormSet(queryset=User.objects.filter(pk=user.pk))

    @pytest.mark.parametrize(
        "src",
        [
            "{{ formset }}",
            "{{ formset.as_p }}",
            "{{ formset.as_table }}",
            "{{ formset.as_ul }}",
            "{{ formset.as_div }}",
            "{{ formset.render }}",
            "{% for f in formset %}{{ f }}{{ f.password }}{{ f.as_p }}{% endfor %}",
            "{{ formset.forms.0 }}{{ formset.forms.0.as_p }}",
            "{% for f in formset.forms %}{% for x in f %}{{ x }}{% endfor %}{% endfor %}",
        ],
    )
    def test_a_formset_over_a_model(self, engine, src):
        out = native(engine, src, formset=self.formset())
        assert MARKER not in out, out
        assert "fs-user" in out, "the formset did render"


class TestSnapshots:
    """``render_form_value`` is the JSON-state converter: its HTML lands in LiveView state."""

    def test_a_form_in_state(self):
        snapshot = serialization.render_form_value(shapes(True))
        assert MARKER not in repr(snapshot)
        assert 'name="username" value="bob"' in snapshot["username"]
        assert "<input" in snapshot["password"]

    def test_a_bound_field_in_state(self):
        form = shapes(True)
        assert MARKER not in serialization.render_form_value(form["password"])
        assert 'value="bob"' in serialization.render_form_value(form["username"])


class TestComponents:
    """djust's own tags read ``bound_field.value()`` in Python; an editable floor field is blank now."""

    @pytest.mark.parametrize(
        "src",
        [
            "{% dj_form form %}",
            "{% combobox field=form.password %}",
            "{% rich_select field=form.password %}",
            "{% for x in form %}{% if x.name == 'email' %}{% combobox field=x %}{% endif %}{% endfor %}",
        ],
    )
    def test_an_editable_floor_field_is_drawn_without_its_value(self, engine, src):
        assert MARKER not in native(engine, src, form=shapes(True))

    def test_the_editable_floor_field_says_so_once(self, engine, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            native(engine, "{% dj_form form %}", form=shapes(True))
            native(engine, "{% dj_form form %}", form=shapes(True))
        said = [r for r in caplog.records if "WITHOUT its stored value" in r.getMessage()]
        assert 0 < len(said) <= len(FLOOR_FIELDS) + 1
        assert all(MARKER not in r.getMessage() for r in caplog.records)


class TestTheWarning:
    def test_a_template_render_names_the_field_once_and_never_the_value(self, engine, caplog):
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            native(engine, "{{ form.email }}{{ form.email }}{{ form.as_p }}", form=shapes(True))
        said = [
            r.getMessage() for r in caplog.records if "WITHOUT its stored value" in r.getMessage()
        ]
        assert any("User.email" in m for m in said), said
        assert len([m for m in said if "User.email" in m]) == 1
        assert all(MARKER not in m for m in said)

    def test_a_disabled_or_password_field_is_not_reported(self, engine, caplog):
        class D(forms.ModelForm):
            password = forms.CharField(widget=forms.PasswordInput, required=False)

            class Meta:
                model = User
                fields = ["username"]

        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            native(engine, "{{ form }}", form=D(instance=row(True)))
        assert not [r for r in caplog.records if "WITHOUT its stored value" in r.getMessage()]


class TestFailClosed:
    def test_a_bound_field_that_cannot_be_copied_draws_nothing(self, engine, monkeypatch, caplog):
        from types import SimpleNamespace

        def boom(_):
            raise RuntimeError("no copy")

        form = shapes(True)
        monkeypatch.setattr(serialization, "copy", SimpleNamespace(copy=boom))
        with caplog.at_level(logging.WARNING, logger="djust.serialization"):
            out = native(engine, "{{ form.password }}|{{ form.as_p }}|{{ form }}", form=form)
        assert MARKER not in out
        assert "could not be drawn" in caplog.text

    def test_a_value_that_is_a_property_is_not_trusted(self, engine):
        class Weird(forms.BoundField):
            @property
            def value(self):  # type: ignore[override]
                return lambda: HASH

        class WeirdField(forms.CharField):
            def get_bound_field(self, form, field_name):
                return Weird(form, self, field_name)

        class W(forms.ModelForm):
            email = WeirdField(required=False)

            class Meta:
                model = User
                fields = ["username"]

        assert MARKER not in native(
            engine, "{{ form.email }}{{ form }}", form=W(instance=row(True))
        )

    def test_an_initial_that_is_a_property_is_not_trusted(self, engine):
        class Weird(forms.BoundField):
            @property
            def initial(self):
                return HASH

        class WeirdField(forms.CharField):
            def get_bound_field(self, form, field_name):
                return Weird(form, self, field_name)

        class W(forms.ModelForm):
            email = WeirdField(required=False)

            class Meta:
                model = User
                fields = ["username"]

        form = W(instance=row(True))
        floor = serialization._model_form_floor(form)
        # no stock route prints ``initial`` of a blank copy, but a form template may: it is not drawn
        assert serialization._floor_blank_bound_field(form["email"], floor) is None
        assert MARKER not in native(engine, "{{ form.email }}{{ form }}", form=form)


class TestCost:
    """Counted, not timed: the floor is asked once per field of a form per render, not per field per field."""

    def test_the_form_floor_is_asked_once_per_field_per_render(self, engine, monkeypatch):
        asked = []
        real = serialization._floor_field_readable

        def counting(floor, name):
            asked.append(name)
            return real(floor, name)

        monkeypatch.setattr(serialization, "_floor_field_readable", counting)
        form = shapes(True)
        native(engine, "{{ form.as_p }}", form=form)
        assert 0 < len(asked) <= 4 * len(form.fields), (len(asked), len(form.fields))

    def test_a_form_with_no_floor_field_pays_nothing_here(self, engine, monkeypatch):
        blanked = []
        real = serialization._floor_blank_bound_field
        monkeypatch.setattr(
            serialization,
            "_floor_blank_bound_field",
            lambda *a, **k: blanked.append(a) or real(*a, **k),
        )

        class Plain(forms.Form):
            a = forms.CharField()
            b = forms.CharField()

        out = native(engine, "{{ form }}{{ form.a }}", form=Plain({"a": "x", "b": "y"}))
        assert 'value="x"' in out
        assert blanked == []
