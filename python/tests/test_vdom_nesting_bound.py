"""The nesting depth of the page's VDOM is bounded.

Every walker over the VDOM (diff, patch serialization, serialization back to
HTML, clone, drop) recurses once per nesting level, so ``djust._rust`` bounds
the depth of the trees its parser returns (``MAX_VDOM_DEPTH``): an element at
the limit keeps its tag and attributes and the text below it, nothing nests
deeper. These run the real render path (``RustLiveView.render_with_diff``,
what the WebSocket mount and every event use) on a thread with the SMALLEST
stack a walker runs on (2 MiB, tokio's default worker size), in a child
process, so that a failure inside the native code is an exit code and does not end
the test run.

Terminal's ANSI conversion nests one span per style, so a short line could
nest as deep as it was long; those cases render through the same path here, and
the conversion's own cap is pinned in ``test_terminal_ansi_span_cap.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from html.parser import HTMLParser

import pytest

from djust import _rust

# The attribute is read through getattr so that a build without the limit fails
# each test below (the child exits abnormally) instead of this whole module.
MAX = getattr(_rust, "MAX_VDOM_DEPTH", 256)
STACK_KIB = 2048  # the smallest stack a walker runs on (tokio's default worker)

# Run in a child: `{body}` builds `html` (or a template + state) and renders it
# twice through the WebSocket path on a thread with a STACK_KIB stack.
_CHILD = """
import json, sys, threading
from djust._rust import RustLiveView

def run():
    {body}

threading.stack_size({stack} * 1024)
out = {{}}
def target():
    out["result"] = run()
t = threading.Thread(target=target)
t.start()
t.join()
print(json.dumps(out.get("result")))
"""


def _child(body: str, stack_kib: int = STACK_KIB) -> dict:
    """Run `body` (the statements of ``run()``, returning a JSON-able value)
    on a thread with a ``stack_kib`` KiB stack in a child process."""
    code = _CHILD.format(
        body=textwrap.indent(textwrap.dedent(body), "    ").lstrip(), stack=stack_kib
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=300, check=False
    )
    assert proc.returncode == 0, (
        f"child exited with {proc.returncode} (a negative code is a signal): {proc.stderr[-500:]}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


class _Depth(HTMLParser):
    """Deepest element nesting of an HTML string (void elements do not nest)."""

    VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "wbr", "source"}

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.max = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        self.depth += 1
        self.max = max(self.max, self.depth)

    def handle_endtag(self, tag):
        if tag not in self.VOID:
            self.depth -= 1


def html_depth(html: str) -> int:
    parser = _Depth()
    parser.feed(html)
    return parser.max


# ---------------------------------------------------------------------------
# The limit itself
# ---------------------------------------------------------------------------


def test_the_limit_is_exported_and_is_far_beyond_a_real_page():
    assert _rust.MAX_VDOM_DEPTH == 256


@pytest.mark.parametrize("extra", [-1, 0, 1, 2])
def test_the_limit_is_exact_through_the_websocket_render_path(extra):
    # `<div dj-root>` is level 1, so `n` spans inside it reach level n + 1.
    n = MAX - 1 + extra
    result = _child(
        f"""
        lv = RustLiveView('<div dj-root>{{{{ x|safe }}}}{{{{ c }}}}</div>', [])
        lv.update_state({{'x': '<span>' * {n} + 'leaf' + '</span>' * {n}, 'c': 0}})
        html = lv.render_with_diff()[0]
        return html
        """
    )
    assert html_depth(result) == min(n + 1, MAX)
    assert "leaf" in result, "the text below the limit is kept"


# ---------------------------------------------------------------------------
# Content that nests as deep as it is long, through each way it reaches a page
# ---------------------------------------------------------------------------

_RENDER_TWICE = """
lv = RustLiveView('<div dj-root>{{ x|safe }}<p>{{ c }}</p></div>', [])
lv.update_state({'x': first, 'c': 0})
mount = lv.render_with_diff()
lv.update_state({'x': second, 'c': 1})
event = lv.render_with_diff()
return {'mount_depth': mount[0], 'patches': event[1] is not None}
"""


def _render_twice(first_expr: str, second_expr: str) -> dict:
    result = _child(
        f"""
        first = {first_expr}
        second = {second_expr}
        {textwrap.indent(_RENDER_TWICE.strip(), "        ").lstrip()}
        """
    )
    result["depth"] = html_depth(result.pop("mount_depth"))
    return result


@pytest.mark.parametrize("n", [20_000, 200_000])
def test_deeply_nested_raw_html_renders_and_diffs(n):
    got = _render_twice(
        f"'<span>' * {n} + 'a' + '</span>' * {n}", f"'<span>' * {n} + 'b' + '</span>' * {n}"
    )
    assert got["depth"] <= MAX
    assert got["patches"] is True


def test_a_line_of_ansi_parameters_through_terminal_renders_and_diffs():
    line = "\x1b[" + ";".join(["1"] * 20_000) + "mhello"
    got = _render_twice(
        f"__import__('djust.components.components.terminal', fromlist=['x']).Terminal(output=[{line!r}]).render()",
        f"__import__('djust.components.components.terminal', fromlist=['x']).Terminal(output=[{line!r} + '!']).render()",
    )
    assert got["depth"] <= MAX


def test_many_ansi_sequences_through_terminal_render_and_diff():
    line = "\x1b[1m" * 5_000 + "x"
    got = _render_twice(
        f"__import__('djust.components.components.terminal', fromlist=['x']).Terminal(output=[{line!r}]).render()",
        f"__import__('djust.components.components.terminal', fromlist=['x']).Terminal(output=[{line!r} + '!']).render()",
    )
    assert got["depth"] <= MAX


def test_nested_markdown_renders_and_diffs():
    got = _render_twice(
        "__import__('djust').render_markdown('> ' * 20000 + 'quote')",
        "__import__('djust').render_markdown('> ' * 20000 + 'quote!')",
    )
    assert got["depth"] <= MAX


def test_the_diff_entry_point_survives_deep_markup():
    result = _child(
        """
        from djust._rust import diff_html
        deep = '<span>' * 50000 + 'x' + '</span>' * 50000
        return len(diff_html('<div>a</div>', '<div>' + deep + '</div>')) > 0
        """
    )
    assert result is True


def test_a_page_root_under_deep_wrappers_is_still_found():
    result = _child(
        """
        wrapped = '<div>' * 50000 + '<section dj-root>{{ x }}</section>' + '</div>' * 50000
        lv = RustLiveView(wrapped, [])
        lv.update_state({'x': 'hello'})
        return 'hello' in lv.render_with_diff()[0]
        """
    )
    assert result is True
