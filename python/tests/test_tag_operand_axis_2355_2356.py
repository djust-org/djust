"""The six unswept filter-expression tags (#2355) and the custom-tag path (#2356).

Both issues were surfaced by the reachability manifest #2345 added to
``scripts/filter-parity-differential.py``, on its first run, and both are the
same failure shape one register over: a mechanism the corpus could not
construct a cell for is a mechanism nothing measures.

#2355 — six tags took a filter-expression operand and were not swept
--------------------------------------------------------------------
``cycle`` / ``firstof`` / ``ifchanged`` / ``regroup`` / ``widthratio`` /
``filter``. #2325 fixed four operand channels and added ``for``/``with``/``if``
to the corpus; these six kept the exemption row ``"TAKES A FILTER-EXPRESSION
OPERAND and is not swept"`` — an admission, not a property. Sweeping them found
four divergences, three of them silent:

1. ``{% widthratio %}`` answered ``0`` for every NON-NUMERIC operand where
   Django answers ``""`` — 16,006 of the shape's 17,298 cells. It also rounded
   half-away-from-zero where Python's ``round`` is half-to-EVEN
   (``{% widthratio 1 2 5 %}`` was ``3``, Django's is ``2``), returned
   ``i64::MAX`` for an infinite ratio where Django returns ``""``, and answered
   ``0`` rather than raising when the final argument is not a number.
2. ``{% widthratio … as w %}`` and ``{% firstof … as v %}`` RENDERED the value
   Django assigns silently, and bound nothing — the ``as`` and the name were
   read as two more operands.
3. ``{% cycle nope 'z' %}`` echoed the unresolved operand's own SOURCE TEXT
   onto the page (``nope``); Django renders ``""``. That is the #2325 echo
   symptom in the one tag whose operands nothing built a cell for.
4. ``{% regroup p by k|upper as g %}`` dropped the ``by`` chain and grouped
   every row under ``None`` — one group where Django builds several, every
   ``{{ x.grouper }}`` empty. #2333 fixed the SOURCE operand; ``by`` is a
   filter expression too (Django compiles ``<var>.<attr>``).

``ifchanged`` and ``filter`` remain UNSUPPORTED by the Rust engine — every
cell is the same refusal, which is fail-closed and pinned below so a future
implementation cannot land unmeasured.

#2356 — nothing dispatched through the three custom-TAG registries
-------------------------------------------------------------------
``register_tag_handler`` / ``register_block_tag_handler`` /
``register_assign_tag_handler`` had been on the ``_rust`` module the whole
time, and every tag cell went through a tag the Rust engine implements
natively. The axis is now built, and what it reports is recorded here as a
KNOWN DIVERGENCE rather than fixed: djust inserts a handler's return value
RAW, where Django escapes a ``simple_tag`` return that lacks ``__html__``.
Fixing that means every one of djust's ~20 built-in handlers must
``mark_safe`` its HTML, which is its own change — tracked at #2379. These
tests pin the CURRENT behaviour so that change cannot land silently.

Method
------
Every expected value is measured against LIVE Django in this process, never
transcribed. The curated cells are doc-claim pins, one per sentence above.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("django")

from django import template as dj_template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.html import conditional_escape  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402

#: A live payload, so every cell doubles as a permissiveness probe.
XSS = "<img src=x onerror=alert(1)>"


def django_render(src: str, ctx: dict) -> str:
    # `dict(ctx)`: `Context(d)` keeps `d` as `dicts[-1]` and `{% … as v %}`
    # writes THERE, so a shared dict would let Django's assignment leak into
    # the djust render below and the two engines would not be comparing the
    # same input. This is the harness bug the `as`-form shapes exposed in
    # `scripts/filter-parity-differential.py::render_both` (#2355).
    return DjangoTemplate(src).render(DjangoContext(dict(ctx)))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, ctx)


def both(src: str, ctx: dict) -> tuple[str, str]:
    """Both engines, with a raise recorded as a comparable outcome."""
    try:
        d = django_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = djust_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# #2355 — {% widthratio %}
# ===========================================================================


class TestWidthRatio:
    """Django's `WidthRatioNode.render`, arm for arm."""

    @pytest.mark.parametrize(
        "value",
        ["abc", "", None, [1, 2], {"k": 1}, float("inf"), float("-inf"), float("nan")],
    )
    def test_a_non_numeric_value_renders_the_empty_string(self, value) -> None:
        # `float(value)` raises; Django catches and the result is `""`. djust
        # answered `0` for every one of these before #2355.
        assert django_render("{% widthratio p 10 100 %}", {"p": value}) == ""
        assert_agrees("{% widthratio p 10 100 %}", {"p": value})

    @pytest.mark.parametrize("max_value", ["abc", "", None, [1, 2]])
    def test_a_non_numeric_max_value_renders_the_empty_string(self, max_value) -> None:
        assert_agrees("{% widthratio 5 p 100 %}", {"p": max_value})

    def test_an_unresolvable_operand_renders_the_empty_string(self) -> None:
        assert django_render("{% widthratio nope 10 100 %}", {}) == ""
        assert_agrees("{% widthratio nope 10 100 %}", {})

    def test_a_zero_divisor_renders_zero(self) -> None:
        # ZeroDivisionError is caught SEPARATELY and answers "0", not "".
        assert django_render("{% widthratio 5 0 100 %}", {}) == "0"
        assert_agrees("{% widthratio 5 0 100 %}", {})
        assert_agrees("{% widthratio 5 p 100 %}", {"p": -0.0})

    @pytest.mark.parametrize(
        "src",
        [
            "{% widthratio 1 2 5 %}",  # ratio 2.5 — Python rounds to 2, Rust to 3
            "{% widthratio 1 4 10 %}",  # ratio 2.5
            "{% widthratio -1 2 5 %}",  # ratio -2.5 — Python -2, Rust -3
            "{% widthratio 3 2 5 %}",  # ratio 7.5 — both give 8
            "{% widthratio 1 2 3 %}",  # ratio 1.5 — Python 2
            "{% widthratio 1 8 4 %}",  # ratio 0.5 — Python 0
        ],
    )
    def test_the_half_case_rounds_to_even_as_pythons_round_does(self, src) -> None:
        assert_agrees(src, {})

    def test_a_negative_result_rounding_toward_zero_is_not_minus_zero(self) -> None:
        # `format!("{:.0}", -0.0)` is "-0"; Python's `str(round(-0.4))` is "0".
        assert_agrees("{% widthratio -1 100 10 %}", {})

    def test_a_bool_is_a_number_as_python_float_says(self) -> None:
        assert django_render("{% widthratio p 10 100 %}", {"p": True}) == "10"
        assert_agrees("{% widthratio p 10 100 %}", {"p": True})

    def test_a_numeric_string_is_a_number(self) -> None:
        assert_agrees("{% widthratio p 10 100 %}", {"p": "5"})
        assert_agrees("{% widthratio p 10 100 %}", {"p": " 5 "})

    def test_a_non_numeric_final_argument_raises_rather_than_answering_zero(self) -> None:
        # Django raises TemplateSyntaxError; djust raises a template error.
        # Both raise, which is the property that matters — a raise is contained
        # and falls back, an answer of `0` is silently wrong.
        with pytest.raises(Exception, match="widthratio final argument must be a number"):
            django_render("{% widthratio 5 10 p %}", {"p": "abc"})
        with pytest.raises(Exception, match="widthratio final argument must be a number"):
            djust_render("{% widthratio 5 10 p %}", {"p": "abc"})

    def test_a_float_LOOKING_STRING_final_argument_raises(self) -> None:
        # The case that separates `int()` from `float()`: Python's
        # `int("100.6")` raises ValueError while `float("100.6")` is 100.6.
        # Without it, a `py_int` that merely parsed a float would pass every
        # other cell here — `"abc"` fails both.
        with pytest.raises(Exception, match="widthratio final argument must be a number"):
            django_render("{% widthratio 1 3 p %}", {"p": "100.6"})
        with pytest.raises(Exception, match="widthratio final argument must be a number"):
            djust_render("{% widthratio 1 3 p %}", {"p": "100.6"})

    def test_an_integer_string_final_argument_is_accepted(self) -> None:
        assert_agrees("{% widthratio 1 3 p %}", {"p": "100"})

    def test_a_float_final_argument_truncates_toward_zero(self) -> None:
        # `int(100.6)` is 100, so the ratio is 1/3*100, not 1/3*100.6.
        assert_agrees("{% widthratio 1 3 100.6 %}", {})

    def test_a_filter_on_the_operand_resolves(self) -> None:
        assert_agrees("{% widthratio p|length 10 100 %}", {"p": "abcde"})
        assert_agrees("{% widthratio p|add:'5' 10 100 %}", {"p": 5})
        assert_agrees("{% widthratio p|upper 10 100 %}", {"p": "abc"})

    def test_as_var_assigns_and_emits_nothing(self) -> None:
        assert django_render("{% widthratio p 10 100 as w %}[{{ w }}]", {"p": 5}) == "[50]"
        assert_agrees("{% widthratio p 10 100 as w %}[{{ w }}]", {"p": 5})

    def test_as_var_binds_the_empty_string_when_the_value_is_not_numeric(self) -> None:
        assert_agrees("{% widthratio p 10 100 as w %}[{{ w }}]", {"p": "abc"})


