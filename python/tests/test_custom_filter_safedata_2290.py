"""Regression: a custom filter must SEE Django's ``SafeData`` markers (#2290).

``Value`` — the enum that crosses the PyO3 boundary — is safety-blind.
``Value::String("<b>x</b>")`` is the same object whether the chain reached it
through ``|safe`` or straight off an unescaped context variable, so
``into_pyobject`` handed every custom filter a bare ``str``.  The consequence,
measured against Django 5.2 with a live ``@register.filter`` probe on one side
and ``_rust.register_custom_filter`` on the other::

    {{ p|probe }}        Django ('str', False, True)        djust ('str', False, True)
    {{ p|safe|probe }}   Django ('SafeString', True, True)  djust ('str', False, True)  <-- #2290

Which means Django's canonical ``needs_autoescape`` opening line

    autoescape = autoescape and not isinstance(value, SafeData)

could never take its second branch under djust, and the wider family —
``conditional_escape(value)``, ``format_html("{}", value)`` — always escaped.

The fix threads the renderer's existing ``InputSafety`` (#2284/#2283) through
``filter_registry::apply_custom_filter`` and restores the marker on the way in.
Both granularities drive a wrap, because they are two different Django states:

* ``container`` — ``mark_safe`` the VALUE (``|safe``, ``|escape``, a
  ``mark_safe``-carrying context variable).
* ``items`` — ``mark_safe`` each ELEMENT and leave the sequence plain, which is
  exactly what ``safeseq``/``escapeseq`` (``[mark_safe(o) for o in value]``)
  produce.  Django's ``safeseq`` output is NOT itself ``SafeData``, so
  answering ``container`` for it would grant a safety Django never granted.

Every assertion below is a comparison against **live Django**, not a
transcription of what djust does — and the direction that matters is asserted
separately: `TestNotMorePermissiveThanDjango` sweeps the whole chain space and
requires djust to grant no capability Django does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django import template  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402
from django.template.base import Template as DjangoTemplate  # noqa: E402
from django.utils.html import conditional_escape, format_html  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

from test_safe_survives_is_safe_filter_2274 import capabilities  # noqa: E402

# Deliberately un-guessable names: the Rust filter registry is process-global
# and is NOT cleared by ``reset_djust_globals`` (see ``djust.test_isolation``'s
# "explicitly NOT reset" list), so a collision with another module's filter
# would be a silent cross-test leak rather than an error.
PROBE = "_dj2290_probe"
PROBE_SEQ = "_dj2290_probe_seq"
IDENT = "_dj2290_ident"
CANON = "_dj2290_canon"
COND = "_dj2290_cond"
FMT = "_dj2290_fmt"

HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
]

_library = template.Library()


@_library.filter(name=PROBE, needs_autoescape=True)
def _probe(value, autoescape=True):
    """The issue's exact probe: what type, is it SafeData, what autoescape."""
    return repr((type(value).__name__, isinstance(value, SafeData), autoescape))


@_library.filter(name=PROBE_SEQ, needs_autoescape=True)
def _probe_seq(value, autoescape=True):
    """The ITEM granularity: the sequence's own safety AND each element's."""
    if isinstance(value, (list, tuple)):
        return repr(
            (
                type(value).__name__,
                isinstance(value, SafeData),
                [(type(v).__name__, isinstance(v, SafeData)) for v in value],
            )
        )
    return repr((type(value).__name__, isinstance(value, SafeData)))


@_library.filter(name=IDENT)
def _ident(value):
    """Returns its input untouched — the sharpest observable consequence.

    Django does not escape the result when the input was a ``SafeString``,
    because ``render_value_in_context`` reads ``__html__`` off the FINAL value.
    """
    return value


@_library.filter(name=CANON, needs_autoescape=True)
def _canon(value, autoescape=True):
    """Django's canonical ``needs_autoescape`` body, verbatim."""
    from django.utils.html import escape

    autoescape = autoescape and not isinstance(value, SafeData)
    return mark_safe("[" + (escape(value) if autoescape else str(value)) + "]")


@_library.filter(name=COND)
def _cond(value):
    """The first of the two widened-scope sinks the issue names."""
    return conditional_escape(value)


@_library.filter(name=FMT)
def _fmt(value):
    """The second: ``format_html`` conditional-escapes its arguments."""
    return format_html("{}", value)


