"""Every image URL a djust component prints is checked, not just ``{% avatar %}``'s (#3665).

The sweep behind #3665 drove every parameter of every template tag and every
class-based component with script-capable URLs and read the FINAL attributes. It
found, besides ``{% avatar %}``, seven more places that printed a ``javascript:`` /
``vbscript:`` / ``data:text/html`` URL as written into an ``<img src>`` or
``srcset`` — five class-based components that escaped with ``html.escape`` where
their tag twins call ``url_attr``, and the ``srcset`` of ``responsive_image`` in
both engines and of the ``ResponsiveImage`` class (a ``srcset`` is a LIST of URLs,
which ``url_attr`` cannot take whole).

Review of the first version found that the ``srcset`` splitter nested parentheses
where the HTML spec does not (``((),`` hid a ``javascript:`` candidate that a real
browser then used), and that the tests used PRODUCTION's parser as their oracle. The
oracle is now an independent port of the spec (``url_sweep.spec_srcset``) that this
file fuzzes production against.

``<img src="javascript:...">`` does not execute in a current browser, so none of
these is exploitable today; each is a missing sanitiser, and the one
``url_attr`` policy now holds at every one of them.
"""

from __future__ import annotations

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from djust.components.components.image_cropper import ImageCropper
from djust.components.components.image_upload_preview import ImageUploadPreview
from djust.components.components.live_indicator import LiveIndicator
from djust.components.components.mentions_input import MentionsInput
from djust.components.components.org_chart import OrgChart
from djust.components.components.responsive_image import ResponsiveImage
import random

from djust.components.utils import _srcset_candidates, srcset_attr, url_attr
from scripts.lib.template_library_parity import url_sweep

BAD = [
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "  javascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
]

#: The forms with no whitespace in them: in a ``srcset`` a space ends the URL.
NO_WHITESPACE = [
    b for b in BAD if b == b.strip() and " " not in b and "\t" not in b and "\n" not in b
]

# (id, constructor taking the url, element the image is in)
COMPONENTS = [
    ("image_cropper", lambda u: ImageCropper(src=u)),
    ("image_upload_preview", lambda u: ImageUploadPreview(previews=[u])),
    ("live_indicator", lambda u: LiveIndicator(user={"name": "A", "avatar": u}, field="f")),
    ("mentions_input", lambda u: MentionsInput(users=[{"id": 1, "name": "A", "avatar": u}])),
    ("org_chart", lambda u: OrgChart(nodes=[{"id": "1", "name": "A", "avatar": u}])),
    ("responsive_image", lambda u: ResponsiveImage(src=u)),
]


def _img_srcs(html: str) -> list[str]:
    return [a.value for a in url_sweep.attributes(html) if a.element == "img" and a.name == "src"]


@pytest.mark.parametrize("make", [c[1] for c in COMPONENTS], ids=[c[0] for c in COMPONENTS])
@pytest.mark.parametrize("bad", BAD)
def test_a_class_component_neutralises_a_script_capable_image_url(make, bad):
    srcs = _img_srcs(str(make(bad).render()))
    assert srcs, "the component rendered no <img src>"
    assert srcs == ["#"] * len(srcs), srcs


@pytest.mark.parametrize("make", [c[1] for c in COMPONENTS], ids=[c[0] for c in COMPONENTS])
@pytest.mark.parametrize(
    "ok",
    ["/media/a.png", "https://example.com/a.png?x=1&y=2", "data:image/png;base64,iVBORw0KGgo="],
)
def test_a_class_component_keeps_an_ordinary_image_url(make, ok):
    assert _img_srcs(str(make(ok).render())) == [ok]


@pytest.mark.parametrize("make", [c[1] for c in COMPONENTS], ids=[c[0] for c in COMPONENTS])
def test_a_class_component_escapes_attribute_breakout(make):
    srcs = _img_srcs(str(make('/a.png" onerror="x').render()))
    assert srcs == [
        '/a.png" onerror="x'
    ]  # one attribute value, quote intact: not a second attribute


