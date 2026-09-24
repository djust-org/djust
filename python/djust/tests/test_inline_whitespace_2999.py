"""#2999: the space between two inline siblings survives the LiveView render.

``<b>A</b> <i>B</i>`` used to come out of ``render_with_diff()`` as
``<b>A</b><i>B</i>`` ("AB"): the VDOM parser dropped every whitespace-only
text node, and the egress normalizer (``_strip_comments_and_whitespace``)
collapsed every ``> <`` to ``><``. Both now keep the whitespace between two
inline-level siblings as one ``" "`` and still drop it everywhere else.

The normalizer runs on ``render_with_diff()``'s own output before it goes on
the wire, so it must agree with the parser EXACTLY: a space it dropped that the
server VDOM kept would leave the client one child short of the server.
"""

# Configure Django settings BEFORE any djust imports
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    django.setup()

import json
import re
from html.parser import HTMLParser

import pytest

from djust._rust import RustLiveView, collapse_inter_tag_whitespace, diff_html
from djust.mixins.template import TemplateMixin


@pytest.fixture
def strip():
    return TemplateMixin()._strip_comments_and_whitespace


def _render_with_diff(template, state):
    view = RustLiveView(template)
    view.update_state(state)
    return view, view.render_with_diff()


# ---------------------------------------------------------------------------
# The issue's repro
# ---------------------------------------------------------------------------


def test_issue_repro_render_with_diff_keeps_the_spaces():
    html = "<p><b>A</b> <i>B</i></p><li><strong>Lead.</strong> <code>x</code></li>"
    view, (out, _patches, _v) = _render_with_diff("<div dj-root>{{ h|safe }}</div>", {"h": html})
    assert "<b>A</b>" not in out  # dj-ids stamped
    assert '</b> <i dj-id="' in out
    assert '</strong> <code dj-id="' in out
    # And render() still agrees.
    assert "</b> <i>" in view.render()


def test_block_whitespace_is_still_dropped():
    _view, (out, _p, _v) = _render_with_diff(
        "<div dj-root>{{ h|safe }}</div>",
        {"h": "<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>\n<p>x</p>\n<p>y</p>"},
    )
    assert "</li><li" in out
    assert "</ul><p" in out
    assert "</p><p" in out
    assert '<ul dj-id="1"><li' in out  # leading indentation dropped


def test_patch_after_a_kept_space_targets_the_right_path():
    view = RustLiveView("<div dj-root><p><b>{{ a }}</b> <i>{{ b }}</i></p></div>")
    view.update_state({"a": "A", "b": "B"})
    view.render_with_diff()
    view.update_state({"b": "C"})
    _html, patches, _v = view.render_with_diff()
    patches = json.loads(patches)
    assert patches == [{"type": "SetText", "path": [0, 2, 0], "text": "C"}]


def test_text_fast_paths_still_fire_on_a_page_with_kept_spaces():
    """The kept " " nodes are excluded from the text fast-path index (the
    byte scanner never emits whitespace-only runs), so a page with inline
    spaces keeps its parse-free text updates — and they target the path with
    the spaces counted."""
    view = RustLiveView("<div dj-root><p><b>{{ a }}</b> <i>{{ b }}</i> <u>tail</u></p></div>")
    view.update_state({"a": "A", "b": "B"})
    view.render_with_diff()
    view.set_changed_keys(["b"])
    view.update_state({"b": "C"})
    _html, patches, _v = view.render_with_diff()
    assert json.loads(patches) == [{"type": "SetText", "path": [0, 2, 0], "text": "C"}]
    assert view.get_render_timing()["fast_path"] != 0.0, "text fast path disabled"


def test_text_becoming_whitespace_only_goes_through_the_full_parse():
    """Whether a whitespace-only text node survives depends on its
    neighbours; the fast path can't see them, so it must not write one."""
    view = RustLiveView("<div dj-root><div>{{ a }}</div><p><b>x</b>{{ sep }}<i>y</i></p></div>")
    view.update_state({"a": "A", "sep": "-"})
    view.render_with_diff()
    view.set_changed_keys(["sep"])
    view.update_state({"sep": " "})
    html, patches, _v = view.render_with_diff()
    assert view.get_render_timing()["fast_path"] == 0.0
    assert "</b> <i" in html
    assert json.loads(patches) == [{"type": "SetText", "path": [1, 1], "text": " "}]


# ---------------------------------------------------------------------------
# One rule for the parser and the normalizer
# ---------------------------------------------------------------------------


