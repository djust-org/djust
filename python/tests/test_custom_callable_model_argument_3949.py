"""What a custom filter or tag receives for a model in state: pinned on every entry (#3949).

A custom ``@register.filter``, ``@register.simple_tag`` (also used with ``as``), ``inclusion_tag`` and
``takes_context`` tag, and a Django-library tag reached through the ``{% load %}`` bridge, are handed a
model by the engine. This file pins WHAT they are handed, on each entry the engine has:

* **liveview** - the real ``LiveViewTestClient``, the model held as public state set in ``mount``;
* **backend** - ``DjustTemplateBackend``;
* **raw** - ``djust._rust.render_template``;

with Django's own engine on real instances as the oracle.

The premise in #3949 ("hand such callables the live model, as the backend entry already does") does not
describe the backend. No entry hands custom code a live Django instance:

* the **backend** and **raw** entries hand a floor-enforcing proxy, ``djust.serialization._SidecarModelProxy``:
  not a ``Model`` instance, ``password`` and ``_meta`` raise ``AttributeError``, no ``v["x"]``, and ``str()``
  is the model's own text;
* the **LiveView** entry hands a plain ``dict`` (the model is turned into the denylist dict in Python
  before the Rust engine sees it, ADR-027: models stay eager on the state path).

What is invariant on every entry, and is what a custom callable can rely on: the global-sensitive field
(``password``), and a field the model lists in ``djust_exclude_fields``, are never present, as a value or
as a key. What differs between the entries is pinned here as a divergence, not as a contract: unifying the
entries is the 1.4.0 render-time handle overlay, decided together with #3936 behind an ADR-027 addendum.
Every assertion that describes behaviour the overlay will deliberately change carries the comment
``changes with the 1.4.0 handle overlay (#3949, #3936)``.

Not pinned on purpose: the NUMBER of keys in a LiveView dict. It depends on what other expressions in the
template name (JIT serialization): the same filter receives a different dict when the template also writes
``{{ x.username }}``. ``TestTheLiveViewDictIsWhatTheTemplateNames`` pins that dependence, not the count.

Engine path: the Rust engine through all three entries; the Python template engine (Django) as oracle.

Refs #3949, #3936.
"""

# ruff: noqa: F811

from __future__ import annotations

import html as html_lib
import json
import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.template import Context as DjangoContext
from django.template.engine import Engine

from djust import _rust, template_libraries
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from python.tests import _customlib_3949 as lib
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401

LABEL = "cc3949"
LIBS = {LABEL: "python.tests._customlib_3949"}
DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*?)</div>", re.S)

SENT_A = "SENT-A-3949"  # the model's @property
SENT_B = "SENT-B-3949"  # the model's get_*() method
SENT_C = "SENT-C-3949"  # an ordinary (non-floor) field: first_name
SENT_D = "SENT-D-3949"  # last_name: an ordinary field, here only so get_full_name() has two parts
EXCLUDED = "EXCLUDED-3949"  # the field the model lists in djust_exclude_fields: email
HASH_MARK = "HASHMARK3949"
PASSWORD_HASH = f"pbkdf2_sha256$1$salt${HASH_MARK}"

ENTRIES = ("liveview", "backend", "raw")
IDENTITY_KEYS = {"id", "pk", "__str__", "__model__"}
PROXY, QS_PROXY = "_SidecarModelProxy", "_SidecarQuerySetProxy"


# -- registration that does not leak ---------------------------------------------------------------


def _forget_library() -> None:
    """Remove every trace of the test library from the process-global registries.

    ``{% load cc3949 %}`` bridges the library into the Rust registries (process-global by design) and into
    ``template_libraries``' bookkeeping, which ``reassert()`` re-applies before every later test. Both are
    undone here, for the library's own names only, so a later test never sees ``cc3949_*``.
    """
    names_t = tuple(lib.register.tags)
    names_f = tuple(lib.register.filters)
    for name in names_t:
        for remove in (_rust.unregister_tag_handler, _rust.unregister_block_tag_handler):
            try:
                remove(name)
            except Exception:  # noqa: BLE001 - a name that was never registered
                pass
    for name in names_f:
        try:
            _rust.unregister_custom_filter(name)
        except Exception:  # noqa: BLE001
            pass
    compile_funcs = set(lib.register.tags.values())
    for state in (
        "_owned_tags",
        "_handlers",
        "_loaded",
        "_tag_owner",
        "_filter_owner",
        "_refused_tags",
        "_loaded_subsets",
    ):
        module_dict = getattr(template_libraries, state)
        containers = {id(module_dict): module_dict}
        engine_dict = template_libraries._engine_state(state, module_dict)
        containers[id(engine_dict)] = engine_dict
        for container in containers.values():
            for key in list(container):
                if (
                    key in names_t
                    or key in names_f
                    or key == LABEL
                    or key in compile_funcs
                    or (isinstance(key, tuple) and key and key[0] == LABEL)
                ):
                    del container[key]


