"""One producer for a model's identity map, so ``"__model__"`` means something (#2322).

The finding
-----------
Six sites stamped ``"__str__": str(...)`` onto a map standing in for a Django
model. **Two** of them also stamped ``"__model__"``; four did not — and which
one fires depends on prefetch depth, on whether the template referenced a
field, and on whether serialization happened to raise. None of that is visible
from the consuming side, so anything keying on ``"__model__"`` to answer "is
this a serialized model?" was correct in development and wrong in production
the moment a relation crossed ``max_depth``.

What every option in the issue assumed
--------------------------------------
The issue offered three: document the split, stamp the key at all six, or
delete the key. All three take the six hand-rolled dict literals as a given and
argue about their CONTENTS. But look at what those four bare sites are: each is
an independent attempt to write "the minimal identity representation of a
model", and ``_IDENTITY_KEYS`` — the frozenset the field filter already treats
as always-allowed — says that representation is
``{"pk", "id", "__str__", "__model__"}``. Six copies of one concept, differing;
that is parallel-path drift (#1646), and the marker gap is its symptom rather
than the disease. The two producers even disagreed about key ORDER (``id, pk``
in ``_serialize_model_safely``, ``pk, id`` in ``jit.py``), which survives to the
wire, and no option in the list would have noticed.

So: ONE producer, :func:`djust.serialization.model_identity`, derived from
``_IDENTITY_KEYS`` and called by all six sites. The marker becomes universal as
a CONSEQUENCE of there being one place to answer the question, not as six
coordinated edits that can drift apart again next quarter. Deleting the key
instead would have been a wire removal of information ``__str__`` cannot carry
(the class name), for a key ``docs/SECURE_DEFAULTS.md`` documents and
out-of-repo consumers may read — and it would have left the four hand-rolled
literals in place to drift on the NEXT key.

What is pinned here
-------------------
:class:`TestEverySiteIsExercised` drives all six producers and asserts the shape
each ACTUALLY emits — the behavioural half the #2294 pin explicitly did not
have ("deliberately a source grep"). A source grep cannot tell you a site is
unreachable, mis-ordered, or that its dict is built somewhere the grep's
6-line window does not see; running it can.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from django.db import models

from djust import serialization as ser
from djust.serialization import (
    _IDENTITY_KEYS,
    DjangoJSONEncoder,
    model_identity,
    normalize_django_value,
)

SER_PY = Path(ser.__file__)
JIT_PY = SER_PY.parent / "mixins" / "jit.py"
RENDERING_PY = SER_PY.parent / "template" / "rendering.py"

#: The order the wire carries, which is the order `_serialize_model_safely`
#: has always used. Pinned as a LIST, not a set: a dict's key order survives
#: `json.dumps` and msgpack, so two producers disagreeing about it is a
#: wire-shape difference even when the key sets match.
IDENTITY_ORDER = ["id", "pk", "__str__", "__model__"]


def _make_user(pk: int = 42, username: str = "alice"):
    from django.contrib.auth.models import User

    user = User(username=username, email=f"{username}@example.com")
    user.pk = pk
    user.id = pk
    return user


_Parent = type(
    "I2322Parent",
    (models.Model,),
    {"__module__": __name__, "name": models.CharField(max_length=50, default="")},
)
_Child = type(
    "I2322Child",
    (models.Model,),
    {
        "__module__": __name__,
        "name": models.CharField(max_length=50, default=""),
        "parent": models.ForeignKey(_Parent, on_delete=models.CASCADE, null=True),
    },
)


def _child_with_parent():
    """A child whose FK is already populated, so no DB query is needed."""
    parent = _Parent(name="p")
    parent.pk = 7
    parent.id = 7
    child = _Child(name="c")
    child.pk = 9
    child.id = 9
    # Assigning the instance fills the FK cache; `getattr(obj, 'parent')`
    # returns it without touching the database.
    child.parent = parent
    return child, parent


class TestTheHelperIsTheDefinition:
    """``model_identity`` is derived from ``_IDENTITY_KEYS``, not parallel to it."""

    def test_the_keys_are_exactly_the_identity_set(self) -> None:
        user = _make_user()
        assert set(model_identity(user)) == set(_IDENTITY_KEYS)

    def test_the_order_is_the_wire_order(self) -> None:
        assert list(model_identity(_make_user())) == IDENTITY_ORDER

    def test_the_values_are_the_model_s_own(self) -> None:
        user = _make_user(pk=3, username="bob")
        got = model_identity(user)
        assert got["id"] == 3 and got["pk"] == 3
        assert got["__str__"] == str(user) == "bob"
        assert got["__model__"] == "User"

    def test_id_and_pk_stay_NATIVE_types_for_template_comparisons(self) -> None:
        # `{% if item.id == state_var %}` compares an int to an int; stringifying
        # the pk here would silently break every such comparison.
        got = model_identity(_make_user(pk=42))
        assert got["id"] == 42 and not isinstance(got["id"], str)


class TestEverySiteIsExercised:
    """All six producers, driven and asserted — the behavioural half.

    The #2294 pin greps the source and says so ("deliberately a source grep").
    That catches a site that forgets the key; it cannot catch a site whose dict
    is assembled where the grep's window does not reach, nor tell you a site is
    dead. Each case below RUNS its producer.
    """

    def test_1_serialize_model_safely_the_main_path(self) -> None:
        got = DjangoJSONEncoder()._serialize_model_safely(_make_user())
        for key in IDENTITY_ORDER:
            assert key in got, f"the main model path dropped {key}: {sorted(got)}"
        assert got["__model__"] == "User"
        # It is a SUPERSET here — the identity keys plus the model's fields.
        assert set(got) > set(IDENTITY_ORDER)

    def test_2_a_related_FK_at_the_depth_limit(self) -> None:
        child, parent = _child_with_parent()
        encoder = DjangoJSONEncoder()
        original = DjangoJSONEncoder._depth
        DjangoJSONEncoder._depth = encoder._get_max_depth()  # at the limit
        try:
            got = encoder._serialize_model_safely(child)
        finally:
            DjangoJSONEncoder._depth = original
        assert isinstance(got.get("parent"), dict), got
        assert list(got["parent"]) == IDENTITY_ORDER, got["parent"]
        assert got["parent"]["__model__"] == "I2322Parent"
        assert got["parent"]["__str__"] == str(parent)

    def test_3_normalize_django_value_at_max_depth(self) -> None:
        user = _make_user()
        got = normalize_django_value(user, _depth=DjangoJSONEncoder._get_max_depth())
        assert list(got) == IDENTITY_ORDER, got
        assert got["__model__"] == "User"

    def test_4_the_jit_identity_only_subset(self) -> None:
        from djust.mixins.jit import JITMixin

        mixin = JITMixin()
        # A template that never references a FIELD of `u` — the whole-object
        # case (finding #19), which emits the identity subset and nothing else.
        got = mixin._jit_serialize_model(_make_user(), "{{ u }}", "u")
        assert list(got) == IDENTITY_ORDER, got
        assert got["__model__"] == "User"

    def test_5_the_rendering_fallback_when_jit_is_unavailable(self, monkeypatch) -> None:
        from djust.template import rendering

        monkeypatch.setattr(rendering, "JIT_AVAILABLE", False)
        got = rendering.DjustTemplate("{{ u }}", backend=None)._jit_serialize_model(
            _make_user(), "u"
        )
        assert list(got) == IDENTITY_ORDER, got
        assert got["__model__"] == "User"

    def test_6_the_rendering_fallback_when_serialization_raises(self, monkeypatch) -> None:
        from djust.template import rendering

        def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("serialization blew up")

        monkeypatch.setattr(rendering, "normalize_django_value", _boom)
        got = rendering.DjustTemplate("{{ u }}", backend=None)._jit_serialize_model(
            _make_user(), "u"
        )
        assert list(got) == IDENTITY_ORDER, got
        assert got["__model__"] == "User"

    def test_all_six_agree_on_the_identity_sub_map(self, monkeypatch) -> None:
        """The property the split broke: the six answers must be one answer.

        Compared as ordered key/value pairs restricted to the identity keys, so
        this fails on a dropped key, an added one, a renamed one, a changed
        value AND a reordering — every way the six could disagree.
        """
        from djust.mixins.jit import JITMixin
        from djust.template import rendering

        user = _make_user()
        expected = [(k, model_identity(user)[k]) for k in IDENTITY_ORDER]

        def subset(d: dict) -> list[tuple[str, Any]]:
            return [(k, v) for k, v in d.items() if k in _IDENTITY_KEYS]

        answers = {
            "serialization.py:_serialize_model_safely": subset(
                DjangoJSONEncoder()._serialize_model_safely(user)
            ),
            "serialization.py:normalize_django_value@max_depth": subset(
                normalize_django_value(user, _depth=DjangoJSONEncoder._get_max_depth())
            ),
            "jit.py:identity-only": subset(JITMixin()._jit_serialize_model(user, "{{ u }}", "u")),
        }
        monkeypatch.setattr(rendering, "JIT_AVAILABLE", False)
        answers["rendering.py:jit-unavailable"] = subset(
            rendering.DjustTemplate("{{ u }}", backend=None)._jit_serialize_model(user, "u")
        )
        monkeypatch.undo()

        def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("boom")

        monkeypatch.setattr(rendering, "normalize_django_value", _boom)
        answers["rendering.py:raised"] = subset(
            rendering.DjustTemplate("{{ u }}", backend=None)._jit_serialize_model(user, "u")
        )
        monkeypatch.undo()

        child, parent = _child_with_parent()
        encoder = DjangoJSONEncoder()
        original = DjangoJSONEncoder._depth
        DjangoJSONEncoder._depth = encoder._get_max_depth()
        try:
            related = encoder._serialize_model_safely(child)["parent"]
        finally:
            DjangoJSONEncoder._depth = original
        # The FK shorthand describes the PARENT, so its values differ; the KEYS
        # and their order are the shared contract.
        assert [k for k, _ in subset(related)] == IDENTITY_ORDER, related

        for where, got in answers.items():
            assert got == expected, f"{where} disagrees: {got} != {expected}"


class TestAddingTheMarkerIsNotMorePermissive:
    """The permissiveness half, and the one the two-build differential cannot do.

    ``scripts/filter-parity-differential.py`` renders literal dicts through both
    engines; nothing in it calls djust's serializer, so a pure-Python
    serialization change moves **zero** cells there (measured: 0 of 33,336
    djust outputs changed, 0 regressions, 0 introduced leaks — the tool's own
    "identical counts" guard fires, correctly, because the two builds differ
    only in code the corpus does not reach).

    The question that IS answerable: this PR adds a key to a payload that
    reaches the browser, so does the payload's rendering gain any live fragment
    it did not have? Swept over every filter in Django's live registry, with the
    key present and absent, against hostile values in BOTH ``__str__`` (app
    data — a model's ``str()`` can hold whatever a user typed) and ``__model__``
    (``obj.__class__.__name__`` — a Python identifier in practice, but
    ``type("<img src=x>", (), {})`` is legal, so it is swept hostile too rather
    than argued inert).
    """

    PAYLOAD = "<img src=x onerror=alert(1)>"
    FRAGMENTS = ("<img", "onerror=", "<script")

    @staticmethod
    def _filters() -> list[str]:
        from django.template.defaultfilters import register

        return sorted(register.filters)

    @staticmethod
    def _render(src: str, value: dict) -> str:
        from djust import _rust

        try:
            return _rust.render_template(src, {"p": value})
        except Exception as exc:  # noqa: BLE001 — a raise is a comparable outcome
            return f"<<EXC {type(exc).__name__}>>"

    def test_the_registry_is_not_empty(self) -> None:
        # A sweep over an empty list passes vacuously; #1859.
        assert len(self._filters()) >= 50, self._filters()

    def test_adding_the_marker_introduces_no_live_fragment(self) -> None:
        without = {"id": 7, "pk": 7, "__str__": self.PAYLOAD}
        with_marker = {**without, "__model__": "Doc"}
        bad = []
        for name in self._filters():
            for src in (f"{{{{ p|{name} }}}}", f"{{{{ p|{name}|safe }}}}", "{{ p }}"):
                a = self._render(src, without)
                b = self._render(src, with_marker)
                gained = {f for f in self.FRAGMENTS if f in b} - {
                    f for f in self.FRAGMENTS if f in a
                }
                if gained:
                    bad.append((src, sorted(gained), a, b))
        assert not bad, f"adding `__model__` made {len(bad)} cells live: {bad[:3]}"

    def test_a_hostile_class_name_is_escaped_like_any_other_value(self) -> None:
        """``type()`` accepts any string as a class name, so assert rather than argue."""
        hostile = type(self.PAYLOAD, (), {"pk": 1, "__str__": lambda self: "ok"})()
        got = model_identity(hostile)
        assert got["__model__"] == self.PAYLOAD
        for src in ("{{ p }}", "{{ p.__model__ }}", "{{ p|pprint }}", "{{ p|escape }}"):
            out = self._render(src, got)
            assert "<img" not in out, f"{src} emitted the class name live: {out!r}"
            assert "<script" not in out, out

    def test_the_marker_is_not_a_field_and_cannot_be_denylisted_away(self) -> None:
        """``__model__`` is identity, not data — which is why adding it does not
        widen what the field denylist controls.

        A Django field name may not contain ``__``, so no model can define a
        field that collides with it; the key can only ever come from
        ``model_identity``.
        """
        from django.core.exceptions import FieldDoesNotExist
        from django.contrib.auth.models import User

        for name in ("__model__", "__str__"):
            try:
                User._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            raise AssertionError(f"a real field named {name} exists — the key can collide")

    def test_the_rendered_page_is_unchanged_for_the_shapes_that_gained_the_key(
        self,
    ) -> None:
        """``{{ obj }}`` and ``{{ obj|length }}`` answer the same before and after.

        The engine's model predicate is ``object_str()``, which keys on
        ``"__str__"`` — so a map that gained ``__model__`` renders identically.
        If this ever fails, the marker has become load-bearing in the renderer
        and the wire change is no longer inert.
        """
        from djust import _rust

        without = {"id": 7, "pk": 7, "__str__": "bob"}
        with_marker = {**without, "__model__": "Doc"}
        for src in ("{{ p }}", "{{ p|length }}", "{{ p.id }}", "{{ p.__str__ }}"):
            assert _rust.render_template(src, {"p": without}) == _rust.render_template(
                src, {"p": with_marker}
            ), src


class TestOneProducerNotSix:
    """The structural cure, pinned mechanically.

    Replaces the #2294 count pin, which asserted a 2-with / 4-without split.
    That pin cannot survive its own fix: once the split is 6/0 a count can only
    ever read 6, and a SEVENTH hand-rolled literal that happens to carry
    ``__model__`` would pass it while re-opening the drift on the next key
    (#1859 — a pin that cannot go red is decorative). What is load-bearing is
    that exactly one place BUILDS the map.
    """

    FILES = ("serialization.py", "jit.py", "rendering.py")

    def _stamp_sites(self) -> list[str]:
        """Every ``"__str__": str(...)`` dict-literal stamp, as file:line."""
        out = []
        for path in (SER_PY, JIT_PY, RENDERING_PY):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r'"__str__":\s*str\(', line):
                    out.append(f"{path.name}:{i}")
        return sorted(out)

    def test_exactly_one_site_builds_the_identity_map(self) -> None:
        sites = self._stamp_sites()
        assert len(sites) == 1, (
            f"{len(sites)} sites still hand-roll a model identity map: {sites}. "
            f"There must be exactly ONE — `model_identity` — and every other "
            f"producer must call it. This is the #1646 cure #2322 chose over "
            f"stamping the key at six sites that can drift again."
        )
        assert sites[0].startswith("serialization.py:"), sites

    def test_that_one_site_is_inside_model_identity(self) -> None:
        src = SER_PY.read_text(encoding="utf-8")
        body = src.split("def model_identity(", 1)
        assert len(body) == 2, "`model_identity` is gone — update this pin"
        body = body[1].split("\ndef ", 1)[0]
        assert re.search(r'"__str__":\s*str\(', body), (
            "the one stamp is no longer inside `model_identity`"
        )

    def test_every_other_producer_calls_the_helper(self) -> None:
        callers = {
            path.name: len(re.findall(r"\bmodel_identity\(", path.read_text(encoding="utf-8")))
            for path in (SER_PY, JIT_PY, RENDERING_PY)
        }
        # serialization.py: the def, the main path, the FK shorthand, the
        # max-depth shorthand. jit.py: the identity-only subset.
        # rendering.py: the two fallbacks.
        assert callers == {
            "serialization.py": 4,
            "jit.py": 1,
            "rendering.py": 2,
        }, (
            f"the `model_identity` call sites moved: {callers}. Six producers "
            f"call it (plus its own `def`); a producer that stopped calling it "
            f"is a hand-rolled literal coming back."
        )

    def test_the_helper_is_derived_from_the_identity_key_set(self) -> None:
        """Not a parallel literal: adding a key to ``_IDENTITY_KEYS`` alone, or
        to the helper alone, is a drift this catches."""
        assert set(model_identity(_make_user())) == set(_IDENTITY_KEYS)