def test_normalizer_uses_the_rust_rule():
    """The inter-tag decision is `djust_core::html_whitespace` — the same
    module the parser's `build_children` uses — so there is no second list to
    drift (the first cut of #2999 kept a Python copy)."""
    assert (
        collapse_inter_tag_whitespace("<p><b>A</b> <i>B</i> </p>", []) == "<p><b>A</b> <i>B</i></p>"
    )
    assert (
        collapse_inter_tag_whitespace("<div>a</div> <div>b</div>", []) == "<div>a</div><div>b</div>"
    )
    assert (
        collapse_inter_tag_whitespace("<b>x</b> __PRESERVED_BLOCK_0__", ["code"])
        == "<b>x</b> __PRESERVED_BLOCK_0__"
    )
    assert (
        collapse_inter_tag_whitespace("<b>x</b> __PRESERVED_BLOCK_0__", ["pre"])
        == "<b>x</b>__PRESERVED_BLOCK_0__"
    )


# ---------------------------------------------------------------------------
# Egress normalizer <-> parser parity
# ---------------------------------------------------------------------------

# Bodies placed inside <div dj-root>…</div>. Every one mixes spaces the parser
# keeps with spaces it drops. They are written the way the HTML5 tree builder
# would build them (explicit <tbody>, no <pre> inside <p>) because the Python
# check below uses a tokenizer, not a tree builder; cases that depend on
# tree-construction fix-ups (`<image>`, foster parenting) are checked in jsdom
# by the JS normalizer-corpus test.
PARITY_CORPUS = [
    "<p><b>A</b> <i>B</i></p>",
    "<p>\n  <strong>Lead.</strong>\n  <code>x = 1</code>\n</p>",
    "<ul>\n  <li><strong>Filters.</strong> <code>{% load app_tags %}</code></li>\n  <li>b</li>\n</ul>",
    "<div>a</div> <div>b</div> <span>c</span> <span>d</span>",
    "<p><b>A</b> <br> <i>B</i> <img src=x> <em>C</em></p>",
    "<p><a href='#'>x</a> <svg viewBox='0 0 1 1'><path d='M0 0'/></svg> <span>y</span></p>",
    "<div><span>a</span> <textarea>t\n t</textarea> <code>c\n c</code> <pre>p</pre> <b>z</b></div>",
    '<p><b>A</b> <!--dj-if id="if-1"--><i>B</i><!--/dj-if--> <u>C</u></p>',
    "<p>Hello <b>x</b> world <i>y</i>!</p>",
    "<table><tbody><tr><td>a</td> <td>b</td></tr></tbody></table>",
    "<p><label>Name</label> <input name=n> <button>Go</button> <select><option>1</option></select></p>",
    "<section>\n  <h1>T</h1>\n  <p><em>e</em> <strong>s</strong></p>\n</section>",
    "<p><b>A</b>\u00a0<i>B</i> <u>C</u></p>",
    "<p><span><b>deep</b> <i>er</i></span> <span>x</span></p>",
]


# The client's view of a parsed page (#2999 review M1). `diff_html` can't be
# used to check the normalizer: it re-parses both sides with the SERVER parser,
# which re-applies the drop rule, so a normalizer that wrongly KEEPS a " "
# between two blocks would still compare equal. Instead, parse with a real
# HTML tokenizer (stdlib `html.parser`) that keeps every whitespace node, as
# the browser does, and count children with the client's rule
# (`isSignificantChild` in 12-vdom-patch.js). The JS twin of this check, over a
# seeded random corpus parsed by jsdom, is
# tests/js/inline_whitespace_parity_2999.test.js ("normalizer corpus").

_VOID = frozenset(
    "area base br col embed hr img input keygen link meta param source track wbr".split()
)
_PRESERVE = frozenset(["pre", "code", "textarea", "script", "style"])


class _ClientTree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ["#root", []]
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = [tag, []]
        self.stack[-1][1].append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1][1].append([tag, []])

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        kids = self.stack[-1][1]
        if kids and isinstance(kids[-1], str):
            kids[-1] += data
        else:
            kids.append(data)

    def handle_comment(self, data):
        self.stack[-1][1].append(("#comment", data))


def _is_dj_if(text):
    t = text.strip()
    return t in ("dj-if", "/dj-if") or t.startswith("dj-if ") or t.startswith("dj-if\t")


