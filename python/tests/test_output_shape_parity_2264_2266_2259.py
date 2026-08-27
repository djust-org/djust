"""Three filters that computed the right value and emitted the wrong bytes.

#2264 (``filesizeformat``), #2266 (``floatformat``'s ``u``/``gu`` suffixes) and
#2259 (``linebreaks``) are one change because they are one failure class: the
number, the paragraph and the size were all correct, and the *shape* they were
written in was not. Two of the three are invisible to a test that compares
strings by eye:

* ``filesizeformat`` joins the number to its unit with U+00A0. A test written
  with an ordinary space passes while shipping the wrong byte — which is exactly
  what ``test_filesizeformat_filter`` did for the whole life of the filter, and
  the same trap #2228 fell into with ``timesince``. Every assertion here is on
  EXACT bytes, and the failure messages carry ``repr()`` so a divergence is
  legible rather than a pair of identical-looking strings.
* ``linebreaks`` was HTML-escaping its own markup, so pages showed the literal
  text ``<p>hello</p>``.

Each issue's premise was treated as a hypothesis, and **all three moved**:

============  ===============================  ==================================
issue         what the issue said              what running it showed
============  ===============================  ==================================
#2264         3 causes (nbsp, plural, f64)     **5** — also negatives (Django
                                               abs-and-re-signs, so ``-1024`` is
                                               ``-1.0 KB``) and ``int()``
                                               coercion (``"1024"`` is
                                               ``1.0 KB``; ``None`` is
                                               ``0 bytes``, not ``None``). The
                                               KB-and-up branch is also
                                               LOCALIZED, which no cause listed.
#2266         residue measured pre-#2263       unmoved — #2263 rewrote the
                                               quantization and never touched
                                               ``finish``'s ``use_l10n`` arm.
#2259         ``linebreaksbr``/``linenumbers``  ``linebreaksbr`` diverges (both
              "do not show up ... check       on escaping AND ``\\r\\n``);
              before assuming they are fine"   ``linenumbers`` is escape-EQUIVALENT
                                               but zero-pads in Django and
                                               space-padded here.
============  ===============================  ==================================

The safe-marking half of #2259 is security-adjacent, so it is asserted from both
directions: the payload must be escaped AND the generated markup must not be.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.utils import translation  # noqa: E402

from djust import _rust  # noqa: E402
from djust.render_env import apply_number_format  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

#: The byte Django's ``avoid_wrapping`` puts between a size and its unit.
#:
#: Spelled as an ESCAPE, never as a literal. A literal U+00A0 is
#: indistinguishable from a space on screen and is silently rewritten by any
#: tool that normalises whitespace — at which point every assertion below
#: would quietly be asserting the bug. The guard on the next line is the
#: mechanical version of that sentence.
NBSP = "\u00a0"
assert NBSP != " ", "U+00A0 was normalised into a plain space in this source file"


@pytest.fixture(autouse=True)
def _push_number_format():
    """Push the active number format, and RESTORE it however the test ends.

    The format is a Rust thread-local. A test that overrides
    ``NUMBER_GROUPING`` and dies before restoring poisons every later test in
    the same xdist worker — a real incident in this repo's history, which is why
    the restore is a fixture teardown and not a line at the end of each test.
    """
    apply_number_format()
    yield
    with override_settings():
        with translation.override("en"):
            apply_number_format()


def render_both(source: str, value: Any) -> tuple[str, str]:
    """``(django, djust)`` for one cell, rendering the SAME value through both."""
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


def assert_agrees(source: str, value: Any, label: str = "") -> None:
    django_out, djust_out = render_both(source, value)
    assert djust_out == django_out, (
        f"{label}{source} on {value!r}: django={django_out!r} djust={djust_out!r}"
    )


# --------------------------------------------------------------------------
# #2264 — filesizeformat
# --------------------------------------------------------------------------

KB, MB, GB, TB, PB = 1 << 10, 1 << 20, 1 << 30, 1 << 40, 1 << 50


class TestFilesizeformatReportedCells:
    """The cells #2264 named, as literal expected strings."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # Cause 1 — the separator, on a value where it is the ONLY difference.
            (Decimal("19.99"), f"19{NBSP}bytes"),
            (1, f"1{NBSP}byte"),  # cause 2 — pluralization
            (Decimal("1.5"), f"1{NBSP}byte"),
            # Cause 3 — the `as_f64` parse, which saturated at `8192.0 PB`.
            (Decimal("12345678901234567890.123456789"), f"10965.2{NBSP}PB"),
        ],
    )
    def test_the_reported_cells_render_djangos_bytes(self, value: Any, expected: str) -> None:
        django_out, djust_out = render_both("{{ p|filesizeformat }}", value)
        assert django_out == expected, f"the issue's Django column moved: {django_out!r}"
        assert djust_out == expected, f"djust={djust_out!r} expected={expected!r}"

    def test_the_separator_is_u00a0_and_there_is_no_plain_space(self) -> None:
        """Stated as a byte property, not a string comparison.

        A `== "1.0 KB"` assertion with a literal nbsp is unreadable and one
        editor pass away from being wrong; this says what is actually required.
        """
        out = _rust.render_template("{{ p|filesizeformat }}", normalize_django_value({"p": 2048}))
        assert " " not in out, f"plain space present: {out!r}"
        assert out.count(NBSP) == 1, f"expected exactly one U+00A0: {out!r}"

    def test_negatives_are_scaled_not_dumped_into_the_bytes_branch(self) -> None:
        """A cause #2264 did not list — found by widening the sweep.

        Django does ``negative = bytes_ < 0; bytes_ = -bytes_``, formats the
        magnitude, then re-signs. The old signed ``bytes < KB`` comparison sent
        EVERY negative into the bytes branch.
        """
        django_out, djust_out = render_both("{{ p|filesizeformat }}", -1024)
        assert django_out == f"-1.0{NBSP}KB"
        assert djust_out == django_out

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1024", f"1.0{NBSP}KB"),  # `int("1024")` succeeds
            (" 42 ", f"42{NBSP}bytes"),  # `int()` strips whitespace
            ("1_024", f"1.0{NBSP}KB"),  # PEP 515 separators
            ("19.99", f"0{NBSP}bytes"),  # `int()` refuses a decimal point
            ("abc", f"0{NBSP}bytes"),
            ("", f"0{NBSP}bytes"),
            (None, f"0{NBSP}bytes"),  # was rendered as the literal `None`
            (True, f"1{NBSP}byte"),
            (False, f"0{NBSP}bytes"),
            ([1, 2], f"0{NBSP}bytes"),
            ({"a": 1}, f"0{NBSP}bytes"),
        ],
    )
    def test_int_coercion_matches_djangos_try_except(self, value: Any, expected: str) -> None:
        """The second cause #2264 did not list.

        Django's first statement is ``int(bytes_)`` with a ``TypeError /
        ValueError / UnicodeDecodeError`` fallback to ZERO. The old filter
        returned the value UNCHANGED for every non-numeric type.
        """
        django_out, djust_out = render_both("{{ p|filesizeformat }}", value)
        assert django_out == expected, f"Django moved: {django_out!r}"
        assert djust_out == expected, f"djust={djust_out!r}"