@pytest.mark.parametrize("bad", NO_WHITESPACE)
def test_the_responsive_image_class_checks_every_srcset_candidate(bad):
    """The class twin of the tag: ``srcset`` is a list of URLs, not one."""
    html = str(ResponsiveImage(src="/a.png", srcset="/ok.png 1x, %s 2x" % bad).render())
    srcset = [a.value for a in url_sweep.attributes(html) if a.name == "srcset"]
    assert srcset == ["/ok.png 1x, # 2x"], srcset


def test_the_responsive_image_class_keeps_an_ordinary_srcset():
    html = str(ResponsiveImage(src="/a.png", srcset="a.png 1x, b.png?x=1&y=2 2x").render())
    assert [a.value for a in url_sweep.attributes(html) if a.name == "srcset"] == [
        "a.png 1x, b.png?x=1&y=2 2x"
    ]


# --- srcset ----------------------------------------------------------------


SPEC_CASES = [
    ("a.png 1x, b.png 2x", [("a.png", ["1x"]), ("b.png", ["2x"])]),
    ("a.png 480w,b.png 800w", [("a.png", ["480w"]), ("b.png", ["800w"])]),
    ("  a.png  ,  b.png 2x  ", [("a.png", []), ("b.png", ["2x"])]),
    ("a.png 1.5x", [("a.png", ["1.5x"])]),
    ("a.png 100w 2x", [("a.png", ["100w", "2x"])]),
    ("data:image/png;base64,AAAA 1x", [("data:image/png;base64,AAAA", ["1x"])]),
    ("a.png,b.png", [("a.png,b.png", [])]),  # a comma inside a URL stays (spec)
    ("a.png, b.png", [("a.png", []), ("b.png", [])]),
    ("a.png,, b.png,,,", [("a.png", []), ("b.png", [])]),  # trailing commas removed
    (",,a.png", [("a.png", [])]),
    ("a.png\tb.png\nc.png\fd.png\re.png", [("a.png", ["b.png", "c.png", "d.png", "e.png"])]),
    ("a.png 1x,\tb.png\n2x,\fc.png", [("a.png", ["1x"]), ("b.png", ["2x"]), ("c.png", [])]),
    # parentheses: ONE level, and the first ")" ends it (the spec does not nest)
    ("a.png (min-width: 1px, x) 1x", [("a.png", ["(min-width: 1px, x)", "1x"])]),
    ("a.png (a(b)c) 1x", [("a.png", ["(a(b)c)", "1x"])]),  # the first ")" closes; "c)" follows
    (
        "/a.png 10w ((), javascript:alert(1) 100w",
        [("/a.png", ["10w", "(()"]), ("javascript:alert(1)", ["100w"])],
    ),
    ("a.png (", [("a.png", ["("])]),  # EOF inside parens
    ("", []),
    (" , , ", []),
    ("\u00a0a.png", [("\u00a0a.png", [])]),  # NBSP is not ASCII whitespace
    ("a.png\u00a01x", [("a.png\u00a01x", [])]),
    ("a.png\x0b1x", [("a.png\x0b1x", [])]),  # VT is not ASCII whitespace either
]


@pytest.mark.parametrize("value,expected", SPEC_CASES)
def test_srcset_candidates_follow_the_html_split(value, expected):
    """Production AND the independent port give the spec's answer on each case."""
    assert url_sweep.spec_srcset(value) == expected, "the oracle disagrees with the spec text"
    assert _srcset_candidates(value) == expected


def test_the_nested_parens_bypass_is_one_candidate_to_neither_parser():
    """The finding: ``((),`` is ONE descriptor ``(()``, then a comma. A parser that
    nests parentheses sees a single candidate and never checks the second URL."""
    value = "/a.png 10w ((), javascript:alert(1) 100w"
    assert [u for u, _ in _srcset_candidates(value)] == ["/a.png", "javascript:alert(1)"]
    out = srcset_attr(value)
    assert [u for u, _ in url_sweep.spec_srcset(out)] == ["/a.png", "#"], out
    assert out == "/a.png 10w ((), # 100w", out


