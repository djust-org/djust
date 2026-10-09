"""Round-two regressions: cyclic planning, source liveness and portable addresses."""

import re
import time

import pytest
from django.contrib.sessions.backends.db import SessionStore

from djust._rust import RustLiveView, authored_lazy_elements, template_needs_provenance
from djust.tests.test_lazy_provenance_http_3252 import Child, Page, TAG, get_ids, post


class Hidden(Child):
    mounts = 0


HIDDEN = '<div dj-view="' + __name__ + '.Hidden" dj-lazy></div>'
REVIVALS = [
    ("<!-- {{ q|safe }} %s -->", "-->"),
    ("<script>var s=\"{{ q|safe }}\";var t='%s';</script>", "</script>"),
    ("<textarea>{{ q|safe }}%s</textarea>", "</textarea>"),
    ("<template>{{ q|safe }}%s</template>", "</template>"),
    ("<p title='{{ q|safe }}%s'></p>", "'>"),
    ("<style>{{ q|safe }}%s</style>", "</style>"),
    ("<title>{{ q|safe }}%s</title>", "</title>"),
    ("<noscript>{{ q|safe }}%s</noscript>", "</noscript>"),
]


@pytest.mark.parametrize("inert,payload", REVIVALS)
def test_source_inert_containers_never_carry_authority(inert, payload):
    rust = RustLiveView(TAG + inert % HIDDEN, [])
    rust.update_state({"q": payload})
    html, spans = rust.render_with_provenance()
    found = authored_lazy_elements(html, spans)
    assert len(found) == 1
    assert found[0][2].endswith(".Child")


@pytest.mark.django_db
@pytest.mark.parametrize("inert,payload", REVIVALS[:5])
def test_http_cannot_mount_revived_hidden_container(inert, payload):
    class RevivalPage(Page):
        template = "<div dj-root>" + TAG + inert % HIDDEN + "</div>"

        def mount(self, request, **kwargs):
            self.q = payload

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, RevivalPage)
    assert len(ids) == 1
    assert post(session, ids[0], "djust_lazy_mount", RevivalPage).status_code == 200
    assert post(session, "lazy_hidden_forged", "djust_lazy_mount", RevivalPage).status_code == 400
    assert Hidden.mounts == 0


@pytest.mark.parametrize("fan", [2, 3])
def test_recursive_tree_planning_and_render_are_bounded(tmp_path, fan):
    branches = ["l", "r", "m"][:fan]
    source = (
        "{% if n %}<i>{{ n.v }}</i>"
        + "".join('{% include "tree.html" with n=n.' + branch + " %}" for branch in branches)
        + "{% endif %}"
    )
    (tmp_path / "tree.html").write_text(source)
    page = '<div dj-root>{% include "tree.html" with n=tree %}</div>'
    rust = RustLiveView(page, [str(tmp_path)])
    rust.update_state({"tree": {"v": 1, "l": {"v": 2}, "r": None, "m": None}})
    start = time.perf_counter()
    assert not template_needs_provenance(page, [str(tmp_path)])
    assert time.perf_counter() - start < 1.0
    start = time.perf_counter()
    for _ in range(15):
        plain = rust.render()
    plain_time = time.perf_counter() - start
    start = time.perf_counter()
    for _ in range(15):
        html, spans, origins = rust.render_lazy_html()
        assert html == plain
        assert not spans and not origins
    tracked_time = time.perf_counter() - start
    assert tracked_time < max(0.5, plain_time * 20)


def test_mutual_cycle_and_dependency_edits_invalidate_plan(tmp_path):
    a = tmp_path / "a.html"
    b = tmp_path / "b.html"
    a.write_text('{% if n %}{% include "b.html" with n=n.child %}{% endif %}')
    b.write_text('{% if n %}{% include "a.html" with n=n.child %}{% endif %}')
    page = '{% include "a.html" with n=tree %}'
    dirs = [str(tmp_path)]
    assert not template_needs_provenance(page, dirs)
    b.write_text(b.read_text() + TAG)
    assert template_needs_provenance(page, dirs)
    b.write_text("plain")
    assert not template_needs_provenance(page, dirs)


def test_dynamic_include_bindings_do_not_share_wrong_memo(tmp_path):
    (tmp_path / "select.html").write_text("{% include target %}")
    (tmp_path / "plain.html").write_text("plain")
    (tmp_path / "lazy.html").write_text(TAG)
    source = '{% include "select.html" with target="plain.html" %}{% include "select.html" with target="lazy.html" %}'
    rust = RustLiveView(source, [str(tmp_path)])
    html, spans, _ = rust.render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.django_db
