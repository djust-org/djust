"""`linenumbers` escapes inside the filter body, as Django does (#2291).

**Second XSS of this drain, and a different mechanism from the first.**
GHSA-9395-2g46-rj3f was an unconditional `SAFE_OUTPUT_FILTERS` grant handed to a
non-sequence input. This one is the inverse: `linenumbers` was *excluded* from
that list and relied on the render-time auto-escape instead.

`renderer.rs` documented the exclusion deliberately, arguing the two were
byte-identical "because everything it adds is escape-invariant". That is true,
and beside the point — the argument holds only while the render-time escape
actually RUNS. A later `|safe` suppresses it, and then nothing had escaped the
input at all:

    {{ p|linenumbers|safe }}  with p = '<img src=x onerror=alert(1)>'
        djust  : '1. <img src=x onerror=alert(1)>'   <- live
        django : '1. &lt;img src=x onerror=alert(1)&gt;'

Django escapes each line *inside* `linenumbers` and returns `mark_safe`, so the
`|safe` has nothing left to expose. Fixed the same way, which is the same shape
#2269 used for `linebreaks`: the escape moves inside and the name joins
`SAFE_OUTPUT_FILTERS` as ONE inseparable change. Either alone is a bug in
opposite directions — the escape without the grant double-escapes, the grant
without the escape IS the vulnerability.

Found by the registry-wide probe written for #2281, not by inspection.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
    "a & b < c",
    "line1\n<b>line2</b>\nline3",
]


@pytest.mark.parametrize("payload", HOSTILE)
def test_a_trailing_safe_cannot_expose_the_input(payload: str) -> None:
    """The vulnerability. `|safe` suppresses the render-time escape."""
    source = "{{ p|linenumbers|safe }}"
    out = _rust.render_template(source, {"p": payload})
    assert "<img" not in out and "<script" not in out and "<svg" not in out, (
        f"live markup reached the page: {out!r}"
    )
    assert out == DjangoTemplate(source).render(DjangoContext({"p": payload}))


@pytest.mark.parametrize("payload", HOSTILE)
def test_the_plain_form_is_unchanged(payload: str) -> None:
    """The fix must not double-escape the ordinary case.

    Escape-inside plus the safety grant has to land on the same bytes the
    render-time escape produced before — that equality is what makes the two
    halves one change rather than two.
    """
    source = "{{ p|linenumbers }}"
    assert _rust.render_template(source, {"p": payload}) == DjangoTemplate(source).render(
        DjangoContext({"p": payload})
    )


@pytest.mark.parametrize("payload", HOSTILE)
def test_a_leading_safe_still_emits_live_because_django_does(payload: str) -> None:
    """NOT a bug, and pinned so it is not "fixed" later.

    `{{ p|safe|linenumbers }}` emits live markup — because `|safe` is the
    template author declaring the input trusted, and Django emits it live too.
    Escaping here would be over-escaping, and this is the assertion that stops
    a future reader from tightening it.
    """
    source = "{{ p|safe|linenumbers }}"
    assert _rust.render_template(source, {"p": payload}) == DjangoTemplate(source).render(
        DjangoContext({"p": payload})
    )


def test_numbering_and_width_are_untouched() -> None:
    """The filter still does its job — width padding included."""
    assert _rust.render_template("{{ p|linenumbers }}", {"p": "a\nb\nc"}) == "1. a\n2. b\n3. c"
    many = "\n".join(str(i) for i in range(12))
    out = _rust.render_template("{{ p|linenumbers }}", {"p": many})
    assert out.startswith("01. 0\n02. 1")
    assert out == DjangoTemplate("{{ p|linenumbers }}").render(DjangoContext({"p": many}))


def test_the_safety_grant_and_the_escape_are_one_change() -> None:
    """Both halves, asserted together.

    If the grant were added without the escape, the plain form would emit live
    markup. If the escape were added without the grant, the plain form would be
    double-escaped. Only both-or-neither produces Django's bytes.
    """
    payload = "<b>x</b>"
    plain = _rust.render_template("{{ p|linenumbers }}", {"p": payload})
    safed = _rust.render_template("{{ p|linenumbers|safe }}", {"p": payload})
    assert plain == "1. &lt;b&gt;x&lt;/b&gt;", plain
    assert safed == plain, "the trailing |safe must not change the bytes"


class TestTheAxesTheSafeShapeDoesNotCover:
    """Two axes the trailing-``|safe`` tests above miss, both from the #2281
    probe's differential rather than from inspection.

    The first is the more useful correction: the surface is not "templates
    ending in ``|safe``". ANY downstream filter that consumes the unescaped
    output is a live cell, and an html-aware truncator is one — it takes the
    filter's output as markup with no ``|safe`` anywhere in the template.
    """

    @pytest.mark.parametrize("payload", HOSTILE)
    @pytest.mark.parametrize(
        "source",
        [
            '{{ p|linenumbers|truncatechars_html:"5" }}',
            "{{ p|linenumbers|truncatewords_html:2 }}",
        ],
    )
    def test_an_html_aware_consumer_is_a_live_cell_with_no_safe_anywhere(
        self, source: str, payload: str
    ) -> None:
        out = _rust.render_template(source, {"p": payload})
        assert "<img" not in out and "<script" not in out and "<svg" not in out, out
        assert out == DjangoTemplate(source).render(DjangoContext({"p": payload}))

    @pytest.mark.parametrize("payload", HOSTILE)
    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|safe|linenumbers }}",
            "{{ p|escape|linenumbers }}",
            "{{ p|force_escape|linenumbers }}",
        ],
    )
    def test_the_escape_does_not_fire_twice_on_already_safe_input(
        self, source: str, payload: str
    ) -> None:
        """The opposite failure, and the reason the escape is conditional.

        Django's ``linenumbers`` skips its own escape when the input is already
        ``SafeData`` (``autoescape and not isinstance(value, SafeData)``). An
        UNCONDITIONAL escape inside the filter would be wrong in this
        direction — visible ``&amp;lt;`` in the page. ``escape``/``force_escape``
        only became a reachable input here once ``escape`` was made eager
        (#2281), so this reads as inert today and starts diverging the moment
        both land.
        """
        assert _rust.render_template(source, {"p": payload}) == DjangoTemplate(source).render(
            DjangoContext({"p": payload})
        )
