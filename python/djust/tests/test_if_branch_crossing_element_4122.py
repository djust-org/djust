"""#4122: an element opened in one ``{% if %}`` branch and closed in another."""

from __future__ import annotations

import copy

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient
from djust.utils import clear_template_dirs_cache

# Bare ``dj-root``: djust stamps ``dj-view`` itself (#2631). A template-variable
# ``dj-view="{{ ... }}"`` renders empty on Django < 6.0 and is refused as an
# unauthored root on Django >= 6.0, where any ``{% extends %}`` page renders
# through the authenticated-root path (lazy_provenance.rs, T013).
ROOT = "<!DOCTYPE html><html><body><div dj-root>%s</div></body></html>"
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
    # refused: the root closes in every branch (see the refusal test below)
    "both": "<!DOCTYPE html><html><body><div dj-root>"
    "{% if app %}A{% block content %}{% endblock %}</div>{% else %}B</div>{% endif %}"
    "</body></html>",
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


# --- the scan's rule, case by case (#4122 review) -----------------------------

_ROOT_CASES = {
    # name: (template, the text the root ends with)
    "verbatim_if_is_text": (
        "<div dj-root>{% verbatim %}{% if %}{% endverbatim %}A</div>TAIL",
        "A</div>",
    ),
    "verbatim_else_is_text": (
        "<div dj-root>{% verbatim %}{% if x %}{% endverbatim %}<div>A"
        "{% verbatim %}{% else %}{% endverbatim %}</div>R</div>TAIL",
        "R</div>",
    ),
    "comment_if_is_nothing": (
        "<div dj-root>{% comment %}{% if a %}{% endcomment %}A</div>TAIL",
        "A</div>",
    ),
    "hash_comment_if": ("<div dj-root>{# {% if a %} #}A</div>TAIL", "A</div>"),
    # Closes only in one branch: as on main, that close ends the root.
    "if_ends_after_the_root": ("<div dj-root>{% if a %}A</div>TAIL{% endif %}", "A</div>"),
    "elif": (
        '<div dj-root>{% if a %}<div class="x">{% elif b %}<div class="y">{% else %}<div>'
        "{% endif %}I</div></div>TAIL",
        "I</div></div>",
    ),
    "nested_ifs": (
        "<div dj-root>{% if a %}{% if b %}<div>B</div>{% endif %}<div>A</div>{% endif %}R</div>TAIL",
        "R</div>",
    ),
    "two_wrappers": (
        '<div dj-root>{% if a %}<div class="p">{% else %}<div class="q">{% endif %}I'
        "{% if a %}</div>{% else %}</div>{% endif %}R</div>TAIL",
        "R</div>",
    ),
    "for_if": (
        "<div dj-root>{% for x in xs %}{% if x %}<div>{{ x }}</div>{% endif %}{% endfor %}R</div>TAIL",
        "R</div>",
    ),
    "close_in_html_comment": ("<div dj-root><!-- </div> -->R</div>TAIL", "R</div>"),
    "close_in_js_string": ("<div dj-root><script>var s = '</div>';</script>R</div>TAIL", "R</div>"),
    "the_issue": (BASES["if_else"], "{% endif %}</div>"),
}


@pytest.mark.parametrize("name", sorted(_ROOT_CASES))
def test_root_extraction(name):
    source, ending = _ROOT_CASES[name]
    view = LiveView()
    root = view._extract_liveview_root_with_wrapper(source)
    assert root.startswith(source[source.index("<div dj-")]), root
    assert root.endswith(ending), root
    assert "TAIL" not in root


def test_a_root_closed_in_every_branch_is_refused_with_a_message():
    """``{% if a %}A</div>{% else %}B</div>{% endif %}``: the root element ends
    in both branches, so the only correct root is the whole ``if`` block, which
    no single close tag delimits. On main the else branch was cut off; now the
    extraction refuses it and says what to change."""
    from django.template import TemplateSyntaxError

    view = LiveView()
    with pytest.raises(TemplateSyntaxError, match=r"closed inside every branch of an \{% if %\}"):
        view._extract_liveview_root_with_wrapper(
            "<div dj-root>{% if a %}A</div>{% else %}B</div>{% endif %}"
        )


def test_the_refusal_reaches_the_developer_on_a_real_mount():
    from django.template import TemplateSyntaxError

    class View(LiveView):
        exposure_policy = "legacy"
        template_name = "child_both.html"

        def mount(self, request, **kwargs):
            self.app, self.title = True, "hello"

    client = LiveViewTestClient(View)
    with pytest.raises(TemplateSyntaxError, match="every branch"):
        client.mount()
        client.render()
