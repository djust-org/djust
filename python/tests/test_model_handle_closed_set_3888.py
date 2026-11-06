"""#3888, review round 2: the floor proxy answers a CLOSED SET of kinds, whatever the name.

Two rounds of review found a way past a per-name refusal (first the slot that holds the wrapped
instance, then a bound method's receiver). The cure is structural, so the guarantee is a property
and not a list of names:

    For EVERY attribute name of a model (``dir(instance)``), what a floor proxy returns is never the
    instance, its class, a manager, a queryset, a bound method, a function, a module or a class, and
    nothing reachable from it by the second-level attributes a Python reader walks
    (``__self__``, ``__func__``, ``__wrapped__``, ``__globals__``, ``__code__``, ``__closure__``,
    ``__dict__``, ``__class__``, ``__module__``) is one either.

Also here: ``isinstance`` against an ABC works on a proxy, the debug ORM-call warning reaches the
Rust reader of a floored call, the floored dict is built once per handle, and a non-model carrier is
not probed for the hook.

Refs #3888.
"""

# ruff: noqa: F811

from __future__ import annotations

import collections.abc
import logging
import types
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import models as dj_models

from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_model_handle_review_3888 import SECRET, _backend, users  # noqa: F401

SECOND_LEVEL = (
    "__self__",
    "__func__",
    "__wrapped__",
    "__globals__",
    "__code__",
    "__closure__",
    "__dict__",
    "__class__",
    "__module__",
    "__defaults__",
    "__objclass__",
)


def _forbidden(model_classes: tuple) -> tuple:
    return (
        dj_models.Model,
        dj_models.Manager,
        dj_models.QuerySet,
        types.MethodType,
        types.FunctionType,
        types.BuiltinFunctionType,
        types.ModuleType,
        types.CodeType,
    ) + model_classes


def _is_floor_object(obj) -> bool:
    return type(obj).__name__ in {
        "_SidecarModelProxy",
        "_SidecarQuerySetProxy",
        "_FlooredCall",
        "_FlooredObject",
    }