# ===========================================================================
# #2355 — {% firstof %}
# ===========================================================================


class TestFirstOf:
    def test_as_var_assigns_and_emits_nothing(self) -> None:
        assert django_render("{% firstof p 'F' as v %}[{{ v }}]", {"p": XSS}) == f"[{XSS}]".replace(
            "<", "&lt;"
        ).replace(">", "&gt;")
        assert_agrees("{% firstof p 'F' as v %}[{{ v }}]", {"p": XSS})

    def test_the_bound_value_is_safe_so_a_later_render_does_not_double_escape(self) -> None:
        # `FirstOfNode` binds `render_value_in_context(...)`, a `SafeString`.
        # Without the grant `{{ v }}` escapes an already-escaped string.
        django, djust = both("{% firstof p 'F' as v %}[{{ v }}]", {"p": "a & b"})
        assert django == "[a &amp; b]"
        assert djust == django

    def test_as_var_binds_the_fallback_when_every_operand_is_falsy(self) -> None:
        assert_agrees("{% firstof p 'F' as v %}[{{ v }}]", {"p": ""})
        assert_agrees("{% firstof p q as v %}[{{ v }}]", {"p": "", "q": 0})

    def test_a_filter_on_the_operand_resolves(self) -> None:
        assert_agrees("{% firstof p|upper 'F' %}", {"p": XSS})
        assert_agrees("{% firstof p|slice:':3' 'F' %}", {"p": XSS})
        assert_agrees("{% firstof p|safe 'F' %}", {"p": XSS})
        assert_agrees("{% firstof p|length 'F' %}", {"p": ""})

    def test_a_literal_as_is_still_an_operand_when_it_is_not_second_to_last(self) -> None:
        # `{% firstof 'as' x %}` is a two-operand tag whose FIRST operand is
        # the string "as" — Django's split only fires on `bits[-2]`.
        assert_agrees("{% firstof p 'as' 'v' %}", {"p": ""})


