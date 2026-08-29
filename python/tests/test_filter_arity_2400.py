"""A filter given the wrong ARGUMENT COUNT is refused, as Django refuses it (#2400).

The defect
----------
Django validates a filter's argument count in ``FilterExpression.__init__`` —
at COMPILE time, before any value is touched::

    def args_check(name, func, provided):
        plen = len(provided) + 1                       # the input is implied
        args, _, _, defaults, _, _, _ = getfullargspec(inspect.unwrap(func))
        if plen < (len(args) - len(defaults or [])) or plen > len(args):
            raise TemplateSyntaxError("%s requires %d arguments, %d provided" % …)

djust's dispatch read ``arg: Option<&str>`` and silently ignored or defaulted
it, so **48 of Django's 57 built-ins** rendered a template Django refuses::

    {{ p|upper:"x" }}   django  <<TemplateSyntaxError: upper requires 1 arguments, 2 provided>>
                        djust   'ABC'
    {{ p|default }}     django  <<TemplateSyntaxError: default requires 2 arguments, 1 provided>>
                        djust   'abc'

Over-permissive, on a dimension orthogonal to what any of them computes: a
typo in a template was silent here and loud there.

The issue's own count, corrected
--------------------------------
The issue says 28 built-ins "raise on an EXTRA argument … TemplateSyntaxError".
Measured: **23** do. The other five — ``linebreaks``, ``linebreaksbr``,
``linenumbers``, ``unordered_list``, ``urlize`` — are ``needs_autoescape=True``
with ``autoescape`` as their only other parameter, so ``args_check`` reads
``plen = 2 <= alen = 2`` and COMPILES; the failure is a **render-time
TypeError** from the call, ``got multiple values for argument 'autoescape'``.
48 is right; "TemplateSyntaxError" is right for 43 of them.

That is why the table carries two upper bounds and the fix has two sites: a
parse-time refusal for all five of those would refuse a template Django
accepts, which is the wrong answer in the other direction.

Every expectation here is LIVE Django, never a transcription.
"""

import inspect
import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template.defaultfilters import register
from django.utils.safestring import mark_safe

from djust import _rust

ARITY_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filter_arity.rs"
)

MARKED = mark_safe("<b>x</b>")
HOSTILE = "<script>alert(1)</script>"

#: The value shapes every sweep runs over. Small on purpose: the subject is the
#: argument COUNT, which no value can change — the payload rows are there to
#: assert a refusal never puts the input on the page.
VALUES: dict[str, object] = {
    "str": "abc",
    "empty": "",
    "int": 5,
    "list": ["a", "b", "c"],
    "dict": {"a": 1},
    "none": None,
    "marked": MARKED,
    "hostile": HOSTILE,
}


# ---------------------------------------------------------------------------
# Django's arity, re-derived — the requirement, never transcribed
# ---------------------------------------------------------------------------


def django_bounds(name: str) -> tuple[int, int, int]:
    """``(min, parse_max, call_max)`` on the number of TEMPLATE arguments.

    Read out of Django's own registry with the same call ``args_check`` makes,
    so the requirement cannot drift from the implementation it describes.

    * ``min`` / ``parse_max`` are ``args_check``'s two comparisons, restated in
      provided-argument terms (it counts the implied input, this does not).
    * ``call_max`` excludes ``autoescape``, which is supplied as a KEYWORD by
      ``FilterExpression.resolve`` — so a positional argument in that slot
      passes ``args_check`` and then raises ``TypeError`` at the call.
    """
    spec = inspect.getfullargspec(inspect.unwrap(register.filters[name]))
    args, dlen = spec.args, len(spec.defaults or [])
    has_autoescape = "autoescape" in args
    n_template = len(args) - 1 - (1 if has_autoescape else 0)
    d_template = max(0, dlen - (1 if has_autoescape else 0))
    return n_template - d_template, len(args) - 1, n_template


def django_refuses(name: str, provided: int) -> bool:
    lo, _parse_max, call_max = django_bounds(name)
    return provided < lo or provided > call_max


def django_refuses_at_compile_time(name: str, provided: int) -> bool:
    lo, parse_max, _call_max = django_bounds(name)
    return provided < lo or provided > parse_max


