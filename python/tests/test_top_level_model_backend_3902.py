"""#3902: a top-level single model through ``DjustTemplateBackend`` is a floored live handle, like a list row.

Was: ``{% with q=user|default:user %}{{ q.groups.count }}{% endwith %}`` over ONE top-level model rendered
EMPTY through ``DjustTemplateBackend`` where Django prints ``1``. ``DjustTemplate.render`` replaced the
model with ``_jit_serialize_model(...)`` (a dict: fields, no manager, no relation) before the Rust
conversion, so a FILTERED rebinding (which registers no loop alias, ``context.rs`` #2504) had nothing to
read the relation from. A list row already reached Rust raw and was a handle since #3888.

Now: the backend carries the model raw. A plain render is not state (no client payload, no msgpack, no
change detection), so there is nothing the dict was protecting. The model's reads go through the same
floor proxy a list row gets, so every security property of a row holds for the single model too; the
second half of this file runs the #3888 review attacks against a TOP-LEVEL model, side by side with the
same read over a list row.

What does NOT change, and is pinned where it lives: the LiveView entry. A LiveView holds the model as
STATE, and ADR-027 keeps models eager there (client payload, msgpack, change detection), so
``normalize_django_value`` still makes it a dict and the same filtered rebinding stays empty on that one
entry (``test_adr027_characterization_net_2539.py::TestTheAliasFallbackStays2628``).

Folded in: ``serialize_value(for_render=True)`` asked ``hasattr(value, "url") and hasattr(value, "name")``
of a model, which a ``Link(name, url)`` row satisfies, so a list row of such a model rendered ``{{ link }}``
as its URL and a row with a raising ``url`` property failed the whole render. A top-level model would have
inherited it the moment it stopped being a dict, so the guard is one rule for both.

Engine path: ``DjustTemplateBackend`` (Rust engine), compared with Django's own engine and with
``_rust.render_template`` (the raw entry, #1646 twin).

Refs #3902, #3888, #3901, #2628, ADR-027.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib
import json
import os
import re
import uuid

import pytest
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.db import models
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.utils.safestring import mark_safe

from djust import _rust
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401
from python.tests.test_model_handle_namespaces_3888 import PyTz
from python.tests.test_model_handle_review_3888 import LIBS, PRIVATE_PATHS

#: the #3888 review library plus the two filters that copy / pickle their argument
LIBS_3902 = {"libraries": {**LIBS["libraries"], "rv3902": "python.tests._reviewlib_3902"}}

SECRET = "pbkdf2-sentinel-hash-3902"
ENV_SECRET = "environ-sentinel-3902"


# -- the three renders ----------------------------------------------------------------------------
def _backend(src: str, ctx: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": LIBS_3902}
    )
    return str(backend.from_string(src).render(context=dict(ctx), request=None))


def _raw(src: str, ctx: dict) -> str:
    return _rust.render_template(src, dict(ctx))


def _django(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(dict(ctx)))


def _observe(render, src: str, ctx: dict) -> str:
    """The bytes, or ``RAISES:<type the PROJECT'S code raised>`` seen through the engine's wrapper."""
    try:
        return re.sub(r"0x[0-9a-f]+", "0x", str(render(src, ctx)))
    except Exception as exc:  # noqa: BLE001 - a measurement: the TYPE is what is compared
        cause = exc.__cause__ if type(exc) is Exception and exc.__cause__ is not None else exc
        return "RAISES:" + type(cause).__name__


# -- the world ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def password_hashes() -> tuple[str, str]:
    """Hash the two users' passwords once: PBKDF2 was most of each case's setup."""
    from django.contrib.auth.hashers import make_password

    return make_password(SECRET), make_password(SECRET + "-b")


@pytest.fixture
def world(db, scratch_models, password_hashes):
    """A user in two groups, a permission, and scratch models for the shapes ``auth`` has none of."""

    class Probe3902(models.Model):
        # fields named like methods a dict-backed template would meet first
        items = models.CharField(max_length=10, default="F-items")
        keys = models.CharField(max_length=10, default="F-keys")
        count = models.CharField(max_length=10, default="F-count")
        get = models.CharField(max_length=10, default="F-get")
        values = models.CharField(max_length=10, default="F-values")

        class Meta:
            app_label = "auth"

        @property
        def boom(self):
            raise ValueError("boom")

        @property
        def denied(self):
            raise PermissionDenied("no")

        def describe(self):
            return "described"

        def needs_arg(self, x):
            return "needs"

        def recent(self):  # a custom queryset reader on the model, not a manager
            return Group.objects.all()

        def delete_me(self):
            return "DELETED"

        delete_me.alters_data = True

    class Link3902(models.Model):
        name = models.CharField(max_length=10, default="L")
        url = models.CharField(max_length=40, default="http://x/")

        class Meta:
            app_label = "auth"

    class PropUrl3902(models.Model):
        name = models.CharField(max_length=10, default="P")

        class Meta:
            app_label = "auth"

        @property
        def url(self):
            raise ValueError("url boom")

    class Doc3902(models.Model):
        attach = models.FileField(upload_to="a")
        raw = models.BinaryField(default=b"xyz")

        class Meta:
            app_label = "auth"

    alice_hash, bob_hash = password_hashes
    # ``create_user`` minus the per-case hash: the same rows, with module-wide hashes.
    alice = User.objects.create(
        username="alice",
        email="a@example.com",
        password=alice_hash,
        is_staff=True,
        is_superuser=True,
        first_name="Alice",
    )
    alice.groups.add(Group.objects.create(name="g1"), Group.objects.create(name="g2"))
    return {
        "user": alice,
        "same": User.objects.get(pk=alice.pk),
        "other": User.objects.create(username="bob", email="b@example.com", password=bob_hash),
        "users": [alice],
        "perm": Permission.objects.order_by("pk").first(),
        "nobody": None,
        "fresh": User(username="fresh"),
        "obj": Probe3902(pk=7),
        "link": Link3902(pk=1),
        "pu": PropUrl3902(pk=2),
        "doc": Doc3902(pk=3, attach="a/b.txt"),
    }


# -- 1. the issue's reproducer and the differential ------------------------------------------------
#: ``name -> template`` over a TOP-LEVEL single model. Every row agrees with Django on the backend
#: except ``DIFFERS``: the serialization floor (``SECURE_DEFAULTS`` Pattern 1) and engine spellings the
#: raw entries share. Those rows are pinned equal to ``_rust.render_template`` instead.
MATRIX = {
    "issue": "{% with q=user|default:user %}{{ q.groups.count }}{% endwith %}",
    "str": "{{ user }}",
    "plain-fields": "{{ user.username }}|{{ user.email }}|{{ user.pk }}|{{ user.id }}|{{ user.is_active }}",
    "methods": "{{ user.get_full_name }}|{{ user.get_username }}|{{ user.is_authenticated }}",
    "date": "{{ user.date_joined|date:'Y' }}|{{ user.last_login|default:'never' }}",
    "count": "{{ user.groups.count }}",
    "all-len": "{{ user.groups.all|length }}",
    "exists": "{{ user.groups.exists }}",
    "all-loop": "{% for g in user.groups.all %}{{ g.name }},{% endfor %}",
    "first": "{{ user.groups.first.name }}",
    "all-first": "{{ user.groups.all|first }}",
    "all-first-with": "{% with g=user.groups.all|first %}{{ g.name }}{% endwith %}",
    "all-index": "{{ user.groups.all.0.name }}",
    "all-count": "{{ user.groups.all.count }}",
    "perm-count": "{{ user.user_permissions.count }}",
    "with": "{% with q=user %}{{ q.groups.count }}|{{ q.username }}{% endwith %}",
    "with-default": "{% with q=user|default:user %}{{ q.groups.count }}|{{ q.username }}{% endwith %}",
    "with-default-if-none": "{% with q=user|default_if_none:user %}{{ q.groups.count }}{% endwith %}",
    "with-default-exists": "{% with q=user|default:user %}{{ q.groups.exists }}{% endwith %}",
    "with-default-loop": "{% with q=user|default:user %}{% for g in q.groups.all %}{{ g.name }},{% endfor %}{% endwith %}",
    "with-default-first": "{% with q=user|default:user %}{{ q.groups.first.name }}{% endwith %}",
    "with-default-attname": "{% with q=user|default:user %}{{ q.id }}|{{ q.pk }}{% endwith %}",
    "default-model": "{{ user|default:'x' }}",
    "default-field": "{{ user.username|default:'x' }}",
    "dictsort": "{% for g in user.groups.all|dictsort:'name' %}{{ g.name }},{% endfor %}",
    "dictsort-reversed": "{% for g in user.groups.all|dictsortreversed:'name' %}{{ g.name }},{% endfor %}",
    "regroup": "{% regroup user.groups.all by name as r %}{% for x in r %}{{ x.grouper }}{% endfor %}",
    "with-all": "{% with a=user.groups.all %}{{ a|length }}{% endwith %}",
    "not-a-mapping": "{% for k, v in user.items %}{{ k }}{% endfor %}|{% if 'username' in user %}Y{% else %}N{% endif %}",
    "join-and-pluralize": "{{ user|join:',' }}|[{{ user|pluralize }}]",
    "equality": "{% if user == same %}eq{% else %}ne{% endif %}|{% if user == other %}eq{% else %}ne{% endif %}",
    "membership": "{% if user in users %}in{% else %}out{% endif %}",
    "missing": "[{{ user.nonexistent }}][{{ user.groups.nonexistent }}]",
    "fk": "{{ perm.content_type.app_label }}|{{ perm.content_type.model }}|{{ perm.content_type_id }}",
    "fk-with-default": "{% with p=perm|default:perm %}{{ p.content_type.app_label }}|{{ p.content_type_id }}{% endwith %}",
    "none": "[{{ nobody.username }}]|{{ nobody|default:'none' }}|{% if nobody %}y{% else %}n{% endif %}",
    "unsaved": "[{{ fresh.username }}][{{ fresh.pk }}][{{ fresh.groups.count }}]",
    "unsaved-with-default": "{% with q=fresh|default:fresh %}[{{ q.username }}][{{ q.pk }}]{% endwith %}",
    "property-raises": "[{{ obj.boom }}]",
    "permission-denied": "[{{ obj.denied }}]",
    "field-named-like-a-method": "[{{ obj.items }}][{{ obj.keys }}][{{ obj.count }}][{{ obj.get }}][{{ obj.values }}]",
    "method-with-and-without-args": "[{{ obj.describe }}][{{ obj.needs_arg }}][{{ obj.pk }}]",
    "method-through-a-filtered-rebinding": "{% with q=obj|default:obj %}[{{ q.describe }}][{{ q.items }}]{% endwith %}",
    "queryset-reader": "[{{ obj.recent.count }}][{{ obj.recent|length }}]",
    "queryset-reader-through-a-rebinding": "{% with q=obj|default:obj %}[{{ q.recent.count }}]{% endwith %}",
    "alters-data-refused": "[{{ obj.delete_me }}]",
}

#: Rows where the backend and raw entries deliberately differ from Django.
DIFFERS = {
    "floor-if": "{% if user.is_staff %}s{% endif %}",
    "iterating-a-model": "{% for x in user %}{{ x }}{% endfor %}",
    "queryset-spelling": "{{ user.groups.filter }}",
}


def _ctx(world: dict, src: str) -> dict:
    return {k: v for k, v in world.items() if re.search(r"\b" + k + r"\b", src)}


@pytest.mark.django_db
class TestADifferentialAgainstDjango:
    @pytest.mark.parametrize("name", list(MATRIX) + list(DIFFERS))
    def test_the_backend_and_the_raw_entry_agree(self, world, name) -> None:
        """Compare both djust entry points and use Django as the oracle for MATRIX rows."""
        src = {**MATRIX, **DIFFERS}[name]
        ctx = _ctx(world, src)
        django = _observe(_django, src, ctx) if name in MATRIX else None
        backend = _observe(_backend, src, ctx)
        raw = _observe(_raw, src, ctx)
        if name == "issue":
            assert django == "2", "premise: Django prints the relation"
        if name in MATRIX:
            assert backend == django, "backend vs Django"
        assert backend == raw, "backend vs raw entry (#1646)"

    def test_the_floor_still_holds_on_the_backend(self, world) -> None:
        src = "{{ user.is_staff }}|{{ user.is_superuser }}|[{{ user.password }}]"
        ctx = {"user": world["user"]}
        out = _backend(src, ctx)
        assert out == "||[]", out
        assert out == _raw(src, ctx)
        assert SECRET not in _backend("{{ user.password }}|{{ user }}", ctx)

    def test_a_data_dump_spells_the_model_as_its_str_like_a_list_row(self, world) -> None:
        """The changelog's behaviour change: ``pprint`` / ``json_script`` of a top-level model no
        longer dump its fields (Django: ``<User: alice>`` and a ``TypeError``)."""
        user = world["user"]
        src = "{{ user|pprint }}|{{ user|json_script:'x' }}"
        ctx = {"user": user}
        top = _backend(src, ctx)
        row = _backend(
            "{% for user in rows %}{{ user|pprint }}|{{ user|json_script:'x' }}{% endfor %}",
            {"rows": [user]},
        )
        assert top == row == 'alice|<script id="x" type="application/json">"alice"</script>'
        assert top == _raw(src, ctx)

    def test_the_matrix_and_the_exclusions_are_disjoint(self) -> None:
        assert not set(MATRIX) & set(DIFFERS)


@pytest.mark.django_db
class TestARequestUserTakesTheSameRoute:
    """``django.contrib.auth.context_processors.auth`` puts ``request.user`` in the context as
    ``user``: a ``SimpleLazyObject`` that ``isinstance``-s as a ``Model``. The commonest top-level
    model there is."""

    SOURCES = {
        "fields": "{{ user.username }}|{{ user.is_authenticated }}|{{ user.pk }}|{{ user }}",
        "relation": "{{ user.groups.count }}|{% for g in user.groups.all %}{{ g.name }}{% endfor %}",
        "filtered rebinding": "{% with q=user|default:user %}{{ q.groups.count }}|{{ q.username }}{% endwith %}",
        "if": "{% if user.is_authenticated %}auth{% else %}anon{% endif %}|{% if user %}t{% endif %}",
        "filtered exists": "{% with q=user|default:user %}{{ q.groups.exists }}{% endwith %}",
        "filtered loop": "{% with q=user|default:user %}{% for g in q.groups.all %}{{ g.name }},{% endfor %}{% endwith %}",
        "filtered first": "{% with q=user|default:user %}{{ q.groups.first.name }}{% endwith %}",
        "filtered attname": "{% with q=user|default:user %}{{ q.id }}|{{ q.pk }}{% endwith %}",
    }

    @staticmethod
    def _render(engine_cls, src, user):
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = user
        params = {
            "NAME": "x",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": ["django.contrib.auth.context_processors.auth"]},
        }
        template = engine_cls(params).from_string(src)
        return str(template.render({}, request))

    @pytest.mark.parametrize("name", list(SOURCES))
    @pytest.mark.parametrize("lazy", [True, False], ids=["lazy", "real"])
    def test_the_backend_renders_djangos_bytes(self, world, name, lazy) -> None:
        from django.template.backends.django import DjangoTemplates
        from django.utils.functional import SimpleLazyObject

        from djust.template import DjustTemplateBackend

        pk = world["user"].pk
        user = SimpleLazyObject(lambda: User.objects.get(pk=pk)) if lazy else world["user"]
        src = self.SOURCES[name]
        want = self._render(DjangoTemplates, src, user)
        assert want and want != "|", "premise: Django renders something"
        assert self._render(DjustTemplateBackend, src, user) == want


@pytest.mark.django_db
class TestTheGlobalFloorHoldsOnTheRequestUserRoute:
    """The floor's global fields and a configured ``DJUST_SENSITIVE_FIELDS`` name are withheld for
    ``request.user`` as the context processor delivers it: lazy, real and anonymous."""

    READS = {
        "password": "[{{ user.password }}]",
        "is_superuser": "[{{ user.is_superuser }}]",
        "is_staff": "[{{ user.is_staff }}]",
        "configured": "[{{ user.first_name }}]",
        "through a rebinding": "{% with q=user|default:user %}[{{ q.password }}|{{ q.is_staff }}|{{ q.first_name }}]{% endwith %}",
    }

    @staticmethod
    def _user(world, kind):
        from django.contrib.auth.models import AnonymousUser
        from django.utils.functional import SimpleLazyObject

        if kind == "anonymous":
            return AnonymousUser()
        pk = world["user"].pk
        if kind == "lazy":
            return SimpleLazyObject(lambda: User.objects.get(pk=pk))
        return world["user"]

    @pytest.mark.parametrize("kind", ["lazy", "real", "anonymous"])
    @pytest.mark.parametrize("name", list(READS))
    def test_a_floor_field_is_withheld(self, world, kind, name) -> None:
        from django.test.utils import override_settings

        from djust.template import DjustTemplateBackend

        from django.template.backends.django import DjangoTemplates

        src = self.READS[name]
        user = self._user(world, kind)
        with override_settings(DJUST_SENSITIVE_FIELDS=["first_name"]):
            got = TestARequestUserTakesTheSameRoute._render(DjustTemplateBackend, src, user)
            want = TestARequestUserTakesTheSameRoute._render(DjangoTemplates, src, user)
        skeleton = re.sub(r"[^\[\]|]", "", want)
        if kind == "anonymous":
            # not a model: nothing to withhold, and the engine agrees with Django
            assert got == want, (got, want)
            return
        assert re.search(r"pbkdf2|True|Alice", want), ("not vacuous: Django prints it", want)
        assert got == skeleton, (got, want)
        assert SECRET not in got and "pbkdf2" not in got


# -- 2. nothing is serialized in Python any more ---------------------------------------------------
@pytest.mark.django_db
class TestTheBackendNoLongerSerializesATopLevelModel:
    def test_neither_the_jit_helper_nor_the_normalizer_is_asked(self, world, monkeypatch) -> None:
        """The mechanism, not the output: a top-level model reaches the engine whole."""
        from djust.template import rendering

        def refuse(*args, **kwargs):
            raise AssertionError("a top-level model was serialized in Python before the engine")

        monkeypatch.setattr(rendering.DjustTemplate, "_jit_serialize_model", refuse)
        monkeypatch.setattr(rendering, "normalize_django_value", refuse)
        got = _backend("{{ user.username }}|{{ user.groups.count }}", {"user": world["user"]})
        assert got == "alice|2"

    def test_a_queryset_is_still_jit_serialized(self, world, monkeypatch) -> None:
        """The control: only the single model moved; a QuerySet keeps its optimized path."""
        from djust.template import rendering

        seen: list[str] = []
        original = rendering.DjustTemplate._jit_serialize_queryset

        def spy(self, queryset, variable_name, *args, **kwargs):
            seen.append(variable_name)
            return original(self, queryset, variable_name, *args, **kwargs)

        monkeypatch.setattr(rendering.DjustTemplate, "_jit_serialize_queryset", spy)
        got = _backend(
            "{% for g in groups %}{{ g.name }},{% endfor %}", {"groups": Group.objects.all()}
        )
        assert seen == ["groups"] and got == "g1,g2,"


# -- 3. a duck-typed file is not a model -----------------------------------------------------------
@pytest.mark.django_db
class TestAModelIsNeverMistakenForAFile:
    SRC_TOP = "[{{ link.name }}][{{ link.url }}][{{ link }}]"
    SRC_ROW = "{% for link in rows %}" + SRC_TOP + "{% endfor %}"

    def test_a_top_level_model_with_name_and_url_renders_its_str(self, world) -> None:
        ctx = {"link": world["link"]}
        got = _backend(self.SRC_TOP, ctx)
        assert got == _django(self.SRC_TOP, ctx) == _raw(self.SRC_TOP, ctx)
        assert got.endswith("[Link3902 object (1)]"), got

    def test_a_list_row_renders_its_str(self, world) -> None:
        """Was ``[http://x/]``: the row was replaced by its URL before the engine saw it."""
        ctx = {"rows": [world["link"]]}
        assert _backend(self.SRC_ROW, ctx) == _django(self.SRC_ROW, ctx)

    def test_a_model_inside_a_dict_renders_its_str(self, world) -> None:
        src = "{{ d.m }}"
        ctx = {"d": {"m": world["link"]}}
        assert _backend(src, ctx) == _django(src, ctx)

    @pytest.mark.parametrize("shape", ["top", "row"])
    def test_a_raising_url_property_does_not_fail_the_render(self, world, shape) -> None:
        """``hasattr(value, "url")`` RAN the property, and only ``AttributeError`` is swallowed."""
        top = "[{{ pu.name }}]"
        if shape == "top":
            ctx = {"pu": world["pu"]}
            assert _backend(top, ctx) == _django(top, ctx) == _raw(top, ctx) == "[P]"
        else:
            src = "{% for pu in rows %}" + top + "{% endfor %}"
            assert _backend(src, {"rows": [world["pu"]]}) == "[P]"

    def test_a_real_file_like_object_is_still_its_url(self) -> None:
        """The control: the duck-typed branch is narrower, not removed."""

        class FileLike:
            name = "n"
            url = "http://f/"

            def __bool__(self):
                return True

        assert _backend("{{ f }}", {"f": FileLike()}) == "http://f/"


@pytest.mark.django_db
class TestWhatTheOldDictDid:
    """Each documented behaviour change (changelog fragment, ADR-027 addendum), run as code."""

    def test_a_bare_filefield_prints_its_stored_name_not_its_url(self, world) -> None:
        ctx = {"doc": world["doc"]}
        for src, want in (
            ("{{ doc.attach }}", "a/b.txt"),
            ('<img src="{{ doc.attach }}">', '<img src="a/b.txt">'),
            ("{{ doc.attach.url }}", "/a/b.txt"),
        ):
            assert _django(src, ctx) == want, "premise: Django"
            assert _backend(src, ctx) == want

    @pytest.mark.parametrize(
        "src",
        ["{% for f in user %}{{ f }},{% endfor %}", "{{ user|unordered_list }}"],
        ids=["for", "unordered_list"],
    )
    def test_iterating_the_model_fails_the_render(self, world, src) -> None:
        with pytest.raises(TypeError):
            _django(src, {"user": world["user"]})
        # the engine's own error: not an empty render and not the field names
        assert _observe(_backend, src, {"user": world["user"]}).startswith("RAISES:")

    def test_join_and_pluralize_see_the_model_not_its_field_names(self, world) -> None:
        ctx = {"user": world["user"]}
        assert (
            _backend("{{ user|join:',' }}", ctx) == _django("{{ user|join:',' }}", ctx) == "alice"
        )
        assert _backend("[{{ user|pluralize }}]", ctx) == _django("[{{ user|pluralize }}]", ctx)

    def test_a_simple_tag_and_a_filter_both_get_the_floor_proxy(self, world) -> None:
        ctx = {"user": world["user"]}
        tag = _backend("{% load rv3888 %}{% attr_of user 'username' %}", ctx)
        flt = _backend("{% load rv3888 %}{{ user|dotted:'username' }}", ctx)
        assert tag == flt == "alice"
        # the argument is the proxy (a class of no methods), never the dict
        assert "_Opaque" in _backend("{% load rv3888 %}{% attr_of user '__class__' %}", ctx)

    def test_a_filter_that_copies_or_pickles_the_model_gets_the_proxys_refusal(self, world) -> None:
        read = "{% load rv3902 %}{{ user|deepcopied }}|{{ user|pickled }}"
        got = _backend(read, {"user": world["user"]})
        assert got == "ERR:copy.Error|ERR:_pickle.PicklingError", got
        # a model inside a list already refused both
        row = _backend("{% for user in rows %}" + read + "{% endfor %}", {"rows": [world["user"]]})
        assert row == got


@pytest.mark.django_db
class TestABinaryFieldIsWithheldOnATopLevelModel:
    """The type floor (#1987) refuses a ``BinaryField`` on the handle exactly as on a list row."""

    def test_the_bytes_are_withheld_like_on_a_row(self, world) -> None:
        top = _backend("[{{ doc.raw }}]", {"doc": world["doc"]})
        row = _backend("{% for doc in rows %}[{{ doc.raw }}]{% endfor %}", {"rows": [world["doc"]]})
        assert "xyz" in _django("[{{ doc.raw }}]", {"doc": world["doc"]}), (
            "premise: Django prints it"
        )
        assert top == row == "[]", (top, row)

    def test_a_dotted_read_and_a_filtered_rebinding_are_refused_too(self, world) -> None:
        src = (
            "{% load rv3888 %}[{{ doc|dotted:'raw' }}]"
            "{% with d=doc|default:doc %}[{{ d.raw }}]{% endwith %}"
        )
        top = _backend(src, {"doc": world["doc"]})
        row = _backend("{% for doc in rows %}" + src + "{% endfor %}", {"rows": [world["doc"]]})
        assert top == row == "[ERR:AttributeError][]", (top, row)
        assert "xyz" not in top


# -- 4. a tag argument is still the denylist dict --------------------------------------------------
@pytest.fixture
def capture_tag():
    from djust.template_tags import TagHandler, register

    seen: list = []

    @register("cap3902")
    class _Cap(TagHandler):
        def render(self, args, context):
            seen.append(list(args))
            return "T"

    seen.clear()
    return seen


@pytest.mark.django_db
class TestATagArgumentIsStillTheDict:
    def test_a_top_level_model_argument_is_the_denylist_dict(self, world, capture_tag) -> None:
        from djust.serialization import DjangoJSONEncoder, normalize_django_value

        user = world["user"]
        _backend("{% cap3902 user=user %}", {"user": user})
        (args,) = capture_tag
        (text,) = [a for a in args if a.startswith("user=")]
        got = json.loads(text.split("=", 1)[1])
        want = json.loads(
            json.dumps(normalize_django_value(user), cls=DjangoJSONEncoder, default=str)
        )
        assert got == want
        assert SECRET not in text and "is_staff" not in text

    def test_it_is_the_text_a_list_row_gives(self, world, capture_tag) -> None:
        user = world["user"]
        _backend("{% cap3902 user=user %}", {"user": user})
        top = list(capture_tag[0])
        capture_tag.clear()
        _backend("{% for user in rows %}{% cap3902 user=user %}{% endfor %}", {"rows": [user]})
        assert list(capture_tag[0]) == top


# -- 5. the #3888 review attacks, against a TOP-LEVEL model, beside the same read over a row --------
@pytest.fixture
def ns(world, monkeypatch):
    """A scratch model carrying each shape the #3888 reviews found reachable, plus the real user."""
    monkeypatch.setenv("A3902_ENV_SECRET", ENV_SECRET)

    def a_function():  # carries ``__globals__``: a module namespace
        return None

    class Ns3902(models.Model):
        uid = models.UUIDField(default=uuid.uuid4)
        when = models.DateTimeField(null=True)

        class Meta:
            app_label = "auth"

        @property
        def a_module(self):
            return os

        @property
        def a_function(self):
            return a_function

        @property
        def a_class(self):
            return type(self)

        @property
        def a_descriptor(self):
            return property(lambda s: s)

        @property
        def by_model(self):
            return {self: 1, "plain": 2}

        @property
        def html(self):
            return mark_safe("<b>x</b>")

    import datetime

    row = Ns3902(pk=1, when=datetime.datetime(2020, 7, 1, 12, 30, tzinfo=PyTz()))
    return {"n": row, "u": world["user"], "rows_n": [row], "rows_u": [world["user"]]}


def _top_and_row(read: str, var: str, ns: dict, *, unescape: bool = True) -> tuple[str, str]:
    """The same read over the model as a top-level variable and as a list row."""
    clean = html_lib.unescape if unescape else str
    top = clean(_backend(read, {"u": ns[var]}))
    row = clean(_backend("{% for u in rows %}" + read + "{% endfor %}", {"rows": [ns[var]]}))
    return top, row


@pytest.mark.django_db
class TestTheFloorHoldsForATopLevelModel:
    @pytest.mark.parametrize("path", PRIVATE_PATHS)
    def test_a_dotted_filter_is_refused(self, ns, path) -> None:
        top, row = _top_and_row("{% load rv3888 %}[{{ u|dotted:'" + path + "' }}]", "u", ns)
        assert top == row
        assert SECRET not in top and "pbkdf2" not in top, top
        assert "ERR:AttributeError" in top, top

    @pytest.mark.parametrize("name", ["_obj", "_s", "__dict__", "_denied"])
    def test_a_simple_tag_is_refused(self, ns, name) -> None:
        top, row = _top_and_row("{% load rv3888 %}[{% attr_of u '" + name + "' %}]", "u", ns)
        assert top == row == "[ERR:AttributeError]"

    def test_the_class_is_a_class_of_no_methods_not_the_models(self, ns) -> None:
        top, row = _top_and_row("{% load rv3888 %}[{{ u|dotted:'__class__.__name__' }}]", "u", ns)
        assert top == row
        assert "_Opaque" in top and "User" not in top, top

    @pytest.mark.parametrize(
        "path",
        ["a_module", "a_module.environ", "a_function.__globals__", "a_class", "a_descriptor"],
    )
    def test_no_python_namespace_is_reachable(self, ns, path) -> None:
        """The dotted-getattr route to a module namespace and ``os.environ``."""
        top, row = _top_and_row("{% load rv3888 %}[{{ u|dotted:'" + path + "' }}]", "n", ns)
        assert top == row == "[ERR:AttributeError]"
        assert ENV_SECRET not in top and "environ" not in top

    def test_a_module_walk_through_a_manager_is_refused(self, ns) -> None:
        for path in ("_qs", "_is_projection", "all._fn", "all.__self__", "all.__closure__"):
            top, row = _top_and_row(
                "{% load rv3888 %}[{{ u.groups|dotted:'" + path + "' }}]", "u", ns
            )
            assert top == row and "ERR:AttributeError" in top, (path, top)

    def test_a_uuid_is_its_text(self, ns) -> None:
        top, row = _top_and_row("{% load rv3888 %}{{ u.uid }}|{{ u|dotted:'uid'|kind }}", "n", ns)
        assert top == row
        assert top == f"{ns['n'].uid}|str", top

    def test_a_python_tzinfo_is_not_returned_raw(self, ns) -> None:
        reads = (
            "{{ u|dotted:'when.tzinfo.__class__.__name__' }}|"
            "{{ u|dotted:'when.tzinfo.utcoffset.__globals__' }}|{{ u|dotted:'when.tzinfo.__dict__' }}"
        )
        top, row = _top_and_row("{% load rv3888 %}" + reads, "n", ns)
        assert top == row
        assert top.split("|") == ["_Opaque", "ERR:AttributeError", "ERR:AttributeError"], top

    def test_a_dict_key_that_is_a_model_is_floored(self, ns) -> None:
        top, row = _top_and_row(
            "{% load rv3888 %}{% for k in u.by_model %}{{ k|kind }},{% endfor %}", "n", ns
        )
        assert top == row
        assert top and "Ns3902" not in top, top

    def test_safe_markup_keeps_its_flag_and_a_plain_string_is_escaped(self, ns) -> None:
        top, row = _top_and_row("{{ u.html }}|{{ u.html|force_escape }}", "n", ns, unescape=False)
        assert top == row
        assert top == "<b>x</b>|&lt;b&gt;x&lt;/b&gt;", top

    def test_the_model_cannot_be_copied_through_a_tag_argument_unfloored(
        self, ns, capture_tag
    ) -> None:
        """A tag argument is text, never the handle (section 4), whichever shape the model had."""
        _backend("{% cap3902 user=u %}", {"u": ns["u"]})
        assert all(isinstance(a, str) for a in capture_tag[0])