def _assert_closed(value, model_classes: tuple, path: str, depth: int = 0) -> None:
    """``value`` is none of the forbidden kinds, and neither is anything inside a container.

    A bound method is forbidden unless its receiver is a floor object itself (the proxy's own hook
    or type-level method): that receiver is the proxy, not the model."""
    if isinstance(value, (types.MethodType, types.BuiltinMethodType)) and _is_floor_object(
        getattr(value, "__self__", None)
    ):
        return
    assert not isinstance(value, _forbidden(model_classes)), (path, type(value))
    if isinstance(value, type):
        # a class may be handed back only if it is not a model / manager / queryset class, and no
        # member of it is a Python function (a function carries `__globals__`: a module namespace)
        assert not issubclass(value, (dj_models.Model, dj_models.Manager, dj_models.QuerySet)), (
            path,
            value,
        )
        for attr in dir(value):
            member = getattr(value, attr, None)
            assert not hasattr(member, "__globals__"), (path, value, attr)
        return
    if depth > 3:
        return
    if type(value) in (list, tuple, set, frozenset):
        for i, item in enumerate(value):
            _assert_closed(item, model_classes, f"{path}[{i}]", depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            _assert_closed(item, model_classes, f"{path}[{key!r}]", depth + 1)


_PLAIN = (str, int, float, bool, bytes, type(None), Decimal, datetime, date, time, timedelta)
_CONTAINERS = (
    list,
    tuple,
    dict,
    set,
    frozenset,
)  # plain data: their items are checked, not their own methods


def _probe(value, model_classes: tuple, path: str, depth: int) -> None:
    """``value`` is closed, and so is everything a reader reaches from it by the second-level
    attributes and by every name it lists, to ``depth``. Only the ASSERTS are outside the ``try``:
    a failed assertion must never be swallowed as "that attribute does not exist"."""
    _assert_closed(value, model_classes, path)
    if (
        depth == 0
        or isinstance(value, _PLAIN)
        or isinstance(value, type)
        or type(value) in _CONTAINERS
    ):
        return
    try:
        names = set(SECOND_LEVEL) | set(dir(value))
    except Exception:  # noqa: BLE001
        names = set(SECOND_LEVEL)
    for name in sorted(names):
        try:
            reached = getattr(value, name)
        except Exception:  # noqa: BLE001
            continue
        _probe(reached, model_classes, f"{path}.{name}", depth - 1)


def _walk_proxy(instance, model_classes: tuple) -> int:
    """Read EVERY name of ``dir(instance)`` through a proxy; assert the closed set on what comes back
    and, three levels down, on everything reachable from it; return the count of names that answered
    (a non-vacuity floor for the caller)."""
    from djust.serialization import _SidecarModelProxy

    proxy = _SidecarModelProxy(instance)
    answered = 0
    for name in dir(instance):
        try:
            value = getattr(proxy, name)
        except Exception:  # noqa: BLE001 - refused, or a property that raises on an unsaved row
            continue
        answered += 1
        _probe(value, model_classes, name, 3)
    return answered


@pytest.mark.django_db
class TestTheClosedSetOfKinds:
    def test_every_name_of_a_real_model_answers_only_the_closed_set(
        self, users, scratch_models
    ) -> None:
        group = Group.objects.get(name="ops")
        group.permissions.add(Permission.objects.order_by("pk").first())
        instances = [
            users[0],
            group,
            Permission.objects.order_by("pk").first(),
            ContentType.objects.order_by("pk").first(),
        ]
        total = 0
        for instance in instances:
            total += _walk_proxy(instance, (type(instance),))
        assert total > 100, total  # not vacuous: the proxies answered a lot of names

    def test_every_name_of_a_model_that_returns_itself_answers_only_the_closed_set(
        self, scratch_models
    ) -> None:
        """A custom model whose properties and methods hand back the instance in every shape a
        reader could ask for: itself, a list of itself, a dict of itself, an object that holds it, a
        bound method, a function, a class attribute that is a class or a module."""
        import os

        class Holder3888:
            def __init__(self, owner) -> None:
                self.owner = owner

        class Closed3888(dj_models.Model):
            name = dj_models.CharField(max_length=10)
            image = dj_models.FileField(upload_to="x")
            data = dj_models.JSONField(default=dict)
            Kind = Holder3888
            module = os

            class Meta:
                app_label = "auth"

            @property
            def me(self):
                return self

            @property
            def many(self):
                return [self, (self,), {"k": self}]

            @property
            def holder(self):
                return Holder3888(self)

            @property
            def method_ref(self):
                return self.get_other

            def get_other(self):
                return self

            def make_fn(self):
                return lambda: self

        row = Closed3888(pk=1, name="n", image="x/a.png", data={"a": [1, {"b": 2}]})
        answered = _walk_proxy(row, (Closed3888,))
        assert answered > 20, answered

    def test_a_call_result_goes_back_through_the_same_function(self, scratch_models) -> None:
        from djust.serialization import _SidecarModelProxy

        class Callable3888(dj_models.Model):
            name = dj_models.CharField(max_length=10)

            class Meta:
                app_label = "auth"

            def get_self(self):
                return self

            def get_many(self):
                return [self, {"k": self}]

        row = Callable3888(pk=1, name="n")
        proxy = _SidecarModelProxy(row)
        result = proxy.get_self()
        assert type(result).__name__ == "_SidecarModelProxy"
        many = proxy.get_many()
        assert type(many[0]).__name__ == "_SidecarModelProxy"
        assert type(many[1]["k"]).__name__ == "_SidecarModelProxy"
        assert result.name == "n"

    def test_a_file_field_still_reads_through_a_floored_object(self, scratch_models) -> None:
        """The one non-plain value a model commonly has: ``{{ p.image.url }}`` keeps working, and the
        object that answers it does not hand back the instance (``FieldFile.instance`` is a model)."""
        from djust.serialization import _SidecarModelProxy

        class File3888(dj_models.Model):
            image = dj_models.FileField(upload_to="x")

            class Meta:
                app_label = "auth"

        proxy = _SidecarModelProxy(File3888(pk=1, image="x/a.png"))
        assert str(proxy.image) == "x/a.png"
        assert proxy.image.url.endswith("x/a.png")
        assert type(proxy.image.instance).__name__ == "_SidecarModelProxy"
        with pytest.raises(AttributeError):
            proxy.image._s


@pytest.mark.django_db
class TestTheHookTheEngineAsksForExposesNothing:
    def test_the_hook_object_is_callable_and_otherwise_closed(self, users) -> None:
        from djust.serialization import _SidecarModelProxy

        hook = _SidecarModelProxy(users[0]).__djust_serialize__
        assert isinstance(hook(), dict) and "password" not in hook()
        for name in ("_s", "__self__", "__func__", "__wrapped__", "__dict__", "fn", "proxy"):
            with pytest.raises(AttributeError):
                getattr(hook, name)
        assert not isinstance(hook, (types.MethodType, types.FunctionType))
        from django.contrib.auth.models import Group

        users[0].groups.add(Group.objects.create(name="hook-probe-3888"))
        qs_hook = _SidecarModelProxy(users[0]).groups.all().__djust_serialize__
        rows = [type(r).__name__ for r in qs_hook()]
        assert rows and set(rows) == {"_SidecarModelProxy"}
        with pytest.raises(AttributeError):
            qs_hook._s


@pytest.mark.django_db
class TestAnAbcCheckOnAProxy:
    ABCS = [
        collections.abc.Mapping,
        collections.abc.Iterable,
        collections.abc.Sequence,
        collections.abc.Hashable,
        collections.abc.Sized,
        collections.abc.Callable,
    ]

    def test_isinstance_and_issubclass_work_on_a_row(self, users) -> None:
        from djust.serialization import _SidecarModelProxy

        proxy = _SidecarModelProxy(users[0])
        for abc in self.ABCS:
            assert isinstance(proxy, abc) == issubclass(type(proxy), abc), abc
        assert isinstance(proxy, collections.abc.Hashable)
        assert not isinstance(proxy, collections.abc.Mapping)
        assert not isinstance(proxy, collections.abc.Iterable)
        # and the proxy reports a class of its own, never the model's
        # (`__class__` reports a class with no Python-level methods; the check also asks `type()`)
        from djust.serialization import _Opaque

        assert proxy.__class__ is _Opaque
        assert not isinstance(proxy.__class__, type(proxy))

    def test_a_custom_filter_can_ask(self, users) -> None:
        got = _backend(
            "{% load rv3888 %}{% for u in rows|slice:':1' %}[{{ u|abc_kinds }}]{% endfor %}",
            {"rows": list(users)},
        )
        assert got == "[Hashable]", got
        # a top-level model is still the dict form (#3902): the filter can ask it too, and no crash
        top = _backend("{% load rv3888 %}[{{ u|abc_kinds }}]", {"u": users[0]})
        assert top == "[Mapping,Iterable,Sized]", top

    def test_normalize_django_value_accepts_a_proxy(self, users) -> None:
        from djust.serialization import _SidecarModelProxy, normalize_django_value

        # it answered before the proxy refused `__class__`, and raised after (the ABC check inside)
        normalize_django_value(_SidecarModelProxy(users[0]))


@pytest.mark.django_db
class TestTheDebugOrmCallWarningReachesAFlooredCall:
    def test_the_rust_reader_of_the_flag_fires(self, users, caplog) -> None:
        """``{{ u.groups.count }}`` auto-calls an ORM method through ``_FlooredCall``; the debug warning
        reads ``djust_orm_call`` there, because the wrapper has no ``__self__`` to show."""
        from django.test import override_settings

        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.WARNING, logger="djust.templates"),
        ):
            _backend(
                "{% for u in rows|slice:':1' %}{{ u.groups.count }}{% endfor %}",
                {"rows": list(users)},
            )
        assert [r for r in caplog.records if "auto-calls an ORM method" in r.getMessage()], [
            r.getMessage() for r in caplog.records
        ]


