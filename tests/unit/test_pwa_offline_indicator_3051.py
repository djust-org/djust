"""#3051: the offline indicator and the offline banner can actually show.

``{% djust_offline_indicator show_when="offline" %}`` emitted an inline
``style="display: none;"`` that nothing removed, and
``djust/pwa/offline_banner.html`` did the same with ``dj-offline="show"``,
which no shipped client code reads. Both now rely on the ``dj-offline-show``
CSS keyed on the ``body.djust-offline`` class the client sets (#3041), and
that CSS hides them until the body is marked offline, so there is no flash
before the client runs. The client swaps the indicator's text and class
(``tests/js/offline-indicator-3051.test.js``).
"""

import re

import pytest
from django.template import Context, Template
from django.template.loader import render_to_string


def _render(src):
    return Template("{% load djust_pwa %}" + src).render(Context({}))


def _indicator_div(html):
    m = re.search(r'<div class="djust-offline-indicator[^>]*>', html)
    assert m, html
    return m.group(0)


HIDE_UNTIL_OFFLINE = "body:not(.djust-offline) [dj-offline-show]"


class TestIndicatorMarkup:
    def test_offline_indicator_has_no_inline_display_none(self):
        div = _indicator_div(_render('{% djust_offline_indicator show_when="offline" %}'))
        assert "dj-offline-show" in div
        assert "style=" not in div

    def test_offline_indicator_css_hides_it_until_the_body_is_offline(self):
        out = _render('{% djust_offline_indicator show_when="offline" %}')
        assert re.search(
            r"body:not\(\.djust-offline\) \.djust-offline-indicator\[dj-offline-show\] \{\s*"
            r"display: none !important;",
            out,
        )

    @pytest.mark.parametrize("show_when", ["online", "always"])
    def test_other_modes_do_not_emit_the_offline_only_rule(self, show_when):
        out = _render('{% djust_offline_indicator show_when="' + show_when + '" %}')
        assert "[dj-offline-show]" not in out

    @pytest.mark.parametrize(
        "show_when, attr, text, cls",
        [
            ("offline", "dj-offline-show", "Down", "is-down"),
            ("online", "dj-offline-hide", "Up", "is-up"),
            ("always", None, "Up", "is-up"),
        ],
    )
    def test_first_paint_matches_the_rendered_state(self, show_when, attr, text, cls):
        out = _render(
            '{% djust_offline_indicator online_text="Up" offline_text="Down" '
            'online_class="is-up" offline_class="is-down" show_when="' + show_when + '" %}'
        )
        div = _indicator_div(out)
        assert f'class="djust-offline-indicator {cls}"' in div
        assert f'<span class="djust-indicator-text">{text}</span>' in out
        for other in ("dj-offline-show", "dj-offline-hide"):
            assert (other in div) == (other == attr)
        # The data attributes the client reads are unchanged.
        assert 'data-online-text="Up"' in div
        assert 'data-offline-text="Down"' in div
        assert 'data-online-class="is-up"' in div
        assert 'data-offline-class="is-down"' in div

    def test_empty_status_class_adds_nothing(self):
        div = _indicator_div(_render('{% djust_offline_indicator offline_class="" %}'))
        assert 'class="djust-offline-indicator"' in div

    def test_status_class_is_escaped(self):
        div = _indicator_div(_render("{% djust_offline_indicator offline_class='a\"b' %}"))
        assert 'class="djust-offline-indicator a&quot;b"' in div


class TestDirectiveCss:
    def test_offline_styles_hide_dj_offline_show_until_offline(self):
        assert HIDE_UNTIL_OFFLINE in _render("{% djust_offline_styles %}")

    def test_pwa_head_hides_dj_offline_show_until_offline(self):
        assert HIDE_UNTIL_OFFLINE in _render("{% djust_pwa_head %}")


class TestOfflineBanner:
    def test_banner_uses_dj_offline_show_without_inline_style(self):
        out = render_to_string("djust/pwa/offline_banner.html", {"message": "No network"})
        m = re.search(r'<div class="djust-offline-banner"[^>]*>', out)
        assert m, out
        div = m.group(0)
        assert "dj-offline-show" in div
        assert 'dj-offline="show"' in div
        assert "style=" not in div
        assert "<p>No network</p>" in out
