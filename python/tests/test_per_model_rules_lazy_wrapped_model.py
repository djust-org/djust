"""A model's own serialization rules apply when the model is held behind a lazy wrapper.

``djust_exclude_fields``, ``djust_serializable_fields`` and the column-type rule (a ``BinaryField``
is never drawn) are properties of the model CLASS. A lazily wrapped model
(``django.utils.functional.SimpleLazyObject``) reports the model's
class through ``__class__`` but its ``type()`` is the wrapper, so a reader that looked at ``type(obj)``
found no per-model setting for it. The same holds for any transparent
proxy that reports the model's class through ``__class__``. The rules are now read from the class the
held model reports, on every path that serializes or draws a model:

* the engine's sidecar (a bare read, a dotted filter, a filtered rebinding, a loop over a list that holds
  it) on the native engine;
* a tag rendered through the ``{% load %}`` bridge;
* the eager dict (``normalize_django_value``, the JSON encoder, the JIT attribute gate and
  ``serialize_for_template``);
* ``request.user`` supplied by a context processor, on a plain GET and on the WebSocket render;
* a LiveView that holds the wrapper as its own state.

Each rule has its own cell and its own gate-off: the exclude list, the allowlist and the column type are
each switched off alone and the cell that pins that rule goes red, so no rule is covered only because
another one happens to hide the same name.

The column-type rule is pinned where it is applied (the sidecar and the eager dict); the JIT attribute gate and
the state path decide by name, so they are pinned with the exclude list and the allowlist.

A control sits beside every cell: Django's own engine prints the withheld value (so a cell cannot pass
because both sides are empty), and a field that is not withheld still draws.

Engine path: the native engine (``DjustTemplateBackend``), the Django bridge, and the eager serializer.

Refs the serialization floor, SECURE_DEFAULTS Pattern 1.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.db import models
from django.template import Context as DjangoContext
from django.template import Engine
from django.test import RequestFactory, override_settings
from django.utils.functional import SimpleLazyObject

from djust.live_view import LiveView
from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.optimization.codegen import emittable_names
from djust.serialization import (
    DjangoJSONEncoder,
    _SidecarModelProxy,
    normalize_django_value,
)
from djust.utils import clear_template_dirs_cache
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401

pytestmark = [pytest.mark.django_db]

EXCLUDED = "excluded-value-3f1a"
HIDDEN = "outside-allowlist-9c2b"
BINARY = b"binary-column-7d4e"
VISIBLE = "visible-name-1b6c"

#: rule -> (model attribute that is withheld by that rule alone)
RULES = {"exclude": "nickname", "allowlist": "hidden", "column-type": "blob"}
#: the rules that are decided by NAME (the exclude list and the allowlist); the JIT attribute gate and
#: the state path decide by name, the column-type rule is applied by the serializers and the sidecar
NAME_RULES = ("exclude", "allowlist")
MARKER = {"nickname": EXCLUDED, "hidden": HIDDEN, "blob": BINARY.decode()}


@pytest.fixture
def world(scratch_models):
    class Excluding(models.Model):
        """A model with a per-model exclude list and a binary column."""

        name = models.CharField(max_length=40, default="")
        nickname = models.CharField(max_length=40, default="")
        blob = models.BinaryField(default=b"")

        djust_exclude_fields = ("nickname",)

        class Meta:
            app_label = "auth"

    class Allowlisting(models.Model):
        """A model with a per-model allowlist."""

        name = models.CharField(max_length=40, default="")
        hidden = models.CharField(max_length=40, default="")

        djust_serializable_fields = ("name",)

        class Meta:
            app_label = "auth"

    class OptedOut(models.Model):
        """A model that takes a floor field back on purpose."""

        name = models.CharField(max_length=40, default="")
        password = models.CharField(max_length=40, default="")

        djust_serialize_sensitive_fields = ("password",)

        class Meta:
            app_label = "auth"

    class Library(models.Model):
        """No per-model rule at all: the ordinary lazy model."""

        name = models.CharField(max_length=40, default="")
        note = models.CharField(max_length=40, default="")

        class Meta:
            app_label = "auth"

    return {"exclude": Excluding, "allowlist": Allowlisting, "plain": Library, "optout": OptedOut}


def _instance(world, rule):
    cls = world["exclude" if rule in ("exclude", "column-type") else "allowlist"]
    if cls is world["exclude"]:
        return cls(pk=7, name=VISIBLE, nickname=EXCLUDED, blob=BINARY)
    return cls(pk=7, name=VISIBLE, hidden=HIDDEN)


class Counted:
    """A lazy factory that counts its calls: a wrapped value is built exactly once."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def _lazy(world, rule):
    factory = Counted(_instance(world, rule))
    return SimpleLazyObject(factory), factory