def test_addresses_stable_across_pods_and_unrelated_edits(tmp_path, settings):
    roots = [tmp_path / "pod-a", tmp_path / "pod-b"]
    for root in roots:
        root.mkdir()
        (root / "child.html").write_text("<p>intro</p>" + TAG + TAG)
        (root / "base.html").write_text(
            '<div dj-root>{% block body %}{% include "child.html" %}{% endblock %}</div>'
        )

    class PortablePage(Page):
        template = '{% extends "base.html" %}{% block body %}{{ block.super }}{% endblock %}'

    def select(root):
        settings.TEMPLATES = [
            {
                "BACKEND": "djust.template_backend.DjustTemplateBackend",
                "DIRS": [str(root)],
                "APP_DIRS": True,
            }
        ]

    session = SessionStore()
    session.create()
    select(roots[0])
    ids = get_ids(session, PortablePage)
    assert len(ids) == len(set(ids)) == 2
    select(roots[1])
    assert get_ids(session, PortablePage) == ids
    (roots[1] / "base.html").write_text(
        '<div dj-root><p>An unrelated parent edit</p>{% block body %}{% include "child.html" %}{% endblock %}</div>'
    )
    assert get_ids(session, PortablePage) == ids
    (roots[1] / "child.html").write_text("<p>A much longer unrelated introduction</p>" + TAG + TAG)
    assert get_ids(session, PortablePage) == ids
    (roots[1] / "child.html").write_text(
        "<p>intro</p>" + TAG.replace("dj-lazy", 'dj-lazy="click"') + TAG
    )
    new_ids = get_ids(session, PortablePage)
    assert new_ids[0] != ids[0] and new_ids[1] == ids[1]
    assert post(session, ids[0], "djust_lazy_mount", PortablePage).status_code == 400
    assert post(session, new_ids[0], "djust_lazy_mount", PortablePage).status_code == 200


@pytest.mark.django_db
def test_unregistered_lazy_markup_has_debug_diagnostic(caplog):
    class InertPage(Page):
        template = "<div dj-root><!--" + TAG + "--></div>"

    session = SessionStore()
    session.create()
    with caplog.at_level("DEBUG", logger="djust._lazy_containers"):
        assert get_ids(session, InertPage) == []
    assert "Lazy markup did not register" in caplog.text


def test_whitespace_after_valueless_lazy_fails_closed():
    rust = RustLiveView('<div{{w|safe}}dj-view="app.A"{{w|safe}}dj-lazy{{w|safe}}></div>', [])
    rust.update_state({"w": "\t\n "})
    html, spans = rust.render_with_provenance()
    assert not authored_lazy_elements(html, spans)
    assert re.search(r"dj-lazy\s+>", html)


@pytest.mark.parametrize(
    "prefix",
    [
        "{% comment %}<script>" + TAG + "{% endcomment %}",
        "{% comment %}" + TAG + "{% endcomment %}",
    ],
)
def test_discarded_comment_literals_do_not_corrupt_source_context(prefix):
    rust = RustLiveView(prefix + TAG, [])
    html, spans = rust.render_with_provenance()
    assert html == TAG
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["loop", "with", "context", "depth"])
def test_documented_nonregistrations_emit_debug_log(tmp_path, settings, caplog, kind):
    (tmp_path / "lazy.html").write_text(TAG)
    for index in range(24):
        (tmp_path / f"level{index}.html").write_text(
            TAG if index == 23 else '{% include "level' + str(index + 1) + '.html" %}'
        )
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    sources = {
        "loop": "{% for t in names %}{% include t %}{% endfor %}",
        "with": '{% with t="lazy.html" %}{% include t %}{% endwith %}',
        "context": "{% if no %}<!--{% endif %}" + TAG,
        "depth": '{% include "level0.html" %}',
    }

    class UnregisteredPage(Page):
        template = "<div dj-root>" + sources[kind] + "</div>"

        def mount(self, request, **kwargs):
            self.names = ["lazy.html"]
            self.no = False

    session = SessionStore()
    session.create()
    with caplog.at_level("DEBUG", logger="djust._lazy_containers"):
        assert get_ids(session, UnregisteredPage) == []
    assert "Lazy markup did not register" in caplog.text


def test_missing_include_and_ancestor_edits_invalidate_plans(tmp_path):
    dirs = [str(tmp_path)]
    source = "{% include chosen %}"
    rust = RustLiveView(source, dirs)
    rust.update_state({"chosen": ["missing.html", "plain.html"]})
    (tmp_path / "plain.html").write_text("plain")
    assert rust.render_lazy_html()[1] == []
    (tmp_path / "missing.html").write_text(TAG)
    html, spans, _ = rust.render_lazy_html()
    assert len(authored_lazy_elements(html, spans)) == 1
    (tmp_path / "parent.html").write_text("<main>{% block body %}plain{% endblock %}</main>")
    child = '{% extends "parent.html" %}'
    assert not template_needs_provenance(child, dirs)
    (tmp_path / "parent.html").write_text("<main>{% block body %}" + TAG + "{% endblock %}</main>")
    assert template_needs_provenance(child, dirs)


@pytest.mark.parametrize(
    "body",
    [
        "{{ block.super }}{{ block.super }}",
        "{% for n in ns %}{{ block.super }}{% endfor %}",
        '{% include "one.html" %}{% include "one.html" %}',
    ],
)
def test_repeat_composition_has_distinct_stable_container_addresses(tmp_path, body):
    (tmp_path / "one.html").write_text(TAG)
    (tmp_path / "base.html").write_text("{% block body %}" + TAG + "{% endblock %}")
    source = '{% extends "base.html" %}{% block body %}' + body + "{% endblock %}"
    rust = RustLiveView(source, [str(tmp_path)])
    rust.update_state({"ns": [1, 2]})
    html, spans, origins = rust.render_lazy_html()
    from djust._render_provenance import RenderedHTML

    tracked = RenderedHTML(html, tuple(spans), origins=tuple(origins))
    addresses = [
        tracked.authored_identity(a, b) for a, b, _, _ in authored_lazy_elements(html, spans)
    ]
    assert len(addresses) == len(set(addresses)) == 2
