"""Withheld form fields are seeded empty (``FormMixin`` form-data seeding).

``form_data`` is seeded from each field's initial value. A SECRET-LIKE floor field
(:func:`djust.serialization._floor_field_is_secret`) is seeded with ``""`` in ``form_data``, in
``as_live()``, ``as_live_field()``, ``{% live_field %}`` and ``get_field_value()``; every other field is
seeded as it was, and an untouched re-save still leaves the column alone (the form field is
``disabled``).

Every surface is asserted with a COUNT of the stored value, not a truthiness check.
"""

from __future__ import annotations

import copy
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

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

User = get_user_model()
STORED = "pbkdf2_sha256$600000$SALT$STOREDSTORED"


class EditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "password"]


class EditView(FormMixin, LiveView):
    exposure_policy = "legacy"
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
    exposure_policy = "legacy"
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


def assert_not_carried(view, client=None):
    carried = {name: text.count(STORED) for name, text in surfaces(view, client).items()}
    assert carried == {name: 0 for name in carried}, carried


def mount_edit(user):
    client = LiveViewTestClient(EditView)
    client.mount(pk=user.pk)
    return client, client.view_instance


class TestSeeding:
    def test_the_stored_value_is_on_no_surface_after_mount(self):
        client, view = mount_edit(make_user())
        assert_not_carried(view, client)

    def test_form_data_is_exact(self):
        """Counted, not truthy: the withheld field is ``""``, every other field is its stored value."""
        _, view = mount_edit(make_user())
        assert view.form_data == {
            "username": "alice",
            "email": "alice@example.org",
            "first_name": "Alice",
            "password": "",
        }

    def test_the_http_get_body_has_no_stored_value(self):
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

    def test_other_fields_render_as_before(self):
        _, view = mount_edit(make_user())
        html = view.as_live()
        for stored in ("alice", "alice@example.org", "Alice"):
            assert html.count(f'value="{stored}"') == 1, stored

    def test_reset_form_does_not_reseed_the_value(self):
        client, view = mount_edit(make_user())
        client.send_event("reset_form")
        assert_not_carried(view, client)
        assert view.form_data["password"] == ""

    def test_live_field_tag_has_no_stored_value(self):
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
        assert_not_carried(view, client)
        assert view.form_data["password"] == ""

    def test_a_posted_blank_or_forged_password_is_ignored_on_an_edit(self):
        for posted in ("", "changed"):
            user = make_user()
            client, view = mount_edit(user)
            client.send_event(
                "submit_form", username="alice3", email="alice@example.org", password=posted
            )
            assert self.stored(user) == STORED, repr(posted)
            user.delete()

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


class TestErrorsOfWithheldFields:
    def test_a_choice_error_on_the_disabled_field_does_not_carry_the_value(self):
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
        assert_not_carried(view, client)
        assert User.objects.get(pk=user.pk).password == STORED

    def test_validate_field_for_the_withheld_field_carries_no_stored_value(self):
        user = make_user()
        client, view = mount_edit(user)
        client.send_event("validate_field", field="password", value="x" * 300)
        assert "password" in view.field_errors
        assert_not_carried(view, client)


class WsEditView(FormMixin, LiveView):
    exposure_policy = "legacy"
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
            exposure_policy = "legacy"

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
        view._pin_callable_initials()
        return view, view.get_context_data()

    def test_a_password_widget_initial_is_in_neither_form_data_nor_field_html(self):
        view, ctx = self._context()
        assert ctx["form_data"] == {"nick": "nick", "pw": "", "drawn": ""}
        everything = json.dumps([ctx["form_data"], ctx["field_html"], view.wizard_step_data])
        for value in ("hunter2", "generated-9"):
            assert everything.count(value) == 0, value
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
            exposure_policy = "legacy"
            _model = User
            _model_admin = Admin()

            def form_valid(self, form):
                self.saved = form.save()

        view = View()
        view.mount(RequestFactory().get("/admin/"), object_id=str(user.pk))
        return view

    def test_the_seed_and_every_getter_have_no_stored_value(self):
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

    def test_an_admin_choice_error_on_the_disabled_field_does_not_carry_the_value(self):
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


