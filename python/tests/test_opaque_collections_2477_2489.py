"""A Python collection crosses as its ITEMS, not as its repr (#2477, #2489).

The divergence
--------------
``impl FromPyObject for Value``'s fallback block ends in::

    Ok(Value::String(ob.str()?.to_string()))

so any object no variant modelled arrived at the renderer as its **``str()``**,
and every consumer that iterates, sizes, subscripts or slices then read the
**repr** one character at a time::

    {{ p|length }}       p = {}.keys()      django 0    djust 13
    {% for x in p %}     p = {"k": 1}.keys() django [k]  djust one cell per repr char
    {{ p|escapeseq }}    p = FalsyIterable() django ['<img …>']  djust ['F','a','l',…]

#2466 closed the FALSY-and-empty half by carrying `bool(o)` on a
``Value::Encoded``; #2477/#2489 close the rest by carrying the object's
``len(o)`` and its enumerated ITEMS on the same struct.

Two paths, and they differed
----------------------------
``normalize_django_value`` runs BEFORE the conversion on the LiveView path and
had its own flattening: a ``set`` became a sorted **list** (subscriptable,
where a set is not) and everything else took ``str()``. So the two djust paths
answered differently for the same value, and *both* fixes are needed for either
to be visible on a page. Every sweep below therefore runs THREE columns —
Django, the raw ``_rust.render_template`` entry point, and the LiveView path
through the normalizer — and asserts all three agree.

Non-vacuity
-----------
The class is enumerated with a DECISION per member (``EXPECTED`` below), and
the decisions are asserted in both directions: a CARRIED member that starts
being stringified fails, and a DECLINED one that starts being carried fails
too. The three declines each keep the string path they already had, so a
decline is an unfixed cell rather than a regression, and each is pinned in the
DIVERGING direction so it cannot be quietly widened.
"""

from __future__ import annotations

import collections
import itertools
import json
import re
import types
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"
LIVE_RS = REPO / "crates" / "djust_live" / "src" / "lib.rs"
SERIALIZATION_PY = REPO / "python" / "djust" / "serialization.py"

#: A payload in every value that can carry one, so the sweep reads the ESCAPING
#: as well as the shape — an items list that reached the page raw would be a
#: leak, not a spelling difference.
PAYLOAD = "<img src=x onerror=alert(1)>"


def instance(name: str, **namespace: object) -> object:
    """A fresh instance of a fresh class.

    ``__module__`` is set explicitly and ``__str__``/``__repr__`` are pinned:
    the default ``object.__repr__`` carries a MEMORY ADDRESS, so a fixture
    without them renders differently on every run.
    """
    namespace.setdefault("__module__", "djust_opaque_fixtures")
    namespace.setdefault("__repr__", lambda self: f"{name}()")
    namespace.setdefault("__str__", lambda self: f"{name}()")
    return type(name, (), namespace)()


def _one_shot():
    """A generator function — ``iter(g) is g``, so reading it consumes it."""

    def gen():
        yield PAYLOAD

    return gen()


