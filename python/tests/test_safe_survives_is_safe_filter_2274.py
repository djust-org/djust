"""#2274 — ``|safe`` must survive an ``is_safe=True`` filter.

Django's escape decision (``django/template/base.py``, ``FilterExpression.resolve``)
has TWO terms::

    new_obj = func(obj, *arg_vals)
    if getattr(func, "is_safe", False) and isinstance(obj, SafeData):
        obj = mark_safe(new_obj)
    else:
        obj = new_obj

``obj`` is the filter's INPUT. So a value comes out safe when either

1. the filter is ``is_safe=True`` **and its input was already safe**, or
2. the filter returned a ``SafeData`` of its own (it called ``mark_safe``).

djust had term 2 (``produced_safe`` + ``SAFE_OUTPUT_FILTERS``) and was missing
term 1 entirely — not a wrong list, an absent term — so ``{{ p|safe|lower }}``
came out escaped: ``|safe`` was undone by the very next filter.

Measured on ``08b42e7a`` with payload ``<b>x</b>``, against Django's LIVE
registry (36 ``is_safe=True`` filters, not the 27 the issue quotes):

===================  ==========  =========
cell                 before      after
===================  ==========  =========
``{{ p|X }}``        5 / 36      5 / 36
``{{ p|safe|X }}``   28 / 36     9 / 36
===================  ==========  =========

The 19 that moved are this defect. The 9 that remain are two OTHER bugs, both
of which diverge in BOTH columns and are therefore not ``|safe``-related:

* ``join`` / ``safeseq`` / ``escapeseq`` / ``unordered_list`` / ``random`` do
  not iterate a STRING as a character sequence the way Python does, so they
  return a shape Django never produces. **Closed by #2283.**
* ``linebreaks`` / ``linebreaksbr`` / ``urlize`` / ``urlizetrunc`` escape their
  input unconditionally, where Django's ``needs_autoescape`` skips the escape
  for input that is already ``SafeData``. **Closed alongside #2281**, which
  made ``escape`` produce a ``SafeString`` and so turned that divergence from
  a quiet over-escape into 104 measurable double-escapes.

Two of those five sequence filters were also a LIVE XSS, and closing it was a
prerequisite rather than scope creep — see ``TestUnearnedSafeGrant``.

Security-adjacent, so every claim here is asserted from BOTH directions: a
hostile payload from an UNSAFE source must still be escaped, AND markup the
filter generates itself must stay live. A probe that checks only the first
passes on fully-escaped output too and proves nothing (#2259 recorded the same
trap).
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

from djust import _rust  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

#: Django's ``is_safe=True`` set, read from the LIVE registry rather than
#: transcribed. A transcribed list drifts silently on a Django upgrade.
DJANGO_IS_SAFE = frozenset(n for n, f in register.filters.items() if getattr(f, "is_safe", False))

#: The 19 names #2274 actually moved: Django registers them ``is_safe=True``,
#: djust does NOT escape internally for them, so the input term is the whole
#: fix. Deliberately NOT derived from the Rust list — a test that recomputes
#: the implementation's own answer asserts nothing.
FIXED_BY_2274 = [
    "addslashes",
    "capfirst",
    "center",
    "escape",
    "last",
    "linenumbers",
    "ljust",
    "lower",
    "phone2numeric",
    "pprint",
    "rjust",
    "slice",
    "stringformat",
    "title",
    "truncatechars",
    "truncatechars_html",
    "truncatewords",
    "truncatewords_html",
    "wordwrap",
]

ARGS = {
    "center": ':"20"',
    "ljust": ':"20"',
    "rjust": ':"20"',
    "slice": ':":4"',
    "stringformat": ':"s"',
    "truncatechars": ':"30"',
    "truncatechars_html": ':"30"',
    "truncatewords": ':"5"',
    "truncatewords_html": ':"5"',
    "wordwrap": ':"10"',
}

#: Hostile payloads. Each targets a different sink, so a fix that only handles
#: ``<`` is not enough to pass the whole set.
HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "javascript:alert(1)",
]


def render_both(source: str, value):
    django_out = DjangoTemplate(source).render(DjangoContext({"p": value}))
    djust_out = _rust.render_template(source, normalize_django_value({"p": value}))
    return django_out, djust_out


def cell(name: str, safe_prefix: bool) -> str:
    return "{{ p|%s%s%s }}" % ("safe|" if safe_prefix else "", name, ARGS.get(name, ""))


class _Capabilities(HTMLParser):
    """What a browser would EXECUTE, not what it would display.

    Substring-matching an entity-decoded string is the wrong model and invents
    exploits that do not exist: ``&lt;img …&gt;`` and ``&LT;IMG …&GT;`` both
    *read* as ``<img …>``, but a character reference is resolved after the
    tokenizer has already decided where tags begin, so neither ever creates an
    element. Only a literal ``<`` does. So parse, and record the capabilities
    the parse actually grants.
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
    p = _Capabilities()
    try:
        p.feed(out)
        p.close()
    except Exception:  # noqa: BLE001 — a parse error grants no capability
        pass
    return p.caps


