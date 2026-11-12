"""A model form's values are held to the serialization floor (#3790).

``ModelForm.initial`` is ``model_to_dict(instance)``: a plain ``dict`` of strings by the time a
template reads it, so the Rust engine's walk never meets a ``Model`` and the model proxy has nothing to
refuse. ``{{ form.initial.password }}``, ``{{ form.password.value }}`` and ``{{ form.password.initial }}``
printed the hash of a ``UserChangeForm``'s user while ``{{ form.instance.password }}`` was empty: the
same secret by a different spelling. Django prints it too; the floor is the stricter policy djust adds
(SECURE_DEFAULTS Pattern 1), and it is applied where the dict leaves the form
(``serialization._protect_sidecar_walk``, called by the engine after every segment of a path).

What each class proves:

* ``TestTheFloorHolds``: every spelling of a floor field's value that a template can write (the three
  from the issue, ``cleaned_data`` / ``data`` of a bound form, ``subwidgets``, the ``as_text`` family,
  a loop variable, a ``{% with %}`` alias, ``visible_fields``, ``get_context``, ``.form``) prints
  nothing of it, for an unbound and a bound form. Django prints it for every one (the control), so the
  comparison is never two empty strings.
* ``TestTheFloorIsTheModelsFloor``: it is the model's floor, not the word ``password``:
  ``DJUST_SENSITIVE_FIELDS`` and a per-model ``djust_exclude_fields`` are held too.
* ``TestBenignOutputIsUnchanged``: a form that holds no floor field, and the non-floor fields of one
  that does, render as Django renders them; the widget the form author chose still renders.
* ``TestTheFormItselfIsNotChanged``: the floor is a copy a template walks. ``initial`` is what a disabled
  field validates against, so the real form keeps its values and still saves.
* ``TestATagIsHandedTheRealObject``: a bridged tag's own Python gets the real form / ``BoundField``
  (djust's component readers call ``bound_field.value()``); only a template PATH is floored.
* ``TestTheRealEntries``: the same render through ``LiveViewTestClient``, an HTTP GET and a WebSocket
  mount.

Refs #3790, #3767, #3771, #1986, #1646, #1468.
"""

from __future__ import annotations

import re

import pytest
from django import forms, template
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.template import Context, Engine
from django.test import override_settings

from djust.live_view import LiveView

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

register = template.Library()


@register.simple_tag
def echo_3790(value=None):
    return "[%s]" % (value,)


@register.simple_tag
def kind_3790(value):
    """The type of the operand the tag is handed."""
    return type(value).__name__


@register.simple_tag
def value_3790(bound_field):
    """What djust's own component readers do: call ``bound_field.value()`` on what they are handed."""
    return "[%s]" % (bound_field.value(),)


@register.simple_tag
def initial_3790(form):
    """A tag author's own Python reading the form it was handed: trusted, never floored."""
    return "[%s]" % (form.initial.get("password"),)


@register.simple_tag(takes_context=True)
def ctx_kind_3790(context, name):
    """The type of what the bridge put in the context under *name* (a loop variable is put there by
    ``value_into_handler_pyobject``, the second route by which an object reaches a tag)."""
    return type(context[name]).__name__


@register.simple_tag(takes_context=True)
def ctx_value_3790(context, name):
    return "[%s]" % (context[name].value(),)


LOAD = "{% load probe3790 %}"


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {
            "NAME": "floor-3790",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {"libraries": {"probe3790": __name__}},
        }
    )
    django = Engine(libraries={"probe3790": __name__})
    return rust, django


@pytest.fixture
def user():
    return get_user_model().objects.create_user(
        "floor-user", "secret-3790@example.com", "pw-3790", first_name="Ada"
    )


class ProfileForm(forms.ModelForm):
    """A model form over fields that are NOT in the built-in floor."""

    class Meta:
        model = get_user_model()
        fields = ["username", "email", "first_name"]


class PlainForm(forms.Form):
    """No model behind it: nothing for a model floor to protect."""

    password = forms.CharField(required=False)
    username = forms.CharField(required=False)


def _render(engines, which: str, src: str, context: dict) -> str:
    rust, django = engines
    if which == "rust":
        out = rust.from_string(LOAD + src).render(dict(context))
    else:
        out = django.from_string(LOAD + src).render(Context(dict(context)))
    return re.sub(r"\s+", " ", str(out))


