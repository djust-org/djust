"""Blanking a secret must not rewrite the column (#3797, the review of the first cut).

A control drawn empty is posted back empty, and a ``ModelForm`` saves what it is posted. The first cut
of #3797 drew EVERY floor field empty and so wiped ``email`` / ``first_name`` / a ``<select>``'s choice
on an untouched re-save, and demoted a superuser through the stock ``UserChangeForm`` (an unchecked box
posts nothing). The maintainer's decision, held here:

1. only SECRET-LIKE floor fields are drawn empty (``password``, ``api_token``, ...); privilege flags,
   selects and ordinary declared-sensitive text keep drawing their real state;
2. every field that is drawn empty is ``disabled=True`` on a ``FormMixin`` form that edits an existing
   row, so Django ignores what is posted and keeps the stored value;
3. ``djust.S014`` reports an editable secret floor field in a plain ``ModelForm``, which no render-time
   change can protect (only ``disabled=True`` on the FORM FIELD does: HTML ``disabled`` / ``readonly``
   and leaving the control out all still save ``""``, measured below).

The submit is simulated the way a browser builds ``FormData`` (text value, only the checked boxes, the
selected-or-first option, a textarea body, nothing for a ``disabled`` control), then saved. The assertion
is the one the reviewer made against ``main``: editing ONE non-floor field leaves EVERY floor column
byte-identical.

Refs #3797, #3790, #1468.
"""

from __future__ import annotations

import gc
import re
from html.parser import HTMLParser

import pytest
from django import forms, template
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.template import Context, Engine
from django.test import override_settings
from django.utils.safestring import mark_safe

from djust import serialization
from djust.forms import FormMixin
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
FLOOR_SETTING = ["email", "first_name", "last_name", "api_token"]
COLUMNS = ("username", "password", "email", "first_name", "last_name", "is_staff", "is_superuser")

register = template.Library()


@register.simple_tag
def draw_3797(bound_field):
    """The shape of django-widget-tweaks' ``render_field``: draws the field it is handed."""
    return mark_safe(str(bound_field))


@register.filter
def drawn_3797(bound_field):
    return mark_safe(str(bound_field))


class Browser(HTMLParser):
    """What ``new FormData(form)`` would hold, by the HTML form-submission rules."""

    def __init__(self):
        super().__init__()
        self.data = {}
        self.sel = None
        self.opts = []
        self.ta = None
        self.ta_buf = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input":
            n, t = a.get("name"), (a.get("type") or "text").lower()
            if not n or "disabled" in a:
                return
            if t in ("checkbox", "radio"):
                if "checked" in a:
                    self.data[n] = a.get("value", "on")
            elif t not in ("submit", "button", "image", "reset", "file"):
                self.data[n] = a.get("value", "")
        elif tag == "select":
            self.sel = a.get("name") if "disabled" not in a else None
            self.opts = []
        elif tag == "option" and self.sel is not None:
            self.opts.append((a.get("value"), "selected" in a))
        elif tag == "textarea":
            self.ta = a.get("name") if "disabled" not in a else None
            self.ta_buf = ""

    def handle_data(self, d):
        if self.ta is not None:
            self.ta_buf += d

    def handle_endtag(self, tag):
        if tag == "select" and self.sel is not None:
            chosen = [v for v, s in self.opts if s]
            self.data[self.sel] = chosen[-1] if chosen else (self.opts[0][0] if self.opts else "")
            self.sel = None
        elif tag == "textarea" and self.ta is not None:
            self.data[self.ta] = self.ta_buf.lstrip("\n")
            self.ta = None


def browser_formdata(html: str) -> dict:
    parser = Browser()
    parser.feed(html)
    return parser.data


class EditUser(forms.ModelForm):
    """Text, textarea, select, checkbox: every kind of control, over a secret and non-secret columns."""

    password = forms.CharField(max_length=128, required=False)
    last_name = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], required=False)
    first_name = forms.CharField(widget=forms.Textarea, required=False)

    class Meta:
        model = User
        fields = list(COLUMNS)


class EditNoPassword(EditUser):
    class Meta(EditUser.Meta):
        fields = [c for c in COLUMNS if c != "password"]