def rust_table() -> dict[str, tuple[int, int, int]]:
    """The `ARITY` const, parsed out of the Rust source.

    Asserts its own parse count against the row count in the literal, because a
    regex that matched nothing would report a clean table (#2135): a checker
    that silently reads zero rows is the failure mode this pin exists to be
    immune to.
    """
    source = ARITY_RS.read_text()
    body = source.split("const ARITY: &[(&str, u8, u8, u8)] = &[", 1)[1].split("\n];", 1)[0]
    rows = re.findall(r'\(\s*"(\w+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', body)
    assert len(rows) == body.count("("), (
        f"the ARITY regex parsed {len(rows)} rows out of {body.count('(')} — "
        "it is not reading the table it claims to check"
    )
    assert rows, "the ARITY table did not parse at all"
    return {name: (int(a), int(b), int(c)) for name, a, b, c in rows}


class TestTheTableIsDjangosOwnArity:
    """The Rust table is a transcription; this is what keeps it honest."""

    def test_the_table_covers_exactly_djangos_registry(self) -> None:
        assert set(rust_table()) == set(register.filters)

    @pytest.mark.parametrize("name", sorted(register.filters))
    def test_all_three_bounds_match_the_live_signature(self, name) -> None:
        assert rust_table()[name] == django_bounds(name), (
            f"{name}: the table disagrees with "
            f"{inspect.getfullargspec(inspect.unwrap(register.filters[name])).args}"
        )

    def test_the_parse_bound_is_args_checks_own_comparison(self) -> None:
        """Non-vacuity for ``django_bounds``: it must reproduce a real compile.

        Derived numbers are only worth anything if they predict what Django
        actually does, so every filter's two spellings are COMPILED and the
        prediction is checked against the outcome.
        """
        for name in sorted(register.filters):
            for provided in (0, 1):
                source = "{{ p|%s%s }}" % (name, ':"x"' if provided else "")
                try:
                    DjangoTemplate(source)
                except Exception as exc:  # noqa: BLE001
                    compiled = False
                    assert "requires" in str(exc), f"{source}: {exc}"
                else:
                    compiled = True
                assert compiled is not django_refuses_at_compile_time(name, provided), (
                    f"{source}: derived says refuse={django_refuses_at_compile_time(name, provided)}"
                )


class TestTheIssuesOwnCounts:
    """Recomputed rather than quoted — and one of them was wrong."""

    def test_forty_eight_of_fifty_seven_builtins_refuse_a_wrong_count(self) -> None:
        refused = {n for n in register.filters for p in (0, 1) if django_refuses(n, p)}
        assert len(register.filters) == 57
        assert len(refused) == 48

    def test_twenty_refuse_a_MISSING_argument(self) -> None:
        missing = {n for n in register.filters if django_refuses(n, 0)}
        assert len(missing) == 20

    def test_the_extra_argument_group_splits_23_compile_and_5_call(self) -> None:
        """The issue calls all 28 a ``TemplateSyntaxError``. Five are not."""
        extra = {n for n in register.filters if django_refuses(n, 1)}
        assert len(extra) == 28
        at_compile = {n for n in extra if django_refuses_at_compile_time(n, 1)}
        at_call = extra - at_compile
        assert len(at_compile) == 23
        assert at_call == {
            "linebreaks",
            "linebreaksbr",
            "linenumbers",
            "unordered_list",
            "urlize",
        }

    @pytest.mark.parametrize(
        "name", ["linebreaks", "linebreaksbr", "linenumbers", "unordered_list", "urlize"]
    )
    def test_those_five_COMPILE_in_django_and_raise_at_the_call(self, name) -> None:
        source = '{{ p|%s:"x" }}' % name
        DjangoTemplate(source)  # compiles — no TemplateSyntaxError
        with pytest.raises(TypeError, match="autoescape"):
            DjangoTemplate(source).render(DjangoContext({"p": "abc"}))


# ---------------------------------------------------------------------------
# Both engines refuse the same cells
# ---------------------------------------------------------------------------


def _django_refused(source: str, ctx: dict) -> bool:
    try:
        DjangoTemplate(source).render(DjangoContext(dict(ctx)))
    except Exception:  # noqa: BLE001 — compile OR render, both are a refusal
        return True
    return False


def _arity_message(name: str, provided: int) -> str:
    """Django's own wording, which djust reproduces verbatim."""
    lo, _parse_max, _call_max = django_bounds(name)
    return f"{name} requires {lo + 1} arguments, {provided + 1} provided"


def _is_arity_message(name: str, provided: int, out: str) -> bool:
    return _arity_message(name, provided) in out


