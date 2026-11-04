"""The serialization floor holds for ``json_viewer`` and for a form's loop variable (#3770 review).

PR #3770's first version moved ``json_viewer`` onto the ``{% load %}`` bridge so Django's own node
could dispatch on a value's TYPE. The bridge runs Django's ``Variable`` resolution over the RAW
objects of the handler dict, so ``{% json_viewer data=form.instance.password %}`` walked
``form -> instance -> password`` with no floor, where main resolved the operand in the Rust walk
(``Context::walk_live``), which re-protects after every segment (``_protect_sidecar_value``:
a ``Model`` becomes a floor-enforcing proxy). Django prints the hash too; the floor is what djust
adds. ``{{ form.instance.password }}`` was always empty.

The fix keeps ``json_viewer`` NATIVE and gives its handler the operand's TYPE instead of moving it
onto the bridge: a handler that declares ``TYPED_ARGS = True`` is handed, next to its string
arguments, the operands the engine resolved through that same floored walk, as typed Python values
(``value_into_handler_pyobject``). The operand is therefore resolved exactly as ``{{ }}`` resolves
it, and the type survives.

What each class proves:

* ``TestTheFloorHolds``: every operand spelling the review named (a ``UserChangeForm`` instance, a
  loop variable's form, a ``ModelChoiceField`` queryset, ``get_session_auth_hash``, a plain object
  holding a ``User``, a manager reached through an object, a view attribute) renders EMPTY under the
  Rust engine, with the secret asserted PRESENT under Django so the comparison is not two empty
  strings. Each also leaks if the operand is resolved by Django's node (the gate-off), proved
  directly by rendering the tag through the bridge.
* ``TestTheLeakThatPredatesThePr``: ``relative_time`` is a bridged tag. A raw operand path through a
  form leaks there on main as well; recorded as a control, not fixed here.

Refs #3756, #3755, #1986, #1646, #1468.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.template import Context, Engine

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}
LOAD = "{% load djust_components %}"


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    component_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend({"NAME": "floor-3756", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


@pytest.fixture
def user():
    return get_user_model().objects.create_user("floor-user", "f@example.com", "pw-3756")


class OwnerForm(forms.Form):
    owner = forms.ModelChoiceField(queryset=get_user_model().objects.all())


class Holder:
    """A plain object holding a ``User`` and a manager: not a form, not a model."""

    def __init__(self, user) -> None:
        self.user = user
        self.users = get_user_model().objects.all()


class View:
    def __init__(self, user) -> None:
        self.user = user


def _render(engines, which: str, src: str, context: dict) -> str:
    rust, django = engines
    if which == "rust":
        out = rust.from_string(LOAD + src).render(dict(context))
    else:
        out = django.from_string(LOAD + src).render(Context(dict(context)))
    return re.sub(r"\s+", " ", str(out))


def _contexts(user):
    return {
        "change": {"form": UserChangeForm(instance=user)},
        "owner": {"form": OwnerForm()},
        "object": {"obj": Holder(user)},
        "view": {"view": View(user)},
    }


#: (id, context key, template body, what must not appear). ``HASH`` / ``SESSION`` are filled
#: from the created user.
ROWS = [
    ("form.instance.password", "change", "{% json_viewer data=form.instance.password %}", "HASH"),
    (
        "loop var -> form.instance.password",
        "change",
        "{% for f in form %}{% json_viewer data=f.form.instance.password %}{% endfor %}",
        "HASH",
    ),
    (
        "queryset.first.password",
        "owner",
        "{% json_viewer data=form.fields.owner.queryset.first.password %}",
        "HASH",
    ),
    (
        "loop var -> get_session_auth_hash",
        "change",
        "{% for f in form %}{% json_viewer data=f.form.instance.get_session_auth_hash %}{% endfor %}",
        "SESSION",
    ),
    ("obj.user.password", "object", "{% json_viewer data=obj.user.password %}", "HASH"),
    (
        "obj.users.first.password",
        "object",
        "{% json_viewer data=obj.users.first.password %}",
        "HASH",
    ),
    (
        "view.user.get_session_auth_hash",
        "view",
        "{% json_viewer data=view.user.get_session_auth_hash %}",
        "SESSION",
    ),
    # the same secrets, as the operand of a filter chain and under a `with`
    (
        "with form.instance.password",
        "change",
        "{% with p=form.instance.password %}{% json_viewer data=p %}{% endwith %}",
        "HASH",
    ),
    (
        "form.instance.password|upper",
        "change",
        "{% json_viewer data=form.instance.password|upper %}",
        "HASH_UPPER",
    ),
]


def _secret(user, which: str) -> str:
    if which == "HASH":
        return user.password
    if which == "HASH_UPPER":
        return user.password.upper()
    return user.get_session_auth_hash()


class TestTheFloorHolds:
    @pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
    def test_the_rust_engine_does_not_print_the_secret(self, engines, user, row):
        _id, key, body, what = row
        ctx = _contexts(user)[key]
        secret = _secret(user, what)
        assert secret, "the fixture produced no secret to look for"
        django_out = _render(engines, "django", body, ctx)
        rust_out = _render(engines, "rust", body, ctx)
        # Django itself prints it (no floor there): the control that the secret is reachable.
        assert secret in django_out or secret.upper() in django_out or what == "HASH_UPPER", (
            f"control: Django did not print the secret for {_id}: {django_out[:200]}"
        )
        assert secret not in rust_out, (
            f"{_id}: the Rust engine printed the secret: {rust_out[:300]}"
        )
        assert secret.upper() not in rust_out and secret.lower() not in rust_out

    @pytest.mark.parametrize("row", ROWS, ids=[r[0] for r in ROWS])
    def test_the_secret_is_not_in_the_raw_json_block_either(self, engines, user, row):
        """``json_viewer`` writes the value twice: the tree and the ``<script>`` raw copy."""
        _id, key, body, what = row
        rust_out = _render(engines, "rust", body, _contexts(user)[key])
        assert "dj-json-viewer__raw" in rust_out
        raw = rust_out.split("dj-json-viewer__raw", 1)[1]
        assert _secret(user, what) not in raw

    def test_the_bridge_would_leak_the_same_operand(self, engines, user):
        """The gate-off, in the suite: Django's node over the raw objects is what leaked. Rendering
        the tag through the ``{% load %}`` bridge handler (the PR's first version of #3756) prints
        the hash for the headline row, so this test is red if the floor were not in the engine's walk."""
        from djust import _rust
        from djust.components.templatetags.djust_components import register
        from djust.template_libraries import LibraryTagHandler

        _rust.register_tag_handler(
            "json_viewer_bridged",
            LibraryTagHandler("djust_components", "json_viewer", register.tags["json_viewer"]),
        )
        try:
            out = _rust.render_template(
                "{% json_viewer_bridged data=form.instance.password %}",
                {"form": UserChangeForm(instance=user)},
            )
        finally:
            # The registry is process-global: leave no handler behind for the next test.
            _rust.unregister_tag_handler("json_viewer_bridged")
        assert user.password in out, "the bridge no longer leaks: this control has lost its meaning"

    def test_a_loop_variables_own_attributes_still_render(self, engines, user):
        """The floor must not be a blanket refusal: public, non-floored attributes still show."""
        body = "{% for f in form %}{% if f.name == 'username' %}{% json_viewer data=f.label %}{% endif %}{% endfor %}"
        out = _render(engines, "rust", body, {"form": UserChangeForm(instance=user)})
        assert "Username" in out

    def test_json_viewer_still_renders_a_plain_user_field(self, engines, user):
        out = _render(
            engines, "rust", "{% json_viewer data=obj.user.username %}", {"obj": Holder(user)}
        )
        assert "floor-user" in out


class TestTheLeakThatPredatesThePr:
    """``relative_time`` is a BRIDGED tag: Django's node walks raw objects, so the floor does not
    apply to it. The first five rows are MAIN's behaviour (measured against a build of the base
    commit: every one prints the hash there too), kept as controls so the PR is shown not to have
    moved them, and reported as #3771 rather than widened here. The last row is a new SPELLING of
    the same reach (the PR hands a bridged tag a live ``BoundField`` for a loop variable; on main
    ``f`` was the widget HTML and had no ``.form``)."""

    CONTROLS = [
        ("form.instance.password", "change", "form.instance.password", "HASH"),
        (
            "form.fields.owner.queryset.first.password",
            "owner",
            "form.fields.owner.queryset.first.password",
            "HASH",
        ),
        ("obj.user.password", "object", "obj.user.password", "HASH"),
        ("obj.users.first.password", "object", "obj.users.first.password", "HASH"),
        ("view.user.get_session_auth_hash", "view", "view.user.get_session_auth_hash", "SESSION"),
    ]

    @pytest.mark.parametrize("row", CONTROLS, ids=[r[0] for r in CONTROLS])
    def test_a_bridged_tag_walks_raw_objects_as_it_did_on_main(self, engines, user, row):
        _id, key, expr, what = row
        body = "{% relative_time datetime=" + expr + " %}"
        out = _render(engines, "rust", body, _contexts(user)[key])
        assert _secret(user, what) in out, (
            "this control records that a BRIDGED tag does not apply the floor (#3771); if it now "
            "does, delete the control and close the issue"
        )
        # and `{{ }}` over the same path is refused, which is what makes it a floor bypass
        plain = _render(engines, "rust", "{{ " + expr + " }}", _contexts(user)[key])
        assert _secret(user, what) not in plain

    def test_the_loop_variable_spelling_is_a_new_spelling_of_the_same_reach(self, engines, user):
        body = (
            "{% for f in form %}{% relative_time datetime=f.form.instance.password %}{% endfor %}"
        )
        out = _render(engines, "rust", body, _contexts(user)["change"])
        assert user.password in out, "see #3771"
        # json_viewer, native and floored, is unaffected by the same spelling (the first
        # `TestTheFloorHolds` rows), which is what the review asked for.
        viewer = _render(
            engines,
            "rust",
            "{% for f in form %}{% json_viewer data=f.form.instance.password %}{% endfor %}",
            _contexts(user)["change"],
        )
        assert user.password not in viewer


class TestAFormCostsNothingItDoesNotUse:
    """Performance review of #3770: a ``Form`` crossing as an object used to enumerate the
    subwidgets of every field at conversion, which for a ``ModelChoiceField`` re-evaluates its
    queryset. A ``Form`` / ``BoundField`` is now carried like a one-shot iterator (handle live,
    items left ``None``) and read at the sink that needs them."""

    def _queries(self, engines, src: str) -> int:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        rust, _django = engines
        template = rust.from_string(src)
        form = OwnerForm()
        with CaptureQueriesContext(connection) as captured:
            template.render({"form": form})
        return len(captured)

    def test_a_template_that_never_touches_the_form_does_not_enumerate_subwidgets(
        self, engines, user
    ):
        # One query is the eager ``str(form)`` (the as_div render), which a lazy ``display`` would
        # remove (#3769). Enumerating the subwidgets at conversion took it to three.
        assert self._queries(engines, "x") <= 1

    def test_the_loop_still_reads_the_subwidgets(self, engines, user):
        """The sink that needs the items gets them: ``{% for w in f %}`` over a choice field."""
        rust, django = engines
        body = "{% for f in form %}{% for w in f %}{{ w.choice_label }},{% endfor %}{% endfor %}"
        out_r = re.sub(r"\s+", " ", rust.from_string(body).render({"form": OwnerForm()}))
        out_d = re.sub(r"\s+", " ", django.from_string(body).render(Context({"form": OwnerForm()})))
        assert out_r == out_d and "floor-user" in out_d
