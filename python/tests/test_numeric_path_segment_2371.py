"""A numeric path segment follows Django's three-step lookup (#2371).

The bug
-------
``{{ d.0 }}`` resolved *nothing* on a dict, whatever the key's type::

    {{ d.0 }}   d={0: 4}     django: '4'    djust: ''
    {{ d.0 }}   d={'0': 4}   django: '4'    djust: ''
    {{ d.a }}   d={'a': 4}   django: '4'    djust: '4'   <- agreed
    {{ l.0 }}   l=[7, 8]     django: '7'    djust: '7'   <- agreed

So a numeric segment reached the list-index step and *only* that step, while a
non-numeric segment reached the mapping step and only that one. The two halves
of Django's rule were split across the two spellings of a segment, and each
spelling was missing the other's half.

The value was silently empty — no exception, no warning — which is the
silent-wrong-output class. It composes with any filter, and ``divisibleby`` is
the sharpest of those::

    {{ d.0|divisibleby:"2" }}   d={0: 4}   django: 'True'   djust: 'False'

A ``{% if %}`` gate reads a definite wrong answer there rather than an
obviously missing one.

Django's rule
-------------
``django.template.base.Variable._resolve_lookup`` tries three things per path
segment, in this order, and takes the first that does not raise:

1. **mapping item access with the segment as a STRING** — ``current[bit]``;
2. **attribute access** — ``getattr(current, bit)``;
3. **integer index** — ``current[int(bit)]``.

That is why both dict spellings work in Django and for *different* reasons:
``{'0': 4}`` is found by step 1 and ``{0: 4}`` by step 3. It is also why the
string-keyed one WINS when a dict carries both — measured, not assumed::

    {{ d.0 }}   d={'0': 's', 0: 'i'}   django: 's'

Step 3 is not list-only. It is ``current[int(bit)]`` on anything subscriptable,
so a *string* answers it too: ``{{ s.0 }}`` on ``"abc"`` is ``'a'`` in Django.

The parallel path that already had this right (#1646)
------------------------------------------------------
``Context::resolve``'s raw-Python sidecar walk has done
``get_item(part).or_else(getattr).or_else(get_item(int(part)))`` since #1997 —
Django's three steps, in Django's order, with a comment saying so. The
``Value``-based ``Context::get`` walk beside it never got the same treatment.
One path was fixed and its twin was not, which is the drift class CLAUDE.md
#1646 names. The fix routes the ``Value`` walk through one
``lookup_segment`` helper that states the order once.

What this deliberately does NOT change
---------------------------------------
* ``{{ l.-1 }}`` — Django raises ``TemplateSyntaxError`` at *parse* time for a
  segment starting with ``-``; djust renders empty. That is a lexer-level
  divergence, not a resolution one, and it is pinned below rather than fixed.
* ``{{ d.items.0 }}`` — a Python ``dict_items`` is not subscriptable, so
  Django's step 3 raises and the cell is empty on both engines. The fix must
  keep it empty, which is why ``Value::DictView`` is absent from the index arm.
"""

from __future__ import annotations

import decimal
import random

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:  # pragma: no cover - import-time bootstrap
    settings.configure(
        DEBUG=False,
        USE_I18N=False,
        USE_TZ=False,
        TIME_ZONE="UTC",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"context_processors": []},
            }
        ],
        INSTALLED_APPS=[],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

#: A live payload, so every cell doubles as a permissiveness probe.
XSS = "<img src=x onerror=alert(1)>"


def both(src: str, ctx: dict) -> tuple[str, str]:
    try:
        d = DjangoTemplate(src).render(DjangoContext(ctx))
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = _rust.render_template(src, ctx)
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The cells the issue measured.
# ===========================================================================


