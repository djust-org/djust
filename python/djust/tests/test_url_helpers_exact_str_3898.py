"""URL helpers convert values to the exact ``str`` type before checking them (#3898).

The checks in ``djust.components.utils`` read a value with ``strip()``, ``lower()``, slices and
``startswith``. On a ``str`` subclass those are the subclass's methods, and a subclass may return a
marked-safe (or simply different) object from any of them, so a check that ran on that result could
be skipped by the value's own type. ``_exact_str`` builds a base ``str`` from the content first.

``Hostile`` below overrides every method the helpers could call, each one returning a benign
marked-safe-looking answer; a helper that trusted one of them would let the real content through.
"""

from __future__ import annotations

import pytest
from django.utils.safestring import mark_safe

from djust.components.utils import (
    _exact_str,
    attr_text,
    data_url_attr,
    safe_css_color,
    safe_css_length,
    safe_url,
    srcset_attr,
    url_allowed,
    url_attr,
)

BENIGN = mark_safe("/benign")


class Hostile(str):
    """A ``str`` whose every method lies: the content is the dangerous text, the answers are not."""

    def __str__(self) -> str:  # type: ignore[override]
        return self  # ``str()`` of it is itself: every later method call is the lying one

    def __html__(self) -> str:
        return str.__str__(self)

    def strip(self, *args: object) -> str:  # type: ignore[override]
        return BENIGN

    lstrip = rstrip = lower = upper = casefold = strip  # type: ignore[assignment]

    def replace(self, *args: object) -> str:  # type: ignore[override]
        return BENIGN

    def startswith(self, *args: object) -> bool:  # type: ignore[override]
        return False

    endswith = startswith  # type: ignore[assignment]

    def __getitem__(self, key: object) -> str:  # type: ignore[override]
        return BENIGN

    def split(self, *args: object) -> list[str]:  # type: ignore[override]
        return [BENIGN]

    def encode(self, *args: object) -> bytes:  # type: ignore[override]
        return b"/benign"

    def __iter__(self):  # type: ignore[override]
        return iter("/benign")

    def __contains__(self, item: object) -> bool:
        return False


SCRIPT = ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)", "vbscript:x"]
BREAKOUT = '/x" onmouseover="alert(1)" y="'


def test_the_hostile_class_really_lies() -> None:
    """Not a tautology: the class defeats the naive reading it is here to catch."""
    h = Hostile("javascript:alert(1)")
    assert h.strip() is BENIGN and h.lower() is BENIGN and not h.startswith("javascript:")
    assert str.__str__(h) == "javascript:alert(1)" and type(str(h)) is Hostile
    assert str(h).strip() is BENIGN  # what ``str(value).strip()`` would hand a check


@pytest.mark.parametrize("text", [*SCRIPT, BREAKOUT, "/ok?a=1&b=2", ""])
def test_the_converted_value_is_the_exact_type_with_the_same_content(text: str) -> None:
    out = _exact_str(Hostile(text))
    assert type(out) is str and out == text
    assert type(_exact_str(mark_safe(text))) is str


@pytest.mark.parametrize("text", SCRIPT)
@pytest.mark.parametrize("image", [False, True])
def test_a_hostile_str_gets_the_scheme_check_on_its_real_content(text: str, image: bool) -> None:
    value = Hostile(text)
    assert safe_url(value) == "#"
    assert url_allowed(value, image=image) is False
    assert url_attr(value, image=image, honor_safe=False) == "#"
    assert data_url_attr(value, image=image) == "#"
    # a srcset splits on whitespace (``java<TAB>script:`` is a candidate and a descriptor): the
    # answer for the hostile value is the answer for its text
    assert srcset_attr(value, honor_safe=False) == srcset_attr(text, honor_safe=False)


@pytest.mark.parametrize("claim", ["hostile", "plain"])
def test_a_hostile_str_gets_the_same_answer_as_the_same_text_unwrapped(claim: str) -> None:
    for text in [
        *SCRIPT,
        BREAKOUT,
        "/ok?a=1&b=2",
        "https://e.x/a b",
        "  /padded  ",
        "data:image/png;base64,AAAA",
    ]:
        value = Hostile(text) if claim == "hostile" else text
        for image in (False, True):
            assert data_url_attr(value, image=image) == data_url_attr(text, image=image), text
        assert safe_url(value) == safe_url(text), text


def test_a_hostile_str_is_escaped_in_an_attribute_and_keeps_the_no_op_href() -> None:
    assert attr_text(Hostile(BREAKOUT)) == attr_text(BREAKOUT)
    assert 'onmouseover="' not in attr_text(Hostile(BREAKOUT))
    assert data_url_attr(Hostile("javascript:void(0)")) == "javascript:void(0)"
    assert data_url_attr(Hostile("javascript:void(0);alert(1)")) == "#"
    assert type(data_url_attr(Hostile("/ok?a=1&b=2"))) is not Hostile
    assert data_url_attr(Hostile("/ok?a=1&b=2")) == "/ok?a=1&amp;b=2"


@pytest.mark.parametrize(
    "text", ["10px;background:url(//e.x/a)", '10px" onmouseover="x', "red;x:y"]
)
def test_the_css_checks_read_the_real_content_too(text: str) -> None:
    assert safe_css_length(Hostile(text), "") == ""
    assert safe_css_color(Hostile(text), "") == ""
    assert safe_css_length(Hostile("120px"), "") == "120px"
    assert type(safe_css_length(Hostile("120px"), "")) is str