@pytest.mark.django_db
class TestTheFlooredDictIsBuiltOnce:
    def test_two_tags_per_row_normalise_each_model_once(self, users, monkeypatch) -> None:
        import djust.serialization as serialization
        from djust.template_tags import TagHandler, register

        @register("twice3888")
        class _Twice(TagHandler):
            def render(self, args, context):
                return "T"

        calls = []
        real = serialization.normalize_django_value

        def counting(value, *a, **kw):
            calls.append(type(value).__name__)
            return real(value, *a, **kw)

        monkeypatch.setattr(serialization, "normalize_django_value", counting)
        _backend(
            "{% twice3888 rows=rows %}{% twice3888 rows=rows %}{% for u in rows %}{% twice3888 user=u %}{% twice3888 user=u %}{% endfor %}",
            {"rows": list(users)},
        )
        models_normalised = [c for c in calls if c == "User"]
        assert len(models_normalised) == len(users), calls

    def test_a_render_without_a_tag_normalises_nothing(self, users, monkeypatch) -> None:
        import djust.serialization as serialization

        calls = []
        real = serialization.normalize_django_value
        monkeypatch.setattr(
            serialization,
            "normalize_django_value",
            lambda v, *a, **k: (calls.append(1), real(v, *a, **k))[1],
        )
        _backend(
            "{% for u in rows|slice:':2' %}{{ u.username }}{{ u }}{% endfor %}",
            {"rows": list(users)},
        )
        assert calls == []


