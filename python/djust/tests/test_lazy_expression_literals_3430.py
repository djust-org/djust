"""Expression literals affect liveness without granting container authority (#3430)."""

import pytest
from django.contrib.sessions.backends.db import SessionStore

from djust._rust import RustLiveView, authored_lazy_elements
from djust.tests.test_lazy_provenance_http_3252 import Child, Page, TAG, get_ids, post


class Hidden(Child):
    mounts = 0


HIDDEN = '<div dj-view="' + __name__ + '.Hidden" dj-lazy></div>'
OPENERS = [
    '{{ "<!--" }}',
    '{{ "<!--"|safe }}',
    '{{ "<!--"|lower }}',
    '{% firstof missing "<!--" %}',
    '{% firstof "<!--" as o %}{{ o }}',
    '{% cycle "<!--" "y" %}',
    '{% cycle "<!--" "y" as o silent %}{{ o }}',
    '{% with o="<!--" %}{{ o }}{% endwith %}',
    '{% with o="<!--" %}{{ o|safe }}{% endwith %}',
    '{% with o="<!--" %}{% with p=o %}{{ p }}{% endwith %}{% endwith %}',
]


@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize("q", ["-->", "x"])
def test_expression_opener_cannot_be_closed_by_value(opener, q):
    rust = RustLiveView(TAG + opener + "{{ q|safe }}" + HIDDEN + "-->", [])
    rust.update_state({"q": q})
    html, spans = rust.render_with_provenance()
    assert [e[2] for e in authored_lazy_elements(html, spans)] == [
        "djust.tests.test_lazy_provenance_http_3252.Child"
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize("q", ["-->", "x"])
def test_http_expression_opener_never_mounts_hidden_child(opener, q):
    class ExpressionPage(Page):
        template = "<div dj-root>" + TAG + opener + "{{ q|safe }}" + HIDDEN + "--></div>"

        def mount(self, request, **kwargs):
            self.q = q

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, ExpressionPage)
    assert len(ids) == 1
    assert post(session, ids[0], "djust_lazy_mount", ExpressionPage).status_code == 200
    assert (
        post(session, "lazy_hidden_forged", "djust_lazy_mount", ExpressionPage).status_code == 400
    )
    assert Hidden.mounts == 0


@pytest.mark.parametrize(
    "closer",
    [
        '{{ "-->" }}',
        '{{ "-->"|safe }}',
        '{% firstof missing "-->" %}',
        '{% cycle "-->" "x" %}',
        '{% with o="-->" %}{{ o }}{% endwith %}',
    ],
)
def test_literal_closer_restores_live_context(closer):
    rust = RustLiveView(TAG + '{{ "<!--" }}hidden' + closer + TAG, [])
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


@pytest.mark.parametrize(
    "value,expected", [("<", 2), ("<!--", 1), ("<script>", 1), ('<p title="', 1), ("<template>", 1)]
)
def test_value_output_is_blank_for_liveness_but_final_survival_required(value, expected):
    # docs/lazy-http-provenance.md: values do not break following authority.
    # E still rejects actual final-page comments, attributes and inert content.
    rust = RustLiveView(TAG + "{{ q|safe }}" + TAG, [])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == expected


@pytest.mark.parametrize(
    "value", ["hello", "é Δ", "-", "&lt;", "42", "", ">", "--", '"', "'", "</script>"]
)
def test_plain_values_preserve_following_authority(value):
    rust = RustLiveView(TAG + "{{ q }}" + TAG, [])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


@pytest.mark.parametrize("filter_suffix", ["", "|safe", "|lower"])
def test_block_super_literal_context(tmp_path, filter_suffix):
    (tmp_path / "base.html").write_text("{% block body %}<!--{% endblock %}")
    source = (
        '{% extends "base.html" %}{% block body %}'
        + TAG
        + "{{ block.super"
        + filter_suffix
        + " }}{{ q|safe }}"
        + HIDDEN
        + "-->{% endblock %}"
    )
    rust = RustLiveView(source, [str(tmp_path)])
    rust.update_state({"q": "-->"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value,expected", [("hello é", 2), ("--", 2), ('"', 2), ("<b></b>", 2), ("<!--", 1)]
)
def test_http_value_output_and_final_survival(value, expected):
    class ValuePage(Page):
        template = "<div dj-root>" + TAG + "{{ q|safe }}" + HIDDEN + "</div>"

        def mount(self, request, **kwargs):
            self.q = value

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, ValuePage)
    assert len(ids) == expected
    for view_id in ids:
        assert post(session, view_id, "djust_lazy_mount", ValuePage).status_code == 200
    assert Hidden.mounts == (1 if expected == 2 else 0)


