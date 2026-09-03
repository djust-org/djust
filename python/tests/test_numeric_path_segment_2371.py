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
  segment starting with ``-``; when #2371 landed djust rendered empty. That
  was a lexer/grammar-level divergence, not a resolution one, and #2371 left
  it alone. It has since CONVERGED: #2578 tightened the plain-variable head
  grammar to Django's ``[\\w.]``-only rule, so both engines now refuse
  ``{{ l.-1 }}`` at compile time (see
  ``TestTheLexerLevelDivergenceIsNowConverged`` below).
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


class TestTheStringIndexStepIsCLOSED:
    """Django's step 3 subscripts a ``str``, and so does djust now (#2373).

    This class asserted the OPPOSITE — ``django == 'a'`` and ``djust == ''`` —
    and said "if this is the #2373 fix, delete this class and add the cell to
    ``TestTheThreeStepsAndTheirOrder``". The cells are kept HERE instead,
    turned around, because the reason they were excluded is worth keeping next
    to them: the exclusion's stated premise, that closing this meant widening
    ``Context::get``'s return type across all of its callers, was **wrong**.
    ``Context::resolve`` already returns an owned ``Value``, and it is the door
    every operand site reaches — so the step is one small helper beside
    ``Context::dict_view``, which exists for exactly the same reason in exactly
    the same place, and ``Context::get``'s signature is untouched.
    """

    @pytest.mark.parametrize(
        ("src", "ctx", "expected"),
        [
            ("{{ s.0 }}", {"s": "abc"}, "a"),
            # BY CODE POINT, not by byte: a byte index would split `é` in half.
            ("{{ s.1 }}", {"s": "héllo"}, "é"),
            ("{{ s.2 }}", {"s": "héllo"}, "l"),
            ("{{ d.a.0 }}", {"d": {"a": "abc"}}, "a"),
            # A character is itself a `str`, so step 3 runs again.
            ("{{ s.0.0 }}", {"s": "abc"}, "a"),
            # Out of range, and a non-numeric segment: both empty, both engines.
            ("{{ s.9 }}", {"s": "abc"}, ""),
            ("{{ s.x }}", {"s": "abc"}, ""),
        ],
    )
    def test_djust_indexes_the_string_exactly_as_django_does(
        self, src: str, ctx: dict, expected: str
    ) -> None:
        d, r = both(src, ctx)
        assert d == expected, f"Django moved: {src} on {ctx!r} is now {d!r}"
        assert r == d, f"{src} on {ctx!r}: django {d!r}, djust {r!r}"

    @pytest.mark.parametrize("src", ["{{ d.items.0 }}", "{{ d.keys.0 }}", "{{ d.values.0 }}"])
    def test_a_dict_view_is_still_not_subscriptable(self, src: str) -> None:
        # Python's `dict_items` raises on `[0]`, so both engines render empty —
        # and the new step must not accidentally reach it. The prefix
        # `d.items` is not a `String`, and `items` does not parse as an index.
        d, r = both(src, {"d": {"a": 1, "b": 2}})
        assert (d, r) == ("", ""), (d, r)

    def test_a_character_of_a_MARKED_string_is_escaped_by_both(self) -> None:
        """The safety direction, measured rather than reasoned about.

        ``SafeString`` overrides ``__add__``, not ``__getitem__``, so
        ``mark_safe("<b>")[0]`` is a plain ``str`` and DJANGO escapes it. And
        ``_collect_safe_keys`` never descends into a ``str``, so ``safe_keys``
        holds no per-character path and djust escapes it too. This step adds no
        grant, and here is the cell that would show it if it did.
        """
        from django.utils.safestring import mark_safe

        from djust.mixins.rust_bridge import _collect_safe_keys

        ctx = {"s": mark_safe("<b>")}
        keys = _collect_safe_keys(ctx["s"], "s")
        # `render_template` carries no `safe_keys` channel at all, so this
        # cell has to go through `render_template_with_dirs` — the only entry
        # point that does. Using `both()` here would measure djust with the
        # mark DROPPED and prove nothing about the grant.
        django_out = DjangoTemplate("{{ s.0 }}").render(DjangoContext(dict(ctx)))
        djust_out = _rust.render_template_with_dirs("{{ s.0 }}", ctx, [], keys)
        assert django_out == "&lt;", django_out
        assert djust_out == django_out, (django_out, djust_out)

        # The control, and it is what makes the assertion above non-vacuous:
        # the WHOLE marked string IS live in both, so the escape above is the
        # SLICE losing the mark rather than the mark never arriving.
        django_out = DjangoTemplate("{{ s }}").render(DjangoContext(dict(ctx)))
        djust_out = _rust.render_template_with_dirs("{{ s }}", ctx, [], keys)
        assert (django_out, djust_out) == ("<b>", "<b>"), (django_out, djust_out)

    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("{% if s.0 %}Y{% else %}N{% endif %}", "Y"),
            ("{% with q=s.0 %}{{ q }}{% endwith %}", "a"),
            ("{% for x in s.0 %}{{ x }}{% empty %}E{% endfor %}", "a"),
            ("{% firstof s.0 %}", "a"),
            ("{{ s.0|upper }}", "A"),
        ],
    )
    def test_every_operand_channel_reaches_it(self, src: str, expected: str) -> None:
        """One helper, called from `Context::resolve` — so every channel gets it.

        `{{ }}` calls `resolve` directly; `{% if %}` / `{% with %}` /
        `{% for %}` / `{% firstof %}` reach it as `get_value_safe`'s last arm.
        If the step had been put anywhere narrower these would disagree, which
        is the #1646 shape this fix is avoiding rather than creating.
        """
        d, r = both(src, {"s": "abc"})
        assert d == expected, f"Django moved: {src} is now {d!r}"
        assert r == d, (d, r)


