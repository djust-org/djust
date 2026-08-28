"""#2284 — the four ``needs_autoescape=True`` filters must skip their own
escape when the value handed to them was already ``SafeData``.

Django registers ``linebreaks``, ``linebreaksbr``, ``urlize`` and
``urlizetrunc`` with ``needs_autoescape=True``, and each body opens::

    autoescape = autoescape and not isinstance(value, SafeData)

djust ignored the whole expression and escaped unconditionally, so markup a
view deliberately marked safe was escaped away from *inside* the filter::

    {{ p|safe|linebreaks }}   django='<p><b>x</b></p>'  djust='<p>&lt;b&gt;x&lt;/b&gt;</p>'

What the issue frames as a ``needs_autoescape`` gap is really two independent
terms, and only one of them is reachable in djust:

* ``autoescape`` — the ``{% autoescape %}`` block policy. djust has no such
  tag; the parser *rejects* it (``TestAutoescapeBlockIsStillUnsupported``), so
  this term is pinned ``True`` and its ``False`` branch is unreachable. It is
  deliberately NOT implemented — see that class.
* ``not isinstance(value, SafeData)`` — fully reachable today, via ``|safe``
  and via a view's ``mark_safe()``. This is the live divergence, and it is
  what this file pins.

Measured on ``52b727d0`` (Python 3.12.9, Django from the repo venv), with a
4000-value adversarial corpus × 3 columns:

===============  ===========  ==========
column           before       after
===============  ===========  ==========
``{{ p|X }}``    0 differ     0 differ
``{{ p|safe|X }}``  4000 differ  0 differ
``mark_safe`` ctx   4000 differ  0 differ
===============  ===========  ==========

(counts are for ``linebreaks`` + ``linebreaksbr``, which reach full parity.
``urlize``/``urlizetrunc`` carry a SEPARATE, pre-existing URL-DETECTION gap —
djust matches on a regex where Django word-splits and ``smart_urlquote``s — and
that gap is identical in all three columns both before and after, which is what
proves it is orthogonal to the escape decision. It is not in scope here.)

Security direction: this only ever escapes LESS, and only for values something
already declared safe. ``TestHostileInputFromAnUnsafeSourceStaysInert`` asserts
the other half from a real HTML parse rather than a substring match, because a
test that only checks "unsafe input is escaped" also passes on output that
escapes everything and therefore proves nothing (#2259 recorded the same trap).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

#: Read from Django's LIVE registry rather than transcribed, so a Django
#: release that adds or drops a ``needs_autoescape`` built-in goes red here
#: instead of diverging silently. On Django 5.2 this is SEVEN names, not the
#: four the issue lists — see ``TestTheRegistryHasSevenNotFour``.
DJANGO_NEEDS_AUTOESCAPE = frozenset(
    n for n, f in register.filters.items() if getattr(f, "needs_autoescape", False)
)

#: The four #2284 fixes: the ones whose body is the whole-value form
#: ``autoescape = autoescape and not isinstance(value, SafeData)``.
FOUR = ["linebreaks", "linebreaksbr", "urlize", "urlizetrunc"]

#: ``linenumbers`` carries the SAME whole-value clause and was ALREADY correct
#: before #2284 — it does not escape internally, so the renderer's own output
#: escape (skipped for safe input via ``IS_SAFE_FILTERS``) lands on exactly
#: Django's answer. ``renderer.rs`` states this; ``TestLinenumbersWasAlready
#: Correct`` is the executable form of the claim.
ALREADY_CORRECT = ["linenumbers"]

#: ``join`` and ``unordered_list`` are ``needs_autoescape`` too, but their
#: bodies use a DIFFERENT shape — ``conditional_escape`` applied PER ELEMENT
#: of a sequence, not ``isinstance(value, SafeData)`` on the value as a whole.
#: A list of ``mark_safe``d strings must come through live element by element.
#:
#: They were out of scope for #2284 and this bucket was named
#: ``SEQUENCE_SHAPE``, carrying a ``TestSequenceShapeIsOutOfScopeAndStillDiverges``
#: class that ASSERTED the divergence was still present and told whoever closed
#: the follow-up to move the names here and delete it. #2287 did exactly that:
#: the item granularity is now seeded from the context by
#: ``Context::items_are_safe``, and the parity that class denied is pinned in
#: ``python/tests/test_context_item_safety_2287.py``.
PER_ELEMENT_FIXED_IN_2287 = ["join", "unordered_list"]

#: ``urlize``/``urlizetrunc`` have a separate, pre-existing URL-DETECTION gap
#: (see the module docstring). Byte-equality with Django is asserted only for
#: the two that are clean; the urlize pair is covered by the escape-specific
#: assertions in ``TestUrlizeHonoursSafeInput`` instead, which pin the exact
#: property #2284 changed without depending on the detector agreeing.
BYTE_EQUAL = ["linebreaks", "linebreaksbr"]

ARGS = {"urlizetrunc": ':"30"'}

#: Payloads with no URL-shaped text in them, so ``urlize``'s detector cannot
#: fire and every one of the four filters is comparable byte-for-byte.
NO_URL_PAYLOADS = [
    "<b>x</b>",
    "a\n\nb <i>y</i>",
    "",
    "plain text",
    "<b>a</b>\nnext\r\n\r\nlast",
    "&amp; already &lt;escaped&gt;",
    '<a title="q">t</a>',
]

#: Hostile payloads. Each targets a different sink, so a fix that only handles
#: ``<`` does not pass the whole set.
HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "<a href=javascript:alert(1)>x</a>",
    "<b>ok</b>\n\n<img src=x onerror=alert(2)>",
]


def cell(name: str, safe_prefix: bool) -> str:
    return "{{ p|%s%s%s }}" % ("safe|" if safe_prefix else "", name, ARGS.get(name, ""))


def django_render(source: str, value) -> str:
    return DjangoTemplate(source).render(DjangoContext({"p": value}))


def djust_render(source: str, value) -> str:
    """``|safe``-in-template / plain-context path."""
    return _rust.render_template(source, normalize_django_value({"p": value}))


def djust_render_marked_safe(source: str, payload: str) -> str:
    """The CONTEXT-level ``mark_safe`` path, driven the way production does.

    ``_rust.render_template`` has NO channel for context safety — it takes a
    plain dict, and a ``SafeString`` in it is indistinguishable from a ``str``
    by the time Rust sees it (``crates/djust_templates/tests/
    test_xss_prevention.rs`` says so outright). The real path is
    ``RustLiveView.mark_safe_keys``, fed from ``_collect_safe_keys`` — exactly
    what ``mixins/rust_bridge.py`` and ``mixins/template.py`` do. A probe that
    used ``render_template`` here would report a failure that production does
    not have, which is precisely the reproduction-fidelity trap.
    """
    view = _rust.RustLiveView(source)
    normalized = normalize_django_value({"p": mark_safe(payload)})
    safe_keys: list[str] = []
    for key, value in normalized.items():
        safe_keys.extend(_collect_safe_keys(value, key))
    view.update_state(normalized)
    if safe_keys:
        view.mark_safe_keys(safe_keys)
    return view.render()


class _Capabilities(HTMLParser):
    """What a browser would EXECUTE, not what it would display.

    Substring-matching an entity-decoded string invents exploits that do not
    exist: ``&lt;img …&gt;`` *reads* as ``<img …>``, but a character reference
    is resolved after the tokenizer has decided where tags begin, so it never
    creates an element. Only a literal ``<`` does.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.caps: set[str] = set()

    def handle_starttag(self, tag, attrs):
        self.caps.add("tag:" + tag)
        for name, value in attrs:
            if name.startswith("on"):
                self.caps.add("evt:" + name)
            if value and html.unescape(value).strip().lower().startswith("javascript:"):
                self.caps.add("url:" + name)

    handle_startendtag = handle_starttag