@pytest.fixture(autouse=True)
def _library():
    with patch.dict(template_libraries._extra_libraries, LIBS):
        try:
            yield
        finally:
            _forget_library()


# -- the world -------------------------------------------------------------------------------------


@pytest.fixture
def world(db, scratch_models):  # noqa: F811
    """Two accounts: a proxy of ``User`` with a property and a method that return sentinels.

    ``password`` holds a recognisable hash, and ``email`` is named in ``djust_exclude_fields``.
    """

    class Account(User):
        djust_exclude_fields = (lib.EXCLUDED_FIELD,)

        class Meta:
            proxy = True
            app_label = "auth"

        @property
        def owner_code(self):
            return SENT_A

        def get_note_text(self):
            return SENT_B

        def __str__(self):
            return f"Account:{self.username}"

    rows = [
        Account.objects.create(
            username=name,
            first_name=SENT_C,
            last_name=SENT_D,
            email=EXCLUDED,
            password=PASSWORD_HASH,
        )
        for name in ("a1", "a2")
    ]
    return {"Account": Account, "a1": rows[0], "a2": rows[1]}


def _shapes(w: dict) -> dict:
    Account = w["Account"]
    return {
        "model": lambda: w["a1"],
        "list": lambda: [w["a1"], w["a2"]],
        "queryset": lambda: Account.objects.filter(username__in=("a1", "a2")).order_by("username"),
        "dict-of-model": lambda: {"owner": w["a1"]},
        "dict-of-list": lambda: {"items": [w["a1"], w["a2"]]},
    }


# -- the entries -----------------------------------------------------------------------------------


def _liveview(src: str, ctx: dict) -> str:
    """The real LiveView entry, with the values held as PUBLIC STATE set in ``mount``."""

    def mount(self, request, **kwargs):
        for name, value in ctx.items():
            setattr(self, name, value)

    view = type(
        "CallableView",
        (LiveView,),
        {"exposure_policy": "legacy", "template": f"<div dj-root>{src}</div>", "mount": mount},
    )
    client = LiveViewTestClient(view)
    client.mount()
    match = DJ_ROOT.search(client.render())
    assert match is not None
    return html_lib.unescape(match.group(1))


def _backend(src: str, ctx: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"libraries": LIBS}}
    )
    return html_lib.unescape(str(backend.from_string(src).render(context=dict(ctx), request=None)))


def _raw(src: str, ctx: dict) -> str:
    return html_lib.unescape(_rust.render_template(src, dict(ctx)))


def _django(src: str, ctx: dict) -> str:
    engine = Engine(libraries={**LIBS, "i18n": "django.templatetags.i18n"})
    return html_lib.unescape(engine.from_string(src).render(DjangoContext(dict(ctx))))


RENDER = {"liveview": _liveview, "backend": _backend, "raw": _raw}


# -- reading what a callable reported --------------------------------------------------------------


def _docs(out: str) -> list[dict]:
    """The JSON documents the callables wrote, one per line."""
    return [json.loads(line) for line in out.strip().split("\n") if line.strip()]


def _sig(doc: dict) -> str:
    """The shape a callable received, as one short string."""
    kind = doc["type"]
    if kind in ("list", "tuple"):
        return "list[" + ",".join(_sig(item) for item in doc["items"]) + "]"
    if kind == "dict":
        children = doc.get("children") or {}
        if children:
            return "dict{" + ",".join(f"{k}:{_sig(v)}" for k, v in sorted(children.items())) + "}"
        return "dict"
    return {PROXY: "proxy", QS_PROXY: "qsproxy"}.get(kind, kind)