# -- the renders -----------------------------------------------------------------------------------
def _native(src, ctx, libraries=None, request=None):
    from djust.template import DjustTemplateBackend

    options = {"libraries": libraries} if libraries else {}
    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": options}
    )
    return str(backend.from_string(src).render(context=dict(ctx), request=request))


def _django(src, ctx):
    return Engine(libraries={"probe_lazy": __name__}).from_string(src).render(DjangoContext(ctx))


def _has(marker, out):
    """Whether ``marker`` is in ``out``, however a filter (``upper``) changed its case."""
    return marker.lower() in str(out).lower()


# A third-party-shaped tag for the bridge cell: it prints whatever it is handed.
from django import template  # noqa: E402

register = template.Library()


@register.simple_tag
def echo_lazy(value=None):
    return "[%s]" % (value,)


# -- the reads ------------------------------------------------------------------------------------
READS = {
    "bare": "[{{ u.F }}]",
    "dotted-filter-upper": "[{{ u.F|upper }}]",
    "dotted-filter-default": "[{{ u.F|default:'d' }}]",
    "filtered-rebinding": "{% with q=u|default:u %}[{{ q.F }}]{% endwith %}",
    "with-alias": "{% with q=u %}[{{ q.F }}]{% endwith %}",
}
CONTAINERS = {
    "bare": lambda u: {"u": u},
    "in-a-list": lambda u: {"u": [u][0], "rows": [u]},
    "in-a-dict": lambda u: {"u": {"inner": u}["inner"], "d": {"inner": u}},
}


@pytest.mark.parametrize("read", sorted(READS))
@pytest.mark.parametrize("rule", sorted(RULES))
class TestNativeEngine:
    def test_the_withheld_field_is_not_drawn_through_a_lazy_wrapper(self, world, rule, read):
        field = RULES[rule]
        lazy, _ = _lazy(world, rule)
        src = READS[read].replace("F", field)
        out = _native(src, {"u": lazy})
        assert not _has(MARKER[field], out), f"{rule}/{read}: {out!r}"

    def test_django_prints_it_so_the_cell_is_not_vacuous(self, world, rule, read):
        field = RULES[rule]
        lazy, _ = _lazy(world, rule)
        src = READS[read].replace("F", field)
        assert _has(MARKER[field], _django(src, {"u": lazy})), "the control must print the value"

    def test_the_direct_instance_agrees_with_the_wrapped_one(self, world, rule, read):
        field = RULES[rule]
        lazy, _ = _lazy(world, rule)
        src = READS[read].replace("F", field)
        direct = _native(src, {"u": _instance(world, rule)})
        assert not _has(MARKER[field], direct)
        assert _native(src, {"u": lazy}) == direct


@pytest.mark.parametrize("rule", sorted(RULES))
class TestEveryContainerShape:
    def test_a_lazy_model_inside_a_list_or_dict_is_held_to_the_rules(self, world, rule):
        field = RULES[rule]
        lazy, _ = _lazy(world, rule)
        loop = "{% for x in rows %}[{{ x.F }}]{% endfor %}".replace("F", field)
        nested = "[{{ d.inner.F }}]".replace("F", field)
        for shape in ("in-a-list", "in-a-dict"):
            ctx = CONTAINERS[shape](lazy)
            out = _native(loop if shape == "in-a-list" else nested, ctx)
            assert not _has(MARKER[field], out), (shape, out)

    def test_a_field_the_rules_allow_still_draws(self, world, rule):
        lazy, _ = _lazy(world, rule)
        assert VISIBLE in _native("[{{ u.name }}]", {"u": lazy})
        assert VISIBLE in _native(
            "{% with q=u|default:u %}[{{ q.name }}]{% endwith %}", {"u": lazy}
        )

    def test_the_wrapper_is_evaluated_once_per_render(self, world, rule):
        field = RULES[rule]
        lazy, factory = _lazy(world, rule)
        _native("[{{ u.%s }}][{{ u.name }}][{{ u.name|upper }}]" % field, {"u": lazy})
        assert factory.calls == 1


