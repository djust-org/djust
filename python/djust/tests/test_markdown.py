"""Tests for :mod:`djust.markdown` and the underlying Rust binding.

Covers:

- Roundtrip through the Python helper.
- Kwargs propagation to the Rust side.
- XSS neutralisation at the Python entry point.
- Thread safety of concurrent renders (GIL release).
- Streaming append stability — the prefix of a completed line does not
  change when more characters are appended.
- Return type is ``SafeString``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from django.utils.safestring import SafeString

from djust.markdown import render_markdown


def test_roundtrip_basic():
    out = render_markdown("# Title\n\nparagraph\n")
    assert "<h1>Title</h1>" in out
    assert "<p>paragraph</p>" in out


def test_kwarg_tables_disabled():
    src = "| a | b |\n| - | - |\n| 1 | 2 |\n"
    with_tables = render_markdown(src, tables=True)
    without_tables = render_markdown(src, tables=False)
    assert "<table>" in with_tables
    assert "<table>" not in without_tables


def test_python_render_markdown_escapes_xss():
    out = render_markdown("<script>alert(1)</script>\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_concurrent_renders_thread_safe():
    # Render the same non-trivial doc from many threads and confirm:
    #  (a) no deadlock / panic,
    #  (b) every thread gets a non-empty string back.
    src = "# heading\n\nsome **bold** text and `code`\n"
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: render_markdown(src), range(128)))
    assert len(results) == 128
    assert all("<h1>heading</h1>" in r for r in results)


def test_stream_append_prefix_stable_for_completed_line():
    # Completed line ("hello\n") should render the same way regardless of
    # what comes after it, as long as the trailing line keeps matching the
    # provisional heuristic (so pulldown-cmark only sees the stable prefix).
    prefix = "hello\n"
    # Append characters that keep the trailing line "mid-token": "**wor".
    chunks = ["**w", "**wo", "**wor"]
    stable_renders = []
    for chunk in chunks:
        full = prefix + chunk
        out = render_markdown(full)
        # The <p>hello</p> for the completed line must appear in every render.
        assert "<p>hello</p>" in out
        # Extract everything before the provisional wrapper.
        head, _, _ = out.partition('<p class="djust-md-provisional">')
        stable_renders.append(head)
    # All "stable" (non-provisional) prefixes must be identical.
    assert stable_renders[0] == stable_renders[1] == stable_renders[2]


def test_returns_safestring():
    out = render_markdown("hi\n")
    assert isinstance(out, SafeString)


def test_completed_line_renders_strong():
    # Fully-closed bold with a trailing newline should render as <strong>.
    # The provisional splitter must NOT steal the completed line.
    out = render_markdown("Hello **word**\n")
    assert "<strong>word</strong>" in out
