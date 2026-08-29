"""``True`` / ``False`` / ``None`` are context BUILTINS, so they resolve (#2347).

The bug
-------
``django.template.context.builtins`` is
``[{"True": True, "False": False, "None": None}]``, added to every Django
``Context`` at ``dicts[0]``. So the three names are not literals — they RESOLVE
through the ordinary context lookup, and ``{{ True }}`` renders ``True``.

djust's ``Context`` did not carry them, so ``{{ True }}`` rendered the empty
string. Two resolvers can reach a bare name and only one of them knew
(#1646): ``renderer::get_value_safe`` carried inline ``True``/``False``/``None``
arms, which is why ``{% if True %}`` and ``{% firstof None False True %}`` were
always right, while ``Context::resolve`` — the resolver ``{{ }}`` output, the
built-in filter-argument channel and the custom-filter argument channel all use
— had no arm at all.

Both now go through one ``djust_core::context::template_builtin``.

Scope, measured rather than assumed
-----------------------------------
The issue's title reads as if the whole comparison surface were affected. It is
not: ``{% if x == True %}``, ``{% if x == None %}`` and ``{% if x is True %}``
all agreed with Django before this change and still do, because the ``{% if %}``
operand path is ``get_value_safe``. :class:`TestTheHalfThatWasAlreadyRight` pins
that — it is the non-regression half of converging the two resolvers, and it is
the half a fix could silently break.

The issue's REMEDY was also incomplete, and this was measured, not reasoned
--------------------------------------------------------------------------
The issue predicted that resolving the builtins would make ``python_int_arg``'s
``"True" => 1`` coercion redundant. It does not. The built-in filter-argument
channel is ``Option<&str>``: ``apply_filter_full_safe`` resolves the bare name
and then calls ``.to_string()`` on the result, so ``Value::Bool(true)`` becomes
the text ``"True"`` again and every built-in still sees text. Measured across
every built-in x {True, False, None} x five value shapes: **69 divergent cells
before the resolve fix, 69 after.** Only the CUSTOM-filter channel
(``filter_registry``, which hands the resolved value to Python through
``into_pyobject``) receives a real ``bool``.

So the coercion stays, and the actual argument-side defect was one filter:
``add`` has its own ``int()`` (``int_digits_of``, arbitrary-precision because a
sum past ``i64`` used to saturate, #2253/#2260) and so never reached #2328's
``python_int_arg`` chokepoint, where the bool rule lived. Two ``int()``s, one of
which knew the rule. Both now call ``bare_bool_arg_as_int``.

And only that one, decided by a CONTROL rather than by reading the diffs.

Two controls, one idea
----------------------
Every divergence this file meets is classified by asking whether the bare
SPELLING is what caused it:

* ``_is_about_the_literal`` replaces the literal with a context variable bound
  to the same Python object. Both spellings put the identical value in front of
  the identical filter, so if the control diverges too, the spelling is not the
  cause. Used by the two sweeps.
* :class:`TestOnlyAddWasBrokenByTheBareLiteral` uses the NUMERIC control — the
  same cell with ``1``/``0`` in place of ``True``/``False`` — because on the
  ARGUMENT axis the question is specifically "would an ordinary integer
  argument work here?", which a bound bool cannot ask.

Both are mechanical, so neither can go stale the way a hand-maintained list of
excluded filter names does — and a name-level list could not have expressed
``pluralize`` at all, which diverges on ``True`` and ``None`` and AGREES on
``False``.

What this PR leaves divergent, and why
--------------------------------------
Seven cells are UNMASKED by this fix — six ``date``/``time`` and one
``{{ None|add:"1" }}``. They agreed before only because both engines rendered
``""`` for unrelated reasons (Django because a bool is not a date / because
``add``'s third branch returns ``""``; djust because the name did not resolve).
Every one has a bound control that diverges identically, so each is a
pre-existing djust choice — "return the value unchanged rather than silently
empty" — becoming reachable by a second spelling, not a new defect.
:class:`TestKnownPreExistingDivergencesNotFixedHere` enumerates them and asserts
each is STILL divergent, so a later fix makes this file red rather than leaving
a stale exclusion behind (#1079).
"""