class _Members:
    """Every member of the class, built FRESH on each access.

    A one-shot iterator is spent by a single render, so a shared instance would
    make the second template in a sweep measure an exhausted object rather than
    the shape under test.
    """

    @staticmethod
    def build() -> dict:
        values = {
            # --- CARRIED: re-iterable collections -------------------------
            "set-empty": set(),
            "set-plain": {PAYLOAD},
            "frozenset-empty": frozenset(),
            "frozenset-plain": frozenset({PAYLOAD}),
            "dv-keys-empty": {}.keys(),
            "dv-keys-plain": {PAYLOAD: 1}.keys(),
            "dv-values-plain": {"k": PAYLOAD}.values(),
            "dv-items-plain": {"k": "v"}.items(),
            "odict-keys": collections.OrderedDict({PAYLOAD: 1}).keys(),
            # A MAPPING that is not a `dict`: re-iterable over its keys, and
            # subscriptable BY KEY — so Django's `first` (`value[0]`) raises a
            # KeyError and djust refuses too. Both refuse; the classes differ.
            "mappingproxy": types.MappingProxyType({"a": "1"}),
            "o-sized-iter": instance(
                "SizedIterable",
                __len__=lambda self: 2,
                __iter__=lambda self: iter(("a", PAYLOAD)),
            ),
            # Falsy WITH `__iter__` and no `__len__` — one of the two shapes
            # #2466 explicitly DECLINED, and #2489's sharpest row:
            # `{{ p|length }}` is 0 on Django and was 15 here.
            "o-falsy-iter": instance(
                "FalsyIterable",
                __bool__=lambda self: False,
                __iter__=lambda self: iter((PAYLOAD,)),
            ),
            # Falsy with a NON-ZERO `__len__` — #2466's other decline.
            "o-falsy-len2": instance(
                "FalsyLen2",
                __bool__=lambda self: False,
                __len__=lambda self: 2,
                __iter__=lambda self: iter(("a", PAYLOAD)),
            ),
            # Iterable AND attribute-carrying. An object with `__iter__` is not
            # a mapping of its attributes, so this arm claims it — and
            # `Encoded::attrs` (#2481) is what keeps `{{ p.tag }}` resolving.
            "o-iter-attrs": instance(
                "IterWithAttrs",
                __iter__=lambda self: iter(("x",)),
            ),
            # --- CARRIED: not iterable, nothing to enumerate ---------------
            "o-len-zero": instance("LenZero", __len__=lambda self: 0),
            "o-bool-false": instance("BoolFalse", __bool__=lambda self: False),
            "complex-zero": complex(0),
            # Truthy, non-iterable, attribute-LESS. Beyond either issue's
            # measured table and the same defect: `{{ p|length }}` was 6, the
            # characters of "(1+0j)", where Django says 0.
            "complex-one": complex(1),
        }
        values["o-iter-attrs"].tag = PAYLOAD
        return values


#: Which arm of the conversion claims each value, asserted in both directions.
#:
#: ``carried``  — ``opaque_value`` builds a ``Value::Encoded`` for it, so the
#:                normalizer must hand the object over unflattened.
#: ``declined`` — this arm refuses it and it keeps the terminal
#:                ``Value::String(str(o))``. An unfixed cell, pinned so it
#:                cannot be widened without a decision.
#: ``earlier``  — a DIFFERENT, earlier arm claims it (PyO3's sequence or
#:                mapping extraction). Nothing here touches those, and the
#:                first version of the normalizer's gate got exactly this
#:                group wrong.
CARRIED = frozenset(_Members.build())

DECLINED: dict[str, str] = {
    # `iter(o) is o`. Enumerating it consumes the caller's object.
    "one-shot-generator": "a one-shot iterator",
    "one-shot-falsy": "a one-shot iterator that is also Python-falsy",
    # Re-iterable, no `__len__`, unbounded. Declined at `OPAQUE_ITEM_CAP`
    # rather than truncated: a short collection is a silently wrong answer.
    "unbounded-reiterable": "an unsized iterable past OPAQUE_ITEM_CAP",
    # The `__dict__` bulk-dump arm's cell. Retiring that arm is a separate,
    # much larger decision.
    "truthy-attrs": "a TRUTHY non-iterable object with public attributes",
}

EARLIER: dict[str, str] = {
    "bytes": "PyO3's sequence extraction — a Value::List of its ints",
    "deque": "PyO3's sequence extraction",
    "range": "PyO3's sequence extraction",
    "getitem-seq": "PyO3's sequence extraction (an integer __getitem__)",
    "counter": "PyO3's mapping extraction — a dict subclass",
    "plain-dict": "PyO3's mapping extraction",
    "plain-list": "PyO3's sequence extraction",
}