class ProtectedEditUser(EditUser):
    """What a plain-``ModelForm`` author declares to protect the secret column (S014's fix)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].disabled = True


def make_user():
    user = User.objects.create_user(
        "bob", "bob@example.com", "pw", first_name="Bobby", last_name="b", is_staff=True
    )
    user.is_superuser = True
    user.save()
    return user


def snapshot(user) -> dict:
    fresh = User.objects.get(pk=user.pk)
    return {c: getattr(fresh, c) for c in COLUMNS}


@pytest.fixture(scope="module")
def engine():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    return DjustTemplateBackend(
        {
            "NAME": "loss-3797",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {"libraries": {"probe3797": __name__}},
        }
    )


@pytest.fixture(autouse=True)
def _floor(monkeypatch):
    monkeypatch.setattr(serialization, "_REFUSED_READ_WARNED", set())
    monkeypatch.setattr(serialization, "_EDITABLE_FLOOR_WARNED", set())
    with override_settings(DJUST_SENSITIVE_FIELDS=FLOOR_SETTING):
        yield


def classic_post(engine, form_class, **edits):
    """A classic view: render ``{{ form }}``, post what a browser would, save."""
    user = make_user()
    before = snapshot(user)
    html = str(engine.from_string("{{ form }}").render({"form": form_class(instance=user)}))
    posted = dict(browser_formdata(html), **edits)
    form = form_class(posted, instance=user)
    assert form.is_valid(), form.errors
    form.save()
    return before, snapshot(user), html, posted


class TestClassicPost:
    @pytest.mark.parametrize("form_class", [EditNoPassword, ProtectedEditUser])
    def test_editing_one_field_leaves_every_floor_column_alone(self, engine, form_class):
        before, after, _, _ = classic_post(engine, form_class, username="bob2")
        assert after.pop("username") == "bob2"
        before.pop("username")
        assert after == before

    def test_the_stock_userchangeform_does_not_demote_a_superuser(self, engine):
        """Zero configuration: the default floor holds ``is_staff`` / ``is_superuser``."""
        with override_settings(DJUST_SENSITIVE_FIELDS=[]):
            user = make_user()
            html = str(
                engine.from_string("{{ form }}").render({"form": UserChangeForm(instance=user)})
            )
            posted = browser_formdata(html)
            posted.update(first_name="Rita2", username="bob")
            posted.pop("user_permissions", None)
            posted.pop("groups", None)
            form = UserChangeForm(posted, instance=user)
            assert form.is_valid(), form.errors
            form.save()
            after = snapshot(user)
        assert after["is_staff"] is True and after["is_superuser"] is True
        assert after["first_name"] == "Rita2"

    def test_the_limit_an_unprotected_secret_is_cleared_by_a_plain_modelform(self, engine):
        """Pinned as a fact: no render-time change can protect it (this is why ``djust.S014`` exists)."""
        before, after, html, posted = classic_post(engine, EditUser, username="bob2")
        assert 'name="password"' in html and posted["password"] == ""
        assert before["password"] != "" and after["password"] == ""
        for column in ("email", "first_name", "last_name", "is_staff", "is_superuser"):
            assert after[column] == before[column], column

    @pytest.mark.parametrize("how", ["html-disabled", "html-readonly", "omitted"])
    def test_html_level_mitigations_do_not_protect_the_column(self, engine, how):
        """Measured, and the reason the protection lives on the FORM FIELD."""
        user = make_user()
        before = snapshot(user)
        html = str(engine.from_string("{{ form }}").render({"form": EditUser(instance=user)}))
        if how == "html-disabled":
            html = html.replace('name="password"', 'name="password" disabled')
        elif how == "html-readonly":
            html = html.replace('name="password"', 'name="password" readonly')
        else:
            html = re.sub(r"<input[^>]*name=\"password\"[^>]*>", "", html)
        posted = browser_formdata(html)
        posted["username"] = "bob2"
        if how == "html-readonly":
            assert posted["password"] == ""  # a readonly control is submitted
        form = EditUser(posted, instance=user)
        assert form.is_valid(), form.errors
        form.save()
        assert before["password"] != "" and snapshot(user)["password"] == ""


class EditView(FormMixin, LiveView):
    template = (
        '<div dj-root><form dj-submit="submit_form">{{ form }}<button>go</button></form></div>'
    )
    form_class = EditUser

    def mount(self, request, **kwargs):
        self._model_instance = User.objects.get(pk=kwargs["pk"])
        super().mount(request, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = self.form_instance
        return ctx

    def form_valid(self, form):
        self.saved = form.save()


class TestFormMixinSubmit:
    @pytest.mark.parametrize("form_class", [EditUser, EditNoPassword])
    def test_an_untouched_submit_leaves_every_floor_column_alone(self, form_class):
        user = make_user()
        before = snapshot(user)

        class View(EditView):
            pass

        View.form_class = form_class
        client = LiveViewTestClient(View)
        client.mount(pk=user.pk)
        html = client.render()
        posted = dict(browser_formdata(html), username="bob2")
        assert ("password" in posted) is False, (
            "a protected secret is a disabled control: not posted"
        )
        client.send_event("submit_form", **posted)
        after = snapshot(user)
        assert after.pop("username") == "bob2"
        before.pop("username")
        assert after == before

    def test_the_secret_control_is_drawn_empty_and_disabled(self):
        user = make_user()
        client = LiveViewTestClient(EditView)
        client.mount(pk=user.pk)
        html = client.render()
        control = re.search(r"<input[^>]*name=\"password\"[^>]*>", html).group(0)
        assert "disabled" in control and user.password not in html

    def test_a_non_secret_floor_field_is_drawn_with_its_real_state(self):
        user = make_user()
        client = LiveViewTestClient(EditView)
        client.mount(pk=user.pk)
        html = client.render()
        assert 'value="bob@example.com"' in html
        assert re.search(r'name="is_staff"[^>]*checked', html)


class TestProtectBlankedColumns:
    def fields(self, form):
        return {n: f.disabled for n, f in form.fields.items()}

    def test_an_existing_row_gets_its_secret_disabled_and_nothing_else(self):
        form = EditUser(instance=make_user())
        serialization.protect_blanked_floor_columns(form)
        disabled = {n for n, d in self.fields(form).items() if d}
        assert disabled == {"password"}

    def test_a_new_record_is_left_alone(self):
        form = EditUser()
        serialization.protect_blanked_floor_columns(form)
        assert not any(self.fields(form).values())

    def test_a_password_widget_is_left_alone(self):
        class Signup(forms.ModelForm):
            password = forms.CharField(widget=forms.PasswordInput)

            class Meta:
                model = User
                fields = ["username"]

        form = Signup(instance=make_user())
        serialization.protect_blanked_floor_columns(form)
        assert form.fields["password"].disabled is False

    def test_the_model_opt_out_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(User, "djust_serialize_sensitive_fields", ["password"], raising=False)
        form = EditUser(instance=make_user())
        serialization.protect_blanked_floor_columns(form)
        assert form.fields["password"].disabled is False

    def test_a_plain_form_is_left_alone(self):
        class Plain(forms.Form):
            api_token = forms.CharField()

        form = Plain()
        serialization.protect_blanked_floor_columns(form)
        assert form.fields["api_token"].disabled is False


class TestMadeReadOnlyIsNotSilent:
    """A secret-LOOKING floor field on an edit form becomes ``disabled``: say so, once, by name."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        monkeypatch.setattr(serialization, "_READ_ONLY_WARNED", set())

    def said(self, caplog):
        return [r.getMessage() for r in caplog.records if "made it read-only" in r.getMessage()]

    def mount(self, form_class, user=None):
        user = user or make_user()

        class View(EditView):
            pass

        View.form_class = form_class
        client = LiveViewTestClient(View)
        client.mount(pk=user.pk)
        return user, client.view_instance.form_instance

    def test_djust_exclude_fields_named_secretish_fields_are_reported(self, monkeypatch, caplog):
        """Reproducer 1: ``djust_exclude_fields`` only keeps fields out of JSON state, yet it put
        ``sort_key`` / ``token_count`` on the floor, and a secret-LOOKING name made them read-only."""
        monkeypatch.setattr(
            User, "djust_exclude_fields", ["sort_key", "token_count"], raising=False
        )

        class SortForm(forms.ModelForm):
            sort_key = forms.CharField(required=False)
            token_count = forms.IntegerField(required=False)

            class Meta:
                model = User
                fields = ["username"]

        with caplog.at_level("WARNING", logger="djust.serialization"):
            user, form = self.mount(SortForm)
            self.mount(SortForm, user)
        assert form.fields["sort_key"].disabled and form.fields["token_count"].disabled
        said = self.said(caplog)
        assert len(said) == 2, said
        assert any("sort_key" in m for m in said) and any("token_count" in m for m in said)
        assert all("djust_serialize_sensitive_fields" in m and "disabled=True" in m for m in said)

    def test_a_set_a_new_password_box_on_an_existing_row_is_reported(self, caplog):
        """Reproducer 2: the ``password`` column as a text box is read-only on an edit form."""
        with caplog.at_level("WARNING", logger="djust.serialization"):
            user, form = self.mount(EditUser)
        assert form.fields["password"].disabled
        said = self.said(caplog)
        assert len(said) == 1 and "auth.User.password" in said[0], said
        assert user.password not in said[0] and user.password not in caplog.text

    def test_a_field_already_disabled_by_the_author_is_not_reported(self, caplog):
        with caplog.at_level("WARNING", logger="djust.serialization"):
            self.mount(ProtectedEditUser)
        assert not self.said(caplog)

    def test_it_is_once_per_model_and_field_across_threads(self, caplog):
        import threading

        def hammer():
            for _ in range(50):
                serialization._warn_field_made_read_only(User, "api_token")

        with caplog.at_level("WARNING", logger="djust.serialization"):
            threads = [threading.Thread(target=hammer) for _ in range(8)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        assert len(self.said(caplog)) == 1

    def test_the_dedupe_set_is_bounded(self, monkeypatch, caplog):
        monkeypatch.setattr(serialization, "_READ_ONLY_WARNED_CAP", 3)
        with caplog.at_level("WARNING", logger="djust.serialization"):
            for i in range(10):
                serialization._warn_field_made_read_only(User, "secret_%d" % i)
        assert len(serialization._READ_ONLY_WARNED) == 3
        assert len(self.said(caplog)) == 3


class TestProtectKeepsTheKeptWidgets:
    def test_a_password_hash_widget_field_is_not_made_read_only(self):
        """``UserChangeForm.password`` shaped: a non-password-widget the floor draws as its own summary."""
        from django.contrib.auth.forms import ReadOnlyPasswordHashWidget

        class Hash(forms.ModelForm):
            password = forms.CharField(widget=ReadOnlyPasswordHashWidget, required=False)

            class Meta:
                model = User
                fields = ["username"]

        form = Hash(instance=make_user())
        serialization.protect_blanked_floor_columns(form)
        assert form.fields["password"].disabled is False


class TestSecretPredicate:
    EXPECTED_WORDS = {
        "passphrase",
        "pwd",
        "key",
        "hash",
        "salt",
        "credential",
        "credentials",
        "otp",
        "signature",
        "pin",
        "password",
        "passwd",
        "secret",
        "token",
        "apikey",
    }

    def test_the_word_list_is_exactly_this(self):
        assert serialization._SECRET_NAME_WORDS == self.EXPECTED_WORDS
        assert serialization._SECRET_NAME_SUBSTRINGS == (
            "password",
            "passwd",
            "secret",
            "token",
            "apikey",
        )

    @pytest.mark.parametrize("word", sorted(EXPECTED_WORDS))
    @pytest.mark.parametrize("shape", ["{w}", "my_{w}", "{w}_value", "my-{w}-x", "my{W}"])
    def test_every_word_matches_as_a_whole_segment(self, word, shape):
        assert serialization._name_is_secret_like(shape.format(w=word, W=word.capitalize()))

    @pytest.mark.parametrize("name", ["pin", "pin_code", "PinCode", "user_pin"])
    def test_pin(self, name):
        assert serialization._name_is_secret_like(name)

    @pytest.mark.parametrize(
        "name",
        ["keyboard", "monkey", "hashtag", "pinned", "pinyin", "pinch", "salty", "keys_count"],
    )
    def test_a_word_inside_a_longer_segment_is_not_a_secret(self, name):
        assert not serialization._name_is_secret_like(name)

    @pytest.mark.parametrize(
        "name",
        [
            "pass",
            "passcode",
            "pw",
            "ssn",
            "cvv",
            "card_number",
            "auth_code",
            "bearer",
            "jwt",
            "totp_seed",
            "recovery_code",
        ],
    )
    def test_the_known_limits_of_a_name_heuristic(self, name):
        """Documented in SECURE_DEFAULTS (a project adds more through the model's own settings)."""
        assert not serialization._name_is_secret_like(name)


class TestSecretPredicateExamples:
    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "api_token",
            "secret_key",
            "authKey",
            "pwd_hash",
            "newpassword",
            "salt",
            "otp",
        ],
    )
    def test_secret_like(self, name):
        assert serialization._name_is_secret_like(name)

    @pytest.mark.parametrize(
        "name",
        [
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "is_superuser",
            "role",
            "groups",
            "monkey",
        ],
    )
    def test_not_secret_like(self, name):
        assert not serialization._name_is_secret_like(name)