class TestANonModelCarrierIsNotProbedForTheHook:
    def test_serialising_a_carried_object_does_not_ask_it_for_the_hook(self) -> None:
        """``floored_model_dict`` is guarded by the carrier's equality class: a Python probe per
        serialised carrier (a datetime, a set, any object) would be paid by every state snapshot."""
        from djust import _rust

        seen: list[str] = []

        class Probe:
            def __len__(self) -> int:
                return 0

            def __getattr__(self, name: str):
                seen.append(name)
                raise AttributeError(name)

        view = _rust.RustLiveView("{{ p }}")
        view.update_state({"p": Probe()})
        seen.clear()
        view.serialize_msgpack()
        assert "__djust_serialize__" not in seen, seen


@pytest.mark.django_db
class TestTheArgumentsQuestionIsAnsweredWithoutASignature:
    """Django asks ``inspect.signature(current).bind()`` when a template-called callable raises
    ``TypeError``: "no arguments were given" is an invalid variable, anything else propagates. A
    call wrapper answers through a marker the engine reads; it hands out no ``Signature`` (whose
    class has Python-level methods)."""

    def test_the_wrapper_has_no_signature(self, users) -> None:
        from djust.serialization import _SidecarModelProxy

        method = _SidecarModelProxy(users[0]).get_full_name
        with pytest.raises(AttributeError):
            method.__signature__

    def test_the_marker_answers_for_a_method_that_needs_arguments(self, users) -> None:
        from djust.serialization import _SidecarModelProxy

        proxy = _SidecarModelProxy(users[0])
        assert proxy.check_password.djust_requires_arguments() is True
        assert proxy.get_full_name.djust_requires_arguments() is False

    def test_the_marker_object_exposes_nothing(self, users) -> None:
        from djust.serialization import _SidecarModelProxy

        marker = _SidecarModelProxy(users[0]).check_password.djust_requires_arguments
        for name in ("_s", "__self__", "__func__", "__wrapped__", "__globals__", "__dict__"):
            with pytest.raises(AttributeError):
                getattr(marker, name)

    def test_the_engine_renders_a_method_that_needs_arguments_empty(self, users) -> None:
        """The Rust walk reads the marker: ``{{ u.check_password }}`` is Django's invalid variable
        (empty), not a crash, for a row reached through a loop over a list of models."""
        got = _backend(
            "{% for u in rows|slice:':1' %}[{{ u.check_password }}][{{ u.get_full_name }}]{% endfor %}",
            {"rows": list(users)},
        )
        assert got.startswith("[]["), got

    def test_a_typeerror_inside_a_zero_argument_method_still_propagates(
        self, scratch_models
    ) -> None:
        class Boom3888(dj_models.Model):
            class Meta:
                app_label = "auth"

            def explode(self):
                return 1 + "a"

        from django.template import TemplateSyntaxError  # noqa: F401

        with pytest.raises(TypeError):
            _backend(
                "{% for u in rows|slice:':1' %}{{ u.explode }}{% endfor %}",
                {"rows": [Boom3888(pk=1)]},
            )