def declined_values() -> dict:
    def unbounded_iter(self):
        return itertools.count()

    # A PUBLIC attribute, and it is load-bearing: `opaque_value` declines a
    # truthy non-iterable object only when `public_dict_attrs` finds one, so a
    # fixture with an empty `__dict__` is CLAIMED and would test the opposite
    # of what its row says.
    truthy_attrs = instance("TruthyAttrs")
    truthy_attrs.name = "ok"
    return {
        "truthy-attrs": truthy_attrs,
        "one-shot-generator": _one_shot(),
        "one-shot-falsy": instance(
            "FalsyOneShot",
            __bool__=lambda self: False,
            __iter__=lambda self: self,
            __next__=lambda self: (_ for _ in ()).throw(StopIteration),
        ),
        "unbounded-reiterable": instance("Unbounded", __iter__=unbounded_iter),
    }


def earlier_values() -> dict:
    return {
        "bytes": b"ab",
        "deque": collections.deque(["a", PAYLOAD]),
        "range": range(3),
        "getitem-seq": instance(
            "SeqLike",
            __len__=lambda self: 2,
            __getitem__=lambda self, i: ("a", PAYLOAD)[i],
        ),
        "counter": collections.Counter({"a": 1}),
        "plain-dict": {"a": PAYLOAD},
        "plain-list": ["a", PAYLOAD],
    }


#: The consumers. Chosen so every question the carrier answers is asked by at
#: least one: truthiness, `__len__`, `__iter__`, subscripting, slicing, the
#: string methods, and the two display spellings.
TEMPLATES = (
    "{{ p }}",
    "{% if p %}T{% else %}F{% endif %}",
    "{{ p|length }}",
    "{{ p|first }}",
    "{{ p|last }}",
    "{{ p|join:',' }}",
    "{{ p|escapeseq }}",
    "{{ p|safeseq }}",
    "{{ p|unordered_list }}",
    "{{ p|slice:':2' }}",
    "{{ p|phone2numeric }}",
    "{{ p|pprint }}",
    "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}",
    "{{ p|default:'D' }}",
    "{{ p|yesno }}",
    "{{ p|make_list }}",
)

#: Cells where BOTH engines refuse and only the exception CLASS differs.
#: Recorded rather than allowed, and exact in both directions — a refusal is
#: never more permissive than Django, which is the property that matters, but a
#: cell that starts AGREEING must lose its row rather than sit here as cover.
REFUSAL_CLASS_ONLY = {
    # `{% for %}` refuses through the renderer's own arm, which raises a
    # RuntimeError naming CPython's message rather than re-spelling the class.
    ("complex-zero", "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}"),
    ("complex-one", "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}"),
    ("o-bool-false", "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}"),
    # A `mappingproxy[0]` is a KEY lookup, so Django raises KeyError where the
    # carrier answers `'mappingproxy' object is not subscriptable`. Both refuse.
    ("mappingproxy", "{{ p|first }}"),
    ("mappingproxy", "{{ p|last }}"),
}


def outcome(source: str, value, engine: str) -> str:
    """One cell's answer: the rendered text, or the exception CLASS.

    The class and not the message, for the reason
    ``test_sequence_op_chokepoint_2451`` records: djust wraps every Django
    exception class in one ``RuntimeError`` and names the modelled class in the
    message, so comparing the texts would mark every agreeing refusal as a
    disagreement.
    """
    try:
        if engine == "django":
            return DjangoTemplate(source).render(DjangoContext({"p": value}))
        if engine == "raw":
            return _rust.render_template(source, {"p": value})
        return _rust.render_template(source, normalize_django_value({"p": value}))
    except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
        found = re.search(r"raises (\w+Error)", str(exc))
        return f"<<{found.group(1)}>>" if found else f"<<{type(exc).__name__}>>"


def refuses(answer: str) -> bool:
    return answer.startswith("<<")