def _forms(user) -> dict:
    """The same user's change form, unbound and bound (and validated, so ``cleaned_data`` exists)."""
    bound = UserChangeForm(
        {
            "username": user.username,
            "password": user.password,
            "date_joined": user.date_joined.strftime("%Y-%m-%d %H:%M:%S"),
        },
        instance=user,
    )
    assert bound.is_valid(), bound.errors
    return {"unbound": UserChangeForm(instance=user), "bound": bound}


#: (id, template body). Every one of them printed the hash on the native engine before #3790.
SPELLINGS = [
    ("form.initial.password", "{{ form.initial.password }}"),
    ("form.password.value", "{{ form.password.value }}"),
    ("form.password.initial", "{{ form.password.initial }}"),
    ("form['password'] via item", "{{ form.initial.password|default:'' }}"),
    ("form.password.data", "{{ form.password.data }}"),
    ("form.cleaned_data.password", "{{ form.cleaned_data.password }}"),
    ("form.data.password", "{{ form.data.password }}"),
    ("as_text", "{{ form.password.as_text }}"),
    ("as_hidden", "{{ form.password.as_hidden }}"),
    ("as_textarea", "{{ form.password.as_textarea }}"),
    ("subwidgets", "{{ form.password.subwidgets.0.data.value }}"),
    ("index into the field", "{{ form.password.0.data.value }}"),
    (
        "iterate the field",
        "{% for w in form.password %}{{ w.data.value }}{{ w.data.attrs }}{% endfor %}",
    ),
    (
        "iterate its subwidgets",
        "{% for w in form.password.subwidgets %}{{ w.data.value }}{% endfor %}",
    ),
    (
        "loop over the form",
        "{% for f in form %}{% if f.name == 'password' %}[{{ f.value }}|{{ f.initial }}]{% endif %}{% endfor %}",
    ),
    (
        "loop over visible_fields",
        "{% for f in form.visible_fields %}{% if f.name == 'password' %}{{ f.value }}{% endif %}{% endfor %}",
    ),
    ("get_context", "{{ form.get_context.fields.0.0.value }}"),
    (
        "initial.items",
        "{% for k, v in form.initial.items %}{% if k == 'password' %}{{ v }}{% endif %}{% endfor %}",
    ),
    ("with alias", "{% with f=form.password %}{{ f.value }}{% endwith %}"),
    ("with alias of the dict", "{% with d=form.initial %}{{ d.password }}{% endwith %}"),
    ("back through .form", "{{ form.password.form.initial.password }}"),
    ("through a filter", "{{ form.initial.password|upper }}"),
    ("as a tag operand", "{% echo_3790 form.initial.password %}"),
    ("as a tag operand (field)", "{% echo_3790 form.password.value %}"),
]


@pytest.mark.parametrize("shape", ["unbound", "bound"])
@pytest.mark.parametrize("name,body", SPELLINGS, ids=[s[0] for s in SPELLINGS])
class TestTheFloorHolds:
    def test_the_rust_engine_prints_nothing_of_it(self, engines, user, name, body, shape):
        form = _forms(user)[shape]
        out = _render(engines, "rust", body, {"form": form})
        assert user.password.lower() not in out.lower(), out
        # Not even the algorithm / salt: the secret is not in a truncated spelling either.
        assert "pbkdf2_sha256$" not in out.lower(), out

    def test_django_prints_it_so_the_comparison_is_not_two_empty_strings(
        self, engines, user, name, body, shape
    ):
        form = _forms(user)[shape]
        control = _render(engines, "django", body, {"form": form})
        if shape == "unbound" and name in (
            "form.cleaned_data.password",
            "form.data.password",
            "form.password.data",
        ):
            # An unbound form has no submitted data and no cleaned data: nothing to print on either engine.
            assert user.password not in control
            return
        # `|upper` changes the case of the secret, not whether it is there.
        assert user.password.lower() in control.lower(), (name, shape, control[:200])


class TestTheFloorHoldsOnEverySpellingOfTheIssue:
    """The three paths the issue names, and the model attribute they sit beside."""

    @pytest.mark.parametrize("shape", ["unbound", "bound"])
    def test_the_three_spellings_and_the_instance_are_all_empty(self, engines, user, shape):
        form = _forms(user)[shape]
        for body in (
            "{{ form.initial.password }}",
            "{{ form.password.value }}",
            "{{ form.password.initial }}",
            "{{ form.instance.password }}",
        ):
            assert _render(engines, "rust", body, {"form": form}) == "", body