def _leaves(doc: dict):
    """Every model-like value inside ``doc``: a dict row, a model proxy, a live instance, a queryset proxy."""
    kind = doc["type"]
    if kind in ("list", "tuple"):
        for item in doc["items"]:
            yield from _leaves(item)
    elif kind == "dict" and doc.get("children"):
        for child in doc["children"].values():
            yield from _leaves(child)
    elif kind == QS_PROXY:
        yield doc
        for item in doc["iter"]:
            yield from _leaves(item)
    elif kind not in ("str", "int", "float", "bool", "NoneType"):
        yield doc


#: tag forms: every one of them is handed the same thing as the others, on every cell below
TAG_CALLS = {
    "simple_tag": "{% load cc3949 %}{% cc3949_tag x %}",
    "assign_tag": "{% load cc3949 %}{% cc3949_tag x as r %}{{ r }}",
    "inclusion_tag": "{% load cc3949 %}{% cc3949_incl x %}",
    "takes_context": "{% load cc3949 %}{% cc3949_ctx 'x' %}",
}
FILTER_CALL = "{% load cc3949 %}{{ x|cc3949_filter }}"
#: a model that reached the callable as a loop variable
LOOP_CALLS = {
    "loop-filter": "{% load cc3949 %}{% for r in x %}{{ r|cc3949_filter }}\n{% endfor %}",
    "loop-simple_tag": "{% load cc3949 %}{% for r in x %}{% cc3949_tag r %}\n{% endfor %}",
}

# -- the measured matrix: (shape, group) -> the type each entry hands the callable ------------------
#
# group "filter" is the custom filter; "tag" is every tag form above; "loop" is the loop variable of
# {% for r in x %} handed to a filter or a tag.
#
# Every LiveView cell below, and the two backend QuerySet cells, describe behaviour the 1.4.0 handle
# overlay will deliberately change (#3949, #3936).
TYPES = {
    # a single model
    ("model", "filter"): {
        "liveview": "dict",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "proxy",
        "raw": "proxy",
    },
    ("model", "tag"): {
        "liveview": "dict",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "proxy",
        "raw": "proxy",
    },
    # a list of models
    ("list", "filter"): {
        "liveview": "list[dict,dict]",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "list[proxy,proxy]",
        "raw": "list[proxy,proxy]",
    },
    ("list", "tag"): {
        "liveview": "list[dict,dict]",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "list[proxy,proxy]",
        "raw": "list[proxy,proxy]",
    },
    ("list", "loop"): {
        "liveview": "dict",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "proxy",
        "raw": "proxy",
    },
    # a QuerySet: the backend hands a FILTER the serialized rows and a TAG a queryset proxy
    ("queryset", "filter"): {
        "liveview": "list[dict,dict]",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "list[dict,dict]",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "raw": "list[proxy,proxy]",
    },
    ("queryset", "tag"): {
        "liveview": "list[dict,dict]",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "qsproxy",
        "raw": "qsproxy",
    },
    ("queryset", "loop"): {
        "liveview": "dict",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "dict",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "raw": "proxy",
    },
    # a dict that HOLDS models stays a dict on every entry; what it holds differs
    ("dict-of-model", "filter"): {
        "liveview": "dict{owner:dict}",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "dict{owner:proxy}",
        "raw": "dict{owner:proxy}",
    },
    ("dict-of-model", "tag"): {
        "liveview": "dict{owner:dict}",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "dict{owner:proxy}",
        "raw": "dict{owner:proxy}",
    },
    ("dict-of-list", "filter"): {
        "liveview": "dict{items:list[dict,dict]}",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "dict{items:list[proxy,proxy]}",
        "raw": "dict{items:list[proxy,proxy]}",
    },
    ("dict-of-list", "tag"): {
        "liveview": "dict{items:list[dict,dict]}",  # changes with the 1.4.0 handle overlay (#3949, #3936)
        "backend": "dict{items:list[proxy,proxy]}",
        "raw": "dict{items:list[proxy,proxy]}",
    },
}
#: shapes whose rows the LiveView dict carries IN FULL when the template names nothing on them
FULL_ROWS = {"list", "queryset", "dict-of-list"}
#: shapes whose LiveView dict carries only the identity keys when the template names nothing on them
IDENTITY_ONLY = {"model", "dict-of-model"}