class TestBothPathsAnswerDjango:
    """The reproducer, as a sweep. Django is CALLED, never transcribed."""

    def test_every_carried_member_agrees_with_django_on_both_paths(self) -> None:
        offenders = []
        for source in TEMPLATES:
            for key in _Members.build():
                dj = outcome(source, _Members.build()[key], "django")
                raw = outcome(source, _Members.build()[key], "raw")
                live = outcome(source, _Members.build()[key], "live")
                if (key, source) in REFUSAL_CLASS_ONLY:
                    continue
                if not (dj == raw == live):
                    offenders.append((key, source, dj, raw, live))
        assert not offenders, f"{len(offenders)} cells disagree:\n" + "\n".join(
            f"  {k} {s}\n    django={d!r}\n    raw   ={r!r}\n    live  ={li!r}"
            for k, s, d, r, li in offenders[:12]
        )

    def test_the_recorded_refusal_class_cells_are_exact_in_both_directions(self) -> None:
        """A recorded cell that starts agreeing must lose its row (#1859)."""
        for key, source in sorted(REFUSAL_CLASS_ONLY):
            dj = outcome(source, _Members.build()[key], "django")
            raw = outcome(source, _Members.build()[key], "raw")
            live = outcome(source, _Members.build()[key], "live")
            assert refuses(dj) and refuses(raw) and refuses(live), (
                f"{key} {source} is recorded as refuse-vs-refuse but one side "
                f"RENDERS: django={dj!r} raw={raw!r} live={live!r}"
            )
            assert dj != raw, (
                f"{key} {source} now answers the same class as Django — delete "
                f"its REFUSAL_CLASS_ONLY row"
            )

    def test_no_cell_renders_where_django_refuses(self) -> None:
        """The permissiveness half, stated on its own.

        The sweep above would also pass if djust started REFUSING where Django
        renders — stricter, not more permissive, but still wrong. This asserts
        the direction that is a security-adjacent property in its own right,
        and its sibling below asserts the other.
        """
        for source in TEMPLATES:
            for key in _Members.build():
                dj = outcome(source, _Members.build()[key], "django")
                if not refuses(dj):
                    continue
                for engine in ("raw", "live"):
                    got = outcome(source, _Members.build()[key], engine)
                    assert refuses(got), (
                        f"{key} {source} RENDERS {got!r} on the {engine} path "
                        f"where Django refuses with {dj}"
                    )

    def test_no_cell_refuses_where_django_renders(self) -> None:
        for source in TEMPLATES:
            for key in _Members.build():
                dj = outcome(source, _Members.build()[key], "django")
                if refuses(dj):
                    continue
                for engine in ("raw", "live"):
                    got = outcome(source, _Members.build()[key], engine)
                    assert not refuses(got), (
                        f"{key} {source} REFUSES on the {engine} path where Django renders {dj!r}"
                    )

    def test_the_sweep_is_not_vacuous(self) -> None:
        """It has to be able to fail: the cells must not all be trivially equal.

        Nineteen members and sixteen consumers is 304 cells, and the class is
        only interesting because the consumers DISAGREE with each other about
        the same value. Asserted directly: the sweep contains cells that render
        and cells that refuse, on Django, for every member.
        """
        members = _Members.build()
        assert len(members) == 18, "the member list moved — update the count"
        assert len(TEMPLATES) == 16
        rendering = refusing = 0
        for source in TEMPLATES:
            for key in members:
                if refuses(outcome(source, _Members.build()[key], "django")):
                    refusing += 1
                else:
                    rendering += 1
        assert rendering > 100, rendering
        assert refusing > 20, refusing