class TestTheFloorIsTheModelsFloor:
    def test_a_configured_sensitive_field_is_held(self, engines, user):
        secret = "secret-3790@example.com"
        form = ProfileForm(instance=user)
        body = (
            "[{{ form.initial.email }}][{{ form.email.value }}][{{ form.email.initial }}]"
            "[{% for f in form %}{{ f.value }}{% endfor %}]"
        )
        # Without the setting, `email` is not a floor field: the control that the form is readable.
        assert secret in _render(engines, "rust", body, {"form": form})
        with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
            out = _render(engines, "rust", body, {"form": form})
        assert secret not in out, out
        # The three paths and the email in the loop are gone; the loop still shows the other fields.
        assert out == "[][][][floor-userAda]", out

    def test_a_configured_field_still_leaves_the_other_fields_readable(self, engines, user):
        form = ProfileForm(instance=user)
        with override_settings(DJUST_SENSITIVE_FIELDS=["email"]):
            out = _render(
                engines,
                "rust",
                "[{{ form.initial.username }}][{{ form.username.value }}][{{ form.first_name.value }}]",
                {"form": form},
            )
        assert out == "[floor-user][floor-user][Ada]"

    def test_a_per_model_exclusion_is_held(self, engines, user, monkeypatch):
        monkeypatch.setattr(
            get_user_model(), "djust_exclude_fields", ("first_name",), raising=False
        )
        form = ProfileForm(instance=user)
        out = _render(
            engines,
            "rust",
            "[{{ form.initial.first_name }}][{{ form.first_name.value }}][{{ form.username.value }}]",
            {"form": form},
        )
        assert out == "[][][floor-user]"

    def test_a_per_model_allowlist_narrows_the_form_as_it_narrows_the_model(
        self, engines, user, monkeypatch
    ):
        monkeypatch.setattr(
            get_user_model(), "djust_serializable_fields", ("username",), raising=False
        )
        form = ProfileForm(instance=user)
        out = _render(
            engines,
            "rust",
            "[{{ form.username.value }}][{{ form.email.value }}][{{ form.instance.email }}]",
            {"form": form},
        )
        assert out == "[floor-user][][]"

    def test_the_type_floor_is_held(self, engines, user):
        """``LIVEVIEW_CONFIG['sensitive_field_types']`` (#1987): the field's TYPE, whatever its name."""
        from djust.config import get_config

        cfg = get_config()
        orig = cfg._config.get("sensitive_field_types")
        form = ProfileForm(instance=user)
        body = "[{{ form.initial.email }}][{{ form.email.value }}][{{ form.username.value }}]"
        try:
            cfg._config["sensitive_field_types"] = ["EmailField"]
            out = _render(engines, "rust", body, {"form": form})
        finally:
            cfg._config["sensitive_field_types"] = orig
        assert out == "[][][floor-user]"
        assert _render(engines, "rust", body, {"form": form}) == (
            "[secret-3790@example.com][secret-3790@example.com][floor-user]"
        )

    def test_the_deliberate_opt_out_lifts_it(self, engines, user, monkeypatch):
        monkeypatch.setattr(
            get_user_model(), "djust_serialize_sensitive_fields", ("password",), raising=False
        )
        form = UserChangeForm(instance=user)
        out = _render(engines, "rust", "{{ form.initial.password }}", {"form": form})
        assert out == user.password

    def test_a_plain_form_has_no_model_floor(self, engines, user):
        form = PlainForm({"password": "typed", "username": "u"})
        assert form.is_valid()
        out = _render(
            engines,
            "rust",
            "[{{ form.password.value }}][{{ form.cleaned_data.username }}]",
            {"form": form},
        )
        assert out == "[typed][u]"


