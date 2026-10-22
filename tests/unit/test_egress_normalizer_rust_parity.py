"""The Rust egress normalizer gives the Python reference's bytes.

``TemplateMixin._strip_comments_and_whitespace`` runs the Rust port
(``djust_core::html_whitespace::strip_comments_and_whitespace``) on a plain
render and keeps ``_strip_comments_and_whitespace_py`` as the reference, for
provenance renders and the inputs the port declines. This test holds the two
to identical bytes over:

* every HTML string in the client VDOM fixtures (``tests/js/fixtures``,
  including the #2999 normalizer corpus),
* every ``.html`` file in the repository, raw and as rendered by the Rust
  template engine with an empty context,
* a seeded fuzzed corpus built from the rules' edge cases (nested and
  unterminated pre/textarea/script/style, closing-tag forms, comments inside
  raw text, dj-if markers, entities, non-ASCII, every HTML and Unicode space),
* every code point below U+3100 (and a stride above) in the three places a
  character's class decides a rule: the ``\\s*`` before ``dj-if``, the ``\\b``
  after it, and the end of a preserved block's tag name.

It also pins that the port does the work: it may decline only the inputs it
documents (the case-folding specials, a non-ASCII character right after
``dj-if`` in a comment), so a port that returned ``None`` everywhere fails.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from djust._rust import RustLiveView, strip_html_comments_and_whitespace
from djust.mixins.template import TemplateMixin

REPO = Path(__file__).resolve().parents[2]
_PY = TemplateMixin()._strip_comments_and_whitespace_py
_SPECIALS = ("İ", "ı", "ſ")


def _declines_by_design(html: str) -> bool:
    if any(c in html for c in _SPECIALS):
        return True
    start = 0
    while (i := html.find("<!--", start)) >= 0:
        start = i + 1
        rest = html[i + 4 :].lstrip(" \t\n\r\x0b\x0c\x1c\x1d\x1e\x1f\x85\xa0                　")
        rest = rest[1:] if rest.startswith("/") else rest
        if rest.startswith("dj-if") and len(rest) > 5 and not rest[5].isascii():
            return True
    return False


def _check(corpus: list[str]) -> int:
    """Assert parity on every input; return how many the port handled."""
    handled = 0
    for html in corpus:
        expected = _PY(html)
        got = strip_html_comments_and_whitespace(html)
        if got is None:
            assert _declines_by_design(html), f"port declined an input it models: {html!r}"
            continue
        handled += 1
        assert got == expected, f"divergence on {html!r}:\n rust={got!r}\n   py={expected!r}"
    return handled


def _strings(value):
    if isinstance(value, str):
        if "<" in value:
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _fixture_corpus() -> list[str]:
    out = []
    for path in sorted((REPO / "tests" / "js" / "fixtures").glob("*.json")):
        out.extend(_strings(json.loads(path.read_text(encoding="utf-8"))))
    return out


def _template_files() -> list[Path]:
    skip = {".git", ".venv", "node_modules", "target", ".claude"}
    return sorted(
        p for p in REPO.rglob("*.html") if not skip.intersection(p.relative_to(REPO).parts)
    )


# --- fuzzed corpus ---------------------------------------------------------

_WS = [" ", "  ", "\n", "\n  ", "\t", "\r\n", "\x0c", "\x0b", "\x1c", "\xa0", " ", "　"]
_TEXT = [
    "x",
    "hello world",
    "a  b",
    "a\n\nb",
    "&amp;",
    "&lt;b&gt;",
    "&#x27;",
    "é",
    "日本",
    "😀",
    "a < b",
    "1 <  2",
    "it's",
    "__x__",
    "_",
    "-->",
    "->",
    "<!",
    "</",
    "<3",
]
_OPEN = [
    "<div>",
    "<p>",
    "<span>",
    "<b>",
    "<i>",
    "<li>",
    "<ul>",
    "<td>",
    "<my-el>",
    "<DIV>",
    '<a href="/x?a=1&b=2">',
    '<img alt="<3">',
    '<img src="a>b">',
    "<br>",
    "<input>",
    "<svg/>",
    "<x-badge>",
    '<section\n class="a\n  b">',
    "<label for=x>",
    "<prefix>",
    "<preview>",
    "<codeblock>",
    "<scripts>",
    "<stylex>",
    "<textareas>",
    "<div title=it's>",
    "<image src=x>",
]
_CLOSE = [
    "</div>",
    "</p>",
    "</span>",
    "</b>",
    "</i>",
    "</li>",
    "</ul>",
    "</td>",
    "</my-el>",
    "</DIV>",
    "</a>",
    "</label>",
    "</x-badge>",
    "</section>",
]
_RAW = {
    "pre": ["<pre>", "<PRE>", '<pre class="x">', "<pre\n>"],
    "code": ["<code>", "<CODE>", '<code class="lang-py">'],
    "textarea": ["<textarea>", '<textarea name="t">', "<TextArea>"],
    "script": ["<script>", '<script type="module">', "<SCRIPT>", "<script\n  defer>"],
    "style": ["<style>", '<style media="x">', "<STYLE>"],
}
_RAW_CLOSE = {
    t: [f"</{t}>", f"</{t.upper()}>", f"</{t} >", f"</{t}\n>", f"</{t} bar>"] for t in _RAW
}
_RAW_BODY = [
    "a  b",
    "\n  line1\n  line2\n",
    "// c\nvar x = 1;",
    "var s = '<!-- x -->';",
    "/* <!-- */ a { b: c }",
    "<b>bold</b>",
    "if (a < b && c > d) {}",
    "  ",
    "",
    "</scrip",
    "<!--dj-if-->",
    "x\xa0y",
    "__PRESERVED_BLOCK_0__",
]
_COMMENTS = [
    "<!-- c -->",
    "<!---->",
    "<!-->",
    "<!--->",
    "<!-- a -- b -->",
    "<!--\n\n-->",
    "<!--dj-if-->",
    '<!--dj-if id="if-1"-->',
    "<!--/dj-if-->",
    "<!-- dj-if -->",
    "<!--\n /dj-if\n-->",
    "<!-- dj-if-->",
    "<!--\xa0/dj-if-->",
    "<!--dj-ifx-->",
    "<!--dj-if_-->",
    "<!--dj-if1-->",
    "<!--dj-if-x-->",
    "<!--DJ-IF-->",
    "<!--dj-if",
    "<!-- unterminated",
    "<!--[if IE]><p>x</p><![endif]-->",
    "<!--<!-- nested -->-->",
    "<!--dj-if <!-- inner -->",
]
_MISC = [
    "pre>",
    "code class=x>",
    "textarea>",
    "script>",
    "<!DOCTYPE html>",
    "<?xml?>",
    "__PRESERVED_BLOCK_1__",
    "__PRESERVED_BLOCK_12__",
    "__PRESERVED_BLOCK_",
    "<",
    ">",
    "&nbsp;",
    "<div dj-root>",
    "</div>",
]


def _fuzz_html(rng: random.Random, depth: int = 0) -> str:
    parts = []
    for _ in range(rng.randint(0, 7)):
        r = rng.random()
        if r < 0.18:
            parts.append(rng.choice(_WS))
        elif r < 0.36:
            parts.append(rng.choice(_TEXT))
        elif r < 0.52 and depth < 4:
            open_tag = rng.choice(_OPEN)
            close = rng.choice(_CLOSE) if rng.random() < 0.3 else ""
            if not close:
                name = open_tag[1:].split(">")[0].split()[0].split("/")[0].lower()
                close = f"</{name}>" if name not in ("br", "input", "img", "svg") else ""
            parts.append(open_tag + _fuzz_html(rng, depth + 1) + close)
        elif r < 0.70:
            tag = rng.choice(list(_RAW))
            body = "".join(rng.choice(_RAW_BODY) for _ in range(rng.randint(0, 3)))
            if depth < 3 and rng.random() < 0.3:
                body += _fuzz_html(rng, depth + 1)
            close = rng.choice(_RAW_CLOSE[tag]) if rng.random() < 0.9 else ""
            parts.append(rng.choice(_RAW[tag]) + body + close)
        elif r < 0.85:
            parts.append(rng.choice(_COMMENTS))
        else:
            parts.append(rng.choice(_MISC))
    return "".join(parts)


def _fuzz_corpus(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [_fuzz_html(rng) for _ in range(n)]


# --- tests ----------------------------------------------------------------


def test_parity_on_vdom_fixtures():
    corpus = _fixture_corpus()
    assert len(corpus) > 500
    assert _check(corpus) == len(corpus)


def test_parity_on_repository_templates_raw_and_rendered():
    raw = [p.read_text(encoding="utf-8", errors="replace") for p in _template_files()]
    assert len(raw) > 100
    rendered = []
    for source in raw:
        try:
            rendered.append(RustLiveView(source).render())
        except Exception:  # noqa: BLE001 — a template needing context; raw is still checked
            continue
    assert len(rendered) > 50
    corpus = raw + rendered
    assert _check(corpus) == len(corpus)


@pytest.mark.parametrize("seed", [3570, 1927, 1678])
def test_parity_on_fuzzed_html(seed):
    corpus = _fuzz_corpus(4000, seed)
    handled = _check(corpus)
    # Only the documented declines (a non-ASCII space before ``dj-if`` makes
    # some markers' next char non-ASCII in this corpus) fall back.
    assert handled > 0.95 * len(corpus)


def _codepoints():
    yield from range(0x3100)
    yield from range(0x3100, 0x110000, 97)


def test_parity_on_every_character_class_boundary():
    corpus = []
    for cp in _codepoints():
        if 0xD800 <= cp <= 0xDFFF:
            continue
        c = chr(cp)
        corpus.append(f"<p> <!--{c}dj-if--> <b>x</b>{c} <!--{c}/dj-if--></p>")
        corpus.append(f"<div> <!--dj-if{c}--> <i>y</i> </div>")
        corpus.append(f"<p><b>a</b> <code{c}x>q  r</code> <pre{c}>s  t</pre> <i>b</i></p>")
    _check(corpus)


def test_parity_when_a_removed_comment_joins_a_new_tag():
    """Stripping a comment can join ``<`` and ``pre>`` into a ``<pre>`` the
    input did not have; the port must still preserve that block."""
    corpus = []
    for tag in ("pre", "code", "textarea", "script", "style"):
        for body in ("a  b", "\n x \n", "<b>c</b>  <i>d</i>"):
            corpus += [
                f"<div> <<!-- c -->{tag}>{body}</{tag}> </div>",
                f"<p>x</p>\n<<!---->{tag.upper()} class=a>{body}</{tag}>\n<p>y</p>",
                f"<<!--\n-->{tag}>{body}<<!-- -->/{tag}>  <b>z</b>",
            ]
    assert _check(corpus) == len(corpus)


def test_declined_inputs_fall_back_to_the_reference():
    view = TemplateMixin()
    for html in ["<ſcript>a  b</script> <p> x </p>", "<div> <!--dj-ifé--> </div>"]:
        assert strip_html_comments_and_whitespace(html) is None
        assert view._strip_comments_and_whitespace(html) == _PY(html)


def test_known_shapes():
    """A few outputs pinned literally, so a shared bug cannot hide in parity."""
    strip = TemplateMixin()._strip_comments_and_whitespace
    assert strip("<div>\n  <p>a\n\nb</p>\n</div>") == "<div><p>a\n\nb</p></div>"
    assert strip("<p>x<!-- gone -->y</p>") == "<p>xy</p>"
    assert strip('<div> <!--dj-if id="1"--> </div>') == '<div><!--dj-if id="1"--></div>'
    assert strip("<script>\n// c\nx()\n</script>") == "<script>\n// c\nx()\n</script>"
    assert strip("<style>a{}</style>\n<pre> a  b </pre>") == "<style>a{}</style><pre> a  b </pre>"
    assert strip("<p><b>A</b> <i>B</i></p>") == "<p><b>A</b> <i>B</i></p>"
