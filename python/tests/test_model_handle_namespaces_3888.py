"""A model handle closes routes to Python-defined objects and namespaces (#3888 review round 3).

A reader that walks attributes from a value a handle returned must not find a Python function (a
function carries ``__globals__``, a module namespace) or a Python-defined instance whose class has
methods. The handle's wrappers report a class without Python-level methods as ``__class__``; a call
wrapper answers "were arguments required?" through a marker instead of a ``Signature``; a UUID is its
text; plain data is matched by EXACT type (a subclass goes through the floored view, SafeString stays
safe markup); a dict's keys are floored like its values.

``TestKindsThatMustStayReadable`` pins what a handle still returns; ``TestWhatMustNotBeReachable`` pins
what it must not. Each test was gate-offed against a mutation that reintroduces the behaviour.
"""

from __future__ import annotations

import copy
import datetime
import enum
import pickle
import uuid

import pytest
from django.contrib.auth.models import User
from django.db import models
from django.utils.safestring import SafeData, mark_safe

from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_model_handle_review_3888 import _backend  # noqa: F401

SENTINEL = object()


def _proxy(obj):
    from djust.serialization import _SidecarModelProxy

    return _SidecarModelProxy(obj)


def _no_python_function(cls) -> None:
    for name in dir(cls):
        member = getattr(cls, name, None)
        assert not hasattr(member, "__globals__"), (cls, name)