class TestBenignOutputIsUnchanged:
    @pytest.mark.parametrize("shape", ["unbound", "bound"])
    @pytest.mark.parametrize(
        "body",
        [
            "{{ form.initial.username }}",
            "{{ form.username.value }}",
            "{{ form.username.initial }}",
            "{{ form.username.label }}",
            "{{ form.username.name }}",
            "{{ form.username.html_name }}",
            "{{ form.username.errors }}",
            "{{ form.username.help_text }}",
            "{{ form.instance.username }}",
            "{% for f in form %}{% if f.name == 'username' %}{{ f.value }}{{ f.label }}{% endif %}{% endfor %}",
            "{% for f in form.visible_fields %}{{ f.name }},{% endfor %}",
            "{% for k, v in form.initial.items %}{% if k == 'username' %}{{ v }}{% endif %}{% endfor %}",
            "{{ form.is_bound }}",
            "{{ form.errors }}",
            "{{ form.non_field_errors }}",
            "{% for f in form.hidden_fields %}{{ f.name }}{% endfor %}",
        ],
    )
    def test_the_output_is_django_s(self, engines, user, body, shape):
        form = _forms(user)[shape]
        assert _render(engines, "rust", body, {"form": form}) == _render(
            engines, "django", body, {"form": form}
        )

    @pytest.mark.parametrize("shape", ["unbound", "bound"])
    def test_the_widget_the_form_author_chose_still_renders(self, engines, user, shape):
        form = _forms(user)[shape]
        assert _render(engines, "rust", "{{ form.password }}", {"form": form}) == re.sub(
            r"\s+", " ", str(form["password"])
        )
        assert _render(engines, "rust", "{{ form.as_p }}", {"form": form}) == re.sub(
            r"\s+", " ", form.as_p()
        )
        assert _render(engines, "rust", "{{ form }}", {"form": form}) == re.sub(
            r"\s+", " ", str(form)
        )
        # The read-only widget shows the hash's summary, not the hash. That is Django's design.
        assert user.password not in str(form["password"])

    def test_a_loop_over_the_form_iterates_every_field_once(self, engines, user):
        form = UserChangeForm(instance=user)
        out = _render(
            engines, "rust", "{% for f in form %}{{ f.name }},{% endfor %}", {"form": form}
        )
        assert out.rstrip(",").split(",") == [f.name for f in form]

    def test_a_field_of_a_form_with_no_floor_is_the_object_itself(self, engines, user):
        from djust.serialization import _protect_sidecar_walk

        form = ProfileForm(instance=user)
        # The form is proxied (its dicts are what a walk reads); a field that is not a floor field is not.
        assert _protect_sidecar_walk(form["username"]) is form["username"]
        assert (
            _protect_sidecar_walk(UserChangeForm(instance=user)["username"]).__class__.__name__
            == "BoundField"
        )

    def test_a_form_over_a_model_with_no_floor_fields_in_it_renders_as_before(self, engines, user):
        form = ProfileForm({"username": "floor-user", "email": "e@example.com"}, instance=user)
        body = "[{{ form.data.username }}][{{ form.cleaned_data.email }}][{{ form.email.value }}]"
        assert form.is_valid(), form.errors
        assert _render(engines, "rust", body, {"form": form}) == _render(
            engines, "django", body, {"form": form}
        )