class TestFilesizeformatDifferential:
    """Every unit boundary, both signs, every numeric type, several locales."""

    @pytest.mark.parametrize("unit", [1, KB, MB, GB, TB, PB])
    @pytest.mark.parametrize("delta", [-2, -1, 0, 1, 2])
    @pytest.mark.parametrize("sign", [1, -1])
    def test_every_unit_boundary(self, unit: int, delta: int, sign: int) -> None:
        assert_agrees("{{ p|filesizeformat }}", sign * (unit + delta))

    @pytest.mark.parametrize(
        "value",
        [
            0,
            1,
            1023,
            -1,
            -1023,
            1.0,
            1.5,
            -1.9,
            0.4,
            float(3 * GB),
            2.25 * KB,  # a half-way tie: Django rounds to even -> 2.2
            Decimal("0"),
            Decimal("0.00"),
            Decimal("1.5"),
            Decimal("-1.5"),
            Decimal("1E+3"),
            Decimal("-1E+3"),
            Decimal("9007199254740993"),  # 2**53 + 1 — the digit a double drops
            2**62,
            -(2**62),
        ],
    )
    def test_curated_values(self, value: Any) -> None:
        assert_agrees("{{ p|filesizeformat }}", value)

    def test_randomized_sweep_against_django(self) -> None:
        """A curated table samples the axis you thought of (v1.1.1-2 retro).

        Django is importable here, so "what does the reference actually do" is a
        call away and is preferred to reasoning about the rounding.
        """
        rng = random.Random(2264)
        divergent = []
        for _ in range(800):
            kind = rng.randrange(4)
            if kind == 0:
                value: Any = rng.randrange(-(1 << 62), 1 << 62)
            elif kind == 1:
                value = rng.uniform(-1e18, 1e18)
            elif kind == 2:
                value = Decimal(f"{rng.randrange(-(10**12), 10**12)}.{rng.randrange(0, 10**9):09d}")
            else:
                value = rng.randrange(0, 1 << 22)
            django_out, djust_out = render_both("{{ p|filesizeformat }}", value)
            if django_out != djust_out:
                divergent.append((repr(value), django_out, djust_out))
        assert not divergent, f"{len(divergent)} divergent cells: {divergent[:5]}"

    @pytest.mark.parametrize("use_thousand_separator", [False, True])
    @pytest.mark.parametrize(
        "value", [KB - 1, MB - 1, 5000 * KB, 1234 * MB, 999999 * PB, -(MB - 1), 0, 1]
    )
    def test_the_scaled_branch_is_localized(self, use_thousand_separator: bool, value: int) -> None:
        """A fifth cause no listed one covers.

        ``filesize_number_format`` is ``formats.number_format(round(v, 1), 1)``,
        so the decimal separator AND ``USE_THOUSAND_SEPARATOR`` grouping apply:
        ``1048575`` is ``1,024.0 KB`` with grouping on. The ``bytes`` branch is a
        raw ``%d`` and is NOT localized, which this parametrization covers via
        the ``0`` and ``1`` rows.
        """
        with override_settings(USE_THOUSAND_SEPARATOR=use_thousand_separator):
            with translation.override("en"):
                apply_number_format()
                assert_agrees("{{ p|filesizeformat }}", value)

    def test_grouping_actually_changes_the_output(self) -> None:
        """Guards the test above from being vacuous.

        If grouping never reached the filter, every row of
        ``test_the_scaled_branch_is_localized`` would agree for the wrong
        reason (#1200).
        """
        with translation.override("en"):
            with override_settings(USE_THOUSAND_SEPARATOR=False):
                apply_number_format()
                plain = _rust.render_template(
                    "{{ p|filesizeformat }}", normalize_django_value({"p": MB - 1})
                )
            with override_settings(USE_THOUSAND_SEPARATOR=True):
                apply_number_format()
                grouped = _rust.render_template(
                    "{{ p|filesizeformat }}", normalize_django_value({"p": MB - 1})
                )
        assert plain == f"1024.0{NBSP}KB"
        assert grouped == f"1,024.0{NBSP}KB"