@pytest.fixture(scope="module", autouse=True)
def _registered():
    """Register with Rust AND with a private Django engine, then tear down.

    The Django side is a private ``Engine`` with the library appended to
    ``template_builtins`` so the comparison templates need no ``{% load %}``
    and nothing is added to the project-wide registry.
    """
    for name, fn in _library.filters.items():
        _rust.register_custom_filter(
            name,
            fn,
            bool(getattr(fn, "is_safe", False)),
            bool(getattr(fn, "needs_autoescape", False)),
        )
    yield
    for name in _library.filters:
        _rust.unregister_custom_filter(name)


_engine = Engine(libraries={}, builtins=[])
_engine.template_builtins.append(_library)


def django_render(source: str, ctx: dict) -> str:
    return DjangoTemplate(source, engine=_engine).render(DjangoContext(ctx))


def djust_render(source: str, ctx: dict, safe_keys: list[str] | None = None) -> str:
    return _rust.render_template_with_dirs(source, normalize_django_value(ctx), [], safe_keys or [])


def assert_agrees(source: str, ctx: dict, safe_keys: list[str] | None = None) -> None:
    d = django_render(source, ctx)
    r = djust_render(source, ctx, safe_keys)
    assert r == d, f"{source} on {ctx!r}: django={d!r} djust={r!r}"


P = "<b>x</b>"
L = ["<b>x</b>", "<i>y</i>"]


# ---------------------------------------------------------------------------
# The reported divergence
# ---------------------------------------------------------------------------


class TestTheReportedTable:
    """The issue's two-row table, asserted against live Django."""

    def test_an_unmarked_value_is_not_safedata_in_either_engine(self) -> None:
        """The control row. It passed before the fix and must keep passing —
        without it, ``|safe|probe`` agreeing proves only that djust marks
        everything safe."""
        assert_agrees("{{ p|%s }}" % PROBE, {"p": P})
        assert "False" in djust_render("{{ p|%s }}" % PROBE, {"p": P})

    def test_safe_then_probe_hands_the_filter_a_SafeString(self) -> None:
        """The reported row: ``('SafeString', True, True)`` in Django,
        ``('str', False, True)`` in djust before this fix."""
        assert_agrees("{{ p|safe|%s }}" % PROBE, {"p": P})
        assert "SafeString" in djust_render("{{ p|safe|%s }}" % PROBE, {"p": P})

    def test_the_canonical_opening_line_can_now_take_its_second_branch(self) -> None:
        """The whole point of ``needs_autoescape``, run as a filter body.

        Before the fix ``isinstance(value, SafeData)`` was always ``False``, so
        `_canon` escaped on both rows and the two spellings were synonyms.
        """
        unmarked = djust_render("{{ p|%s }}" % CANON, {"p": P})
        marked = djust_render("{{ p|safe|%s }}" % CANON, {"p": P})
        assert unmarked != marked, "the SafeData branch is still unreachable"
        assert_agrees("{{ p|%s }}" % CANON, {"p": P})
        assert_agrees("{{ p|safe|%s }}" % CANON, {"p": P})


# ---------------------------------------------------------------------------
# `container` — the value itself is SafeData
# ---------------------------------------------------------------------------


