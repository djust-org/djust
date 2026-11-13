"""A tag rendered through the ``{% load %}`` bridge is held to the serialization floor (#3771).

``{{ obj.user.password }}`` is empty under the Rust engine: ``Context::walk_live`` re-protects after
every segment (``_protect_sidecar_value``: a ``Model`` becomes a floor-enforcing proxy), so the path
is refused where it first meets the model. A tag rendered through the ``{% load %}`` bridge
(``template_libraries``) was not held to it. The bridge hands Django's own node the operand TOKENS
and the node resolves them with ``Variable._resolve_lookup`` over the RAW objects of the handler
dict, so ``{% relative_time datetime=obj.user.password %}`` walked ``obj -> user -> password`` with no
floor and printed the hash. ``_protect_sidecar_value`` only wraps a ``Model`` / ``Manager`` /
``QuerySet`` that is itself an operand's root; it does nothing for a ``Form``, a ``BoundField`` or a
plain object that HOLDS a model.

The fix keeps the node Django's and floors the WALK: every operand ``FilterExpression`` the bridge
compiles (``_parser``) carries a ``Variable`` whose lookup runs through a floor-enforcing walker, one
segment at a time, and releases the operand (a protected model stays a protected model, anything else
arrives as itself). What each class proves:

* ``TestTheFloorHolds``: the five spellings the issue measured, plus the spellings a reviewer would
  try next (a dict, a list, a namedtuple, a lazy object, a method, a property, a mapping, a
  manager's ``all.0``, a view attribute), each under an operand shape (keyword, positional,
  ``|filter`` chain, ``|default``, ``as var``, a ``{% for %}`` loop variable), print NOTHING of the
  secret under the Rust engine. Django prints it for every one (the control), so the comparison is
  never two empty strings.
* ``TestBenignOutputIsByteIdentical``: the floor is not a blanket refusal. Public, non-floored
  paths render exactly as Django renders them.
* ``TestWhatATagIsHanded``: the invariants of the operand a tag receives, pinned so a change to the
  walker cannot move them silently.

Refs #3771, #3756, #1986, #1646, #1468.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from django import forms, template
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserChangeForm
from django.template import Context, Engine
from django.test import override_settings
from django.utils.functional import SimpleLazyObject

from djust.live_view import LiveView
from djust import state
from djust.tests._live_session import with_live_session

pytestmark = [pytest.mark.django_db, pytest.mark.pr_tier]

register = template.Library()


@register.simple_tag
def echo_3771(value=None, other=None):
    """A third-party-shaped tag: it prints whatever it is handed."""
    return "[%s]" % (value if other is None else "%s|%s" % (value, other))


@register.simple_tag
def kind_3771(value):
    """The type of the operand the tag is handed (what the invariants below pin)."""
    return type(value).__name__


@register.simple_tag(takes_context=True)
def reads_context_3771(context):
    """A tag AUTHOR's own code reading the context: trusted, never floored."""
    obj = context["obj"]
    return "%s:%s" % (type(obj).__name__, obj.user.username)


LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "probe3771": __name__,
    "i18n": "django.templatetags.i18n",
    "cache": "django.templatetags.cache",
}
LOAD = "{% load djust_components probe3771 %}"


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend

    component_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {
            "NAME": "floor-3771",
            "DIRS": [],
            "APP_DIRS": True,
            "OPTIONS": {"libraries": {"probe3771": __name__}},
        }
    )
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


@pytest.fixture(scope="module")
def password_hash() -> str:
    """Hash the user's password once: PBKDF2 was most of each case's setup."""
    from django.contrib.auth.hashers import make_password

    return make_password("pw-3771")


@pytest.fixture
def user(password_hash):
    # ``create_user`` minus the per-case hash: the same row, with a module-wide hash.
    return get_user_model().objects.create(
        username="floor-user", email="f@example.com", password=password_hash
    )


@pytest.fixture(autouse=True)
def _model_accessors_that_return_containers(monkeypatch):
    """A model method and two properties that return a plain container of models, the shape a real
    ``friends`` / ``members`` / ``related_*`` accessor has. Patched onto ``User`` for the test only."""
    User = get_user_model()
    monkeypatch.setattr(User, "get_friends", lambda self: [self], raising=False)
    monkeypatch.setattr(User, "friends_prop", property(lambda self: [self]), raising=False)
    monkeypatch.setattr(User, "friends_map", property(lambda self: {"best": self}), raising=False)


