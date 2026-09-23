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

import pytest

from djust._rust import RustLiveView, diff_html, vdom_inline_level_tags
from djust.mixins.template import _INLINE_LEVEL_TAGS, TemplateMixin


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
# One list of inline-level tags
# ---------------------------------------------------------------------------


def test_python_inline_list_matches_rust():
    assert _INLINE_LEVEL_TAGS == frozenset(vdom_inline_level_tags())


# ---------------------------------------------------------------------------
# Egress normalizer <-> parser parity
# ---------------------------------------------------------------------------

# Bodies placed inside <div dj-root>…</div>. Every one mixes spaces the parser
# keeps with spaces it drops.
PARITY_CORPUS = [
    "<p><b>A</b> <i>B</i></p>",
    "<p>\n  <strong>Lead.</strong>\n  <code>x = 1</code>\n</p>",
    "<ul>\n  <li><strong>Filters.</strong> <code>{% load app_tags %}</code></li>\n  <li>b</li>\n</ul>",
    "<div>a</div> <div>b</div> <span>c</span> <span>d</span>",
    "<p><b>A</b> <br> <i>B</i> <img src=x> <em>C</em></p>",
    "<p><a href='#'>x</a> <svg viewBox='0 0 1 1'><path d='M0 0'/></svg> <span>y</span></p>",
    "<p><span>a</span> <textarea>t\n t</textarea> <code>c\n c</code> <pre>p</pre> <b>z</b></p>",
    '<p><b>A</b> <!--dj-if id="if-1"--><i>B</i><!--/dj-if--> <u>C</u></p>',
    "<p>Hello <b>x</b> world <i>y</i>!</p>",
    "<table><tr><td>a</td> <td>b</td></tr></table>",
    "<p><label>Name</label> <input name=n> <button>Go</button> <select><option>1</option></select></p>",
    "<section>\n  <h1>T</h1>\n  <p><em>e</em> <strong>s</strong></p>\n</section>",
    "<p><b>A</b>\u00a0<i>B</i> <u>C</u></p>",
    "<p><span><b>deep</b> <i>er</i></span> <span>x</span></p>",
]


@pytest.mark.parametrize("body", PARITY_CORPUS)
def test_normalizer_is_identity_on_vdom_html(strip, body):
    """The WS frame is render_with_diff()'s HTML through the normalizer. It
    must not add or remove a single node the server VDOM has."""
    _view, (vdom_html, _p, _v) = _render_with_diff("<div dj-root>{{ h|safe }}</div>", {"h": body})
    normalized = strip(vdom_html)
    patches = json.loads(diff_html(vdom_html, normalized))
    structural = [p for p in patches if p["type"] != "SetText"]
    assert structural == [], f"normalizer changed the structure:\n{vdom_html}\n{normalized}"
    assert normalized.count("> <") == vdom_html.count("> <")


@pytest.mark.parametrize("body", PARITY_CORPUS)
def test_normalizer_agrees_with_parser_on_raw_html(strip, body):
    """The initial-GET dj-root is plain render() output through the same
    normalizer. Parsing it must give the VDOM the parser builds from the raw
    HTML, so the #1610 mount morph has nothing structural to fix."""
    raw = f"<div dj-root>{body}</div>"
    patches = json.loads(diff_html(raw, strip(raw)))
    structural = [p for p in patches if p["type"] != "SetText"]
    assert structural == [], f"normalizer and parser disagree on:\n{raw}\n{strip(raw)}"


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
