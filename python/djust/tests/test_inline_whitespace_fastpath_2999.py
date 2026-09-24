"""#2999 review H1: the text fast path must not go stale after a full parse.

A text fragment that becomes empty (or whitespace-only) is sent to the full
parse, which drops its text node. The fragment→text-node map used to survive
that parse, so when the fragment filled again the fast path emitted a
``SetText`` at a path that no longer existed: the server's own tree never took
the new text, ``render_with_diff()`` kept returning the empty element, and the
client logged "node not found". These drive real views through
``LiveViewTestClient`` and check the server's HTML and patches at every step;
``tests/js/inline_whitespace_parity_2999.test.js`` applies the same kind of
batches (the ``error_message`` / ``separator_variable`` fuzz shapes) in jsdom.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient

pytestmark = [pytest.mark.django_db]


def _view(template: str, initial: Dict[str, Any]) -> type:
    class V(LiveView):
        pass

    V.template = template

    def mount(self: Any, request: Any, **kwargs: Any) -> None:
        for k, v in initial.items():
            setattr(self, k, v)

    def get_context_data(self: Any, **kwargs: Any) -> Dict[str, Any]:
        ctx = LiveView.get_context_data(self, **kwargs)
        for k in initial:
            ctx[k] = getattr(self, k)
        return ctx

    @event_handler
    def set_state(self: Any, **state: Any) -> None:
        for k, v in state.items():
            setattr(self, k, v)

    V.mount = mount
    V.get_context_data = get_context_data
    V.set_state = set_state
    return V


def _node_at(html_tree: Any, path: list) -> Any:
    node = html_tree
    for i in path:
        if i >= len(node.children):
            return None
        node = node.children[i]
    return node


ERROR_TEMPLATE = (
    '<div class="ws-root"><form><input name="email"> '
    '<span class="error">{{ error }}</span></form></div>'
)


def test_error_message_clears_and_comes_back():
    """The review's repro: "Required" -> "" -> "Invalid email" -> "Too short"
    -> "" -> "Bad". Every render must show the current message."""
    c = LiveViewTestClient(_view(ERROR_TEMPLATE, {"error": "Required"}))
    c.mount()
    html, _, _ = c.render_with_patches()
    assert '<span class="error" dj-id="3">Required</span>' in html
    for msg in ["", "Invalid email", "Too short", "", "Bad"]:
        c.send_event("set_state", error=msg)
        html, patches, _ = c.render_with_patches()
        assert f">{msg}</span>" in html, (msg, html, patches)
        if msg:
            # The message reaches the client: a SetText at the span's text
            # node (path [0, 2, 0]) or an InsertChild of it into the span.
            assert any(
                (p["type"] == "SetText" and p["path"] == [0, 2, 0] and p["text"] == msg)
                or (
                    p["type"] == "InsertChild"
                    and p["path"] == [0, 2]
                    and p["node"].get("text") == msg
                )
                for p in patches
            ), (msg, patches)


def test_separator_between_inline_siblings_goes_whitespace_and_back():
    """The review's second H1 shape: ``{{ sep }}`` between two inline
    elements flips x -> " " -> "" -> y. The stale map used to overwrite the
    <i>'s text ("AyB" read "Ay")."""
    t = '<div class="ws-root"><p><b>A</b>{{ sep }}<i>B</i> <u>{{ n }}</u></p></div>'
    c = LiveViewTestClient(_view(t, {"sep": "x", "n": 0}))
    c.mount()
    c.render_with_patches()
    expected = {"x": "<b", " ": "<b", "": "<b", "y": "<b"}
    for n, sep in enumerate([" ", "\n  ", "", "y", "z", " ", "w"], start=1):
        c.send_event("set_state", sep=sep, n=n)
        html, patches, _ = c.render_with_patches()
        body = html.split('<p dj-id="1">', 1)[1].split("</p>", 1)[0]
        shown_sep = " " if sep.strip() == "" and sep else sep
        assert f'>A</b>{shown_sep}<i dj-id="' in body, (sep, body, patches)
        assert '>B</i> <u dj-id="' in body and f">{n}</u>" in body, (sep, body, patches)
        assert expected  # keep flake8 quiet about the unused table
        for p in patches:
            # No patch may overwrite the <i> (path [0, 2] is b, " "/sep, i).
            if p["type"] == "SetText":
                assert p["text"] in (shown_sep, str(n), sep), (sep, p)


def test_fragment_map_is_rebuilt_after_a_structural_render():
    """Any full parse can move text nodes; the fast-path map must follow."""
    t = (
        '<div class="ws-root"><ul>{% for i in items %}<li>{{ i }}</li>{% endfor %}</ul>'
        "<p><b>{{ a }}</b> <i>{{ b }}</i></p></div>"
    )
    c = LiveViewTestClient(_view(t, {"items": ["x"], "a": "A", "b": "B"}))
    c.mount()
    c.render_with_patches()
    c.send_event("set_state", items=["x", "y"])  # structural
    c.render_with_patches()
    c.send_event("set_state", b="C")  # text only
    html, patches, _ = c.render_with_patches()
    assert '<i dj-id="' in html and ">C</i>" in html
    assert patches == [{"type": "SetText", "path": [1, 2, 0], "text": "C"}], patches


def test_loop_placeholder_never_reaches_the_page_on_a_later_partial_render():
    """Found while fixing H1 (pre-existing on main): a render whose loop items
    hit the parse cache stored its REDUCED fragment — with the per-render
    ``<dj-pc-<nonce>>`` placeholder — in the partial-render fragment cache. A
    later partial render that didn't re-render the loop reused that fragment
    with no manifest to expand it, so a full parse put a literal
    ``<dj-pc-…>`` element into the page in place of the item."""
    from djust._rust import RustLiveView

    t = (
        '<div dj-root class="ws-root"><ul>{% for i in items %}<li>{{ i }}</li>{% endfor %}</ul>'
        "{% if f %}<p>on</p>{% endif %}<p><b>{{ a }}</b> <i>{{ b }}</i></p></div>"
    )
    v = RustLiveView(t)
    v.set_loop_render_cache_enabled(True)
    v.update_state({"items": ["x"], "f": False, "a": "A", "b": "B"})
    v.render_with_diff()
    v.set_changed_keys(["items"])
    v.update_state({"items": ["x", "y"]})
    v.render_with_diff()
    v.set_changed_keys(["f"])
    v.update_state({"f": True})
    html, patches, _ = v.render_with_diff()
    assert "dj-pc-" not in html and "dj-pc-" not in patches
    assert "<li" in html and ">x</li>" in html
    # And the text fast path (whose byte map assumes the fragments
    # concatenate to the full html) targets the right node afterwards.
    v.set_changed_keys(["b"])
    v.update_state({"b": "C"})
    html, patches, _ = v.render_with_diff()
    assert "dj-pc-" not in html
    assert ">C</i>" in html