class OwnerForm(forms.Form):
    owner = forms.ModelChoiceField(queryset=get_user_model().objects.all())

    def __init__(self, *args, only=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if only is not None:
            self.fields["owner"].queryset = get_user_model().objects.filter(pk=only.pk)


class Mapped(Mapping):
    """A ``Mapping`` that is not a ``dict``."""

    def __init__(self, data) -> None:
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


Pair = collections.namedtuple("Pair", "label user")


class Inner:
    def __init__(self, user) -> None:
        self.user = user


class Holder:
    """A plain object holding a ``User`` in every way a template author could reach one."""

    title = "a title"

    def __init__(self, user) -> None:
        self.user = user
        self.users = get_user_model().objects.filter(pk=user.pk)
        self.rows = [{"user": user}]
        self.pair = Pair("pair", user)
        self.lazy = SimpleLazyObject(lambda: user)
        self.mapping = Mapped({"user": user})
        self.holder = Inner(user)
        self.tup = ("t", user)

    def get_user(self):
        return self.user

    @property
    def user_prop(self):
        return self.user

    @property
    def items(self):
        return [self.user]


class View:
    def __init__(self, user) -> None:
        self.user = user


def _contexts(user):
    return {
        "change": {"form": UserChangeForm(instance=user)},
        "owner": {"form": OwnerForm(only=user)},
        "object": {
            "obj": Holder(user),
            "d": {"user": user},
            "qs": get_user_model().objects.filter(pk=user.pk),
            "user": user,
        },
        "view": {"view": View(user)},
    }


def _secret(user, which: str) -> str:
    if which == "HASH":
        return user.password
    return user.get_session_auth_hash()


def _render(engines, which: str, src: str, context: dict) -> str:
    rust, django = engines
    if which == "rust":
        out = rust.from_string(LOAD + src).render(dict(context))
    else:
        out = django.from_string(LOAD + src).render(Context(dict(context)))
    return re.sub(r"\s+", " ", str(out))


#: (id, context key, path, which secret). The first five are the issue's measured spellings.
PATHS = [
    ("form.instance.password", "change", "form.instance.password", "HASH"),
    (
        "form.fields.owner.queryset.first.password",
        "owner",
        "form.fields.owner.queryset.first.password",
        "HASH",
    ),
    ("obj.user.password", "object", "obj.user.password", "HASH"),
    ("obj.users.first.password", "object", "obj.users.first.password", "HASH"),
    # ---- the spellings a reviewer would try next --------------------------------------------
    ("obj.users.all.0.password", "object", "obj.users.all.0.password", "HASH"),
    ("obj.rows.0.user.password", "object", "obj.rows.0.user.password", "HASH"),
    ("d.user.password", "object", "d.user.password", "HASH"),
    ("obj.pair.user.password", "object", "obj.pair.user.password", "HASH"),
    ("obj.tup.1.password", "object", "obj.tup.1.password", "HASH"),
    ("obj.lazy.password", "object", "obj.lazy.password", "HASH"),
    ("obj.mapping.user.password", "object", "obj.mapping.user.password", "HASH"),
    ("obj.holder.user.password", "object", "obj.holder.user.password", "HASH"),
    ("obj.get_user.password", "object", "obj.get_user.password", "HASH"),
    ("obj.user_prop.password", "object", "obj.user_prop.password", "HASH"),
    ("obj.items.0.password", "object", "obj.items.0.password", "HASH"),
    ("view.user.password", "view", "view.user.password", "HASH"),
    # ---- the floor must not stop at the first floor proxy (review of #3789) ----------------
    # A model/queryset proxy re-protects only its own immediate return value, and only if that value
    # is itself a model or queryset. A stock queryset method or a model property that returns a plain
    # CONTAINER of models hands Django a raw container, whose next step reads a raw model. `PK` is
    # replaced by the fixture's primary key.
    ("qs.in_bulk.PK.password", "object", "qs.in_bulk.PK.password", "HASH"),
    ("obj.users.in_bulk.PK.password", "object", "obj.users.in_bulk.PK.password", "HASH"),
    ("obj.user.friends_prop.0.password", "object", "obj.user.friends_prop.0.password", "HASH"),
    ("obj.user.friends_map.best.password", "object", "obj.user.friends_map.best.password", "HASH"),
    ("obj.user.get_friends.0.password", "object", "obj.user.get_friends.0.password", "HASH"),
    ("user.get_friends.0.password", "object", "user.get_friends.0.password", "HASH"),
    ("qs.first.friends_prop.0.password", "object", "qs.first.friends_prop.0.password", "HASH"),
    (
        "qs.in_bulk.PK.get_friends.0.get_session_auth_hash",
        "object",
        "qs.in_bulk.PK.get_friends.0.get_session_auth_hash",
        "SESSION",
    ),
    (
        "obj.user.get_session_auth_hash",
        "object",
        "obj.user.get_session_auth_hash",
        "SESSION",
    ),
    (
        "view.user.get_session_auth_hash",
        "view",
        "view.user.get_session_auth_hash",
        "SESSION",
    ),
    (
        "form.instance.get_session_auth_hash",
        "change",
        "form.instance.get_session_auth_hash",
        "SESSION",
    ),
]

#: How the operand is spelled in the tag. ``{p}`` is the path.
SHAPES = [
    ("relative_time kw", "{{% relative_time datetime={p} %}}"),
    ("echo positional", "{{% echo_3771 {p} %}}"),
    ("echo kw", "{{% echo_3771 value={p} %}}"),
    ("echo second positional", "{{% echo_3771 1 {p} %}}"),
    ("filter upper", "{{% echo_3771 {p}|upper %}}"),
    ("filter default", '{{% echo_3771 {p}|default:"-" %}}'),
    ("filter add", '{{% echo_3771 {p}|add:"" %}}'),
    ("filter as argument", '{{% echo_3771 ""|default:{p} %}}'),
    ("as var", "{{% echo_3771 {p} as got %}}<{{{{ got }}}}>"),
    ("if", "{{% if {p} %}}{{% echo_3771 {p} %}}{{% endif %}}"),
]


def _body(shape: str, path: str, loop: bool) -> str:
    body = shape.format(p=path)
    if loop:
        body = "{% for f in form %}" + body.replace("form.", "f.form.", 1) + "{% endfor %}"
    return body


class TestTheFloorHolds:
    @pytest.mark.parametrize("shape", SHAPES, ids=[s[0] for s in SHAPES])
    @pytest.mark.parametrize("row", PATHS, ids=[r[0] for r in PATHS])
    def test_the_rust_engine_does_not_print_the_secret(self, engines, user, row, shape):
        _id, key, path, which = row
        path = path.replace("PK", str(user.pk))
        ctx = _contexts(user)[key]
        secret = _secret(user, which)
        assert secret, "the fixture produced no secret to look for"
        body = shape[1].format(p=path)
        django_out = _render(engines, "django", body, ctx)
        try:
            rust_out = _render(engines, "rust", body, ctx)
        except template.VariableDoesNotExist as exc:
            # A FILTER ARGUMENT that does not resolve raises in Django too (`{{ x|default:missing }}`):
            # the refused path is "missing", and Django's own consequence follows. The point is that
            # nothing of the secret is in what is raised.
            assert shape[0] == "filter as argument", shape
            rust_out = "<<%s>>" % exc
        if shape[0] in ("filter upper",):
            needles = (secret.upper(), secret)
        else:
            needles = (secret,)
        # The control: Django has no floor, so it prints the secret.
        assert any(n in django_out for n in needles), (
            f"control: Django did not print the secret for {_id} / {shape[0]}: {django_out[:200]}"
        )
        for needle in needles:
            assert needle not in rust_out, (
                f"{_id} / {shape[0]}: the Rust engine printed the secret: {rust_out[:300]}"
            )
        assert secret.lower() not in rust_out.lower()

    @pytest.mark.parametrize(
        "path", ["f.form.instance.password", "f.form.instance.get_session_auth_hash"]
    )
    @pytest.mark.parametrize("shape", SHAPES[:3], ids=[s[0] for s in SHAPES[:3]])
    def test_a_loop_variable_does_not_print_the_secret(self, engines, user, path, shape):
        """The #3770 spelling: ``{% for f in form %}`` hands a bridged tag a live ``BoundField``."""
        ctx = _contexts(user)["change"]
        secret = _secret(user, "SESSION" if "session" in path else "HASH")
        body = "{% for f in form %}" + shape[1].format(p=path) + "{% endfor %}"
        assert secret in _render(engines, "django", body, ctx), "control"
        assert secret not in _render(engines, "rust", body, ctx)

    def test_a_with_binding_is_unchanged(self, engines, user):
        """``{% with %}`` is native: it resolves through the floored walk already."""
        body = "{% with p=obj.user.password %}{% echo_3771 p %}{% endwith %}"
        assert user.password not in _render(engines, "rust", body, _contexts(user)["object"])

    def test_an_underscore_name_is_refused_at_compile_by_both_engines(self, engines, user):
        """``_meta`` / ``_state`` are not a template-reachable name: Django refuses the
        variable at compile time, and the bridge compiles with Django's own parser."""
        for engine in ("django", "rust"):
            with pytest.raises(Exception, match="underscore"):
                _render(
                    engines, engine, "{% echo_3771 obj.user._meta %}", _contexts(user)["object"]
                )

    def test_str_of_the_model_is_not_a_secret(self, engines, user):
        """``{% echo_3771 obj.user %}`` prints ``str(user)`` (the username) under both engines."""
        body = "{% echo_3771 obj.user %}"
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)
        assert "floor-user" in _render(engines, "rust", body, ctx)


