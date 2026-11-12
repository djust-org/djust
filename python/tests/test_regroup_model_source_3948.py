"""``{% regroup %}`` over ONE model refuses, as Django does; over a container of models it is unchanged (#3948).

Django's ``RegroupNode.render`` runs ``groupby()`` over the resolved source. A model instance is not
iterable, so ``{% regroup r by username as g %}`` over a single model raises ``TypeError``. The bridged
tag handler was handed that model as the mapping it crosses the engine as, iterated it, and so grouped
the model's KEY NAMES: through the LiveView entry the one group's grouper was ``None`` and the list
members were the identity keys (``id``, ``pk``, ``__str__``, ``__model__``).

The rule now in force: the tag refuses a source that is a serialized model map (the identity shape
every serializer stamps: a text ``__str__``, a text ``__model__`` and a ``pk``), the way the other
sinks that treat a map as a model do. A list, tuple or QuerySet of models is read element-wise, as
Django reads it, and an ordinary dict is a dict: both are unchanged, and pinned below against live
Django. A dict that only carries some of the identity keys stays a dict.

Engine path: the Rust engine through the LiveView state entry, ``DjustTemplateBackend`` and the raw
``render_template`` entry, with Django's own engine on real model instances as the oracle.
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
from python.tests._scratch_models_3888 import scratch_models  # noqa: F401

SENTINEL = "SENTINEL-first-name-3948"
DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*?)</div>", re.S)

#: the source is named ``r``; the grouper is a real field, so a list of models groups by it
REGROUP = "{% regroup r by username as g %}{% for grp in g %}[{{ grp.grouper }}]{% endfor %}"
RAISES = "RAISES"


def _lv(src: str, **state) -> str:
    """The real LiveView entry, with the values held as PUBLIC STATE set in ``mount``."""

    def mount(self, request, **kwargs):
        for name, value in state.items():
            setattr(self, name, value)

    view = type(
        "RegroupView",
        (LiveView,),
        {"exposure_policy": "legacy", "template": f"<div dj-root>{src}</div>", "mount": mount},
    )
    client = LiveViewTestClient(view)
    client.mount()
    match = DJ_ROOT.search(client.render())
    assert match is not None
    return html_lib.unescape(match.group(1))


def _liveview(src: str, ctx: dict) -> str:
    return _lv(src, **ctx)


def _backend(src: str, ctx: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return html_lib.unescape(str(backend.from_string(src).render(context=dict(ctx), request=None)))


def _raw(src: str, ctx: dict) -> str:
    return html_lib.unescape(_rust.render_template(src, dict(ctx)))


def _django(src: str, ctx: dict) -> str:
    return html_lib.unescape(DjangoTemplate(src).render(DjangoContext(dict(ctx))))


ENTRIES = {"liveview": _liveview, "backend": _backend, "raw": _raw}


def _observe(run, src: str, ctx: dict) -> str:
    """The bytes, or ``RAISES`` for any exception (the TYPE differs across engines, the fact does not)."""
    try:
        return run(src, ctx)
    except Exception:  # noqa: BLE001 - a measurement: whether the render refuses is what is compared
        return RAISES


@pytest.fixture
def world(db, scratch_models):  # noqa: F811
    """Two accounts: a proxy of ``User`` whose ``first_name`` is a recognisable sentinel."""

    class Account(User):
        class Meta:
            proxy = True
            app_label = "auth"

        def __str__(self):
            return f"Account:{self.username}"

    a1 = Account.objects.create(username="a1", first_name=SENTINEL)
    a2 = Account.objects.create(username="a2", first_name=SENTINEL)
    return {"Account": Account, "a1": a1, "a2": a2}


def _source(w, shape: str):
    Account = w["Account"]
    return {
        "model": lambda: w["a1"],
        "list": lambda: [w["a1"], w["a2"]],
        "tuple": lambda: (w["a1"], w["a2"]),
        "queryset": lambda: Account.objects.filter(username__in=("a1", "a2")).order_by("username"),
        "list-of-dicts": lambda: [{"username": "x"}, {"username": "x"}, {"username": "y"}],
    }[shape]()


class TestDjangoIsTheOracle:
    """The expectations below are Django's, measured on real instances, not transcribed."""

    def test_django_refuses_a_single_model(self, world) -> None:
        with pytest.raises(TypeError):
            _django(REGROUP, {"r": world["a1"]})

    @pytest.mark.parametrize("shape", ["list", "tuple", "queryset", "list-of-dicts"])
    def test_django_groups_a_container(self, world, shape) -> None:
        out = _django(REGROUP, {"r": _source(world, shape)})
        assert out in ("[a1][a2]", "[x][y]")