class TestLengthAndIterationAreTwoQuestions:
    """`len` and `items` are carried separately because Django asks both.

    A single ``sized_empty`` bit could not answer them: Django's ``length``
    filter reads ``__len__`` under ``except TypeError: return 0`` while its
    iterating filters are comprehensions that call ``iter()``. A falsy
    ``__iter__`` class with no ``__len__`` therefore answers **0** and **one
    item** at the same time — which is the case that forced the widening.
    """

    def test_a_falsy_iterable_without_a_len_answers_zero_and_one_item(self) -> None:
        for engine in ("raw", "live"):
            assert outcome("{{ p|length }}", _Members.build()["o-falsy-iter"], engine) == "0"
            assert (
                outcome(
                    "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}",
                    _Members.build()["o-falsy-iter"],
                    engine,
                )
                == "[&lt;img src=x onerror=alert(1)&gt;]"
            )
        # ...and Django says exactly that, called rather than transcribed.
        assert outcome("{{ p|length }}", _Members.build()["o-falsy-iter"], "django") == "0"

    def test_a_zero_len_class_with_no_iter_renders_empty_and_refuses_safeseq(self) -> None:
        """The mirror case, and the one the `for`-arm and `iter_values` split over."""
        value = _Members.build()["o-len-zero"]
        for engine in ("django", "raw", "live"):
            assert (
                outcome(
                    "{% for x in p %}[{{ x }}]{% empty %}EMPTY{% endfor %}",
                    _Members.build()["o-len-zero"],
                    engine,
                )
                == "EMPTY"
            )
            assert refuses(outcome("{{ p|safeseq }}", _Members.build()["o-len-zero"], engine))
        assert outcome("{{ p|length }}", value, "raw") == "0"


class TestTheItemsAreEscaped:
    """A carried item reaches the page ESCAPED.

    The items come from an arbitrary Python object through
    ``extract::<Value>()`` and carry no safety mark, so the `for`-arm grants
    none. Asserted rather than argued, because the whole point of the fix is
    that these strings now reach the page at all.
    """

    @pytest.mark.parametrize(
        "key",
        ["set-plain", "dv-keys-plain", "o-falsy-iter", "o-sized-iter", "o-iter-attrs"],
    )
    def test_a_payload_item_is_escaped_on_both_paths(self, key: str) -> None:
        for engine in ("raw", "live"):
            for source in (
                "{% for x in p %}{{ x }}{% endfor %}",
                "{{ p|join:'' }}",
                "{{ p|unordered_list }}",
            ):
                got = outcome(source, _Members.build()[key], engine)
                assert "<img" not in got, f"{key} {source} on {engine} emitted a live tag: {got!r}"

    def test_an_attribute_on_a_carried_collection_still_resolves_and_escapes(self) -> None:
        """#2481's `Encoded::attrs`, which is what lets this arm claim the object."""
        for engine in ("django", "raw", "live"):
            got = outcome("{{ p.tag }}", _Members.build()["o-iter-attrs"], engine)
            assert got == "&lt;img src=x onerror=alert(1)&gt;", (engine, got)


class TestTheClassIsEnumeratedWithADecisionEach:
    """Every member has a decision, and both directions are asserted.

    ``_rust.crosses_as_encoded`` is the conversion's own answer, so this is the
    behavioural pin: a CARRIED member that stops being carried fails, and a
    DECLINED or EARLIER one that starts being carried fails.
    """

    def test_every_carried_member_crosses_as_an_encoded(self) -> None:
        for key, value in _Members.build().items():
            assert _rust.crosses_as_encoded(value), (
                f"{key} is listed as CARRIED but no longer crosses as a "
                f"Value::Encoded — it fell back to str() or to an earlier arm"
            )
        assert set(_Members.build()) == CARRIED

    def test_every_declined_shape_keeps_the_string_path(self) -> None:
        for key, value in declined_values().items():
            assert key in DECLINED
            assert not _rust.crosses_as_encoded(value), (
                f"{key} ({DECLINED[key]}) is now CARRIED. That may be right, "
                f"but it is a decision: move its row and record what changed"
            )

    def test_every_earlier_arm_value_is_untouched_by_this_one(self) -> None:
        """The group the first version of the normalizer's gate got wrong.

        A ``bytes`` and a ``deque`` satisfy ``opaque_value``'s gate in
        isolation but never reach it — PyO3's sequence extraction claims them
        first. A gate that TRANSCRIBED the last two arms therefore said TRUE,
        the normalizer stopped stringifying them, and ``{{ p }}`` over ``b"ab"``
        went from Django's ``b'ab'`` to ``[97, 98]``.
        """
        for key, value in earlier_values().items():
            assert key in EARLIER
            assert not _rust.crosses_as_encoded(value), (
                f"{key} now crosses as an Encoded — it is claimed by "
                f"{EARLIER[key]}, and this arm must not reach it"
            )

    def test_the_three_groups_are_disjoint_and_counted(self) -> None:
        """A member in two groups would be excused twice (#2233)."""
        assert not (CARRIED & set(DECLINED))
        assert not (CARRIED & set(EARLIER))
        assert not (set(DECLINED) & set(EARLIER))
        assert len(CARRIED) == 18
        assert len(DECLINED) == 4
        assert len(EARLIER) == 7