class TestBenignOutputIsByteIdentical:
    BENIGN = [
        ("object", "obj.title"),
        ("object", "obj.title|upper"),
        ("object", "obj.user.username"),
        ("object", "obj.user.email"),
        ("object", "obj.user.get_full_name"),
        ("object", "obj.user.is_active"),
        ("object", "obj.users.count"),
        ("object", "obj.users.first.username"),
        ("object", "obj.users.all.0.username"),
        ("object", "obj.rows.0.user.username"),
        ("object", "d.user.email"),
        ("object", "obj.pair.label"),
        ("object", "obj.pair.0"),
        ("object", "obj.tup.0"),
        ("object", "obj.lazy.username"),
        ("object", "obj.mapping.user.username"),
        ("object", "obj.get_user.username"),
        ("object", "obj.user_prop.username"),
        ("object", "obj.items.0.username"),
        ("object", "obj.missing.attr"),
        ("object", "obj.user.nope"),
        ("object", "obj.rows.5"),
        ("object", "obj.rows.0.nope"),
        ("object", "obj.user.has_usable_password"),
        ("object", "obj.user.check_password"),
        ("object", "obj.user.delete"),
        ("change", "form.instance.username"),
        ("change", "form.fields.username.label"),
        ("change", "form.username.label"),
        ("change", "form.username.value"),
        ("change", "form.initial.username"),
        ("owner", "form.fields.owner.queryset.first.username"),
        ("owner", "form.owner.label"),
        ("view", "view.user.username"),
    ]

    @pytest.mark.parametrize("key,path", BENIGN, ids=[f"{k}:{p}" for k, p in BENIGN])
    @pytest.mark.parametrize(
        "shape",
        [
            "{{% relative_time datetime={p} %}}",
            "{{% echo_3771 {p} %}}",
            '{{% echo_3771 {p}|default:"-" %}}',
            "{{% echo_3771 {p} as got %}}<{{{{ got }}}}>",
        ],
    )
    def test_the_output_is_django_s(self, engines, user, key, path, shape):
        ctx = _contexts(user)[key]
        body = shape.format(p=path)
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)

    def test_a_nested_dict_of_scalars_is_walked_as_before(self, engines):
        ctx = {"d": {"a": {"b": [10, {"c": "deep"}]}}}
        body = "{% echo_3771 d.a.b.1.c %}|{% echo_3771 d.a.b.0 %}|{% echo_3771 d.a.missing %}"
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)

    def test_a_loop_variable_still_reaches_public_attributes(self, engines, user):
        body = "{% for f in form %}{% echo_3771 f.label %}{% echo_3771 f.name %}{% endfor %}"
        ctx = _contexts(user)["change"]
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)

    def test_a_bound_field_errors_attribute_still_reaches_a_tag(self, engines, user):
        form = UserChangeForm({"username": ""}, instance=user)
        body = "{% for f in form %}{% echo_3771 f.errors %}{% endfor %}"
        out = _render(engines, "rust", body, {"form": form})
        assert out == _render(engines, "django", body, {"form": form})
        assert "required" in out.lower() or "errorlist" in out