# --------------------------------------------------------------------------
# #2266 — floatformat's u / gu suffixes
# --------------------------------------------------------------------------

#: The exact table #2266 measured, so closing it is checked against the report.
REPORTED_U_CELLS = [
    ('{{ p|floatformat:"2u" }}', "6666!67"),
    ('{{ p|floatformat:"2gu" }}', "6_666!67"),
    ('{{ p|floatformat:"2" }}', "6666.67"),
    ('{{ p|floatformat:"2g" }}', "6,666.67"),
]

#: `u` reads the RAW settings; the localized rows read the active locale's.
#: A row that agrees under BOTH is not evidence, so the overrides are chosen to
#: make the two triples disagree.
RAW_OVERRIDES = [
    {},
    {"DECIMAL_SEPARATOR": "!", "THOUSAND_SEPARATOR": "_", "NUMBER_GROUPING": 3},
    {"DECIMAL_SEPARATOR": ",", "THOUSAND_SEPARATOR": ".", "NUMBER_GROUPING": 3},
    {"NUMBER_GROUPING": 3},
    {"NUMBER_GROUPING": [3, 2, 0], "THOUSAND_SEPARATOR": ","},
    {"DECIMAL_SEPARATOR": "@", "THOUSAND_SEPARATOR": " ", "NUMBER_GROUPING": 0},
]

U_ARGS = [
    None,
    '"0"',
    '"1"',
    '"2"',
    '"3"',
    '"-2"',
    '"-3"',
    '"u"',
    '"1u"',
    '"2u"',
    '"3u"',
    '"-2u"',
    '"g"',
    '"2g"',
    '"-2g"',
    '"gu"',
    '"0gu"',
    '"2gu"',
    '"2ug"',
]

U_VALUES = [
    Decimal("6666.6666"),
    Decimal("0.00"),
    Decimal("-1234567.891"),
    Decimal("1234567890.5"),
    Decimal("999.95"),
    Decimal("1E+3"),
    0,
    1,
    -1,
    1234567,
    3.14159,
    -0.5,
    2.675,
]


