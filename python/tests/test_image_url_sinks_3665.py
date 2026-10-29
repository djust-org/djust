"""Every image URL a djust component prints is checked, not just ``{% avatar %}``'s (#3665).

The sweep behind #3665 drove every parameter of every template tag and every
class-based component with script-capable URLs and read the FINAL attributes. It
found, besides ``{% avatar %}``, six more places that printed a ``javascript:`` /
``vbscript:`` / ``data:text/html`` URL as written into an ``<img src>`` or
``srcset`` — five class-based components that escaped with ``html.escape`` where
their tag twins call ``url_attr``, and the ``srcset`` of ``responsive_image`` in
both engines (a ``srcset`` is a LIST of URLs, which ``url_attr`` cannot take whole).

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


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a.png 1x, b.png 2x", [("a.png", "1x"), ("b.png", "2x")]),
        ("a.png 480w,b.png 800w", [("a.png", "480w"), ("b.png", "800w")]),
        ("  a.png  ,  b.png 2x  ", [("a.png", ""), ("b.png", "2x")]),
        ("data:image/png;base64,AAAA 1x", [("data:image/png;base64,AAAA", "1x")]),
        ("a.png,b.png", [("a.png,b.png", "")]),  # a comma inside a URL stays (spec)
        ("a.png, b.png", [("a.png", ""), ("b.png", "")]),
        ("a.png (min-width: 1px, x) 1x", [("a.png", "(min-width: 1px, x) 1x")]),
        ("", []),
        (" , , ", []),
    ],
)
def test_srcset_candidates_follow_the_html_split(value, expected):
    assert _srcset_candidates(value) == expected


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
    assert not [u for u, _ in _srcset_candidates(out) if url_sweep.scheme_of(u)], out


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
    assert not [u for u, _ in _srcset_candidates(srcset[0]) if url_sweep.scheme_of(u)], srcset


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
