"""`int(arg)` is a TypeError for a non-str non-number argument (#2366).

The bug
-------
Four filters — ``truncatechars``, ``truncatewords``, ``get_digit`` and
``floatformat`` — have a Django source that catches ``ValueError`` **only**::

    def truncatechars(value, arg):
        try:    length = int(arg)
        except ValueError:  return value      # a TypeError escapes

``int()`` raises **TypeError**, not ValueError, for anything that is neither a
string nor a number: a list, a tuple, a dict, a model instance, a ``datetime``.
So Django **raises** for those arguments, and djust returned its input::

    {{ p|truncatechars:q }}   q=[1, 2]
      django: TypeError: int() argument must be a string, a bytes-like object
              or a real number, not 'list'
      djust : 'abcdefghij'

Which is the more permissive direction: djust rendered where Django 500s. It
was also inconsistent with djust's OWN ``Raise``-policy filters — ``center``,
``ljust``, ``rjust``, ``wordwrap``, ``divisibleby``, ``urlizetrunc`` — which
already raised for the same argument.

The premise the issue stated, and where it is wrong
----------------------------------------------------
#2366 framed the choice as *"either the dispatch table learns the argument's
original type (a ``Value`` beside the ``&str``), or this is declared a bounded
wire-format residue and pinned"*. Measuring it shows the dichotomy is false,
and where the line actually falls is the finding:

* a **list**, a **tuple** and a **dict** reach ``Context::resolve`` as
  ``Value::List`` / ``Value::Tuple`` / ``Value::Object``. Their type is intact
  at the resolution site — one line above where it used to be stringified — so
  the fix is to compute ONE BIT there ("is ``int(arg)`` a TypeError?") and
  thread it, rather than pushing a whole ``Value`` through 57 filter arms.
* a **datetime**, a **date**, a **time**, a **set** and an arbitrary object are
  ``Value::String`` by then. Their type was lost at the **PyO3 extraction
  boundary**, not at the dispatch table — ``{{ q }}`` on a ``datetime`` renders
  19 characters and ``{{ q|length }}`` answers 19. Nothing below that boundary
  can recover it.

So the ``datetime`` the issue's own headline uses is the half that stays. It is
pinned in :class:`TestTheExtractionBoundaryResidueIsNamed` with the measurement
that locates the loss, so the next reader does not go looking for it in the
dispatch table.

One mechanism, not two
----------------------
#2328 asked this same question for the one spelling it had noticed — a bare
``None`` — with a predicate named after that spelling. ``int_arg_is_type_error``
asks it of the TYPE, and subsumes it: ``None`` resolves to ``Value::None``
since #2347, so the resolved-value arm answers the ``None`` case too. Keeping
both would have been two mechanisms on the same half, which is the shape
CLAUDE.md's v1.1.1-2 rule says to delete rather than test around.

The rule is stated as what ``int()`` **accepts**, which is how CPython's own
error message states it, so a new ``Value`` variant defaults to "``int()``
refuses it" — the conservative direction. The refusal list would default a new
container to "accepted" and silently return the input where Django raises:
this bug, one variant later.
"""

from __future__ import annotations

import datetime
import random
from decimal import Decimal

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

#: The four filters whose Django source catches `ValueError` only, with a value
#: each filter actually does something with.
RETURN_INPUT = {
    "truncatechars": "abcdefghij",
    "truncatewords": "a b c d e",
    "get_digit": 4231,
    "floatformat": 4231.5,
}

#: The filters that already RAISE for an unparseable argument (#2328). Listed
#: so "the fix made the four agree with the other six" is asserted rather than
#: claimed — they are the shape the four now match.
ALREADY_RAISE = {
    "center": "ab",
    "ljust": "ab",
    "rjust": "ab",
    "wordwrap": "a b c",
    "divisibleby": 4,
    "urlizetrunc": "http://example.com/x",
}

#: Arguments whose Python type `int()` refuses with a TypeError AND whose type
#: survives to the resolution site.
TYPED_REFUSALS = {
    "list": [1, 2],
    "empty-list": [],
    "tuple": (1, 2),
    "dict": {"a": 1},
    "empty-dict": {},
    "none": None,
}