from __future__ import annotations

import random
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402

from djust import _rust  # noqa: E402

_BUILTINS = ["True", "False", "None"]

#: Time- or randomness-dependent: not comparable between two renders.
_NONDETERMINISTIC = {"random", "timesince", "timeuntil"}


def _render_both(source: str, ctx: dict[str, Any] | None = None) -> tuple[str, str]:
    ctx = {"p": 5} if ctx is None else ctx
    return (
        DjangoTemplate(source).render(DjangoContext(dict(ctx))),
        _rust.render_template(source, dict(ctx)),
    )


def _assert_agrees(source: str, ctx: dict[str, Any] | None = None) -> None:
    django_out, djust_out = _render_both(source, ctx)
    assert djust_out == django_out, (
        f"{source} on {ctx!r}: django={django_out!r} djust={djust_out!r}"
    )


_OBJECTS = {"True": True, "False": False, "None": None}


def _is_about_the_literal(source: str, name: str, ctx: dict[str, Any]) -> bool:
    """Is this cell's divergence caused by the bare SPELLING, or by the value?

    The control replaces the literal ``name`` with a context variable bound to
    the same Python object. Both spellings put the identical object in front of
    the identical filter, so if the control diverges too, the spelling is not
    what is wrong and the cell belongs to a different bug (#1079).

    This is a mechanical control rather than a list of excluded filter names: a
    list has to be maintained, goes stale silently when the underlying filter is
    fixed, and — the sharper failure — cannot distinguish per-VALUE cases.
    ``pluralize`` is exactly that: it diverges on ``True`` and ``None`` and
    agrees on ``False``, so any name-level exclusion is wrong in one direction
    or the other.
    """
    probe = "__djust_2347_control"
    control_src = source.replace(name, probe)
    control_ctx = dict(ctx, **{probe: _OBJECTS[name]})
    try:
        django_out = DjangoTemplate(control_src).render(DjangoContext(dict(control_ctx)))
    except Exception:  # noqa: BLE001
        return False
    try:
        djust_out = _rust.render_template(control_src, dict(control_ctx))
    except Exception:  # noqa: BLE001
        return False
    # The control AGREES -> the bare spelling is the only difference -> it IS
    # about the literal, and the cell is this issue's.
    return djust_out == django_out


class TestTheValuePosition:
    """The half that was broken: a bare builtin as the value."""

    @pytest.mark.parametrize("name", _BUILTINS)
    def test_bare_builtin_renders_its_python_repr(self, name: str) -> None:
        django_out, djust_out = _render_both("{{ %s }}" % name)
        assert django_out == name, "Django's own answer changed; re-derive"
        assert djust_out == django_out

    @pytest.mark.parametrize("name", _BUILTINS)
    def test_every_builtin_filter_agrees_on_a_bare_builtin_value(self, name: str) -> None:
        """The divergence COMPOSES, so the fix has to reach through a filter.

        ``{{ True|yesno }}`` answered ``maybe`` — the ``Missing`` answer — where
        Django says ``yes``. A fix that special-cased only the bare form would
        leave every one of these.
        """
        mismatched, value_side, swept = [], 0, 0
        for filter_name in sorted(register.filters):
            if filter_name in _NONDETERMINISTIC:
                continue
            source = "{{ %s|%s }}" % (name, filter_name)
            try:
                django_out = DjangoTemplate(source).render(DjangoContext({}))
            except Exception:  # noqa: BLE001 — arity raises are a different bug
                continue
            djust_out = _rust.render_template(source, {})
            swept += 1
            if djust_out == django_out:
                continue
            if _is_about_the_literal(source, name, {}):
                mismatched.append(f"{source}: django={django_out!r} djust={djust_out!r}")
            else:
                value_side += 1
        # Django raises `TemplateSyntaxError` for every filter that REQUIRES an
        # argument when it is called bare, so only the no-argument half of the
        # registry is comparable in this shape. The argument half is swept by
        # `TestTheArgumentChannel` and by the differential's argument axis.
        assert swept >= 20, f"the sweep collapsed to {swept} filters"
        assert not mismatched, "\n".join(mismatched)
        # The value-side cells are real divergences, just not this issue's — see
        # `TestKnownPreExistingDivergencesNotFixedHere`. Asserted non-zero so a
        # change that makes the control stop discriminating is visible rather
        # than silently turning this into a weaker test.
        assert value_side > 0, (
            "no cell was classified value-side; either every filter now agrees "
            "(shrink the known list) or `_is_about_the_literal` stopped working"
        )

    def test_lowercase_spellings_are_not_builtins_in_a_value_position(self) -> None:
        """Django has no ``true`` builtin, and neither does this fix.

        ``get_value_safe`` accepts the lowercase spellings as a djust extension
        in TAG operands; ``template_builtin`` is exactly the Django set, so
        ``{{ true }}`` stays an undefined variable and renders empty — which is
        what Django does.
        """
        for name in ("true", "false", "none"):
            _assert_agrees("{{ %s }}" % name)