class TestWhatATagIsHanded:
    """The operand a tag receives. These are the invariants of the walker, not of any one tag."""

    def test_a_scalar_operand_is_the_same_object(self, engines, user):
        assert (
            _render(engines, "rust", "{% kind_3771 obj.title %}", _contexts(user)["object"])
            == "str"
        )

    def test_a_plain_object_operand_arrives_as_itself(self, engines, user):
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", "{% kind_3771 obj.holder %}", ctx) == "Inner"
        assert _render(engines, "rust", "{% kind_3771 obj %}", ctx) == "Holder"

    def test_a_container_operand_keeps_its_type(self, engines, user):
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", "{% kind_3771 obj.rows %}", ctx) == "list"
        assert _render(engines, "rust", "{% kind_3771 obj.pair %}", ctx) == "Pair"
        assert _render(engines, "rust", "{% kind_3771 obj.mapping %}", ctx) == "Mapped"

    def test_a_model_operand_is_the_same_wherever_it_is_spelled(self, engines, user):
        """A model handed to a tag is the floor-enforcing proxy; the type must not depend on the
        spelling (a top-level ``user`` versus a path through a holder)."""
        ctx = dict(_contexts(user)["object"], user=user)
        top = _render(engines, "rust", "{% kind_3771 user %}", ctx)
        assert top == _render(engines, "rust", "{% kind_3771 obj.user %}", ctx)
        assert top == _render(engines, "rust", "{% kind_3771 d.user %}", ctx)
        assert top == _render(engines, "rust", "{% kind_3771 obj.rows.0.user %}", ctx)

    def test_a_tag_authors_own_code_reads_the_context_unfloored(self, engines, user):
        """``takes_context``: the TAG's Python is trusted. Only the template author's expression is
        walked through the floor."""
        out = _render(engines, "rust", "{% reads_context_3771 %}", _contexts(user)["object"])
        assert out == "Holder:floor-user"


