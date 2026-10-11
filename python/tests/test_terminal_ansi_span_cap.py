"""Terminal's ANSI conversion keeps a bounded number of styles open per line.

Each open style is a nested ``<span>`` that stays open until a reset or the end
of the line, so a short line (one ``ESC[1m`` is 4 bytes) could nest as deep as
it was long. The conversion exists three times (the ``Terminal`` component, the
inline handler, the ``{% terminal %}`` tag) and they must agree. Ordinary lines
render byte for byte as before; only a line that keeps more than
``MAX_OPEN_SPANS`` styles open is cut.
"""

from __future__ import annotations

import random
import re
from html.parser import HTMLParser

import pytest


class _Depth(HTMLParser):
    """Deepest element nesting of an HTML string."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.max = 0

    def handle_starttag(self, tag, attrs):
        self.depth += 1
        self.max = max(self.max, self.depth)

    def handle_endtag(self, tag):
        self.depth -= 1


def html_depth(html: str) -> int:
    parser = _Depth()
    parser.feed(html)
    return parser.max


def _terminal_converters():
    from djust.components.components.terminal import Terminal
    from djust.components.rust_handlers import TerminalHandler
    from djust.components.templatetags._dev_tools import TerminalNode

    return {
        "component": Terminal._ansi_to_html,
        "handler": TerminalHandler._ansi_to_html,
        "tag": TerminalNode._ansi_to_html,
    }


def _reference_ansi_to_html(text: str) -> str:
    """The conversion as it was before the caps (one span per style, no limit)."""
    import html as _html

    colors = {
        "30": "#000", "31": "#e74c3c", "32": "#2ecc71", "33": "#f1c40f",
        "34": "#3498db", "35": "#9b59b6", "36": "#1abc9c", "37": "#ecf0f1",
        "90": "#7f8c8d", "91": "#ff6b6b", "92": "#55efc4", "93": "#ffeaa7",
        "94": "#74b9ff", "95": "#a29bfe", "96": "#81ecec", "97": "#fff",
    }  # fmt: skip
    result = []
    open_spans = 0
    last_end = 0
    for m in re.compile(r"\033\[([0-9;]*)m").finditer(text):
        start, end = m.span()
        result.append(_html.escape(text[last_end:start]))
        last_end = end
        for code in m.group(1).split(";"):
            if code == "0" or code == "":
                result.append("</span>" * open_spans)
                open_spans = 0
            elif code == "1":
                result.append('<span style="font-weight:bold">')
                open_spans += 1
            elif code in colors:
                result.append(f'<span style="color:{colors[code]}">')
                open_spans += 1
    result.append(_html.escape(text[last_end:]))
    result.append("</span>" * open_spans)
    return "".join(result)


def _ordinary_lines(seed: int, count: int) -> list[str]:
    """Lines a real program prints: text, a handful of SGR sequences, each with
    a few parameters, never more than the caps allow."""
    rng = random.Random(seed)
    params = ["0", "1", "31", "32", "33", "34", "90", "97", "4", "7", "", "38"]
    pieces = ["ok ", "error: <b>&", "x", "  ", "é", "[1m", "\x1b[", "\x1b[2K", "plain"]
    lines = []
    for _ in range(count):
        parts = []
        for _ in range(rng.randint(0, 12)):
            if rng.random() < 0.5:
                parts.append(rng.choice(pieces))
            else:
                parts.append(
                    "\x1b[" + ";".join(rng.choice(params) for _ in range(rng.randint(0, 3))) + "m"
                )
        lines.append("".join(parts))
    return lines


def test_ordinary_lines_are_byte_identical_to_the_uncapped_conversion():
    converters = _terminal_converters()
    cap = converters["component"].__self__.MAX_OPEN_SPANS
    lines = _ordinary_lines(2026, 4000) + ["", "no escapes", "\x1b[1;31mred\x1b[0m done"]
    checked = 0
    for line in lines:
        expected = _reference_ansi_to_html(line)
        if html_depth(expected) > cap:
            continue  # not ordinary: more styles open than a line is allowed to keep
        for name, convert in converters.items():
            assert convert(line) == expected, (name, line)
        checked += 1
    assert checked > 3900


@pytest.mark.parametrize(
    "line",
    [
        "\x1b[" + ";".join(["1"] * 100_000) + "mhello",
        "\x1b[1m" * 100_000 + "hello",
        "\x1b[31m" * 100_000 + "hello" + "\x1b[0m",
        ("\x1b[1;31;1;31;1;31;1;31;1;31;1;31;1;31;1;31;1;31m" * 5_000) + "x",
    ],
)
def test_pathological_lines_nest_a_bounded_number_of_spans(line):
    converters = _terminal_converters()
    outputs = {name: convert(line) for name, convert in converters.items()}
    cap = converters["component"].__self__.MAX_OPEN_SPANS
    for name, out in outputs.items():
        assert html_depth(out) <= cap, name
        assert "hello" in out or line.endswith("x"), name
        assert out.count("<span") == out.count("</span>"), name
    assert len(set(outputs.values())) == 1, "the three copies agree"


def test_a_reset_closes_the_spans_and_styles_work_again():
    convert = _terminal_converters()["component"]
    cap = convert.__self__.MAX_OPEN_SPANS
    line = "\x1b[1m" * (cap + 10) + "a\x1b[0m\x1b[31mb\x1b[0m"
    out = convert(line)
    assert out.count('<span style="font-weight:bold">') == cap
    assert out.endswith('<span style="color:#e74c3c">b</span>')


def test_the_parameter_cap_matches_the_client_hook():
    convert = _terminal_converters()["component"]
    params = convert.__self__.MAX_PARAMS
    out = convert("\x1b[" + ";".join(["1"] * (params + 5)) + "mx")
    assert out.count("<span") == params


def test_a_reset_past_the_parameter_cap_still_closes_the_spans():
    """Only styles are capped: a reset anywhere in a sequence closes every open
    span, as it does in a terminal."""
    for convert in _terminal_converters().values():
        params = convert.__self__.MAX_PARAMS
        line = "\x1b[" + ";".join(["1"] * (params + 4) + ["0"]) + "mx"
        out = convert(line)
        assert out.count("<span") == params
        assert out.endswith("</span>" * params + "x")
        # an empty parameter is a reset too
        assert convert("\x1b[" + "1;" * (params + 4) + ";mx").endswith("</span>" * params + "x")


def test_a_run_of_resets_is_cheap_and_leaves_nothing_open():
    convert = _terminal_converters()["component"]
    out = convert("\x1b[1m\x1b[" + ";" * 100_000 + "mx")
    assert out == '<span style="font-weight:bold"></span>x'