class TestTheReportedCells:
    """Every row of #2371's table, verbatim."""

    @pytest.mark.parametrize(
        ("src", "ctx", "expected"),
        [
            ("{{ d.0 }}", {"d": {0: 4}}, "4"),
            ("{{ d.1 }}", {"d": {1: "one"}}, "one"),
            ("{{ d.0 }}", {"d": {"0": 4}}, "4"),
            ("{{ d.a }}", {"d": {"a": 4}}, "4"),
            ("{{ l.0 }}", {"l": [7, 8]}, "7"),
        ],
    )
    def test_resolution(self, src: str, ctx: dict, expected: str) -> None:
        d, r = both(src, ctx)
        assert d == expected, f"Django moved: {src} on {ctx!r} is now {d!r}"
        assert r == expected, f"{src} on {ctx!r}: django={d!r} djust={r!r}"

    @pytest.mark.parametrize(
        ("src", "ctx", "expected"),
        [
            # The sharpest of the three: a DEFINITE wrong answer, not a
            # missing one, so a `{% if %}` gate reads False rather than empty.
            ('{{ d.0|divisibleby:"2" }}', {"d": {0: 4}}, "True"),
            ('{{ d.0|add:"1" }}', {"d": {0: 4}}, "5"),
            ("{{ d.0|length }}", {"d": {0: "abcd"}}, "4"),
        ],
    )
    def test_composed_with_a_filter(self, src: str, ctx: dict, expected: str) -> None:
        d, r = both(src, ctx)
        assert d == expected, f"Django moved: {src} on {ctx!r} is now {d!r}"
        assert r == expected, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The three steps, each independently reachable.
# ===========================================================================


class TestTheThreeStepsAndTheirOrder:
    """One test per step, plus the cell that pins the order between them.

    Each step needs a case only IT can answer, or a mechanism can be deleted
    with the suite still green (CLAUDE.md, #2129/#2135).
    """

    def test_step_one_string_key_is_reachable_alone(self) -> None:
        """``{'0': …}`` is found by the STRING lookup. No int key exists."""
        assert_agrees("{{ d.0 }}", {"d": {"0": "by-string"}})
        assert _rust.render_template("{{ d.0 }}", {"d": {"0": "by-string"}}) == "by-string"

    def test_step_three_int_key_is_reachable_alone(self) -> None:
        """``{0: …}`` is found by the INT index. No string key exists."""
        assert_agrees("{{ d.0 }}", {"d": {0: "by-int"}})
        assert _rust.render_template("{{ d.0 }}", {"d": {0: "by-int"}}) == "by-int"

    def test_the_string_key_wins_when_a_dict_carries_both(self) -> None:
        """Django's order, measured: step 1 runs before step 3."""
        ctx = {"d": {"0": "by-string", 0: "by-int"}}
        assert_agrees("{{ d.0 }}", ctx)
        assert _rust.render_template("{{ d.0 }}", ctx) == "by-string"

    def test_step_three_indexes_a_list(self) -> None:
        assert_agrees("{{ l.1 }}", {"l": [7, 8]})
        assert _rust.render_template("{{ l.1 }}", {"l": [7, 8]}) == "8"

    def test_step_three_indexes_a_tuple(self) -> None:
        assert_agrees("{{ t.1 }}", {"t": (7, 8)})


class TestNumericKeysAreConflatedTheWayPythonConflatesThem:
    """``{1: 'a'}[True]`` and ``{1: 'a'}[1.0]`` both resolve in CPython.

    ``ObjectKey`` already hashes numerics by VALUE across ``Int``/``Float``/
    ``Bool``/``Decimal``/``BigInt`` (#2339); the index step inherits that,
    and these pin that it does.
    """

    @pytest.mark.parametrize(
        ("key", "label"),
        [
            (1, "int"),
            (1.0, "float"),
            (True, "bool"),
            (decimal.Decimal("1"), "decimal"),
        ],
    )
    def test_a_numeric_key_of_any_type_answers_the_int_index(self, key, label) -> None:
        assert_agrees("{{ d.1 }}", {"d": {key: label}})
        assert _rust.render_template("{{ d.1 }}", {"d": {key: label}}) == label

    def test_a_non_matching_numeric_key_still_misses(self) -> None:
        """``{{ d.0 }}`` on ``{1.0: …}`` is EMPTY on both engines.

        The conflation is by value, not "any numeric key answers any numeric
        segment" — without this the previous test would pass for a fix that
        returned the first entry.
        """
        d, r = both("{{ d.0 }}", {"d": {1.0: "float-one"}})
        assert d == "", f"Django moved: {d!r}"
        assert r == ""

    def test_int_of_the_segment_not_its_spelling(self) -> None:
        """Django's step 3 is ``int(bit)``, so ``007`` is the key ``7``.

        And step 1 still finds a literal ``'007'`` string key first, which is
        what makes these two rows differ.
        """
        assert_agrees("{{ d.007 }}", {"d": {7: "seven"}})
        assert_agrees("{{ d.007 }}", {"d": {"007": "oh-oh-seven"}})


