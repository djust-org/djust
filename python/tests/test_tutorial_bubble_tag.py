"""Tests for the ``{% tutorial_bubble %}`` template tag.

ADR-002 Phase 1c. The client-side bubble behavior is tested in
``tests/js/tutorial-bubble.test.js``; this file only covers the
server-side rendering.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template


@pytest.fixture
def render():
    def _render(template_source: str, **ctx) -> str:
        return Template(template_source).render(Context(ctx))

    return _render


class TestTutorialBubbleTag:
    def test_default_bubble(self, render):
        out = render("{% load djust_tutorials %}{% tutorial_bubble %}")
        assert 'id="dj-tutorial-bubble"' in out
        assert 'class="dj-tutorial-bubble"' in out
        assert 'dj-update="ignore"' in out
        assert 'data-event="tour:narrate"' in out
        assert 'data-default-position="bottom"' in out
        assert 'data-visible="false"' in out
        assert 'role="status"' in out
        assert 'aria-live="polite"' in out

    def test_custom_css_class(self, render):
        out = render('{% load djust_tutorials %}{% tutorial_bubble css_class="my-bubble" %}')
        assert 'class="my-bubble"' in out
        # Other attrs still default
        assert 'data-event="tour:narrate"' in out

    def test_custom_event_name(self, render):
        out = render('{% load djust_tutorials %}{% tutorial_bubble event="my:narrate" %}')
        assert 'data-event="my:narrate"' in out

    def test_custom_position(self, render):
        out = render('{% load djust_tutorials %}{% tutorial_bubble position="top" %}')
        assert 'data-default-position="top"' in out

    def test_invalid_position_falls_back_to_bottom(self, render):
        """Invalid positions silently fall back to bottom rather than
        raising — template tags shouldn't crash the whole page render.
        """
        out = render('{% load djust_tutorials %}{% tutorial_bubble position="diagonal" %}')
        assert 'data-default-position="bottom"' in out

    def test_includes_skip_and_cancel_buttons(self, render):
        out = render("{% load djust_tutorials %}{% tutorial_bubble %}")
        assert 'dj-click="skip_tutorial"' in out
        assert 'dj-click="cancel_tutorial"' in out

    def test_includes_text_and_progress_elements(self, render):
        out = render("{% load djust_tutorials %}{% tutorial_bubble %}")
        assert 'class="dj-tutorial-bubble__text"' in out
        assert 'class="dj-tutorial-bubble__step"' in out
        assert 'class="dj-tutorial-bubble__progress"' in out

    def test_escapes_hostile_css_class(self, render):
        """A css_class kwarg from user-controlled data should be escaped."""
        hostile = '"><script>alert(1)</script>'
        out = render(
            "{% load djust_tutorials %}{% tutorial_bubble css_class=cls %}",
            cls=hostile,
        )
        assert "<script>" not in out
        assert "&lt;script&gt;" in out or "&lt;" in out

    def test_escapes_hostile_event_name(self, render):
        hostile = '"><img src=x onerror=alert(1)>'
        out = render(
            "{% load djust_tutorials %}{% tutorial_bubble event=evt %}",
            evt=hostile,
        )
        assert "<img" not in out
        assert "onerror" not in out or "&lt;img" in out