class TestContainerSafety:
    """Every route by which the renderer sets ``InputSafety.container``."""

    @pytest.mark.parametrize(
        "producer",
        # One per mechanism `renderer::filter_output_is_safe` recognises:
        # SAFE_OUTPUT_FILTERS (`safe`, `escape`, `force_escape`, `linebreaks`)
        # and the per-call grant (`cut`, via `builtin_produced_safe`).
        ["safe", "escape", "force_escape", "linebreaks", "linebreaksbr"],
    )
    def test_a_safe_output_filter_marks_the_value_for_the_next_filter(self, producer: str) -> None:
        assert_agrees("{{ p|%s|%s }}" % (producer, PROBE), {"p": P})
        assert "SafeString" in djust_render("{{ p|%s|%s }}" % (producer, PROBE), {"p": P})

    def test_a_mark_safe_context_variable_marks_the_value(self) -> None:
        """The other source of ``container``: the view ``mark_safe``d it.

        djust carries context safety as an explicit ``safe_keys`` list built by
        ``rust_bridge._collect_safe_keys`` from ``__html__``-carrying values,
        so the real path is exercised by passing it — a bare
        ``render_template`` cannot express the state at all.
        """
        assert_agrees("{{ p|%s }}" % PROBE, {"p": mark_safe(P)}, safe_keys=["p"])
        assert "SafeString" in djust_render("{{ p|%s }}" % PROBE, {"p": P}, ["p"])

    def test_upper_re_taints_and_the_probe_sees_a_plain_str(self) -> None:
        """The gate must close again.

        ``upper`` is one of the few string filters Django registers
        ``is_safe=False``, precisely because upper-casing ``&lt;`` yields
        ``&LT;`` — which every browser still decodes to ``<``. So the grant
        must NOT survive it, and the probe must be back to a plain ``str``.
        """
        src = "{{ p|safe|upper|%s }}" % PROBE
        assert_agrees(src, {"p": P})
        assert "False" in djust_render(src, {"p": P})

    @pytest.mark.parametrize("preserver", ["lower", "striptags", "title", "slice:':4'"])
    def test_an_is_safe_filter_carries_the_grant_through(self, preserver: str) -> None:
        """The other half, and the reason the test above names ``upper`` rather
        than "a plain filter": these four ARE ``is_safe=True`` in Django, so
        Django keeps the ``SafeString`` and djust must too. Written as its own
        case after the first draft asserted the opposite and Django disagreed.
        """
        src = "{{ p|safe|%s|%s }}" % (preserver, PROBE)
        assert_agrees(src, {"p": P})
        assert "SafeString" in djust_render(src, {"p": P})

    def test_the_identity_filter_stops_escaping_a_value_that_was_already_safe(
        self,
    ) -> None:
        """The user-visible half. ``{{ p|safe|ident }}`` was escaped by djust
        and live in Django, because Django reads ``__html__`` off the value the
        filter returned — which is the very object it was handed."""
        assert_agrees("{{ p|safe|%s }}" % IDENT, {"p": P})
        assert djust_render("{{ p|safe|%s }}" % IDENT, {"p": P}) == P

    def test_the_identity_filter_still_escapes_an_unmarked_value(self) -> None:
        """The gate-off sibling of the row above: same filter, no ``|safe``."""
        assert_agrees("{{ p|%s }}" % IDENT, {"p": P})
        assert capabilities(djust_render("{{ p|%s }}" % IDENT, {"p": P})) == set()

    @pytest.mark.parametrize("value,expected_type", [(42, "int"), (1.5, "float"), (True, "bool")])
    def test_a_non_string_is_passed_through_unwrapped(self, value, expected_type) -> None:
        """The documented boundary of the wrap, pinned rather than asserted in
        prose (#1867).

        Django's ``mark_safe`` STRINGIFIES a non-``str`` — ``mark_safe(42)`` is
        ``SafeString('42')`` — so Django really does hand ``{{ n|safe|probe }}``
        a ``SafeString``. djust deliberately does not follow it there: doing so
        would change the TYPE an existing filter receives (an ``int`` becoming a
        string), which is a pre-existing ``|safe``-on-a-non-``str`` SHAPE
        divergence with its own blast radius rather than the safety gap #2290 is
        about. The residue is djust reporting ``SafeData`` False where Django
        reports True — the ESCAPING direction, and unchanged by this fix.
        Tracked separately; see the PR body.
        """
        out = djust_render("{{ p|safe|%s }}" % PROBE, {"p": value})
        assert f"&#x27;{expected_type}&#x27;, False" in out, out
        assert "SafeString" in django_render("{{ p|safe|%s }}" % PROBE, {"p": value})


class TestTheWiderScope:
    """The issue's "scope is wider than ``needs_autoescape``" section.

    Neither filter below is registered ``needs_autoescape``; both inspect
    safety through a helper, and both were unconditionally escaping.
    """

    @pytest.mark.parametrize("name", [COND, FMT])
    def test_the_helper_passes_marked_markup_through(self, name: str) -> None:
        assert_agrees("{{ p|safe|%s }}" % name, {"p": P})
        assert djust_render("{{ p|safe|%s }}" % name, {"p": P}) == P

    @pytest.mark.parametrize("name", [COND, FMT])
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_the_helper_still_escapes_unmarked_markup(self, name: str, payload: str) -> None:
        out = djust_render("{{ p|%s }}" % name, {"p": payload})
        assert capabilities(out) == set(), f"LIVE: {out!r}"
        assert out == django_render("{{ p|%s }}" % name, {"p": payload})