#: The same rule for an argument that is a dict VIEW — reached by a PATH
#: rather than by binding, and the one refused type no bound value can produce.
#: `int(dict_keys(...))` is a TypeError in Python too.
VIEW_PATHS = ["d.keys", "d.items", "d.values"]

#: The same rule, for arguments whose type was already lost at extraction.
STRINGIFIED_AT_EXTRACTION = {
    "datetime": datetime.datetime(2020, 1, 1, 15, 30),
    "date": datetime.date(2020, 1, 1),
    "time": datetime.time(15, 30),
    "set": {1, 2},
}

#: Arguments `int()` accepts, which must keep working.
ACCEPTED = {
    "int": 3,
    "zero": 0,
    "negative": -1,
    "float": 2.7,
    "bool": True,
    "decimal": Decimal("3"),
    "numeric-str": "3",
    "big": 2**70,
}


#: (filter, argument) pairs that diverge for a reason of their own, each with
#: the mechanism. NOT a blanket exclusion: a pair listed here is asserted to
#: still diverge, so a stale row goes red rather than lingering.
OTHER_MECHANISM = {
    ("floatformat", "big"): (
        "a 2**70 PRECISION, not a bad argument type. `int(2**70)` is fine; it "
        "is Django's `Decimal.quantize` that raises `InvalidOperation`, and "
        "djust refuses past `MAX_PLACES` and returns its input. Predates "
        "#2366 and is untouched by it — the argument's TYPE is `int` on both "
        "engines"
    ),
}


def django_raises(src: str, ctx: dict) -> bool:
    try:
        DjangoTemplate(src).render(DjangoContext(ctx))
    except Exception:  # noqa: BLE001
        return True
    return False


def djust_raises(src: str, ctx: dict) -> bool:
    try:
        _rust.render_template(src, ctx)
    except Exception:  # noqa: BLE001
        return True
    return False


def both(src: str, ctx: dict) -> tuple[str, str]:
    try:
        d = DjangoTemplate(src).render(DjangoContext(ctx))
    except BaseException as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = _rust.render_template(src, ctx)
    except BaseException as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


# ===========================================================================
# The raise BIT, which is the comparable property.
# ===========================================================================


class TestTheFourReturnInputFiltersNowRaise:
    """Both engines raise. The exception TYPES differ and always have.

    Django's is the `TypeError` from `int()`; djust's is the
    `RuntimeError: Template error: …` every filter error becomes at the PyO3
    boundary. The comparable property is the BIT — does this template render or
    fail — which is the same property #2328 used for the six filters that
    already raised.
    """

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    @pytest.mark.parametrize("arg", sorted(TYPED_REFUSALS))
    def test_a_typed_refusal_raises_on_both_engines(self, name: str, arg: str) -> None:
        src = "{{ p|%s:q }}" % name
        ctx = {"p": RETURN_INPUT[name], "q": TYPED_REFUSALS[arg]}
        assert django_raises(src, ctx), f"Django no longer raises: {src} with {arg}"
        assert djust_raises(src, ctx), f"{src} with a {arg} argument did not raise"

    @pytest.mark.parametrize("name", sorted(ALREADY_RAISE))
    @pytest.mark.parametrize("arg", sorted(TYPED_REFUSALS))
    def test_the_six_that_already_raised_still_do(self, name: str, arg: str) -> None:
        """The shape the four now match. Without this the claim "the four now
        agree with djust's own Raise-policy filters" is unmeasured."""
        src = "{{ p|%s:q }}" % name
        ctx = {"p": ALREADY_RAISE[name], "q": TYPED_REFUSALS[arg]}
        assert django_raises(src, ctx), f"Django no longer raises: {src} with {arg}"
        assert djust_raises(src, ctx), f"{src} with a {arg} argument did not raise"