class TestFloatformatUSuffix:
    def test_the_reported_table_now_agrees(self) -> None:
        """#2266's own four rows, values included.

        Also confirms the residue did NOT move under #2263 (which rewrote the
        quantization): the two ``u`` rows still read ``6666.67`` on the
        pre-fix build, because #2263 never touched ``finish``'s ``use_l10n``
        arm.
        """
        with override_settings(DECIMAL_SEPARATOR="!", THOUSAND_SEPARATOR="_", NUMBER_GROUPING=3):
            apply_number_format()
            for source, expected in REPORTED_U_CELLS:
                django_out, djust_out = render_both(source, Decimal("6666.6666"))
                assert django_out == expected, f"the issue's Django column moved: {django_out!r}"
                assert djust_out == expected, f"{source}: djust={djust_out!r}"

    @pytest.mark.parametrize("overrides", RAW_OVERRIDES, ids=lambda o: str(sorted(o)) or "defaults")
    @pytest.mark.parametrize("use_thousand_separator", [False, True])
    @pytest.mark.parametrize("language", ["en", "de"])
    def test_the_full_suffix_matrix(
        self, overrides: dict[str, Any], use_thousand_separator: bool, language: str
    ) -> None:
        """Every argument form x every override x grouping x language.

        The language axis matters on its own: under ``de`` with NO overrides,
        Django's ``u`` gives ``6666.67`` (the raw default ``.``) while the plain
        argument gives ``6666,67`` (the locale's ``,``). Pushing one format
        cannot produce both.
        """
        with override_settings(USE_THOUSAND_SEPARATOR=use_thousand_separator, **overrides):
            with translation.override(language):
                apply_number_format()
                for arg in U_ARGS:
                    source = (
                        "{{ p|floatformat }}" if arg is None else "{{ p|floatformat:%s }}" % arg
                    )
                    for value in U_VALUES:
                        assert_agrees(source, value, label=f"[{language}/{overrides}] ")

    def test_gu_groups_only_when_the_RAW_grouping_is_non_zero(self) -> None:
        """Django's arithmetic, not a simplification.

        ``use_grouping`` is False whenever ``use_l10n`` is False, and only then
        does ``force_grouping`` OR in — after which ``and grouping != 0`` still
        applies. So with ``NUMBER_GROUPING`` at its default ``0``, ``gu`` does
        NOT group even though ``g`` alone does.
        """
        with translation.override("en"):
            with override_settings(USE_THOUSAND_SEPARATOR=True):
                apply_number_format()
                django_out, djust_out = render_both(
                    '{{ p|floatformat:"2gu" }}', Decimal("6666.6666")
                )
                assert django_out == "6666.67", f"Django moved: {django_out!r}"
                assert djust_out == django_out
                # `g` alone (localized) DOES group — proving the row above is
                # about the raw grouping and not about grouping being broken.
                assert render_both('{{ p|floatformat:"2g" }}', Decimal("6666.6666"))[1] == (
                    "6,666.67"
                )

            # The OTHER half of the same rule, and the half the assertions above
            # cannot see: with the raw NUMBER_GROUPING set to 3, a bare `u` must
            # STILL not group, because `use_grouping` is False on the unlocalized
            # format and only `g` supplies `force_grouping`.
            #
            # Added after a gate-off found the mutation `use_grouping: false ->
            # true` left this test green: at the default NUMBER_GROUPING of 0,
            # `grouping != 0` suppresses grouping anyway, so the flag was a
            # semantic no-op for the values above (the "valid mutation, no-op for
            # the tested inputs" class, v1.1.1-2 retro). These two rows are the
            # ones that distinguish it.
            with override_settings(USE_THOUSAND_SEPARATOR=True, NUMBER_GROUPING=3):
                apply_number_format()
                ungrouped, ungrouped_djust = render_both(
                    '{{ p|floatformat:"2u" }}', Decimal("6666.6666")
                )
                assert ungrouped == "6666.67", f"Django moved: {ungrouped!r}"
                assert ungrouped_djust == ungrouped, (
                    f"`u` grouped when it must not: {ungrouped_djust!r}"
                )
                # ...while `gu` DOES, so the row above is not just "grouping is
                # off everywhere".
                grouped, grouped_djust = render_both(
                    '{{ p|floatformat:"2gu" }}', Decimal("6666.6666")
                )
                assert grouped == "6,666.67", f"Django moved: {grouped!r}"
                assert grouped_djust == grouped

    def test_both_formats_reach_rust_and_they_are_different(self) -> None:
        """A setter with no getter cannot be tested end to end (#2017).

        Also non-vacuous by construction: the assertion is that the two
        thread-locals hold DIFFERENT triples, which a single-format push cannot
        satisfy.
        """
        with override_settings(DECIMAL_SEPARATOR="!", THOUSAND_SEPARATOR="_", NUMBER_GROUPING=3):
            with translation.override("de"):
                apply_number_format()
                localized = _rust.active_number_format()
                raw = _rust.active_unlocalized_number_format()
        assert localized is not None and raw is not None
        assert localized[0] == ",", f"de's localized separator: {localized!r}"
        assert raw[0] == "!", f"the raw DECIMAL_SEPARATOR: {raw!r}"
        assert raw[3] is False, "the unlocalized format never groups on its own"
        assert localized != raw


