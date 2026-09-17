"""ADR-030: wrapper-shaped raw block tags take the rendered-body route.

The component library is the population the ADR measured: 57 raw
body-consuming tags, of which the five without a native handler
(``aspect_ratio``, ``callout``, ``scroll_area``, ``sticky_header``, ``tab``)
are plain wrappers. Bridging the library here (the app is NOT installed in
the test settings, so no native handler shadows a name) must route every
wrapper through ``LibraryBlockTagHandler`` and refuse the rest by name.
"""

from __future__ import annotations

import pytest
from django.template.library import import_library

from djust import template_libraries as tl

COMPONENTS = "djust.components.templatetags.djust_components"
FIVE = ("aspect_ratio", "callout", "scroll_area", "sticky_header", "tab")


@pytest.fixture
def bridged_components():
    """Bridge the component library, then unregister everything it added."""
    from djust._rust import unregister_block_tag_handler, unregister_tag_handler

    library = import_library(COMPONENTS)
    before = {
        name: dict(tl._engine_state(name, getattr(tl, name)))
        for name in ("_loaded", "_owned_tags", "_handlers", "_tag_owner", "_filter_owner")
    }
    tl._bridge_library("djust_components", library)
    yield library
    for name in library.tags:
        try:
            unregister_tag_handler(name)
            unregister_block_tag_handler(name)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass
    for name, snapshot in before.items():
        state = tl._engine_state(name, getattr(tl, name))
        state.clear()
        state.update(snapshot)
    tl.reassert()


def _raw_body_tags(library):
    return {
        name
        for name, fn in library.tags.items()
        if tl._classify(fn) == "raw" and tl._consumes_body(fn)
    }


def test_the_five_gallery_tags_are_wrappers_and_bridge(bridged_components):
    owned = tl._engine_state("_owned_tags", tl._owned_tags)
    for name in FIVE:
        label, handler = owned[name]
        assert isinstance(handler, tl.LibraryBlockTagHandler), (name, type(handler))
        assert handler.end_name == "end" + name


def test_the_component_census_matches_adr_030():
    """The probe alone, independent of which names djust already owns in this
    process: the five are wrappers; `split_pane` is two-segment; the
    nodelist-introspecting parents drop a text body and are refused."""
    library = import_library(COMPONENTS)
    reasons = {
        name: tl._wrapper_refusal(name, library.tags[name]) for name in _raw_body_tags(library)
    }
    assert len(reasons) == 57
    assert all(reasons[name] is None for name in FIVE)
    assert "more than one body segment" in reasons["split_pane"]
    for parent in ("tabs", "accordion", "modal", "sidebar", "nav_menu"):
        assert "does not render its body" in reasons[parent], parent
    # Renders its body under a pushed variable: the one shape that would
    # diverge from Django silently on the bridge, so it must be refused.
    assert "modified context" in reasons["form_array"]
    wrappers = sorted(name for name, reason in reasons.items() if reason is None)
    refused = sorted(name for name, reason in reasons.items() if reason is not None)
    # A deliberate pin: a new body-consuming component tag moves one of these
    # counts, and the change should say which side it landed on.
    assert len(wrappers) == 40 and len(refused) == 17, (wrappers, refused)


def test_a_bridged_wrapper_renders_through_the_rust_engine(bridged_components):
    from djust._rust import render_template

    html = render_template(
        '{% callout variant="info" title="Heads up" %}<b dj-click="go">{{ who|upper }}</b>{% endcallout %}',
        {"who": "world"},
    )
    assert "dj-callout--info" in html
    assert '<b dj-click="go">WORLD</b>' in html


def test_probe_reasons_are_specific():
    from django import template

    lib = template.Library()

    class Wrap(template.Node):
        def __init__(self, nodelist):
            self.nodelist = nodelist

        def render(self, context):
            return "(" + self.nodelist.render(context) + ")"

    @lib.tag
    def okwrap(parser, token):
        nodelist = parser.parse(("endokwrap",))
        parser.delete_first_token()
        return Wrap(nodelist)

    @lib.tag
    def tokens(parser, token):
        parser.next_token()
        return template.Node()

    assert tl._wrapper_refusal("okwrap", okwrap) is None
    assert "reads the token stream directly" in tl._wrapper_refusal("tokens", tokens)
