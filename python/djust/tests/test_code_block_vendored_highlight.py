from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust.components.templatetags.djust_components import code_block


def _inline_script(html):
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1)


def test_code_block_loads_vendored_highlight_with_integrity():
    from djust.assets.tags import stored_integrity

    html = str(code_block(code="x = 1", language="python", theme="github-dark"))
    assert "cdn.jsdelivr.net" not in html and "cdnjs" not in html
    script = _inline_script(html)
    assert '"/static/djust_components/vendor/highlight/highlight.js"' in script
    integrity = stored_integrity("djust_components/vendor/highlight/highlight.js", "sha384")
    assert json.dumps(integrity) in script
    assert (
        'href="/static/djust_components/vendor/highlight/styles/github-dark.css" integrity="sha384-'
        in html
    )


def test_highlight_js_is_loaded_once_per_page_not_per_block():
    """Each block used to emit its own <script src>, so 30 blocks parsed and
    executed the 165 KB library 30 times. The inline script now injects it
    once, guarded by window.__djcHljsLoading."""
    page = str(code_block(code="a", language="python")) + str(
        code_block(code="b", language="python")
    )
    assert not re.search(r"<script[^>]*\bsrc=", page)
    assert page.count("window.__djcHljsLoading") >= 2
    assert "if(!window.hljs&&!window.__djcHljsLoading)" in page


def test_absolute_static_url_sets_cross_origin_on_the_injected_script():
    with override_settings(STATIC_URL="https://static.example.com/"):
        script = _inline_script(str(code_block(code="x", language="python")))
    assert '"https://static.example.com/djust_components/vendor/highlight/highlight.js"' in script
    assert '"crossOrigin": "anonymous"' in script
    script = _inline_script(str(code_block(code="x", language="python")))
    assert '"crossOrigin": null' in script


def test_emitted_inline_script_is_valid_javascript(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    target = tmp_path / "code_block_inline.js"
    target.write_text(_inline_script(str(code_block(code="</script>x", language="python"))))
    result = subprocess.run([node, "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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


def test_wait_loop_gives_up_after_200_ticks_and_warns_once():
    """R14: if the vendored highlight.js never loads (bad SRI, CSP block,
    404), the fallback poll must not run forever."""
    html = str(code_block(code="x = 1", language="python"))
    assert "tries>=200" in html
    assert "clearInterval(iv)" in html
    assert "__djcHljsWarned" in html
    assert "console.warn(" in html
    assert "highlight.js did not load" in html
    # The warn is guarded by the flag, not unconditional.
    assert "if(!window.__djcHljsWarned){window.__djcHljsWarned=true;" in html