# --------------------------------------------------------------------------
# #2259 — linebreaks / linebreaksbr / linenumbers
# --------------------------------------------------------------------------

LINEBREAK_VALUES = [
    "hello",
    "",
    " ",
    "a\nb",
    "a\n\nb",
    "a\n\n\nb",
    "a\n\n\n\nb",
    "\n",
    "\n\n",
    "\n\n\n",
    "a\r\nb",
    "a\rb",
    "a\r\r\nb",
    "\na",
    "a\n",
    "  \n\n  ",
    "a\n\nb\n\nc",
    None,
    True,
    False,
    0,
    -42,
    1234567,
    3.5,
    [1, 2, 3],
    {"a": 1},
    Decimal("1.5"),
    "a & b",
    '<a href="x">&</a>',
    "x\n<b>y</b>",
    "'\"`",
    "<p>already</p>",
    "emoji \U0001f600\nsecond",
    "tab\there",
]

#: Payloads that must come out inert.
XSS_PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "<a href='javascript:alert(1)'>x</a>",
    "line one\n<img src=x onerror=alert(1)>\n\nline two",
]


class TestLinebreaksReported:
    def test_the_reported_cell(self) -> None:
        django_out, djust_out = render_both("{{ p|linebreaks }}", "hello")
        assert django_out == "<p>hello</p>"
        assert djust_out == "<p>hello</p>"

    def test_the_empty_string_is_an_empty_paragraph(self) -> None:
        """The second divergence #2259 named: Django gives ``<p></p>``.

        djust filtered empty paragraphs out and rendered ``''``.
        """
        django_out, djust_out = render_both("{{ p|linebreaks }}", "")
        assert django_out == "<p></p>"
        assert djust_out == django_out

    def test_paragraphs_are_joined_with_two_newlines_not_one(self) -> None:
        """Not in the issue — found by widening past a single-line value."""
        django_out, djust_out = render_both("{{ p|linebreaks }}", "a\n\nb")
        assert django_out == "<p>a</p>\n\n<p>b</p>"
        assert djust_out == django_out

    def test_three_or_more_newlines_are_one_separator(self) -> None:
        """Django splits on ``\\n{2,}``; a literal ``"\\n\\n"`` split does not."""
        django_out, djust_out = render_both("{{ p|linebreaks }}", "a\n\n\nb")
        assert django_out == "<p>a</p>\n\n<p>b</p>"
        assert djust_out == django_out

    def test_linebreaksbr_diverged_too(self) -> None:
        """The issue said to CHECK it rather than assuming; it was broken.

        Both on escaping and on ``\\r\\n`` normalization.
        """
        for value, expected in [("a\nb", "a<br>b"), ("a\r\nb", "a<br>b")]:
            django_out, djust_out = render_both("{{ p|linebreaksbr }}", value)
            assert django_out == expected, f"Django moved: {django_out!r}"
            assert djust_out == expected, f"djust={djust_out!r}"

    def test_linenumbers_zero_pads_which_the_issue_did_not_mention(self) -> None:
        """The neighbour check came back with a DIFFERENT defect.

        #2259 assumed ``linenumbers`` was fine because it emits no tags. Its
        escaping is indeed equivalent — but Django's format is
        ``("%0" + width + "d. %s")``, zero-padded, and djust space-padded. Only
        visible past ten lines, which is why no single-line differential saw it.
        """
        value = "\n".join(f"l{i}" for i in range(11))
        django_out, djust_out = render_both("{{ p|linenumbers }}", value)
        assert django_out.startswith("01. l0"), f"Django moved: {django_out[:12]!r}"
        assert djust_out == django_out

    @pytest.mark.parametrize("n", [1, 9, 10, 11, 99, 100, 101])
    def test_linenumbers_width_boundaries(self, n: int) -> None:
        assert_agrees("{{ p|linenumbers }}", "\n".join(f"l{i}<&>" for i in range(n)))