class TestUserVariablesShadowTheBuiltins:
    """Position is the whole of the semantics.

    ``builtins`` is ``Context.dicts[0]`` and ``__getitem__`` walks
    ``reversed(self.dicts)``, so a context key named ``True`` wins. The fallback
    therefore runs only where resolution used to answer nothing — which is what
    bounds this change to the cells that rendered empty.
    """

    @pytest.mark.parametrize("name", _BUILTINS)
    def test_a_context_key_of_the_same_name_wins(self, name: str) -> None:
        _assert_agrees("{{ %s }}" % name, {name: "shadowed", "p": 5})

    @pytest.mark.parametrize("name", _BUILTINS)
    def test_shadowing_reaches_the_filter_argument_channel_too(self, name: str) -> None:
        _assert_agrees("{{ p|add:%s }}" % name, {name: 7, "p": 5})

    def test_a_shadowing_value_is_not_coerced_to_a_bool(self) -> None:
        """The shadow must be the USER's value, not the builtin's."""
        _, djust_out = _render_both("{{ True }}", {"True": [1, 2], "p": 5})
        assert djust_out != "True"


class TestTheHalfThatWasAlreadyRight:
    """Non-regression for the resolver that already knew (#1646).

    ``get_value_safe`` answered these correctly before the fix. Converging it
    onto ``template_builtin`` must not change a single one — this is the half a
    convergence silently breaks.
    """

    @pytest.mark.parametrize("name", _BUILTINS)
    @pytest.mark.parametrize(
        "shape",
        [
            "{%% if %s %%}Y{%% else %%}N{%% endif %%}",
            "{%% with q=%s %%}[{{ q }}]{%% endwith %%}",
            # `{% for x in True %}` is deliberately absent: Django RAISES
            # `TypeError` there (a 500), and djust renders the `{% empty %}`
            # branch. That divergence is pre-existing on both spellings — a
            # bound `p=True` diverges identically — and belongs to the
            # iterate-a-non-iterable question, not to this one. Pinned in
            # `TestKnownPreExistingDivergencesNotFixedHere`.
            "{%% if p == %s %%}Y{%% else %%}N{%% endif %%}",
            "{%% if p != %s %%}Y{%% else %%}N{%% endif %%}",
            "{%% if p is %s %%}Y{%% else %%}N{%% endif %%}",
            "{%% if p is not %s %%}Y{%% else %%}N{%% endif %%}",
        ],
    )
    @pytest.mark.parametrize("value", [True, False, None, 5, "", [1]])
    def test_tag_operands_agree(self, name: str, shape: str, value: Any) -> None:
        _assert_agrees(shape % name, {"p": value})

    def test_firstof_still_picks_the_first_truthy_builtin(self) -> None:
        _assert_agrees("{% firstof None False True %}")

    def test_the_lowercase_tag_extension_is_unchanged(self) -> None:
        """A djust extension, deliberately kept out of ``template_builtin``.

        Not Django parity — Django reads ``true`` as an undefined variable — so
        this asserts djust's OWN historical answer rather than Django's. Pinned
        so that moving the Django set into a shared helper does not quietly
        take the extension with it.
        """
        assert _rust.render_template("{% if true %}Y{% else %}N{% endif %}", {}) == "Y"
        assert _rust.render_template("{% if false %}Y{% else %}N{% endif %}", {}) == "N"
        assert _rust.render_template("{% if none %}Y{% else %}N{% endif %}", {}) == "N"