class TestASingleModelIsRefused:
    """A model is not iterable, so ``{% regroup %}`` over one is an error in every entry, with no output."""

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_the_tag_raises_as_django_does(self, world, entry) -> None:
        out = _observe(ENTRIES[entry], REGROUP, {"r": world["a1"]})
        assert out == RAISES, f"{entry}: {out[:300]!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize(
        "src",
        [
            "{% regroup r by username as g %}{{ g|length }}",
            "{% regroup r by first_name as g %}{% for grp in g %}{{ grp.grouper }}{% endfor %}",
            "{% with x=r %}{% regroup x by username as g %}{{ g|length }}{% endwith %}",
            "{% for x in rows %}{% regroup x by username as g %}{{ g|length }}{% endfor %}",
            "{% regroup d.owner by username as g %}{{ g|length }}",
            "{% regroup r by username as g %}{% for grp in g %}{% for k in grp.list %}<{{ k }}>{% endfor %}{% endfor %}",
        ],
        ids=[
            "length",
            "by-first_name",
            "with-alias",
            "loop-variable",
            "dict-value",
            "group-members",
        ],
    )
    def test_it_does_not_group_a_models_key_names(self, world, entry, src) -> None:
        ctx = {"r": world["a1"], "rows": [world["a1"]], "d": {"owner": world["a1"]}}
        assert _observe(_django, src, ctx) == RAISES
        out = _observe(ENTRIES[entry], src, ctx)
        assert out == RAISES, f"{entry}: {out[:300]!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_filtered_source_answers_as_django_does(self, world, entry) -> None:
        """``dictsort`` over a model answers ``''`` (it swallows the error), so the tag sees a string."""
        src = "{% regroup r|dictsort:'username' by username as g %}{{ g|length }}"
        ctx = {"r": world["a1"]}
        assert _observe(_django, src, ctx) == "0"
        assert _observe(ENTRIES[entry], src, ctx) == "0"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_filter_that_returns_the_model_is_refused(self, world, entry) -> None:
        src = "{% regroup r|default:'x' by username as g %}{{ g|length }}"
        ctx = {"r": world["a1"]}
        assert _observe(_django, src, ctx) == RAISES
        assert _observe(ENTRIES[entry], src, ctx) == RAISES

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_no_value_and_no_key_name_is_printed(self, world, entry) -> None:
        out = _observe(ENTRIES[entry], REGROUP, {"r": world["a1"]})
        for text in (SENTINEL, "first_name", "__str__", "__model__"):
            assert text not in out, f"{entry}: {text!r} in {out[:300]!r}"


class TestAContainerOfModelsIsUnchanged:
    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("shape", ["list", "tuple", "queryset", "list-of-dicts"])
    def test_it_agrees_with_django(self, world, entry, shape) -> None:
        ctx = {"r": _source(world, shape)}
        expected = _django(REGROUP, ctx)
        out = _observe(ENTRIES[entry], REGROUP, ctx)
        assert out == expected, f"{entry}/{shape}: {out[:300]!r} != {expected!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_container_prints_no_value(self, world, entry) -> None:
        src = (
            "{% regroup r by username as g %}"
            "{% for grp in g %}{{ grp.grouper }}:{% for o in grp.list %}{{ o.username }},{% endfor %};{% endfor %}"
        )
        out = _observe(ENTRIES[entry], src, {"r": _source(world, "list")})
        assert out == _django(src, {"r": _source(world, "list")})
        assert SENTINEL not in out

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_grouping_by_a_non_identity_field_still_works(self, world, entry) -> None:
        src = "{% regroup r by first_name as g %}{% for grp in g %}[{{ grp.grouper }}]{% endfor %}"
        ctx = {"r": _source(world, "list")}
        out = _observe(ENTRIES[entry], src, ctx)
        assert out == _django(src, ctx)


