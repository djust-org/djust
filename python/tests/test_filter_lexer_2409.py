"""Django's filter-expression LEXER rule, at both djust sites (#2409).

The defect
----------
Django's variable lexer allows a filter **at most one** argument, and decides
that before any filter is looked up. ``filter_raw_string``'s argument group is
optional and NON-repeating, and ``FilterExpression.__init__`` requires the
regex matches to TILE the token, so a second ``:arg`` is what is left over::

    {{ p|cut:"a":"b" }}   django  <<TemplateSyntaxError: Could not parse the
                                    remainder: ':"b"' from 'p|cut:"a":"b"'>>
                          djust   'a b c'

djust split on the FIRST colon and kept everything after it as one argument, so
``cut`` was handed ``"a":"b"`` — quotes and all — found no such substring and
rendered the input unchanged. A wrong page, silently, from a template Django
refuses to compile.

This is not the arity check (#2400)
-----------------------------------
``args_check`` reads each filter's own signature and refuses a wrong COUNT.
This is one layer up and applies to EVERY filter, ``upper`` being the one cell
where the two are visible apart: djust folded two arguments into one and
#2400's check then refused that ONE argument for a filter that takes none, so
``upper`` agreed while every filter that legitimately takes one still diverged.

Quoting is the whole difficulty
-------------------------------
A "refuse a second colon" check gets ``{{ p|date:"H:i" }}`` and
``{{ p|cut:":" }}`` wrong, and both must keep parsing. The same blindness sat
one character over on the pipe — ``{{ p|cut:"a|b" }}`` was split into two
filters and raised ``Unknown filter`` where Django renders normally, djust
being STRICTER for once but wrong either way.

Two sites, one rule
-------------------
``parser::parse_filter_specs`` (for ``{{ … }}``) and
``renderer::get_value_safe`` (for a TAG operand) were independently
quote-blind and independently accepted a second argument, so a ``{{ }}``-only
fix would have left ``{% if %}``, ``{% for %}`` and ``{% with %}``
over-permissive. Both now call ``crate::filter_lexer`` (#1646).

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.template import TemplateSyntaxError
from django.template.base import FILTER_ARGUMENT_SEPARATOR, FILTER_SEPARATOR

from djust import _rust

CTX: dict[str, object] = {"p": "a b c", "q": "Q", "d": {"k": "v"}}

#: The four expression shapes, so a fix at one site cannot pass for a fix at
#: both. `{{ }}` goes through `parser::parse_filter_specs`; the other three
#: resolve their operand through `renderer::get_value_safe`.
SHAPES: dict[str, str] = {
    "var": "{{{{ {e} }}}}",
    "if": "{{% if {e} %}}Y{{% endif %}}",
    "for": "{{% for z in {e} %}}[{{{{ z }}}}]{{% endfor %}}",
    "with": "{{% with v={e} %}}{{{{ v }}}}{{% endwith %}}",
}


def django_renders(source: str) -> bool:
    """Does Django produce output for this template — compile AND render."""
    try:
        DjangoTemplate(source).render(DjangoContext(dict(CTX)))
    except Exception:  # noqa: BLE001 — any refusal is a refusal
        return False
    return True


def djust_renders(source: str) -> tuple[bool, str]:
    try:
        return True, _rust.render_template(source, dict(CTX))
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    except BaseException as exc:  # noqa: BLE001 — a Rust panic is not a refusal
        return False, f"PANIC {exc}"


# ---------------------------------------------------------------------------
# The bound itself, read off Django rather than asserted
# ---------------------------------------------------------------------------


class TestDjangosLexerBound:
    """One argument, and the probe says so rather than this docstring."""

    def test_django_accepts_one_argument_and_refuses_two(self) -> None:
        assert django_renders('{{ p|default:"x" }}')
        with pytest.raises(TemplateSyntaxError, match="Could not parse the remainder"):
            DjangoTemplate('{{ p|default:"x":"y" }}')

    def test_the_refusal_is_the_LEXER_not_the_signature(self) -> None:
        """``default`` takes exactly one argument, so a second one cannot be
        an ``args_check`` complaint — ``args_check`` never sees it."""
        with pytest.raises(TemplateSyntaxError) as excinfo:
            DjangoTemplate('{{ p|default:"x":"y" }}')
        assert "requires" not in str(excinfo.value), str(excinfo.value)


# ---------------------------------------------------------------------------
# The cells the issue names
# ---------------------------------------------------------------------------

#: Verbatim from #2409, plus the three-argument shape.
TWO_ARGUMENT_CELLS = [
    'p|cut:"a":"b"',
    'p|default:"a":"b"',
    'p|truncatewords:"1":"2"',
    'p|upper:"a":"b"',
    'p|add:"1":"2":"3"',
]


class TestATwoArgumentCallIsRefused:
    @pytest.mark.parametrize("expr", TWO_ARGUMENT_CELLS)
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_neither_engine_renders_it(self, expr: str, shape: str) -> None:
        source = SHAPES[shape].format(e=expr)
        assert not django_renders(source), "premise: Django must refuse this"
        rendered, out = djust_renders(source)
        assert not rendered, f"djust rendered {out!r}"
        assert not out.startswith("PANIC"), out

    @pytest.mark.parametrize("expr", TWO_ARGUMENT_CELLS)
    def test_the_refusal_never_puts_the_input_on_the_page(self, expr: str) -> None:
        rendered, out = djust_renders("{{ " + expr + " }}")
        assert not rendered
        assert "a b c" not in out, out

    def test_djust_quotes_djangos_own_remainder_wording(self) -> None:
        """Not the exception CLASS — every djust template error crosses as a
        ``RuntimeError``. The shared property is that the template does not
        render; the WORDING is pinned because it is what a developer reads."""
        _, out = djust_renders('{{ p|cut:"a":"b" }}')
        assert """Could not parse the remainder: ':"b"'""" in out, out

    def test_a_separator_with_nothing_after_it_is_refused(self) -> None:
        for shape in SHAPES.values():
            source = shape.format(e="p|default:")
            assert not django_renders(source)
            assert not djust_renders(source)[0], source


# ---------------------------------------------------------------------------
# The quoting that must keep working — the other half of the same fix
# ---------------------------------------------------------------------------

#: Arguments carrying a separator inside the quotes. Spelled from Django's own
#: two constants, so this list and the grammar cannot drift apart.
QUOTED_SEPARATOR_CELLS = {
    f'p|cut:"a{FILTER_SEPARATOR}b"': "a|b c",
    f'p|cut:"{FILTER_ARGUMENT_SEPARATOR}"': "a:b",
    f'p|default:"x{FILTER_SEPARATOR}y"': "",
    f'p|default:"x{FILTER_ARGUMENT_SEPARATOR}y"': "",
}


class TestAQuotedSeparatorIsPartOfTheArgument:
    @pytest.mark.parametrize("expr", sorted(QUOTED_SEPARATOR_CELLS))
    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_both_engines_render_it(self, expr: str, shape: str) -> None:
        ctx = dict(CTX, p=QUOTED_SEPARATOR_CELLS[expr])
        source = SHAPES[shape].format(e=expr)
        try:
            expected = DjangoTemplate(source).render(DjangoContext(dict(ctx)))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"premise: Django must render {source!r} — {exc}")
        assert _rust.render_template(source, dict(ctx)) == expected

    def test_the_pipe_inside_quotes_does_not_start_a_second_filter(self) -> None:
        """The pre-fix djust saw a filter named ``b"`` here."""
        rendered, out = djust_renders('{{ p|cut:"a|b" }}')
        assert rendered, out
        assert "Unknown filter" not in out

    def test_an_escaped_quote_does_not_end_the_constant(self) -> None:
        source = r'{{ p|cut:"a\"b" }}'
        assert _rust.render_template(source, dict(CTX)) == DjangoTemplate(source).render(
            DjangoContext(dict(CTX))
        )


