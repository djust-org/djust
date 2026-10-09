"""Rendered-branch liveness and defining-template include identities (#3428)."""

import json
import re

import pytest
from django.contrib.sessions.backends.db import SessionStore

from djust._rust import RustLiveView, authored_lazy_elements
from djust.tests.test_lazy_provenance_http_3252 import Child, Page, TAG, get_ids, post


class Hidden(Child):
    mounts = 0


HIDDEN = '<div dj-view="' + __name__ + '.Hidden" dj-lazy></div>'
CONTROL_FLOW = [
    ("<script>{{ q|safe }}{% if a %}</script>{% endif %}%s</script>", "</script>"),
    ("<!-- {{ q|safe }}{% if a %}-->{% endif %}%s -->", "-->"),
    ("<textarea>{{ q|safe }}{% if a %}</textarea>{% endif %}%s</textarea>", "</textarea>"),
    ("<p title='{{ q|safe }}{% if a %}'>{% endif %}%s'></p>", "'>"),
    ("<!-- {{ q|safe }}{% for x in xs %}-->{% endfor %}%s -->", "-->"),
    (
        "<!-- {{ q|safe }}{% for x in xs %}x{% empty %}{% if a %}-->{% endif %}{% endfor %}%s -->",
        "-->",
    ),
]


@pytest.mark.parametrize("source,payload", CONTROL_FLOW)
@pytest.mark.parametrize("payload_enabled", [True, False])
def test_rendered_branch_cannot_revive_hidden(source, payload, payload_enabled):
    rust = RustLiveView(TAG + source.replace("%s", HIDDEN), [])
    rust.update_state({"q": payload if payload_enabled else "x", "a": False, "xs": []})
    html, spans = rust.render_with_provenance()
    found = authored_lazy_elements(html, spans)
    assert len(found) == 1
    assert found[0][2].endswith(".Child")


@pytest.mark.django_db
@pytest.mark.parametrize("source,payload", CONTROL_FLOW)
def test_http_rendered_branch_refuses_hidden_mount(source, payload):
    class RevivalPage(Page):
        template = "<div dj-root>" + TAG + source.replace("%s", HIDDEN) + "</div>"

        def mount(self, request, **kwargs):
            self.q = payload
            self.a = False
            self.xs = []

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, RevivalPage)
    for lazy_id in ids:
        assert post(session, lazy_id, "djust_lazy_mount", RevivalPage).status_code == 200
    assert len(ids) == 1
    assert Hidden.mounts == 0


@pytest.mark.django_db
@pytest.mark.parametrize("loop", [False, True])
def test_inline_child_and_base_include_sites_have_isolated_state(tmp_path, settings, loop):
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    include = "{% include 'cx.html' %}"
    if loop:
        include = "{% for i in xs %}" + include + "{% endfor %}"
    (tmp_path / "cx.html").write_text(TAG)
    (tmp_path / "base.html").write_text(
        "<div dj-root>" + include + "{% block body %}{% endblock %}</div>"
    )

    class InlinePage(Page):
        template = "{% extends 'base.html' %}{% block body %}" + include + "{% endblock %}"

        def mount(self, request, **kwargs):
            self.xs = [1, 2]

    session = SessionStore()
    session.create()
    ids = get_ids(session, InlinePage)
    assert len(ids) == len(set(ids)) == (4 if loop else 2)
    for lazy_id in ids:
        assert post(session, lazy_id, "djust_lazy_mount", InlinePage).status_code == 200
    response = post(session, ids[0], "inc", InlinePage)
    assert response.status_code == 200
    html = json.loads(response.content)["html"]
    assert re.search(r'class="count"[^>]*>1</span>', html)
    for lazy_id in ids[1:]:
        response = post(session, lazy_id, "inc", InlinePage)
        assert response.status_code == 200
        assert re.search(r'class="count"[^>]*>1</span>', json.loads(response.content)["html"])
    response = post(session, ids[0], "inc", InlinePage)
    assert re.search(r'class="count"[^>]*>2</span>', json.loads(response.content)["html"])
    assert get_ids(session, InlinePage) == ids


@pytest.mark.parametrize("taken", [False, True])
def test_legitimate_branch_closer_controls_authority(taken):
    source = TAG + "<script>{% if a %}</script>{% endif %}" + HIDDEN + "</script>"
    rust = RustLiveView(source, [])
    rust.update_state({"a": taken})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == (2 if taken else 1)


@pytest.mark.parametrize("composition", ["include", "super"])
def test_branch_liveness_crosses_include_and_super_boundaries(tmp_path, composition):
    (tmp_path / "closer.html").write_text("{% if a %}</script>{% endif %}")
    if composition == "include":
        source = TAG + '<script>{{ q|safe }}{% include "closer.html" %}' + HIDDEN + "</script>"
    else:
        (tmp_path / "base.html").write_text(
            TAG + "<script>{% block body %}{% if a %}</script>{% endif %}{% endblock %}</script>"
        )
        source = (
            '{% extends "base.html" %}{% block body %}{{ q|safe }}{{ block.super }}'
            + HIDDEN
            + "{% endblock %}"
        )
    rust = RustLiveView(source, [str(tmp_path)])
    rust.update_state({"q": "</script>", "a": False})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize("only", [False, True])
def test_nested_selector_bindings_invalidate_plan_without_sharing_wrong_memo(tmp_path, only):
    (tmp_path / "select.html").write_text("{% include target %}")
    (tmp_path / "middle.html").write_text('{% include "select.html" with target=choice %}')
    (tmp_path / "plain.html").write_text("plain")
    (tmp_path / "lazy.html").write_text(TAG)
    scoped = " only" if only else ""
    source = '{% include "middle.html" with choice=chosen' + scoped + " %}"
    rust = RustLiveView(source, [str(tmp_path)])
    for chosen, expected in [("plain.html", 0), ("lazy.html", 1), ("plain.html", 0)]:
        rust.update_state({"chosen": chosen})
        html, spans, _ = rust.render_lazy_html()
        assert len(authored_lazy_elements(html, spans)) == expected
    source = (
        '{% include "middle.html" with choice="plain.html"' + scoped + " %}"
        '{% include "middle.html" with choice="lazy.html"' + scoped + " %}"
    )
    html, spans, _ = RustLiveView(source, [str(tmp_path)]).render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize("selector", ["choices.0", "cfg.target|default:fallback"])
def test_selector_paths_and_filter_arguments_invalidate(tmp_path, selector):
    (tmp_path / "plain.html").write_text("plain")
    (tmp_path / "lazy.html").write_text(TAG)
    rust = RustLiveView("{% include " + selector + " %}", [str(tmp_path)])
    for target, expected in [("plain.html", 0), ("lazy.html", 1), ("plain.html", 0)]:
        rust.update_state({"choices": [target], "cfg": {"target": ""}, "fallback": target})
        html, spans, _ = rust.render_lazy_html()
        assert len(authored_lazy_elements(html, spans)) == expected