class TestAnOrdinaryDictIsUnchanged:
    """A dict is a dict: grouping it iterates its keys, as Django does. A dict that carries only some
    of the identity keys is not read as a model."""

    CASES = {
        "plain": {"a": 1, "b": 2},
        "text-str-key-only": {"__str__": "label", "k": 1},
        "str-and-model-no-pk": {"__str__": "label", "__model__": "Thing"},
        "str-and-pk-no-model": {"__str__": "label", "pk": 1},
        "empty": {},
    }

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("case", sorted(CASES))
    def test_it_agrees_with_django(self, entry, case) -> None:
        src = "{% regroup r by k as g %}[{{ g|length }}]"
        ctx = {"r": self.CASES[case]}
        expected = _observe(_django, src, ctx)
        out = _observe(ENTRIES[entry], src, ctx)
        assert out == expected, f"{entry}/{case}: {out[:300]!r} != {expected!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_list_of_dicts_with_identity_keys_is_grouped(self, entry) -> None:
        rows = [{"k": 1, "__str__": "a"}, {"k": 1, "__str__": "b"}, {"k": 2, "__str__": "c"}]
        src = "{% regroup r by k as g %}{% for grp in g %}[{{ grp.grouper }}:{{ grp.list|length }}]{% endfor %}"
        expected = _django(src, {"r": rows})
        assert expected == "[1:2][2:1]"
        assert _observe(ENTRIES[entry], src, {"r": rows}) == expected


class TestTheIdentityShapeReader:
    """``is_serialized_model_map`` reads what ``model_identity`` writes, and nothing looser."""

    def test_it_accepts_what_the_producer_writes(self, world) -> None:
        from djust.serialization import is_serialized_model_map, model_identity

        assert is_serialized_model_map(model_identity(world["a1"]))

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param({"__str__": "x", "__model__": "M"}, id="no-pk"),
            pytest.param({"__str__": "x", "pk": 1}, id="no-model"),
            pytest.param({"__model__": "M", "pk": 1}, id="no-str"),
            pytest.param({"__str__": None, "__model__": "M", "pk": 1}, id="str-not-text"),
            pytest.param({"__str__": "x", "__model__": 3, "pk": 1}, id="model-not-text"),
            pytest.param({}, id="empty"),
            pytest.param([{"__str__": "x", "__model__": "M", "pk": 1}], id="list"),
            pytest.param("__str__", id="text"),
            pytest.param(None, id="none"),
        ],
    )
    def test_it_refuses_anything_else(self, value) -> None:
        from djust.serialization import is_serialized_model_map

        assert not is_serialized_model_map(value)


class _PickleableSource:
    """A stand-in for a ``FilterExpression``: a module-level class, so pickle can find it."""

    var = "rows"


class TestTheCompiledTemplateStillCopies:
    """The wrapped source must survive ``copy.deepcopy`` and ``pickle``, as the bare node did."""

    SRC = "{% regroup rows by username as g %}{% for grp in g %}[{{ grp.grouper }}]{% endfor %}"

    def test_the_node_deep_copies_and_still_refuses_a_model(self, world) -> None:
        import copy

        from django.template import engines

        template = engines["django"].from_string(self.SRC)
        again = copy.deepcopy(template)
        assert again.render({"rows": [world["a1"], world["a2"]]}) == "[a1][a2]"

    def test_the_wrapper_round_trips_through_pickle(self) -> None:
        import pickle

        from djust.template_tags.regroup import _ModelRefusingSource

        again = pickle.loads(pickle.dumps(_ModelRefusingSource(_PickleableSource())))
        assert again.var == "rows"