# ===========================================================================
# #2355 — {% cycle %}
# ===========================================================================


class TestCycle:
    def test_an_unresolved_operand_renders_nothing_not_its_own_source_text(self) -> None:
        assert django_render("{% cycle nope 'z' %}", {}) == ""
        assert_agrees("{% cycle nope 'z' %}", {})
        assert "nope" not in djust_render("{% cycle nope 'z' %}", {})

    def test_a_resolvable_operand_still_renders(self) -> None:
        assert_agrees("{% cycle p 'z' %}", {"p": XSS})
        assert_agrees("{% cycle p 'z' %}", {"p": 5})
        assert_agrees("{% cycle p 'z' %}", {"p": None})

    def test_a_quoted_literal_is_not_a_variable_and_still_renders(self) -> None:
        assert_agrees("{% cycle 'red' 'blue' %}", {})

    def test_a_filter_on_the_operand_resolves(self) -> None:
        assert_agrees(
            "{% for i in L %}{% cycle p|upper 'z' %}{% endfor %}", {"L": [1, 2], "p": XSS}
        )
        assert_agrees("{% for i in L %}{% cycle p|safe 'z' %}{% endfor %}", {"L": [1, 2], "p": XSS})


# ===========================================================================
# #2355 — {% regroup %}'s `by` operand
# ===========================================================================