class TestLinebreaksSafeMarking:
    """The security-adjacent half, asserted from both directions.

    ``linebreaks``/``linebreaksbr`` are exempt from the renderer's auto-escape
    ONLY because they escape their own input first. Django's registration is
    ``is_safe=True, needs_autoescape=True`` over a body that calls
    ``escape(p)``; marking the output safe without that inner escape turns
    ``{{ comment|linebreaks }}`` into an XSS sink.
    """

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    @pytest.mark.parametrize("filter_name", ["linebreaks", "linebreaksbr"])
    def test_the_payload_is_escaped_and_the_generated_markup_is_not(
        self, payload: str, filter_name: str
    ) -> None:
        source = "{{ p|%s }}" % filter_name
        django_out, djust_out = render_both(source, payload)
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        # Direction 1 — nothing the ATTACKER wrote is live.
        for dangerous in ("<img", "<script", "</script", "<svg", "<a href"):
            assert dangerous not in djust_out, f"{dangerous!r} is live in {djust_out!r}"
        assert "&lt;" in djust_out, f"nothing was escaped at all: {djust_out!r}"
        # Direction 2 — the tags the FILTER generated ARE live. Without this the
        # test would pass on the pre-fix (fully escaped) output too.
        generated = "<p>" if filter_name == "linebreaks" else "<br>"
        if generated == "<br>" and "\n" not in payload:
            generated = ""
        if generated:
            assert generated in djust_out, f"{generated!r} was escaped away: {djust_out!r}"

    def test_the_filter_names_are_in_the_renderer_safe_list(self) -> None:
        """Pins the pairing structurally.

        The escape and the safe-list membership are ONE change; this asserts the
        list actually contains them so a revert of the renderer half is caught
        even if the filter half stays.
        """
        source = _rust.__file__.rsplit("/python/", 1)[0] + "/crates/djust_templates/src/renderer.rs"
        try:
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            pytest.skip("Rust source not available next to the built extension")
        block = text.split("const SAFE_OUTPUT_FILTERS", 1)[1].split("];", 1)[0]
        assert '"linebreaks"' in block
        assert '"linebreaksbr"' in block
        # And the neighbour, which joined them in #2291 — see below for why the
        # line that used to be here asserted the opposite.
        assert '"linenumbers"' in block

    def test_linenumbers_membership_is_paired_with_the_escape(self) -> None:
        """This test used to assert the exact opposite, and was wrong (#2291).

        It read ``assert '"linenumbers"' not in block``, with the comment "the
        one that must stay OUT — it does not escape its own input", and a
        sibling named ``test_linenumbers_stays_out_of_the_safe_list_and_is_still_correct``
        that rendered ``{{ p|linenumbers }}`` over six values, found djust and
        Django byte-identical, and concluded the exclusion was correct. Every
        one of those observations was accurate. The conclusion was not: djust
        escaped nothing inside the filter and leaned on the render-time escape,
        so ``{{ p|linenumbers|safe }}`` — the one column that removes that
        escape — emitted attacker markup live.

        The old docstring even named the precondition: "adding the name to the
        safe list *without moving the escape inside* would stop the input being
        escaped at all." That is true, and it is a statement about a PAIR. What
        it pinned instead was one half of the pair, unconditionally, which made
        the vulnerable arrangement the asserted-correct one.

        So this now pins the pair, in both directions: membership without the
        inner escape is the XSS, and the inner escape without membership
        double-escapes. Neither half can move alone.
        """
        base = _rust.__file__.rsplit("/python/", 1)[0] + "/crates/djust_templates/src/"
        try:
            with open(base + "renderer.rs", encoding="utf-8") as fh:
                block = fh.read().split("const SAFE_OUTPUT_FILTERS", 1)[1].split("];", 1)[0]
            with open(base + "filters.rs", encoding="utf-8") as fh:
                body = fh.read().split("fn add_linenumbers", 1)[1].split("\nfn ", 1)[0]
        except OSError:
            pytest.skip("Rust source not available next to the built extension")
        assert '"linenumbers"' in block, "the safety grant went missing"
        assert "html_escape(line)" in body, (
            "the escape moved out of add_linenumbers while the safe-list grant "
            "stayed — that arrangement IS the #2291 vulnerability"
        )
        # And the behaviour both halves exist to produce.
        for value in ["<b>x</b>", "a\n<i>", "&", '"q"', "'s'", "<img src=x onerror=alert(1)>"]:
            for src in ("{{ p|linenumbers }}", "{{ p|linenumbers|safe }}"):
                django_out, djust_out = render_both(src, value)
                assert djust_out == django_out, f"{src}: django={django_out!r} djust={djust_out!r}"
                assert "<b>" not in djust_out and "<img" not in djust_out


