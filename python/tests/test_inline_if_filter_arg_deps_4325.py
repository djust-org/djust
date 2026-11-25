"""An inline-if's filter ARGUMENT must reach that node's deps (#4325, #787).

The parser splits the filter chain off the whole ``{{ }}`` before it looks for
``if`` (#2745), so ``{{ a if c else b|add:x }}`` is an ``InlineIf`` whose
``filters`` hold ``add:x``. The ``InlineIf`` arm of ``extract_from_nodes``
ignored ``filters``, so ``x`` was never a dependency: a partial render after
only ``x`` changed reused the stale bytes, and the per-item loop store reused
an item rendered with the old ``x``. ``{{ }}`` has tracked filter arguments
since #787; the inline-if arm now does the same.
"""

from __future__ import annotations

import re

import pytest

from djust import _rust


def _body(html: str) -> str:
    return html.split('<body dj-id="2">', 1)[-1].split("</body>", 1)[0]


def _partial_then_reference(source: str, initial: dict, change: dict) -> tuple:
    view = _rust.RustLiveView(source)
    for key, value in initial.items():
        view.set_state(key, value)
    view.render_with_diff()
    for key, value in change.items():
        view.set_state(key, value)
    view.set_changed_keys(list(change))
    partial, _, _ = view.render_with_diff()
    reference = _rust.RustLiveView(source)
    for key, value in {**initial, **change}.items():
        reference.set_state(key, value)
    want, _, _ = reference.render_with_diff()
    return _body(partial), _body(want)


@pytest.mark.parametrize(
    "source",
    [
        "{{ a if c else b|add:x }}",
        '{{ a if c else "z"|add:x }}',
        "{{ a if c else b|default:x }}",
        "{{ a if c else b|add:x.y }}",
    ],
)
def test_the_filter_argument_is_a_dependency(source: str) -> None:
    found = set(_rust.extract_template_variables(source))
    assert "x" in found, sorted(found)


def test_a_quoted_argument_is_not_a_name() -> None:
    found = set(_rust.extract_template_variables("{{ a if c else b|add:'q' }}"))
    assert not {k for k in found if "q" in k}, sorted(found)


@pytest.mark.parametrize(
    ("source", "initial", "change", "expected"),
    [
        (
            "<div>{% if show %}{{ a if c else b|add:x }}{% endif %}</div>",
            {"show": True, "a": "A", "b": "b", "c": False, "x": "1"},
            {"x": "2"},
            "b2",
        ),
        (
            '<div>{% if show %}{{ a if c else "z"|add:x }}{% endif %}</div>',
            {"show": True, "a": "A", "c": False, "x": "1"},
            {"x": "2"},
            "z2",
        ),
    ],
)
def test_a_partial_render_after_only_the_argument_changed(
    source: str, initial: dict, change: dict, expected: str
) -> None:
    got, want = _partial_then_reference(source, initial, change)
    strip = re.compile(r"<[^>]*>|<!--.*?-->")
    assert strip.sub("", got) == strip.sub("", want) == expected


def test_the_loop_item_store_rerenders_after_only_the_argument_changed() -> None:
    template = (
        "<div dj-root><ul>{% for r in items %}<li>{{ r.a if r.n else r.a|add:outer }}"
        '/{{ r.a if r.n else "z"|add:outer }}</li>{% endfor %}</ul></div>'
    )
    view = _rust.RustLiveView(template)
    view.update_state({"items": [{"a": "x", "n": 0}, {"a": "q", "n": 0}], "outer": "A"})
    view.render_with_diff()
    state = {"items": [{"a": "y", "n": 0}, {"a": "q", "n": 0}], "outer": "B"}
    view.update_state(state)
    view.set_changed_keys(["items", "outer"])
    html = view.render_with_diff()[0]
    fresh = _rust.RustLiveView(template)
    fresh.update_state(state)
    strip = re.compile(r' dj-id="[^"]*"')
    assert strip.sub("", html) == strip.sub("", fresh.render_with_diff()[0])
    assert "qB/zB" in html
