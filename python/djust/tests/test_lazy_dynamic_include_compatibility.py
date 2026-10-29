"""Lazy-child registration remains scoped across dynamic Django includes."""

import re

import django
import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust.tests.test_lazy_provenance_http_3252 import Page, TAG, get_ids, post, request

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 partial roots"),
]


def configured_templates(tmp_path):
    return [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]


def root_with(body, partial):
    root = "<main dj-root>" + body + "</main>"
    if not partial:
        return root
    return "{% partialdef root %}" + root + "{% endpartialdef %}{% partial root %}"


def session():
    result = SessionStore()
    result.create()
    return result


@pytest.mark.parametrize("partial", [False, True], ids=["ordinary-root", "partial-root"])
def test_direct_lazy_survives_unrelated_dynamic_include(tmp_path, partial):
    (tmp_path / "plain.html").write_text("<p>plain</p>", encoding="utf-8")

    class LazyPage(Page):
        template = root_with(TAG + "{% include selected %}", partial)

        def mount(self, request, **kwargs):
            self.selected = "plain.html"

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        ids = get_ids(browser_session, LazyPage)
        assert len(ids) == 1
        mounted = post(browser_session, ids[0], "djust_lazy_mount", LazyPage)
        assert mounted.status_code == 200, mounted.content


@pytest.mark.parametrize("partial", [False, True], ids=["ordinary-root", "partial-root"])
def test_direct_dynamic_include_registers_lazy_from_rendered_selection(tmp_path, partial):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class LazyPage(Page):
        template = root_with("{% include selected %}", partial)

        def mount(self, request, **kwargs):
            self.selected = "lazy.html"

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        ids = get_ids(browser_session, LazyPage)
        assert len(ids) == 1
        mounted = post(browser_session, ids[0], "djust_lazy_mount", LazyPage)
        assert mounted.status_code == 200, mounted.content


def test_stateful_direct_selector_is_not_reevaluated_for_lazy_registration(tmp_path):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class Chooser:
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "lazy.html"

    chooser = Chooser()

    class LazyPage(Page):
        template = root_with("{% include chooser.selected %}", partial=True)

        def mount(self, request, **kwargs):
            self.chooser = chooser

        def get_context_data(self, **kwargs):
            return {"chooser": self.chooser}

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        ids = get_ids(browser_session, LazyPage)
    assert len(ids) == 1
    assert chooser.reads == 1


def test_nested_include_binding_preserves_registration(tmp_path):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")
    (tmp_path / "selector.html").write_text("{% include target %}", encoding="utf-8")

    class LazyPage(Page):
        template = root_with('{% include "selector.html" with target=selected only %}', True)

        def mount(self, request, **kwargs):
            self.selected = "lazy.html"

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        ids = get_ids(browser_session, LazyPage)
        assert len(ids) == 1
        mounted = post(browser_session, ids[0], "djust_lazy_mount", LazyPage)
        assert mounted.status_code == 200, mounted.content


@pytest.mark.parametrize(
    "include",
    [
        "{% for t in names %}{% include t %}{% endfor %}",
        '{% with t="lazy.html" %}{% include t %}{% endwith %}',
    ],
    ids=["loop-bound", "with-bound"],
)
def test_direct_lazy_registration_does_not_authorize_unplanned_dynamic_child(tmp_path, include):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class LazyPage(Page):
        template = root_with(TAG + include, partial=True)

        def mount(self, request, **kwargs):
            self.names = ["lazy.html"]

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        response = LazyPage.as_view()(request("get", browser_session))
        html = response.content.decode()
        assert html.count(" dj-lazy") == 2
        ids = re.findall(r'data-djust-lazy-id="([^"]+)"', html)
    assert len(ids) == 1


def test_direct_lazy_registration_does_not_authorize_overdeep_included_child(tmp_path):
    for index in range(24):
        content = TAG if index == 23 else '{% include "level' + str(index + 1) + '.html" %}'
        (tmp_path / f"level{index}.html").write_text(content, encoding="utf-8")

    class LazyPage(Page):
        template = root_with(TAG + '{% include "level0.html" %}', partial=True)

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        response = LazyPage.as_view()(request("get", browser_session))
        html = response.content.decode()
        assert html.count(" dj-lazy") == 2
        ids = re.findall(r'data-djust-lazy-id="([^"]+)"', html)
    assert len(ids) == 1


@pytest.mark.parametrize(
    "include,expected",
    [
        ('{% with unrelated="x" %}{% include selected %}{% endwith %}', 1),
        ("{% for item in items %}{% include selected %}{% endfor %}", 2),
    ],
    ids=["unrelated-with", "unrelated-loop"],
)
def test_dynamic_global_selector_remains_available_inside_unrelated_scope(
    tmp_path, include, expected
):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class LazyPage(Page):
        template = root_with(include, partial=True)

        def mount(self, request, **kwargs):
            self.selected = "lazy.html"
            self.items = [1, 2]

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        ids = get_ids(browser_session, LazyPage)
    assert len(ids) == expected
    assert len(set(ids)) == expected