class TestTheArgumentChannel:
    """``add`` was the one filter the bare literal actually broke."""

    def test_add_reads_a_bare_true_as_the_integer_one(self) -> None:
        django_out, djust_out = _render_both("{{ p|add:True }}", {"p": 5})
        assert django_out == "6", "Django's own answer changed; re-derive"
        assert djust_out == django_out

    def test_add_reads_a_bare_false_as_zero(self) -> None:
        _assert_agrees("{{ p|add:False }}", {"p": 5})

    def test_a_quoted_true_is_a_string_and_int_raises_on_it(self) -> None:
        """``int("True")`` raises, so Django CONCATENATES. The rule is
        arg-was-quoted, and without that half a quoted argument would be read
        as a bool too."""
        _assert_agrees('{{ p|add:"True" }}', {"p": "ab"})
        _assert_agrees("{{ p|add:'False' }}", {"p": "ab"})

    def test_the_chokepoint_still_reads_the_bool(self) -> None:
        """``python_int_arg``'s callers, the OTHER user of the shared rule.

        If the helper were wired into ``add`` alone, these would go back to
        being wrong — which is the shape #2328 fixed and this must not undo.
        """
        for source in (
            "{{ p|center:True }}",
            "{{ p|ljust:True }}",
            "{{ p|truncatechars:True }}",
            "{{ p|slice:True }}",
        ):
            _assert_agrees(source, {"p": "ab"})

    def test_a_custom_filter_receives_the_real_python_bool(self) -> None:
        """The one channel that DOES get the type.

        ``filter_registry`` hands the resolved value to Python via
        ``into_pyobject``, so a project's own filter sees ``True`` and not the
        text — which is the half of this fix no built-in cell can observe.
        """
        seen: list[Any] = []

        def probe(value, arg):
            seen.append(arg)
            return ""

        _rust.register_custom_filter("djust_2347_probe", probe)
        try:
            for name, expected in (("True", True), ("False", False), ("None", None)):
                seen.clear()
                _rust.render_template("{{ p|djust_2347_probe:%s }}" % name, {"p": 5})
                assert seen == [expected], f"{name}: filter received {seen!r}"
                assert type(seen[0]) is type(expected), (
                    f"{name}: received {type(seen[0]).__name__}, not "
                    f"{type(expected).__name__} — the text leaked through"
                )
        finally:
            _rust.unregister_custom_filter("djust_2347_probe")