class TestTheDeclinesAreRecordedInTheDivergingDirection:
    """Each decline is an UNFIXED cell, pinned so it cannot be widened silently."""

    def test_a_one_shot_iterator_is_not_consumed_by_the_conversion(self) -> None:
        """The reason for the decline, asserted rather than argued.

        Reading a generator to build the items would empty the caller's object.
        The gate is ``iter(o) is o``, and this is the test that it holds: after
        a full render the generator still yields everything it was going to.
        """
        gen = _one_shot()
        _rust.render_template("{% for x in p %}[{{ x }}]{% endfor %}", {"p": gen})
        assert list(gen) == [PAYLOAD], "the conversion consumed the generator"

    def test_a_one_shot_iterator_still_renders_its_repr(self) -> None:
        """Pinned DIVERGING: Django iterates it, djust reads the text."""
        gen = _one_shot()
        got = _rust.render_template("{{ p|length }}", {"p": gen})
        assert got != "0", (
            "a one-shot iterator now answers Django's length — the decline was "
            "lifted, so delete this pin and record what carries it"
        )

    def test_an_unbounded_reiterable_is_declined_rather_than_hanging(self) -> None:
        """`itertools.count()` behind a re-iterable `__iter__`.

        The one-shot guard does not catch this shape — ``iter(o)`` is a fresh
        ``count()`` each time, so ``iter(o) is not o`` — and enumerating it
        would never return. ``OPAQUE_ITEM_CAP`` bounds the read and DECLINES
        at it; the value keeps the string path it already had, so the cap can
        never produce a short collection.
        """
        value = declined_values()["unbounded-reiterable"]
        got = _rust.render_template("{{ p }}", {"p": value})
        assert got == "Unbounded()", got
        assert not _rust.crosses_as_encoded(value)

    def test_a_truthy_attribute_object_still_crosses_as_its_attribute_map(self) -> None:
        """The `__dict__` bulk-dump arm, untouched.

        `opaque_value` declines a TRUTHY, NON-iterable object with public
        attributes, so this arm keeps it — and `{{ p }}` keeps rendering the
        dict repr rather than `str(o)`. That cell diverges from Django and did
        before; retiring the arm is a separate decision.
        """
        value = instance("Presenter")
        value.name = "ok"
        assert not _rust.crosses_as_encoded(value)
        assert _rust.render_template("{{ p.name }}", {"p": value}) == "ok"
        assert (
            _rust.render_template("{{ p }}", {"p": value}) == "{&#x27;name&#x27;: &#x27;ok&#x27;}"
        )

    def test_a_FALSY_attribute_object_is_claimed_by_this_arm_not_that_one(self) -> None:
        """#2478's cell, and the reason the decline needs BOTH qualifiers."""
        value = instance("FalsyPresenter", __len__=lambda self: 0)
        value.name = "ok"
        assert _rust.crosses_as_encoded(value)
        assert _rust.render_template("{{ p.name }}", {"p": value}) == "ok"
        assert _rust.render_template("{% if p %}T{% else %}F{% endif %}", {"p": value}) == "F"