class TestTheLexerLevelDivergenceIsNowConverged:
    """``{{ l.-1 }}`` is a Django *parse* error, and since #2578 djust refuses it too.

    This was a lexer-level divergence when #2371 landed — Django raised
    ``TemplateSyntaxError`` at parse time for a segment starting with ``-``
    while djust rendered empty. #2578 tightened the plain-variable head
    grammar to Django's ``[\\w.]``-only ``FilterExpression`` rule, so the
    ``-1`` remainder is now refused at compile time on both engines (djust
    surfaces it as ``RuntimeError`` carrying Django's
    ``Could not parse the remainder`` wording). Pinned so it goes red if
    either engine moves back.
    """

    @pytest.mark.parametrize("src", ["{{ l.-1 }}", "{{ d.-1 }}"])
    def test_both_engines_refuse_at_compile_time(self, src: str) -> None:
        d, r = both(src, {"l": [7, 8], "d": {-1: "neg"}})
        assert d == "<<EXC TemplateSyntaxError>>", f"Django moved: {d!r}"
        assert r == "<<EXC RuntimeError>>", f"djust no longer refuses (#2578): {r!r}"


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

    That was the whole of the #2373 gap, computed by running Django's own three
    steps rather than by guessing from the outputs. #2373 is CLOSED, so this no
    longer EXCUSES a divergence — the sweep now requires those cells to agree
    like every other, and uses this only to assert it builds the shape at all.
    A fix whose sweep never constructs the input it fixes is unmeasured, which
    is the same reading the count had when it bounded an exclusion.
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
        string_index_cells = 0
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
            if _walks_through_a_string_index(value, path):
                string_index_cells += 1
            if d == r:
                continue
            mismatches.append(f"{path} on {value!r}: django={d!r} djust={r!r}")
        assert checked == 3000
        # Two harness preconditions, because a sweep that measures nothing
        # reports the same zero as one that measures everything and agrees.
        assert resolved >= 300, (
            f"only {resolved} of {checked} cells resolved to anything — the "
            "sweep is not reaching the surface it claims to measure"
        )
        # This bounded an EXCLUSION until #2373 closed; it now bounds
        # COVERAGE. Same number, opposite meaning: those cells are required to
        # agree along with every other, and if the sweep stopped constructing
        # them the fix would be unmeasured here rather than excused here.
        assert string_index_cells >= 10, (
            f"the string-index shape was built {string_index_cells} times — if "
            "it is 0 the sweep never exercises what #2373 closed"
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