# ---------------------------------------------------------------------------------------------
# djust.S014


def _form_class(name, fields, module="myproject.forms", **attrs):
    meta = type("Meta", (), {"model": User, "fields": fields})
    return type(name, (forms.ModelForm,), {"__module__": module, "Meta": meta, **attrs})


class TestS014:
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        gc.collect()

    def run(self):
        from djust.checks.security import check_editable_secret_floor_fields

        return [m for m in check_editable_secret_floor_fields(None) if m.id == "djust.S014"]

    def test_an_editable_secret_is_reported_with_the_fix(self):
        EditPassword = _form_class("EditPassword3797", ["username", "password"])
        messages = self.run()
        mine = [m for m in messages if "EditPassword3797" in m.msg]
        assert len(mine) == 1
        assert "password" in mine[0].msg and "disabled=True" in mine[0].hint
        assert "disabled = True" in mine[0].fix_hint
        del EditPassword

    def test_nothing_that_is_safe_is_reported(self):
        _kept = [
            _form_class(
                "Disabled3797", ["username", "password"], password=forms.CharField(disabled=True)
            ),
            _form_class(
                "Widget3797", ["username"], password=forms.CharField(widget=forms.PasswordInput)
            ),
            _form_class("Plain3797", ["username", "email", "first_name", "is_staff"]),
            _form_class("Test3797", ["username", "password"], module="myproject.tests"),
            _form_class("Django3797", ["username", "password"], module="django.contrib.fake"),
        ]
        names = {m.msg.split(" ")[0].rsplit(".", 1)[-1] for m in self.run()}
        assert not names & {"Disabled3797", "Widget3797", "Plain3797", "Test3797", "Django3797"}
        del _kept

    def test_the_model_opt_out_silences_it(self, monkeypatch):
        monkeypatch.setattr(User, "djust_serialize_sensitive_fields", ["password"], raising=False)
        kept = _form_class("OptedOut3797", ["username", "password"])
        assert not [m for m in self.run() if "OptedOut3797" in m.msg]
        del kept

    def test_a_kept_widget_is_not_reported(self):
        from django.contrib.auth.forms import ReadOnlyPasswordHashWidget

        kept = _form_class(
            "Hash3797",
            ["username"],
            password=forms.CharField(widget=ReadOnlyPasswordHashWidget, required=False),
        )
        assert not [m for m in self.run() if "Hash3797" in m.msg]
        del kept

    def test_it_can_be_suppressed(self):
        kept = _form_class("Suppressed3797", ["username", "password"])
        with override_settings(DJUST_CONFIG={"suppress_checks": ["S014"]}):
            from djust.checks.security import check_editable_secret_floor_fields

            assert check_editable_secret_floor_fields(None) == []
        del kept