def _djust_refused(source: str, ctx: dict) -> tuple[bool, str]:
    try:
        return False, _rust.render_template(source, ctx)
    except Exception as exc:  # noqa: BLE001
        return True, str(exc)
    except BaseException as exc:  # noqa: BLE001 — a PyO3 panic is not an Exception
        return True, f"PANIC {exc}"


class TestEveryWrongArityCellIsRefusedByBoth:
    @pytest.mark.parametrize("provided", [0, 1])
    @pytest.mark.parametrize("name", sorted(register.filters))
    def test_every_count_django_refuses_is_refused_here_too(self, name, provided) -> None:
        """The over-permissive direction, for all 48 x every value shape.

        Asserted one-directionally on purpose. "djust refuses iff Django
        refuses" would be the wrong claim: a LEGAL count can still refuse for a
        reason that is not arity — ``{{ p|center:"x" }}`` is ``int("x")`` in
        both engines, ``{{ "abc"|timesince }}`` reads ``value.year`` — and
        folding those in would make this test about four bugs at once. The
        other direction is :class:`TestALegalArityIsUntouched`, which asserts
        no legal count is refused with an ARITY message.
        """
        if not django_refuses(name, provided):
            pytest.skip("a legal count — the other direction")
        source = "{{ p|%s%s }}" % (name, ':"x"' if provided else "")
        for key, value in VALUES.items():
            refused, out = _djust_refused(source, {"p": value})
            assert refused, f"{source} over {key} rendered {out!r}; Django refuses it"
            assert _is_arity_message(name, provided, out), (
                f"{source} over {key} was refused, but not as an arity error: {out!r}"
            )

    def test_a_refusal_never_puts_the_input_on_the_page(self) -> None:
        """The over-permissive direction, and the message is checked too."""
        for name in sorted(register.filters):
            for provided in (0, 1):
                if not django_refuses(name, provided):
                    continue
                source = "{{ p|%s%s }}" % (name, ':"x"' if provided else "")
                refused, out = _djust_refused(source, {"p": HOSTILE})
                assert refused, source
                assert "<script>" not in out, f"{source} put the payload in its error: {out!r}"

    def test_the_message_is_djangos_own_wording_where_django_compiles_it(self) -> None:
        """Verbatim, so a reader can match the two engines' errors by text."""
        refused, out = _djust_refused('{{ p|upper:"x" }}', {"p": "abc"})
        assert refused
        assert "upper requires 1 arguments, 2 provided" in out
        with pytest.raises(Exception, match="upper requires 1 arguments, 2 provided"):
            DjangoTemplate('{{ p|upper:"x" }}')


class TestALegalArityIsUntouched:
    """The non-regression half: nothing legal started failing."""

    @pytest.mark.parametrize("provided", [0, 1])
    @pytest.mark.parametrize("name", sorted(register.filters))
    def test_a_legal_count_still_renders_or_fails_for_its_own_reason(self, name, provided) -> None:
        if django_refuses(name, provided):
            pytest.skip("the wrong-arity half — covered above")
        source = "{{ p|%s%s }}" % (name, ':"x"' if provided else "")
        for key, value in VALUES.items():
            refused, out = _djust_refused(source, {"p": value})
            if refused:
                assert not _is_arity_message(name, provided, out), (
                    f"{source} over {key} was refused as an ARITY error, but "
                    f"Django accepts this count: {out!r}"
                )

    def test_the_two_argument_taking_shapes_still_work(self) -> None:
        assert _rust.render_template('{{ p|default:"z" }}', {"p": ""}) == "z"
        assert _rust.render_template('{{ p|truncatewords:"1" }}', {"p": "a b c"}) == "a …"
        assert _rust.render_template("{{ p|upper }}", {"p": "abc"}) == "ABC"


# ---------------------------------------------------------------------------
# The two sites, and the test that goes red when only one of them is removed
# ---------------------------------------------------------------------------