class TestKnownPreExistingDivergencesNotFixedHere:
    """Every cell this PR leaves divergent, and the proof each is not its own.

    Self-naming: each assertion asserts the divergence STILL EXISTS, so when the
    underlying filter is fixed this class goes red and tells the author to
    shrink ``_PRE_EXISTING`` rather than leaving a stale exclusion behind. That
    is what keeps the sweeps above from quietly stopping to cover a filter.
    """

    #: (filter, literal) pairs measured as divergent AFTER this fix, each with
    #: the reason it belongs to a different issue. Per-VALUE and not per-name,
    #: because `pluralize` diverges on `True` and `None` and AGREES on `False`
    #: — a name-level list would be wrong in one direction or the other.
    _KNOWN = [
        # The SIX `date`/`time` rows and the two `pluralize` rows lived here
        # until #2359 closed all three mechanisms; `add:"1"` on `None` went
        # with them. They were the cells this PR unmasked — they had agreed
        # only because both engines rendered "" for unrelated reasons, Django
        # because the value is not a date and djust because the NAME did not
        # resolve — and this class's own message is what told #2359's author
        # to shrink the list rather than leave a stale exclusion:
        # "now AGREES - drop this row from _KNOWN and let the sweeps cover it".
        #
        # djust stamps a default `id="data"`; Django emits no id without an
        # argument. Nothing to do with the literal at all.
        ("json_script", "True"),
        ("json_script", "False"),
        ("json_script", "None"),
    ]

    @pytest.mark.parametrize("filter_name,name", _KNOWN)
    def test_the_bound_control_diverges_identically(self, filter_name: str, name: str) -> None:
        """The proof that the LITERAL is not what is wrong.

        Binding a variable to the same Python object puts the identical value in
        front of the identical filter. If that diverges too, the bare spelling
        is not the cause.
        """
        source = "{{ %s|%s }}" % (name, filter_name)
        django_out = DjangoTemplate(source).render(DjangoContext({}))
        djust_out = _rust.render_template(source, {})
        assert djust_out != django_out, (
            f"{source} now AGREES — drop this row from _KNOWN and let the sweeps cover it"
        )
        assert not _is_about_the_literal(source, name, {}), (
            f"{source}: the bound control now AGREES, so this IS about the "
            "literal after all and needs fixing here rather than listing"
        )

    def test_iterating_a_bool_is_a_pre_existing_divergence(self) -> None:
        """`{% for x in True %}`: Django 500s, djust renders `{% empty %}`.

        Excluded from :class:`TestTheHalfThatWasAlreadyRight`'s shapes; pinned
        here with the bound control that shows it is not about the literal.

        djust being MORE permissive than Django (a render where Django raises)
        is the direction that matters, so this is deliberately loud rather than
        skipped — but it is unchanged by this PR in both spellings.
        """
        # `.replace`, not `%`: the body holds `{{ x }}` and `%`-formatting reads
        # a Django tag body as conversion specifiers.
        loop = "{% for x in @OP@ %}[{{ x }}]{% empty %}E{% endfor %}"
        bare = loop.replace("@OP@", "True")
        with pytest.raises(TypeError):
            DjangoTemplate(bare).render(DjangoContext({}))
        assert _rust.render_template(bare, {}) == "E"
        # The bound control: same object, same divergence, so the literal is
        # not what is wrong.
        bound = loop.replace("@OP@", "q")
        with pytest.raises(TypeError):
            DjangoTemplate(bound).render(DjangoContext({"q": True}))
        assert _rust.render_template(bound, {"q": True}) == "E"

    def test_iterating_none_is_the_one_that_agrees(self) -> None:
        """`None` is NOT in the same boat, which is why the pin names bools.

        Django's `{% for %}` resolves an unresolvable/`None` operand to the
        empty branch rather than raising, so this cell agrees on both engines —
        and a fix for the bool case must not disturb it.
        """
        _assert_agrees("{% for x in None %}[{{ x }}]{% empty %}E{% endfor %}", {})