def _cells():
    """(shape, group, callable-id, template, entry) for every cell of the matrix."""
    for entry in ENTRIES:
        for shape in ("model", "list", "queryset", "dict-of-model", "dict-of-list"):
            calls = {"filter": FILTER_CALL}
            calls.update({name: body for name, body in TAG_CALLS.items()})
            for call_id, body in calls.items():
                group = "filter" if call_id == "filter" else "tag"
                yield pytest.param(shape, group, body, entry, id=f"{entry}-{shape}-{call_id}")
        for shape in ("list", "queryset"):
            for call_id, body in LOOP_CALLS.items():
                yield pytest.param(shape, "loop", body, entry, id=f"{entry}-{shape}-{call_id}")


def _received(world, shape: str, body: str, entry: str) -> list[dict]:
    return _docs(RENDER[entry](body, {"x": _shapes(world)[shape]()}))


# -- 1. the type -----------------------------------------------------------------------------------


class TestTheTypeACustomCallableReceives:
    @pytest.mark.parametrize("shape,group,body,entry", list(_cells()))
    def test_the_type_received_on_each_entry(self, world, shape, group, body, entry) -> None:
        docs = _received(world, shape, body, entry)
        expected = TYPES[(shape, group)][entry]
        got = [_sig(d) for d in docs]
        # a loop yields one document per row, everything else exactly one
        assert got == [expected] * (2 if group == "loop" else 1), (entry, shape, group, got)

    @pytest.mark.parametrize("shape,group,body,entry", list(_cells()))
    def test_it_is_never_a_live_model_instance(self, world, shape, group, body, entry) -> None:
        """The premise of #3949 corrected: not on the backend, not on raw, not on LiveView."""
        for doc in _received(world, shape, body, entry):
            for leaf in _leaves(doc):
                assert leaf["is_model"] is False, (entry, shape, group, leaf["type"])

    @pytest.mark.parametrize("entry", ENTRIES)
    @pytest.mark.parametrize(
        "body",
        [
            "{% load cc3949 %}{{ x.username|cc3949_filter }}",
            "{% load cc3949 %}{% cc3949_tag x.username %}",
            "{% load cc3949 %}{% cc3949_tag x.owner_code %}",
            "{% load cc3949 %}{{ x.get_note_text|cc3949_filter }}",
        ],
    )
    def test_a_field_the_template_reads_arrives_as_the_plain_value_on_every_entry(
        self, world, entry, body
    ) -> None:
        """Passing the field, not the model, is the same on every entry: a ``str``."""
        (doc,) = _docs(RENDER[entry](body, {"x": world["a1"]}))
        assert doc == {"is_model": False, "type": "str"}, (entry, body, doc)

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_a_filter_and_a_tag_are_handed_the_same_thing_except_for_a_queryset(
        self, world, entry
    ) -> None:
        for shape in ("model", "list", "dict-of-model", "dict-of-list"):
            as_filter = _received(world, shape, FILTER_CALL, entry)
            for call_id, body in TAG_CALLS.items():
                assert _sig(_received(world, shape, body, entry)[0]) == _sig(as_filter[0]), (
                    entry,
                    shape,
                    call_id,
                )

    def test_a_queryset_is_the_one_shape_a_filter_and_a_tag_disagree_on_the_backend(
        self, world
    ) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        queryset = {"x": _shapes(world)["queryset"]()}
        as_filter = _sig(_docs(_backend(FILTER_CALL, queryset))[0])
        as_tag = _sig(_docs(_backend(TAG_CALLS["simple_tag"], queryset))[0])
        assert (as_filter, as_tag) == ("list[dict,dict]", "qsproxy")
        # the raw entry hands both the proxies
        as_filter = _sig(_docs(_raw(FILTER_CALL, queryset))[0])
        as_tag = _sig(_docs(_raw(TAG_CALLS["simple_tag"], queryset))[0])
        assert (as_filter, as_tag) == ("list[proxy,proxy]", "qsproxy")


# -- 2. the floor: what holds on every entry -------------------------------------------------------