class TestBothSitesRefuse:
    """Two mechanisms, each independently reachable (CLAUDE.md #2129/#2135).

    A parse-time check and a render-time check would shadow each other if every
    cell exercised both. These two do not:

    * ``{% if False %}{{ p|upper:"x" }}{% endif %}`` never RENDERS the node, so
      only a PARSE-time refusal can see it — and Django refuses it, because
      ``args_check`` runs while the template compiles;
    * ``{% if p|upper:"x" %}`` is a raw operand string at parse time — the
      parser stores the condition verbatim and ``renderer::get_value_safe``
      splits the pipes at render time — so only the RENDER-time check sees it.
    """

    UNRENDERED = '{% if False %}{{ p|upper:"x" }}{% endif %}'
    TAG_OPERAND = '{% if p|upper:"x" %}Y{% else %}N{% endif %}'
    #: The MISSING-argument half of the same site. Both bounds live in one
    #: function, and gating the `provided < lo` term off reddens NOTHING while
    #: every cell that exercises it also renders — the render-time site checks
    #: the same lower bound and shadows it (CLAUDE.md #2129/#2135). An
    #: unrendered node is the one shape only the parse site can see.
    UNRENDERED_MISSING = "{% if False %}{{ p|default }}{% endif %}"

    def test_django_refuses_a_node_that_never_renders(self) -> None:
        """The premise: this is a COMPILE-time error, not a render-time one."""
        assert _django_refused(self.UNRENDERED, {"p": "abc"})

    def test_djust_refuses_the_unrendered_node_too(self) -> None:
        """Goes red if the PARSE-time site is removed."""
        refused, out = _djust_refused(self.UNRENDERED, {"p": "abc"})
        assert refused, f"rendered {out!r} — the parse-time check did not fire"

    def test_djust_refuses_an_unrendered_node_MISSING_its_argument(self) -> None:
        """Goes red if the parse site stops checking the LOWER bound.

        Its sibling above covers the upper bound; without this one the lower
        bound has no test that distinguishes the two sites, because every other
        missing-argument cell renders and is caught by the render-time check.
        """
        assert _django_refused(self.UNRENDERED_MISSING, {"p": "abc"})
        refused, out = _djust_refused(self.UNRENDERED_MISSING, {"p": "abc"})
        assert refused, f"rendered {out!r} — the parse-time lower bound did not fire"
        assert "default requires 2 arguments, 1 provided" in out

    def test_django_refuses_a_tag_operand(self) -> None:
        assert _django_refused(self.TAG_OPERAND, {"p": "abc"})

    def test_djust_refuses_the_tag_operand_too(self) -> None:
        """Goes red if the RENDER-time site is removed."""
        refused, out = _djust_refused(self.TAG_OPERAND, {"p": "abc"})
        assert refused, f"rendered {out!r} — the render-time check did not fire"

    @pytest.mark.parametrize(
        "source",
        [
            '{% if p|upper:"x" %}Y{% endif %}',
            '{% for x in p|upper:"x" %}[{{ x }}]{% endfor %}',
            '{% with q=p|upper:"x" %}{{ q }}{% endwith %}',
            '{% firstof p|upper:"x" %}',
        ],
    )
    def test_every_tag_operand_shape_is_refused(self, source) -> None:
        """The four filter-expression tags #2355 swept, on this axis."""
        assert _django_refused(source, {"p": "abc"})
        refused, out = _djust_refused(source, {"p": "abc"})
        assert refused, f"{source} rendered {out!r}"

    def test_the_two_sites_take_DIFFERENT_bounds(self) -> None:
        """``urlize`` is the cell that proves one bound would be wrong.

        Django COMPILES ``{{ p|urlize:"x" }}`` — ``args_check`` counts
        ``autoescape`` as a slot — and raises ``TypeError`` when the call
        happens. A parse-time refusal here would refuse a template Django
        accepts, so the parse site must NOT use the call bound.
        """
        DjangoTemplate('{{ p|urlize:"x" }}')  # compiles in Django
        with pytest.raises(TypeError):
            DjangoTemplate('{{ p|urlize:"x" }}').render(DjangoContext({"p": "abc"}))
        # djust refuses at the call, and — the sharp half — an UNRENDERED
        # `urlize:"x"` is accepted by both engines, because the parse site is
        # not using the tighter bound.
        refused, _ = _djust_refused('{{ p|urlize:"x" }}', {"p": "abc"})
        assert refused
        assert not _django_refused('{% if False %}{{ p|urlize:"x" }}{% endif %}', {"p": "abc"})
        unrendered_refused, out = _djust_refused(
            '{% if False %}{{ p|urlize:"x" }}{% endif %}', {"p": "abc"}
        )
        assert not unrendered_refused, (
            f"the parse site used the CALL bound and refused a template Django compiles: {out!r}"
        )


# ---------------------------------------------------------------------------
# What is deliberately NOT checked
# ---------------------------------------------------------------------------