class TestKindsThatMustStayReadable:
    """Gate-off survivors of round 3: the refusals and the preserved markup flag."""

    def test_a_class_is_not_readable(self, scratch_models) -> None:  # noqa: F811
        class NsClass(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def a_class(self):
                return type(self)

        p = _proxy(NsClass())
        for name in ("a_class", "DoesNotExist", "MultipleObjectsReturned"):
            with pytest.raises(AttributeError):
                getattr(p, name)

    def test_a_module_is_not_readable(self, scratch_models) -> None:  # noqa: F811
        import os

        class NsModule(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def a_module(self):
                return os

        with pytest.raises(AttributeError):
            _proxy(NsModule()).a_module

    def test_a_descriptor_is_not_readable(self, scratch_models) -> None:  # noqa: F811
        class NsDescriptor(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def a_descriptor(self):
                return property(lambda s: s)

        with pytest.raises(AttributeError):
            _proxy(NsDescriptor()).a_descriptor

    def test_a_safe_string_keeps_its_markup_flag(self, scratch_models) -> None:  # noqa: F811
        class NsSafe(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def html(self):
                return mark_safe("<b>x</b>")

        got = _proxy(NsSafe()).html
        assert hasattr(got, "__html__"), type(got)
        assert isinstance(got, SafeData) and got == "<b>x</b>"
        assert got.__html__() == "<b>x</b>"

    def test_a_safe_string_renders_unescaped_and_a_plain_one_escaped(self, scratch_models) -> None:  # noqa: F811
        class NsSafeRender(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def safe(self):
                return mark_safe("<b>x</b>")

            @property
            def plain(self):
                return "<i>y</i>"

        from djust.template import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        template = backend.from_string(
            "{% for r in rows|slice:':1' %}{{ r.safe }}|{{ r.plain }}{% endfor %}"
        )
        got = str(template.render(context={"rows": [NsSafeRender(pk=1)]}, request=None))
        assert got == "<b>x</b>|&lt;i&gt;y&lt;/i&gt;", got

    def test_a_choices_member_is_its_value(self, scratch_models) -> None:  # noqa: F811
        class Status(models.TextChoices):
            ACTIVE = "active"

        class NsChoices(models.Model):
            status = models.CharField(max_length=10)

            class Meta:
                app_label = "auth"

        got = _proxy(NsChoices(pk=1, status=Status.ACTIVE)).status
        assert type(got) is str and got == "active"

    def test_a_proxy_cannot_be_copied_or_pickled(self) -> None:
        p = _proxy(User(username="x"))
        with pytest.raises((TypeError, copy.Error)):
            copy.copy(p)
        with pytest.raises((TypeError, pickle.PicklingError)):
            pickle.dumps(p)


class TestWhatMustNotBeReachable:
    def test_the_class_of_every_wrapper_has_no_python_level_methods(self, scratch_models) -> None:  # noqa: F811
        from djust.serialization import (
            _FlooredCall,
            _FlooredObject,
            _FloorSafeText,
            _Hook,
            _SidecarModelProxy,
            _SidecarQuerySetProxy,
        )

        class NsWrappers(models.Model):
            image = models.FileField(upload_to="x")

            class Meta:
                app_label = "auth"

        row = NsWrappers(pk=1, image="x/a.png")
        wrappers = [
            _SidecarModelProxy(User(username="x")),
            _SidecarQuerySetProxy(User.objects.none()),
            _FlooredCall(User.get_full_name),
            _FlooredObject(row.image),
            _FloorSafeText("x"),
            _Hook(lambda p: p, row),
        ]
        assert {type(w) for w in wrappers} == {
            _SidecarModelProxy,
            _SidecarQuerySetProxy,
            _FlooredCall,
            _FlooredObject,
            _FloorSafeText,
            _Hook,
        }
        for wrapper in wrappers:
            _no_python_function(wrapper.__class__)

    def test_isinstance_against_the_abcs_still_works_on_a_wrapper(self, users_row) -> None:
        import collections.abc as abc

        from djust.serialization import _FlooredCall

        proxy = _proxy(users_row)
        assert isinstance(proxy, abc.Hashable) and not isinstance(proxy, abc.Mapping)
        call = _FlooredCall(users_row.get_full_name)
        assert isinstance(call, abc.Callable)
        assert callable(call)

    def test_the_signature_of_a_row_method_is_not_handed_out(self) -> None:
        call = _proxy(User(username="x")).get_full_name
        with pytest.raises(AttributeError):
            getattr(call, "__signature__")

    def test_a_signature_keeps_no_default_object_of_the_callable(self, scratch_models) -> None:  # noqa: F811
        class NsDefault(models.Model):
            class Meta:
                app_label = "auth"

            def m(self, x=SENTINEL):
                return 1

        call = _proxy(NsDefault()).m
        with pytest.raises(AttributeError):
            call.__signature__
        assert call.djust_requires_arguments() is False  # x has a default

    def test_a_uuid_field_value_is_its_text(self, scratch_models) -> None:  # noqa: F811
        class NsUuid(models.Model):
            uid = models.UUIDField(default=uuid.uuid4)

            class Meta:
                app_label = "auth"

        row = NsUuid(pk=1)
        value = _proxy(row).uid
        assert type(value) is str and value == str(row.uid)

    def test_a_uuid_still_renders_as_its_text(self, scratch_models) -> None:  # noqa: F811
        class NsUuidRender(models.Model):
            uid = models.UUIDField()

            class Meta:
                app_label = "auth"

        row = NsUuidRender(pk=1, uid=uuid.UUID(int=7))
        got = _backend("{% for r in rows|slice:':1' %}{{ r.uid }}{% endfor %}", {"rows": [row]})
        assert got == str(uuid.UUID(int=7)), got

    def test_a_plain_subclass_carrying_a_model_is_not_returned_raw(self, scratch_models) -> None:  # noqa: F811
        class Tagged(str):
            pass

        class NsTagged(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def tagged(self):
                t = Tagged("x")
                t.owner = self
                return t

        got = _proxy(NsTagged(pk=1)).tagged
        assert type(got).__name__ == "_FlooredObject"
        # the model it carries comes back as a floor proxy, never as the instance
        assert type(got.owner).__name__ == "_SidecarModelProxy"
        assert not isinstance(got.owner, models.Model)
        assert str(got) == "x"

    def test_each_plain_base_is_matched_by_exact_type(self, scratch_models) -> None:  # noqa: F811
        import datetime as dt
        from decimal import Decimal

        class MyInt(int):
            pass

        class MyDecimal(Decimal):
            pass

        class MyDate(dt.datetime):
            pass

        class NsSubclasses(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def an_int(self):
                return MyInt(3)

            @property
            def a_decimal(self):
                return MyDecimal("1.5")

            @property
            def a_datetime(self):
                return MyDate(2020, 1, 1)

            @property
            def exact(self):
                return [3, Decimal("1.5"), dt.datetime(2020, 1, 1), "s", b"b", 1.5, True, None]

        p = _proxy(NsSubclasses())
        for name in ("an_int", "a_decimal", "a_datetime"):
            assert type(getattr(p, name)).__name__ == "_FlooredObject", name
        assert [type(v) for v in p.exact] == [
            int,
            Decimal,
            dt.datetime,
            str,
            bytes,
            float,
            bool,
            type(None),
        ]

    def test_an_enum_member_is_not_returned_raw(self, scratch_models) -> None:  # noqa: F811
        class Color(str, enum.Enum):
            RED = "red"

        class NsEnum(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def color(self):
                return Color.RED

        got = _proxy(NsEnum()).color
        assert type(got).__name__ == "_FlooredObject"

    def test_a_dict_key_that_is_a_model_is_floored(self, scratch_models) -> None:  # noqa: F811
        class NsKeys(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def by_model(self):
                return {self: 1, "plain": 2}

            @property
            def in_a_tuple(self):
                return {(self, 1): 3}

        p = _proxy(NsKeys(pk=1))
        for name in ("by_model", "in_a_tuple"):
            got = getattr(p, name)
            keys = list(got)
            assert not any(isinstance(k, models.Model) for k in keys), keys
            flat = [x for k in keys for x in (k if isinstance(k, tuple) else (k,))]
            assert not any(isinstance(k, models.Model) for k in flat), keys
        assert {k for k in p.by_model if isinstance(k, str)} == {"plain"}

    def test_a_dict_key_that_is_a_class_refuses_the_read(self, scratch_models) -> None:  # noqa: F811
        class NsClassKey(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def by_class(self):
                return {type(self): 1}

        with pytest.raises(AttributeError):
            _proxy(NsClassKey()).by_class


# -- review round 4: safe text, the engine bridge walker, and a Python-defined tzinfo ---------------


class TestSafeMarkupIsNotTheRawSafeString:
    def test_safe_markup_is_not_the_raw_safestring(self, scratch_models) -> None:  # noqa: F811
        class NsSafeKind(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def html(self):
                return mark_safe("<b>x</b>")

        got = _proxy(NsSafeKind()).html
        from djust.serialization import _Opaque

        assert got.__class__ is _Opaque, got.__class__
        assert type(got).__name__ == "_FloorSafeText"

    def test_safe_markup_refuses_underscore_names(self, scratch_models) -> None:  # noqa: F811
        class NsSafeNames(models.Model):
            class Meta:
                app_label = "auth"

            @property
            def html(self):
                return mark_safe("<b>x</b>")

        got = _proxy(NsSafeNames()).html
        for name in ("__dict__", "__reduce__", "__init_subclass__", "__getstate__", "_x"):
            with pytest.raises(AttributeError):
                getattr(got, name)


class TestTheBridgeWalkerAsksTheModuleForTheShape:
    """The Django-engine bridge (``template_bridge_floor``) gives Django ``inspect.signature(current)``;
    a call wrapper has no signature to show, so the walker asks ``floored_call_signature``."""

    def test_a_walked_method_that_needs_arguments_does_not_bind(self) -> None:
        import inspect

        from djust.serialization import _FlooredCall
        from djust.template_bridge_floor import wrap

        user = User(username="x")
        needs = wrap(_FlooredCall(user.check_password))
        with pytest.raises(TypeError):
            inspect.signature(needs).bind()
        inspect.signature(needs).bind("raw")
        takes_none = wrap(_FlooredCall(user.get_full_name))
        inspect.signature(takes_none).bind()

    def test_a_plain_callable_keeps_its_own_signature(self) -> None:
        import inspect

        from djust.serialization import floored_call_signature
        from djust.template_bridge_floor import wrap

        def two(a, b):
            return a

        assert floored_call_signature(two) is None
        with pytest.raises(TypeError):
            inspect.signature(wrap(two)).bind()


class PyTz(datetime.tzinfo):
    """A DST-aware, Python-defined tzinfo: the shape a pytz or dateutil zone has."""

    zone = "Europe/Test"

    def utcoffset(self, d):
        return datetime.timedelta(hours=2 if d and 4 <= d.month <= 9 else 1)

    def dst(self, d):
        return datetime.timedelta(hours=1 if d and 4 <= d.month <= 9 else 0)

    def tzname(self, d):
        return "CEST" if d and 4 <= d.month <= 9 else "CET"


class TestAPythonTzinfoIsNotReturnedRaw:
    def _row(self, when, at):
        class NsTz(models.Model):
            when = models.DateTimeField(null=True)
            at = models.TimeField(null=True)

            class Meta:
                app_label = "auth"

        return NsTz(pk=1, when=when, at=at)

    def test_R4_1_a_python_tzinfo_is_not_returned_raw(self, scratch_models) -> None:  # noqa: F811
        row = self._row(
            datetime.datetime(2020, 7, 1, 12, 30, tzinfo=PyTz()),
            datetime.time(8, 5, tzinfo=PyTz()),
        )
        p = _proxy(row)
        for value in (p.when, p.at):
            tz = value.tzinfo
            assert not hasattr(getattr(tz, "utcoffset", None), "__globals__")
            assert tz.__class__.__name__ == "_Opaque"
            for name in ("__dict__", "_s", "__reduce__", "__init_subclass__"):
                with pytest.raises(AttributeError):
                    getattr(tz, name)
            _no_python_function(tz.__class__)

    def test_the_datetime_still_answers_what_the_original_did(self, scratch_models) -> None:  # noqa: F811
        for month in (1, 7):
            original = datetime.datetime(2020, month, 1, 12, 30, tzinfo=PyTz())
            got = _proxy(self._row(original, None)).when
            assert got == original and got.utcoffset() == original.utcoffset()
            assert got.dst() == original.dst() and got.tzname() == original.tzname()
            assert got.astimezone(datetime.timezone.utc) == original.astimezone(
                datetime.timezone.utc
            )
            assert str(got.tzinfo) == str(original.tzinfo)
            assert got.tzinfo.zone == "Europe/Test"
        t = datetime.time(8, 5, tzinfo=PyTz())
        got_time = _proxy(self._row(None, t)).at
        assert got_time.utcoffset() == t.utcoffset() and got_time.tzname() == t.tzname()

    def test_c_tzinfo_types_and_naive_values_pass_through_untouched(self, scratch_models) -> None:  # noqa: F811
        import zoneinfo

        for value in (
            datetime.datetime(2020, 7, 1, 12, 30),
            datetime.datetime(2020, 7, 1, 12, 30, tzinfo=datetime.timezone.utc),
            datetime.datetime(2020, 7, 1, 12, 30, tzinfo=zoneinfo.ZoneInfo("Europe/Paris")),
        ):
            assert _proxy(self._row(value, None)).when is value
        t = datetime.time(8, 5, tzinfo=datetime.timezone.utc)
        assert _proxy(self._row(None, t)).at is t

    def test_a_wrapped_tzinfo_is_wrapped_once(self) -> None:
        from djust.serialization import _floored_result

        first = _floored_result(datetime.datetime(2020, 7, 1, tzinfo=PyTz()))
        assert _floored_result(first) is first

    def test_rendering_is_django_s_for_the_common_filters(self, scratch_models) -> None:  # noqa: F811
        import html
        import zoneinfo

        from django.template.backends.django import DjangoTemplates

        from djust.template import DjustTemplateBackend

        src = (
            "{% load tz %}{% for r in rows|slice:':1' %}"
            "{{ r.when|date:'c' }}|{{ r.when|date:'Y-m-d H:i T e I Z' }}|{{ r.when|time:'H:i' }}|"
            "{{ r.when|timesince:now }}|{{ r.when|timeuntil:now }}|{{ r.when }}|"
            "{{ r.at|time:'H:i T' }}|{{ r.when.tzinfo }}|{{ r.when.utcoffset }}|{{ r.when.tzname }}|"
            "{{ r.when.dst }}|{{ r.when|localtime }}|{{ r.when|utc }}|{{ r.when|timezone:'Asia/Tokyo' }}"
            "{% endfor %}"
        )
        now = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
        zones = {
            "naive": None,
            "timezone": datetime.timezone(datetime.timedelta(hours=3)),
            "zoneinfo": zoneinfo.ZoneInfo("Europe/Paris"),
            "python": PyTz(),
        }
        django_engine = DjangoTemplates(
            {"NAME": "d", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": {}}}
        )
        djust_engine = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        for label, tz in zones.items():
            for month in (1, 7):
                row = self._row(
                    datetime.datetime(2020, month, 1, 12, 30, tzinfo=tz),
                    datetime.time(8, 5, tzinfo=tz),
                )
                ctx = {"rows": [row], "now": now}
                want = html.unescape(django_engine.from_string(src).render(dict(ctx)))
                got = html.unescape(str(djust_engine.from_string(src).render(context=dict(ctx))))
                assert got == want, (label, month)


@pytest.fixture
def users_row(db):
    return User.objects.create(username="ns3888", first_name="N", last_name="S")