# ---------------------------------------------------------------------------------------------
# A raw ``@register.tag`` builds its own node, and most resolve their operands with a
# ``template.Variable`` they construct themselves, at compile time (djust's own component tags,
# ``components/templatetags/_registry.py``) or at render time. A compile-time swap of the
# ``FilterExpression`` ``Variable`` would miss both; the floor is on the ``Variable`` resolution
# itself, gated on the ``Context`` the bridged node renders on.


class _CompileTimeVariableNode(template.Node):
    def __init__(self, variable) -> None:
        self.variable = variable

    def render(self, context):
        return "<%s>" % self.variable.resolve(context)


class _RenderTimeVariableNode(template.Node):
    def __init__(self, expr) -> None:
        self.expr = expr

    def render(self, context):
        return "<%s>" % template.Variable(self.expr).resolve(context)


@register.tag("raw_compile_var_3771")
def _raw_compile_var(parser, token):
    return _CompileTimeVariableNode(template.Variable(token.split_contents()[1]))


@register.tag("raw_render_var_3771")
def _raw_render_var(parser, token):
    return _RenderTimeVariableNode(token.split_contents()[1])


class TestARawTagThatBuildsItsOwnVariable:
    @pytest.mark.parametrize("tag", ["raw_compile_var_3771", "raw_render_var_3771"])
    @pytest.mark.parametrize("row", PATHS[:8], ids=[r[0] for r in PATHS[:8]])
    def test_the_floor_holds(self, engines, user, tag, row):
        _id, key, path, which = row
        path = path.replace("PK", str(user.pk))
        body = "{%% %s %s %%}" % (tag, path)
        ctx = _contexts(user)[key]
        secret = _secret(user, which)
        assert secret in _render(engines, "django", body, ctx), "control"
        try:
            out = _render(engines, "rust", body, ctx)
        except template.VariableDoesNotExist as exc:
            # A raw tag that calls ``Variable.resolve`` does not catch the miss, in Django or here:
            # the refused path is "missing", and the tag's own consequence follows.
            out = "<<%s>>" % exc
        assert secret not in out

    @pytest.mark.parametrize("tag", ["raw_compile_var_3771", "raw_render_var_3771"])
    def test_a_benign_path_renders_as_django_renders_it(self, engines, user, tag):
        body = "{%% %s obj.user.username %%}" % tag
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)


class SilentFailure(Exception):
    silent_variable_failure = True


class _Methods:
    """Every shape of callable Django's ``_resolve_lookup`` branches on."""

    def plain(self):
        return "plain"

    def needs_args(self, required):
        return "never"

    def alters(self):
        raise AssertionError("alters_data must not be called")

    alters.alters_data = True

    def keep(self):
        raise AssertionError("do_not_call_in_templates must not be called")

    keep.do_not_call_in_templates = True

    def quiet(self):
        raise SilentFailure()

    def loud(self):
        raise RuntimeError("loud")

    def inner_type_error(self):
        raise TypeError("raised inside the call")

    def returns_object(self):
        return Inner("inner")

    @property
    def raising_property(self):
        raise AttributeError("from a property")


class TestDjangoSCallableRulesAreKept:
    """The walker is transparent to ``callable`` / ``alters_data`` / ``do_not_call_in_templates`` /
    signature binding / ``silent_variable_failure`` / a property that raises ``AttributeError``."""

    CASES = [
        "m.plain",
        "m.needs_args",
        "m.alters",
        "m.keep",
        "m.quiet",
        "m.inner_type_error",
        "m.returns_object.user",
        "m.raising_property",
        "m.nope",
    ]

    @pytest.mark.parametrize("path", CASES)
    def test_the_output_is_django_s(self, engines, path):
        body = "{% echo_3771 " + path + " %}"
        ctx = {"m": _Methods()}
        results = []
        for engine in ("django", "rust"):
            try:
                results.append(("ok", _render(engines, engine, body, ctx)))
            except Exception as exc:  # noqa: BLE001 - the exception type IS the answer
                results.append(("raised", type(exc).__name__))
        assert results[0] == results[1], (path, results)

    def test_loud_exceptions_cross_whole(self, engines):
        for engine in ("django", "rust"):
            with pytest.raises(RuntimeError, match="loud"):
                _render(engines, engine, "{% echo_3771 m.loud %}", {"m": _Methods()})


