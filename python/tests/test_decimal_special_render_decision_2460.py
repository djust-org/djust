"""A non-finite ``Decimal`` renders here and 500s in Django — decided (#2460).

#2460 asks a question before it asks for a fix, in the shape of #2429. The
answer is that djust stays **permissive** and the divergence is recorded. This
file is that record, written as assertions rather than prose so every step of
the argument is run rather than believed (#1867).

The divergence
--------------
::

    >>> Template("{{ p }}").render(Context({"p": Decimal("Infinity")}))
    TypeError: bad operand type for abs(): 'str'

No filter is involved — this is the bare render. Django's
``render_value_in_context`` calls ``localize`` → ``number_format`` →
``numberformat.format``, which reaches::

    _, digits, exponent = number.as_tuple()
    if abs(exponent) + len(digits) > 200:      # <- raises

and ``Decimal("Infinity").as_tuple().exponent`` is the **string** ``'F'``
(``'n'`` for ``NaN``, ``'N'`` for ``sNaN``), so ``abs('F')`` raises. djust
renders ``Infinity`` / ``-Infinity`` / ``NaN`` / ``sNaN``.

Direction: djust is more permissive. A template Django refuses renders here.

Why "match Django" is the wrong target
---------------------------------------
Django's behaviour here is a **crash**, not a considered refusal, and four
measured facts say so — each one a test below:

1. **It is not a policy about non-finite numbers.** ``float("inf")`` renders
   ``inf`` in Django without complaint. The same mathematical value is refused
   only when it arrives as a ``Decimal``, because that is the only branch that
   calls ``as_tuple()``.
2. **The line that raises is a performance guard.** It is the >200-digit
   scientific-notation cutoff, added so ``"{:f}".format()`` does not build a
   200-megabyte string. It is not a validity check, and a special has one
   digit.
3. **Django's own next line computes exactly what djust emits.**
   ``"{:f}".format(Decimal("Infinity"))`` is ``"Infinity"`` — the ``else`` arm
   directly below the guard. djust is not inventing a rendering; it is
   producing the one Django's code says it wants and then fails to reach.
4. **Django puts those characters on the page one filter over.**
   ``floatformat``, ``stringformat:"s"``, ``safe``, ``escape``,
   ``force_escape``, ``title`` and ``linebreaks`` all render ``Infinity`` for
   the same value on Django itself. The characters are not the objection.

Against that, matching would turn a rendered page into a 500 for a value an
ordinary ``DecimalField`` aggregate can hold — an outage bought with parity
against a crash. #2429 decided ``json_script`` the same way, and this is the
easier call: ``json.dumps``' refusal there is at least a documented contract,
where ``abs('F')`` is documented nowhere.

**Not fixed, deliberately. If Django fixes it upstream, this file goes red.**
:meth:`TestTheDecisionCloses_Itself_IfDjangoFixesIt` asserts the refusal in the
DIVERGING direction for exactly that reason.

Re-derived counts — the issue's are wrong
------------------------------------------
#2460 says *"12 of the 17 surviving single-filter ``{{ }}`` cells"*, from *"6
filters x 2 Decimal specials"*. Measured on the 353,909-cell differential:

* the whole class is **57** cells (26 on ``dec-inf``, 31 on ``dec-nan``), not
  12 — it reaches the ``with``, ``cycle``, ``firstof``, ``firstof-as``,
  ``@path`` and ``@ctag`` axes as well as the bare filter one;
* the bare single-filter position is **11**, not 12, and the six filters are
  **not symmetric across the two inputs**: five on ``Infinity`` (``cf_ident``,
  ``default``, ``default_if_none``, ``join``, ``slice``) and six on ``NaN``
  (those five plus ``get_digit``). :meth:`TestTheAsymmetryTheIssueMissed`
  explains why, because the reason is the interesting part.

A further 195 cells reach the same ``abs()`` raise in Django and are NOT this
class, because djust refuses them too, for its own reason.

Refs #2429, #2451, #2349, #2214, #1079.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils import numberformat  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RENDERER_RS = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"

#: The four values Python's ``decimal`` calls special, and the text ``str()``
#: gives each. Every one of them 500s a bare ``{{ }}`` in Django 5.2.
SPECIALS = {
    "Infinity": "Infinity",
    "-Infinity": "-Infinity",
    "NaN": "NaN",
    "sNaN": "sNaN",
}

#: The float forms of the same mathematical values, which Django renders.
FLOAT_SPECIALS = {"inf": float("inf"), "-inf": float("-inf"), "nan": float("nan")}


def dj(source: str, value):
    """Django's answer, or ``("raised", ExceptionClassName, message)``."""
    try:
        return DjangoTemplate(source).render(DjangoContext({"p": value}))
    except Exception as exc:  # noqa: BLE001 — the refusal IS the answer
        return ("raised", type(exc).__name__, str(exc))