class TestTheFloorHoldsOnEveryEntry:
    @pytest.mark.parametrize("shape,group,body,entry", list(_cells()))
    def test_neither_the_password_nor_an_excluded_field_is_ever_received(
        self, world, shape, group, body, entry
    ) -> None:
        raw_out = RENDER[entry](body, {"x": _shapes(world)[shape]()})
        # nothing the callable reported contains the hash or the excluded value, as value or as key
        assert HASH_MARK not in raw_out, (entry, shape, group)
        assert EXCLUDED not in raw_out, (entry, shape, group)
        for doc in _docs(raw_out):
            for leaf in _leaves(doc):
                if leaf["type"] == "dict":
                    assert "password" not in leaf["keys"], (entry, shape, leaf["keys"])
                    assert lib.EXCLUDED_FIELD not in leaf["keys"], (entry, shape, leaf["keys"])
                    assert leaf["get_pw"] == "ABSENT"
                    assert leaf["get_excluded"] == "ABSENT"
                else:
                    assert leaf["pw_read"] == "ERR:AttributeError", (entry, shape, leaf["type"])
                    assert leaf["excluded_read"] == "ERR:AttributeError", (entry, shape)

    @pytest.mark.parametrize("shape,group,body,entry", list(_cells()))
    def test_a_str_of_the_value_never_writes_the_hash_or_the_excluded_field(
        self, world, shape, group, body, entry
    ) -> None:
        """A callable that PRINTS its argument writes ``str(value)``: that text holds neither."""
        for doc in _received(world, shape, body, entry):
            blob = json.dumps(doc)
            assert HASH_MARK not in blob and EXCLUDED not in blob, (entry, shape, group)

    @pytest.mark.parametrize("entry", ("backend", "raw"))
    def test_a_proxy_is_what_a_filter_author_can_read(self, world, entry) -> None:
        (doc,) = _received(world, "model", FILTER_CALL, entry)
        assert doc["type"] == PROXY and doc["is_model"] is False
        assert doc["str"] == "Account:a1"  # the model's own text, not a mapping of its fields
        # attributes a filter reads on the model work: a field, a property, a method, the pk
        assert doc["username"] == "a1" and doc["first_name"] == SENT_C
        assert doc["owner_code"] == SENT_A and doc["note_text"] == SENT_B
        assert isinstance(doc["pk_read"], int)
        # what it cannot do: the floor names, the model's _meta, dict-style access
        assert doc["pw_read"] == "ERR:AttributeError"
        assert doc["excluded_read"] == "ERR:AttributeError"
        assert doc["meta_read"] == "ERR:AttributeError"
        assert doc["subscript"] == "ERR:TypeError"
        assert doc["iter"] == "ERR:TypeError"

    def test_a_queryset_proxy_refuses_attributes_and_yields_proxies_when_iterated(
        self, world
    ) -> None:
        (doc,) = _received(world, "queryset", TAG_CALLS["simple_tag"], "backend")
        assert doc["type"] == QS_PROXY
        assert doc["username"] == "ERR:AttributeError"
        assert [_sig(item) for item in doc["iter"]] == ["proxy", "proxy"]
        assert [item["username"] for item in doc["iter"]] == ["a1", "a2"]
        assert all(item["pw_read"] == "ERR:AttributeError" for item in doc["iter"])

    @pytest.mark.parametrize("shape", ("list", "queryset", "dict-of-list"))
    def test_liveview_full_rows_carry_the_non_floor_values_as_a_mapping(self, world, shape) -> None:
        """A row the template names nothing on is the serialized mapping, and a filter reads it by key.

        The sentinel in an ordinary field, in a property and in a ``get_*()`` result IS present on the
        LiveView dict (these are the values "the page never named"); the floor names are not.
        """
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        (doc,) = _received(world, shape, FILTER_CALL, "liveview")
        rows = [leaf for leaf in _leaves(doc) if leaf["type"] == "dict"]
        assert len(rows) == 2
        for row in rows:
            assert IDENTITY_KEYS <= set(row["keys"])
            assert row["get_first_name"] == SENT_C
            assert row["get_owner_code"] == SENT_A
            assert row["get_note_text"] == SENT_B
            assert SENT_C in row["str"], "str(row) is the mapping, which writes the field values"

    @pytest.mark.parametrize("shape", ("model", "dict-of-model"))
    def test_liveview_a_model_the_template_names_nothing_on_is_the_identity_keys(
        self, world, shape
    ) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        (doc,) = _received(world, shape, FILTER_CALL, "liveview")
        (row,) = [leaf for leaf in _leaves(doc) if leaf["type"] == "dict"]
        assert IDENTITY_KEYS <= set(row["keys"])
        assert row["get_first_name"] == "ABSENT"
        assert SENT_C not in row["str"]

    def test_the_django_oracle_hands_the_live_instance_and_its_hash(self, world) -> None:
        """The reference point: Django gives a filter the real instance. No djust entry does."""
        (doc,) = _docs(_django(FILTER_CALL, {"x": world["a1"]}))
        assert doc["type"] == "Account" and doc["is_model"] is True
        assert doc["pw_read"] == PASSWORD_HASH
        assert doc["excluded_read"] == EXCLUDED
        assert doc["subscript"] == "ERR:TypeError"

    def test_the_django_oracle_hands_a_list_of_live_instances(self, world) -> None:
        (doc,) = _docs(_django(FILTER_CALL, {"x": [world["a1"], world["a2"]]}))
        assert _sig(doc) == "list[Account,Account]"
        assert all(item["is_model"] for item in doc["items"])


