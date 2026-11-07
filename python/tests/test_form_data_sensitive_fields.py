"""Sensitive form fields are not pre-filled from the stored value (``FormMixin`` form-data seeding).

``form_data`` is seeded from each field's initial value, which for a ``ModelForm`` over an existing row
is the stored column. The rule held here: a form field that is on the serialization floor and is
credential-like (:func:`djust.serialization._floor_field_is_secret`) is seeded with ``""``, everything
else is as it was, and an untouched re-save still leaves the column alone (the form field is ``disabled``).

Every surface (``form_data``, the getters, ``get_state()``, ``get_debug_info()``, the rendered HTML,
the WebSocket frames, the HTTP GET body, the session blob) is asserted with a COUNT of the stored
value, not a truthiness check.
"""

from __future__ import annotations

import json

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.test import RequestFactory, override_settings

from djust import serialization
from djust.forms import FormMixin
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient

pytestmark = [pytest.mark.django_db]

User = get_user_model()
STORED = "pbkdf2_sha256$600000$SALT$VALUEVALUEVALUE"


class EditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "password"]


class EditView(FormMixin, LiveView):
    form_class = EditForm
    template = '<div dj-root><form dj-submit="submit_form">{{ form.as_live }}<button>go</button></form></div>'

    def mount(self, request, **kwargs):
        self._model_instance = User.objects.get(pk=kwargs["pk"])
        super().mount(request, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = self
        return ctx

    def form_valid(self, form):
        self.saved = form.save()


class CreateView(FormMixin, LiveView):
    form_class = EditForm
    template = '<div dj-root><form dj-submit="submit_form">{{ form.as_live }}</form></div>'

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = self
        return ctx

    def form_valid(self, form):
        self.saved = form.save()


def make_user():
    return User.objects.create(
        username="alice", email="alice@example.org", first_name="Alice", password=STORED
    )


def surfaces(view, client=None) -> dict:
    """Every place the framework puts a form's value, as text."""
    out = {
        "form_data": json.dumps(view.form_data, default=str),
        "as_live": str(view.as_live()),
        "as_live_field": str(view.as_live_field("password")),
        "get_field_value": json.dumps(view.get_field_value("password"), default=str),
        "get_state": json.dumps(view.get_state(), default=str),
        "get_debug_info": json.dumps(view.get_debug_info(), default=str),
        "field_errors": json.dumps(
            {k: [str(e) for e in v] for k, v in view.field_errors.items()}, default=str
        ),
    }
    if client is not None:
        out["render"] = client.render()
    return out


def assert_not_prefilled(view, client=None):
    found = {name: text.count(STORED) for name, text in surfaces(view, client).items()}
    assert found == {name: 0 for name in found}, found


def mount_edit(user):
    client = LiveViewTestClient(EditView)
    client.mount(pk=user.pk)
    return client, client.view_instance


class TestSeeding:
    def test_the_stored_value_is_on_no_surface_after_mount(self):
        client, view = mount_edit(make_user())
        assert_not_prefilled(view, client)

    def test_form_data_is_exact(self):
        """Counted, not truthy: the secret is ``""``, every other field is its stored value."""
        _, view = mount_edit(make_user())
        assert view.form_data == {
            "username": "alice",
            "email": "alice@example.org",
            "first_name": "Alice",
            "password": "",
        }

    def test_the_http_get_body_has_no_value(self):
        user = make_user()
        request = RequestFactory().get("/x/")
        request.user = None
        from django.contrib.sessions.backends.signed_cookies import SessionStore

        request.session = SessionStore()
        view = EditView()
        view.setup(request, pk=user.pk)
        body = view.get(request, pk=user.pk).content.decode()
        assert body.count(STORED) == 0
        assert body.count("alice@example.org") >= 1
        assert json.dumps(dict(request.session), default=str).count(STORED) == 0

    def test_non_sensitive_fields_render_as_before(self):
        _, view = mount_edit(make_user())
        html = view.as_live()
        for stored in ("alice", "alice@example.org", "Alice"):
            assert html.count(f'value="{stored}"') == 1, stored

    def test_reset_form_does_not_reseed_the_value(self):
        client, view = mount_edit(make_user())
        client.send_event("reset_form")
        assert_not_prefilled(view, client)
        assert view.form_data["password"] == ""

    def test_live_field_tag_has_no_value(self):
        from django.template import Context, Engine

        _, view = mount_edit(make_user())
        engine = Engine(libraries={"live_tags": "djust.templatetags.live_tags"})
        html = engine.from_string(
            '{% load live_tags %}{% live_field view "password" %}{% live_field view "email" %}'
        ).render(Context({"view": view}))
        assert html.count(STORED) == 0
        assert html.count("alice@example.org") == 1


class TestSubmitKeepsTheColumn:
    def stored(self, user):
        return User.objects.get(pk=user.pk).password

    def test_editing_one_field_leaves_the_password_column_byte_identical(self):
        user = make_user()
        client, view = mount_edit(user)
        client.send_event(
            "submit_form", username="alice2", email="alice@example.org", first_name="Alice"
        )
        assert view.is_valid is True, view.field_errors
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert self.stored(user) == STORED
        assert_not_prefilled(view, client)
        assert view.form_data["password"] == ""

    def test_a_posted_blank_password_is_ignored_on_an_edit(self):
        user = make_user()
        client, view = mount_edit(user)
        client.send_event("submit_form", username="alice3", email="alice@example.org", password="")
        assert self.stored(user) == STORED

    def test_a_typed_value_into_a_sensitive_named_text_field_still_writes(self):
        """DIFFERENCE from the main line: there a secret-like field that is not a password input is
        read-only on an edit form. On the stable line it stays editable, as it always was: only the
        stored value is withheld and only a blank submission keeps the column."""
        user = make_user()
        client, view = mount_edit(user)
        client.send_event(
            "submit_form", username="alice3", email="alice@example.org", password="new-one"
        )
        assert self.stored(user) == "new-one"
        assert_not_prefilled(view, client)

    def test_a_create_view_can_still_set_the_password(self):
        client = LiveViewTestClient(CreateView)
        client.mount()
        view = client.view_instance
        client.send_event(
            "submit_form", username="newbie", email="n@example.org", password="correct horse"
        )
        assert view.is_valid is True, view.field_errors
        assert User.objects.get(username="newbie").password == "correct horse"
        # what was typed is not kept in the state the framework reports
        assert json.dumps(view.get_state(), default=str).count("correct horse") == 0
        assert view.form_data["password"] == ""


class TestErrorsAreGeneric:
    def test_a_choice_error_on_the_disabled_sensitive_does_not_carry_the_value(self):
        """A disabled field is validated against its STORED value; ``invalid_choice`` prints it."""

        class ChoiceForm(forms.ModelForm):
            password = forms.ChoiceField(choices=[("x", "X")], required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        class V(EditView):
            form_class = ChoiceForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        view = client.view_instance
        assert view.is_valid is False
        assert "password" in view.field_errors
        assert_not_prefilled(view, client)
        assert User.objects.get(pk=user.pk).password == STORED

    def test_validate_field_for_the_sensitive_does_not_echo_the_stored_value(self):
        user = make_user()
        client, view = mount_edit(user)
        client.send_event("validate_field", field="password", value="x" * 300)
        assert "password" in view.field_errors
        assert_not_prefilled(view, client)


class WsEditView(FormMixin, LiveView):
    form_class = EditForm
    template = (
        f'<div dj-view="{__name__}.WsEditView" dj-id="0"><form dj-submit="submit_form">'
        "{{ form.as_live }}</form><p>{{ form_data.email }}</p></div>"
    )

    def mount(self, request, **kwargs):
        self._model_instance = User.objects.get(username="alice")
        super().mount(request, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = self
        return ctx

    def form_valid(self, form):
        self.saved = form.save()


@pytest.mark.django_db(transaction=True)
class TestOverTheWebSocket:
    """The frames a real socket carries: the mount's HTML, the patches, the replies."""

    def drive(self, events):
        import asyncio

        pytest.importorskip("channels")

        async def session():
            from channels.testing import WebsocketCommunicator

            from djust.websocket import LiveViewConsumer

            comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            connected, _ = await comm.connect()
            assert connected
            frames = [await comm.receive_json_from()]
            try:
                await comm.send_json_to(
                    {"type": "mount", "view": f"{__name__}.WsEditView", "url": "/x/"}
                )
                frames.append(await comm.receive_json_from(timeout=10))
                for ref, event in enumerate(events, 1):
                    await comm.send_json_to({"type": "event", "ref": ref, **event})
                    while True:
                        frame = await comm.receive_json_from(timeout=10)
                        frames.append(frame)
                        if frame.get("type") in {"patch", "html_update", "error", "noop"}:
                            break
            finally:
                await comm.disconnect()
            return frames

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(session())
        finally:
            loop.close()

    def test_no_frame_carries_the_value_and_the_column_survives_a_submit(self):
        make_user()
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=False):
            frames = self.drive(
                [
                    {
                        "event": "submit_form",
                        "params": {
                            "username": "alice2",
                            "email": "alice@example.org",
                            "first_name": "Alice",
                            "password": "",
                        },
                    },
                    {"event": "reset_form", "params": {}},
                ]
            )
        text = json.dumps(frames, default=str)
        assert text.count(STORED) == 0
        assert text.count("alice@example.org") >= 1  # the frames are not vacuous
        assert User.objects.get(username="alice2").password == STORED


class WizardStepForm(forms.Form):
    nick = forms.CharField(initial="nick", required=False)
    pw = forms.CharField(widget=forms.PasswordInput, initial="hunter2", required=False)
    drawn = forms.CharField(
        widget=forms.PasswordInput, initial=lambda: "generated-9", required=False
    )


class TestWizardStep:
    """``WizardMixin`` seeds a step's ``form_data`` and ``field_html`` through ``initial_field_value``."""

    def _context(self):
        from djust.wizard import WizardMixin

        class Stub:
            def get_context_data(self, **kwargs):
                return dict(kwargs)

        cls = type(
            "W",
            (WizardMixin, Stub),
            {"wizard_steps": [{"name": "s", "form_class": WizardStepForm, "title": "S"}]},
        )
        view = cls()
        view.wizard_step_index = 0
        view.wizard_step_data = {}
        view.wizard_step_errors = {}
        view.wizard_completed_steps = []
        return view, view.get_context_data()

    def test_a_password_widget_initial_is_in_neither_form_data_nor_field_html(self):
        view, ctx = self._context()
        assert ctx["form_data"] == {"nick": "nick", "pw": "", "drawn": ""}
        everything = json.dumps([ctx["form_data"], ctx["field_html"], view.wizard_step_data])
        for secret in ("hunter2", "generated-9"):
            assert everything.count(secret) == 0, secret
        assert ctx["field_html"]["nick"].count('value="nick"') == 1


class TestAdminFormView:
    """``AdminFormMixin`` seeds ``form_data`` from ``field.value()`` and builds its own forms."""

    def _view(self, user, form_class=EditForm):
        from djust.admin_ext.forms import AdminFormMixin

        class Admin:
            def get_object(self, request, object_id):
                return user

            def get_form(self, request, obj=None):
                return form_class

        class View(AdminFormMixin, LiveView):
            _model = User
            _model_admin = Admin()

            def form_valid(self, form):
                self.saved = form.save()

        view = View()
        view.mount(RequestFactory().get("/admin/"), object_id=str(user.pk))
        return view

    def test_the_seed_and_every_getter_have_no_value(self):
        view = self._view(make_user())
        assert view.form_data["password"] == ""
        assert view.form_data["email"] == "alice@example.org"
        assert view.get_field_value("password") == ""
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0
        assert json.dumps(view.get_debug_info(), default=str).count(STORED) == 0
        assert str(view.as_live()).count(STORED) == 0

    def test_a_name_that_is_not_a_form_field_is_not_read_off_the_instance(self):
        class NoPassword(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username"]

        view = self._view(make_user(), NoPassword)
        assert view.get_field_value("password", default="-") == "-"
        assert view.get_field_value("email") == "alice@example.org"

    def test_an_admin_choice_error_on_the_disabled_sensitive_does_not_carry_the_value(self):
        class ChoiceForm(forms.ModelForm):
            password = forms.ChoiceField(choices=[("x", "X")], required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        view = self._view(make_user(), ChoiceForm)
        view.submit_form(username="alice")
        assert view.is_valid is False
        assert "password" in view.field_errors
        assert (
            json.dumps({k: [str(e) for e in v] for k, v in view.field_errors.items()}).count(STORED)
            == 0
        )

    def test_an_untouched_admin_submit_keeps_the_password_column(self):
        user = make_user()
        view = self._view(user)
        view.submit_form(username="alice2", email="alice@example.org", first_name="Alice")
        assert view.is_valid is True, view.field_errors
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert User.objects.get(pk=user.pk).password == STORED


class TestOtherSensitiveShapes:
    def test_a_plain_form_password_widget_initial_is_withheld(self):
        class Plain(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput, initial="hunter2")
            name = forms.CharField(initial="n")

        class V(FormMixin, LiveView):
            form_class = Plain
            template = "<div dj-root></div>"

        client = LiveViewTestClient(V)
        client.mount()
        view = client.view_instance
        assert view.form_data == {"pw": "", "name": "n"}
        assert view.as_live().count("hunter2") == 0

    def test_a_render_value_password_input_is_withheld_from_form_data_too(self):
        """The widget's own echo is the Django widget's business; ``form_data``, the state, the session
        and the frames are a worse place for a secret (the live renderers do not echo it)."""

        class Plain(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput(render_value=True), initial="hunter2")

        class V(FormMixin, LiveView):
            form_class = Plain
            template = "<div dj-root></div>"

        client = LiveViewTestClient(V)
        client.mount()
        view = client.view_instance
        assert view.form_data == {"pw": ""}
        assert json.dumps(view.get_state(), default=str).count("hunter2") == 0
        assert view.as_live().count("hunter2") == 0

    def test_the_stock_userchangeform_password_field_is_withheld(self):
        class V(EditView):
            form_class = UserChangeForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        assert_not_prefilled(client.view_instance, client)

    def test_the_model_opt_out_keeps_the_value(self, monkeypatch):
        monkeypatch.setattr(User, "djust_serialize_sensitive_fields", ["password"], raising=False)
        _, view = mount_edit(make_user())
        assert view.form_data["password"] == STORED

    def test_an_api_token_column_is_withheld_by_name(self):
        class TokenForm(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "last_name"]

        class V(EditView):
            form_class = TokenForm

        # ``last_name`` is not secret-like: unchanged
        user = make_user()
        user.last_name = "Smith"
        user.save()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        assert client.view_instance.form_data["last_name"] == "Smith"

    def test_a_declared_sensitive_field_that_is_not_credential_like_keeps_its_value(self):
        """``email`` / ``first_name`` / ``is_staff`` on the floor are not secrets: drawing them empty
        would overwrite them on the next save, so they are seeded as they were."""

        class Flags(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "email", "first_name", "is_staff", "password"]

        class V(EditView):
            form_class = Flags

        user = make_user()
        user.is_staff = True
        user.save()
        with override_settings(DJUST_SENSITIVE_FIELDS=["email", "first_name"]):
            client = LiveViewTestClient(V)
            client.mount(pk=user.pk)
        assert client.view_instance.form_data == {
            "username": "alice",
            "email": "alice@example.org",
            "first_name": "Alice",
            "is_staff": True,
            "password": "",
        }

    def test_a_credential_like_column_outside_a_per_model_allowlist_is_withheld(self):
        """The floor includes every column outside a ``djust_serializable_fields`` allowlist: a
        credential-like one is seeded empty, one inside the allowlist (or on a model without one)
        keeps its stored value, as for ``{{ obj.api_key }}``."""
        from django.db import models

        def build(label, **attrs):
            return type(
                label,
                (models.Model,),
                {
                    "__module__": __name__,
                    "name": models.CharField(max_length=20, default=""),
                    "api_key": models.CharField(max_length=20, default=""),
                    "Meta": type("Meta", (), {"app_label": "tests"}),
                    **attrs,
                },
            )

        def form_over(model):
            form_class = forms.modelform_factory(model, fields=["name", "api_key"])
            return form_class(instance=model(name="n", api_key="stored-value"))

        outside = form_over(build("AllowlistOutside", djust_serializable_fields=["name"]))
        assert serialization.form_field_value_withheld(outside, "api_key")
        assert not serialization.form_field_value_withheld(outside, "name")
        assert FormMixin._initial_form_data(outside) == {"name": "n", "api_key": ""}

        inside = form_over(build("AllowlistInside", djust_serializable_fields=["name", "api_key"]))
        assert not serialization.form_field_value_withheld(inside, "api_key")
        assert FormMixin._initial_form_data(inside)["api_key"] == "stored-value"

        plain = form_over(build("AllowlistNone"))
        assert not serialization.form_field_value_withheld(plain, "api_key")

    def test_the_helper_agrees_with_the_floor_predicate(self):
        form = EditForm(instance=make_user())
        assert serialization.form_field_value_withheld(form, "password", form.fields["password"])
        for name in ("username", "email", "first_name"):
            assert not serialization.form_field_value_withheld(form, name, form.fields[name])


# ---------------------------------------------------------------------------------------------
# The submit of a withheld field that stays editable (a password-widget field, a form built by a
# ``get_form()`` that skipped ``super()``): a blank or absent value keeps the stored column.
class PwWidgetForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "password"]


class PwWidgetRequiredForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=True)

    class Meta:
        model = User
        fields = ["username", "password"]


class PwEchoForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(render_value=True), required=False)

    class Meta:
        model = User
        fields = ["username", "password"]


class PlainSensitiveForm(forms.ModelForm):
    """A secret-named text field and a ``get_form()`` override that never reaches ``super()``."""

    password = forms.CharField(required=False)

    class Meta:
        model = User
        fields = ["username", "password"]


def view_for(form_class, **attrs):
    return type("V", (EditView,), {"form_class": form_class, **attrs})


def column(user):
    return User.objects.get(pk=user.pk).password


class TestAnEditFormKeepsAWithheldColumnWhenTheSubmissionIsBlank:
    @pytest.mark.parametrize(
        "form_class",
        [PwWidgetForm, PwWidgetRequiredForm, PwEchoForm],
        ids=["opt", "required", "echo"],
    )
    @pytest.mark.parametrize(
        "posted", [None, "", "   ", "\t\n"], ids=["absent", "empty", "spaces", "ws"]
    )
    def test_blank_or_absent_keeps_the_column(self, form_class, posted):
        user = make_user()
        client = LiveViewTestClient(view_for(form_class))
        client.mount(pk=user.pk)
        params = {"username": "alice2"}
        if posted is not None:
            params["password"] = posted
        client.send_event("submit_form", **params)
        view = client.view_instance
        assert view.is_valid is True, (
            view.field_errors
        )  # a required untouched secret is not invalid
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert column(user) == STORED
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0

    @pytest.mark.parametrize("form_class", [PwWidgetForm, PwWidgetRequiredForm, PwEchoForm])
    def test_a_typed_value_still_writes(self, form_class):
        """A person can change a password through a password-widget field."""
        user = make_user()
        client = LiveViewTestClient(view_for(form_class))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice", password="a-new-one")
        assert client.view_instance.is_valid is True
        assert column(user) == "a-new-one"
        assert client.view_instance.form_data["password"] == ""

    def test_a_get_form_override_that_skips_super_keeps_the_column(self):
        user = make_user()

        def get_form(self, form_class=None):
            return PlainSensitiveForm(**self.get_form_kwargs())

        client = LiveViewTestClient(view_for(PlainSensitiveForm, get_form=get_form))
        client.mount(pk=user.pk)
        assert client.view_instance.form_data["password"] == ""
        client.send_event("submit_form", username="alice2")
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert column(user) == STORED

    def test_a_create_form_is_not_touched(self):
        """Nothing is stored on a new row: blank saves blank, a typed value saves."""
        for posted, expected in (("", ""), ("chosen", "chosen")):
            client = LiveViewTestClient(type("C", (CreateView,), {"form_class": PwWidgetForm}))
            client.mount()
            name = f"new-{expected or 'blank'}"
            client.send_event("submit_form", username=name, password=posted)
            assert client.view_instance.is_valid is True
            assert User.objects.get(username=name).password == expected

    def test_validate_field_neither_echoes_nor_wipes(self):
        user = make_user()
        client = LiveViewTestClient(view_for(PwWidgetForm))
        client.mount(pk=user.pk)
        view = client.view_instance
        # a blur on the untouched, empty box validates "" and wipes nothing
        client.send_event("validate_field", field="password", value="")
        assert_not_prefilled(view, client)
        client.send_event("submit_form", username="alice2")
        assert column(user) == STORED
        assert_not_prefilled(view, client)
        # a draft the person typed is theirs: it is not the stored value, and a submit writes it
        client.send_event("validate_field", field="password", value="typed-draft")
        assert_not_prefilled(view, client)
        assert view.get_field_value("password") == "typed-draft"

    def test_the_display_widget_field_that_is_not_disabled_is_withheld_and_kept(self):
        from django.contrib.auth.forms import ReadOnlyPasswordHashWidget

        class HashEditable(forms.ModelForm):
            password = forms.CharField(widget=ReadOnlyPasswordHashWidget, required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        user = make_user()
        client = LiveViewTestClient(view_for(HashEditable))
        client.mount(pk=user.pk)
        view = client.view_instance
        assert_not_prefilled(view, client)
        client.send_event("submit_form", username="alice2")
        assert column(user) == STORED

    def test_the_admin_view_keeps_the_column_and_lets_a_typed_value_write(self):
        user = make_user()
        view = TestAdminFormView()._view(user, PwWidgetForm)
        view.submit_form(username="alice2")
        assert view.is_valid is True, view.field_errors
        assert column(user) == STORED
        view = TestAdminFormView()._view(user, PwWidgetForm)
        view.submit_form(username="alice2", password="typed")
        assert column(user) == "typed"

    def test_a_wizard_step_has_no_stored_row(self):
        """Wizard forms are plain ``Form``s: nothing is stored under a field, so there is no column
        to keep and :func:`keep_withheld_columns_when_blank` is a no-op for them."""
        from djust.serialization import keep_withheld_columns_when_blank

        form = WizardStepForm(data={"nick": "x"})
        keep_withheld_columns_when_blank(form)
        assert not any(f.disabled for f in form.fields.values())


# ---------------------------------------------------------------------------------------------
# State saved by an earlier version may still hold the stored value.
def restore_public_state(view, saved_state):
    """What every restore of saved public state does (session, snapshot, sticky child, HTTP
    fallback, reconnect): ``safe_setattr`` each public key. The scrub hook lives there."""
    from djust.security import safe_setattr

    applied = {}
    for key, value in saved_state.items():
        if safe_setattr(view, key, value, allow_private=False):
            applied[key] = getattr(view, key)
    return applied


class TestRestoredEarlierStateIsCleaned:
    def earlier_state(self, view):
        return {"form_data": dict(view.form_data, password=STORED), "is_valid": False}

    def test_restore_public_state_scrubs_it(self):
        client, view = mount_edit(make_user())
        applied = restore_public_state(view, self.earlier_state(view))
        assert json.dumps(applied).count(STORED) == 0
        assert_not_prefilled(view, client)
        assert view.form_data["email"] == "alice@example.org"

    def test_the_next_event_does_not_write_it_back(self):
        client, view = mount_edit(make_user())
        restore_public_state(view, self.earlier_state(view))
        client.send_event("validate_field", field="username", value="alice9")
        assert_not_prefilled(view, client)
        assert json.dumps(view._capture_snapshot_state(), default=str).count(STORED) == 0

    def test_the_signed_snapshot_restore_goes_through_the_same_place(self):
        client, view = mount_edit(make_user())
        view._restore_snapshot(self.earlier_state(view))
        assert_not_prefilled(view, client)

    def test_the_admin_view_scrubs_it_too(self):
        view = TestAdminFormView()._view(make_user())
        restore_public_state(view, {"form_data": dict(view.form_data, password=STORED)})
        assert view.form_data["password"] == ""
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0

    def test_a_view_whose_form_class_cannot_be_resolved_loses_only_sensitive_named_keys(self):
        class V(FormMixin, LiveView):
            template = "<div dj-root></div>"

            def get_form_class(self):
                raise RuntimeError("needs state")

        view = V()
        view.form_data = {}
        restore_public_state(view, {"form_data": {"password": STORED, "city": "Oslo"}})
        assert view.form_data == {"password": "", "city": "Oslo"}

    def test_the_http_event_fallback_restores_an_earlier_session_without_the_value(self):
        from django.contrib.sessions.backends.db import SessionStore

        user = make_user()
        view_class = view_for(EditForm)
        session = SessionStore()
        session.create()
        factory = RequestFactory()
        request = factory.get("/x/")
        request.user = user
        request.session = session
        view_class.as_view()(request, pk=user.pk)
        keys = [
            k for k in session.keys() if k.startswith("liveview_") and isinstance(session[k], dict)
        ]
        blobs = [k for k in keys if "form_data" in session[k]]
        assert blobs, list(session.keys())  # the earlier shape is really there to be doctored
        for key in blobs:
            blob = session[key]
            blob["form_data"]["password"] = STORED
            session[key] = blob
        session.save()
        post = factory.post(
            "/x/",
            data=json.dumps(
                {"event": "validate_field", "params": {"field": "username", "value": "z"}}
            ),
            content_type="application/json",
            HTTP_X_DJUST_EVENT="validate_field",
        )
        post.user = user
        post.session = session
        response = view_class.as_view()(post, pk=user.pk)
        assert response.status_code == 200
        assert response.content.decode().count(STORED) == 0
        saved = json.dumps(
            {k: session[k] for k in session.keys() if k.startswith("liveview_")}, default=str
        )
        assert saved.count(STORED) == 0

    def test_the_restore_callers_are_the_ones_this_was_checked_against(self):
        """Every restore of saved public state sets attributes through ``safe_setattr`` (where the
        scrub hook lives): a new caller is a new thing to check."""
        import pathlib
        import re

        root = pathlib.Path(serialization.__file__).parent
        found = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "tests" not in path.parts
            and re.search(r"safe_setattr\(", path.read_text())
            and path.parent.name != "security"
        )
        assert found == [
            "live_view.py",
            "mixins/request.py",
            "mixins/sticky.py",
            "runtime.py",
            "time_travel.py",
            "websocket.py",  # the server-push ``state`` sink
        ]


class TestTheReadTimeGuard:
    """``get_field_value`` / ``as_live_field`` never print the STORED value, whatever put it in
    ``form_data``; a typed value is returned as typed."""

    def test_a_form_data_holding_the_stored_value_is_not_printed(self):
        client, view = mount_edit(make_user())
        view.form_data["password"] = STORED
        assert view.get_field_value("password") == ""
        assert view.as_live_field("password").count(STORED) == 0
        assert view.as_live().count(STORED) == 0

    def test_what_was_typed_is_returned_as_typed(self):
        client, view = mount_edit(make_user())
        view.form_data["password"] = "typed-draft"
        assert view.get_field_value("password") == "typed-draft"

    def test_other_fields_are_untouched(self):
        client, view = mount_edit(make_user())
        assert view.get_field_value("email") == "alice@example.org"

    def test_the_admin_view_guards_too(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = STORED
        assert view.get_field_value("password") == ""


class TestKeepWithheldColumnsWhenBlankDirectly:
    """The helper on its own: an ABSENT key (a bound form whose data never mentioned the field) and a
    blank one are the same, and a typed value is not."""

    def form(self, data):
        return PwWidgetForm(data, instance=make_user())

    @pytest.mark.parametrize("data", [{"username": "x"}, {"username": "x", "password": " "}])
    def test_absent_and_blank_disable_the_field(self, data):
        from djust.serialization import keep_withheld_columns_when_blank

        form = self.form(data)
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is True
        assert form.is_valid()
        assert form.save().password == STORED

    def test_a_typed_value_leaves_it_editable(self):
        from djust.serialization import keep_withheld_columns_when_blank

        form = self.form({"username": "x", "password": "typed"})
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False

    def test_a_form_with_a_prefix_is_asked_under_its_prefixed_name(self):
        from djust.serialization import keep_withheld_columns_when_blank

        user = make_user()
        form = PwWidgetForm({"u-username": "x"}, prefix="u", instance=user)
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is True
        form = PwWidgetForm({"u-username": "x", "u-password": "typed"}, prefix="u", instance=user)
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False

    def test_an_unbound_form_is_left_alone(self):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PwWidgetForm(instance=make_user())
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False


def test_the_admin_restore_scrub_asks_the_admin_form_not_the_field_name():
    """``phrase`` is not a secret NAME: only the admin form's password widget makes it one."""

    class Phrase(forms.ModelForm):
        phrase = forms.CharField(widget=forms.PasswordInput, required=False)

        class Meta:
            model = User
            fields = ["username"]

    view = TestAdminFormView()._view(make_user(), Phrase)
    restore_public_state(view, {"form_data": {"phrase": "found", "username": "alice"}})
    assert view.form_data["phrase"] == ""
    assert view.form_data["username"] == "alice"


def test_the_admin_sensitive_text_field_keeps_the_column_when_blank_and_writes_when_typed():
    """The same policy as the ``FormMixin`` one: blank keeps the column, a typed value writes."""
    user = make_user()
    view = TestAdminFormView()._view(user, EditForm)
    view.submit_form(username="alice2", email="alice@example.org", password="")
    assert view.is_valid is True, view.field_errors
    assert column(user) == STORED
    view = TestAdminFormView()._view(user, EditForm)
    view.submit_form(username="alice2", email="alice@example.org", password="typed")
    assert column(user) == "typed"


class TestLowItemsOfTheSecondReview:
    def test_the_read_time_guard_rebuilds_a_lost_form(self):
        """``_form_instance`` is private and not restored: the guard must not depend on it."""
        client, view = mount_edit(make_user())
        view.form_data["password"] = STORED
        view._form_instance = None
        assert view.get_field_value("password") == ""
        view._form_instance = None
        assert view.as_live_field("password").count(STORED) == 0

    @pytest.mark.parametrize("blank", [[], {}, 0, 0.0, False, None, "", "  "], ids=repr)
    def test_every_falsy_value_is_blank(self, blank):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PwWidgetForm({"username": "x", "password": blank}, instance=make_user())
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is True
        assert form.is_valid()
        assert form.save().password == STORED

    @pytest.mark.parametrize("typed", ["x", " x ", 1, True, ["a"], {"a": 1}], ids=repr)
    def test_a_truthy_value_is_not_blank(self, typed):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PwWidgetForm({"username": "x", "password": typed}, instance=make_user())
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False

    @pytest.mark.parametrize("blank", [[], {}, 0, False])
    def test_a_crafted_falsy_value_over_the_event_path_keeps_the_column(self, blank):
        user = make_user()
        client = LiveViewTestClient(view_for(PwWidgetForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2", password=blank)
        assert column(user) == STORED

    def test_a_create_form_with_a_credential_like_initial_and_a_blank_submit_saves_blank(self):
        """Nothing is stored on a new row: the keep rule must not apply, or the initial the live
        renderers never showed would be saved in place of what was (not) typed."""

        class Generated(forms.ModelForm):
            password = forms.CharField(widget=forms.PasswordInput, required=False, initial="gen-1")

            class Meta:
                model = User
                fields = ["username", "password"]

        client = LiveViewTestClient(type("C", (CreateView,), {"form_class": Generated}))
        client.mount()
        view = client.view_instance
        assert view.form_data["password"] == ""
        assert view.as_live().count("gen-1") == 0
        client.send_event("submit_form", username="gen-user", password="")
        assert view.is_valid is True
        assert User.objects.get(username="gen-user").password == ""

    def test_the_keep_helper_ignores_a_form_over_a_new_row(self):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PwWidgetForm({"username": "x"}, instance=User(username="x"))
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False


# ---------------------------------------------------------------------------------------------
# The restore paths that do not go through the HTTP fallback or the signed snapshot, and the hook's
# own contract.
class RuntimeFormView(FormMixin, LiveView):
    """An opt-in view: its session-saved state is restored in lieu of ``mount()``."""

    form_class = EditForm
    enable_state_snapshot = True
    template = '<div dj-root dj-id="0">{{ form_data.email }}</div>'

    def mount(self, request, **kwargs):
        self._model_instance = User.objects.get(username="alice")
        super().mount(request, **kwargs)


class TestEveryRestoreSinkScrubs:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_the_runtime_session_restore_on_a_reconnect_mount(self):
        from asgiref.sync import sync_to_async

        from djust.tests.test_runtime_mount_state_restore_1913 import (
            PAGE_URL,
            _make_db_session,
            _make_request,
            _make_runtime,
        )

        await sync_to_async(make_user)()
        session = await sync_to_async(_make_db_session)()
        view_key = f"liveview_{PAGE_URL}"
        await session.aset(
            view_key,
            {
                "form_data": {
                    "username": "alice",
                    "email": "alice@example.org",
                    "first_name": "Alice",
                    "password": STORED,
                }
            },
        )
        await sync_to_async(session.save)()
        runtime, transport = _make_runtime(_make_request(session))
        with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
            await runtime.dispatch_mount(
                {"type": "mount", "view": f"{__name__}.RuntimeFormView", "url": PAGE_URL}
            )
        view = runtime.view_instance
        assert view is not None and view._mounted_from_restore is True
        assert view.form_data["email"] == "alice@example.org"  # the restore really ran
        assert view.form_data["password"] == ""
        assert json.dumps(transport.sent, default=str).count(STORED) == 0
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0

    def test_a_server_push_state_is_scrubbed_too(self):
        import asyncio

        from djust.websocket import LiveViewConsumer

        client, view = mount_edit(make_user())
        consumer = LiveViewConsumer()
        asyncio.run(
            consumer._apply_server_push(
                view, {"state": {"form_data": dict(view.form_data, password=STORED)}}
            )
        )
        assert view.form_data["password"] == ""
        assert view.form_data["email"] == "alice@example.org"

    def test_safe_setattr_itself_scrubs_a_form_data(self):
        from djust.security import safe_setattr

        _, view = mount_edit(make_user())
        assert safe_setattr(view, "form_data", dict(view.form_data, password=STORED))
        assert view.form_data["password"] == ""


class TestTheScrubHookContract:
    def test_it_is_looked_up_on_the_class_so_a_catch_all_object_is_not_asked(self):
        from unittest.mock import MagicMock

        from djust.security import safe_setattr

        mock = MagicMock()
        sentinel = {"password": "x"}
        assert safe_setattr(mock, "form_data", sentinel)
        assert mock.form_data is sentinel

    def test_only_form_data_is_ever_touched(self):
        _, view = mount_edit(make_user())
        assert view._djust_scrub_restored("is_valid", True) is True
        assert view._djust_scrub_restored("form_data", "not a dict") == "not a dict"
        # another attribute that happens to be a dict with a withheld NAME in it is not form_data
        other = {"password": "not a form value"}
        assert view._djust_scrub_restored("saved_prefs", other) is other

    def test_a_scrub_that_has_nothing_to_remove_returns_the_same_object(self):
        _, view = mount_edit(make_user())
        saved = {"username": "a", "email": "b"}
        assert view._djust_scrub_restored("form_data", saved) is saved


# ---------------------------------------------------------------------------------------------
# The predicate itself: what makes a field secret-like, and what must keep its value.
class TestThePredicate:
    @pytest.mark.parametrize(
        "name",
        [
            "password",
            "new_password",
            "newpassword",
            "passwd",
            "client_secret",
            "api_token",
            "apiToken",
            "apikey",
            "auth_key",
            "AuthKey",
            "pwd_hash",
            "passphrase",
            "pwd",
            "salt",
            "credentials",
            "otp",
            "signature",
            "pin",
        ],
    )
    def test_credential_like_names(self, name):
        assert serialization._name_is_secret_like(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "email",
            "first_name",
            "username",
            "is_staff",
            "is_superuser",
            "monkey",
            "keyboard",
            "spin",
        ],
    )
    def test_ordinary_names(self, name):
        assert serialization._name_is_secret_like(name) is False

    def test_a_password_input_is_a_password_widget_whatever_it_is_called(self):
        from types import SimpleNamespace

        assert serialization.field_has_password_widget(
            SimpleNamespace(widget=forms.PasswordInput())
        )

        class Typed(forms.TextInput):
            input_type = "password"

        assert serialization.field_has_password_widget(SimpleNamespace(widget=Typed()))
        assert not serialization.field_has_password_widget(
            SimpleNamespace(widget=forms.TextInput())
        )

    def test_a_multiwidget_with_a_password_part_is_one(self):
        from types import SimpleNamespace

        with_pw = forms.MultiWidget(widgets=[forms.TextInput(), forms.PasswordInput()])
        without = forms.MultiWidget(widgets=[forms.TextInput(), forms.TextInput()])
        assert serialization.field_has_password_widget(SimpleNamespace(widget=with_pw))
        assert not serialization.field_has_password_widget(SimpleNamespace(widget=without))

    def test_a_column_of_a_configured_sensitive_type_is_withheld(self):
        from djust.config import get_config

        cfg = get_config()
        original = cfg._config.get("sensitive_field_types")
        cfg._config["sensitive_field_types"] = ["EmailField"]
        try:
            _, view = mount_edit(make_user())
        finally:
            cfg._config["sensitive_field_types"] = original
        assert view.form_data["email"] == ""
        assert view.form_data["first_name"] == "Alice"

    def test_an_error_of_an_ordinary_field_keeps_its_message(self):
        user = make_user()
        client, view = mount_edit(user)
        client.send_event("submit_form", username="x" * 300, email="alice@example.org")
        assert view.is_valid is False
        message = " ".join(str(e) for e in view.field_errors["username"])
        assert "300" in message and "at most" in message


# ---------------------------------------------------------------------------------------------
# A kept (disabled) field reads as the blank it was posted, and the column is still untouched.
class SetPasswordIdiomForm(forms.ModelForm):
    """The usual "blank = leave the password alone" form."""

    password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "password"]

    def save(self, commit=True):
        user = super().save(commit=False)
        typed = self.cleaned_data.get("password")
        if typed:
            user.set_password(typed)
        if commit:
            user.save()
        return user


class ConfirmForm(forms.ModelForm):
    """``clean()`` compares the field with a confirmation box."""

    password = forms.CharField(widget=forms.PasswordInput, required=False)
    password2 = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "password"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("password2"):
            raise forms.ValidationError("The two password fields did not match.")
        return cleaned


class TestAKeptFieldReadsAsBlank:
    def hashed_user(self):
        from django.contrib.auth.hashers import make_password

        return User.objects.create(username="alice", password=make_password("oldpw"))

    def test_a_blank_submit_does_not_set_the_stored_value_as_the_new_password(self):
        """``cleaned_data["password"]`` was the stored value (a disabled field cleans to its initial
        value), so ``if typed: set_password(typed)`` made the old STORED the new password."""
        user = self.hashed_user()
        client = LiveViewTestClient(view_for(SetPasswordIdiomForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2", password="")
        assert client.view_instance.is_valid is True, client.view_instance.field_errors
        user.refresh_from_db()
        assert user.username == "alice2"
        assert user.check_password("oldpw") is True
        assert user.check_password(user.password) is False

    def test_a_typed_password_still_sets_the_new_one(self):
        user = self.hashed_user()
        client = LiveViewTestClient(view_for(SetPasswordIdiomForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice", password="newpw")
        user.refresh_from_db()
        assert user.check_password("newpw") is True

    def test_the_admin_path_reads_it_as_blank_too(self):
        user = self.hashed_user()
        view = TestAdminFormView()._view(user, SetPasswordIdiomForm)
        view.submit_form(username="alice2", password="")
        assert view.is_valid is True, view.field_errors
        user.refresh_from_db()
        assert user.check_password("oldpw") is True

    def test_a_clean_comparing_with_a_confirmation_box_sees_blank(self):
        user = self.hashed_user()
        client = LiveViewTestClient(view_for(ConfirmForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2", password="", password2="")
        assert client.view_instance.is_valid is True, client.view_instance.field_errors
        user.refresh_from_db()
        assert user.check_password("oldpw") is True

    def test_the_contract_directly(self):
        from djust.serialization import keep_withheld_columns_when_blank

        user = self.hashed_user()
        stored = user.password
        form = SetPasswordIdiomForm({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(form)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["password"] == ""  # read as the blank it was posted
        assert form.instance.password == stored  # the construction of the instance kept the column
        assert form.save().password == stored

    def test_a_clean_that_assigns_the_field_owns_it(self):
        from djust.serialization import keep_withheld_columns_when_blank

        class Assigns(SetPasswordIdiomForm):
            def clean(self):
                cleaned = super().clean()
                cleaned["password"] = "assigned-by-clean"
                return cleaned

        user = self.hashed_user()
        form = Assigns({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(form)
        assert form.is_valid()
        assert form.cleaned_data["password"] == "assigned-by-clean"


class PairWidget(forms.MultiWidget):
    def __init__(self, attrs=None):
        super().__init__(widgets=[forms.PasswordInput, forms.PasswordInput], attrs=attrs)

    def decompress(self, value):
        return [value, ""] if value else [None, None]


class PairField(forms.MultiValueField):
    widget = PairWidget

    def __init__(self, **kwargs):
        super().__init__(
            fields=[forms.CharField(required=False), forms.CharField(required=False)],
            require_all_fields=False,
            **kwargs,
        )

    def compress(self, data_list):
        return (data_list[0] if data_list else "") or ""


class PairForm(forms.ModelForm):
    password = PairField(required=False)

    class Meta:
        model = User
        fields = ["username", "password"]


class TestAMultiWidgetSensitive:
    def test_all_blank_parts_keep_the_column(self):
        from djust.serialization import keep_withheld_columns_when_blank

        user = make_user()
        form = PairForm({"username": "alice", "password_0": "", "password_1": ""}, instance=user)
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is True
        assert form.is_valid(), form.errors
        assert form.save().password == STORED

    def test_a_typed_part_leaves_it_editable(self):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PairForm(
            {"username": "alice", "password_0": "typed", "password_1": ""}, instance=make_user()
        )
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False


class TestTheGuardWithoutAForm:
    """``_form_instance`` is private and is not restored."""

    def test_the_admin_view_rebuilds_the_form_for_the_read_time_guard(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = STORED
        view._form_instance = None
        assert view.get_field_value("password") == ""
        assert view.get_field_value("email") == "alice@example.org"

    def test_what_was_typed_in_the_admin_view_survives_a_lost_form(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = "typed-draft"
        view._form_instance = None
        assert view.get_field_value("password") == "typed-draft"

    def test_no_form_at_all_falls_back_to_the_form_class_in_the_admin_view(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = STORED
        view.form_data["email"] = "alice@example.org"
        view._form_instance = None

        def broken(*args, **kwargs):
            raise RuntimeError("no request here")

        view._create_form = broken
        # the class still resolves (the ModelAdmin answers), so only the class's withheld names go
        assert view.get_field_value("password") == ""
        assert view.get_field_value("email") == "alice@example.org"

    def test_nothing_resolves_so_a_credential_like_name_is_withheld(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = STORED
        view.form_data["city"] = "Oslo"
        view._form_instance = None

        def broken(*args, **kwargs):
            raise RuntimeError("nothing here")

        view._create_form = broken
        view._model_admin.get_form = broken
        assert view.get_field_value("password") == ""
        assert view.get_field_value("city") == "Oslo"

    def test_a_formmixin_view_whose_form_cannot_be_built_guards_by_class(self):
        client, view = mount_edit(make_user())
        view.form_data["password"] = STORED

        def broken(*args, **kwargs):
            raise RuntimeError("cannot build")

        view._form_instance = None
        view._create_form = broken
        assert view.get_field_value("password") == ""
        assert view.get_field_value("email") == "alice@example.org"


# ---------------------------------------------------------------------------------------------
# Third round: a form that should have a stored row but has none, a many-to-many, copies of a form,
# and the raw errors a developer's ``form_invalid(form)`` receives.
class TestTheGuardWhenTheStoredRowIsGone:
    """The form CAN be rebuilt, but over a blank instance: its initial is ``""``, so comparing the
    value with it would print the stored value. A view that says a row should exist guards by class."""

    def test_a_base_view_whose_instance_cannot_be_rehydrated(self):
        class V(FormMixin, LiveView):
            form_class = EditForm
            template = "<div dj-root></div>"

        view = V()
        view._model_instance = None
        view.model_pk = 999999
        view.model_label = "auth.User"
        view.form_data = {"password": STORED, "email": "e"}
        view.field_errors = {}
        assert view.get_field_value("password") == ""
        assert str(view.as_live_field("password")).count(STORED) == 0
        assert view.get_field_value("email") == "e"

    def test_the_admin_view_after_a_restore_that_lost_the_form_and_the_object(self):
        view = TestAdminFormView()._view(make_user())
        view._form_instance = None
        view.object = None
        view.form_data["password"] = STORED
        assert view.get_field_value("password") == ""
        assert view.get_field_value("email") == "alice@example.org"

    def test_a_genuine_create_view_keeps_a_typed_draft(self):
        client = LiveViewTestClient(CreateView)
        client.mount()
        view = client.view_instance
        view.form_data["password"] = "typed-draft"
        view._form_instance = None
        assert view.model_pk is None
        assert view.get_field_value("password") == "typed-draft"

    def test_an_admin_create_view_keeps_a_typed_draft(self):
        from djust.admin_ext.forms import AdminFormMixin

        class Admin:
            def get_form(self, request, obj=None):
                return EditForm

        class View(AdminFormMixin, LiveView):
            _model = User
            _model_admin = Admin()

        view = View()
        view.mount(RequestFactory().get("/admin/"))
        view.form_data["password"] = "typed-draft"
        view._form_instance = None
        assert view.get_field_value("password") == "typed-draft"


class TestAManyToManyIsNotBlanked:
    def test_an_untouched_credential_like_m2m_keeps_its_relation(self, monkeypatch):
        from django.contrib.auth.models import Group

        user = make_user()
        group = Group.objects.create(name="staff-g")
        user.groups.add(group)
        original = serialization._name_is_secret_like
        monkeypatch.setattr(
            serialization, "_name_is_secret_like", lambda n: n == "groups" or original(n)
        )

        class M(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "groups"]

        with override_settings(DJUST_SENSITIVE_FIELDS=["groups"]):
            client = LiveViewTestClient(view_for(M))
            client.mount(pk=user.pk)
            client.send_event("submit_form", username="m2m")
        assert client.view_instance.is_valid is True, client.view_instance.field_errors
        assert list(User.objects.get(pk=user.pk).groups.values_list("name", flat=True)) == [
            "staff-g"
        ]


class TestACopiedForm:
    """``copy.copy`` / ``copy.deepcopy`` of a form that has not been validated, then ``full_clean``."""

    @pytest.mark.parametrize("how", ["copy", "deepcopy"])
    def test_the_copy_is_validated_on_its_own(self, how):
        import copy

        from djust.serialization import keep_withheld_columns_when_blank

        user = make_user()
        form = SetPasswordIdiomForm({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(form)
        duplicate = getattr(copy, how)(form)
        assert duplicate.is_valid(), duplicate.errors
        assert duplicate.cleaned_data["password"] == ""
        assert duplicate.instance.password == STORED
        assert not hasattr(form, "cleaned_data")  # the original was not validated through the copy
        assert form.is_valid(), form.errors
        assert form.cleaned_data["password"] == ""

    def test_the_class_of_other_forms_is_not_changed(self):
        from djust.serialization import keep_withheld_columns_when_blank

        user = make_user()
        form = SetPasswordIdiomForm({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(form)
        other = SetPasswordIdiomForm({"username": "alice", "password": "x"}, instance=user)
        assert type(form) is not SetPasswordIdiomForm
        assert isinstance(form, SetPasswordIdiomForm)
        assert type(other) is SetPasswordIdiomForm  # a form that kept nothing is a plain one
        assert "_clean_form" not in vars(SetPasswordIdiomForm)
        again = SetPasswordIdiomForm({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(again)
        assert type(again) is type(form)  # the derived class is made once per class


class TestTheDeveloperSeesNoValueBearingError:
    def test_form_invalid_receives_generic_errors_for_a_withheld_field(self):
        seen = {}

        class ChoiceForm(forms.ModelForm):
            password = forms.ChoiceField(choices=[("x", "X")], required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        def form_invalid(self, form):
            seen["errors"] = json.dumps(
                {k: [str(e) for e in v] for k, v in form.errors.items()}, default=str
            )

        user = make_user()
        client = LiveViewTestClient(view_for(ChoiceForm, form_invalid=form_invalid))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        assert client.view_instance.is_valid is False
        assert "password" in seen["errors"]
        assert seen["errors"].count(STORED) == 0

    def test_the_admin_form_invalid_hook_too(self):
        seen = {}

        class ChoiceForm(forms.ModelForm):
            password = forms.ChoiceField(choices=[("x", "X")], required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

        view = TestAdminFormView()._view(make_user(), ChoiceForm)

        def form_invalid(form):
            seen["errors"] = json.dumps(
                {k: [str(e) for e in v] for k, v in form.errors.items()}, default=str
            )

        view.form_invalid = form_invalid
        view.submit_form(username="alice")
        assert "password" in seen["errors"]
        assert seen["errors"].count(STORED) == 0

    def test_an_ordinary_fields_errors_are_untouched(self):
        seen = {}

        def form_invalid(self, form):
            seen["errors"] = json.dumps(
                {k: [str(e) for e in v] for k, v in form.errors.items()}, default=str
            )

        client = LiveViewTestClient(view_for(EditForm, form_invalid=form_invalid))
        user = make_user()
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="x" * 300)
        assert "at most" in seen["errors"]


# ---------------------------------------------------------------------------------------------
# Fourth round: the derived class must not keep a per-call form class alive; an error message that
# carries the stored value is generic.
class TestPerCallFormClassesAreCollectable:
    def test_a_class_built_per_call_is_freed(self):
        import gc
        import weakref

        from djust.serialization import keep_withheld_columns_when_blank

        user = make_user()
        refs = []
        for _ in range(20):
            cls = forms.modelform_factory(User, form=PwWidgetForm, fields=["username", "password"])
            form = cls({"username": "alice"}, instance=user)
            keep_withheld_columns_when_blank(form)
            assert form.fields["password"].disabled is True
            refs.append(weakref.ref(cls))
            del cls, form
        gc.collect()
        assert [r() for r in refs] == [None] * 20

    def test_admin_submits_that_build_a_class_per_call_grow_no_module_container(self):
        import weakref

        from djust.admin_ext.forms import AdminFormMixin

        user = make_user()
        built = []

        class Admin:
            def get_object(self, request, object_id):
                return user

            def get_form(self, request, obj=None):
                cls = forms.modelform_factory(
                    User, form=PwWidgetForm, fields=["username", "password"]
                )
                built.append(weakref.ref(cls))
                return cls

        class View(AdminFormMixin, LiveView):
            _model = User
            _model_admin = Admin()

            def form_valid(self, form):
                self.saved = form.save()

        def submit():
            view = View()
            view.mount(RequestFactory().get("/admin/"), object_id=str(user.pk))
            view.submit_form(username="alice")
            assert view.is_valid is True, view.field_errors

        def sizes():
            containers = (dict, set, list, frozenset)
            return {
                name: len(value)
                for name, value in vars(serialization).items()
                if isinstance(value, containers)
            }

        submit()  # warm up whatever is memoised on first use
        import gc

        gc.collect()
        before = sizes()
        for _ in range(10):
            submit()
        gc.collect()
        assert sizes() == before
        assert column(user) == STORED
        assert sum(1 for ref in built if ref() is not None) <= 1  # the classes are freed

    def test_a_subclass_does_not_inherit_its_parents_derived_class(self):
        from djust.serialization import keep_withheld_columns_when_blank

        class Child(PwWidgetForm):
            pass

        user = make_user()
        parent_form = PwWidgetForm({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(parent_form)
        child_form = Child({"username": "alice"}, instance=user)
        keep_withheld_columns_when_blank(child_form)
        assert type(child_form) is not type(parent_form)
        assert isinstance(child_form, Child)
        assert child_form.is_valid(), child_form.errors
        assert child_form.save().password == STORED

    def test_threads_share_one_derived_class(self):
        import threading

        from djust.serialization import keep_withheld_columns_when_blank

        import time

        user = make_user()

        class Slow(PwWidgetForm):
            """Deriving a class from this one takes a moment: a race has time to happen."""

            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                time.sleep(0.05)

        cls = Slow
        forms_made = [cls({"username": "alice"}, instance=user) for _ in range(16)]
        barrier = threading.Barrier(16)

        def work(form):
            barrier.wait()
            keep_withheld_columns_when_blank(form)

        threads = [threading.Thread(target=work, args=(form,)) for form in forms_made]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len({type(form) for form in forms_made}) == 1


class RaisesWithTheValue(forms.ModelForm):
    """The documented window: ``clean_<name>`` sees the stored value of a kept field."""

    password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "password"]

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if pw:
            raise forms.ValidationError(f"password {pw!r} is not allowed")
        return pw


class RaisesWithParams(RaisesWithTheValue):
    def clean_password(self):
        pw = self.cleaned_data["password"]
        if pw:
            raise forms.ValidationError("bad %(v)s", code="bad", params={"v": pw})
        return pw


class RaisesWithOnlyText(RaisesWithTheValue):
    def clean_password(self):
        if self.cleaned_data["password"]:
            raise forms.ValidationError("Passwords are not editable here.")
        return self.cleaned_data["password"]


class TestAMessageThatCarriesTheStoredValueIsGeneric:
    @pytest.mark.parametrize("form_class", [RaisesWithTheValue, RaisesWithParams])
    def test_every_surface(self, form_class):
        seen = {}

        def form_invalid(self, form):
            seen["raw"] = json.dumps(
                {k: [str(e) for e in v] for k, v in form.errors.items()}, default=str
            )

        user = make_user()
        client = LiveViewTestClient(view_for(form_class, form_invalid=form_invalid))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice", password="")
        view = client.view_instance
        assert view.is_valid is False
        assert "password" in view.field_errors
        assert seen["raw"].count(STORED) == 0
        assert_not_prefilled(view, client)
        assert column(user) == STORED

    def test_the_admin_path(self):
        view = TestAdminFormView()._view(make_user(), RaisesWithTheValue)
        view.submit_form(username="alice", password="")
        assert view.is_valid is False
        assert (
            json.dumps({k: [str(e) for e in v] for k, v in view.field_errors.items()}).count(STORED)
            == 0
        )

    def test_a_message_that_does_not_carry_the_value_stays_specific(self):
        user = make_user()
        client = LiveViewTestClient(view_for(RaisesWithOnlyText))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice", password="")
        errors = " ".join(str(e) for e in client.view_instance.field_errors["password"])
        assert errors == "Passwords are not editable here."

    def test_validate_field_messages_that_carry_the_stored_value_are_generic(self):
        class Echo(forms.ModelForm):
            password = forms.CharField(widget=forms.PasswordInput, required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                raise forms.ValidationError("the stored value is " + self.instance.password)

        user = make_user()
        client = LiveViewTestClient(view_for(Echo))
        client.mount(pk=user.pk)
        client.send_event("validate_field", field="password", value="typed")
        view = client.view_instance
        assert "password" in view.field_errors
        assert_not_prefilled(view, client)

    def test_scrub_error_messages_directly(self):
        form = EditForm(instance=make_user())
        assert serialization.scrub_error_messages(form, "password", [f"no {STORED}"]) == [
            "Enter a valid value."
        ]
        assert serialization.scrub_error_messages(form, "password", ["too long"]) == ["too long"]
        assert serialization.scrub_error_messages(form, "email", [f"no {STORED}"]) == [
            f"no {STORED}"
        ]
        # an ordinary field's message that contains its own value is its own business
        assert serialization.scrub_error_messages(form, "email", ["bad alice@example.org"]) == [
            "bad alice@example.org"
        ]


class EchoStored(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False)

    class Meta:
        model = User
        fields = ["username", "password"]

    def clean_password(self):
        raise forms.ValidationError("the stored value is %r" % self.instance.password)


class TestTheStoredValueNeedsEscapingInItsRepr:
    ODD = "pbkdf2_sha256$1$it's\\a$STORED"

    def test_a_repr_of_a_value_with_quotes_and_backslashes_is_found(self):
        user = User.objects.create(username="odd", password=self.ODD)
        form = EchoStored({"username": "odd"}, instance=user)
        assert not form.is_valid()
        raw = " ".join(form.errors["password"])
        assert self.ODD not in raw  # the repr escaped it: a plain substring test misses it
        scrubbed = serialization.form_errors_without_secret_values(form)
        assert list(scrubbed["password"]) == ["Enter a valid value."]

    def test_the_admin_validate_field_path(self):
        view = TestAdminFormView()._view(make_user(), EchoStored)
        view.validate_field(field="password", value="typed")
        assert (
            json.dumps({k: [str(e) for e in v] for k, v in view.field_errors.items()}).count(STORED)
            == 0
        )
        assert view.field_errors["password"] == ["Enter a valid value."]


# ---------------------------------------------------------------------------------------------
# Fifth round: a re-entrant lock, a class that refuses the cache attribute, and the params branch.
class TestTheDerivationLock:
    def test_a_form_class_whose_init_subclass_derives_another_does_not_deadlock(self, monkeypatch):
        import threading

        from djust.serialization import keep_withheld_columns_when_blank

        # a fresh lock of the SAME kind, so a failure cannot leave the real one held process-wide
        monkeypatch.setattr(
            serialization, "_KEPT_BLANK_LOCK", type(serialization._KEPT_BLANK_LOCK)()
        )
        user = make_user()

        class Nesting(PwWidgetForm):
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                # runs while the outer derivation holds the lock
                fresh = forms.modelform_factory(
                    User, form=PwWidgetForm, fields=["username", "password"]
                )
                keep_withheld_columns_when_blank(fresh({"username": "alice"}, instance=user))

        outcome = {}

        def work():
            form = Nesting({"username": "alice"}, instance=user)
            keep_withheld_columns_when_blank(form)
            outcome["disabled"] = form.fields["password"].disabled

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "the derivation deadlocked on its own lock"
        assert outcome == {"disabled": True}


class _RefusingMeta(forms.models.ModelFormMetaclass):
    refuse = TypeError

    def __setattr__(cls, name, value):
        if name == "_djust_kept_blank_class":
            raise cls.refuse("read-only class")
        super().__setattr__(name, value)


class TestAClassThatRefusesTheCacheAttribute:
    @pytest.mark.parametrize("refuse", [TypeError, AttributeError])
    def test_it_still_works_and_nothing_is_carried(self, refuse):
        import gc
        import weakref

        from djust.serialization import keep_withheld_columns_when_blank

        meta = type("Meta", (_RefusingMeta,), {"refuse": refuse})
        user = make_user()
        refs = []
        for _ in range(5):
            cls = meta(
                "Refusing",
                (PwWidgetForm,),
                {"__module__": __name__},
            )
            form = cls({"username": "alice"}, instance=user)
            keep_withheld_columns_when_blank(form)
            assert form.fields["password"].disabled is True
            assert form.is_valid(), form.errors
            assert form.save().password == STORED
            refs.append(weakref.ref(cls))
            del cls, form
        gc.collect()
        assert [r() for r in refs] == [None] * 5


class RaisesWithATruncatedParam(RaisesWithTheValue):
    def clean_password(self):
        if self.cleaned_data["password"]:
            raise forms.ValidationError(
                "starts with %(v).24s", params={"v": self.cleaned_data["password"]}
            )
        return self.cleaned_data["password"]


class TestTheParamsBranch:
    def test_a_truncated_param_whose_text_lacks_the_full_value_is_still_scrubbed(self):
        user = make_user()
        client = LiveViewTestClient(view_for(RaisesWithATruncatedParam))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice", password="")
        view = client.view_instance
        assert [str(e) for e in view.field_errors["password"]] == ["Enter a valid value."]
        assert STORED[:24] not in json.dumps(view.field_errors, default=str)
        assert STORED[:24] not in str(view.as_live())

    def test_a_message_that_cannot_be_formatted_is_made_generic(self):
        class Unprintable:
            def __str__(self):
                raise RuntimeError("cannot format")

        class Broken(RaisesWithTheValue):
            def clean_password(self):
                if self.cleaned_data["password"]:
                    raise forms.ValidationError(Unprintable())
                return self.cleaned_data["password"]

        form = Broken({"username": "alice", "password": "typed"}, instance=make_user())
        assert not form.is_valid()
        scrubbed = serialization.form_errors_without_secret_values(form)
        assert [str(e) for e in scrubbed["password"]] == ["Enter a valid value."]