class TestOtherWithheldShapes:
    def test_a_plain_form_password_widget_initial_is_withheld(self):
        class Plain(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput, initial="hunter2")
            name = forms.CharField(initial="n")

        class V(FormMixin, LiveView):
            exposure_policy = "legacy"
            form_class = Plain
            template = "<div dj-root></div>"

        client = LiveViewTestClient(V)
        client.mount()
        view = client.view_instance
        assert view.form_data == {"pw": "", "name": "n"}
        assert view.as_live().count("hunter2") == 0

    def test_a_render_value_password_input_is_withheld_from_form_data_too(self):
        """The widget's own echo is the Django widget's business; ``form_data``, the state, the session
        and the frames are a worse place for a value (the live renderers do not echo it)."""

        class Plain(forms.Form):
            pw = forms.CharField(widget=forms.PasswordInput(render_value=True), initial="hunter2")

        class V(FormMixin, LiveView):
            exposure_policy = "legacy"
            form_class = Plain
            template = "<div dj-root></div>"

        client = LiveViewTestClient(V)
        client.mount()
        view = client.view_instance
        assert view.form_data == {"pw": ""}
        assert json.dumps(view.get_state(), default=str).count("hunter2") == 0
        assert view.as_live().count("hunter2") == 0

    def test_the_stock_userchangeform_hash_field_is_withheld(self):
        class V(EditView):
            form_class = UserChangeForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        assert_not_carried(client.view_instance, client)

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

    def test_a_declared_sensitive_but_not_secret_like_field_keeps_its_value(self):
        """``email`` / ``first_name`` / ``is_staff`` on the floor are not withheld: drawing them empty
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


class PwRenderValueForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(render_value=True), required=False)

    class Meta:
        model = User
        fields = ["username", "password"]


class PlainNamedForm(forms.ModelForm):
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
        [PwWidgetForm, PwWidgetRequiredForm, PwRenderValueForm],
        ids=["opt", "required", "render_value"],
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
        )  # a required untouched withheld field is not invalid
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert column(user) == STORED
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0

    @pytest.mark.parametrize("form_class", [PwWidgetForm, PwWidgetRequiredForm, PwRenderValueForm])
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
            return PlainNamedForm(**self.get_form_kwargs())

        client = LiveViewTestClient(view_for(PlainNamedForm, get_form=get_form))
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

    def test_a_form_with_a_prefix_keeps_the_column(self):
        class Prefixed(EditView):
            form_class = PwWidgetForm

            def get_prefix(self):
                return "u"

        user = make_user()
        client = LiveViewTestClient(Prefixed)
        client.mount(pk=user.pk)
        client.send_event("submit_form", **{"u-username": "alice2"})
        assert User.objects.get(pk=user.pk).username == "alice2"
        assert column(user) == STORED

    def test_validate_field_neither_returns_nor_wipes_the_stored_value(self):
        user = make_user()
        client = LiveViewTestClient(view_for(PwWidgetForm))
        client.mount(pk=user.pk)
        view = client.view_instance
        # a blur on the untouched, empty box validates "" and wipes nothing
        client.send_event("validate_field", field="password", value="")
        assert_not_carried(view, client)
        client.send_event("submit_form", username="alice2")
        assert column(user) == STORED
        assert_not_carried(view, client)
        # a draft the person typed is theirs: it is not the stored value, and a submit writes it
        client.send_event("validate_field", field="password", value="typed-draft")
        assert_not_carried(view, client)
        assert view.get_field_value("password") == "typed-draft"

    def test_the_hash_widget_field_that_is_not_disabled_is_withheld_and_disabled(self):
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
        assert_not_carried(view, client)
        assert view.form_instance.fields["password"].disabled is True
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
# Earlier saved state is cleaned when it is restored.
class TestRestoredEarlierStateIsCleaned:
    def prefix_state(self, view):
        return {"form_data": dict(view.form_data, password=STORED), "is_valid": False}

    def test_restore_public_state_scrubs_it(self):
        from djust._state_restore import restore_public_state

        client, view = mount_edit(make_user())
        applied = restore_public_state(view, self.prefix_state(view))
        assert json.dumps(applied).count(STORED) == 0
        assert_not_carried(view, client)
        assert view.form_data["email"] == "alice@example.org"

    def test_the_next_event_does_not_write_it_back(self):
        from djust._state_restore import restore_public_state

        client, view = mount_edit(make_user())
        restore_public_state(view, self.prefix_state(view))
        client.send_event("validate_field", field="username", value="alice9")
        assert_not_carried(view, client)
        assert json.dumps(view._capture_snapshot_state(), default=str).count(STORED) == 0

    def test_the_admin_view_scrubs_it_too(self):
        from djust._state_restore import restore_public_state

        view = TestAdminFormView()._view(make_user())
        restore_public_state(view, {"form_data": dict(view.form_data, password=STORED)})
        assert view.form_data["password"] == ""
        assert json.dumps(view.get_state(), default=str).count(STORED) == 0

    def test_a_view_whose_form_class_cannot_be_resolved_loses_only_keys_named_like_one(self):
        from djust._state_restore import restore_public_state

        class V(FormMixin, LiveView):
            exposure_policy = "legacy"
            template = "<div dj-root></div>"

            def get_form_class(self):
                raise RuntimeError("needs state")

        view = V()
        view.form_data = {}
        restore_public_state(view, {"form_data": {"password": STORED, "city": "Oslo"}})
        assert view.form_data == {"password": "", "city": "Oslo"}

    def test_the_restore_callers_are_the_ones_this_was_checked_against(self):
        """Every restore of saved public state passes ``restore_public_state``: a new caller is a new
        thing to check."""
        import pathlib
        import re

        root = pathlib.Path(serialization.__file__).parent
        found = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "tests" not in path.parts
            and re.search(r"restore_public_state\(", path.read_text())
            and path.name != "_state_restore.py"
        )
        # 2.0 removed the page-POST fallback's restore (``mixins/request.py``).
        assert found == [
            "_lazy_containers.py",
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

    def test_an_unbound_form_is_left_alone(self):
        from djust.serialization import keep_withheld_columns_when_blank

        form = PwWidgetForm(instance=make_user())
        keep_withheld_columns_when_blank(form)
        assert form.fields["password"].disabled is False


def test_the_admin_restore_scrub_asks_the_admin_form_not_the_field_name():
    """``phrase`` is not a name that looks secret-like: only the admin form's password widget makes it one."""

    class Phrase(forms.ModelForm):
        phrase = forms.CharField(widget=forms.PasswordInput, required=False)

        class Meta:
            model = User
            fields = ["username"]

    from djust._state_restore import restore_public_state

    view = TestAdminFormView()._view(make_user(), Phrase)
    restore_public_state(view, {"form_data": {"phrase": "changed", "username": "alice"}})
    assert view.form_data["phrase"] == ""
    assert view.form_data["username"] == "alice"


def test_the_admin_withheld_text_field_is_read_only_like_the_formmixin_one():
    """Protection at construction, not just on a blank submission: a secret-named text field of an
    admin edit form ignores what is typed into it (the same policy as ``FormMixin.get_form()``)."""
    user = make_user()
    view = TestAdminFormView()._view(user, EditForm)
    view.submit_form(username="alice2", email="alice@example.org", password="forged")
    assert view.is_valid is True, view.field_errors
    assert column(user) == STORED