ALPHABET = [
    "/",
    ".",
    ",",
    "(",
    ")",
    " ",
    "\t",
    "\n",
    "j",
    "a",
    "v",
    "s",
    "c",
    "r",
    "i",
    "p",
    "t",
    ":",
    "1",
    "w",
    "x",
]


def test_production_and_the_independent_port_agree_on_20000_random_srcsets():
    """Candidate URLs AND descriptors, from a small alphabet that makes every state
    transition (commas, parens, each whitespace, the letters of ``javascript:``) common."""
    rng = random.Random(3665)
    for _ in range(20_000):
        value = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40)))
        assert _srcset_candidates(value) == url_sweep.spec_srcset(value), repr(value)


WORDS = [
    "javascript:alert(1)",
    "vbscript:x",
    "data:text/html,x",
    "/ok.png",
    "a.png",
    "((),",
    "(",
    ")",
    ",",
    " ",
    "\t",
    "\n",
    "\f",
    "1x",
    "100w",
    "10w",
]


def test_production_and_the_independent_port_agree_on_token_fuzz():
    """The character fuzz almost never spells ``javascript:``; this one splices whole
    tokens, so a script URL sits after every kind of separator and descriptor."""
    rng = random.Random(36651)
    for _ in range(10_000):
        value = "".join(rng.choice(WORDS) for _ in range(rng.randint(1, 12)))
        assert _srcset_candidates(value) == url_sweep.spec_srcset(value), repr(value)


def test_the_fuzz_reaches_the_cases_that_matter():
    """Presence before absence: a fuzz that never produced a parenthesised descriptor, a
    second candidate, or a ``javascript:`` URL after the first candidate would agree
    trivially."""
    rng = random.Random(3665)
    seen_paren = seen_two = 0
    for _ in range(20_000):
        value = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40)))
        cands = url_sweep.spec_srcset(value)
        seen_paren += any("(" in d for _, ds in cands for d in ds)
        seen_two += len(cands) >= 2
    assert seen_paren > 200 and seen_two > 2000
    rng = random.Random(36651)
    seen_late_script = 0
    for _ in range(10_000):
        value = "".join(rng.choice(WORDS) for _ in range(rng.randint(1, 12)))
        cands = url_sweep.spec_srcset(value)
        seen_late_script += any(u.startswith("javascript:") for u, _ in cands[1:])
    assert seen_late_script > 50


def test_no_random_srcset_leaves_a_script_url_after_srcset_attr():
    """The property the parser exists for, judged by the INDEPENDENT port: whatever goes in,
    no candidate of what comes out has a script scheme."""
    rng = random.Random(36650)
    for _ in range(5_000):
        value = "".join(rng.choice(WORDS) for _ in range(rng.randint(1, 12)))
        out = srcset_attr(value)
        bad = [u for u, _ in url_sweep.spec_srcset(out) if url_sweep.scheme_of(u)]
        assert not bad, (value, out, bad)


def test_srcset_splits_on_ascii_whitespace_only():
    """The spec's whitespace is TAB, LF, FF, CR and SPACE; a browser does not split on
    U+00A0 or VT, so neither does ``srcset_attr`` (pinned, per the review)."""
    assert [u for u, _ in _srcset_candidates("a.png\u00a0b.png")] == ["a.png\u00a0b.png"]
    assert [u for u, _ in _srcset_candidates("a.png\x0bb.png")] == ["a.png\x0bb.png"]
    assert [u for u, _ in _srcset_candidates("a.png\fb.png 1x")] == ["a.png"]  # FF does split


@pytest.mark.parametrize("bad", NO_WHITESPACE)
def test_srcset_attr_neutralises_every_candidate(bad):
    out = srcset_attr("/ok.png 1x, %s 2x, /also.png 3x" % bad)
    assert out == "/ok.png 1x, # 2x, /also.png 3x"