class TestTheWalkIsPerSegment:
    """A numeric segment is not special-cased to the LAST position."""

    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{{ d.0.1 }}", {"d": {0: [9, 10]}}),
            ("{{ d.0.a }}", {"d": {0: {"a": "deep"}}}),
            ("{{ d.a.0 }}", {"d": {"a": {0: "nested-int-key"}}}),
            ("{{ l.0.0 }}", {"l": [[5]]}),
            ("{{ d.0.0.0 }}", {"d": {0: {0: {0: "three-deep"}}}}),
        ],
    )
    def test_nested(self, src: str, ctx: dict) -> None:
        assert_agrees(src, ctx)


class TestTagOperandsResolveThroughTheSameWalk:
    """Every operand site reaches ``Context::get``; none may be left behind."""

    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{% if d.0 %}Y{% else %}N{% endif %}", {"d": {0: 4}}),
            ("{% if d.0 %}Y{% else %}N{% endif %}", {"d": {0: 0}}),
            ("{% for x in d.0 %}[{{ x }}]{% empty %}E{% endfor %}", {"d": {0: [1, 2]}}),
            ("{% with v=d.0 %}[{{ v }}]{% endwith %}", {"d": {0: 4}}),
            ("{% firstof d.0 'fallback' %}", {"d": {0: "hit"}}),
        ],
    )
    def test_operand(self, src: str, ctx: dict) -> None:
        assert_agrees(src, ctx)


# ===========================================================================
# The misses that must STAY misses.
# ===========================================================================


class TestTheMissesThatMustStayMisses:
    """A resolver made more permissive than Django is the failure this guards.

    Every row here renders empty in Django. If the fix opened a path that
    Django's three steps do not have, one of these goes red.
    """

    @pytest.mark.parametrize(
        ("src", "ctx"),
        [
            ("{{ d.0 }}", {"d": {}}),
            ("{{ l.5 }}", {"l": [7, 8]}),
            ("{{ l.a }}", {"l": [7, 8]}),
            ("{{ n.0 }}", {"n": 42}),
            ("{{ n.0 }}", {"n": None}),
            ("{{ b.0 }}", {"b": True}),
            # `dict_items` is not subscriptable in Python, so step 3 raises.
            #
            # Two spellings, because they reach the index arm by DIFFERENT
            # routes and only the second one reaches it at all. `{{ d.items.0 }}`
            # dies at the `items` segment — `Value::Object` has no such key, so
            # the walk returns before any view is built. `{% with %}` BINDS the
            # view into a frame, so `q.0` really does hand a `Value::DictView`
            # to the index step. Without the bound form the `DictView`-is-absent
            # decision is untested: a mutation adding it to the index arm left
            # the whole file green until these rows existed.
            ("{{ d.items.0 }}", {"d": {"a": 1}}),
            ("{{ d.keys.0 }}", {"d": {"a": 1}}),
            ("{{ d.values.0 }}", {"d": {"a": 1}}),
            ("{% with q=d.keys %}{{ q.0 }}{% endwith %}", {"d": {"a": 1, "b": 2}}),
            ("{% with q=d.items %}{{ q.0 }}{% endwith %}", {"d": {"a": 1, "b": 2}}),
            ("{% with q=d.values %}{{ q.1 }}{% endwith %}", {"d": {"a": 1, "b": 2}}),
            # A serialized model's map has no numeric key, and `getattr` on a
            # real model cannot answer `"0"` either.
            ("{{ m.0 }}", {"m": {"pk": 7, "__str__": "Doc", "__model__": "Doc"}}),
        ],
    )
    def test_still_empty_on_both_engines(self, src: str, ctx: dict) -> None:
        d, r = both(src, ctx)
        assert d == "", f"Django moved: {src} on {ctx!r} is now {d!r}"
        assert r == "", f"{src} on {ctx!r}: djust now renders {r!r} where Django renders ''"