class TestADictViewArgumentRaisesToo:
    """`Value::DictView`, the one refused type no BOUND value can produce.

    It is reached only by a path — `{{ p|truncatechars:d.keys }}` — so a table
    that binds `q` to every Python type it can think of never builds it. A
    gate-off mutation moving `DictView` into the accepted set left the whole
    file green until these cells existed.
    """

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    @pytest.mark.parametrize("path", VIEW_PATHS)
    def test_raises_on_both_engines(self, name: str, path: str) -> None:
        src = "{{ p|%s:%s }}" % (name, path)
        ctx = {"p": RETURN_INPUT[name], "d": {"a": 1, "b": 2}}
        assert django_raises(src, ctx), f"Django no longer raises: {src}"
        assert djust_raises(src, ctx), f"{src} did not raise"


class TestEveryRendererCallSiteResolvesItsArgument:
    """The structural pin that makes deleting the spelling fallback safe.

    `int_arg_is_type_error` answers `false` whenever nothing was resolved,
    because the only way to reach that is a QUOTED argument — whose `int()` is
    an ordinary ValueError. That reasoning holds exactly as long as every
    renderer call site hands `apply_filter_full_safe` a real context. A future
    site passing `None` would silently stop raising for a bare `None`, and no
    behavioural test would notice, because the path is unreachable today.

    So the invariant is pinned MECHANICALLY, on the source, rather than left as
    a sentence in a doc-comment (#1859: a pin that cannot go red is decorative).
    """

    def test_no_renderer_call_site_passes_a_none_context(self) -> None:
        import pathlib

        renderer = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_templates"
            / "src"
            / "renderer.rs"
        )
        lines = renderer.read_text(encoding="utf-8").splitlines()
        # Scanned by POSITION from each call's opening line rather than by a
        # `(.*?)\);` body match. The body regex is what a first pass used, and
        # it silently found only TWO of the three sites — `findall` does not
        # overlap, so one call's non-greedy body swallowed the next call's
        # opening line. It reported "n calls: 2", the `>= 2` floor passed, and
        # a gate-off mutation of the middle site changed nothing. A pin whose
        # own scan can miss a site is not a pin (#1859).
        opens = [i for i, ln in enumerate(lines) if "apply_filter_full_safe(" in ln]
        assert len(opens) == 3, (
            f"found {len(opens)} apply_filter_full_safe call sites in renderer.rs, "
            "expected 3 — if a site was added or removed, decide its context "
            "argument explicitly and update this count"
        )
        for at in opens:
            # The context is the FOURTH positional argument; take the next few
            # code lines and require it among them.
            args = [
                ln.strip().rstrip(",")
                for ln in lines[at + 1 : at + 6]
                if ln.strip() and not ln.strip().startswith("//")
            ]
            assert "Some(context)" in args, (
                f"the apply_filter_full_safe call at renderer.rs:{at + 1} does not "
                f"pass Some(context) — its first arguments are {args}.\n\n"
                "With a None context nothing resolves, so `int_arg_is_type_error` "
                "answers false and a bare `None` argument stops raising. Either "
                "pass a context, or restore the spelling fallback deleted in #2366."
            )


class TestAnAcceptedArgumentStillWorks:
    """The half a fix that raised unconditionally would have destroyed."""

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    @pytest.mark.parametrize("arg", sorted(ACCEPTED))
    def test_agrees_with_django(self, name: str, arg: str) -> None:
        if (name, arg) in OTHER_MECHANISM:
            pytest.skip(OTHER_MECHANISM[(name, arg)])
        src = "{{ p|%s:q }}" % name
        ctx = {"p": RETURN_INPUT[name], "q": ACCEPTED[arg]}
        d, r = both(src, ctx)
        assert not d.startswith("<<EXC"), f"Django raises for {arg}: {d!r} — wrong list"
        assert r == d, f"{src} with {arg}: django={d!r} djust={r!r}"

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    def test_an_unparseable_STRING_still_returns_the_input(self, name: str) -> None:
        """The `ValueError` half, which Django DOES catch.

        This is the row that separates the two exception types. Without it a
        fix that raised for every bad argument would look correct.
        """
        src = '{{ p|%s:"notanumber" }}' % name
        ctx = {"p": RETURN_INPUT[name]}
        d, r = both(src, ctx)
        assert not d.startswith("<<EXC"), f"Django raises for a bad STRING: {d!r}"
        assert r == d, f"{src}: django={d!r} djust={r!r}"

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    def test_a_QUOTED_None_is_the_string_and_takes_the_ValueError_path(self, name: str) -> None:
        """`int("None")` IS a ValueError, so the quoted spelling must NOT raise.

        One character of template syntax between an error frame and a render,
        and it is the same distinction `arg_was_quoted` carries elsewhere.
        """
        src = '{{ p|%s:"None" }}' % name
        ctx = {"p": RETURN_INPUT[name]}
        d, r = both(src, ctx)
        assert not d.startswith("<<EXC"), f"Django raises for a quoted None: {d!r}"
        assert r == d