@pytest.mark.parametrize("bad", BAD)
def test_no_candidate_of_the_output_has_a_script_scheme(bad):
    """Whatever the input, no candidate URL of the OUTPUT, split the way a browser
    splits it, carries a script scheme. (``java<TAB>script:`` is the URL ``java``
    plus a descriptor to a browser, not ``javascript:``: the scan splits as it does.)"""
    out = srcset_attr("/ok.png 1x, %s 2x, /also.png 3x" % bad)
    assert out.startswith("/ok.png 1x, ") and out.endswith(", /also.png 3x"), out
    assert not [u for u, _ in url_sweep.spec_srcset(out) if url_sweep.scheme_of(u)], out


def test_srcset_attr_keeps_ordinary_candidates_and_escapes():
    assert srcset_attr("a.png 1x, b.png 2x") == "a.png 1x, b.png 2x"
    assert srcset_attr("a.png?x=1&y=2 1x") == "a.png?x=1&amp;y=2 1x"
    assert srcset_attr('a.png" onerror="x 1x') == "a.png&quot; onerror=&quot;x 1x"
    assert srcset_attr(None) == ""
    assert srcset_attr("") == ""


def test_srcset_attr_keeps_an_inline_image_and_a_marked_safe_value():
    uri = "data:image/png;base64,iVBORw0KGgo="
    assert srcset_attr("%s 1x" % uri) == "%s 1x" % uri
    assert srcset_attr("data:text/html,x 1x") == "# 1x"
    safe = mark_safe("javascript:void(0) 1x")
    assert srcset_attr(safe) == safe  # the author's explicit opt-out, as url_attr


def test_srcset_attr_is_consistent_with_url_attr_for_one_candidate():
    for value in NO_WHITESPACE + ["/a.png", "data:image/gif;base64,R0lGOD", "data:text/html,x"]:
        assert srcset_attr(value) == url_attr(value, image=True), value


@pytest.fixture
def _both_engines(settings):
    settings.TEMPLATES = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "NAME": "django",
            "APP_DIRS": True,
            "OPTIONS": {},
        },
        {
            "BACKEND": "djust.template.backend.DjustTemplateBackend",
            "NAME": "djust",
            "APP_DIRS": True,
            "OPTIONS": {},
        },
    ]
    engines._engines = {}  # type: ignore[attr-defined]
    yield
    engines._engines = {}  # type: ignore[attr-defined]


@pytest.mark.django_db
@pytest.mark.usefixtures("_both_engines")
@pytest.mark.parametrize("engine", ["django", "djust"])
@pytest.mark.parametrize("bad", BAD + ["java\x00script:alert(1)"])
def test_responsive_image_srcset_in_both_engines(engine, bad):
    src = "{% load djust_components %}{% responsive_image src='/a.png' srcset=s %}"
    html = (
        engines[engine].from_string(src).render({"s": "/ok.png 1x, %s 2x" % bad.replace(" ", "")})
    )
    srcset = [a.value for a in url_sweep.attributes(html) if a.name == "srcset"]
    assert len(srcset) == 1
    assert srcset[0].startswith("/ok.png 1x, ")
    assert not [u for u, _ in url_sweep.spec_srcset(srcset[0]) if url_sweep.scheme_of(u)], srcset


@pytest.mark.django_db
@pytest.mark.usefixtures("_both_engines")
def test_responsive_image_srcset_is_identical_in_both_engines():
    src = "{% load djust_components %}{% responsive_image src='/a.png' srcset=s %}"
    for value in BAD + ["a.png 1x, b.png 2x", "a.png?x=1&y=2 1x", "data:image/png;base64,AAAA 1x"]:
        outs = [
            [
                a.value
                for a in url_sweep.attributes(engines[e].from_string(src).render({"s": value}))
                if a.name == "srcset"
            ]
            for e in ("django", "djust")
        ]
        assert outs[0] == outs[1], (value, outs)


# --- the safe marker (review of #3675) ---------------------------------------

QUOTE_BREAK = '/x.png" onerror="alert(1)'
SAFE_COMPONENTS = [c for c in COMPONENTS if c[0] != "responsive_image"]


def _attr_names(html: str) -> set[str]:
    return {a.name for a in url_sweep.attributes(html)}


