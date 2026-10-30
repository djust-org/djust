"""A ``<template>`` element's contents take part in the render diff.

The VDOM used to hold every ``<template>`` empty (html5ever keeps a
template's children in its contents fragment): a list rendered into a
template after the first render never reached the browser, and a template
inserted by a patch arrived without children. The parser and the client now
treat the contents as the template's children (Rust:
``crates/djust_vdom/tests/test_template_contents.rs``; client:
``tests/js/template_contents_patch.test.js``).
"""

import json

from djust._rust import RustLiveView

_TEMPLATE = (
    '<div dj-root><template data-state="{{ state }}">'
    "{% for o in options %}<li>{{ o }}</li>{% endfor %}"
    "</template>"
    "{% if open %}<template data-panel><b>{{ label }}</b></template>{% endif %}"
    "</div>"
)


def _patches(view):
    _, patches, _ = view.render_with_diff()
    return json.loads(patches)


def test_options_rendered_into_a_template_are_patched():
    view = RustLiveView(_TEMPLATE)
    view.update_state({"state": "unloaded", "options": [], "open": False, "label": ""})
    view.render_with_diff()
    view.update_state({"state": "ready", "options": ["a", "b"]})
    patches = _patches(view)
    inserted = [p for p in patches if p["type"] == "InsertChild"]
    assert [p["node"]["children"][0]["text"] for p in inserted] == ["a", "b"], patches


def test_a_template_inserted_by_a_patch_carries_its_contents():
    view = RustLiveView(_TEMPLATE)
    view.update_state({"state": "u", "options": [], "open": False, "label": "img"})
    view.render_with_diff()
    view.update_state({"open": True})
    patches = _patches(view)
    nodes = [p["node"] for p in patches if p["type"] == "InsertChild"]
    panel = next(n for n in nodes if n["tag"] == "template")
    assert panel["children"][0]["tag"] == "b"
    assert panel["children"][0]["children"][0]["text"] == "img"
