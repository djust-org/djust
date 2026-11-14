"""Default element ids of template tags repeat on every render (#4108).

The page-POST fallback answers with patches only when a fresh render of the
restored view reproduces the page the browser shows. An id drawn at random on
each render makes that impossible, and costs a patch per turn on a socket. The
tags below now derive their default id from the call (``_render_id``): equal
on every render of the same template, different for two identical calls in
one render. An explicit ``id=`` is used as given.

``{% filter_bar %}`` is not among them: on the Rust engine, which renders
LiveViews, its native handler already uses a constant id, and the Django tag's
output is held to that handler's by the parity baseline
(``scripts/template-library-parity-baseline.csv``).
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template
from django.test import RequestFactory

TOOLBAR = (
    "{% load djust_components %}"
    '{% toolbar size="sm" %}<button>a</button>{% endtoolbar %}'
    '{% toolbar size="sm" %}<button>a</button>{% endtoolbar %}'
    '{% toolbar size="lg" %}<button>b</button>{% endtoolbar %}'
    '{% toolbar id="mine" %}<button>c</button>{% endtoolbar %}'
)
TOOLTIP = (
    "{% load theme_components %}"
    '{% theme_tooltip "Help" position="top" %}'
    '{% theme_tooltip "Help" position="top" %}'
    '{% theme_tooltip "Other" position="top" %}'
    '{% theme_tooltip "Help" id="mine" %}'
)


def _ids(source: str, role: str) -> list[str]:
    request = RequestFactory().get("/")
    html = Template(source).render(Context({"request": request}))
    return re.findall(r'id="([^"]+)"[^>]*role="%s"|role="%s"[^>]*id="([^"]+)"' % (role, role), html)


def _flat(pairs: list) -> list[str]:
    return [a or b for a, b in pairs]


@pytest.mark.parametrize(
    "source, role",
    [(TOOLBAR, "toolbar"), (TOOLTIP, "tooltip")],
    ids=["toolbar", "theme_tooltip"],
)
def test_default_ids_repeat_across_renders_and_differ_within_one(source, role):
    first = _flat(_ids(source, role))
    second = _flat(_ids(source, role))
    assert len(first) == 4, first
    # The same on every render of the same template ...
    assert first == second
    # ... unique within one render, identical calls included ...
    assert len(set(first)) == 4
    assert first[1] == first[0] + "-2"
    # ... and an explicit id is used as given.
    assert first[3] == "mine"