# ---------------------------------------------------------------------------
# `items` — the sequence's ELEMENTS are SafeData, the sequence is not
# ---------------------------------------------------------------------------


class TestItemSafety:
    """``safeseq``/``escapeseq`` mark the items and never the sequence.

    This is a genuinely separate mechanism from ``container``: gating the wrap
    on ``container`` alone leaves every one of these red, and answering
    ``container`` for them would hand a filter a safety Django withholds.
    """

    @pytest.mark.parametrize("producer", ["safeseq", "escapeseq"])
    def test_the_items_are_SafeString_and_the_list_is_not(self, producer: str) -> None:
        src = "{{ p|%s|%s }}" % (producer, PROBE_SEQ)
        assert_agrees(src, {"p": L})
        out = djust_render(src, {"p": L})
        # Both halves, or the assertion cannot tell a per-item wrap from a
        # whole-container one — which is the mistake this field exists to
        # prevent.
        assert "(&#x27;list&#x27;, False," in out, out
        assert "SafeString" in out, out

    def test_an_unmarked_sequence_reaches_the_filter_with_plain_items(self) -> None:
        """The control. ``{{ p|probe_seq }}`` must stay all-``False``."""
        src = "{{ p|%s }}" % PROBE_SEQ
        assert_agrees(src, {"p": L})
        assert "SafeString" not in djust_render(src, {"p": L})

    def test_slice_carries_the_item_grant_because_it_returns_the_same_objects(
        self,
    ) -> None:
        """``ITEM_SAFETY_PRESERVING_FILTERS`` — Django's ``slice`` hands back
        the very ``SafeString``s ``safeseq`` made."""
        src = "{{ p|safeseq|slice:':1'|%s }}" % PROBE_SEQ
        assert_agrees(src, {"p": L})
        assert "SafeString" in djust_render(src, {"p": L})

    def test_a_filter_that_rebuilds_the_items_drops_the_grant(self) -> None:
        """``make_list`` splits a fresh string into fresh characters, so the
        grant must NOT survive it — the escaping direction."""
        src = "{{ p|safeseq|make_list|%s }}" % PROBE_SEQ
        assert "SafeString" not in djust_render(src, {"p": L})

    def test_a_tuple_input_arrives_as_a_marked_LIST_in_both_engines(self) -> None:
        """Why the wrap needs only a list arm.

        Django's ``safeseq`` is ``[mark_safe(o) for o in value]`` — a list
        comprehension — so a TUPLE input is already a list by the time any item
        grant exists. A first draft of ``mark_input_safety`` carried a parallel
        tuple arm; the gate-off reported it SURVIVED because nothing can reach
        it, and it was deleted rather than tested around (#1859). This case is
        what proves the list arm is the whole surface.
        """
        src = "{{ p|safeseq|%s }}" % PROBE_SEQ
        assert_agrees(src, {"p": ("<b>x</b>", "<i>y</i>")})
        out = djust_render(src, {"p": ("<b>x</b>", "<i>y</i>")})
        assert "(&#x27;list&#x27;, False," in out, out
        assert "SafeString" in out, out

    def test_a_non_string_element_is_left_alone(self) -> None:
        """``mark_safe`` is applied only to a ``str``; an ``int`` element keeps
        its type rather than becoming ``SafeString('2')``."""
        out = djust_render("{{ p|safeseq|%s }}" % PROBE_SEQ, {"p": ["<b>", 2]})
        assert "&#x27;int&#x27;, False" in out, out


# ---------------------------------------------------------------------------
# The direction that matters
# ---------------------------------------------------------------------------