class TestAnExcludedFieldIsNotWhatIsComputedFromIt:
    """``djust_exclude_fields`` excludes the FIELD. A method or property that computes from it is a result.

    ``User.get_full_name()`` joins ``first_name`` and ``last_name``. With ``last_name`` excluded, the
    field is gone on every entry (no key, no attribute), and the zero-argument method result that
    contains its text is still handed over: as a value of the LiveView dict and as a call on the proxy.
    This is how the floor already behaved for a template (``{{ x.get_full_name }}`` prints it); it is
    pinned here so the guide can say what the guarantee is, and so a change to it is deliberate.
    """

    @pytest.fixture
    def named(self, db, scratch_models):  # noqa: F811
        class Named(User):
            djust_exclude_fields = ("last_name",)

            class Meta:
                proxy = True
                app_label = "auth"

            def __str__(self):
                return f"Named:{self.username}"

        rows = [
            Named.objects.create(username=n, first_name="First", last_name=EXCLUDED)
            for n in ("n1", "n2")
        ]
        return rows

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_the_field_is_gone_and_the_computed_value_is_not(self, named, entry) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        # (the LiveView dict becomes a proxy; the proxy's answer below is already today's backend answer)
        (doc,) = _docs(RENDER[entry](FILTER_CALL, {"x": named}))
        for leaf in _leaves(doc):
            if leaf["type"] == "dict":
                assert "last_name" not in leaf["keys"]
                assert leaf["get_full_name"] == f"First {EXCLUDED}"
            else:
                assert leaf["last_name_read"] == "ERR:AttributeError"
                assert leaf["full_name"] == f"First {EXCLUDED}"


# -- 3. the LiveView dict is whatever the template names --------------------------------------------


class TestTheLiveViewDictIsWhatTheTemplateNames:
    """The LiveView dict is not a fixed shape: its keys depend on what ELSE the template names.

    This is why "pin the dict" cannot give a custom filter a contract on that entry, and an independent
    argument for the 1.4.0 handle overlay. Pinned as the dependence, not as a key count.
    """

    def test_a_property_the_template_names_elsewhere_is_in_the_dict(self, world) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        out = _liveview(
            "{% load cc3949 %}{{ x.owner_code }}|{{ x|cc3949_filter }}", {"x": world["a1"]}
        )
        named, received = out.split("|", 1)
        assert named == SENT_A
        row = json.loads(received)
        assert row["type"] == "dict" and row["get_owner_code"] == SENT_A

    def test_naming_a_different_path_prunes_the_dict_the_filter_receives(self, world) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        out = _liveview(
            "{% load cc3949 %}{{ x.username }}|{{ x|cc3949_filter }}", {"x": world["a1"]}
        )
        named, received = out.split("|", 1)
        assert named == "a1"
        row = json.loads(received)
        assert row["type"] == "dict"
        assert row["get_owner_code"] == "ABSENT" and row["get_first_name"] == "ABSENT"

    def test_the_same_filter_on_the_backend_does_not_depend_on_its_neighbours(self, world) -> None:
        for src in (
            "{% load cc3949 %}{{ x|cc3949_filter }}",
            "{% load cc3949 %}{{ x.username }}|{{ x|cc3949_filter }}",
        ):
            out = _backend(src, {"x": world["a1"]})
            row = json.loads(out.split("|", 1)[-1])
            assert row["type"] == PROXY and row["owner_code"] == SENT_A, out

    def test_a_list_row_the_template_names_a_path_on_is_pruned_too(self, world) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        out = _liveview(
            "{% load cc3949 %}{% for r in x %}{{ r.username }}{% endfor %}|{{ x|cc3949_filter }}",
            {"x": [world["a1"], world["a2"]]},
        )
        named, received = out.split("|", 1)
        assert named == "a1a2"
        doc = json.loads(received)
        assert _sig(doc) == "list[dict,dict]"
        assert all(item["get_first_name"] == "ABSENT" for item in doc["items"])