class TestOnlyAddWasBrokenByTheBareLiteral:
    """Every other argument-axis divergence, against its NUMERIC control.

    The control is the same cell with ``1`` / ``0`` in place of
    ``True`` / ``False``. If the control diverges identically, the bare literal
    is not what is wrong and the cell belongs to a different bug — which is how
    the scope of this PR was decided, and what keeps a future reader from
    "fixing" #2347 again for a cell it never owned.
    """

    # (filter, bool spelling, the numeric spelling that means the same thing,
    #  the value). Each row is a cell measured as divergent on the argument
    #  axis before this change.
    _ROWS = [
        ("divisibleby", "True", "1", 1.5),
        ("dictsort", "False", "0", "ab"),
        ("pluralize", "True", "1", 5),
        # `get_digit` was here until #2403, and it left the same way
        # `stringformat`, `date` and `time` did: the numeric control
        # `{{ 1.5|get_digit:0 }}` diverged because djust's `arg < 1` exit
        # returned the value UNCONVERTED, where Django's `value = int(value)`
        # has already run — Django says `1`, djust said `1.5`. So the cell
        # belonged to that bug, not to the bare-literal one this class tests.
        # With it closed, BOTH spellings agree (measured: `get_digit:False`
        # and `get_digit:0` are `1` on both engines over `1.5`), so the row
        # has nothing left to assert.
        # `stringformat` was here until #2358. Its numeric control diverged
        # because the catch-all arm echoed the value for `"1"` — a spec
        # CPython answers `incomplete format` to — so the cell belonged to
        # that bug and not to #2347. With the grammar in place both
        # spellings render `""` on both engines and the row has nothing left
        # to assert, which is what this class's own failure message told the
        # #2358 author to do: "Re-measure and either fix it or move this row."
        #
        # `date` and `time` went the same way in #2359, and for the same
        # reason one level over: their numeric controls `date:"1"` and
        # `time:"1"` diverged because a format string carrying no specifier
        # renders its LITERAL text in Django, and djust echoed the value
        # instead. With that closed both controls agree. `time:True` agrees
        # outright now; `{{ p|date:True }}` still diverges (Django raises
        # `TypeError` from `get_format(True)`, djust renders `""`) but for the
        # ARGUMENT-TYPE reason #2366 is about, not the bare-literal reason
        # this class tests — the argument reaches the dispatch table as the
        # STRING `"True"`, whose characters are all `date` specifiers. Pinned
        # in `test_bool_and_none_values_2359.py::
        # TestTheArgumentTypeResidueIsNamed`.
    ]

    @pytest.mark.parametrize("name,boolarg,numarg,value", _ROWS)
    def test_the_numeric_control_diverges_the_same_way(
        self, name: str, boolarg: str, numarg: str, value: Any
    ) -> None:
        def outcome(arg: str) -> tuple[str, str]:
            source = "{{ p|%s:%s }}" % (name, arg)
            try:
                dj = DjangoTemplate(source).render(DjangoContext({"p": value}))
            except Exception as exc:  # noqa: BLE001
                dj = "<%s>" % type(exc).__name__
            try:
                du = _rust.render_template(source, {"p": value})
            except Exception as exc:  # noqa: BLE001
                du = "<%s>" % type(exc).__name__
            return dj, du

        dj_num, du_num = outcome(numarg)
        assert dj_num != du_num, (
            f"{name}:{numarg} now AGREES — the other bug was fixed, so "
            f"{name}:{boolarg} may now be a genuine #2347 cell. Re-measure and "
            "either fix it or move this row."
        )