class TestLinebreaksDifferential:
    @pytest.mark.parametrize("filter_name", ["linebreaks", "linebreaksbr", "linenumbers"])
    @pytest.mark.parametrize("value", LINEBREAK_VALUES, ids=repr)
    def test_curated_values(self, filter_name: str, value: Any) -> None:
        assert_agrees("{{ p|%s }}" % filter_name, value)

    @pytest.mark.parametrize("filter_name", ["linebreaks", "linebreaksbr", "linenumbers"])
    def test_randomized_sweep_over_the_characters_that_matter(self, filter_name: str) -> None:
        """Newlines, carriage returns and every character ``escape`` touches."""
        rng = random.Random(2259)
        alphabet = "ab<>&\"'\n\r \té"
        source = "{{ p|%s }}" % filter_name
        divergent = []
        for _ in range(600):
            value = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 24)))
            django_out, djust_out = render_both(source, value)
            if django_out != djust_out:
                divergent.append((repr(value), django_out, djust_out))
        assert not divergent, f"{len(divergent)} divergent cells: {divergent[:5]}"


# --------------------------------------------------------------------------
# Stated rather than left to be discovered.
# --------------------------------------------------------------------------


class TestKnownRemainingDivergences:
    """Each is pre-existing and outside these three issues (#1079).

    Asserted as facts so that closing one turns this file red, which is the
    signal to prune the entry (#1125).
    """

    def test_filesizeformat_unit_names_are_not_translated(self) -> None:
        """Django ``gettext``s the unit; the Rust engine has no catalogue.

        The NUMBER half is localized correctly — this is the ``{% trans %}`` gap,
        not a ``filesizeformat`` one.
        """
        with translation.override("fr"):
            apply_number_format()
            django_out, djust_out = render_both("{{ p|filesizeformat }}", 1536)
            assert django_out == f"1,5{NBSP}Kio"
            # The digits and the separator agree; only the unit name does not.
            assert djust_out == f"1,5{NBSP}KB"

    def test_a_tuple_renders_as_a_list_before_any_filter_runs(self) -> None:
        """Not a filter gap — ``{{ p }}`` alone already disagrees.

        Surfaced by the ``linebreaks`` differential and left alone: the fix
        belongs at the value boundary.
        """
        bare_django, bare_djust = render_both("{{ p }}", (1, 2))
        assert (bare_django, bare_djust) == ("(1, 2)", "[1, 2]")
        _, filtered = render_both("{{ p|linebreaks }}", (1, 2))
        assert filtered == "<p>[1, 2]</p>"

    def test_filesizeformat_on_an_infinity_cannot_reproduce_djangos_500(self) -> None:
        """Django RAISES here; a filter in this engine cannot.

        ``int(float('inf'))`` is an ``OverflowError``, which is NOT in
        ``filesizeformat``'s ``except`` tuple — so Django propagates it and the
        page 500s. djust lands on the same `0 bytes` fallback as every other
        uncoercible value. Recorded because it is a deliberate refusal to
        reproduce a crash, not an oversight; before this change djust rendered
        ``8192.0 PB`` for an infinity, which is worse than either.
        """
        with pytest.raises(OverflowError):
            DjangoTemplate("{{ p|filesizeformat }}").render(DjangoContext({"p": float("inf")}))
        out = _rust.render_template(
            "{{ p|filesizeformat }}", normalize_django_value({"p": float("inf")})
        )
        assert out == f"0{NBSP}bytes"

    def test_a_float_nan_no_longer_spells_itself_differently(self) -> None:
        """CLOSED by #2258, hours after this entry was written.

        It read: ``Value::Float``'s ``Display`` writes Rust's ``NaN`` where
        Python writes ``nan`` — surfaced by the ``linebreaks`` differential and
        correctly left alone, because ``{{ p }}`` alone already disagreed, so it
        was the ``Display`` gap and not a ``linebreaks`` one. That diagnosis is
        exactly why #2258 closes it here for free: `Display` now routes through
        Django's own ``numberformat.format`` rules.

        Kept and inverted rather than deleted — the entry was right about where
        the gap lived, and the pin is what goes red if either fix regresses.
        """
        assert render_both("{{ p }}", float("nan")) == ("nan", "nan")
        django_out, djust_out = render_both("{{ p|linebreaks }}", float("nan"))
        assert django_out == "<p>nan</p>"
        assert djust_out == django_out