# -- 4. a Django-library tag reached through the {% load %} bridge ----------------------------------


class TestADjangoLibraryTagThroughTheLoadBridge:
    """``{% blocktranslate with u=x %}[{{ u }}]{% endblocktranslate %}`` prints its operand.

    The tag is Django's own, bridged by ``{% load i18n %}``; the operand it prints is what the engine
    hands it, so the entries differ the same way the custom callables do.
    """

    BODY = "{% load i18n %}{% blocktranslate with u=x %}[{{ u }}]{% endblocktranslate %}"

    @pytest.mark.parametrize("entry", ("backend", "raw"))
    def test_backend_and_raw_print_the_models_own_text(self, world, entry) -> None:
        run = RENDER[entry]
        assert run(self.BODY, {"x": world["a1"]}) == "[Account:a1]"
        assert run(self.BODY, {"x": [world["a1"], world["a2"]]}) == (
            "[[<Account: Account:a1>, <Account: Account:a2>]]"
        )
        assert run(self.BODY, {"x": _shapes(world)["queryset"]()}) == (
            "[<QuerySet [<Account: Account:a1>, <Account: Account:a2>]>]"
        )

    def test_liveview_prints_the_mapping_for_a_model(self, world) -> None:
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        out = _liveview(self.BODY, {"x": world["a1"]})
        assert out.startswith("[{") and "'__str__': 'Account:a1'" in out
        assert "Account:a1" in out

    @pytest.mark.parametrize("shape", ("list", "queryset"))
    def test_liveview_prints_every_collected_value_for_unnamed_rows(self, world, shape) -> None:
        """The leak the issue names: a bridged library tag that prints its operand writes values the
        page never named. ``password`` and the excluded field are not among them (the floor holds)."""
        # changes with the 1.4.0 handle overlay (#3949, #3936)
        out = _liveview(self.BODY, {"x": _shapes(world)[shape]()})
        assert SENT_C in out and SENT_A in out and SENT_B in out
        assert HASH_MARK not in out and EXCLUDED not in out
        assert "password" not in out

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_the_floor_holds_for_a_library_tag_on_every_entry(self, world, entry) -> None:
        for shape in _shapes(world):
            out = RENDER[entry](self.BODY, {"x": _shapes(world)[shape]()})
            assert HASH_MARK not in out and EXCLUDED not in out, (entry, shape, out[:300])
            assert "password" not in out, (entry, shape)

    @pytest.mark.parametrize("entry", ENTRIES)
    def test_naming_the_field_prints_the_field_on_every_entry(self, world, entry) -> None:
        """The advice the guide gives: put the field, not the model, in the tag's operand."""
        body = (
            "{% load i18n %}{% blocktranslate with u=x.username %}[{{ u }}]{% endblocktranslate %}"
        )
        assert RENDER[entry](body, {"x": world["a1"]}) == "[a1]"

    def test_django_prints_the_text(self, world) -> None:
        assert _django(self.BODY, {"x": world["a1"]}) == "[Account:a1]"


# -- 5. the registrations do not leak ---------------------------------------------------------------


class TestTheTestLibraryDoesNotLeak:
    def test_forgetting_removes_the_library_from_every_registry(self, world) -> None:
        # a {% load %} bridges the library into the process-global Rust registry: a template that does
        # NOT load it still resolves the names
        _raw(TAG_CALLS["simple_tag"], {"x": world["a1"]})
        assert json.loads(_raw("{{ x|cc3949_filter }}", {"x": 1}))["type"] == "int"
        assert "cc3949_tag" in template_libraries.owned_tags()
        _forget_library()
        assert not [n for n in template_libraries.owned_tags() if n.startswith("cc3949")]
        assert LABEL not in template_libraries._loaded
        # the Rust registry no longer knows the filter: a template naming it is refused at parse time
        with pytest.raises(Exception, match="cc3949_filter"):
            _rust.render_template("{{ x|cc3949_filter }}", {"x": 1})