class TestRegroupByOperand:
    def test_a_filter_on_the_by_operand_applies_per_item(self) -> None:
        src = "{% regroup p by k|upper as g %}[{{ g|length }}]{% for x in g %}({{ x.grouper }}){% endfor %}"
        rows = {"p": [{"k": "a"}, {"k": "b"}, {"k": "A"}]}
        # Three consecutive groups: A, B, A — measured, not assumed.
        assert django_render(src, rows) == "[3](A)(B)(A)"
        assert_agrees(src, rows)

    def test_the_unfiltered_by_operand_is_unchanged(self) -> None:
        src = (
            "{% regroup p by k as g %}[{{ g|length }}]{% for x in g %}({{ x.grouper }}){% endfor %}"
        )
        assert_agrees(src, {"p": [{"k": "a"}, {"k": "b"}, {"k": "A"}]})

    def test_a_dotted_by_path_with_a_filter(self) -> None:
        src = "{% regroup p by k.j|upper as g %}{% for x in g %}({{ x.grouper }}){% endfor %}"
        assert_agrees(src, {"p": [{"k": {"j": "a"}}, {"k": {"j": "A"}}]})

    def test_a_filter_argument_containing_a_colon_survives(self) -> None:
        src = "{% regroup p by k|default:'z' as g %}{% for x in g %}({{ x.grouper }}){% endfor %}"
        assert_agrees(src, {"p": [{"k": ""}, {"k": "b"}]})

    def test_the_source_operands_filter_still_applies(self) -> None:
        # #2333's fix, pinned here because the `by` change is in the same
        # handler and the two operands are resolved by different mechanisms.
        assert_agrees(
            '{% regroup p|dictsort:"k" by k as g %}{{ g|length }}', {"p": [{"k": 2}, {"k": 1}]}
        )


# ===========================================================================
# #2355 — the two tags the Rust engine does NOT implement
# ===========================================================================


class TestTheUnsupportedTwo:
    """`ifchanged` and `filter` refuse, which is fail-closed and measured.

    The corpus builds their cells anyway: "no cell exists" and "every cell is
    the same refusal" are different states, and only the second goes red the
    day someone implements the tag and gets its escaping wrong.
    """

    @pytest.mark.parametrize(
        "src",
        [
            "{% ifchanged p %}[{{ p }}]{% endifchanged %}",
            "{% ifchanged p|upper %}[{{ p }}]{% endifchanged %}",
            "{% filter upper %}{{ p }}{% endfilter %}",
        ],
    )
    def test_the_rust_engine_refuses_rather_than_rendering_something_wrong(self, src) -> None:
        with pytest.raises(Exception, match="Unsupported template tag"):
            djust_render(src, {"p": XSS})
        # Django renders it; the refusal is what the Python fallback catches.
        assert django_render(src, {"p": XSS})


# ===========================================================================
# #2356 — the custom-TAG dispatch path
# ===========================================================================

_PROBE_LIBRARY = dj_template.Library()


@_PROBE_LIBRARY.simple_tag(name="pt_ident")
def _pt_ident(value):
    return value


@_PROBE_LIBRARY.simple_tag(name="pt_safe")
def _pt_safe(value):
    return mark_safe("[" + str(value) + "]")


@_PROBE_LIBRARY.simple_tag(name="pt_cond")
def _pt_cond(value):
    return conditional_escape(value)


class _PyProbe:
    def __init__(self, fn):
        self.fn = fn

    def render(self, args, context):
        return self.fn(*args)


class _PyBlockProbe:
    def render(self, args, content, context):
        return "[" + str(content) + "]"


class _PyAssignProbe:
    RESOLVE_ARG_POSITIONS = None

    def render(self, args, context):
        return {args[-1]: args[0]}


@pytest.fixture(scope="module", autouse=True)
def _register_probes():
    """Register the probes on BOTH engines, and unregister after.

    Both, from the same function body, is the whole point: without the Django
    half there is nothing to compare the Rust dispatch against, and without a
    `{% load %}`-free registration the same source cannot run through both.
    """
    Engine.get_default().template_builtins.append(_PROBE_LIBRARY)
    _rust.register_tag_handler("pt_ident", _PyProbe(_pt_ident))
    _rust.register_tag_handler("pt_safe", _PyProbe(_pt_safe))
    _rust.register_tag_handler("pt_cond", _PyProbe(_pt_cond))
    _rust.register_block_tag_handler("pb_plain", "endpb_plain", _PyBlockProbe())
    _rust.register_assign_tag_handler("pa_ident", _PyAssignProbe())

    _PROBE_LIBRARY.simple_tag(name="pa_ident")(_pt_ident)

    class _BlockNode(dj_template.Node):
        def __init__(self, nodelist):
            self.nodelist = nodelist

        def render(self, context):
            output = "[" + str(self.nodelist.render(context)) + "]"
            return conditional_escape(output) if context.autoescape else output

    @_PROBE_LIBRARY.tag(name="pb_plain")
    def _compile_block(parser, token):
        nodelist = parser.parse(("endpb_plain",))
        parser.delete_first_token()
        return _BlockNode(nodelist)

    yield

    Engine.get_default().template_builtins.remove(_PROBE_LIBRARY)
    _rust.unregister_tag_handler("pt_ident")
    _rust.unregister_tag_handler("pt_safe")
    _rust.unregister_tag_handler("pt_cond")
    _rust.unregister_block_tag_handler("pb_plain")
    _rust.unregister_assign_tag_handler("pa_ident")