class TestLastFilterWinsForSafeness:
    """A drift #2259 had to cure before it could add a name to the safe list.

    ``get_value_safe`` applied the safe-name check PER FILTER (Django's rule:
    ``FilterExpression.resolve`` marks safe only when the filter it just ran is
    ``is_safe``) while the ``Node::Variable`` and ``Node::InlineIf`` arms applied
    it as ``any()`` over the whole chain — and ``get_value_safe``'s comment said
    all three matched. So ``{{ p|urlize|upper }}`` diverged from Django on an
    unmodified build, and adding ``linebreaks`` to the list would have widened
    that divergence to a fourth name.

    Found by the non-regression set comparison against a ``main`` build, not by
    reading the diff: 396 cells that AGREED with Django on ``main`` disagreed on
    the first version of this branch, all of them ``{{ p|linebreaks|upper }}``.
    """

    @pytest.mark.parametrize("safe_filter", ["safe", "urlize", "linebreaks", "linebreaksbr"])
    def test_a_later_is_safe_false_filter_re_taints(self, safe_filter: str) -> None:
        """``upper`` is registered ``is_safe=False`` in Django, precisely
        because uppercasing ``&amp;`` produces the non-entity ``&AMP;``.

        ``unordered_list`` is the fifth name in the list and is exercised in its
        own test below rather than here: it iterates a STRING character by
        character in Django and not in djust, a whole-filter divergence that
        would swamp the escaping question this test is asking.
        """
        assert_agrees("{{ p|%s|upper }}" % safe_filter, "a <i>x</i> b")

    def test_unordered_list_also_re_taints(self) -> None:
        """The fifth safe name, on the list input it is actually for."""
        assert_agrees("{{ p|unordered_list|upper }}", ["a <i>x</i> b", "c & d"])
        assert_agrees("{{ p|unordered_list }}", ["a <i>x</i> b", "c & d"])

    @pytest.mark.parametrize("safe_filter", ["safe", "urlize", "linebreaks", "linebreaksbr"])
    def test_a_safe_filter_LAST_still_marks_the_output_safe(self, safe_filter: str) -> None:
        """The other direction, so the fix is not simply 'escape everything'."""
        out = _rust.render_template(
            "{{ p|%s }}" % safe_filter, normalize_django_value({"p": "a\nb"})
        )
        assert "&lt;" not in out, f"{safe_filter} last should not be escaped: {out!r}"

    def test_the_inline_if_arm_agrees_with_the_variable_arm(self) -> None:
        """The second of the three drifted sites, exercised through its own node.

        A test that only used ``{{ p|... }}`` would leave the ``InlineIf`` arm
        unpinned, which is how the two drifted apart in the first place (#1646).
        """
        # A newline is load-bearing: `linebreaksbr` emits no tag at all without
        # one, so the "its own markup survived" check below would be vacuous.
        ctx = normalize_django_value({"p": "a <i>x</i>\nb", "c": True})
        for chain in ["linebreaks|upper", "linebreaks", "urlize|upper", "linebreaksbr"]:
            variable = _rust.render_template("{{ p|%s }}" % chain, ctx)
            # The parser splits the filters off the expression FIRST, so an
            # inline-if carries its filters after the whole conditional.
            inline_if = _rust.render_template('{{ p if c else "" |%s }}' % chain, ctx)
            assert variable == inline_if, f"{chain}: variable={variable!r} inline_if={inline_if!r}"
            # Non-vacuous: if both arms escaped everything, or neither did, the
            # equality above would hold for the wrong reason. So assert the two
            # chain shapes land on OPPOSITE sides of the escape decision.
            if chain.endswith("upper"):
                # The renderer's final escape fired: `urlize` had already turned
                # `<` into `&lt;`, `upper` made it `&LT;`, and escaping that
                # again is what produces the `&amp;`.
                assert "&amp;" in variable or "&lt;" in variable, (
                    f"{chain} was not escaped at the end: {variable!r}"
                )
            else:
                # The filter's OWN markup survived, i.e. it was marked safe.
                assert "<p>" in variable or "<br>" in variable or "<a " in variable, (
                    f"{chain} lost its own markup: {variable!r}"
                )