def test_capabilities_probe_is_not_vacuous():
    """The probe itself, before anything is asserted through it.

    Every security assertion below is only as good as this function. If it
    returned an empty set unconditionally, the whole file would be green and
    worthless — so pin both directions of the exact distinction it exists to
    draw: a literal ``<`` is live, a character reference is not.
    """
    assert capabilities("<img src=x onerror=alert(1)>") == {"tag:img", "evt:onerror"}
    assert capabilities("&lt;img src=x onerror=alert(1)&gt;") == set()
    assert capabilities("&LT;IMG SRC=X ONERROR=ALERT(1)&GT;") == set()
    assert capabilities('<a href="javascript:alert(1)">x</a>') == {"tag:a", "url:href"}
    assert capabilities('<a href="&#106;avascript:alert(1)">x</a>') == {"tag:a", "url:href"}


# ---------------------------------------------------------------------------
# The list, pinned against Django rather than transcribed
# ---------------------------------------------------------------------------


class TestIsSafeSetTracksDjango:
    RENDERER = (
        Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "renderer.rs"
    )

    def _rust_list(self) -> frozenset[str]:
        src = self.RENDERER.read_text()
        m = re.search(r"const IS_SAFE_FILTERS: \[&str; (\d+)\] = \[(.*?)\];", src, re.S)
        assert m, "IS_SAFE_FILTERS array not found in renderer.rs"
        names = frozenset(re.findall(r'"([^"]+)"', m.group(2)))
        assert int(m.group(1)) == len(names), (
            f"the declared length {m.group(1)} disagrees with the {len(names)} names in the array"
        )
        return names

    def test_is_safe_set_matches_djangos_registry(self):
        """Mechanical, in BOTH directions — a Django upgrade that flips a flag
        goes red here rather than diverging silently."""
        rust = self._rust_list()
        assert rust == DJANGO_IS_SAFE, (
            f"missing from djust: {sorted(DJANGO_IS_SAFE - rust)}; "
            f"not is_safe in Django: {sorted(rust - DJANGO_IS_SAFE)}"
        )

    def test_django_still_has_thirty_six(self):
        """The count the issue disputes. 36, not the 27 it quotes."""
        assert len(DJANGO_IS_SAFE) == 36, sorted(DJANGO_IS_SAFE)

    def test_upper_is_deliberately_absent(self):
        """``upper`` is the load-bearing exclusion, not an oversight.

        Django registers ``upper`` ``is_safe=False`` because upper-casing
        ``&lt;`` yields ``&LT;`` and ``&#x27;`` yields ``&#X27;`` — both still
        valid HTML5 references. Adding it would let a case change survive an
        escape. ``lower``/``title``/``capfirst`` ARE on the list.
        """
        assert "upper" not in DJANGO_IS_SAFE
        assert {"lower", "title", "capfirst"} <= DJANGO_IS_SAFE


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


