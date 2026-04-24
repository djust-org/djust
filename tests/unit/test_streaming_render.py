"""
Unit tests for Streaming Initial Render (v0.6.1, Phase 1).

Covers:
- Opt-in flag (``streaming_render`` on LiveView) and the default-off behavior.
- ``_split_for_streaming`` helper on :class:`TemplateMixin` — shell-open,
  main-content, shell-close split plus edge cases (no ``dj-root``, no
  ``</body>``, case insensitivity, whitespace tolerance).
- ``_make_streaming_response`` helper on :class:`RequestMixin` — chunk
  iteration, response headers, and ``Content-Length`` omission.
- End-to-end behavior through the ``get()`` path for both streaming
  and non-streaming modes.

DOM assertions use :mod:`html.parser` per the framework's test guidelines.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.http import HttpResponse, StreamingHttpResponse

from djust import LiveView


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _TagCollector(HTMLParser):
    """Collect lowercase tag names from an HTML fragment (for assertions)."""

    def __init__(self):
        super().__init__()
        self.start_tags: list = []
        self.end_tags: list = []

    def handle_starttag(self, tag, attrs):
        self.start_tags.append(tag)

    def handle_endtag(self, tag):
        self.end_tags.append(tag)


def _parse_tags(html: str) -> _TagCollector:
    parser = _TagCollector()
    parser.feed(html)
    return parser


def _to_bytes(chunk) -> bytes:
    """Normalize streaming_content items (str or bytes) to bytes."""
    return chunk if isinstance(chunk, (bytes, bytearray)) else chunk.encode("utf-8")


def _join_streaming(response) -> bytes:
    return b"".join(_to_bytes(c) for c in response.streaming_content)


# ---------------------------------------------------------------------------
# LiveView subclasses used as fixtures
# ---------------------------------------------------------------------------


class _StreamingView(LiveView):
    """A LiveView opted into streaming with a minimal template.

    Includes a ``<div dj-root>`` wrapper so the render output actually has
    a split boundary for the streaming iterator to fire on. (Templates
    without a wrapper are a documented single-chunk edge case.)
    """

    template = "<div dj-root><p>hello streaming</p></div>"
    streaming_render = True


class _NonStreamingView(LiveView):
    """Default LiveView (streaming_render left at False)."""

    template = "<div dj-root><p>hello non-streaming</p></div>"


# ---------------------------------------------------------------------------
# Tests for the class attribute default
# ---------------------------------------------------------------------------


class TestStreamingRenderDefault:
    """Opt-in flag defaults to False and returns a plain HttpResponse."""

    @pytest.mark.django_db
    def test_streaming_render_default_false_returns_httpresponse(self, get_request):
        """A LiveView without ``streaming_render=True`` returns ``HttpResponse``."""
        view = _NonStreamingView()
        response = view.get(get_request)

        assert isinstance(response, HttpResponse)
        assert not isinstance(response, StreamingHttpResponse)
        # Default HttpResponse path should NOT set the observability marker.
        assert "X-Djust-Streaming" not in response

    def test_streaming_render_class_attr_default_is_false(self):
        """The base ``LiveView`` class attribute defaults to ``False``."""
        assert LiveView.streaming_render is False


# ---------------------------------------------------------------------------
# Tests for end-to-end streaming behavior through get()
# ---------------------------------------------------------------------------


class TestStreamingRenderEnabled:
    """``streaming_render=True`` returns a chunked ``StreamingHttpResponse``."""

    @pytest.mark.django_db
    def test_streaming_render_true_returns_streaming_response(self, get_request):
        """Opt-in flag produces a ``StreamingHttpResponse``."""
        view = _StreamingView()
        response = view.get(get_request)

        assert isinstance(response, StreamingHttpResponse)

    @pytest.mark.django_db
    def test_streaming_x_djust_streaming_header_set(self, get_request):
        """Streaming responses set ``X-Djust-Streaming: 1`` for observability."""
        view = _StreamingView()
        response = view.get(get_request)

        assert response["X-Djust-Streaming"] == "1"

    @pytest.mark.django_db
    def test_streaming_no_content_length_header(self, get_request):
        """Streaming responses omit ``Content-Length`` (chunked transfer)."""
        view = _StreamingView()
        response = view.get(get_request)

        assert response.has_header("Content-Length") is False

    @pytest.mark.django_db
    def test_streaming_iterator_yields_chunks(self, get_request):
        """Iterator yields at most three chunks (or one for edge case)."""
        view = _StreamingView()
        response = view.get(get_request)

        chunks = list(response.streaming_content)
        # A normal full-page LiveView render has a ``<div dj-root>`` but no
        # outer ``<html><body>`` wrapper by default, so shell_open may be
        # empty and shell_close may be empty. The iterator yields only
        # non-empty chunks, so we get 1–3 chunks here.
        assert 1 <= len(chunks) <= 3

    @pytest.mark.django_db
    def test_streaming_chunks_concatenate_to_original_html(self, get_request):
        """Joining all streamed chunks equals the non-streaming render output.

        The two code paths (``HttpResponse(html)`` and the streaming
        iterator over the same ``html`` string) must produce byte-identical
        bodies. Using the same view class avoids spurious diffs on the
        ``dj-view="<module>.<class>"`` attribute that ``get()`` injects.
        """
        # Build the streamed response through the streaming iterator.
        streaming_view = _StreamingView()
        streaming_response = streaming_view.get(get_request)
        streamed_bytes = _join_streaming(streaming_response)

        # Build a non-streaming baseline by temporarily toggling the flag
        # off on a fresh instance of the SAME class, so the injected
        # ``dj-view`` path matches.
        baseline_view = _StreamingView()
        baseline_view.streaming_render = False
        baseline_response = baseline_view.get(get_request)
        expected_bytes = baseline_response.content

        assert streamed_bytes == expected_bytes

    @pytest.mark.django_db
    def test_streaming_main_content_contains_dj_root_wrapper(self, get_request):
        """The chunk containing the main body starts with ``<div dj-root``."""
        view = _StreamingView()
        response = view.get(get_request)
        chunks = [_to_bytes(c).decode("utf-8") for c in response.streaming_content]

        # Look for the chunk that begins with ``<div dj-root``.
        main_chunks = [c for c in chunks if c.startswith("<div dj-root")]
        assert len(main_chunks) == 1, f"expected exactly one dj-root chunk, got {chunks!r}"


# ---------------------------------------------------------------------------
# Tests for _split_for_streaming helper
# ---------------------------------------------------------------------------


class TestSplitForStreaming:
    """Direct tests against ``TemplateMixin._split_for_streaming``."""

    def _split(self, html: str):
        view = _StreamingView()
        return view._split_for_streaming(html)

    def test_split_basic_three_chunks(self):
        """Full document splits into shell / main / close."""
        html = (
            "<!DOCTYPE html><html><head><title>t</title></head>"
            "<body><header>h</header>"
            "<div dj-root><p>main</p></div>"
            "</body></html>"
        )
        shell_open, main, shell_close = self._split(html)

        # The shell_open must contain the head's closing tag so the browser
        # can begin parsing CSS as soon as the first chunk arrives.
        assert "</head>" in shell_open
        assert main.startswith("<div dj-root")
        assert "<p>main</p>" in main
        assert shell_close.startswith("</body>")
        # Concatenation is lossless.
        assert shell_open + main + shell_close == html

    def test_split_shell_open_contains_head_closing_tag(self):
        """Shell-open chunk must carry ``</head>`` so CSS loads early."""
        html = (
            "<!DOCTYPE html><html><head>"
            '<link rel="stylesheet" href="/x.css">'
            "</head><body>"
            "<div dj-root>x</div>"
            "</body></html>"
        )
        shell_open, _, _ = self._split(html)

        assert "</head>" in shell_open
        # The browser sees at least the beginning of <body> too.
        assert "<body>" in shell_open

    def test_split_shell_close_contains_body_close(self):
        """Shell-close chunk starts with ``</body>``."""
        html = "<html><body><div dj-root>x</div>trailing</body></html>"
        _, _, shell_close = self._split(html)

        assert shell_close.startswith("</body>")
        # Trailing ``</html>`` survives in shell_close.
        assert "</html>" in shell_close

    def test_split_html_without_dj_root_falls_back_to_single_chunk(self):
        """Templates with no ``<div dj-root>`` return ``(html, '', '')``."""
        html = "<div>no root here</div>"
        shell_open, main, shell_close = self._split(html)

        assert shell_open == html
        assert main == ""
        assert shell_close == ""

    def test_split_html_without_body_close_returns_two_chunks(self):
        """No ``</body>`` means main chunk runs to end; shell_close is empty."""
        html = "<html><head></head><body><div dj-root>x</div>"
        shell_open, main, shell_close = self._split(html)

        assert shell_open == "<html><head></head><body>"
        assert main == "<div dj-root>x</div>"
        assert shell_close == ""
        assert shell_open + main + shell_close == html

    def test_split_case_insensitive_dj_root(self):
        """Uppercase ``<DIV DJ-ROOT>`` is detected."""
        html = "<html><body><DIV DJ-ROOT>x</DIV></body></html>"
        shell_open, main, shell_close = self._split(html)

        assert main.startswith("<DIV DJ-ROOT>")
        assert "<body>" in shell_open
        assert shell_close.startswith("</body>")

    def test_split_handles_body_close_whitespace(self):
        """``</body >`` with trailing whitespace inside the tag matches."""
        html = "<html><body><div dj-root>x</div></body ></html>"
        shell_open, main, shell_close = self._split(html)

        assert shell_open == "<html><body>"
        assert main == "<div dj-root>x</div>"
        assert shell_close == "</body ></html>"

    def test_split_preserves_dj_root_attributes(self):
        """Attributes on ``<div dj-root>`` (like ``dj-view=...``) survive."""
        html = '<html><body><div dj-root dj-view="app.FooView">inner</div></body></html>'
        shell_open, main, shell_close = self._split(html)

        assert 'dj-view="app.FooView"' in main
        # Use html.parser to ensure the main chunk is well-formed.
        parser = _parse_tags(main)
        assert parser.start_tags[0] == "div"
        assert "div" in parser.end_tags

    def test_split_word_boundary_on_dj_root(self):
        """``dj-root-other`` must NOT match — ``\\b`` boundary is enforced."""
        html = "<html><body><div dj-rooted>nope</div></body></html>"
        shell_open, main, shell_close = self._split(html)

        # No match means fallback to single-chunk behavior.
        assert shell_open == html
        assert main == ""
        assert shell_close == ""

    # ---- Precise dj-root matching (regression for hyphenated-suffix false-match) ----

    @pytest.mark.parametrize(
        "tag, should_match",
        [
            ("<div dj-root>", True),
            ("<DIV DJ-ROOT>", True),
            ("<div dj-root class='x'>", True),
            ("<div class='x' dj-root>", True),
            ("<div dj-root/>", True),  # self-closing form
            ("<div dj-rooted>", False),  # letter suffix
            ("<div dj-root-other>", False),  # hyphen suffix — the 🔴
            ("<div dj-root-alt class='x'>", False),
            ("<div data-dj-root>", False),  # prefix attribute
            ("<div foo-dj-root-bar>", False),
        ],
    )
    def test_split_matches_dj_root_precisely(self, tag, should_match):
        """Only bare ``dj-root`` attributes match — not hyphenated suffixes/prefixes."""
        html = f"<html><body>{tag}inner</div></body></html>"
        view = _StreamingView()
        shell_open, main, shell_close = view._split_for_streaming(html)

        if should_match:
            # Main chunk must begin with the tag we put in.
            assert main.startswith(tag), (
                f"tag {tag!r} should match but main={main!r}, shell_open={shell_open!r}"
            )
            assert shell_close.startswith("</body>")
        else:
            # No dj-root match -> full HTML stays in shell_open, other chunks empty.
            assert shell_open == html, (
                f"tag {tag!r} should NOT match but split produced "
                f"shell_open={shell_open!r}, main={main!r}, shell_close={shell_close!r}"
            )
            assert main == ""
            assert shell_close == ""

    # ---- </body> inside <script> literal (regression for 🟡 #2) ----

    def test_split_ignores_body_in_script_literal(self):
        """Literal ``</body>`` inside a ``<script>`` block must not split early."""
        html = (
            "<html><body>"
            "<div dj-root><script>var s = '</body>';</script></div>"
            "<div class='footer'>actual footer</div>"
            "</body></html>"
        )
        view = _StreamingView()
        shell_open, main, shell_close = view._split_for_streaming(html)

        # The real </body> is the one AFTER "actual footer"; the one inside
        # the <script> string literal must be skipped.
        assert "actual footer" in main, f"main was {main!r}"
        assert "var s = '</body>';" in main
        assert shell_close.startswith("</body>")
        # Lossless split.
        assert shell_open + main + shell_close == html

    def test_split_ignores_multiple_body_tokens_in_scripts(self):
        """Multiple ``<script>`` blocks with ``</body>`` literals are all skipped."""
        html = (
            "<html><body>"
            "<div dj-root>"
            "<script>a = '</body>';</script>"
            "<p>x</p>"
            "<script>b = '</body>';</script>"
            "</div>"
            "</body></html>"
        )
        view = _StreamingView()
        shell_open, main, shell_close = view._split_for_streaming(html)

        # Only ONE </body> should remain in shell_close (the real one).
        assert shell_close.count("</body>") == 1
        assert "<p>x</p>" in main
        assert shell_open + main + shell_close == html


# ---------------------------------------------------------------------------
# Tests for _make_streaming_response helper
# ---------------------------------------------------------------------------


class TestMakeStreamingResponse:
    """Direct tests against ``RequestMixin._make_streaming_response``."""

    def test_make_streaming_response_yields_three_chunks_for_full_doc(self):
        """A full document produces three non-empty chunks."""
        view = _StreamingView()
        full_html = "<!DOCTYPE html><html><head></head><body><div dj-root>main</div></body></html>"
        response = view._make_streaming_response(full_html)

        chunks = [_to_bytes(c).decode("utf-8") for c in response.streaming_content]
        assert len(chunks) == 3

    def test_make_streaming_response_single_chunk_when_no_dj_root(self):
        """Fragment without ``<div dj-root>`` yields a single chunk."""
        view = _StreamingView()
        html = "<p>no root</p>"
        response = view._make_streaming_response(html)

        chunks = list(response.streaming_content)
        assert len(chunks) == 1
        assert _to_bytes(chunks[0]).decode("utf-8") == html

    def test_make_streaming_response_content_type_is_html_utf8(self):
        view = _StreamingView()
        response = view._make_streaming_response("<div dj-root>x</div>")

        assert "text/html" in response["Content-Type"]
        assert "charset=utf-8" in response["Content-Type"]

    def test_make_streaming_response_marker_header(self):
        view = _StreamingView()
        response = view._make_streaming_response("<div dj-root>x</div>")

        assert response["X-Djust-Streaming"] == "1"

    def test_make_streaming_response_no_content_length(self):
        view = _StreamingView()
        response = view._make_streaming_response("<div dj-root>x</div>")

        assert response.has_header("Content-Length") is False

    def test_make_streaming_response_lossless_roundtrip(self):
        """Concatenated chunks equal the input bytes."""
        view = _StreamingView()
        full_html = (
            "<!DOCTYPE html><html><head><title>t</title></head>"
            "<body><header>H</header>"
            "<div dj-root><main>M</main></div>"
            "<footer>F</footer></body></html>"
        )
        response = view._make_streaming_response(full_html)

        joined = _join_streaming(response)
        assert joined == full_html.encode("utf-8")
