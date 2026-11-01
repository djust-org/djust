"""``TtydTerminalView`` parses ``?rows=`` / ``?cols=`` defensively and clamps them (#3699).

``mount`` did ``int(params.get("rows", ...))`` on the query string, so ``?rows=x`` or
``?cols=`` raised ``ValueError`` (a 500), and ``?rows=1000000000`` flowed unbounded into
the CSS ``calc(<rows> * 1.2em)`` min-height and into xterm's ``rows`` / ``cols``.

Each class proves one half, so a refusal test cannot pass merely because everything is
refused:

* ``TestMalformedFallsBackToTheClassDefault`` -- the reproducer: a value that is not an
  integer (and an empty one) mounts, with the class default.
* ``TestClamped`` -- an integer outside the range is pulled to the nearest bound, in the
  mounted state AND in the rendered attributes the browser reads.
* ``TestValidPassesThrough`` -- an in-range value is used as written, including both
  bounds, so the clamp is not a constant.

Refs #3699, #3679, #1468.
"""

from __future__ import annotations

import re

import pytest
from django.test import RequestFactory

from djust.components.ttyd import TtydTerminalView
from djust.components.ttyd.terminal import MAX_COLS, MAX_ROWS

pytestmark = pytest.mark.pr_tier


class CustomDefaults(TtydTerminalView):
    rows = 40
    cols = 120


def _mount(view_cls, query: str) -> TtydTerminalView:
    request = RequestFactory().get("/shell/" + query)
    view = view_cls()
    view.request = request
    view.mount(request)
    return view


def _get(view_cls, query: str) -> str:
    """The HTML of a real GET through ``as_view()`` (what the browser is sent)."""
    from django.contrib.sessions.backends.cache import SessionStore

    request = RequestFactory().get("/shell/" + query)
    request.session = SessionStore()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    assert response.status_code == 200
    return response.content.decode()


def _attr(html: str, name: str) -> str:
    found = re.findall(r'data-%s="([^"]*)"' % name, html)
    assert len(found) == 1, (name, found)
    return found[0]


# Not integers, in the forms a link or a typo produces.
MALFORMED = ["x", "", "  ", "1.5", "1e3", "0x10", "--5", "²", "NaN", "1,000", "12px"]


class TestMalformedFallsBackToTheClassDefault:
    @pytest.mark.parametrize("bad", MALFORMED)
    def test_rows_that_is_not_an_integer_mounts_with_the_default(self, bad):
        assert _mount(TtydTerminalView, "?rows=" + bad).rows == TtydTerminalView.rows
        assert _mount(CustomDefaults, "?rows=" + bad).rows == 40

    @pytest.mark.parametrize("bad", MALFORMED)
    def test_cols_that_is_not_an_integer_mounts_with_the_default(self, bad):
        assert _mount(TtydTerminalView, "?cols=" + bad).cols == TtydTerminalView.cols
        assert _mount(CustomDefaults, "?cols=" + bad).cols == 120

    def test_a_bare_key_with_no_value_mounts_with_the_default(self):
        view = _mount(CustomDefaults, "?rows&cols")
        assert (view.rows, view.cols) == (40, 120)

    def test_a_digit_string_past_pythons_int_limit_mounts_with_the_default(self):
        """``int()`` refuses more than ~4300 digits with ``ValueError`` (3.11+)."""
        view = _mount(CustomDefaults, "?rows=%s&cols=%s" % ("9" * 5000, "9" * 5000))
        assert (view.rows, view.cols) == (40, 120)

    def test_one_bad_value_does_not_discard_the_other(self):
        view = _mount(CustomDefaults, "?rows=x&cols=100")
        assert (view.rows, view.cols) == (40, 100)
        view = _mount(CustomDefaults, "?rows=30&cols=")
        assert (view.rows, view.cols) == (30, 120)

    def test_the_real_get_is_a_200_not_a_500(self):
        html = _get(TtydTerminalView, "?rows=x&cols=")
        assert _attr(html, "rows") == str(TtydTerminalView.rows)
        assert _attr(html, "cols") == str(TtydTerminalView.cols)


class TestClamped:
    @pytest.mark.parametrize("value", ["0", "-1", "-24", "-1000000000"])
    def test_zero_and_negative_are_raised_to_one(self, value):
        view = _mount(TtydTerminalView, "?rows=%s&cols=%s" % (value, value))
        assert (view.rows, view.cols) == (1, 1)

    @pytest.mark.parametrize("value", ["501", "1000000000", "9" * 40])
    def test_oversized_rows_are_capped(self, value):
        assert _mount(TtydTerminalView, "?rows=" + value).rows == MAX_ROWS

    @pytest.mark.parametrize("value", ["1001", "1000000000", "9" * 40])
    def test_oversized_cols_are_capped(self, value):
        assert _mount(TtydTerminalView, "?cols=" + value).cols == MAX_COLS

    def test_the_bounds_are_the_documented_ones(self):
        assert (MAX_ROWS, MAX_COLS) == (500, 1000)

    def test_the_rendered_attributes_and_min_height_carry_the_clamped_value(self):
        """The sink the issue names: the CSS ``calc()`` and the data attributes xterm reads."""
        html = _get(TtydTerminalView, "?rows=1000000000&cols=1000000000")
        assert _attr(html, "rows") == str(MAX_ROWS)
        assert _attr(html, "cols") == str(MAX_COLS)
        assert "calc(%d * 1.2em)" % MAX_ROWS in html
        assert "1000000000" not in html

    def test_a_subclass_default_outside_the_range_is_not_clamped_away_silently(self):
        """The class attribute is the developer's own, trusted like ``ttyd_url``: only the
        query string is untrusted, so an absent query keeps the default as written."""

        class Tall(TtydTerminalView):
            rows = 700

        assert _mount(Tall, "").rows == 700


class TestValidPassesThrough:
    def test_in_range_values_are_used_as_written(self):
        view = _mount(TtydTerminalView, "?rows=30&cols=100")
        assert (view.rows, view.cols) == (30, 100)

    def test_both_bounds_pass_through_unchanged(self):
        view = _mount(TtydTerminalView, "?rows=1&cols=1")
        assert (view.rows, view.cols) == (1, 1)
        view = _mount(TtydTerminalView, "?rows=%d&cols=%d" % (MAX_ROWS, MAX_COLS))
        assert (view.rows, view.cols) == (MAX_ROWS, MAX_COLS)

    def test_surrounding_whitespace_and_a_plus_sign_are_what_int_always_accepted(self):
        view = _mount(TtydTerminalView, "?rows=%2030%20&cols=%2B90")
        assert (view.rows, view.cols) == (30, 90)

    def test_no_query_keeps_the_class_defaults(self):
        view = _mount(CustomDefaults, "")
        assert (view.rows, view.cols) == (40, 120)

    def test_the_real_get_renders_the_valid_value(self):
        html = _get(TtydTerminalView, "?rows=30&cols=100")
        assert _attr(html, "rows") == "30"
        assert _attr(html, "cols") == "100"
        assert "calc(30 * 1.2em)" in html