@pytest.mark.parametrize("rule", sorted(RULES))
def test_the_bridge_is_held_to_the_rules(world, rule):
    field = RULES[rule]
    lazy, _ = _lazy(world, rule)
    src = "{% load probe_lazy %}{% echo_lazy value=u.F %}".replace("F", field)
    out = _native(src, {"u": lazy}, libraries={"probe_lazy": __name__})
    assert not _has(MARKER[field], out), out
    control = _django(
        "{% load probe_lazy %}{% echo_lazy value=u.F %}".replace("F", field), {"u": lazy}
    )
    assert _has(MARKER[field], control)


# -- what the wrapper never reveals -----------------------------------------------------------------
def test_the_sidecar_handle_holds_the_model_and_not_the_wrapper(world):
    lazy, _ = _lazy(world, "exclude")
    proxy = _SidecarModelProxy(lazy)
    for name in ("_wrapped", "_setup", "_s", "__wrapped__", "__dict__"):
        with pytest.raises(AttributeError):
            getattr(proxy, name)
    assert VISIBLE in str(proxy.name)


def test_the_wrapper_is_not_evaluated_by_asking_for_a_withheld_name(world):
    lazy, factory = _lazy(world, "exclude")
    assert MARKER["nickname"] not in _native("[{{ u.nickname }}]", {"u": lazy})
    assert factory.calls == 1


# -- the eager dict path ---------------------------------------------------------------------------
@pytest.mark.parametrize("rule", sorted(RULES))
class TestEagerPaths:
    def test_normalize_django_value(self, world, rule):
        field = RULES[rule]
        lazy, factory = _lazy(world, rule)
        data = normalize_django_value(lazy)
        assert field not in data and VISIBLE == data["name"], data
        assert factory.calls == 1

    def test_the_json_encoder(self, world, rule):
        field = RULES[rule]
        lazy, _ = _lazy(world, rule)
        text = json.dumps(lazy, cls=DjangoJSONEncoder)
        assert MARKER[field] not in text and VISIBLE in text

    def test_the_direct_instance_gives_the_same_dict(self, world, rule):
        lazy, _ = _lazy(world, rule)
        assert normalize_django_value(lazy) == normalize_django_value(_instance(world, rule))


@pytest.mark.parametrize("rule", NAME_RULES)
def test_the_jit_attribute_gate(world, rule):
    field = RULES[rule]
    lazy, _ = _lazy(world, rule)
    assert emittable_names(lazy, {field, "name"}) == {"name"}
    assert emittable_names(_instance(world, rule), {field, "name"}) == {"name"}


def test_a_model_to_dict_is_honoured_behind_a_wrapper(scratch_models):
    class Opted(models.Model):
        name = models.CharField(max_length=10, default="")

        def to_dict(self):
            return {"mine": "authored"}

        class Meta:
            app_label = "auth"

    lazy = SimpleLazyObject(lambda: Opted(pk=1, name="x"))
    assert normalize_django_value(lazy) == {"mine": "authored"}


# -- other ways a model is held: a lazy of a lazy, a transparent proxy, a lazy that raises ----------
class TransparentProxy:
    """The smallest ``wrapt``-style proxy: it is not a ``LazyObject``, and it reports the class it stands
    for through ``__class__`` (so ``isinstance(p, Model)`` is true) while its ``type()`` is its own."""

    def __init__(self, wrapped):
        object.__setattr__(self, "_wrapped_instance", wrapped)

    @property
    def __class__(self):
        return object.__getattribute__(self, "_wrapped_instance").__class__

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_wrapped_instance"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_wrapped_instance"), name, value)

    def __str__(self):
        return str(object.__getattribute__(self, "_wrapped_instance"))


def _held(world, rule, kind):
    instance = _instance(world, rule)
    if kind == "double-lazy":
        return SimpleLazyObject(lambda: SimpleLazyObject(lambda: instance))
    if kind == "transparent-proxy":
        return TransparentProxy(instance)
    if kind == "lazy-proxy":
        return SimpleLazyObject(lambda: TransparentProxy(instance))
    return SimpleLazyObject(lambda: instance)


