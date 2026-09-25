from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust.components.templatetags.djust_components import code_block


def test_code_block_loads_vendored_highlight_with_integrity():
    html = str(code_block(code="x = 1", language="python", theme="github-dark"))
    assert "cdn.jsdelivr.net" not in html and "cdnjs" not in html
    assert 'src="/static/djust_components/vendor/highlight/highlight.js" integrity="sha384-' in html
    assert (
        'href="/static/djust_components/vendor/highlight/styles/github-dark.css" integrity="sha384-'
        in html
    )


def test_unknown_theme_fails_loudly():
    with pytest.raises(ImproperlyConfigured, match="github-dark"):
        code_block(code="x", language="python", theme="dracula")


def test_highlight_false_loads_nothing():
    assert "<script" not in str(code_block(code="x", highlight=False))


def test_ttyd_template_imports_vendored_xterm():
    from django.template.loader import render_to_string

    html = render_to_string(
        "djust_components/ttyd_terminal.html",
        {"ttyd_url": "ws://x", "rows": 24, "cols": 80, "theme_json": "{}"},
    )
    assert "esm.sh" not in html
    assert 'data-xterm-src="/static/djust_components/vendor/xterm/xterm.mjs"' in html
    assert 'rel="modulepreload"' in html and 'rel="stylesheet"' in html


def test_dependencies_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("djust.components.dependencies")