class TestSafeSurvives:
    @pytest.mark.parametrize("name", FIXED_BY_2274)
    def test_safe_survives_the_filter(self, name):
        """``{{ p|safe|X }}`` byte-agrees with Django. This is the 19."""
        src = cell(name, safe_prefix=True)
        django_out, djust_out = render_both(src, "<b>x</b>")
        assert djust_out == django_out, f"{src}: django={django_out!r} djust={djust_out!r}"

    @pytest.mark.parametrize("name", FIXED_BY_2274)
    def test_the_same_filter_without_safe_is_unchanged(self, name):
        """The other half of the cell. If BOTH columns came out live, the test
        above would pass for the wrong reason — 'escape nothing' also agrees
        with Django on the ``|safe`` column."""
        src = cell(name, safe_prefix=False)
        django_out, djust_out = render_both(src, "<b>x</b>")
        assert djust_out == django_out, f"{src}: django={django_out!r} djust={djust_out!r}"
        assert "<b>" not in djust_out, f"{src} leaked live markup: {djust_out!r}"

    @pytest.mark.parametrize("name", FIXED_BY_2274)
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_hostile_input_from_an_unsafe_source_stays_inert(self, name, payload):
        """Direction 1 of the probe: no ``is_safe`` name may pass a payload
        through live when nothing marked it safe."""
        src = cell(name, safe_prefix=False)
        _, djust_out = render_both(src, payload)
        assert capabilities(djust_out) == set(), f"{src} on {payload!r} -> {djust_out!r}"

    @pytest.mark.parametrize("payload", HOSTILE)
    def test_a_filter_that_generates_markup_keeps_it_live(self, payload):
        """Direction 2: the fix must not be 'escape everything'.

        ``urlize`` and ``linebreaks`` escape their input and then add markup of
        their own; that markup has to survive, while the payload's does not.
        """
        out = _rust.render_template(
            "{{ p|linebreaks }}", normalize_django_value({"p": payload + "\nsecond"})
        )
        assert "<p>" in out and "<br>" in out, f"linebreaks lost its own markup: {out!r}"
        assert capabilities(out) <= {"tag:p", "tag:br"}, out

    def test_a_plain_filter_still_re_taints(self):
        """``upper`` is ``is_safe=False``, so it must undo an earlier ``|safe``
        — the LAST-filter rule #2259 established, which the input term must not
        quietly repeal."""
        for src in ["{{ p|safe|upper }}", "{{ p|safe|lower|upper }}", "{{ p|urlize|upper }}"]:
            django_out, djust_out = render_both(src, "<b>x</b>")
            assert djust_out == django_out, f"{src}: django={django_out!r} djust={djust_out!r}"
            assert capabilities(djust_out) == set(), f"{src} -> {djust_out!r}"

    def test_safety_survives_a_chain_of_is_safe_filters(self):
        """Not just one hop — the flag has to be fed FORWARD, not recomputed
        from the first filter."""
        for src in [
            "{{ p|safe|lower|capfirst }}",
            "{{ p|safe|lower|capfirst|truncatechars:30 }}",
            "{{ p|safe|addslashes|title|ljust:20 }}",
        ]:
            django_out, djust_out = render_both(src, "<b>x</b>")
            assert djust_out == django_out, f"{src}: django={django_out!r} djust={djust_out!r}"

    def test_the_inline_if_arm_agrees_with_the_variable_arm(self):
        """The second of the three sites that share ``filter_output_is_safe``.

        A file that only used ``{{ p|… }}`` would leave this arm unpinned,
        which is how the three drifted apart before #2259 (#1646).
        """
        ctx = normalize_django_value({"p": "<b>x</b>", "c": True})
        for chain in ["safe|lower", "safe|upper", "safe|title|truncatechars:30", "lower"]:
            variable = _rust.render_template("{{ p|%s }}" % chain, ctx)
            inline_if = _rust.render_template('{{ p if c else "" |%s }}' % chain, ctx)
            assert variable == inline_if, f"{chain}: variable={variable!r} if={inline_if!r}"

    def test_the_firstof_arm_agrees_with_the_variable_arm(self):
        """The third site — ``get_value_safe``, reached via ``{% firstof %}``
        and ``{% cycle %}``."""
        ctx = normalize_django_value({"p": "<b>x</b>"})
        for chain in ["safe|lower", "safe|upper", "lower"]:
            variable = _rust.render_template("{{ p|%s }}" % chain, ctx)
            firstof = _rust.render_template("{%% firstof p|%s %%}" % chain, ctx)
            assert variable == firstof, f"{chain}: variable={variable!r} firstof={firstof!r}"