class TestRandomisedDifferential:
    """Not a curated table: Django is imported here, so ask it.

    The builtins are swept as VALUES, as ARGUMENTS and as TAG OPERANDS against
    randomly composed filter chains and value shapes.
    """

    _VALUES = [
        5,
        -7,
        0,
        "ab",
        "",
        "中文",
        "<b>",
        [1, 2],
        [],
        (1, 2),
        {"a": 1},
        None,
        True,
        False,
        1.5,
        "  a b  ",
    ]

    def test_randomised_sweep_against_django(self) -> None:
        rng = random.Random(2347)
        names = [f for f in sorted(register.filters) if f not in _NONDETERMINISTIC]
        mismatched = []
        checked = 0
        value_side = 0
        for _ in range(3000):
            lit = rng.choice(_BUILTINS)
            value = rng.choice(self._VALUES)
            source = rng.choice(
                [
                    "{{ %s }}" % lit,
                    "{{ %s|%s }}" % (lit, rng.choice(names)),
                    "{{ p|add:%s }}" % lit,
                    "{%% if p == %s %%}Y{%% else %%}N{%% endif %%}" % lit,
                    "{%% if p is %s %%}Y{%% else %%}N{%% endif %%}" % lit,
                    "{%% if %s %%}Y{%% else %%}N{%% endif %%}" % lit,
                    "{%% with q=%s %%}[{{ q }}]{%% endwith %%}" % lit,
                    "{%% firstof %s p %%}" % lit,
                ]
            )
            try:
                django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
            except Exception:  # noqa: BLE001 — Django raising is a different question
                continue
            try:
                djust_out = _rust.render_template(source, {"p": value})
            except Exception as exc:  # noqa: BLE001
                mismatched.append(f"{source} on {value!r}: djust RAISED {exc}")
                continue
            checked += 1
            if djust_out == django_out:
                continue
            # The bound control decides whether this cell is about the bare
            # SPELLING (this issue's) or about the VALUE (someone else's).
            if _is_about_the_literal(source, lit, {"p": value}):
                mismatched.append(
                    f"{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
                )
            else:
                value_side += 1
        assert checked > 1500, f"corpus collapsed to {checked} comparable cells"
        assert not mismatched, "\n".join(sorted(set(mismatched))[:25])
        assert value_side > 0, (
            "no cell was classified value-side — either everything agrees now "
            "or `_is_about_the_literal` stopped discriminating"
        )


class TestOneStatementOfEachRule:
    """Source pins: both rules have exactly the callers they claim (#1125)."""

    @staticmethod
    def _read(path: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / path).read_text()

    def test_exactly_one_place_maps_the_three_names(self) -> None:
        """One mechanism, not two — the shadowing the gate-off found.

        ``get_value_safe`` used to spell the three names inline. With them in
        ``Context::resolve`` and ``get_value_safe`` already ending in a
        ``context.resolve(expr)`` fallback, an arm there was a second mechanism
        that no behavioural test could distinguish from the first: gating it off
        reddened only a source pin (#2129/#2135). It was deleted rather than
        tested around (#2233).

        The LOWERCASE spellings are a djust extension and legitimately live in
        the renderer, so the pin is on the capitalised set.
        """
        core = self._read("crates/djust_core/src/context.rs")
        renderer = self._read("crates/djust_templates/src/renderer.rs")
        assert "pub fn template_builtin(" in core
        assert core.count("template_builtin(key)") == 1, (
            "Context::resolve should consult the helper exactly once"
        )
        # The renderer must not re-derive the mapping. `get_value_safe`'s
        # surroundings are checked rather than the whole file, since `"True"`
        # appears legitimately elsewhere (component attribute parsing).
        start = renderer.index("fn get_value_safe(")
        body = renderer[start : renderer.index("\nfn ", start + 10)]
        for spelling in ('"True" =>', '"True" ==', 'expr == "True"'):
            assert spelling not in body, (
                f"get_value_safe re-derives the builtin mapping ({spelling!r}); "
                "there must be exactly one place that maps these names (#1646)"
            )
        assert '"true" =>' in body, "the lowercase djust extension should still live here"

    def test_the_bool_argument_rule_has_both_int_helpers_as_callers(self) -> None:
        """``python_int_arg`` AND ``add`` — the drift that broke ``add:True``."""
        filters = self._read("crates/djust_templates/src/filters.rs")
        callers = filters.count("bare_bool_arg_as_int(")
        # One definition plus two call sites.
        assert callers == 3, (
            f"bare_bool_arg_as_int appears {callers} times (expected 1 "
            "definition + 2 call sites: python_int_arg and the add arm)"
        )

    def test_the_literal_exemption_no_longer_lists_the_builtins(self) -> None:
        """The redundant mechanism was deleted, not left to shadow (#2233).

        With the names resolving, ``is_literal_filter_arg``'s ``True | False |
        None`` arm was unreachable — so no test could tell which of the two was
        doing the work.
        """
        filters = self._read("crates/djust_templates/src/filters.rs")
        start = filters.index("fn is_literal_filter_arg(")
        body = filters[start : start + 500]
        assert '"True" | "False" | "None"' not in body