class TestTheScopeIsExact:
    """The patch of ``Variable._resolve_lookup`` is inert except for the ``Context`` a bridged node is
    rendered on."""

    def test_a_django_engine_in_the_same_process_is_untouched(self, engines, user):
        _render(engines, "rust", "{% echo_3771 obj.user.username %}", _contexts(user)["object"])
        # The floor patch is installed now. Django's own engine still prints what Django prints.
        out = _render(engines, "django", "{{ obj.user.password }}", _contexts(user)["object"])
        assert user.password in out

    def test_a_context_other_than_the_registered_one_is_not_floored(self, user):
        from django.template import Context as DjangoContext
        from djust.template_bridge_floor import floor_scope

        other = DjangoContext({"obj": Holder(user)})
        registered = DjangoContext({"obj": Holder(user)})
        with floor_scope(registered):
            assert template.Variable("obj.user.password").resolve(other) == user.password
            with pytest.raises(template.VariableDoesNotExist):
                template.Variable("obj.user.password").resolve(registered)

    def test_the_scope_is_left_on_an_exception(self):
        from django.template import Context as DjangoContext
        from djust.template_bridge_floor import _floor_root, floor_scope

        registered = DjangoContext({})
        with pytest.raises(RuntimeError):
            with floor_scope(registered):
                assert _floor_root.get() is registered
                raise RuntimeError("boom")
        assert _floor_root.get() is None

    def test_scopes_nest(self):
        from django.template import Context as DjangoContext
        from djust.template_bridge_floor import _floor_root, floor_scope

        outer, inner = DjangoContext({}), DjangoContext({})
        with floor_scope(outer):
            with floor_scope(inner):
                assert _floor_root.get() is inner
            assert _floor_root.get() is outer
        assert _floor_root.get() is None

    def test_every_render_of_a_bridged_node_is_inside_the_scope(self):
        """Pins the callers: the inline bridge, the raw-block bridge and the cache operand each
        render under ``floor_scope``. A fourth place that renders a bridged node without it would be
        a bypass, so it has to show up here (the ``+ 1`` is the load-time probe, which renders a
        sentinel body and resolves no operand)."""
        src = (Path(__file__).resolve().parents[1] / "djust" / "template_libraries.py").read_text()
        assert src.count("with floor_scope(") == 3, "inline, raw-block, cache operand"
        scoped = re.findall(
            r"with floor_scope\(ctx\):\n\s+(?:output = |return )?[^\n]*\(ctx\)", src
        )
        assert len(scoped) == 3, scoped
        assert len(re.findall(r"\.render\(ctx\)", src)) == 2 + 1, "two scoped renders + the probe"


class TestAContainerOperandKeepsItsIdentity:
    def test_a_list_with_no_model_is_the_callers_list(self, engines):
        seen = []
        items = [1, 2, {"a": 3}]
        _Capture.seen = seen
        rust, _django = engines
        rust.from_string("{% load probe3771 %}{% capture_3771 obj.items %}").render(
            {"obj": type("H", (), {"items": items})()}
        )
        assert seen and seen[0] is items

    def test_a_container_that_holds_a_model_is_protected_as_a_top_level_one_is(self, engines, user):
        seen = []
        _Capture.seen = seen
        rust, _django = engines
        holder = Holder(user)
        rust.from_string("{% load probe3771 %}{% capture_3771 obj.rows %}").render({"obj": holder})
        assert seen and seen[0] is not holder.rows
        assert type(seen[0][0]["user"]).__name__ == "_SidecarModelProxy"
        assert not hasattr(seen[0][0]["user"], "password")
        assert holder.rows[0]["user"] is user, "the caller's own container is never mutated"


class _Capture:
    seen: list = []


@register.simple_tag(name="capture_3771")
def _capture(value):
    _Capture.seen.append(value)
    return ""


class TestEveryBridgedNodeKind:
    """The inline bridge, the raw-block bridge (``{% blocktranslate %}``) and the hand-written
    ``{% cache %}`` handler each render a node on their own ``Context``; each is floored."""

    def test_a_raw_block_tag_resolves_its_with_operands_through_the_floor(self, engines, user):
        body = (
            "{% load i18n %}{% blocktranslate with p=obj.user.password %}[{{ p }}]"
            "{% endblocktranslate %}"
        )
        ctx = _contexts(user)["object"]
        assert user.password in _render(engines, "django", body, ctx), "control"
        assert user.password not in _render(engines, "rust", body, ctx)

    def test_a_raw_block_tag_still_renders_a_benign_operand(self, engines, user):
        body = (
            "{% load i18n %}{% blocktranslate with p=obj.user.username %}[{{ p }}]"
            "{% endblocktranslate %}"
        )
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)

    def test_a_cache_vary_on_operand_is_resolved_through_the_floor(
        self, engines, user, monkeypatch
    ):
        """``{% cache 10 name obj.user.password %}`` keys the fragment on the operand's value: the
        secret must not be what the key is computed from."""
        import django.core.cache.utils as cache_utils

        seen = []
        real = cache_utils.make_template_fragment_key

        def spy(fragment_name, vary_on=None):
            seen.append(list(vary_on or []))
            return real(fragment_name, vary_on)

        monkeypatch.setattr(cache_utils, "make_template_fragment_key", spy)
        body = "{% load cache %}{% cache 10 frag3771 obj.user.password %}x{% endcache %}"
        ctx = _contexts(user)["object"]
        _render(engines, "rust", body, ctx)
        assert seen, "the cache handler did not compute a key"
        assert all(user.password not in map(str, vary) for vary in seen), seen