@pytest.mark.parametrize(
    "include",
    [
        '{% for item in items %}{% include "selector.html" with target=item only %}{% endfor %}',
        '{% with item="lazy.html" %}{% include "selector.html" with target=item only %}{% endwith %}',
    ],
    ids=["nested-loop-binding", "nested-with-binding"],
)
def test_scoped_dynamic_bindings_do_not_authorize_nested_children(tmp_path, include):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")
    (tmp_path / "selector.html").write_text("{% include target %}", encoding="utf-8")

    class LazyPage(Page):
        template = root_with(TAG + include, partial=True)

        def mount(self, request, **kwargs):
            self.items = ["lazy.html"]

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        response = LazyPage.as_view()(request("get", browser_session))
        html = response.content.decode()
        assert html.count(" dj-lazy") == 2
        ids = re.findall(r'data-djust-lazy-id="([^"]+)"', html)
    assert len(ids) == 1


def test_firstof_shadowing_in_loop_does_not_authorize_dynamic_child(tmp_path):
    (tmp_path / "plain.html").write_text("<p>plain</p>", encoding="utf-8")
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class LazyPage(Page):
        template = root_with(
            TAG
            + "{% for item in items %}"
            + "{% firstof item as selected %}{% include selected %}"
            + "{% endfor %}",
            partial=True,
        )

        def mount(self, request, **kwargs):
            self.selected = "plain.html"
            self.items = ["lazy.html"]

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        response = LazyPage.as_view()(request("get", browser_session))
        html = response.content.decode()
        assert html.count(" dj-lazy") == 2
        ids = re.findall(r'data-djust-lazy-id="([^"]+)"', html)
    assert len(ids) == 1


def test_block_super_cannot_authorize_with_scoped_dynamic_child(tmp_path):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")
    (tmp_path / "base.html").write_text(
        "<main dj-root>" + TAG + "{% block content %}{% include t %}{% endblock %}</main>",
        encoding="utf-8",
    )

    class LazyPage(Page):
        template = (
            '{% extends "base.html" %}{% block content %}'
            '{% with t="lazy.html" %}{{ block.super }}{% endwith %}{% endblock %}'
        )

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        browser_session = session()
        response = LazyPage.as_view()(request("get", browser_session))
        html = response.content.decode()
        assert html.count(" dj-lazy") == 2
        ids = re.findall(r'data-djust-lazy-id="([^"]+)"', html)
    assert len(ids) == 1


@pytest.mark.parametrize(
    "effect",
    [
        "{{ trigger.fire }}",
        "{% if trigger.fire %}effect{% endif %}",
        '{% include "effect.html" with trigger=trigger only %}',
    ],
    ids=["variable", "condition", "include-only"],
)
def test_live_python_effect_before_dynamic_selector_does_not_register(tmp_path, effect):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")
    (tmp_path / "effect.html").write_text("{{ trigger.fire }}", encoding="utf-8")

    class Chooser:
        target = "plain.html"
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return self.target

    class Trigger:
        reads = 0

        @property
        def fire(self):
            self.reads += 1
            chooser.target = "lazy.html"
            return True

    chooser = Chooser()
    trigger = Trigger()

    class LazyPage(Page):
        template = root_with(effect + "{% include chooser.selected %}", partial=True)

        def get_context_data(self, **kwargs):
            return {"chooser": chooser, "trigger": trigger}

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        response = LazyPage.as_view()(request("get", session()))
        html = response.content.decode()
    assert "dj-lazy" in html
    assert 'data-djust-lazy-id="' not in html
    assert chooser.reads == 1
    assert trigger.reads == 1


def test_pure_builtin_filter_before_dynamic_selector_remains_available(tmp_path):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class Chooser:
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "lazy.html"

    chooser = Chooser()

    class LazyPage(Page):
        template = root_with("{{ label|upper }}{% include chooser.selected %}", partial=True)

        def get_context_data(self, **kwargs):
            return {"chooser": chooser, "label": "safe"}

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        ids = get_ids(session(), LazyPage)
    assert len(ids) == 1
    assert chooser.reads == 1


def test_custom_filter_effect_before_dynamic_selector_does_not_register(tmp_path):
    from djust._rust import register_custom_filter, unregister_custom_filter

    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class Chooser:
        target = "plain.html"
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return self.target

    chooser = Chooser()
    filter_calls = []

    def change_choice(value):
        filter_calls.append(value)
        chooser.target = "lazy.html"
        return value

    class LazyPage(Page):
        template = root_with("{{ label|test_lazy_effect }}{% include chooser.selected %}", True)

        def get_context_data(self, **kwargs):
            return {"chooser": chooser, "label": "effect"}

    register_custom_filter("test_lazy_effect", change_choice, False, False)
    try:
        with override_settings(TEMPLATES=configured_templates(tmp_path)):
            response = LazyPage.as_view()(request("get", session()))
            html = response.content.decode()
        assert "dj-lazy" in html
        assert 'data-djust-lazy-id="' not in html
        assert chooser.reads == 1
        assert filter_calls == ["effect"]
    finally:
        unregister_custom_filter("test_lazy_effect")


def test_python_effect_before_plain_string_selector_still_registers(tmp_path):
    (tmp_path / "lazy.html").write_text(TAG, encoding="utf-8")

    class Trigger:
        reads = 0

        @property
        def fire(self):
            self.reads += 1
            return "effect"

    trigger = Trigger()

    class LazyPage(Page):
        template = root_with("{{ trigger.fire }}{% include selected %}", True)

        def get_context_data(self, **kwargs):
            return {"trigger": trigger, "selected": "lazy.html"}

    with override_settings(TEMPLATES=configured_templates(tmp_path)):
        ids = get_ids(session(), LazyPage)
    assert len(ids) == 1
    assert trigger.reads == 1