# ---------------------------------------------------------------------------
# The prerequisite: an unearned unconditional-safe grant
# ---------------------------------------------------------------------------


class TestUnearnedSafeGrant:
    """``unordered_list`` and ``safeseq`` returned a NON-SEQUENCE input verbatim
    while sitting in ``SAFE_OUTPUT_FILTERS`` — an unconditional "emit this
    unescaped" grant for a filter that had done nothing at all. So
    ``{{ hostile_string|safeseq }}`` was an exact synonym for ``|safe`` with no
    ``mark_safe`` anywhere in the template, and it was live on ``main``.

    It is fixed here rather than filed because #2274 makes it WORSE: once an
    ``is_safe`` filter preserves the safety it is handed, that unearned grant
    survives arbitrarily far down the chain. Measured over 44,610 differential
    cells, closing it turned the fix from +1067 more-permissive-than-Django
    cells into -663.

    Only the safety half was fixed here. The output SHAPE was still wrong
    (Django iterates a string as a character sequence; djust did not) and
    #2283 closed that — so the assertions below became EQUALITY against
    Django, which is strictly stronger than the inertness they started as.
    """

    @pytest.mark.parametrize("name", ["unordered_list", "safeseq"])
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_a_string_input_is_not_handed_back_live(self, name, payload):
        """The payload stays inert, and the output is now Django's byte for byte.

        The assertion moved from ``capabilities(out) == set()`` to a subset of
        Django's when #2283 landed: ``unordered_list`` GENERATES ``<li>`` for a
        string now, exactly as Django does, so "no markup at all" stopped being
        the right bar. What must not appear is any capability traceable to the
        PAYLOAD, which is what comparing against Django's own output says.
        """
        django_out, djust_out = render_both("{{ p|%s }}" % name, payload)
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert capabilities(djust_out) <= capabilities(django_out), djust_out
        assert capabilities(djust_out) <= {"tag:li"}, f"{name} on {payload!r} -> {djust_out!r}"

    @pytest.mark.parametrize("name", ["unordered_list", "safeseq"])
    def test_the_grant_no_longer_survives_a_later_is_safe_filter(self, name):
        """The amplification path specifically: the exact chain shape that made
        #2274 unsafe to ship on its own."""
        django_out, djust_out = render_both(
            "{{ p|%s|lower }}" % name, "<IMG SRC=x ONERROR=alert(1)>"
        )
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert capabilities(djust_out) <= {"tag:li"}, djust_out

    def test_the_list_path_is_untouched(self):
        """The shape the filters are actually FOR keeps working, and keeps
        agreeing with Django. Without this the fix above could be 'escape
        everything' and still look green."""
        django_out, djust_out = render_both("{{ p|unordered_list }}", ["<i>a</i>", "b & c"])
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert "<li>" in djust_out and "&lt;i&gt;" in djust_out, djust_out

    def test_the_string_output_shape_agrees_now(self):
        """The landmark, turned over. #2283 landed; this is the equality
        assertion its predecessor asked to become.

        Django iterates the string as characters; djust does too now. The
        escape on the non-sequence branch that this class added is still
        present and is now a NO-OP for every reachable value — see
        ``TestNonIterableFallThrough`` in
        ``test_escape_chain_and_sequence_filters_2281_2283.py`` and the
        enum-side pin in ``crates/djust_templates/src/filters.rs``.
        """
        django_out, djust_out = render_both("{{ p|unordered_list }}", "ab")
        assert djust_out == django_out, f"django={django_out!r} djust={djust_out!r}"
        assert "<li>a</li>" in djust_out and "<li>b</li>" in djust_out