# ---------------------------------------------------------------------------------------------
# bridged tags and filters (F2), validation messages (F4), the copy that is the original (F5)


def _tags_engine_render(engine, src, **context):
    return re.sub(
        r"\s+", " ", str(engine.from_string("{% load probe3797 %}" + src).render(context))
    )


def row_with_secrets():
    return User(
        username="bob", password='"><i>SECRET-3797-hash</i>', email="plain-3797@example.com"
    )


class SecretForm(forms.ModelForm):
    api_token = forms.CharField(required=False)

    class Meta:
        model = User
        fields = ["username", "password", "email"]


class TestBridgedTagsAndFilters:
    SRC = (
        "{% draw_3797 form.password %}|{% draw_3797 form.api_token %}|{{ form.password|drawn_3797 }}|"
        "{{ form.api_token|drawn_3797 }}|{% draw_3797 form.email %}"
    )

    def form(self):
        return SecretForm(instance=row_with_secrets(), initial={"api_token": "SECRET-3797-token"})

    def test_a_tag_or_filter_that_draws_a_secret_field_prints_no_value(self, engine):
        out = _tags_engine_render(engine, self.SRC, form=self.form())
        assert "SECRET-3797" not in out, out
        assert 'name="password"' in out, "the control is still drawn"

    def test_a_field_that_is_not_a_secret_is_drawn_with_its_real_value(self, engine):
        out = _tags_engine_render(engine, self.SRC, form=self.form())
        assert "plain-3797@example.com" in out

    def test_not_vacuous_the_real_field_prints_it(self):
        assert "SECRET-3797" in str(self.form()["password"])

    def test_the_component_readers_still_get_a_field_they_can_call_value_on(self, engine):
        out = _tags_engine_render(engine, "{% combobox field=form.password %}", form=self.form())
        assert "SECRET-3797" not in out