class TestOneMechanismNotTwo:
    """#2328's bare-`None` predicate is subsumed, not shadowed.

    `None` resolves to `Value::None` since #2347, so the resolved-TYPE arm
    answers it; the SPELLING fallback exists only for call sites that resolve
    nothing. Both spellings are asserted so neither arm can be deleted without
    a red test.
    """

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    def test_the_bare_None_literal_still_raises(self, name: str) -> None:
        src = "{{ p|%s:None }}" % name
        ctx = {"p": RETURN_INPUT[name]}
        assert django_raises(src, ctx), f"Django no longer raises: {src}"
        assert djust_raises(src, ctx), f"{src} did not raise"

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    def test_a_bound_None_variable_raises_for_the_same_reason(self, name: str) -> None:
        """The resolved-TYPE arm, reached without the `None` spelling at all."""
        src = "{{ p|%s:q }}" % name
        ctx = {"p": RETURN_INPUT[name], "q": None}
        assert django_raises(src, ctx), f"Django no longer raises: {src}"
        assert djust_raises(src, ctx), f"{src} with a bound None did not raise"


# ===========================================================================
# The residue, located rather than shrugged at.
# ===========================================================================


class TestTheExtractionBoundaryResidueIsNamed:
    """A `datetime` argument still diverges — and NOT at the dispatch table.

    The type is gone before `Context::resolve` ever sees it. The measurement
    below is what locates the loss, and it is the reason the issue's proposed
    remedy ("the dispatch table learns the argument's original type") could not
    have closed its own headline case.
    """

    @pytest.mark.parametrize("name", sorted(RETURN_INPUT))
    @pytest.mark.parametrize("arg", sorted(STRINGIFIED_AT_EXTRACTION))
    def test_django_raises_and_djust_returns_its_input(self, name: str, arg: str) -> None:
        src = "{{ p|%s:q }}" % name
        ctx = {"p": RETURN_INPUT[name], "q": STRINGIFIED_AT_EXTRACTION[arg]}
        assert django_raises(src, ctx), f"Django no longer raises: {src} with {arg}"
        assert not djust_raises(src, ctx), (
            f"{src} with a {arg} argument now RAISES — if the extraction "
            "boundary learned the type, move this row into "
            "TestTheFourReturnInputFiltersNowRaise"
        )

    @pytest.mark.parametrize("arg", sorted(STRINGIFIED_AT_EXTRACTION))
    def test_the_type_is_gone_before_any_filter_sees_it(self, arg: str) -> None:
        """Where the loss happens, measured rather than asserted.

        Each of these renders as TEXT and answers `|length` with that text's
        LENGTH — a `datetime` is 19 characters, not an object. A list, by
        contrast, answers its element count. That difference is the whole
        boundary.
        """
        value = STRINGIFIED_AT_EXTRACTION[arg]
        rendered = _rust.render_template("{{ q }}", {"q": value})
        length = _rust.render_template("{{ q|length }}", {"q": value})
        assert length == str(len(rendered)), (
            f"{arg} answers |length with {length!r} for a {len(rendered)}-character "
            "rendering — it is no longer a string at the boundary, so this row "
            "may be fixable now"
        )

    @pytest.mark.parametrize("pair", sorted(OTHER_MECHANISM))
    def test_every_other_mechanism_row_is_still_divergent(self, pair) -> None:
        """Non-vacuity for `OTHER_MECHANISM`. A stale exclusion is a hole."""
        name, arg = pair
        src = "{{ p|%s:q }}" % name
        ctx = {"p": RETURN_INPUT[name], "q": ACCEPTED[arg]}
        assert django_raises(src, ctx) != djust_raises(src, ctx), (
            f"{src} with {arg} now agrees — remove its OTHER_MECHANISM row"
        )

    def test_a_list_is_NOT_stringified_which_is_why_it_was_fixable(self) -> None:
        """The control for the row above. Without it, "the type is lost at
        extraction" would be unfalsifiable — it would read as true of
        everything."""
        assert _rust.render_template("{{ q|length }}", {"q": [1, 2]}) == "2"
        assert _rust.render_template("{{ q }}", {"q": [1, 2]}) == "[1, 2]"