@pytest.mark.parametrize(
    "make", [c[1] for c in SAFE_COMPONENTS], ids=[c[0] for c in SAFE_COMPONENTS]
)
def test_a_class_component_never_honours_the_safe_marker_for_an_image_url(make):
    """Before #3665 these classes ran ``html.escape`` on whatever they were given, a
    ``SafeString`` included. Routing them through ``url_attr`` must not widen that into an
    opt-out: a marked-safe value that breaks out of the attribute stays escaped."""
    html = str(make(mark_safe(QUOTE_BREAK)).render())
    assert "onerror" not in _attr_names(html), html
    assert _img_srcs(html) == [QUOTE_BREAK], html


@pytest.mark.parametrize(
    "make", [c[1] for c in SAFE_COMPONENTS], ids=[c[0] for c in SAFE_COMPONENTS]
)
def test_a_marked_safe_image_url_is_escaped_exactly_as_before(make):
    """The behaviour, stated precisely: a ``SafeString`` is treated as text, so an
    already-escaped ``&amp;`` is escaped again (``&amp;amp;``), as ``html.escape`` did."""
    html = str(make(mark_safe("/a.png?x=1&amp;y=2")).render())
    assert "/a.png?x=1&amp;amp;y=2" in html, html


def test_url_attr_can_be_told_to_ignore_the_safe_marker():
    safe = mark_safe("javascript:void(0)")
    assert url_attr(safe) == "javascript:void(0)"
    assert url_attr(safe, honor_safe=False) == "#"
    assert (
        url_attr(mark_safe(QUOTE_BREAK), honor_safe=False) == "/x.png&quot; onerror=&quot;alert(1)"
    )
    assert url_attr(mark_safe(QUOTE_BREAK), image=True, honor_safe=False).count('"') == 0
    for value in ("/a?b=1&c=2", "it's", "<b>"):
        assert url_attr(mark_safe(value), honor_safe=False) == url_attr(value)


def test_srcset_attr_can_be_told_to_ignore_the_safe_marker():
    safe = mark_safe('a.png" onerror="x 1x, javascript:y 2x')
    assert srcset_attr(safe) == safe
    assert srcset_attr(safe, honor_safe=False) == "a.png&quot; onerror=&quot;x 1x, # 2x"


def test_the_responsive_image_class_escapes_a_marked_safe_srcset():
    html = str(ResponsiveImage(src="/a.png", srcset=mark_safe('a.png" onerror="x 1x')).render())
    assert "onerror" not in _attr_names(html), html
    assert [a.value for a in url_sweep.attributes(html) if a.name == "srcset"] == [
        'a.png" onerror="x 1x'
    ]


def test_the_responsive_image_class_src_is_unchanged_by_this_pr():
    """``ResponsiveImage.src`` already went through ``url_attr`` before #3665 (honouring the
    marker, like the tag). Pinned so a later change to it is a decision, not a drift."""
    html = str(ResponsiveImage(src=mark_safe("javascript:void(0)")).render())
    assert _img_srcs(html)[0] == "javascript:void(0)"


@pytest.mark.django_db
@pytest.mark.usefixtures("_both_engines")
@pytest.mark.parametrize("engine", ["django", "djust"])
@pytest.mark.parametrize("empty", ["", "   ", "\t\n", " , , ", "\u00a0"])
def test_a_srcset_with_no_candidate_is_omitted_in_both_engines(engine, empty):
    """Django's engine printed ``srcset=""`` for ``"   "`` while the native handler printed
    no attribute; both now omit it (a ``srcset`` with no candidate says nothing)."""
    src = "{% load djust_components %}{% responsive_image src='/a.png' srcset=s %}"
    html = engines[engine].from_string(src).render({"s": empty})
    assert "srcset" not in _attr_names(html), html


@pytest.mark.parametrize("empty", ["", "   ", " , , "])
def test_the_responsive_image_class_omits_an_empty_srcset(empty):
    assert "srcset" not in _attr_names(str(ResponsiveImage(src="/a.png", srcset=empty).render()))