def capabilities(out: str) -> set[str]:
    parser = _Capabilities()
    try:
        parser.feed(out)
        parser.close()
    except Exception:  # noqa: BLE001 — a parse error grants no capability
        pass
    return parser.caps


def test_capabilities_probe_is_not_vacuous():
    """Every security assertion below is only as good as this function; if it
    returned an empty set unconditionally the whole file would be green and
    worthless. Pin both directions of the distinction it exists to draw."""
    assert capabilities("<img src=x onerror=alert(1)>") == {"tag:img", "evt:onerror"}
    assert capabilities("&lt;img src=x onerror=alert(1)&gt;") == set()
    assert capabilities("<p>&lt;b&gt;x&lt;/b&gt;</p>") == {"tag:p"}
    assert capabilities("<a href=javascript:alert(1)>x</a>") == {"tag:a", "url:href"}


# ---------------------------------------------------------------------------
# The premise the decision rests on
# ---------------------------------------------------------------------------


class TestAutoescapeBlockIsStillUnsupported:
    """``{% autoescape %}`` is NOT implemented, deliberately (#2284).

    Django's ``needs_autoescape`` expression has two terms. #2284 implements
    the ``SafeData`` one because it is reachable; the ``autoescape`` one is
    pinned ``True`` because there is no way to make it ``False``. Implementing
    the block tag was considered and declined — it is a much larger change
    (block-scoped policy through every render arm) whose only effect would be
    to let templates turn escaping OFF, and no divergence today needs it.

    These two tests are what makes that decision falsifiable rather than
    assumed: if someone adds the tag, the first goes red and is a direct
    instruction to thread the real block policy into
    ``apply_filter_full_safe``'s ``input_was_safe`` sibling instead of the
    pinned ``true``.
    """

    def test_the_tag_is_rejected_by_the_parser(self):
        for source in (
            "{% autoescape off %}{{ p }}{% endautoescape %}",
            "{% autoescape on %}{{ p }}{% endautoescape %}",
        ):
            with pytest.raises(Exception) as exc:
                djust_render(source, "<b>x</b>")
            assert "autoescape" in str(exc.value), (
                f"{source} no longer raises for the expected reason: {exc.value}"
            )

    def test_django_does_support_it_which_is_why_this_is_a_gap_and_not_parity(self):
        """The other half. Without this, the test above would also pass if
        Django had dropped the tag, and it would be pinning nothing."""
        assert django_render("{% autoescape off %}{{ p }}{% endautoescape %}", "<b>x</b>") == (
            "<b>x</b>"
        )