class TestTheNormalizerCarriesExactlyTheModelledClass:
    """#2477's half: `normalize_django_value` stops flattening what Rust models."""

    def test_every_carried_member_is_handed_over_by_identity(self) -> None:
        for key, value in _Members.build().items():
            got = normalize_django_value(value)
            assert got is value, (
                f"the normalizer flattened {key} to {got!r} — the LiveView path "
                f"and the raw path answer differently again"
            )

    def test_a_declined_or_earlier_value_still_takes_its_old_route(self) -> None:
        for key, value in declined_values().items():
            got = normalize_django_value(value)
            assert isinstance(got, str), (key, got)
        # A `bytes` is NOT stringified by the normalizer — it has no branch and
        # is not `crosses_as_encoded`, so it reaches the `str()` fallback. That
        # is the behaviour `{{ p }}` == `b'ab'` depends on.
        assert normalize_django_value(b"ab") == "b'ab'"
        assert normalize_django_value(collections.deque(["a"])) == "deque(['a'])"

    def test_a_set_nested_in_a_container_is_carried_too(self) -> None:
        """The recursion, which is where a real context puts one."""
        got = normalize_django_value({"tags": {PAYLOAD}, "rows": [{"a"}]})
        assert isinstance(got["tags"], set)
        assert isinstance(got["rows"][0], set)

    def test_state_roundtrip_still_flattens_a_set_to_a_sorted_list(self) -> None:
        """The one boundary that cannot take the live object.

        Django's session serializer passes no encoder and `json.dumps` refuses
        a set, so `state_roundtrip=True` keeps #626's sorted list — and the
        result must actually be dumpable, which is the property the branch
        exists for.
        """
        got = normalize_django_value({"tags": {"b", "a"}}, state_roundtrip=True)
        assert got == {"tags": ["a", "b"]}
        assert json.loads(json.dumps(got)) == {"tags": ["a", "b"]}

    def test_state_roundtrip_still_stringifies_the_rest_of_the_class(self) -> None:
        got = normalize_django_value({"k": {}.keys()}, state_roundtrip=True)
        assert got == {"k": "dict_keys([])"}
        json.dumps(got)

    def test_a_carried_collection_returns_to_python_as_its_items(self) -> None:
        """`into_pyobject`, which is what keeps this from regressing state.

        A `set` used to become a sorted LIST here, so a `set` written into view
        state came back out of the codec as a list. Coming back as the STRING
        `"{'a'}"` would hand a handler a string where it wrote a collection.
        """
        view = _rust.RustLiveView("{{ tags|join:',' }}")
        view.set_state("tags", {"a", "b"})
        assert sorted(view.get_state()["tags"]) == ["a", "b"]
        # ...and it survives the msgpack round trip the state backend does on
        # every read, which is the reopening `ENCODED_TAG` exists to prevent.
        back = _rust.RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert sorted(back.get_state()["tags"]) == ["a", "b"]