class TestTheWalkerHasTheProtocolOfItsObject:
    """Django decides what to try from the TYPE of what it holds (``hasattr(type(current),
    "__getitem__")``, ``callable(current)``) and from the names it is asked for, so a walker has
    exactly the protocol its object has and refuses what a template never resolves."""

    def test_the_class_follows_the_objects_own_getitem_and_call(self):
        from djust.template_bridge_floor import wrap

        class Plain:
            pass

        class Item:
            def __getitem__(self, key):
                return key

        class Call:
            def __call__(self):
                return 1

        class Both(Item, Call):
            pass

        for obj, item, call in [
            (Plain(), False, False),
            (Item(), True, False),
            (Call(), False, True),
            (Both(), True, True),
        ]:
            walker = wrap(obj)
            assert hasattr(type(walker), "__getitem__") is item, type(obj).__name__
            assert callable(walker) is call, type(obj).__name__

    def test_a_leading_underscore_is_never_resolved(self):
        """Django refuses an underscore name at compile time (``Variable``), so no template can
        spell one; a walker refuses it too, so a caller that builds a lookup by hand cannot reach
        ``__wrapped__`` or a private attribute of the object through it."""
        from djust.template_bridge_floor import wrap

        class Secret:
            _hidden = "h"
            public = "p"

        walker = wrap(Secret())
        assert walker.public == "p"
        for name in ("_hidden", "__wrapped__", "_other"):
            with pytest.raises(AttributeError):
                getattr(walker, name)


@register.simple_tag
def same_3771(a, b):
    """What a permission / ownership tag does with two operands."""
    return "%s/%s" % (a == b, hash(a) == hash(b))


class TestTwoViewsOfOneModelAreEqual:
    """A model reached by a path is the floor proxy. Before ``__eq__`` / ``__hash__`` delegated, two
    operands that were the same row compared UNEQUAL (a silent flip for a tag that checks ownership)."""

    @pytest.mark.parametrize(
        "a,b",
        [
            ("obj.user", "obj.user"),
            ("obj.user", "d.user"),
            ("obj.user", "user"),
            ("obj.lazy", "obj.user"),
            ("obj.rows.0.user", "obj.user"),
            ("qs.first", "obj.user"),
        ],
    )
    def test_the_same_row_is_equal_and_hashes_alike(self, engines, user, a, b):
        body = "{% same_3771 " + a + " " + b + " %}"
        ctx = _contexts(user)["object"]
        assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)
        assert _render(engines, "rust", body, ctx) == "True/True"

    def test_a_different_row_or_a_non_model_is_not_equal(self, engines, user):
        other = get_user_model().objects.create_user("other-3771", "o@example.com", "pw")
        ctx = dict(_contexts(user)["object"], other=other)
        for b in ("other", "obj.holder", "obj.title"):
            body = "{% same_3771 obj.user " + b + " %}"
            assert _render(engines, "rust", body, ctx) == _render(engines, "django", body, ctx)
            assert _render(engines, "rust", body, ctx).startswith("False")

    def test_equality_reveals_no_field(self, user):
        from djust.serialization import _SidecarModelProxy

        proxy = _SidecarModelProxy(user)
        assert proxy == user and proxy == _SidecarModelProxy(user)
        assert hash(proxy) == hash(user)
        with pytest.raises(AttributeError):
            proxy.password


# ---------------------------------------------------------------------------------------------
# The real entries. ``DjustTemplateBackend`` is one door; a LiveView is entered through the
# ``LiveViewTestClient``, an HTTP GET and a WebSocket mount, and each has its own sidecar and
# first-render path. The two spellings that stopped at the first floor proxy (a stock queryset
# method, a model property returning a container) must hold on all three.


def _leak_template(user) -> str:
    return (
        "<div dj-root>"
        "[{% relative_time datetime=obj.users.in_bulk." + str(user.pk) + ".password %}]"
        "[{% relative_time datetime=obj.user.friends_prop.0.password %}]"
        "[{% relative_time datetime=obj.user.get_friends.0.password %}]"
        "[{% relative_time datetime=obj.user.password %}]"
        "[{% relative_time datetime=obj.user.username %}]"
        "</div>"
    )