class TestEditFormEdgeCases:
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

    def test_a_create_form_with_a_withheld_initial_and_a_blank_submit_saves_blank(self):
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
# A field djust makes ``disabled`` cleans to the STORED value; ``cleaned_data`` must not carry it,
# and a multi-part blank is blank.
REAL_STORED = None


def real_stored():
    from django.contrib.auth.hashers import make_password

    global REAL_STORED
    if REAL_STORED is None:
        REAL_STORED = make_password("oldpw")
    return REAL_STORED


def set_password_form(widget, required=False, seen=None, author_disabled=False):
    """The usual "blank means leave the password alone" idiom, over a secret-named column."""

    kwargs = {"required": required, "disabled": author_disabled}
    if widget is not None:
        kwargs["widget"] = widget

    class PF(forms.ModelForm):
        password = forms.CharField(**kwargs)

        class Meta:
            model = User
            fields = ["username", "password"]

        def clean(self):
            data = super().clean()
            if seen is not None:
                seen.append(data.get("password"))
            return data

        def save(self, commit=True):
            user = super().save(commit=False)
            typed = self.cleaned_data.get("password")
            if typed:
                user.set_password(typed)
            if commit:
                user.save()
            return user

    return PF


def idiom_submit(form_class, **posted):
    user = User.objects.create(username="idiom", password=real_stored())
    client = LiveViewTestClient(view_for(form_class))
    client.mount(pk=user.pk)
    client.send_event("submit_form", **{"username": "idiom", **posted})
    assert client.view_instance.is_valid is True, client.view_instance.field_errors
    user.refresh_from_db()
    return user, client.view_instance


class TestCleanedDataOfAKeptFieldIsBlank:
    @pytest.mark.parametrize("widget", [None, forms.PasswordInput], ids=["text", "passwordinput"])
    @pytest.mark.parametrize("required", [False, True], ids=["optional", "required"])
    def test_a_blank_submit_does_not_re_encode_the_stored_value(self, widget, required):
        seen = []
        user, view = idiom_submit(set_password_form(widget, required, seen), password="")
        assert user.check_password("oldpw") is True
        assert user.password == real_stored()
        assert user.check_password(real_stored()) is False
        assert seen and all(
            value == "" for value in seen
        )  # clean() saw the blank, not the stored value
        assert view.form_instance.cleaned_data["password"] == ""

    @pytest.mark.parametrize("widget", [None, forms.PasswordInput], ids=["text", "passwordinput"])
    def test_an_absent_key_is_the_same(self, widget):
        user, _ = idiom_submit(set_password_form(widget))
        assert user.check_password("oldpw") is True

    def test_a_typed_value_still_goes_through_save(self):
        user, _ = idiom_submit(set_password_form(forms.PasswordInput), password="newpw")
        assert user.check_password("newpw") is True

    def test_an_author_declared_disabled_field_keeps_djangos_own_semantics(self):
        """Only a field djust made ``disabled`` is pinned blank."""
        user, view = idiom_submit(set_password_form(None, author_disabled=True), password="")
        assert view.form_instance.cleaned_data["password"] == real_stored()

    def test_the_admin_view_pins_it_too(self):
        user = User.objects.create(username="idiom", password=real_stored())
        view = TestAdminFormView()._view(user, set_password_form(None))
        view.submit_form(username="idiom", password="")
        assert view.is_valid is True, view.field_errors
        assert view.form_instance.cleaned_data["password"] == ""
        user.refresh_from_db()
        assert user.check_password("oldpw") is True

    def test_a_form_the_helpers_never_touch_is_unchanged(self):
        """The wrappers are instance-level: another form of the same class cleans as Django does."""
        form_class = set_password_form(None)
        user = User.objects.create(username="idiom", password=real_stored())
        protected = form_class({"username": "idiom"}, instance=user)
        serialization.protect_blanked_floor_columns(protected)
        assert protected.is_valid()
        assert protected.cleaned_data["password"] == ""
        plain = form_class({"username": "idiom"}, instance=user)
        assert plain.is_valid()
        assert (
            plain.cleaned_data["password"] == ""
        )  # blank posted, enabled: no stored value involved
        assert "_djust_cleaned_blank" not in plain.__dict__

    def test_protect_is_idempotent(self):
        user = User.objects.create(username="idiom", password=real_stored())
        form = set_password_form(None)({"username": "idiom"}, instance=user)
        serialization.protect_blanked_floor_columns(form)
        pinned = type(form)
        serialization.protect_blanked_floor_columns(form)
        assert type(form) is pinned
        assert form.is_valid()


class MWPassword(forms.MultiWidget):
    def __init__(self):
        super().__init__([forms.PasswordInput(), forms.PasswordInput()])

    def decompress(self, value):
        return [value, value] if value else [None, None]


class MWPasswordField(forms.MultiValueField):
    widget = MWPassword

    def __init__(self, **kwargs):
        super().__init__(
            fields=[forms.CharField(required=False), forms.CharField(required=False)],
            require_all_fields=False,
            required=False,
            **kwargs,
        )

    def compress(self, values):
        return values[0] if values and values[0] else ""


class MWForm(forms.ModelForm):
    password = MWPasswordField()

    class Meta:
        model = User
        fields = ["username", "password"]