class TestTheRegistryHasSevenNotFour:
    """The issue says the ``needs_autoescape`` built-ins are four. They are
    SEVEN, and the extra three are the reason this file partitions them rather
    than treating "needs_autoescape" as one bug.

    The partition is by the SHAPE OF THE FILTER BODY, which is what decides
    whether the fix applies:

    ==================  =============================================  ========
    names               body                                           status
    ==================  =============================================  ========
    ``FOUR``            ``autoescape and not isinstance(v, SafeData)``  fixed here
    ``ALREADY_CORRECT`` same clause, but djust never escaped inside     already ok
    ``PER_ELEMENT…``    ``conditional_escape`` PER ELEMENT             fixed in #2287
    ==================  =============================================  ========
    """

    def test_the_partition_covers_the_registry_exactly(self):
        """Mechanical, in BOTH directions. A Django release that adds an
        eighth ``needs_autoescape`` filter — or drops one — goes red here and
        forces a decision about which bucket it belongs in, rather than
        silently falling through the gap the issue's count of four left."""
        partition = (
            frozenset(FOUR) | frozenset(ALREADY_CORRECT) | frozenset(PER_ELEMENT_FIXED_IN_2287)
        )
        assert DJANGO_NEEDS_AUTOESCAPE == partition, (
            f"unclassified in djust: {sorted(DJANGO_NEEDS_AUTOESCAPE - partition)}; "
            f"not needs_autoescape in Django: {sorted(partition - DJANGO_NEEDS_AUTOESCAPE)}"
        )

    def test_the_buckets_do_not_overlap(self):
        """Otherwise the count above could be satisfied by a name appearing
        twice while a real one is missing."""
        names = FOUR + ALREADY_CORRECT + PER_ELEMENT_FIXED_IN_2287
        assert len(names) == len(set(names)) == 7, names

    def test_all_seven_are_also_is_safe(self):
        """``FOUR`` are in ``SAFE_OUTPUT_FILTERS`` unconditionally, and that
        stays earned only because Django pairs both flags. A name that was
        ``needs_autoescape`` WITHOUT ``is_safe`` would need different handling
        — pin the pairing so such a name cannot arrive unnoticed."""
        for name in DJANGO_NEEDS_AUTOESCAPE:
            assert getattr(register.filters[name], "is_safe", False), name


