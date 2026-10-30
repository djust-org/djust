"""The theming URL denylist also refuses the obsolete script schemes (#3665 review).

``djust_theming`` guards URLs with a DENYLIST (``javascript:``, ``vbscript:``, ``data:``);
``djust_components`` guards them with an allowlist. The review found ``livescript:`` (Netscape)
and ``mhtml:`` (Internet Explorer) passing through every theming sink. Both are dead in every
current browser and neither has a legitimate use in an ``href``, so adding them is a pure
hardening. The wider gap (the denylist passing every scheme the allowlist rejects: ``blob:``,
``file:``, ``view-source:``, custom schemes, ...) is deliberately NOT changed here: unifying on
the allowlist could break a legitimate custom scheme, and needs a decision.
"""

from __future__ import annotations

import pytest
from django.template import Context, Engine

from djust.theming.templatetags.theme_components import _has_unsafe_url_scheme

_ENGINE = Engine(
    libraries={"theme_components": "djust.theming.templatetags.theme_components"},
    builtins=[],
)


@pytest.mark.parametrize(
    "value",
    [
        "livescript:alert(1)",
        "LiveScript:alert(1)",
        "  livescript:alert(1)",
        "live\tscript:alert(1)",
        "mhtml:file://x.mht!y",
        "MHTML:x",
        "javascript:alert(1)",
        "vbscript:x",
        "data:text/html,x",
    ],
)
def test_script_and_obsolete_schemes_are_unsafe(value):
    assert _has_unsafe_url_scheme(value)


@pytest.mark.parametrize(
    "value",
    [
        "/a",
        "https://example.com",
        "mailto:a@b.c",
        "#x",
        "livescripts://x",
        "mhtmlx:y",
        "a:livescript:",
    ],
)
def test_other_values_are_not_refused(value):
    assert not _has_unsafe_url_scheme(value)


@pytest.mark.parametrize("scheme", ["livescript:alert(1)", "mhtml:x"])
def test_theme_nav_item_neutralises_them(scheme):
    html = _ENGINE.from_string(
        "{% load theme_components %}{% theme_nav_item label='L' url=u %}"
    ).render(Context({"u": scheme}))
    assert scheme not in html
    assert 'href="#"' in html