def du(source: str, value):
    try:
        return _rust.render_template(source, normalize_django_value({"p": value}))
    except Exception as exc:  # noqa: BLE001
        return ("raised", type(exc).__name__, str(exc))


class TestTheDivergenceItself:
    """The cell, run on both engines rather than transcribed from the issue."""

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    def test_django_500s_the_bare_render(self, spelling: str) -> None:
        answer = dj("{{ p }}", Decimal(spelling))
        assert answer[0] == "raised", answer
        assert answer[1] == "TypeError", answer
        assert answer[2] == "bad operand type for abs(): 'str'", answer

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    def test_djust_renders_str_of_the_value(self, spelling: str) -> None:
        assert du("{{ p }}", Decimal(spelling)) == SPECIALS[spelling]
        # …and it is `str(value)`, not a djust spelling of its own.
        assert du("{{ p }}", Decimal(spelling)) == str(Decimal(spelling))

    def test_no_filter_is_involved(self) -> None:
        """The issue's premise 3, and the whole reason this is not five bugs.

        #2451's body attributes 2 cells each to ``default``,
        ``default_if_none``, ``join``, ``slice`` and a custom-filter probe.
        Each of those filters returns its input UNTOUCHED for this value; the
        raise belongs to the render that follows.
        """
        from django.template.defaultfilters import default, default_if_none, join
        from django.template.defaultfilters import slice_filter

        value = Decimal("Infinity")
        assert default(value, "D") is value
        assert default_if_none(value, "D") is value
        assert join(value, ",") is value
        assert slice_filter(value, ":1") is value
        # And each of them 500s in a template only because `{{ }}` follows.
        for source in (
            '{{ p|default:"D" }}',
            '{{ p|default_if_none:"D" }}',
            '{{ p|join:"," }}',
            '{{ p|slice:":1" }}',
        ):
            assert dj(source, value)[1] == "TypeError", source
            assert dj("{{ p }}", value)[2] == dj(source, value)[2], source