class TestLinenumbersColumnsThatWereAlreadyCorrect:
    """These three columns agreed before #2291 and still agree after it.

    This class was originally named ``TestLinenumbersWasAlreadyCorrect`` and
    said so in its docstring — it ran ``renderer.rs``'s prose invariant that
    ``linenumbers`` was deliberately absent from ``SAFE_OUTPUT_FILTERS``
    because djust's whole-output escape and Django's per-line escape are
    byte-identical, found the three columns below in agreement, and concluded
    the filter was correct.

    It was not. The invariant holds only while the render-time escape actually
    RUNS, and the one column that removes it — a TRAILING ``|safe`` — was never
    sampled. ``{{ p|linenumbers|safe }}`` emitted attacker markup live (#2291).
    A leading ``|safe`` was sampled and is a different question entirely (it
    means the author trusts the input, and Django emits it live too), which is
    exactly why sampling it read as coverage of the safety axis.

    Kept, renamed, and narrowed to the claim it can actually support: THESE
    columns agree. #2291's own file owns the trailing-``|safe`` column and the
    equality between the two escape placements.
    """

    @pytest.mark.parametrize("payload", NO_URL_PAYLOADS)
    def test_these_three_columns_agree_with_django(self, payload):
        plain = "{{ p|linenumbers }}"
        assert djust_render(plain, payload) == django_render(plain, payload)
        safe = "{{ p|safe|linenumbers }}"
        assert djust_render(safe, payload) == django_render(safe, payload)
        assert djust_render_marked_safe(plain, payload) == django_render(plain, mark_safe(payload))

    @pytest.mark.parametrize("payload", NO_URL_PAYLOADS)
    def test_and_the_column_that_did_not(self, payload):
        """The one #2291 fixed — here so this file cannot mislead again."""
        source = "{{ p|linenumbers|safe }}"
        assert djust_render(source, payload) == django_render(source, payload)


# ---------------------------------------------------------------------------
# Mechanism 1+2 — linebreaks / linebreaksbr skip the escape for safe input
# ---------------------------------------------------------------------------


class TestLinebreaksHonoursSafeInput:
    @pytest.mark.parametrize("name", BYTE_EQUAL)
    @pytest.mark.parametrize("payload", NO_URL_PAYLOADS)
    def test_safe_filter_prefix_agrees_with_django(self, name, payload):
        """``{{ p|safe|X }}`` byte-agrees with Django. These are the cells the
        issue reported."""
        source = cell(name, safe_prefix=True)
        expected = django_render(source, payload)
        assert djust_render(source, payload) == expected

    @pytest.mark.parametrize("name", BYTE_EQUAL)
    @pytest.mark.parametrize("payload", NO_URL_PAYLOADS)
    def test_context_mark_safe_agrees_with_django(self, name, payload):
        """The same thing through the CONTEXT channel — a view that did
        ``mark_safe()`` and a template that pipes it straight into the filter.
        This is the realistic shape (rendered markdown, a sanitized comment)
        and it goes through completely different plumbing from ``|safe``."""
        source = cell(name, safe_prefix=False)
        expected = django_render(source, mark_safe(payload))
        assert djust_render_marked_safe(source, payload) == expected

    @pytest.mark.parametrize("name", BYTE_EQUAL)
    @pytest.mark.parametrize("payload", NO_URL_PAYLOADS)
    def test_plain_input_is_unchanged_and_still_escaped(self, name, payload):
        """The other half of every cell. Without this the tests above would
        pass for the wrong reason — 'escape nothing, ever' also agrees with
        Django on the safe columns."""
        source = cell(name, safe_prefix=False)
        expected = django_render(source, payload)
        got = djust_render(source, payload)
        assert got == expected
        if "<" in payload:
            assert "&lt;" in got, f"{source} on {payload!r} stopped escaping: {got!r}"

    def test_the_reported_cells_verbatim(self):
        """The four lines from the issue body, as literals rather than as a
        parametrization, so the report itself is pinned."""
        assert djust_render("{{ p|safe|linebreaks }}", "<b>x</b>") == "<p><b>x</b></p>"
        assert djust_render("{{ p|safe|linebreaksbr }}", "<b>x</b>") == "<b>x</b>"
        assert djust_render("{{ p|safe|urlize }}", "<b>x</b>") == "<b>x</b>"
        assert djust_render('{{ p|safe|urlizetrunc:"30" }}', "<b>x</b>") == "<b>x</b>"

    def test_a_later_plain_filter_still_retaints(self):
        """``|safe`` reaching the filter does not make the CHAIN permanently
        safe. ``upper`` is ``is_safe=False`` in Django, so the whole thing is
        escaped again at output — including the ``<p>`` the filter generated."""
        source = "{{ p|safe|linebreaks|upper }}"
        expected = django_render(source, "<b>x</b>")
        assert djust_render(source, "<b>x</b>") == expected
        assert "<p>" not in djust_render(source, "<b>x</b>")