class _EntryView(LiveView):
    exposure_policy = "explicit"
    obj = state()
    template = "<div dj-root></div>"
    username = "floor-user"

    def mount(self, request, **kwargs):
        self.obj = Holder(get_user_model().objects.get(username=self.username))


class TestTheRealEntries:
    def test_liveview_test_client(self, engines, user):
        from djust.testing import LiveViewTestClient

        class View(_EntryView):
            template = _leak_template(user)

        client = LiveViewTestClient(View)
        client.mount()
        html = client.render()
        assert user.password not in html
        assert "floor-user" in html, "the benign operand still renders (the page did render)"

    def test_http_get(self, engines, user):
        from django.test import RequestFactory

        class View(_EntryView):
            template = _leak_template(user)

        request = RequestFactory().get("/")
        request.user = user
        from django.contrib.sessions.backends.db import SessionStore

        request.session = SessionStore()
        response = View.as_view()(request)
        html = response.content.decode()
        assert user.password not in html
        assert "floor-user" in html

    @pytest.mark.django_db(transaction=True)
    def test_websocket_mount(self, engines):
        import asyncio

        pytest.importorskip("channels")
        created = get_user_model().objects.create_user("ws-floor-user", "w@example.com", "pw-ws")
        _WsView.template = _leak_template(created)

        async def run() -> str:
            from channels.testing import WebsocketCommunicator
            from djust.websocket import LiveViewConsumer

            class _Session:
                session_key = "ws-3771"

            communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
            communicator.scope["session"] = _Session()
            connected, _ = await with_live_session(communicator).connect()
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


# ---------------------------------------------------------------------------------------------
# ``install()`` is a single assignment of a constant. The first version read the class attribute
# back at install time and stored it as "the original"; a thread that lost the race stored the
# already-patched function, and from then on every ``Variable.resolve`` in the process recursed.


class TestInstallCannotSelfReference:
    def test_the_original_is_django_s_not_the_patch(self):
        import djust.template_bridge_floor as floor

        assert floor._ORIGINAL is not floor.floored_resolve_lookup
        assert floor._ORIGINAL.__module__ == "django.template.base"

    def test_installing_again_and_again_never_changes_what_is_delegated_to(self):
        import djust.template_bridge_floor as floor

        original = floor._ORIGINAL
        for _ in range(5):
            floor.install()
        assert floor._ORIGINAL is original
        assert template.Variable("a.b").resolve({"a": {"b": 1}}) == 1

    def test_a_loser_of_the_race_sees_the_patched_class_and_still_cannot_recurse(self):
        """The interleaving, forced: the class attribute ALREADY holds the patch when a second
        install runs, which is exactly what the losing thread observed."""
        import djust.template_bridge_floor as floor

        floor.install()
        assert template.Variable._resolve_lookup is floor.floored_resolve_lookup
        floor.install()  # the loser
        assert floor._ORIGINAL is not floor.floored_resolve_lookup
        assert template.Variable("a.b").resolve({"a": {"b": 2}}) == 2

    def test_reimporting_the_module_after_it_patched_the_class_keeps_the_genuine_original(self):
        import importlib

        import djust.template_bridge_floor as floor

        genuine = floor._ORIGINAL
        floor.install()
        try:
            reloaded = importlib.reload(floor)
            assert reloaded._ORIGINAL is genuine
            assert template.Variable("a.b").resolve({"a": {"b": 3}}) == 3
        finally:
            floor.uninstall()

    def test_concurrent_installs(self):
        import sys
        import threading

        import djust.template_bridge_floor as floor

        floor.uninstall()
        errors = []
        barrier = threading.Barrier(8)

        def go():
            try:
                barrier.wait()
                for _ in range(200):
                    floor.install()
                    floor.uninstall()
                    floor.install()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            threads = [threading.Thread(target=go) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.setswitchinterval(old)
        assert not errors
        assert floor._ORIGINAL is not floor.floored_resolve_lookup
        assert template.Variable("a.b").resolve({"a": {"b": 4}}) == 4


class TestThisFilesTagsDoNotPolluteTheWorker:
    """The ``{% load %}`` bridge registers a library's tags in the PROCESS-global Rust registry and
    ``reassert()`` (run before every test by ``reset_djust_globals``) re-registers them for the rest of
    the worker; nothing is unregistered. CI shard 4/4 failed
    ``tests/unit/test_assign_tag.py::test_assign_tag_multiple_args_resolved_against_context``
    (``'[body|label][]' == '[body]'``) because this file's first version registered a tag called
    ``echo``, which won over that test's own assign tag of the same name. Not the floor: a name
    collision with another test's tag. Every tag this library registers carries the issue number."""

    def test_the_probe_tags_the_file_defines_are_all_namespaced(self):
        assert register.tags, "no tags defined"
        assert all(name.endswith("_3771") for name in register.tags), sorted(register.tags)
