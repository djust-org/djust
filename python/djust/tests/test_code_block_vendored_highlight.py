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
    404), the fallback poll must not run forever. The warning comes from the
    injected script's onerror, not from the poll's cap."""
    html = str(code_block(code="x = 1", language="python"))
    assert "tries>=200" in html
    assert "clearInterval(iv)" in html
    assert "s.onerror=warn;" in html
    assert "clearInterval(iv);warn()" not in html
    assert "__djcHljsWarned" in html
    assert "console.warn(" in html
    assert "highlight.js did not load" in html
    # The warn is guarded by the flag, not unconditional.
    assert "if(!window.__djcHljsWarned){window.__djcHljsWarned=true;" in html


# A tiny fake DOM, run under node's vm module, so the inline scripts' timing
# behaviour is tested rather than their source text.
_NODE_HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const [scriptsPath, scenario] = process.argv.slice(2);
const scripts = JSON.parse(fs.readFileSync(scriptsPath, "utf8"));
const codes = scripts.map(() => ({ dataset: {}, matches: () => true }));
const warns = [];
let injected = null;
const intervals = [];
const ctx = {
  console: { warn: (m) => warns.push(String(m)) },
  setInterval: (fn) => intervals.push(fn) - 1,
  clearInterval: (i) => { intervals[i] = null; },
  MutationObserver: class { observe() {} },
  document: {
    head: { appendChild: (s) => { injected = s; } },
    body: {},
    createElement: () => ({}),
    querySelectorAll: () => codes,
    currentScript: null,
  },
};
ctx.window = ctx;
vm.createContext(ctx);
scripts.forEach((src, i) => {
  ctx.document.currentScript = {
    previousElementSibling: { querySelector: () => codes[i] },
  };
  vm.runInContext(src, ctx);
});
// Run every live poll well past its cap before the library arrives.
for (let t = 0; t < 400; t++) intervals.forEach((fn) => fn && fn());
const polling = intervals.filter(Boolean).length;
if (scenario === "load") {
  ctx.hljs = { highlightElement: (n) => { n.hl = (n.hl || 0) + 1; } };
  injected.onload();
} else {
  injected.onerror();
  injected.onerror();
}
process.stdout.write(JSON.stringify({
  highlighted: codes.map((c) => c.hl || 0),
  warns: warns.length,
  polling,
}));
"""


def _run_page(tmp_path, blocks, scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    scripts = [
        _inline_script(str(code_block(code="x%d" % i, language="python"))) for i in range(blocks)
    ]
    scripts_path = tmp_path / "scripts.json"
    scripts_path.write_text(json.dumps(scripts))
    harness = tmp_path / "harness.js"
    harness.write_text(_NODE_HARNESS)
    result = subprocess.run(
        [node, str(harness), str(scripts_path), scenario], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_late_library_highlights_every_block_without_a_false_warning(tmp_path):
    """The library arrives after the other blocks' polls hit their cap (slow
    network). Its onload must highlight every block on the page, once, and no
    "did not load" warning may fire."""
    out = _run_page(tmp_path, 3, "load")
    assert out["polling"] == 0  # the cap still stops the polls
    assert out["highlighted"] == [1, 1, 1]
    assert out["warns"] == 0


def test_script_error_warns_exactly_once(tmp_path):
    out = _run_page(tmp_path, 3, "error")
    assert out["highlighted"] == [0, 0, 0]
    assert out["warns"] == 1