# ===========================================================================
# A randomised differential over the argument axis.
# ===========================================================================

_ALL_FILTERS = {**RETURN_INPUT, **ALREADY_RAISE}
_ALL_ARGS = {**TYPED_REFUSALS, **ACCEPTED, **STRINGIFIED_AT_EXTRACTION}


class TestARandomisedDifferentialOverTheArgumentAxis:
    """The raise BIT over every (filter, argument) pair, sampled and swept.

    Compares the bit rather than the text, because the exception types differ
    by construction and always have.
    """

    def test_every_pair_agrees_on_whether_it_raises(self) -> None:
        checked = 0
        raised = 0
        residue = 0
        mismatches: list[str] = []
        for name, value in _ALL_FILTERS.items():
            for argname, arg in _ALL_ARGS.items():
                src = "{{ p|%s:q }}" % name
                ctx = {"p": value, "q": arg}
                checked += 1
                d, r = django_raises(src, ctx), djust_raises(src, ctx)
                if d:
                    raised += 1
                if d == r:
                    continue
                if argname in STRINGIFIED_AT_EXTRACTION or (name, argname) in OTHER_MECHANISM:
                    residue += 1
                    continue
                mismatches.append(f"{src} with {argname}: django_raises={d} djust_raises={r}")
        assert checked == len(_ALL_FILTERS) * len(_ALL_ARGS)
        assert raised >= checked // 4, (
            f"only {raised} of {checked} pairs make Django raise — the sweep is "
            "not reaching the surface it claims to measure"
        )
        assert residue >= 4, (
            f"the extraction-boundary exclusion fired {residue} times — if it is "
            "0 the sweep never builds the shape it excuses"
        )
        assert not mismatches, f"{len(mismatches)} of {checked} pairs disagree:\n" + "\n".join(
            mismatches[:10]
        )

    def test_a_thousand_random_pairs_agree_on_their_OUTPUT_where_neither_raises(self) -> None:
        """The other half: when both render, they must render the same thing."""
        rng = random.Random(2366)
        names = sorted(_ALL_FILTERS)
        argnames = sorted(_ALL_ARGS)
        compared = 0
        mismatches: list[str] = []
        for _ in range(1000):
            name = rng.choice(names)
            argname = rng.choice(argnames)
            src = "{{ p|%s:q }}" % name
            ctx = {"p": _ALL_FILTERS[name], "q": _ALL_ARGS[argname]}
            d, r = both(src, ctx)
            if d.startswith("<<EXC") or r.startswith("<<EXC"):
                continue
            compared += 1
            if d != r:
                mismatches.append(f"{src} with {argname}: django={d!r} djust={r!r}")
        assert compared >= 200, (
            f"only {compared} of 1000 pairs rendered on both engines — the sweep "
            "is measuring exceptions rather than output"
        )
        assert not mismatches, (
            f"{len(mismatches)} of {compared} rendered pairs differ:\n" + "\n".join(mismatches[:10])
        )
