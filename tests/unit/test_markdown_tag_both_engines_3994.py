"""``{% djust_markdown %}`` renders under Django's template engine too, byte for byte (#3994).

The tag was a djust-engine tag only. A template rendered by Django's own backend (the demo
project's ``/demos/markdown-stream/``) loads ``live_tags`` and could not parse it. ``live_tags``
now registers it, and both registrations call ``djust_markdown_html`` (the one sanitising
renderer, with the one set of options), so the same Markdown is the same bytes on both engines,
hostile Markdown included.
"""

from __future__ import annotations

import logging

import pytest
from django.template import Context, Engine

import djust  # noqa: F401  (registers the native handlers)
from djust._rust import render_template
from djust.template_tags.markdown import MarkdownTagHandler  # noqa: F401
from djust.tests.test_data_table_row_url_link_3887 import view
from scripts.lib.template_library_parity import url_sweep

_DJANGO = Engine(libraries={"live_tags": "djust.templatetags.live_tags"})

HOSTILE = {
    "script-tag": "Hi <script>alert(1)</script> there",
    "script-block": "<script>\nalert(document.cookie)\n</script>\n",
    "raw-html": '<div onclick="alert(1)">x</div>\n\n<img src=x onerror=alert(1)>',
    "iframe": '<iframe src="https://evil.example/"></iframe>',
    "javascript-link": "[click](javascript:alert(1))",
    "javascript-link-mixed-case": "[click](JaVaScRiPt:alert(1))",
    "javascript-autolink": "<javascript:alert(1)>",
    "data-link": "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "vbscript-link": "[x](vbscript:msgbox(1))",
    "javascript-image": "![x](javascript:alert(1))",
    "reference-link": "[x][r]\n\n[r]: javascript:alert(1)\n",
    "entity-scheme": "[x](javascript&#58;alert(1))",
    "nested-emphasis": "***bold italic*** and **bold *nested* bold** and *a **b** c*",
    "deep-nesting": "> " * 60 + "quoted\n",
    "unclosed-emphasis": "Hello **wor",
    "table": "| a | <b>b</b> |\n| - | - |\n| 1 | [x](javascript:1) |\n",
    "task-list": "- [ ] todo <i>x</i>\n- [x] done\n",
    "strike": "~~gone~~ <s>raw</s>",
    "code": "`<script>` and\n\n```\n<script>alert(1)</script>\n```\n",
    "html-comment": "<!-- <script>alert(1)</script> -->text",
    "unicode": "café ☃ ‮ reversed",
    "empty": "",
}

OPTIONS = [
    "",
    "provisional=False",
    "tables=False",
    "strikethrough=False",
    "task_lists=True",
    'provisional="false" tables=0 task_lists="yes"',
]


def _native(src: str, ctx: dict) -> str:
    return render_template(src, ctx)


def _django(src: str, ctx: dict) -> str:
    return _DJANGO.from_string("{% load live_tags %}" + src).render(Context(ctx))


@pytest.mark.parametrize("options", OPTIONS)
@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_both_engines_write_the_same_bytes(name: str, options: str) -> None:
    src = "{% djust_markdown body " + options + " %}"
    ctx = {"body": HOSTILE[name]}
    assert _django(src, ctx) == _native(src, ctx)


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_neither_engine_writes_live_script_or_a_script_url(name: str) -> None:
    """Absolute, not only parity: identical output that is unsafe would pass the test above."""
    for out in (
        _django("{% djust_markdown body %}", {"body": HOSTILE[name]}),
        _native("{% djust_markdown body %}", {"body": HOSTILE[name]}),
    ):
        lowered = out.lower()
        assert "<script" not in lowered and "<iframe" not in lowered, out
        # Parsed: no element carries an ``on...`` attribute, and no URL attribute a script scheme
        # (the escaped TEXT of the payload may still read ``onclick=`` / ``javascript:``).
        assert view(out).handlers == [] and url_sweep.findings(out) == [], out


def test_markdown_is_rendered_not_escaped_on_django() -> None:
    out = _django("{% djust_markdown body %}", {"body": "**bold** *em*"})
    assert "<strong>bold</strong>" in out and "<em>em</em>" in out, out


@pytest.mark.parametrize(
    ("src", "ctx"),
    [
        ("{% djust_markdown body %}", {"body": None}),
        ("{% djust_markdown missing %}", {}),
        ("{% djust_markdown body %}", {"body": 42}),
        ("{% djust_markdown '# literal' %}", {}),
        ("{% djust_markdown body tables=flag %}", {"body": "| a |\n| - |\n| 1 |\n", "flag": False}),
    ],
)
def test_odd_operands_agree(src: str, ctx: dict) -> None:
    assert _django(src, ctx) == _native(src, ctx)


def test_an_unknown_option_is_ignored_with_a_warning_on_both(caplog) -> None:
    src = "{% djust_markdown body bogus=True %}"
    ctx = {"body": "**x**"}
    with caplog.at_level(logging.WARNING, logger="djust.template_tags.markdown"):
        assert _django(src, ctx) == _native(src, ctx)
    assert sum("bogus" in r.getMessage() for r in caplog.records) == 2


def test_no_source_is_empty_with_a_warning_on_both(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="djust.template_tags.markdown"):
        assert _django("{% djust_markdown %}", {}) == _native("{% djust_markdown %}", {}) == ""
    assert sum("without a source" in r.getMessage() for r in caplog.records) == 2