class TestTheCustomTagPathIsReachableAtAll:
    """The registries dispatch — which is what #2356 says nothing verified."""

    def test_a_simple_tag_handler_is_called(self) -> None:
        assert djust_render("{% pt_ident p %}", {"p": "abc"}) == "abc"

    def test_a_block_tag_handler_is_called(self) -> None:
        assert djust_render("{% pb_plain %}x{% endpb_plain %}", {}) == "[x]"

    def test_an_assign_tag_handler_is_called(self) -> None:
        assert djust_render("{% pa_ident p as v %}[{{ v }}]", {"p": "abc"}) == "[abc]"

    def test_a_mark_safe_return_is_not_escaped_by_either_engine(self) -> None:
        assert_agrees("{% pt_safe p %}", {"p": XSS})

    def test_a_conditional_escape_return_agrees_for_an_unmarked_value(self) -> None:
        assert_agrees("{% pt_cond p %}", {"p": XSS})


class TestKnownDivergencesOnTheCustomTagPath:
    """Recorded, not fixed — the fix is #2379 and touches every built-in handler.

    Pinned so that change cannot land silently: each of these goes red when
    djust starts escaping a handler's non-`SafeData` return, which is the
    signal that #2379 has landed and these pins must be rewritten as parity
    assertions.
    """

    def test_djust_emits_a_plain_str_return_RAW_where_django_escapes_it(self) -> None:
        # Django: `SimpleNode.render` → `conditional_escape` on a return with
        # no `__html__`. djust: inserted verbatim. THIS IS MORE PERMISSIVE
        # THAN DJANGO and is the #2356 finding.
        assert django_render("{% pt_ident p %}", {"p": XSS}) == (
            "&lt;img src=x onerror=alert(1)&gt;"
        )
        assert djust_render("{% pt_ident p %}", {"p": XSS}) == XSS

    def test_the_same_holds_for_a_block_handlers_plain_str_return(self) -> None:
        assert django_render("{% pb_plain %}x{% endpb_plain %}", {}) == "[x]"
        # The block body is escaped by both; only the handler's own wrapper
        # differs, so the sharpest cell is the one whose wrapper is markup.
        assert djust_render("{% pb_plain %}{{ p }}{% endpb_plain %}", {"p": XSS}) == (
            "[&lt;img src=x onerror=alert(1)&gt;]"
        )

    def test_a_mark_safe_context_value_reaches_the_handler_as_a_bare_str(self) -> None:
        # #2290's finding, one registry over: the `SafeData` marker does not
        # survive the PyO3 hop, so `conditional_escape` escapes where Django
        # does not. djust is STRICTER here, which is the safe direction.
        assert django_render("{% pt_cond p %}", {"p": mark_safe(XSS)}) == XSS
        assert djust_render("{% pt_cond p %}", {"p": mark_safe(XSS)}) == (
            "&lt;img src=x onerror=alert(1)&gt;"
        )

    def test_every_argument_arrives_as_a_string(self) -> None:
        # Django hands the handler the real object; the Rust bridge encodes
        # each resolved arg with `value_to_arg_string` first.
        assert djust_render("{% pt_ident p %}", {"p": 5}) == "5"
        assert django_render("{% pt_ident p %}", {"p": 5}) == "5"
        # …and a structured value arrives as JSON rather than a Python repr.
        assert djust_render("{% pt_ident p %}", {"p": [1, 2]}) == "[1,2]"
        assert django_render("{% pt_ident p %}", {"p": [1, 2]}) == "[1, 2]"

    def test_a_quoted_literal_argument_keeps_its_quotes(self) -> None:
        assert djust_render('{% pt_ident "<b>" %}', {}) == '"<b>"'
        assert django_render('{% pt_ident "<b>" %}', {}) == "<b>"