# ---------------------------------------------------------------------------
# Mechanism 3 — urlize skips the escape of the text it QUOTES
# ---------------------------------------------------------------------------


class TestUrlizeHonoursSafeInput:
    """``urlize``/``urlizetrunc`` carry a pre-existing URL-detection gap, so
    these assert the escape property directly rather than byte-equality."""

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_safe_markup_around_a_url_survives(self, name):
        source = cell(name, safe_prefix=True)
        got = djust_render(source, "see http://ex.com/ <b>z</b>")
        assert got == django_render(source, "see http://ex.com/ <b>z</b>")
        assert "<b>z</b>" in got, got

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_the_same_markup_from_an_unsafe_source_is_escaped(self, name):
        source = cell(name, safe_prefix=False)
        got = djust_render(source, "see http://ex.com/ <b>z</b>")
        assert got == django_render(source, "see http://ex.com/ <b>z</b>")
        assert "<b>z</b>" not in got and "&lt;b&gt;z&lt;/b&gt;" in got, got

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_context_mark_safe_reaches_urlize_too(self, name):
        source = cell(name, safe_prefix=False)
        got = djust_render_marked_safe(source, "see http://ex.com/ <b>z</b>")
        assert got == django_render(source, mark_safe("see http://ex.com/ <b>z</b>"))
        assert "<b>z</b>" in got, got

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_trailing_punctuation_after_a_url_follows_the_flag(self, name):
        """The ``trail`` arm of Django's ``if autoescape and not safe_input``.
        ``<`` immediately after a URL is split off as trailing text, so this
        exercises a DIFFERENT branch from the between-matches text above —
        a fix that threaded the flag to only one of them fails here."""
        payload = "http://ex.com/a <b>t</b>"
        for safe_prefix, want_live in ((True, True), (False, False)):
            source = cell(name, safe_prefix=safe_prefix)
            got = djust_render(source, payload)
            assert got == django_render(source, mark_safe(payload) if safe_prefix else payload)
            assert ("<b>t</b>" in got) is want_live, (safe_prefix, got)


# ---------------------------------------------------------------------------
# Mechanism 4 — the href escape is NOT conditional
# ---------------------------------------------------------------------------


class TestUrlizeHrefIsEscapedUnconditionally:
    """Django writes ``self.url_template % {"href": escape(url), ...}``
    OUTSIDE its ``if autoescape and not safe_input`` branch.

    That is not an oversight: the ``href`` lands inside ``href="…"``, so an
    unescaped ``"`` or ``&`` there breaks the attribute regardless of what the
    surrounding escaping policy is. Making the href conditional along with the
    display text — the obvious way to write this fix — is an XSS that Django
    does not have, and it is invisible to any test that only looks at the
    anchor's text.
    """

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_ampersand_in_a_safe_input_url_is_still_escaped_in_the_href(self, name):
        source = cell(name, safe_prefix=True)
        got = djust_render(source, "http://ex.com/?a=1&b=2")
        assert got == django_render(source, "http://ex.com/?a=1&b=2")
        href = re.search(r'href="([^"]*)"', got)
        assert href, got
        assert "&amp;" in href.group(1), (
            f"the href was emitted with a raw & under safe input: {got!r}"
        )

    @pytest.mark.parametrize("name", ["urlize", "urlizetrunc"])
    def test_a_safe_input_url_cannot_break_out_of_the_href_attribute(self, name):
        """The capability form of the same claim: whatever the URL contains,
        the anchor must expose no event handler and no ``javascript:`` URL."""
        source = cell(name, safe_prefix=True)
        for payload in (
            "http://ex.com/?a=1&b=2",
            "http://ex.com/x&quot;onmouseover=alert(1)",
            "http://ex.com/&amp;&lt;",
        ):
            got = djust_render(source, payload)
            caps = capabilities(got)
            assert not any(c.startswith("evt:") for c in caps), (payload, got, caps)
            assert not any(c.startswith("url:") for c in caps), (payload, got, caps)