# ---------------------------------------------------------------------------
# Both sites, separately reachable
# ---------------------------------------------------------------------------


class TestBothSitesRefuse:
    """Neither site's refusal can stand in for the other's.

    ``{{ }}`` is refused by ``parser::parse_filter_specs``; the three tag
    shapes by ``renderer::get_value_safe``. Gating either one off leaves the
    other's rows green, which is what makes this pair non-tautological.
    """

    def test_the_variable_site_refuses(self) -> None:
        assert not djust_renders('{{ p|cut:"a":"b" }}')[0]

    @pytest.mark.parametrize("shape", ["if", "for", "with"])
    def test_the_tag_operand_site_refuses(self, shape: str) -> None:
        assert not djust_renders(SHAPES[shape].format(e='p|cut:"a":"b"'))[0]

    def test_the_variable_site_refuses_a_node_that_never_renders(self) -> None:
        """Django refuses at COMPILE time, so a branch nothing takes still
        raises. djust's ``{{ }}`` site is a parse-time check and matches."""
        source = '{% if False %}{{ p|cut:"a":"b" }}{% endif %}'
        assert not django_renders(source)
        assert not djust_renders(source)[0]

    def test_both_sites_go_through_one_module(self) -> None:
        """The structural pin: a second copy of the rule is the drift this
        repo keeps retiring (#1646). Read from the AST-free source of the two
        call sites, excluding this file — a string search over the repo would
        find its own needle."""
        crates = pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src"
        for module in ("parser.rs", "renderer.rs"):
            source = (crates / module).read_text()
            assert (
                "filter_lexer::scan_filter_spec"
                if module == "parser.rs"
                else "filter_lexer::split_filter_spec"
            ) in source, module
            assert "filter_lexer::split_pipes" in source, module
        # And neither site kept a private copy of the split it replaced.
        assert "split('|')" not in (crates / "renderer.rs").read_text()
        assert "var.split('|')" not in (crates / "parser.rs").read_text()


