"""``{% badge %}``'s screen-reader text can be named or dropped with ``sr_label`` (#4359)."""

import pytest
from django.template import engines

from djust.components.rust_handlers import BadgeHandler


def _django(tag):
    return engines["django"].from_string("{% load djust_components %}" + tag).render({})


@pytest.mark.parametrize("render", ["django", "handler"])
class TestBadgeSrLabel:
    def _render(self, render, **kw):
        if render == "handler":
            return str(BadgeHandler().render_kwargs(kw, {}))
        args = " ".join('%s="%s"' % (k, v) for k, v in kw.items())
        return _django("{% badge " + args + " %}")

    def test_default_text_is_unchanged(self, render):
        html = self._render(render, label="High", status="error")
        assert '<span class="sr-only">Status: error</span>' in html

    def test_sr_label_names_the_text(self, render):
        html = self._render(render, label="High", status="error", sr_label="Priority")
        assert '<span class="sr-only">Priority</span><span class="dj-badge__label">High' in html
        assert "Status:" not in html

    def test_empty_sr_label_drops_the_text(self, render):
        html = self._render(render, label="Completed", status="online", sr_label="")
        assert "sr-only" not in html
        assert '<span class="dj-badge__label">Completed</span>' in html

    def test_sr_label_is_escaped(self, render):
        html = self._render(render, label="x", status="info", sr_label="<b>")
        assert '<span class="sr-only">&lt;b&gt;</span>' in html


def test_false_sr_label_drops_the_text():
    html = str(BadgeHandler().render_kwargs({"label": "x", "status": "info", "sr_label": False}, {}))
    assert "sr-only" not in html
