"""Handler-metadata injection target (#3018) and the raw-text masker's cost (#3019).

* #3018 — ``_inject_handler_metadata`` used ``html.replace("</body>", …)``,
  which put the script before EVERY ``</body>`` string: one inside an inline
  script, a comment or a ``<textarea>`` too. It now uses the masked lookup
  #3017 introduced for the CSRF meta and the client scripts.
* #3019 — the #2663 masker opened a script region with ``<script\\b[^>]*>``:
  every bare ``<script`` with no ``>`` after it scanned to the end, which made
  the mask quadratic (about 3 s at 32 000 of them). ``_split_for_streaming``
  had its own mask of the same shape (22 s on 32 000 unclosed ``<script>``).
"""

from __future__ import annotations

import time

import pytest

from djust import LiveView
from djust.decorators import debounce
from djust.mixins.template import _mask_raw_text


class _View(LiveView):
    template = "<div dj-root>x</div>"

    @debounce(wait=0.5)
    def search(self, **kwargs):
        pass


def _inject(html: str) -> str:
    return _View()._inject_handler_metadata(html)


MARK = "// Handler metadata for client-side decorators"


class TestHandlerMetadataInjection3018:
    def test_injected_once_before_the_real_body_close(self):
        html = (
            "<html><body><script>var s = '</body>';</script>"
            "<!-- </body> --><textarea></body></textarea>"
            "<div>x</div></body></html>"
        )
        out = _inject(html)
        assert out.count(MARK) == 1
        tail = out[out.index(MARK) :]
        assert tail.index("</script>") < tail.index("</body></html>")
        # Everything but the injected script is unchanged.
        start = out.index("\n<script>")
        end = out.index("</script>", start) + len("</script>\n")
        assert out[:start] + out[end:] == html

    def test_html_close_is_the_fallback(self):
        html = "<html><script>'</html>'</script><div>x</div></html>"
        out = _inject(html)
        assert out.count(MARK) == 1
        assert out.endswith("</script>\n</html>")

    def test_appended_when_neither_tag_exists(self):
        out = _inject("<div>x</div>")
        assert out.startswith("<div>x</div>") and out.count(MARK) == 1

    def test_well_formed_page_output_is_unchanged(self):
        """The pre-#3018 ``str.replace`` result, for a page with one ``</body>``."""
        html = "<html><body><div>x</div></body></html>"
        out = _inject(html)
        script = out[len("<html><body><div>x</div>") : out.index("\n</body>")]
        assert out == html.replace("</body>", f"{script}\n</body>")

    def test_uppercase_body_close_is_found(self):
        out = _inject("<HTML><BODY>x</BODY></HTML>")
        assert out.index(MARK) < out.index("</BODY>")


PATHOLOGICAL = [
    "<html><head></head><body>" + "<script" * 32000,
    "<html><body><script>" + "</script" * 32000,
    "<html><body>" + "<style" * 32000,
    "<html><body><style>" + "</style " * 32000,
]


class TestMaskerIsLinear3019:
    @pytest.mark.parametrize("html", PATHOLOGICAL)
    def test_mask_raw_text(self, html):
        start = time.perf_counter()
        masked = _mask_raw_text(html)
        assert time.perf_counter() - start < 0.5
        assert len(masked) == len(html)

    @pytest.mark.parametrize(
        "html",
        [
            "<div dj-root>x</div>" + "<script>" * 32000,
            "<div dj-root>x</div>" + "<script" * 32000,
        ],
    )
    def test_split_for_streaming(self, html):
        start = time.perf_counter()
        _View()._split_for_streaming(html)
        assert time.perf_counter() - start < 0.5

    def test_regions_are_masked_as_before(self):
        html = (
            "<p>a</p><script type='x'>if (a</b) {}</script ><style>b{}</style>"
            "<!-- c --><script>unterminated"
        )
        masked = _mask_raw_text(html)
        assert masked.startswith("<p>a</p>\x00")
        assert set(masked[len("<p>a</p>") :]) == {"\x00"}

    def test_streaming_split_ignores_body_close_in_script_and_comment(self):
        html = (
            "<html><body><div dj-root><script>'</body>'</script>"
            "<!-- </body> --></div></body></html>"
        )
        shell_open, main, shell_close = _View()._split_for_streaming(html)
        assert shell_close == "</body></html>"
        assert shell_open + main + shell_close == html