KINDS = ["double-lazy", "transparent-proxy", "lazy-proxy"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("rule", sorted(RULES))
class TestOtherHeldShapes:
    def test_a_proxy_is_not_a_lazy_object(self, world, rule, kind):
        held = _held(world, rule, kind)
        assert type(held) is not type(_instance(world, rule))
        assert isinstance(held, models.Model), "the serializers take it for a model"

    @pytest.mark.parametrize("read", sorted(READS))
    def test_the_native_engine(self, world, rule, kind, read):
        field = RULES[rule]
        out = _native(READS[read].replace("F", field), {"u": _held(world, rule, kind)})
        assert not _has(MARKER[field], out), (kind, read, out)

    def test_the_visible_field_still_draws(self, world, rule, kind):
        out = _native("[{{ u.name }}]", {"u": _held(world, rule, kind)})
        assert VISIBLE in out

    def test_the_eager_dict_and_the_json_encoder(self, world, rule, kind):
        field = RULES[rule]
        held = _held(world, rule, kind)
        assert normalize_django_value(held) == normalize_django_value(_instance(world, rule))
        assert field not in normalize_django_value(held)
        assert MARKER[field] not in json.dumps([held], cls=DjangoJSONEncoder)

    def test_a_list_and_a_dict_of_them(self, world, rule, kind):
        field = RULES[rule]
        held = _held(world, rule, kind)
        for container in ([held], {"k": held}, (held,)):
            assert MARKER[field] not in json.dumps(normalize_django_value(container), default=str)
        out = _native(
            "{% for x in rows %}[{{ x.F }}]{% endfor %}[{{ d.k.F }}]".replace("F", field),
            {"rows": [held], "d": {"k": held}},
        )
        assert not _has(MARKER[field], out), out

    def test_the_gate_and_the_class_helper(self, world, rule, kind):
        from djust.serialization import _model_class_of

        held = _held(world, rule, kind)
        assert _model_class_of(held) is type(_instance(world, rule))
        if rule in NAME_RULES:
            assert emittable_names(held, {RULES[rule], "name"}) == {"name"}


def test_a_class_attribute_that_is_not_a_class_is_not_taken_for_one():
    """A ``__class__`` that is not a class falls back to ``type(obj)``; it does not raise."""
    from djust.serialization import _model_class_of

    class Odd:
        @property
        def __class__(self):
            return "not a class"

    assert _model_class_of(Odd()) is type(Odd())


def test_a_floor_handle_is_not_taken_for_a_model_class(world):
    from djust.serialization import _model_class_of

    handle = _SidecarModelProxy(_instance(world, "exclude"))
    assert not issubclass(_model_class_of(handle), models.Model)


class TestALazyThatRaises:
    def _raising(self):
        def boom():
            raise RuntimeError("the wrapped value could not be built")

        return SimpleLazyObject(boom)

    def test_nothing_is_drawn_and_the_failure_is_not_swallowed(self, world):
        lazy = self._raising()
        with pytest.raises(RuntimeError):
            _native("[{{ u.nickname }}][{{ u.name }}]", {"u": lazy})

    def test_the_gate_fails_closed(self):
        assert emittable_names(self._raising(), {"nickname", "name"}) == frozenset()

    def test_the_class_helper_does_not_guess(self):
        from djust.serialization import _model_class_of

        with pytest.raises(RuntimeError):
            _model_class_of(self._raising())


# -- the other per-model rules, read from the same place ------------------------------------------
OPTED = "opted-back-in-5e9f"


class TestTheOptOutAndTheForm:
    def test_a_field_the_model_takes_back_draws_the_same_behind_a_wrapper(self, world):
        direct = world["optout"](pk=2, name=VISIBLE, password=OPTED)
        lazy = SimpleLazyObject(lambda: direct)
        src = "[{{ u.password }}][{{ u.name }}]"
        assert OPTED in _native(src, {"u": direct}), "the model's own opt-out is honoured"
        assert _native(src, {"u": lazy}) == _native(src, {"u": direct})
        assert normalize_django_value(lazy) == normalize_django_value(direct)

    def test_a_model_form_over_a_wrapped_instance_is_held_to_the_models_rules(self, world):
        from django import forms

        class WrappedForm(forms.ModelForm):
            class Meta:
                model = world["exclude"]
                fields = ["name", "nickname"]

        lazy, _ = _lazy(world, "exclude")
        src = "[{{ form.initial.name }}][{{ form.initial.nickname }}]"
        out = _native(src, {"form": WrappedForm(instance=lazy)})
        assert VISIBLE in out and EXCLUDED not in out, out
        direct = _native(src, {"form": WrappedForm(instance=_instance(world, "exclude"))})
        assert out == direct


# -- the ordinary lazy model: nothing changes for it -------------------------------------------------
class TestAnOrdinaryLazyModel:
    def test_the_global_floor_still_holds(self):
        user = User(
            pk=3,
            username="ordinary",
            password="pbkdf2$global-floor-8a1d",
            is_staff=True,
            first_name="Visible",
        )
        lazy = SimpleLazyObject(lambda: user)
        out = _native(
            "[{{ u.username }}][{{ u.first_name }}][{{ u.password }}][{{ u.is_staff }}]",
            {"u": lazy},
        )
        assert "global-floor-8a1d" not in out
        assert "ordinary" in out and "Visible" in out

    def test_a_model_without_per_model_rules_draws_all_its_ordinary_fields(self, world):
        lazy = SimpleLazyObject(lambda: world["plain"](pk=1, name=VISIBLE, note="n-ote"))
        out = _native("[{{ u.name }}][{{ u.note }}]", {"u": lazy})
        assert VISIBLE in out and "n-ote" in out
        data = normalize_django_value(lazy)
        assert data["name"] == VISIBLE and data["note"] == "n-ote"

    def test_a_lazy_anonymous_user_is_untouched(self):
        lazy = SimpleLazyObject(AnonymousUser)
        assert (
            _native("{% if u.is_authenticated %}in{% else %}out{% endif %}", {"u": lazy}) == "out"
        )


# -- request.user from a context processor -------------------------------------------------------
_PROCESSORS = [
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
]


@pytest.fixture
def template_dir():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "who_lazy.html").write_text(
        "<div dj-root>[{{ user.name }}][{{ user.nickname }}][{{ user.hidden }}][{{ user.blob }}]"
        "{% with q=user|default:user %}[{{ q.nickname }}]{% endwith %}"
        "[{{ user.nickname|upper }}][{{ request.user.nickname }}]</div>"
    )
    templates = [
        {
            "BACKEND": "djust.template.backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": _PROCESSORS},
        }
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        yield tmp
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    shutil.rmtree(tmp, ignore_errors=True)