class TestTheGateHasOneStatement:
    """The structural half: one call site each, canaried in BOTH directions.

    A count pin that only catches an ADDED call site is half a pin — the
    failure this class actually has is a call site REMOVED or moved, which
    silently reopens the divergence. So each reader below is run against a
    mutated copy of the source with the call deleted (must report 0) and with a
    second one inserted (must report 2), which is what proves the count can
    move at all.
    """

    @staticmethod
    def _call_sites(source: str, name: str) -> int:
        """Calls to *name* that are not its own definition."""
        return len(
            [
                line
                for line in source.splitlines()
                if f"{name}(" in line
                and not line.lstrip().startswith(("///", "//", "#"))
                and "fn " + name not in line
                and "def " + name not in line
            ]
        )

    def test_opaque_value_is_called_exactly_once_in_the_conversion(self) -> None:
        source = CORE_RS.read_text(encoding="utf-8")
        assert self._call_sites(source, "opaque_value") == 1, (
            "`opaque_value` must have exactly ONE call site — the fallback "
            "block of `impl FromPyObject for Value`. A second caller is a "
            "second policy for one question (#1646)"
        )
        # The canary, both ways.
        removed = source.replace(
            "if let Some(encoded) = opaque_value(&ob.to_owned()) {", "if false {", 1
        )
        assert removed != source, "the mutation text did not match"
        assert self._call_sites(removed, "opaque_value") == 0, (
            "the reader cannot see a REMOVED call site, so its count is not a pin"
        )
        added = source.replace(
            "if let Some(encoded) = opaque_value(&ob.to_owned()) {",
            "let _ = opaque_value(&ob.to_owned());\n"
            "            if let Some(encoded) = opaque_value(&ob.to_owned()) {",
            1,
        )
        assert added != source
        assert self._call_sites(added, "opaque_value") == 2

    def test_crosses_as_encoded_is_consulted_exactly_once_by_the_normalizer(self) -> None:
        source = SERIALIZATION_PY.read_text(encoding="utf-8")
        assert self._call_sites(source, "crosses_as_encoded") == 1, (
            "the normalizer must consult the conversion at ONE place — its "
            "final fallback. A second consultation is a second policy (#1646)"
        )
        removed = source.replace("if _rust.crosses_as_encoded(value):", "if False:", 1)
        assert removed != source, "the mutation text did not match"
        assert self._call_sites(removed, "crosses_as_encoded") == 0
        added = source.replace(
            "if _rust.crosses_as_encoded(value):",
            "_ = _rust.crosses_as_encoded(value)\n            if _rust.crosses_as_encoded(value):",
            1,
        )
        assert added != source
        assert self._call_sites(added, "crosses_as_encoded") == 2

    def test_the_predicate_runs_the_conversion_rather_than_restating_it(self) -> None:
        """The bug the first version had, pinned as a source property.

        `crosses_as_encoded` must ask `extract::<Value>()` — the real
        conversion — and must NOT name the fallback block's last two arms,
        which is what made it claim a `bytes`. A behavioural sibling for this
        is `test_every_earlier_arm_value_is_untouched_by_this_one`; this pin is
        what names the mechanism, so the next author does not reach for the
        transcription again.
        """
        body = LIVE_RS.read_text(encoding="utf-8").split("fn crosses_as_encoded", 1)[1]
        body = body.split("\n}\n", 1)[0]
        assert "extract::<djust_core::Value>()" in body
        assert "opaque_value" not in body, (
            "`crosses_as_encoded` names `opaque_value` again — that is the "
            'transcription that regressed `{{ p }}` over `b"ab"` to `[97, 98]`'
        )
        assert "django_json_encoded" not in body


class TestTheCarrierCarriesMeasuredFactsNotCopiedOnes:
    """`repr` is measured, not cloned from `display` (#2472), and so is `len`.

    Both are correct for every BUILTIN in the class — `str(set())` and
    `repr(set())` coincide, and so do the two for `frozenset()` and
    `complex(0)` — which is exactly why a copy would look right and be wrong
    for the user classes this arm was widened to carry.
    """

    def test_a_class_whose_str_and_repr_differ_keeps_both(self) -> None:
        value = instance(
            "TwoSpellings",
            __len__=lambda self: 0,
            __str__=lambda self: "STR",
            __repr__=lambda self: "REPR",
        )
        for engine in ("django", "raw", "live"):
            assert outcome("{{ p }}", value, engine) == "STR", engine
            assert outcome("{{ p|pprint }}", value, engine) == "REPR", engine

    def test_a_lengths_count_is_the_objects_own_not_the_item_count(self) -> None:
        """`len` and `items` can legitimately disagree, and Django follows `len`."""
        value = _Members.build()["o-falsy-iter"]
        assert outcome("{{ p|length }}", value, "django") == "0"
        assert outcome("{{ p|length }}", _Members.build()["o-falsy-iter"], "raw") == "0"
        # ...while the ITEMS are one, which is what `{% for %}` renders.
        assert (
            outcome(
                "{% for x in p %}[{{ x }}]{% endfor %}", _Members.build()["o-falsy-iter"], "raw"
            )
            == "[&lt;img src=x onerror=alert(1)&gt;]"
        )