# ---------------------------------------------------------------------------
# The corpus gap that hid this from the differential
# ---------------------------------------------------------------------------

DIFFERENTIAL = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
)


class TestTheCorpusGapThatHidThisFromTheDifferential:
    """``ARITY_COUNTS`` was ``(0, 1)`` and said so on purpose.

    Its comment read: djust cannot SPELL two arguments, so a two-argument cell
    would be measuring the lexer rather than the arity. Both halves were true
    and the conclusion was wrong — djust could not spell two arguments because
    it silently FOLDED them into one. Measuring the lexer is the point.
    """

    @staticmethod
    def _module():
        import importlib.util

        spec = importlib.util.spec_from_file_location("_fpd_2409", DIFFERENTIAL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_corpus_now_builds_a_two_argument_cell(self) -> None:
        module = self._module()
        assert 2 in module.ARITY_COUNTS
        spelled = {f"{name}:{provided}" for name, provided, _key in module.arity_cells()}
        assert any(cell.endswith(":2") for cell in spelled)

    def test_the_bound_is_probed_against_django_not_transcribed(self) -> None:
        module = self._module()
        assert module.django_lexer_max_arguments() == 1
        assert re.search(r"def django_lexer_max_arguments", DIFFERENTIAL.read_text()), (
            "the probe must live in the corpus, not here"
        )

    def test_every_filter_requires_a_two_argument_cell(self) -> None:
        """The lexer refuses two for EVERY filter, whatever its signature —
        so reading only the argspec would stop requiring the cell the day
        Django shipped a two-argument filter."""
        module = self._module()
        for name in module.register.filters:
            assert module.django_refuses_arity(name, 2), name

    def test_the_separator_axis_requires_both_of_djangos_separators(self) -> None:
        module = self._module()
        required = module._required_separators_in_constants()
        assert set(required) == {FILTER_SEPARATOR, FILTER_ARGUMENT_SEPARATOR}
        assert set(required) <= module._swept_separators_in_constants()

    def test_the_separator_axis_goes_red_when_the_spellings_lose_them(self) -> None:
        """The empirical canary: with the separator-carrying spellings removed,
        the axis reports the gap rather than reporting clean."""
        module = self._module()
        module.ARG_SPELLINGS = [
            s for s in module.ARG_SPELLINGS if FILTER_SEPARATOR not in s and ":" not in s
        ]
        assert module._swept_separators_in_constants() == set()