class TestTheArgumentForStayingPermissive:
    """The four facts the decision rests on. Each is RUN (#1867).

    A canon claim of the form *"Django's refusal here is an artefact"* has to
    be falsification-tested, not merely cited — the citation can be exact while
    the claim it supports is false. These are the falsifying cases, and none of
    them falsifies.
    """

    @pytest.mark.parametrize("name", sorted(FLOAT_SPECIALS))
    def test_1_django_renders_the_FLOAT_form_of_the_same_value(self, name: str) -> None:
        """Not a policy about non-finite numbers: only about the Decimal branch."""
        value = FLOAT_SPECIALS[name]
        assert dj("{{ p }}", value) == name
        assert du("{{ p }}", value) == name

    def test_2_the_line_that_raises_is_the_200_digit_cutoff(self) -> None:
        """Read out of Django's live source, not asserted from memory."""
        import inspect

        body = inspect.getsource(numberformat.format)
        assert "if abs(exponent) + len(digits) > 200:" in body
        # The comment directly above it says what the guard is FOR, and it is
        # not validity.
        assert "to avoid high memory usage" in body
        # And a special has exactly one digit, so the guard has nothing to do.
        for spelling in SPECIALS:
            assert len(Decimal(spelling).as_tuple().digits) <= 1, spelling

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    def test_3_djangos_own_else_arm_computes_what_djust_emits(self, spelling: str) -> None:
        """``str_number = "{:f}".format(number)`` — the line below the guard.

        The strongest of the four: djust's output is byte-identical to the
        answer Django's own code computes one line past the crash.
        """
        assert format(Decimal(spelling), "f") == du("{{ p }}", Decimal(spelling))

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|floatformat }}",
            '{{ p|stringformat:"s" }}',
            "{{ p|safe }}",
            "{{ p|escape }}",
            "{{ p|force_escape }}",
            "{{ p|title }}",
        ],
    )
    def test_4_django_itself_renders_these_characters_one_filter_over(self, source: str) -> None:
        """The characters are not what Django objects to."""
        value = Decimal("Infinity")
        assert dj(source, value) == "Infinity", source
        assert du(source, value) == dj(source, value), source

    def test_the_exponent_really_is_a_str_and_that_is_the_mechanism(self) -> None:
        """The root cause, one level below the four facts."""
        for spelling in SPECIALS:
            exponent = Decimal(spelling).as_tuple().exponent
            assert isinstance(exponent, str), (spelling, exponent)
        # …and a FINITE Decimal's is an int, which is why only the specials
        # reach the crash.
        assert isinstance(Decimal("1.5").as_tuple().exponent, int)


class TestTheDecisionCloses_Itself_IfDjangoFixesIt:
    """Asserted in the DIVERGING direction, so it is self-retiring.

    A decision recorded only as "we render this" would stay green forever. This
    asserts that Django still REFUSES — so the day an upstream release makes
    ``{{ Decimal("Infinity") }}`` render, this test goes red and names the
    decision to revisit rather than leaving a stale divergence in the docs.
    """

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    def test_django_still_refuses(self, spelling: str) -> None:
        answer = dj("{{ p }}", Decimal(spelling))
        assert answer[0] == "raised", (
            f"Django now RENDERS {{{{ Decimal({spelling!r}) }}}} as {answer!r}. "
            "The #2460 divergence is closed upstream — delete this class, drop the "
            "decision block in renderer.rs, and check whether djust's text matches."
        )

    def test_the_decision_is_recorded_where_the_code_is(self) -> None:
        """The renderer's number arm carries the argument, not just this file.

        #1197: a decision recorded only in a test file is invisible to the next
        person editing the arm. The pin is on the ISSUE NUMBER plus the
        mechanism, so a reword keeps it green and a deletion does not.
        """
        source = RENDERER_RS.read_text(encoding="utf-8")
        assert "#2460" in source
        assert "abs('F')" in source
        assert "as_tuple" in source


class TestTheAsymmetryTheIssueMissed:
    """Why the bare-position count is 5 + 6 and not 6 + 6.

    ``{{ p|get_digit:"1" }}`` is in the class for ``NaN`` and NOT for
    ``Infinity``, because the two take different exits out of ``get_digit``
    before the render is ever reached:

    * ``int(Decimal("NaN"))`` raises **ValueError**, which ``get_digit``'s
      ``except ValueError`` catches — so the ``Decimal`` is handed back
      untouched and the render raises ``abs()``, putting the cell in this
      class;
    * ``int(Decimal("Infinity"))`` raises **OverflowError**, which that
      ``except`` does not catch — so ``get_digit`` itself raises, the render is
      never reached, and the cell is a different refusal entirely.

    Both engines model this identically (#2435 taught the crate which of the
    three exceptions ``int()`` raises), so the asymmetry is a fact about
    Django's exception taxonomy rather than a djust artefact.
    """

    def test_int_of_the_two_specials_raises_DIFFERENT_exceptions(self) -> None:
        with pytest.raises(ValueError):
            int(Decimal("NaN"))
        with pytest.raises(OverflowError):
            int(Decimal("Infinity"))

    def test_get_digit_over_NaN_reaches_the_render_and_over_Infinity_does_not(self) -> None:
        nan = dj('{{ p|get_digit:"1" }}', Decimal("NaN"))
        assert nan[1] == "TypeError" and "abs()" in nan[2], nan
        inf = dj('{{ p|get_digit:"1" }}', Decimal("Infinity"))
        assert inf[1] == "OverflowError", inf

    def test_djust_models_the_same_split(self) -> None:
        """The ``Infinity`` cell is NOT in the permissive class here either.

        djust refuses it too — for the same ``OverflowError`` reason — which is
        what makes 5 the right number on that axis rather than 6.
        """
        inf = du('{{ p|get_digit:"1" }}', Decimal("Infinity"))
        assert inf[0] == "raised", inf
        assert "OverflowError" in inf[2], inf
        # …and the NaN one renders, as the class says.
        assert du('{{ p|get_digit:"1" }}', Decimal("NaN")) == "NaN"