class TestTheStringIndexStepIsNamedNotFixed:
    """Django's step 3 subscripts a ``str``; djust's does not (#2373).

    ``{{ s.0 }}`` on ``"abc"`` is ``'a'`` in Django and empty here. Closing it
    needs an OWNED return — the character is constructed, not borrowed — which
    is a return-type change across every ``Context::get`` caller rather than an
    arm in ``lookup_segment``'s ``match``. Scoped out of #2371 deliberately and
    filed separately; pinned here so it is a named limit rather than a silent
    one, and so it goes red the day someone closes it.
    """

    @pytest.mark.parametrize(
        ("src", "ctx", "django_says"),
        [
            ("{{ s.0 }}", {"s": "abc"}, "a"),
            ("{{ s.1 }}", {"s": "héllo"}, "é"),
            ("{{ d.a.0 }}", {"d": {"a": "abc"}}, "a"),
        ],
    )
    def test_django_indexes_the_string_and_djust_renders_empty(
        self, src: str, ctx: dict, django_says: str
    ) -> None:
        d, r = both(src, ctx)
        assert d == django_says, f"Django moved: {src} on {ctx!r} is now {d!r}"
        assert r == "", (
            f"{src} now renders {r!r} — if this is the #2373 fix, delete this "
            "class and add the cell to TestTheThreeStepsAndTheirOrder"
        )


class TestTheLexerLevelDivergenceIsNamedNotFixed:
    """``{{ l.-1 }}`` is a Django *parse* error; djust renders empty.

    Not a resolution bug and not fixed here. Pinned so it cannot be mistaken
    for something this change was supposed to have covered, and so it goes red
    if either engine moves.
    """

    @pytest.mark.parametrize("src", ["{{ l.-1 }}", "{{ d.-1 }}"])
    def test_django_raises_at_parse_time_and_djust_renders_empty(self, src: str) -> None:
        d, r = both(src, {"l": [7, 8], "d": {-1: "neg"}})
        assert d == "<<EXC TemplateSyntaxError>>", f"Django moved: {d!r}"
        assert r == ""


# ===========================================================================
# Permissiveness: a newly-resolvable value is still escaped.
# ===========================================================================


class TestANewlyResolvableValueIsEscapedExactlyAsDjangoEscapesIt:
    """The direction constraint: no cell may emit markup Django does not.

    Resolution and escaping are separate stages, so making a path resolve
    could not by itself open a hole — but "could not by itself" is a
    hypothesis, and this runs it.
    """

    @pytest.mark.parametrize(
        "ctx",
        [
            {"d": {0: XSS}},
            {"d": {"0": XSS}},
            {"d": {0: {"a": XSS}}},
            {"l": [XSS]},
        ],
    )
    def test_the_payload_comes_back_escaped(self, ctx: dict) -> None:
        src = "{{ d.0 }}" if "d" in ctx else "{{ l.0 }}"
        if ctx.get("d", {}).get(0) == {"a": XSS}:
            src = "{{ d.0.a }}"
        d, r = both(src, ctx)
        assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"
        assert "<img" not in r, f"{src} emitted a live tag opener: {r!r}"


# ===========================================================================
# A randomised differential over the whole segment surface.
# ===========================================================================

#: Values a segment can land on. Every one is placed at every position by
#: the sweep below, rather than at the one position a curated table happens
#: to pick (CLAUDE.md, v1.0.0rc4 finding #1).
_LEAVES = [4, 0, "hit", "", XSS, True, None, 1.5, decimal.Decimal("2.5")]