class TestNotMorePermissiveThanDjango:
    """djust may stop over-escaping; it must not out-permit Django anywhere.

    This is the custom-filter twin of
    ``test_escape_chain_and_sequence_filters_2281_2283.py::
    test_no_chain_containing_escape_is_more_permissive_than_django`` — the
    single-build half of ``scripts/filter-parity-differential.py``, which grew
    a ``--custom`` corpus in this PR for the two-build half.
    """

    #: Every built-in that can hand a custom filter a safety grant, plus the
    #: ones that take it away again. Composed on BOTH sides of each probe so a
    #: producer-then-consumer and a consumer-then-producer chain are both swept.
    HOT = [
        "safe",
        "escape",
        "force_escape",
        "safeseq",
        "escapeseq",
        "slice:':2'",
        "linebreaks",
        "linebreaksbr",
        "upper",
        "lower",
        "striptags",
        "make_list",
        "join:'<br>'",
        "first",
        "last",
        "cut:'b'",
        "default:'D'",
        "add:'1'",
        "urlize",
        "unordered_list",
        "linenumbers",
        "title",
        "length",
        "pprint",
    ]
    #: The custom filters worth composing: every one that inspects safety, plus
    #: the identity filter (the sharpest — it returns exactly what it was given).
    CUSTOM = [PROBE, PROBE_SEQ, IDENT, CANON, COND, FMT]
    VALUES = [
        "<img src=x onerror=alert(1)>",
        '"><svg onload=alert(1)>',
        ["<img src=x onerror=alert(1)>", "b"],
        {"k": "<img src=x onerror=alert(1)>"},
        42,
        None,
    ]

    def test_no_chain_through_a_custom_filter_out_permits_django(self) -> None:
        leaks, compared = [], 0
        for custom in self.CUSTOM:
            for builtin in self.HOT:
                for source in (
                    "{{ p|%s|%s }}" % (builtin, custom),
                    "{{ p|%s|%s }}" % (custom, builtin),
                    "{{ p|safe|%s|%s }}" % (builtin, custom),
                ):
                    for value in self.VALUES:
                        try:
                            d = django_render(source, {"p": value})
                        except Exception:  # noqa: BLE001 — no bar to compare against
                            continue
                        try:
                            r = djust_render(source, {"p": value})
                        except Exception:  # noqa: BLE001 — a raise grants nothing
                            continue
                        compared += 1
                        extra = capabilities(r) - capabilities(d)
                        if extra:
                            leaks.append((source, value, sorted(extra), r[:200]))
        assert compared > 800, f"the sweep only compared {compared} cells — it is not sweeping"
        assert leaks == [], f"{len(leaks)} cells more permissive than Django: {leaks[:3]}"

    @pytest.mark.parametrize("payload", HOSTILE)
    @pytest.mark.parametrize("custom", [PROBE, IDENT, CANON, COND, FMT])
    def test_hostile_input_reaching_a_custom_filter_unmarked_stays_inert(
        self, custom: str, payload: str
    ) -> None:
        """Stated on its own rather than only inside the sweep: a value nothing
        ever marked must reach the filter as a plain ``str`` and leave inert."""
        out = djust_render("{{ p|%s }}" % custom, {"p": payload})
        assert capabilities(out) == set(), f"LIVE: {out!r}"


# ---------------------------------------------------------------------------
# Structural pins
# ---------------------------------------------------------------------------

_TEMPLATES_SRC = Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src"


def test_the_only_custom_filter_call_site_forwards_the_real_input_safety() -> None:
    """The wrap is only as good as what reaches it.

    ``apply_custom_filter`` has exactly ONE production caller. Pinning the count
    is what makes this mechanical: a second dispatch path added later that
    passes ``InputSafety::default()`` would re-open #2290 for whatever it
    routes, and nothing else in the suite would notice.
    """
    src = (_TEMPLATES_SRC / "filters.rs").read_text()
    calls = re.findall(r"filter_registry::apply_custom_filter\((.*?)\n    \) \{", src, re.S)
    assert len(calls) == 1, f"expected 1 apply_custom_filter call site, found {len(calls)}"
    assert "input_safety" in calls[0], calls[0]
    assert "InputSafety::default()" not in calls[0], calls[0]


def test_the_wrap_reads_only_the_input_safety_fields() -> None:
    """The security invariant, as a source pin.

    #2290's whole licence is that the wrap is gated on the renderer's report of
    the INPUT's safety and on nothing else. A future edit that consulted the
    filter's own ``is_safe`` metadata, the ``autoescape`` flag, or the value's
    content would be granting UNEARNED safety — a custom filter that trusts
    ``SafeData`` would then emit attacker input raw.
    """
    body = (_TEMPLATES_SRC / "filter_registry.rs").read_text()
    fn = body.split("fn mark_input_safety<", 1)[1].split("\n}\n", 1)[0]
    code = "\n".join(ln for ln in fn.splitlines() if not ln.strip().startswith("//"))
    for forbidden in ("meta.", "is_safe", "autoescape", "needs_autoescape"):
        assert forbidden not in code, f"mark_input_safety must not consult {forbidden!r}"
    assert code.count("input_safety.container") >= 1
    assert code.count("input_safety.items") >= 1