# ---------------------------------------------------------------------------
# The security direction
# ---------------------------------------------------------------------------


class TestHostileInputFromAnUnsafeSourceStaysInert:
    """The assertion is that NONE OF THE PAYLOAD'S OWN capabilities survive —
    not that the output has none at all.

    ``linebreaks`` emits ``<p>``/``<br>`` and ``urlize`` emits ``<a>`` by
    design, so ``capabilities(out) == set()`` would fail on correct output and
    would have to be relaxed into something weaker. Intersecting with the
    payload's capabilities keeps the assertion sharp — a leaked ``<img
    onerror>`` is caught, a generated ``<p>`` is not — and it stays
    non-vacuous because ``test_marking_it_safe_is_what_makes_it_live`` asserts
    the SAME intersection is non-empty when the value is marked safe.
    """

    @pytest.mark.parametrize("name", FOUR)
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_no_needs_autoescape_filter_passes_an_unmarked_payload_through(self, name, payload):
        """The whole point of the ``not input_was_safe`` guard. If it were ever
        inverted, or seeded ``true``, every one of these goes live."""
        source = cell(name, safe_prefix=False)
        got = djust_render(source, payload)
        assert capabilities(payload), f"payload {payload!r} grants nothing to leak"
        leaked = capabilities(got) & capabilities(payload)
        assert leaked == set(), f"{source} on {payload!r} leaked {leaked} -> {got!r}"

    @pytest.mark.parametrize("name", FOUR)
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_and_the_context_channel_is_no_looser(self, name, payload):
        """Same claim on the ``mark_safe_keys`` path, WITHOUT marking the key —
        a payload that reaches the context unmarked must be escaped there too."""
        source = cell(name, safe_prefix=False)
        view = _rust.RustLiveView(source)
        view.update_state(normalize_django_value({"p": payload}))
        got = view.render()
        leaked = capabilities(got) & capabilities(payload)
        assert leaked == set(), f"{source} on {payload!r} leaked {leaked} -> {got!r}"

    @pytest.mark.parametrize("payload", HOSTILE)
    def test_marking_it_safe_is_what_makes_it_live_and_django_agrees(self, payload):
        """The inverse, so the class above cannot pass by escaping everything.
        A payload the caller explicitly marked safe DOES come through live —
        that is `mark_safe`'s contract, and Django's output is the reference."""
        source = cell("linebreaks", safe_prefix=True)
        got = djust_render(source, payload)
        assert got == django_render(source, payload)
        assert capabilities(got) & capabilities(payload), (
            f"marking {payload!r} safe changed nothing: {got!r}"
        )


# ---------------------------------------------------------------------------
# Mechanism 5 — every render site threads the flag (#1646)
# ---------------------------------------------------------------------------


class TestTheFirstofArmThreadsItToo:
    """The third ``apply_filter_full_safe`` call site is ``get_value_safe``,
    which is what ``{% firstof %}`` / ``{% cycle %}`` render through.

    It exists as its own class because gating that ONE site off reddens
    nothing else: the two ``{{ … }}`` arms are covered by every other test in
    this file, so without these assertions the firstof arm's threading would
    be pinned only by a source grep — and a grep pin is not behaviour (#1859).
    """

    @pytest.mark.parametrize("name", FOUR)
    def test_firstof_honours_safe_input_exactly_as_the_variable_arm_does(self, name):
        source = "{%% firstof p|safe|%s%s %%}" % (name, ARGS.get(name, ""))
        assert djust_render(source, "<b>x</b>") == django_render(source, "<b>x</b>")

    @pytest.mark.parametrize("name", FOUR)
    def test_and_still_escapes_an_unmarked_value(self, name):
        source = "{%% firstof p|%s%s %%}" % (name, ARGS.get(name, ""))
        got = djust_render(source, "<b>x</b>")
        assert got == django_render(source, "<b>x</b>")
        assert "&lt;b&gt;" in got, got

    def test_a_later_argument_is_reached_and_still_threaded(self):
        """The first argument is falsy, so the filter runs on the SECOND —
        which is a different iteration of the loop that carries the flag."""
        source = "{% firstof empty p|safe|linebreaksbr %}"
        expected = DjangoTemplate(source).render(DjangoContext({"empty": "", "p": "<b>x</b>"}))
        got = _rust.render_template(source, normalize_django_value({"empty": "", "p": "<b>x</b>"}))
        assert got == expected == "<b>x</b>"