def _request(lazy):
    request = RequestFactory().get("/who/")
    request.user = lazy
    request.session = SessionStore()
    request.session.create()
    return request


class TestRequestUserFromAContextProcessor:
    def _view(self):
        return type(
            "WhoLazy",
            (LiveView,),
            {"template_name": "who_lazy.html", "mount": lambda self, request, **kw: None},
        )

    @pytest.mark.parametrize("rule", ["exclude", "column-type"])
    def test_a_plain_get(self, world, template_dir, rule):
        lazy, factory = _lazy(world, rule)
        html = self._view().as_view()(_request(lazy)).content.decode()
        assert VISIBLE in html, html
        assert EXCLUDED not in html and BINARY.decode() not in html, html
        assert factory.calls == 1

    def test_a_plain_get_with_an_allowlisted_model(self, world, template_dir):
        lazy, _ = _lazy(world, "allowlist")
        html = self._view().as_view()(_request(lazy)).content.decode()
        assert VISIBLE in html and HIDDEN not in html, html

    @pytest.mark.parametrize("rule", ["exclude", "allowlist"])
    def test_the_websocket_render_path(self, world, template_dir, rule):
        lazy, _ = _lazy(world, rule)
        request = _request(lazy)
        view = self._view()()
        view.setup(request)
        view._initialize_temporary_assigns()
        view.mount(request)
        view.request = request
        view.get_template()
        html, _patches, _version = view.render_with_diff(request)
        assert VISIBLE in html, html
        assert EXCLUDED not in html and HIDDEN not in html, html


# -- a LiveView that holds the wrapper as its own state ---------------------------------------------
class TestLiveViewState:
    @pytest.mark.parametrize("held", ["lazy", "direct"])
    @pytest.mark.parametrize("rule", NAME_RULES)
    def test_a_wrapper_held_as_state(self, world, rule, held):
        from djust.testing import LiveViewTestClient

        field = RULES[rule]
        lazy = _lazy(world, rule)[0] if held == "lazy" else _instance(world, rule)

        class StateView(LiveView):
            template = "<div dj-root>[{{ member.name }}][{{ member.%s }}]</div>" % field

            def mount(self, request, **kwargs):
                self.member = lazy

        client = LiveViewTestClient(StateView)
        client.mount()
        html = client.render()
        assert VISIBLE in html, html
        assert MARKER[field] not in html, html
