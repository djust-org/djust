"""#4122: an element opened in one ``{% if %}`` branch and closed in another."""

from __future__ import annotations

import copy

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient
from djust.utils import clear_template_dirs_cache

ROOT = '<!DOCTYPE html><html><body><div dj-view="{{ view_path }}" dj-root>%s</div></body></html>'
BASES = {
    "if_else": ROOT
    % (
        '{% if app %}<div class="frame"><header>A</header>{% else %}<header>B</header>{% endif %}'
        "<main>{% block content %}{% endblock %}</main>"
        "{% if app %}<footer>A</footer></div>{% else %}<footer>B</footer>{% endif %}"
    ),
    "plain_if": ROOT
    % (
        '{% if app %}<div class="frame">{% endif %}<main>{% block content %}{% endblock %}</main>'
        "{% if app %}</div>{% endif %}"
    ),
    "balanced": ROOT
    % (
        '<div class="frame{% if app %} on{% endif %}"><main>{% block content %}{% endblock %}</main>'
        "</div>"
    ),
}


@pytest.fixture(autouse=True)
def templates(settings, tmp_path):
    for name, source in BASES.items():
        (tmp_path / f"base_{name}.html").write_text(source)
        (tmp_path / f"child_{name}.html").write_text(
            f'{{% extends "base_{name}.html" %}}{{% block content %}}<p>{{{{ title }}}}</p>{{% endblock %}}'
        )
    engines = copy.deepcopy(settings.TEMPLATES)
    engines[0]["DIRS"] = [str(tmp_path), *engines[0].get("DIRS", [])]
    settings.TEMPLATES = engines
    clear_template_dirs_cache()
    yield
    clear_template_dirs_cache()


@pytest.mark.parametrize("app", [True, False])
@pytest.mark.parametrize("base", ["if_else", "plain_if", "balanced"])
def test_render(base, app):
    class View(LiveView):
        exposure_policy = "legacy"
        template_name = f"child_{base}.html"

        def mount(self, request, **kwargs):
            self.app, self.title = app, "hello"

    client = LiveViewTestClient(View)
    client.mount()
    assert "hello" in client.render()


def test_the_root_close_is_not_taken_from_inside_an_if_branch():
    """The scan behind the extraction, directly: the wrapper's ``</div>`` sits
    in an ``{% if %}`` branch and must not be read as the root's close."""
    from djust.mixins.template import TemplateMixin

    source = BASES["if_else"]
    inner = source.index("dj-root>") + len("dj-root>")
    close = TemplateMixin._find_closing_tag_pos(source, inner, "div")
    assert close == (source.rindex("</div>"), source.rindex("</div>") + len("</div>"))


@pytest.mark.parametrize("app", [True, False])
def test_the_page_renders_each_branch_and_re_renders_after_an_event(app):
    from djust.decorators import event_handler

    class View(LiveView):
        exposure_policy = "legacy"
        template_name = "child_if_else.html"

        def mount(self, request, **kwargs):
            self.app, self.title = app, "hello"

        @event_handler()
        def retitle(self, **kwargs):
            self.title = "again"

    client = LiveViewTestClient(View)
    client.mount()
    html = client.render()
    if app:
        assert '<div class="frame"' in html and "<header>A</header>" in html
        assert "<footer>A</footer></div>" in html
    else:
        assert "frame" not in html and "<header>B</header>" in html and "<footer>B</footer>" in html
    client.send_event("retitle")
    assert "again" in client.render()