def _random_container(rng: random.Random, depth: int):
    """A dict or list whose keys span every spelling a segment can carry."""
    if depth == 0:
        return rng.choice(_LEAVES)
    kind = rng.choice(["dict-int", "dict-str", "dict-mixed", "list", "tuple", "str"])
    if kind == "list":
        return [_random_container(rng, depth - 1) for _ in range(rng.randint(0, 3))]
    if kind == "tuple":
        return tuple(_random_container(rng, depth - 1) for _ in range(rng.randint(0, 3)))
    if kind == "str":
        return rng.choice(["abc", "héllo→", "", "a<b"])
    out = {}
    for i in range(rng.randint(0, 3)):
        if kind == "dict-int":
            key = rng.choice([0, 1, 2, 1.0, True, decimal.Decimal("1")])
        elif kind == "dict-str":
            key = rng.choice(["0", "1", "a", "007", "k"])
        else:
            key = rng.choice([0, "0", 1, "1", "a", 2.0, True])
        out[key] = _random_container(rng, depth - 1)
    return out


def _random_path(rng: random.Random) -> str:
    segs = [
        rng.choice(["0", "1", "2", "007", "a", "k", "items", "keys"])
        for _ in range(rng.randint(1, 3))
    ]
    return "p." + ".".join(segs)


def _walks_through_a_string_index(root, path: str) -> bool:
    """Whether resolving *path* would use Django's step 3 on a ``str``.

    That is the whole of the #2373 gap, computed by running Django's own three
    steps rather than by guessing from the outputs — so the sweep below can
    separate "the known, named limit" from "a real divergence" mechanically
    instead of by a heuristic on the rendered text.
    """
    current = root
    for bit in path.split(".")[1:]:
        try:
            current = current[bit]
            continue
        except (TypeError, AttributeError, KeyError, IndexError):
            pass
        try:
            current = getattr(current, bit)
            continue
        except AttributeError:
            pass
        try:
            index = int(bit)
        except ValueError:
            return False
        if isinstance(current, str):
            return True
        try:
            current = current[index]
        except (TypeError, KeyError, IndexError):
            return False
    return False


class TestARandomisedDifferentialOverTheSegmentSurface:
    """A curated table samples the axis you noticed; this samples the axis.

    Seeded, so a failure names the exact cell and reruns identically.
    """

    def test_three_thousand_random_paths_agree_with_django(self) -> None:
        rng = random.Random(20371)
        checked = 0
        resolved = 0
        known_gap = 0
        mismatches: list[str] = []
        for _ in range(3000):
            value = _random_container(rng, rng.randint(1, 3))
            path = _random_path(rng)
            src = "[{{ %s }}]" % path
            ctx = {"p": value}
            d, r = both(src, ctx)
            checked += 1
            if d != "[]":
                resolved += 1
            if d == r:
                continue
            if _walks_through_a_string_index(value, path):
                known_gap += 1
                continue
            mismatches.append(f"{path} on {value!r}: django={d!r} djust={r!r}")
        assert checked == 3000
        # Two harness preconditions, because a sweep that measures nothing
        # reports the same zero as one that measures everything and agrees.
        assert resolved >= 300, (
            f"only {resolved} of {checked} cells resolved to anything — the "
            "sweep is not reaching the surface it claims to measure"
        )
        assert known_gap >= 10, (
            f"the #2373 string-index classifier fired {known_gap} times — if "
            "it is 0 the sweep never builds the shape it excuses, and the "
            "exclusion is unfalsifiable rather than bounded"
        )
        assert not mismatches, (
            f"{len(mismatches)} of {checked} cells diverge; first 10:\n"
            + "\n".join(mismatches[:10])
        )

    def test_no_random_cell_emits_a_tag_opener_django_escaped(self) -> None:
        """The permissiveness half of the same sweep."""
        rng = random.Random(20372)
        leaks: list[str] = []
        for _ in range(1500):
            value = _random_container(rng, rng.randint(1, 3))
            path = _random_path(rng)
            src = "[{{ %s }}]" % path
            d, r = both(src, {"p": value})
            if "<img" in r and "<img" not in d:
                leaks.append(f"{path} on {value!r}: django={d!r} djust={r!r}")
        assert not leaks, "djust emitted live markup Django escaped:\n" + "\n".join(leaks[:10])