class TestTheClassIsSweptNotListed:
    """The affected positions, computed rather than typed.

    The issue's "6 filters" is a list. This sweeps the same positions the
    differential does and asserts the property that actually defines the class:
    *Django raises ``abs()`` and djust renders ``str(value)``* — which holds
    for whatever set of shapes reach the bare render, including ones nobody
    enumerated.
    """

    #: The tag and path shapes the differential reaches this class through,
    #: plus the bare filters. Each renders ``p`` unchanged, which is what puts
    #: the raise in the RENDER rather than in the shape.
    SHAPES = [
        "{{ p }}",
        '{{ p|default:"D" }}',
        '{{ p|default_if_none:"D" }}',
        '{{ p|join:"<br>" }}',
        '{{ p|slice:":3" }}',
        "{% with q=p %}[{{ q }}]{% endwith %}",
        "{% firstof p 'F' %}",
        "{% firstof p 'F' as v %}[{{ v }}]",
        "{% cycle p 'z' %}",
        "{% with q=p %}{% with q=q %}[{{ q }}]{% endwith %}{% endwith %}",
    ]

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    @pytest.mark.parametrize("shape", SHAPES)
    def test_django_refuses_and_djust_renders_the_text(self, shape: str, spelling: str) -> None:
        value = Decimal(spelling)
        answer = dj(shape, value)
        assert answer[0] == "raised" and "abs()" in answer[2], (shape, answer)
        rendered = du(shape, value)
        assert not isinstance(rendered, tuple), (shape, rendered)
        assert SPECIALS[spelling] in rendered, (shape, rendered)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_the_same_shape_over_a_FLOAT_special_agrees(self, shape: str) -> None:
        """The control that makes the sweep above a divergence and not a shape bug.

        If any of these shapes diverged for its own reason, it would diverge
        for ``float("inf")`` too. None does.
        """
        assert dj(shape, float("inf")) == du(shape, float("inf")), shape

    @pytest.mark.parametrize("shape", SHAPES)
    def test_the_same_shape_over_a_FINITE_decimal_agrees(self, shape: str) -> None:
        """The second control: the ``Decimal`` branch itself is fine."""
        value = Decimal("1.5")
        assert dj(shape, value) == du(shape, value), shape


class TestNoLocaleCanCorruptTheSpelling:
    """A special passes the localiser untouched, whatever the active format.

    Worth pinning because the arm this decision lives in is the LOCALISING one
    (#2221/#2242): if a grouping locale ever treated ``Infinity`` as digits, the
    permissive answer would stop being ``str(value)`` and the decision above
    would quietly become a different one.
    """

    @pytest.mark.parametrize("spelling", sorted(SPECIALS))
    def test_the_rendered_text_carries_no_separator(self, spelling: str) -> None:
        rendered = du("{{ p }}", Decimal(spelling))
        assert not re.search(r"[.,  ]", rendered.lstrip("-")), rendered

    def test_the_localiser_guard_is_the_reason_and_is_still_there(self) -> None:
        """``localize_plain``'s digits-and-a-point guard, read off the source."""
        locale_rs = (REPO / "crates" / "djust_core" / "src" / "locale.rs").read_text(
            encoding="utf-8"
        )
        assert "!body.chars().all(|c| c.is_ascii_digit() || c == '.')" in locale_rs