class TestValidationMessages:
    def form(self):
        class D(forms.ModelForm):
            auth_key = forms.ChoiceField(choices=[("a", "A")], disabled=True, required=False)
            template_name = "inline-3797"

            class Meta:
                model = User
                fields = ["username"]

        return D(
            {"username": "bob"},
            instance=User(username="bob"),
            initial={"auth_key": "SECRET-3797-stored"},
        )

    def test_a_disabled_secret_choice_field_does_not_echo_its_stored_value(self, engine):
        with override_settings(DJUST_SENSITIVE_FIELDS=["auth_key"]):
            form = self.form()
            reference = re.sub(
                r"\s+",
                " ",
                Engine().from_string("{{ form.as_p }}").render(Context({"form": self.form()})),
            )
            assert "SECRET-3797-stored" in reference, "not vacuous: Django prints it"
            out = _tags_engine_render(
                engine,
                "{{ form.as_p }}|{{ form.auth_key.errors }}|{{ form.errors }}|{{ form.errors.auth_key }}",
                form=form,
            )
        assert "SECRET-3797" not in out, out
        assert "errorlist" in out, "an error is still reported"

    def test_the_form_copy_a_renderer_template_gets_has_scrubbed_errors_too(self, engine):
        from django.forms.renderers import DjangoTemplates

        class StringRenderer(DjangoTemplates):
            def get_template(self, template_name):
                return self.engine.from_string(
                    "{% for k, v in form.errors.items %}{{ k }}={% for e in v %}{{ e }}|{% endfor %}{% endfor %}"
                )

        with override_settings(DJUST_SENSITIVE_FIELDS=["auth_key"]):
            form = self.form()
            form.renderer = StringRenderer()
            assert "SECRET-3797-stored" in form.as_div(), "not vacuous: the real form prints it"
            out = _tags_engine_render(engine, "{{ form.as_div }}", form=form)
        assert "SECRET-3797" not in out and "auth_key=" in out, out

    def test_a_formsets_errors_do_not_echo_it(self, engine):
        from django.forms import modelformset_factory

        class D(forms.ModelForm):
            auth_key = forms.ChoiceField(choices=[("a", "A")], disabled=True, required=False)

            class Meta:
                model = User
                fields = ["username"]

        FormSet = modelformset_factory(User, form=D, extra=1)
        data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-username": "x",
        }
        with override_settings(DJUST_SENSITIVE_FIELDS=["auth_key"]):
            formset = FormSet(
                data,
                queryset=User.objects.none(),
                form_kwargs={"initial": {"auth_key": "SECRET-3797-stored"}},
            )
            reference = (
                Engine().from_string("{{ formset.errors }}").render(Context({"formset": formset}))
            )
            assert "SECRET-3797-stored" in reference, "not vacuous: Django prints it"
            out = _tags_engine_render(
                engine, "{{ formset.errors }}|{{ formset.as_p }}", formset=formset
            )
        assert "SECRET-3797" not in out, out