class TestEveryRenderSiteThreadsTheInputSafety:
    """``filter_output_is_safe`` was extracted in #2259 because three render
    arms had drifted. The input-safety argument is fed from the SAME
    ``runtime_safe`` at the SAME three arms, and this pins the caller SET
    rather than a floor — a fourth arm that forgets the argument will not
    compile, but a fourth arm that passes a literal ``false`` would compile and
    silently reintroduce the bug for ``{% firstof %}`` or whatever it renders.

    #2283 widened the argument from ``input_was_safe: bool`` to
    ``InputSafety { container, items }``, because Django asks the question at
    two granularities: ``escape`` and the four ``needs_autoescape`` filters read
    whether the VALUE was safe, while ``join`` and ``unordered_list``
    ``conditional_escape`` per ELEMENT and read whether ``safeseq`` /
    ``escapeseq`` marked the ITEMS. ``container`` is this test's original
    subject under a new name; both fields are pinned below, since a fourth arm
    could get either wrong.
    """

    RENDERER = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
    )

    def _call_sites(self) -> list[str]:
        src = self.RENDERER.read_text()
        return re.findall(r"filters::apply_filter_full_safe\(\s*(.*?)\)\?;", src, re.S)

    def test_there_are_exactly_three_and_every_one_passes_runtime_safe(self):
        sites = self._call_sites()
        assert len(sites) == 3, (
            f"expected 3 apply_filter_full_safe call sites in renderer.rs, found "
            f"{len(sites)} — a new one must pass `runtime_safe`, not a literal"
        )
        for site in sites:
            args = [
                line.strip().rstrip(",")
                for line in site.splitlines()
                if line.strip() and not line.strip().startswith("//")
            ]
            # The struct literal spans lines; the fields are what matter.
            fields = {a for a in args if ":" in a}
            assert "container: runtime_safe" in fields, (
                "a call site passes something other than `runtime_safe` as the "
                f"container half of the input safety: {args!r}"
            )
            assert "items: items_safe" in fields, (
                f"a call site does not thread the ITEM granularity (#2283); got {args!r}"
            )

    def test_the_classic_entry_point_still_defaults_to_escaping(self):
        """``apply_filter_full`` has no view of the chain and must report the
        SAFE default. A ``true`` there would make every non-renderer caller
        stop escaping.

        Since #2283 the default is spelled ``InputSafety::default()`` rather
        than a literal ``false``. That is the same claim with a stronger
        guarantee: ``Default`` derives every field ``false``, so a THIRD
        granularity added later is safe-by-construction at this call site
        instead of needing another literal remembered here.
        """
        src = (self.RENDERER.parent / "filters.rs").read_text()
        body = src.split("pub fn apply_filter_full(", 1)[1].split("\n}\n", 1)[0]
        # Depth-counted rather than `[^)]*`: the last argument is itself a call
        # (`InputSafety::default()`) since #2283, and a non-greedy scan stops
        # inside it — which this test did, reporting `InputSafety::default(`.
        start = body.index("apply_filter_full_safe(") + len("apply_filter_full_safe(")
        depth, end = 1, start
        while depth:
            depth += {"(": 1, ")": -1}.get(body[end], 0)
            end += 1
        # Whitespace-insensitive, so a `cargo fmt` that rewraps the call does
        # not false-fail this. The LAST argument is the one under test.
        args = [a.strip() for a in body[start : end - 1].split(",") if a.strip()]
        assert args[-1] == "InputSafety::default()", (
            f"apply_filter_full must pass the SAFE default; it passes {args[-1]!r}"
        )
        # …and the default must actually BE the closed one. A `Default` impl
        # that set a field `true` would satisfy the line above and undo it.
        struct = src.split("pub struct InputSafety", 1)[1].split("}", 1)[0]
        assert "derive" not in struct, struct
        decl = src.split("pub struct InputSafety", 1)[0].rsplit("#[derive(", 1)[1]
        assert "Default" in decl.split(")", 1)[0], (
            "InputSafety must derive Default so every field is false; "
            f"derives are {decl.split(')', 1)[0]!r}"
        )