class TestAMultiPartBlankIsBlank:
    @pytest.mark.parametrize(
        "value",
        [["", ""], ("", " "), [[], ""], [None, None], []],
        ids=repr,
    )
    def test_all_blank_parts_are_blank(self, value):
        assert serialization._is_blank_submission(value) is True

    @pytest.mark.parametrize(
        "value", [["", "x"], ("a", ""), [["", "x"], ""], {"a": 1}, 3, "x"], ids=repr
    )
    def test_a_part_with_content_is_not_blank(self, value):
        assert serialization._is_blank_submission(value) is False

    def test_a_blank_multiwidget_password_keeps_the_column(self):
        user = make_user()
        client = LiveViewTestClient(view_for(MWForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        assert client.view_instance.is_valid is True, client.view_instance.field_errors
        assert column(user) == STORED

    def test_a_filled_multiwidget_password_writes(self):
        user = make_user()
        client = LiveViewTestClient(view_for(MWForm))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2", password_0="typed", password_1="typed")
        assert column(user) == "typed"


class TestTheAdminGuardWithoutAForm:
    def test_a_lost_form_is_rebuilt_for_the_read_time_guard(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = STORED
        view._form_instance = None
        assert view.get_field_value("password") == ""

    def test_what_was_typed_is_still_returned(self):
        view = TestAdminFormView()._view(make_user())
        view.form_data["password"] = "typed-draft"
        view._form_instance = None
        assert view.get_field_value("password") == "typed-draft"


class TestTwoKindsOfKeptFieldOnOneForm:
    def test_the_protected_and_the_kept_field_are_both_blank_after_clean(self):
        """``get_form()`` pins the text field, ``submit_form`` then pins the password-widget one:
        the second call adds to the first call's set."""

        class Both(forms.ModelForm):
            password = forms.CharField(required=False)
            last_name = forms.CharField(widget=forms.PasswordInput, required=False)

            class Meta:
                model = User
                fields = ["username", "password", "last_name"]

        user = make_user()
        user.last_name = "stored-last-name"
        user.save()
        with override_settings(DJUST_SENSITIVE_FIELDS=["last_name"]):
            client = LiveViewTestClient(view_for(Both))
            client.mount(pk=user.pk)
            client.send_event("submit_form", username="alice2")
        view = client.view_instance
        assert view.is_valid is True, view.field_errors
        cleaned = view.form_instance.cleaned_data
        assert (cleaned["password"], cleaned["last_name"]) == ("", "")
        user.refresh_from_db()
        assert (user.password, user.last_name) == (STORED, "stored-last-name")


def test_a_form_with_nothing_to_keep_gets_no_wrappers():
    class Plain(forms.ModelForm):
        class Meta:
            model = User
            fields = ["username", "email"]

    form = Plain({"username": "x"}, instance=make_user())
    serialization.protect_blanked_floor_columns(form)
    serialization.keep_withheld_columns_when_blank(form)
    assert "_djust_cleaned_blank" not in form.__dict__
    assert not getattr(type(form), "_djust_pinned", False)
    assert type(form) is Plain


# ---------------------------------------------------------------------------------------------
# Relations, copies, and a form rebuilt over a row that is not there.
class TestARelationIsNotEmptied:
    def test_a_many_to_many_field_named_like_a_credential_keeps_its_relation_on_an_untouched_save(
        self, monkeypatch
    ):
        """``_save_m2m`` hands ``cleaned_data[name]`` to ``save_form_data``: a blank there empties it."""
        from django.contrib.auth.models import Group

        group = Group.objects.create(name="staff-g")
        user = make_user()
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
            client.send_event("submit_form", username="alice2")
        assert client.view_instance.is_valid is True, client.view_instance.field_errors
        assert list(User.objects.get(pk=user.pk).groups.values_list("name", flat=True)) == [
            "staff-g"
        ]
        # the relation is not pinned blank; the other (non-relation) kept fields still are
        assert "groups" not in client.view_instance.form_instance.__dict__.get(
            "_djust_cleaned_blank", ()
        )

    def test_the_helper_names_the_relation_and_not_the_column(self):
        form = EditForm(instance=make_user())
        assert serialization._saved_from_cleaned_data_after_clean(form, "password") is False
        assert serialization._saved_from_cleaned_data_after_clean(form, "no_such_field") is False

        class M(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "groups"]

        assert serialization._saved_from_cleaned_data_after_clean(M(), "groups") is True
        assert serialization._saved_from_cleaned_data_after_clean(M(), "username") is False


class TestACopyOfAPinnedFormCleansItself:
    def pinned(self, username="idiom"):
        user = User.objects.create(username=username, password=real_stored())
        form = set_password_form(None)({"username": username}, instance=user)
        serialization.protect_blanked_floor_columns(form)
        return form

    @pytest.mark.parametrize("how", [copy.copy, copy.deepcopy], ids=["copy", "deepcopy"])
    def test_an_unvalidated_copy_is_cleaned_not_the_original(self, how):
        form = self.pinned()
        duplicate = how(form)
        assert duplicate.is_valid()
        assert duplicate.cleaned_data["password"] == ""
        assert not hasattr(form, "cleaned_data")  # the original was never cleaned
        assert form.is_valid()
        assert form.cleaned_data["password"] == ""

    def test_a_copy_does_not_share_the_set_when_names_are_added(self):
        form = self.pinned()
        duplicate = copy.copy(form)
        serialization._cleaned_data_stays_blank(duplicate, {"username"})
        assert form._djust_cleaned_blank == {"password"}
        assert duplicate._djust_cleaned_blank == {"password", "username"}

    def test_the_pinned_class_is_made_once_and_is_a_subclass(self):
        form_class = set_password_form(None)
        forms_made = []
        for username in ("first", "second"):
            user = User.objects.create(username=username, password=real_stored())
            form = form_class({"username": username}, instance=user)
            serialization.protect_blanked_floor_columns(form)
            forms_made.append(form)
        a, b = forms_made
        assert type(a) is type(b) and type(a) is not form_class
        assert type(a).__bases__ == (serialization._BlankPinned, form_class)
        assert isinstance(a, forms.ModelForm)
        assert type(a).__name__ == "PF"
        assert type(a).__mro__[1] is serialization._BlankPinned


class TestAFormRebuiltOverARowThatIsNotThere:
    def test_a_stored_value_in_form_data_is_not_printed_when_the_row_cannot_be_rehydrated(self):
        client, view = mount_edit(make_user())
        view._model_instance = None
        view.model_pk = 999999
        view._form_instance = None
        view.form_data["password"] = STORED
        assert view.get_field_value("password") == ""
        assert view.as_live_field("password").count(STORED) == 0

    def test_a_create_view_keeps_the_typed_draft(self):
        client = LiveViewTestClient(CreateView)
        client.mount()
        view = client.view_instance
        view.form_data["password"] = "draft-in-progress"
        assert view.get_field_value("password") == "draft-in-progress"
        assert "draft-in-progress" in view.as_live_field("password")

    def test_the_admin_change_page_with_a_lost_object(self):
        view = TestAdminFormView()._view(make_user())
        view._form_instance = None
        view.object = None
        view.form_data["password"] = STORED
        assert view.get_field_value("password") == ""

    def test_the_admin_add_page_keeps_the_typed_draft(self):
        from djust.admin_ext.forms import AdminFormMixin

        class Admin:
            def get_object(self, request, object_id):
                return None

            def get_form(self, request, obj=None):
                return EditForm

        class Add(AdminFormMixin, LiveView):
            exposure_policy = "legacy"
            _model = User
            _model_admin = Admin()

        view = Add()
        view.mount(RequestFactory().get("/admin/"), object_id=None)
        view.form_data["password"] = "draft-in-progress"
        assert view.get_field_value("password") == "draft-in-progress"


# ---------------------------------------------------------------------------------------------
# Any message that carries the stored value, and where the derived class lives.
CORE = "CORECORE"
TRICKY = CORE + "\"q'b\\k-é日"

MESSAGE_BUILDERS = {
    "plain": lambda pw: f"password {pw} not allowed",
    "repr": lambda pw: f"password {pw!r} not allowed",
    "ascii": lambda pw: f"password {pw!a} not allowed",
    "json": lambda pw: "password " + json.dumps(pw) + " not allowed",
    "json-unescaped": lambda pw: "password " + json.dumps(pw, ensure_ascii=False) + " no",
    "in a list": lambda pw: f"not in {[pw]}",
    "in a dict": lambda pw: f"not in {{'k': {pw!r}}}",
    "str of an object": lambda pw: f"bad object {type('O', (), {'__str__': lambda s: pw})()}",
    "doubly quoted": lambda pw: f"got {repr(repr(pw))}",
    "escaped, no quotes": lambda pw: f"x {repr(pw)[1:-1]} y",
}


def message_form(builder, field_kwargs=None, seen=None):
    class Messaging(forms.ModelForm):
        password = forms.CharField(required=False, **(field_kwargs or {}))

        class Meta:
            model = User
            fields = ["username", "password"]

        def clean_password(self):
            stored = self.cleaned_data["password"]  # the stored value: the documented window
            if stored:
                raise forms.ValidationError(builder(stored))
            return stored

        def clean_username(self):
            name = self.cleaned_data["username"]
            if name == "taken":
                raise forms.ValidationError(f"username {name!r} is taken (not {name})")
            return name

    return Messaging


def tricky_user():
    return User.objects.create(username="alice", email="a@example.org", password=TRICKY)


def every_text(view, client):
    return {
        "field_errors": json.dumps(
            {k: [str(e) for e in v] for k, v in view.field_errors.items()}, ensure_ascii=False
        ),
        "form_errors": json.dumps([str(e) for e in (view.form_errors or [])], ensure_ascii=False),
        "as_live": str(view.as_live()),
        "get_state": json.dumps(view.get_state(), default=str, ensure_ascii=False),
        "get_debug_info": json.dumps(view.get_debug_info(), default=str, ensure_ascii=False),
        "render": client.render(),
    }


class TestMessagesThatCarryTheStoredValue:
    @pytest.mark.parametrize("how", sorted(MESSAGE_BUILDERS))
    def test_a_developers_clean_method_cannot_put_the_value_on_any_path(self, how):
        user = tricky_user()
        hooked = []

        class V(EditView):
            form_class = message_form(MESSAGE_BUILDERS[how])

            def form_invalid(self, form):
                hooked.append(
                    json.dumps({k: [str(e) for e in v] for k, v in form.errors.items()})
                    + json.dumps(list(form.non_field_errors()))
                    + str(form.errors.as_text())
                    + str(form["password"].errors)
                )

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        view = client.view_instance
        assert view.is_valid is False
        assert view.field_errors["password"], view.field_errors
        for name, text in every_text(view, client).items():
            assert text.count(CORE) == 0, (how, name)
        assert hooked and all(entry.count(CORE) == 0 for entry in hooked), how
        assert [str(m) for m in view.field_errors["password"]] == ["Enter a valid value."]
        assert User.objects.get(pk=user.pk).password == TRICKY

    def test_a_kept_value_widget_is_scrubbed_too(self):
        """``render_value=True`` is withheld from ``form_data``; its errors follow the same rule."""
        user = tricky_user()

        class Carrier(forms.ModelForm):
            password = forms.CharField(
                widget=forms.PasswordInput(render_value=True), required=False
            )

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                raise forms.ValidationError(f"never {self.instance.password!r}")

        class V(EditView):
            form_class = Carrier

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        view = client.view_instance
        assert view.field_errors["password"] == ["Enter a valid value."]
        for name, text in every_text(view, client).items():
            assert text.count(CORE) == 0, name

    def test_a_parameterised_message_is_still_generic(self):
        user = tricky_user()

        def builder(pw):
            raise forms.ValidationError("bad %(v)s", params={"v": pw})

        class V(EditView):
            form_class = message_form(builder)

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        for name, text in every_text(client.view_instance, client).items():
            assert text.count(CORE) == 0, name

    def test_a_non_field_error_that_prints_it_is_scrubbed(self):
        user = tricky_user()

        class Whole(forms.ModelForm):
            password = forms.CharField(required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean(self):
                super().clean()
                raise forms.ValidationError(f"whole form refused {self.instance.password!r}")

        class V(EditView):
            form_class = Whole

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        view = client.view_instance
        assert view.form_errors, "the non-field error is still reported"
        for name, text in every_text(view, client).items():
            assert text.count(CORE) == 0, name

    def test_a_field_that_is_not_withheld_keeps_its_messages(self):
        """``alice`` is a username, not a withheld value: a message that quotes it is the person's to read."""
        user = tricky_user()
        User.objects.create(username="taken", password="x")

        class V(EditView):
            form_class = message_form(lambda pw: "never reached")

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="taken", password="")
        view = client.view_instance
        assert [str(m) for m in view.field_errors["username"]] == [
            "username 'taken' is taken (not taken)"
        ]
        # the withheld field has a message of its own that does NOT contain the value: kept as it is
        assert [str(m) for m in view.field_errors["password"]] == ["never reached"]

    def test_a_withheld_fields_message_without_the_value_is_untouched(self):
        user = tricky_user()

        class V(EditView):
            form_class = message_form(lambda pw: "too short, try again")

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice2")
        assert [str(m) for m in client.view_instance.field_errors["password"]] == [
            "too short, try again"
        ]

    def test_validate_field_scrubs_the_same_way(self):
        user = tricky_user()

        class Reads(forms.ModelForm):
            password = forms.CharField(required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                raise forms.ValidationError(f"never {self.instance.password!r}")

        class V(EditView):
            form_class = Reads

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("validate_field", field="password", value="typed")
        view = client.view_instance
        assert view.field_errors["password"] == ["Enter a valid value."]
        for name, text in every_text(view, client).items():
            assert text.count(CORE) == 0, name

    def test_the_admin_views_scrub_both_paths(self):
        user = tricky_user()

        class Reads(forms.ModelForm):
            password = forms.CharField(required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                raise forms.ValidationError(f"never {self.instance.password!r}")

        view = TestAdminFormView()._view(user, Reads)
        view.validate_field(field="password", value="typed")
        assert view.field_errors["password"] == ["Enter a valid value."]
        view.field_errors = {}
        view.submit_form(username="alice2")
        assert view.is_valid is False
        assert (
            json.dumps(
                {k: [str(e) for e in v] for k, v in view.field_errors.items()}, ensure_ascii=False
            ).count(CORE)
            == 0
        )
        assert str(view.form_instance.errors).count(CORE) == 0

    def test_scrub_error_messages_directly(self):
        form = EditForm(instance=tricky_user())
        assert serialization.scrub_error_messages(form, "password", [f"no {TRICKY!r}"]) == [
            "Enter a valid value."
        ]
        assert serialization.scrub_error_messages(form, "password", ["too long"]) == ["too long"]
        assert serialization.scrub_error_messages(form, "email", [f"no {TRICKY}"]) == [
            f"no {TRICKY}"
        ]
        # a field that is not withheld may quote ITS OWN value
        for name, value in (("email", "a@example.org"), ("username", "alice")):
            message = [f"{name} {value!r} is already taken"]
            assert serialization.scrub_error_messages(form, name, message) == message


# ---------------------------------------------------------------------------------------------
class TestThePinnedClassLivesOnTheClassAndIsCollectable:
    def pin(self, form_class, username):
        user = User.objects.create(username=username, password=real_stored())
        form = form_class({"username": username}, instance=user)
        serialization.protect_blanked_floor_columns(form)
        assert getattr(type(form), "_djust_pinned", False)
        return form

    def containers(self):
        return {
            name: len(value)
            for name, value in vars(serialization).items()
            if isinstance(value, (dict, list, set, frozenset)) and not name.startswith("__")
        }

    def test_it_is_in_vars_of_the_class_and_a_subclass_does_not_inherit_it(self):
        parent = set_password_form(None)

        class Child(parent):
            pass

        self.pin(parent, "p")
        assert serialization._PINNED_CLASS_ATTR in vars(parent)
        assert serialization._PINNED_CLASS_ATTR not in vars(Child)
        child_form = self.pin(Child, "c")
        assert serialization._PINNED_CLASS_ATTR in vars(Child)
        assert (
            vars(Child)[serialization._PINNED_CLASS_ATTR]
            is not vars(parent)[serialization._PINNED_CLASS_ATTR]
        )
        assert type(child_form).__bases__ == (serialization._BlankPinned, Child)

    def test_twenty_factory_classes_are_collectable_and_grow_no_module_container(self):
        import gc
        import weakref

        before = self.containers()
        refs = []
        for index in range(20):
            form_class = forms.modelform_factory(
                User,
                fields=["username", "password"],
                form=set_password_form(None),
            )
            form = self.pin(form_class, f"fac{index}")
            refs.extend([weakref.ref(form_class), weakref.ref(type(form))])
            del form, form_class
        gc.collect()
        assert [ref() for ref in refs if ref() is not None] == []
        assert self.containers() == before

    def test_ten_admin_submits_that_build_a_fresh_class_each_are_collectable(self):
        import gc
        import weakref

        from djust.admin_ext.forms import AdminFormMixin

        user = make_user()
        built = []

        class Admin:
            def get_object(self, request, object_id):
                return user

            def get_form(self, request, obj=None):
                form_class = forms.modelform_factory(
                    User, fields=["username", "password"], form=set_password_form(None)
                )
                built.append(weakref.ref(form_class))
                return form_class

        class View(AdminFormMixin, LiveView):
            exposure_policy = "legacy"
            _model = User
            _model_admin = Admin()

        before = self.containers()
        refs = []
        for _ in range(10):
            view = View()
            view.mount(RequestFactory().get("/admin/"), object_id=str(user.pk))
            view.submit_form(username="alice2")
            refs.append(weakref.ref(type(view.form_instance)))
            del view
        assert len(built) >= 10
        gc.collect()
        assert [ref() for ref in built + refs if ref() is not None] == []
        assert self.containers() == before


# ---------------------------------------------------------------------------------------------
# Hardening: a re-entrant lock, and a form class that cannot be swapped.
class TestThePinLockIsReentrant:
    def test_a_form_class_that_pins_another_class_while_it_is_derived_does_not_deadlock(
        self, monkeypatch
    ):
        import threading

        other = set_password_form(None)

        class Hooked(set_password_form(None)):
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                if "_pins_other" not in vars(cls):  # once, for the derived class
                    cls._pins_other = True
                    serialization._pinned_form_class(other)

        # a fresh lock of the real lock's type, so a failing run cannot leave the real one held
        monkeypatch.setattr(
            serialization, "_PINNED_CLASS_LOCK", type(serialization._PINNED_CLASS_LOCK)()
        )
        result = {}

        def work():
            result["derived"] = serialization._pinned_form_class(Hooked)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "deriving a class that pins another one deadlocked"
        assert result["derived"].__bases__ == (serialization._BlankPinned, Hooked)
        assert serialization._PINNED_CLASS_ATTR in vars(other)


class Unswappable:
    """A mixin whose instances refuse ``__class__`` assignment, on every Python.

    The product swaps a form's class to pin its behaviour and falls back to pinning on the instance when
    the swap is refused. Which layouts the interpreter refuses differs between versions (a ``__slots__``
    mixin refuses on 3.12 to 3.14 and not on 3.15), so the refusal is made explicit here."""

    def __setattr__(self, name, value):
        if name == "__class__":
            raise TypeError("__class__ assignment is refused for this class")
        super().__setattr__(name, value)


def unswappable(form_class):
    class UnswappableForm(Unswappable, form_class):
        class Meta(form_class.Meta):
            pass

    return UnswappableForm


class TestAFormClassThatCannotBeSwapped:
    def test_the_class_really_cannot_be_swapped(self):
        """If this stops raising, the tests below no longer reach the fallback."""
        user = User.objects.create(username="slot", password=real_stored())
        form = unswappable(set_password_form(None))({"username": "slot"}, instance=user)
        with pytest.raises(TypeError):
            form.__class__ = serialization._pinned_form_class(type(form))
        assert type(form).__name__ == "UnswappableForm"  # the refused swap changed nothing

    def test_an_ordinary_form_class_can_be_swapped(self):
        """The control: it is the mixin that refuses, not the form."""
        user = User.objects.create(username="slot", password=real_stored())
        form = set_password_form(None)({"username": "slot"}, instance=user)
        form.__class__ = serialization._pinned_form_class(type(form))
        assert getattr(type(form), "_djust_pinned", False) is True

    @pytest.mark.parametrize("widget", [None, forms.PasswordInput], ids=["text", "passwordinput"])
    def test_a_blank_submission_keeps_the_column_and_cleans_to_blank(self, widget, caplog):
        """The text field is made read-only at MOUNT (``get_form``); the password-widget field is kept
        at SUBMIT. Both reach the fallback."""
        seen = []
        form_class = unswappable(set_password_form(widget, seen=seen))
        user = User.objects.create(username="slot", password=real_stored())
        client = LiveViewTestClient(view_for(form_class))
        with caplog.at_level("WARNING"):
            client.mount(pk=user.pk)
            client.send_event("submit_form", username="slot2")
        view = client.view_instance
        assert view.is_valid is True, view.field_errors
        form = view.form_instance
        assert form.__dict__.get("_djust_pinned_on_instance") is True
        assert type(form) is form_class  # not swapped
        assert form.cleaned_data["password"] == ""
        assert seen and all(value == "" for value in seen)
        user.refresh_from_db()
        assert user.username == "slot2"
        assert user.password == real_stored()
        assert user.check_password("oldpw") is True
        # logged, value-free
        said = [r.getMessage() for r in caplog.records if "cannot be derived" in r.getMessage()]
        assert said and all("UnswappableForm" in m and real_stored() not in m for m in said)

    def test_a_typed_value_still_writes(self):
        form_class = unswappable(set_password_form(forms.PasswordInput))
        user = User.objects.create(username="slot", password=real_stored())
        client = LiveViewTestClient(view_for(form_class))
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="slot", password="newpw")
        assert client.view_instance.is_valid is True
        user.refresh_from_db()
        assert user.check_password("newpw") is True

    def test_pinning_twice_wraps_once(self):
        user = User.objects.create(username="slot", password=real_stored())
        form = unswappable(set_password_form(None))({"username": "slot"}, instance=user)
        serialization.protect_blanked_floor_columns(form)
        wrapped = form.__dict__["_clean_fields"]
        serialization.protect_blanked_floor_columns(form)
        serialization._cleaned_data_stays_blank(form, {"username"})
        assert form.__dict__["_clean_fields"] is wrapped
        assert form.is_valid()
        assert form.cleaned_data["password"] == ""


# -- ordinary messages are kept -------------------------------------------------------------------
def _messages(view):
    return {k: [str(e) for e in v] for k, v in view.field_errors.items()}


def _view_for_errors(form_class, **extra):
    attrs = {"form_class": form_class, "template": "<div dj-root></div>"}
    attrs.update(extra)
    return type("ErrorsView", (FormMixin, LiveView), {"exposure_policy": "legacy", **attrs})


class TestOrdinaryMessagesAreKept:
    """A message that carries no stored value is shown as Django wrote it, whether or not the form
    has a withheld field: only an error that can carry a value is replaced."""

    def test_a_failed_login_keeps_djangos_message(self):
        from django.contrib.auth.forms import AuthenticationForm

        class LoginView(_view_for_errors(AuthenticationForm)):
            def get_form_kwargs(self):
                kwargs = super().get_form_kwargs()
                kwargs["request"] = None
                return kwargs

        User.objects.create_user("bob", password="right-password")
        client = LiveViewTestClient(LoginView)
        client.mount()
        client.send_event("submit_form", username="bob", password="wrong-password")
        got = _messages(client.view_instance)
        direct = AuthenticationForm(data={"username": "bob", "password": "wrong-password"})
        expected = [str(e) for e in direct.errors["__all__"]]
        assert got["__all__"] == expected, (got, expected)
        assert "Enter a valid value." not in json.dumps(got)

    @pytest.mark.parametrize("form_name", ["UserCreationForm", "PasswordChangeForm"])
    def test_a_password_policy_message_is_kept(self, form_name):
        from django.contrib.auth import forms as auth_forms

        validators = [
            {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
            {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        ]
        form_class = getattr(auth_forms, form_name)
        carol = None
        if form_name == "UserCreationForm":
            posted = {"username": "carol", "password1": "abc", "password2": "abc"}
            extra = {}
        else:
            carol = User.objects.create_user("carol", password="old-password-1")
            posted = {
                "old_password": "old-password-1",
                "new_password1": "abc",
                "new_password2": "abc",
            }

            def get_form_kwargs(self):
                kwargs = FormMixin.get_form_kwargs(self)
                kwargs["user"] = carol
                return kwargs

            extra = {"get_form_kwargs": get_form_kwargs}
        view_class = _view_for_errors(form_class, **extra)
        with override_settings(AUTH_PASSWORD_VALIDATORS=validators):
            client = LiveViewTestClient(view_class)
            client.mount()
            client.send_event("submit_form", **posted)
            kwargs = {"user": carol} if carol is not None else {}
            direct = form_class(data=posted, **kwargs)
            expected = {k: [str(e) for e in v] for k, v in direct.errors.items()}
        got = _messages(client.view_instance)
        assert got == expected, (got, expected)
        assert any("too short" in m for ms in got.values() for m in ms), got

    def test_a_parameterised_clean_error_on_a_form_without_a_stored_value_is_kept(self):
        class LimitForm(forms.Form):
            pass_word = forms.CharField(widget=forms.PasswordInput, required=False)

            def clean(self):
                raise forms.ValidationError("Limit %(n)s exceeded", params={"n": 3})

        client = LiveViewTestClient(_view_for_errors(LimitForm))
        client.mount()
        client.send_event("submit_form", pass_word="typed")
        assert _messages(client.view_instance)["__all__"] == ["Limit 3 exceeded"]

    def test_a_parameterised_error_that_carries_the_stored_value_is_replaced(self):
        class CarryForm(forms.ModelForm):
            password = forms.CharField(required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                stored = self.cleaned_data["password"]
                raise forms.ValidationError("bad value %(v)s", params={"v": stored})

        user = make_user()

        class V(EditView):
            form_class = CarryForm

        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        view = client.view_instance
        text = json.dumps(_messages(view))
        assert STORED not in text, text
        assert _messages(view).get("password") == ["Enter a valid value."]

    def test_a_transformed_copy_of_the_stored_value_in_a_parameterised_error_is_replaced(self):
        """The substring rule cannot see an upper-cased copy; the parameterised rule does."""

        class ShoutForm(forms.ModelForm):
            password = forms.CharField(required=False)

            class Meta:
                model = User
                fields = ["username", "password"]

            def clean_password(self):
                stored = self.cleaned_data["password"]
                raise forms.ValidationError("bad %(v)s", params={"v": stored.upper()})

        class V(EditView):
            form_class = ShoutForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        text = json.dumps(_messages(client.view_instance))
        assert STORED.upper() not in text.upper(), text
        assert _messages(client.view_instance).get("password") == ["Enter a valid value."]

    def test_in_one_error_list_only_the_error_that_carries_the_value_is_replaced(self):
        """The decision is made per error: a sibling parameterised error of the same list is kept."""

        class MixedForm(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "password"]

            def clean(self):
                stored = self.initial.get("password")
                raise forms.ValidationError(
                    [
                        forms.ValidationError("bad value %(v)s", params={"v": stored}, code="a"),
                        forms.ValidationError("Limit %(n)s exceeded", params={"n": 3}, code="b"),
                    ]
                )

        class V(EditView):
            form_class = MixedForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        view = client.view_instance
        assert _messages(view)["__all__"] == ["Enter a valid value.", "Limit 3 exceeded"]
        assert_not_carried(view, client)

    def test_a_parameterised_non_field_error_is_kept_beside_a_stored_value(self):
        class LimitEditForm(forms.ModelForm):
            class Meta:
                model = User
                fields = ["username", "password"]

            def clean(self):
                raise forms.ValidationError("Limit %(n)s exceeded", params={"n": 3})

        class V(EditView):
            form_class = LimitEditForm

        user = make_user()
        client = LiveViewTestClient(V)
        client.mount(pk=user.pk)
        client.send_event("submit_form", username="alice")
        assert _messages(client.view_instance)["__all__"] == ["Limit 3 exceeded"]
        assert_not_carried(client.view_instance, client)

    def test_the_difference_is_only_whether_a_stored_value_is_there_to_carry(self):
        """A change that always replaced a parameterised message, or never did, fails here."""
        from django.core.exceptions import ValidationError

        error = ValidationError("Too short %(n)s", params={"n": 8})
        assert serialization._error_carries_value(error, (), params_count=False) is False
        assert serialization._error_carries_value(error, ("abc",), params_count=True) is True
        assert serialization._error_carries_value(error, ("abc",), params_count=False) is False
        assert serialization._error_carries_value(ValidationError("x abc y"), ("abc",), False)