class TestTheFormItselfIsNotChanged:
    """The floor is a copy a template walks. A disabled field validates against ``initial``."""

    def test_rendering_leaves_the_form_whole(self, engines, user):
        forms_ = _forms(user)
        for form in forms_.values():
            initial = dict(form.initial)
            data = form.data
            for body, _ in ((s[1], s[0]) for s in SPELLINGS):
                _render(engines, "rust", body, {"form": form})
            assert form.initial == initial
            assert form.initial["password"] == user.password
            assert form.data is data
        assert forms_["bound"].cleaned_data["password"] == user.password
        assert forms_["bound"].is_valid()

    def test_a_form_that_was_rendered_still_saves_without_clearing_the_password(
        self, engines, user
    ):
        form = UserChangeForm(
            {"username": "renamed", "date_joined": user.date_joined.strftime("%Y-%m-%d %H:%M:%S")},
            instance=user,
        )
        _render(
            engines, "rust", "{{ form.initial.password }}{{ form.password.value }}", {"form": form}
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        saved.refresh_from_db()
        assert saved.username == "renamed"
        assert saved.password == user.password, "rendering must not have cost the user the hash"
        assert saved.has_usable_password()

    def test_the_querydict_a_bound_form_holds_keeps_its_type_and_immutability(self, engines, user):
        from django.http import QueryDict

        data = QueryDict("username=floor-user&password=" + "x" * 8, mutable=False)
        form = UserChangeForm(data, instance=user)
        _render(engines, "rust", "{{ form.data.username }}{{ form.data.password }}", {"form": form})
        assert form.data is data and not data._mutable
        assert data["password"] == "x" * 8


class TestWhatTheWalkIsHanded:
    """Pins of the proxies' contract, so a change to them cannot move it silently."""

    def test_initial_is_a_copy_without_floor_keys_and_the_forms_own_when_there_is_none(self, user):
        from djust.serialization import _protect_sidecar_walk

        proxy = _protect_sidecar_walk(UserChangeForm(instance=user))
        assert "password" not in proxy.initial
        assert "username" in proxy.initial
        own = _protect_sidecar_walk(ProfileForm(instance=user))
        assert own.initial is object.__getattribute__(own, "_form").initial

    def test_a_floor_fields_bound_field_refuses_exactly_the_value_readers(self, user):
        from djust.serialization import _FLOOR_BOUND_FIELD_ATTRS, _protect_sidecar_walk

        form = UserChangeForm(instance=user)
        proxy = _protect_sidecar_walk(form)
        field = proxy["password"]
        for name in _FLOOR_BOUND_FIELD_ATTRS:
            assert not hasattr(field, name), name
            assert name not in dir(field), name
        for name in ("name", "label", "errors", "help_text", "html_name", "id_for_label", "form"):
            assert hasattr(field, name), name
        assert str(field) == str(form["password"])
        assert bool(field) is True

    def test_the_walk_floor_is_idempotent(self, user):
        from djust.serialization import _protect_sidecar_walk

        once = _protect_sidecar_walk(UserChangeForm(instance=user))
        assert _protect_sidecar_walk(once) is once
        field = once["password"]
        assert _protect_sidecar_walk(field) is field

    def test_a_leading_underscore_is_never_resolved(self, user):
        from djust.serialization import _protect_sidecar_walk

        proxy = _protect_sidecar_walk(UserChangeForm(instance=user))
        for name in ("_errors", "__dict__", "_meta", "_form"):
            assert not hasattr(proxy, name) or name == "_form"
            assert not hasattr(proxy["password"], name) or name == "_bf"

    def test_the_proxies_are_recognised_as_a_form_by_the_engine(self, user):
        from djust.serialization import _is_form_object, _protect_sidecar_walk

        proxy = _protect_sidecar_walk(UserChangeForm(instance=user))
        assert _is_form_object(proxy) and _is_form_object(proxy["password"])

    def test_the_leaf_floor_does_not_wrap_a_form(self, user):
        """``_protect_sidecar_value`` is what an object handed to a tag is protected with."""
        from djust.serialization import _protect_sidecar_value

        form = UserChangeForm(instance=user)
        assert _protect_sidecar_value(form) is form
        assert _protect_sidecar_value(form["password"]) is form["password"]


class TestATagIsHandedTheRealObject:
    """A tag is the application's own Python: ``bound_field.value()`` in djust's component readers, a
    third-party form tag. Handing it a proxy would draw a ``User``'s ``is_staff`` checkbox unchecked and
    the next save would clear it. Only a template PATH is floored."""

    def test_the_form_and_the_field_arrive_as_themselves(self, engines, user):
        form = UserChangeForm(instance=user)
        assert _render(engines, "rust", "{% kind_3790 form %}", {"form": form}) == "UserChangeForm"
        assert (
            _render(engines, "rust", "{% kind_3790 form.password %}", {"form": form})
            == "BoundField"
        )
        assert (
            _render(
                engines,
                "rust",
                "{% for f in form %}{% if f.name == 'password' %}{% kind_3790 f %}{% endif %}{% endfor %}",
                {"form": form},
            )
            == "BoundField"
        )

    def test_a_loop_variable_in_the_context_is_the_real_field(self, engines, user):
        """A loop variable lives only in the engine's value stack; the bridge builds the context a tag
        reads it from with ``value_into_handler_pyobject``. That route hands the live object under the
        LEAF floor: the walk's form proxy is for a path, and a ``takes_context`` tag is the app's code."""
        form = UserChangeForm(instance=user)
        body = (
            "{% for f in form %}{% if f.name == 'password' %}"
            "{% ctx_kind_3790 'f' %}{% ctx_value_3790 'f' %}"
            "{% endif %}{% endfor %}"
        )
        assert _render(engines, "rust", body, {"form": form}) == "BoundField[%s]" % user.password
        assert (
            _render(engines, "rust", "{% ctx_kind_3790 'form' %}", {"form": form})
            == "UserChangeForm"
        )

    def test_a_tag_that_calls_value_reads_the_real_value(self, engines, user):
        form = UserChangeForm(instance=user)
        out = _render(engines, "rust", "{% value_3790 form.password %}", {"form": form})
        assert out == "[%s]" % user.password

    def test_a_tag_authors_own_code_reads_the_form_it_was_handed(self, engines, user):
        form = UserChangeForm(instance=user)
        out = _render(engines, "rust", "{% initial_3790 form %}", {"form": form})
        assert out == "[%s]" % user.password

    def test_a_tag_operand_spelled_as_a_path_is_floored(self, engines, user):
        form = UserChangeForm(instance=user)
        for body in (
            "{% echo_3790 form.initial.password %}",
            "{% echo_3790 form.password.value %}",
            "{% echo_3790 form.password.form.initial.password %}",
        ):
            assert _render(engines, "rust", body, {"form": form}) == "[]", body

    def test_a_benign_path_operand_reaches_the_tag(self, engines, user):
        form = UserChangeForm(instance=user)
        assert (
            _render(engines, "rust", "{% echo_3790 form.initial.username %}", {"form": form})
            == "[floor-user]"
        )
        assert (
            _render(engines, "rust", "{% echo_3790 form.username.value %}", {"form": form})
            == "[floor-user]"
        )


class Page:
    """A plain object holding a form: reached through the engine's attribute walk. Until #3767 a
    LiveView's own top-level ``form`` attribute was not part of the raw-object sidecar
    (``mixins/rust_bridge.py`` flattened it into the state and skipped it), so it never reached these
    paths; it does now, and ``test_form_floor_differential_3790.py::TestAFormHeldAsLiveViewState...``
    runs the whole grammar against a form held directly as view state."""

    def __init__(self, form) -> None:
        self.form = form


def _leak_template() -> str:
    return (
        "<div dj-root>"
        "[{{ page.form.initial.password }}]"
        "[{{ page.form.password.value }}]"
        "[{{ page.form.password.initial }}]"
        "[{% for f in page.form %}{{ f.value }}{% endfor %}]"
        "[{{ page.form.initial.username }}]"
        "</div>"
    )


class _EntryView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root></div>"
    username = "floor-user"

    def mount(self, request, **kwargs):
        self.page = Page(
            UserChangeForm(instance=get_user_model().objects.get(username=self.username))
        )


class TestTheRealEntries:
    def test_liveview_test_client(self, engines, user):
        from djust.testing import LiveViewTestClient

        class View(_EntryView):
            template = _leak_template()

        client = LiveViewTestClient(View)
        client.mount()
        html = client.render()
        assert user.password not in html
        assert "floor-user" in html, "the benign path still renders (the page did render)"

    def test_http_get(self, engines, user):
        from django.contrib.sessions.backends.db import SessionStore
        from django.test import RequestFactory

        class View(_EntryView):
            template = _leak_template()

        request = RequestFactory().get("/")
        request.user = user
        request.session = SessionStore()
        html = View.as_view()(request).content.decode()
        assert user.password not in html
        assert "floor-user" in html

    @pytest.mark.django_db(transaction=True)
    def test_websocket_mount(self, engines):
        import asyncio

        pytest.importorskip("channels")
        created = get_user_model().objects.create_user("ws-floor-user", "w@example.com", "pw-ws")
        _WsView.template = _leak_template().replace("floor-user", "ws-floor-user")

        async def run() -> str:
            from channels.testing import WebsocketCommunicator
            from djust.websocket import LiveViewConsumer

            class _Session:
                session_key = "ws-3790"

            communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            communicator.scope["session"] = _Session()
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from()
            await communicator.send_json_to(
                {"type": "mount", "view": f"{__name__}._WsView", "url": "/"}
            )
            frame = {}
            seen = []
            for _ in range(6):
                frame = await communicator.receive_json_from(timeout=30)
                seen.append(frame.get("type"))
                if frame.get("type") in ("mount", "error"):
                    break
            await communicator.disconnect()
            assert frame.get("type") == "mount", (seen, frame)
            return str(frame.get("html", ""))

        try:
            with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
                html = asyncio.run(run())
        finally:
            _WsView.template = "<div dj-root></div>"
        assert created.password not in html
        assert "ws-floor-user" in html, html[:300]


class _WsView(_EntryView):
    username = "ws-floor-user"


class TestThisFilesTagsDoNotPolluteTheWorker:
    """The ``{% load %}`` bridge registers a library's tags in the PROCESS-global Rust registry and
    nothing is unregistered: every tag this library registers carries the issue number (see
    ``test_bridge_floor_3771``)."""

    def test_the_probe_tags_the_file_defines_are_all_namespaced(self):
        assert register.tags, "no tags defined"
        assert all(name.endswith("_3790") for name in register.tags), sorted(register.tags)