# ===========================================================================
# The corpus coupling — the manifest must stay honest
# ===========================================================================


class TestTheCorpusReachesWhatTheseIssuesSaidItCouldNot:
    """`--manifest` is the artifact both issues were filed off; pin it.

    A `stale_exemptions` row or a `missing` member here means the corpus went
    back to not being able to construct the cell — which is the state that
    made all four #2355 divergences and the #2356 finding invisible.

    Run in a SUBPROCESS, and that is not tidiness. The script registers its own
    probe library into `Engine.get_default().template_builtins`, and so does
    this module's fixture; importing it here would make each set of probes show
    up as a "required built-in tag" the other does not sweep, and would leave
    the shared default engine carrying the corpus's tags for every test that
    runs afterwards. The manifest is a property of the script, so it is
    measured in a process that is only the script.
    """

    @staticmethod
    def _manifest() -> dict:
        import json
        import pathlib
        import subprocess
        import sys

        repo = pathlib.Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [
                sys.executable,
                str(repo / "scripts" / "filter-parity-differential.py"),
                "--manifest",
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            env={**os.environ, "PYTHONPATH": str(repo / "python")},
        )
        assert proc.returncode == 0, f"--manifest failed:\n{proc.stderr}"
        data = json.loads(proc.stdout)
        # Assert the harness read something, rather than trusting an empty
        # list to mean "nothing is missing".
        assert data["axes"], "the manifest reported no axes at all"
        return data

    def test_every_filter_expression_tag_is_swept(self) -> None:
        row = next(r for r in self._manifest()["axes"] if r["axis"] == "tag")
        assert row["required"], "the tag axis reported no required tags"
        for tag in ("cycle", "firstof", "ifchanged", "regroup", "widthratio", "filter"):
            assert tag in row["required"], f"{tag} is not a Django built-in any more?"
            assert tag not in row["exempt"], f"{tag} still carries an exemption row"
            assert tag not in row["missing"], f"{tag} is required and no shape builds a cell"

    def test_every_custom_tag_registry_is_dispatched_through(self) -> None:
        row = next(r for r in self._manifest()["axes"] if r["axis"] == "entrypoint")
        assert row["required"], "the entrypoint axis reported no required functions"
        for entry in (
            "register_tag_handler",
            "register_block_tag_handler",
            "register_assign_tag_handler",
        ):
            assert entry in row["required"], f"{entry} left the `_rust` module?"
            assert entry not in row["exempt"], f"{entry} still carries an exemption row"
            assert entry not in row["missing"], f"{entry} is on the module and nothing calls it"

    def test_render_both_hands_the_two_engines_the_SAME_context(self) -> None:
        """The harness bug the `as`-form shapes exposed (#2355).

        `Context(d)` keeps `d` as `dicts[-1]` and Django's assignment tags
        write THERE, so a shared dict lets Django's `{% … as v %}` bind a name
        that the djust render then reads. The two engines stop being handed
        the same input, and the corpus reports a djust behaviour that is
        partly Django's.

        Also in a subprocess, for the reason the class docstring gives.
        """
        import pathlib
        import subprocess
        import sys
        import textwrap

        repo = pathlib.Path(__file__).resolve().parents[2]
        code = textwrap.dedent(
            """
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location(
                "fpd", "scripts/filter-parity-differential.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules["fpd"] = module
            spec.loader.exec_module(module)
            ctx = {"p": "<b>"}
            module.render_both("{% firstof p 'F' as v %}[{{ v }}]", ctx)
            assert list(ctx) == ["p"], (
                "Django's assignment leaked into the context djust is handed: %r" % (ctx,))
            print("OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=repo,
            env={**os.environ, "PYTHONPATH": str(repo / "python")},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OK" in proc.stdout

    def test_no_axis_reports_a_missing_member_or_a_stale_exemption(self) -> None:
        for row in self._manifest()["axes"]:
            assert not row["missing"], f"{row['axis']}: {row['missing']}"
            assert not row.get("stale_exemptions"), f"{row['axis']}: {row['stale_exemptions']}"