def _significant(children, preserve):
    out = []
    for c in children:
        if isinstance(c, str):
            if preserve or c == " " or re.search(r"[^ \t\n\r\f]", c):
                out.append(("text", c))
        elif isinstance(c, tuple):
            if _is_dj_if(c[1]):
                out.append(("comment", c[1].strip()))
        else:
            out.append((c[0], _significant(c[1], preserve or c[0] in _PRESERVE)))
    return out


def client_tree(html):
    """Significant-child tree of ``html`` as the client counts it. Text
    content is compared after collapsing whitespace runs (the normalizer
    collapses runs inside text, which moves no index)."""
    p = _ClientTree()
    p.feed(html)
    p.close()

    def norm(tree):
        return [
            (k, re.sub(r"[ \t\n\r\f]+", " ", v))
            if k == "text"
            else (k, v)
            if k == "comment"
            else (k, norm(v))
            for k, v in tree
        ]

    return norm(_significant(p.root[1], False))


def _vdom_html(body):
    _view, (vdom_html, _p, _v) = _render_with_diff("<div dj-root>{{ h|safe }}</div>", {"h": body})
    return vdom_html


@pytest.mark.parametrize("body", PARITY_CORPUS)
def test_normalizer_is_identity_on_vdom_html(strip, body):
    """The WS frame is render_with_diff()'s HTML through the normalizer. As
    the client counts children, it must hold exactly the server VDOM's."""
    vdom_html = _vdom_html(body)
    assert client_tree(strip(vdom_html)) == client_tree(vdom_html)


@pytest.mark.parametrize("body", PARITY_CORPUS)
def test_normalizer_agrees_with_parser_on_raw_html(strip, body):
    """The initial-GET dj-root is plain render() output through the same
    normalizer. As the client counts children, it must match the server's
    VDOM of the raw HTML, so the #1610 mount morph has nothing to fix."""
    raw = f"<div dj-root>{body}</div>"
    assert client_tree(strip(raw)) == client_tree(_vdom_html(body))


def test_the_client_tree_check_sees_a_space_kept_between_blocks(strip):
    """Guard for the check itself (review M1): a normalizer that KEPT the
    space between two blocks must fail it — `diff_html` said they were equal."""
    vdom = _vdom_html("<div>a</div> <div>b</div>")
    assert client_tree(vdom) != client_tree('<div dj-root=""><div>a</div> <div>b</div></div>')
    assert json.loads(diff_html(vdom, '<div dj-root=""><div>a</div> <div>b</div></div>')) == [], (
        "diff_html is blind to this — which is why the check doesn't use it"
    )
    # And the real normalizer drops it.
    assert client_tree(strip("<div dj-root><div>a</div> <div>b</div></div>")) == client_tree(vdom)


@pytest.mark.parametrize(
    "body",
    [
        '<p><img alt="<3"> <b>heart</b></p>',
        '<p><input value="<x"> <b>x</b></p>',
        '<p><span title="a<b">t</span> <b>q</b></p>',
        "<p><my-badge>A</my-badge> <my-badge>B</my-badge></p>",
        "<div><x-card>a</x-card> <sl-badge>b</sl-badge></div>",
    ],
)
def test_normalizer_edge_cases_review_l1_m2(strip, body):
    """Review L1 (a ``<`` inside an attribute value, ``<image>``) and M2
    (custom elements are inline): the normalizer agrees with the parser."""
    raw = f"<div dj-root>{body}</div>"
    assert client_tree(strip(raw)) == client_tree(_vdom_html(body))
    assert client_tree(strip(_vdom_html(body))) == client_tree(_vdom_html(body))


def test_custom_elements_keep_the_space_2999():
    _view, (out, _p, _v) = _render_with_diff(
        "<div dj-root>{{ h|safe }}</div>",
        {"h": "<p><my-badge>A</my-badge> <my-badge>B</my-badge></p>"},
    )
    assert "</my-badge> <my-badge" in out


def test_normalizer_keeps_inline_spaces_in_template_source(strip):
    src = (
        "<div dj-root>\n  <p>\n    <strong>{{ a }}</strong>\n    <code>{{ b }}</code>\n"
        "  </p>\n  <div>x</div>\n  <div>y</div>\n</div>"
    )
    out = strip(src)
    assert "<p><strong>{{ a }}</strong> <code>{{ b }}</code></p>" in out
    assert "</div><div>" in out


def test_normalizer_does_not_collapse_nbsp(strip):
    """NBSP is content (the parser keeps it verbatim); Python's ``\\s`` used to
    turn it into a space that the tag rule could then drop."""
    out = strip("<div>a</div>\u00a0<div>b</div>")
    assert "\u00a0" in out
