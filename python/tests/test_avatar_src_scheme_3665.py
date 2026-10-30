"""``{% avatar src=... %}`` neutralises a script-capable URL in BOTH engines (#3665).

The Python ``avatar`` tag (an ``inclusion_tag`` over ``avatar.html``) printed the
``src`` it was given verbatim, while the native handler passed it through
``url_attr(src, image=True)``. So ``u = "javascript:alert(1)"`` rendered
``src="javascript:alert(1)"`` under Django's engine and ``src="#"`` under
``DjustTemplateBackend``. ``javascript:`` in ``<img src>`` does not run in a
current browser, which is why this is low risk; the point is that one engine had
a sanitiser the other lacked.

These tests render through both REAL engines and read the final ``src`` — the
reproduction-fidelity rule (a unit test of ``url_attr`` would have passed all
along, because ``url_attr`` was never the problem: the Python tag did not call it).
"""

from __future__ import annotations

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from scripts.lib.template_library_parity import url_sweep

pytestmark = pytest.mark.django_db

ENGINES = ("django", "djust")
#: ``data:image/*`` is a legitimate ``<img src>`` (``test_data_image_is_kept``).
#: Forms whose script scheme is only visible AFTER a decode the browser does not
#: apply to a scheme (an entity or a percent escape inside ``javascript``). Once
#: the value is HTML-escaped they are a relative URL with that literal text: inert
#: and, importantly, not rewritten differently by the two engines. The ``srcset-*``
#: labels are lists of URLs: as one ``src`` they are a single relative URL.
ENCODED = (
    "srcset-parens",
    "srcset-parens-data",
    "srcset-comma",
    "srcset-formfeed",
    "js-entity-hex",
    "js-entity-dec",
    "js-entity-nosemi",
    "js-entity-colon",
    "js-percent-colon",
)
NOT_IMAGES = [d for d in url_sweep.DANGEROUS if d[0] != "data-svg"]
LOAD = "{% load djust_components %}"


def _engine(name: str):
    return engines[name]


def _final_src(engine: str, **ctx) -> list[str]:
    html = _engine(engine).from_string(LOAD + "{% avatar src=u initials='AB' %}").render(ctx)
    return [a.value for a in url_sweep.attributes(html) if a.element == "img" and a.name == "src"]


@pytest.fixture(autouse=True)
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


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("label,payload", NOT_IMAGES, ids=[d[0] for d in NOT_IMAGES])
def test_a_script_capable_src_is_neutralised(engine, label, payload):
    """The issue's reproduction, and every obfuscated form of it."""
    srcs = _final_src(engine, u=payload)
    assert srcs, "the avatar rendered no <img> at all"
    assert not any(url_sweep.scheme_of(v) for v in srcs), (engine, label, srcs)
    if label not in ENCODED:
        assert srcs == ["#"], (engine, label, srcs)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("label,payload", url_sweep.SAFE, ids=[s[0] for s in url_sweep.SAFE])
def test_an_ordinary_src_is_kept(engine, label, payload):
    assert _final_src(engine, u=payload) == [payload]


@pytest.mark.parametrize("engine", ENGINES)
def test_data_image_is_kept(engine):
    uri = "data:image/svg+xml,<svg onload=alert(1)>"
    assert _final_src(engine, u=uri) == [uri]


@pytest.mark.parametrize("engine", ENGINES)
def test_the_two_engines_emit_the_same_src_for_every_payload(engine):
    """Same input, same final attribute: the accept/reject sets are identical."""
    other = "djust" if engine == "django" else "django"
    for label, payload in url_sweep.PAYLOADS:
        assert _final_src(engine, u=payload) == _final_src(other, u=payload), label


def test_a_marked_safe_src_is_the_authors_opt_out_under_django():
    """``mark_safe`` is the documented way to say "I vouch for this" (``url_attr``).

    The Rust engine never sees the marker (a context value crosses as plain text),
    so it neutralises the same value: the stricter side, and an existing,
    separately tracked escape-policy difference (``ESCAPE_VARIANTS`` in the parity
    ratchet). This test pins that the fix did not change the Django side."""
    assert _final_src("django", u=mark_safe("javascript:void(0)")) == ["javascript:void(0)"]
    assert _final_src("djust", u=mark_safe("javascript:void(0)")) == ["#"]


@pytest.mark.parametrize("engine", ENGINES)
def test_attribute_breakout_is_escaped(engine):
    html = (
        _engine(engine)
        .from_string(LOAD + "{% avatar src=u alt=a %}")
        .render({"u": '/a.png" onerror="x', "a": '"><b>'})
    )
    assert 'onerror="x' not in html.replace("&quot;", "")
    assert "<b>" not in html


@pytest.mark.parametrize("engine", ENGINES)
def test_no_src_still_shows_the_initials(engine):
    html = _engine(engine).from_string(LOAD + "{% avatar initials='AB' %}").render({})
    assert "AB" in html
    assert not [a for a in url_sweep.attributes(html) if a.name == "src"]


def test_a_marked_safe_src_is_not_escaped_twice_under_django():
    """``url_attr`` returns escaped text marked safe, and the tag hands exactly that to the
    template; if it ever handed plain text instead, autoescape would escape it a second time
    (``&amp;amp;``). Pinned because the fix depends on it and no other case passes a value
    with an ampersand through."""
    assert _final_src("django", u="/a.png?x=1&y=2") == ["/a.png?x=1&y=2"]
    assert _final_src("django", u=mark_safe("/a.png?x=1&amp;y=2")) == ["/a.png?x=1&y=2"]
    html = (
        _engine("django").from_string(LOAD + "{% avatar src=u %}").render({"u": "/a.png?x=1&y=2"})
    )
    assert "x=1&amp;y=2" in html and "&amp;amp;" not in html