class TestCustomFiltersAreNotArityChecked:
    """Out of scope (#1079), and the reason is structural rather than a choice.

    Django arity-checks a project's own ``@register.filter`` too, from the same
    ``getfullargspec``. djust's parser runs in Rust and cannot introspect a
    Python signature — and a custom filter is registered after the templates
    that use it may already have been parsed. Refusing an unknown name would
    break every custom filter there is, so the table answers ``None`` for one.
    """

    @staticmethod
    def _register():
        def cf_arity_probe(value, arg=None):
            return f"[{value}:{arg}]"

        _rust.register_custom_filter("cf_arity_probe", cf_arity_probe, False, False)

    def test_a_custom_filter_takes_any_count_djust_can_spell(self) -> None:
        self._register()
        assert _rust.render_template("{{ p|cf_arity_probe }}", {"p": "a"}) == "[a:None]"
        assert _rust.render_template('{{ p|cf_arity_probe:"z" }}', {"p": "a"}) == "[a:z]"

    def test_an_unknown_filter_still_fails_at_RENDER_not_at_parse(self) -> None:
        """The pre-existing timing, unchanged: djust does not know the name yet."""
        refused, out = _djust_refused("{{ p|no_such_filter_anywhere }}", {"p": "a"})
        assert refused
        assert "Unknown filter" in out
        # …and an unrendered one is NOT refused, which is what "render-time"
        # means and is the pre-existing divergence from Django this PR does not
        # change (Django's `Invalid filter` is a TemplateSyntaxError).
        refused, _ = _djust_refused(
            "{% if False %}{{ p|no_such_filter_anywhere }}{% endif %}", {"p": "a"}
        )
        assert not refused


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """A corpus gap is silent by construction; this pins that it is closed.

    The reachability manifest reported ``0 MISSING`` on ten axes over ~345,000
    cells while all 48 of these were divergent, because no cell it built could
    have a wrong argument COUNT: ``cells()`` gives each argument-taking filter
    exactly one VALID argument from ``FILTER_ARGS`` and gives the rest none,
    and ``arg_cells()`` sweeps spellings of a single argument over the filters
    that take one. Neither can construct ``{{ p|upper:"x" }}``.
    """

    @staticmethod
    def _differential():
        import importlib.util

        path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "scripts"
            / "filter-parity-differential.py"
        )
        spec = importlib.util.spec_from_file_location("_arity_differential", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_corpus_now_builds_a_wrong_arity_cell_for_every_refused_pair(self) -> None:
        mod = self._differential()
        swept = {(name, provided) for name, provided, _key in mod.arity_cells()}
        required = {
            (name, provided)
            for name in register.filters
            for provided in (0, 1)
            if django_refuses(name, provided)
        }
        assert swept == required
        # 48 pairs, not 96: no built-in refuses BOTH counts — a filter whose
        # argument is required accepts one, and one whose signature has no
        # slot for it accepts none. The pair count and the FILTER count
        # coincide, which is why the issue's headline number is 48.
        assert len(required) == 48
        assert len({name for name, _ in required}) == 48

    def test_the_axis_is_declared_and_reports_no_missing_member(self) -> None:
        mod = self._differential()
        rows = {row["axis"]: row for row in mod.manifest()["axes"]}
        assert "arity" in rows, "the axis must be DECLARED, not merely swept"
        assert not rows["arity"]["missing"], rows["arity"]["missing"]
        assert rows["arity"]["required"], "the requirement set is empty — it did not compute"

    def test_the_requirement_is_read_out_of_DJANGO_not_out_of_djust(self) -> None:
        """Otherwise the omission would make itself satisfiable (#2404's rule).

        Asserted by source-grep rather than by behaviour, because the whole
        point is WHERE the number comes from: a requirement derived from
        `filter_arity.rs` would agree with the table by construction and could
        never report the table wrong.
        """
        mod = self._differential()
        body = "".join(
            inspect.getsource(fn)
            for fn in (mod._required_arities, mod.django_refuses_arity, mod.django_arity_bounds)
        )
        assert "getfullargspec" in body and "register.filters" in body
        # Stated as "it does not READ the Rust source", which is the property:
        # a prose mention of the module is fine, a `read_text` of it is not.
        for reader in ("_crate_source", "read_text", "_rust_const", "open("):
            assert reader not in body, (
                f"the arity requirement reaches djust's own table via {reader!r} — "
                "it would be satisfied by the very omission it exists to detect"
            )