class TestSensitiveTypeSecrets:
    def test_a_configured_sensitive_type_is_a_secret(self, engine):
        form = SecretForm(instance=row_with_secrets())
        floor = serialization._model_form_floor(form)
        assert serialization._floor_field_is_secret(floor, "email", form.fields["email"]) is False
        with override_settings(LIVEVIEW_CONFIG={"sensitive_field_types": ["EmailField"]}):
            from djust.config import config

            config.reload() if hasattr(config, "reload") else None
            floor = serialization._model_form_floor(form)
            assert (
                serialization._floor_field_is_secret(floor, "email", form.fields["email"]) is True
            )


class TestACopyThatIsTheOriginal:
    def test_the_real_field_is_never_written_to(self):
        class Same(forms.BoundField):
            def __copy__(self):
                return self

        class SameField(forms.CharField):
            def get_bound_field(self, form, field_name):
                return Same(form, self, field_name)

        class W(forms.ModelForm):
            api_token = SameField(required=False)

            class Meta:
                model = User
                fields = ["username"]

        with override_settings(DJUST_SENSITIVE_FIELDS=["api_token"]):
            form = W(instance=User(username="bob"), initial={"api_token": "SECRET-3797-real"})
            floor = serialization._model_form_floor(form)
            assert serialization._floor_blank_bound_field(form["api_token"], floor) is None
            assert form["api_token"].value() == "SECRET-3797-real"
            assert form["api_token"].initial == "SECRET-3797-real"