@pytest.mark.django_db
@pytest.mark.parametrize("filter_suffix", ["", "|safe", "|lower"])
def test_http_filtered_super_cannot_mount_hidden_child(tmp_path, settings, filter_suffix):
    (tmp_path / "base.html").write_text("{% block body %}<!--{% endblock %}")
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]

    (tmp_path / "inherited.html").write_text(
        '{% extends "base.html" %}{% block body %}'
        + "{{ block.super"
        + filter_suffix
        + " }}{{ q|safe }}"
        + HIDDEN
        + "-->{% endblock %}"
    )

    class SuperPage(Page):
        template = "<div dj-root>" + TAG + '{% include "inherited.html" %}</div>'

        def mount(self, request, **kwargs):
            self.q = "-->"

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, SuperPage)
    assert len(ids) == 1
    assert post(session, ids[0], "djust_lazy_mount", SuperPage).status_code == 200
    assert Hidden.mounts == 0


@pytest.mark.parametrize(
    "opener,closer",
    [
        ('{{ "<script>" }}', "</script>"),
        ('{{ "<textarea>"|safe }}', "</textarea>"),
        ('{% firstof "<template>" %}', "</template>"),
        ('{% cycle "<style>" "x" %}', "</style>"),
        ('{% with o="<title>" %}{{ o }}{% endwith %}', "</title>"),
    ],
)
def test_expression_raw_text_and_inert_openers(opener, closer):
    rust = RustLiveView(TAG + opener + "{{ q|safe }}" + HIDDEN + closer, [])
    rust.update_state({"q": closer})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize(
    "expression",
    [
        '{{ "-->"|safe }}',
        '{% with o="-->" %}{{ o|safe }}{% endwith %}',
        '{% cycle "-->" "x" as o %}',
        '{% firstof missing "-->" as o %}{{ o }}',
    ],
)
def test_literal_closer_restores_authored_container_liveness(expression):
    # The selected literal expression closes the comment in both parsed trees.
    rust = RustLiveView("<!--" + expression + TAG + "-->", [])
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_literal_generated_markup_never_grants_authority():
    rust = RustLiveView("{{ markup|safe }}{{ \"<div dj-view='app.A' dj-lazy></div>\" }}" + TAG, [])
    rust.update_state({"markup": ""})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_literal_binding_shadowed_by_value_cannot_supply_closer():
    source = (
        TAG
        + '{{ "<!--" }}{% with o="x" %}'
        + "{% with o=q %}{{ o|safe }}{% endwith %}{% endwith %}"
        + HIDDEN
        + "-->"
    )
    rust = RustLiveView(source, [])
    rust.update_state({"q": "-->"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_final_survival_rejects_value_hidden_include_and_loop(tmp_path):
    (tmp_path / "gap.html").write_text("{{ q|safe }}")
    source = TAG + '{% for x in xs %}{% include "gap.html" %}' + TAG + "{% endfor %}" + TAG
    rust = RustLiveView(source, [str(tmp_path)])
    rust.update_state({"q": "<!--", "xs": [1, 2]})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize("value,expected", [("<!--", 1), ("x", 2)])
def test_filter_with_value_argument_is_opaque(value, expected):
    rust = RustLiveView(TAG + '{{ ""|default:q|safe }}' + TAG, [])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == expected


@pytest.mark.parametrize("suffix", ["", "|lower", "|safe"])
def test_block_super_literal_closer_preserves_following_authority(tmp_path, suffix):
    (tmp_path / "base.html").write_text("{% block body %}-->{% endblock %}")
    source = (
        '{% extends "base.html" %}{% block body %}{{ "<!--" }}'
        + "{{ block.super"
        + suffix
        + " }}"
        + TAG
        + "{% endblock %}"
    )
    rust = RustLiveView(source, [str(tmp_path)])
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize("only", ["", " only"])
def test_include_literal_binding_can_supply_closer(tmp_path, only):
    (tmp_path / "close.html").write_text("{{ o|safe }}")
    source = TAG + '{{ "<!--" }}{% include "close.html" with o="-->"' + only + " %}" + TAG
    rust = RustLiveView(source, [str(tmp_path)])
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


def test_split_nonliteral_dash_runs_preserve_data_context():
    rust = RustLiveView(TAG + "{{ a|safe }}{{ b|safe }}" + TAG, [])
    rust.update_state({"a": "-", "b": "-"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize("q", ["-->", "x"])
def test_standalone_literal_revival_registers_zero(opener, q):
    rust = RustLiveView(opener + "{{ q|safe }}" + HIDDEN + "-->", [])
    rust.update_state({"q": q})
    html, spans = rust.render_with_provenance()
    assert authored_lazy_elements(html, spans) == []


@pytest.mark.django_db
@pytest.mark.parametrize("opener", OPENERS)
@pytest.mark.parametrize("q", ["-->", "x"])
def test_http_standalone_literal_revival_registers_zero_and_never_mounts(opener, q):
    class ExpressionPage(Page):
        template = "<div dj-root>" + opener + "{{ q|safe }}" + HIDDEN + "--></div>"

        def mount(self, request, **kwargs):
            self.q = q

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    assert get_ids(session, ExpressionPage) == []
    assert (
        post(session, "lazy_hidden_forged", "djust_lazy_mount", ExpressionPage).status_code == 400
    )
    assert Hidden.mounts == 0
