"""Authored liveness: I1 positives and documented M1 limitations (#3430)."""

from pathlib import Path

import pytest
from djust._rust import RustLiveView, authored_lazy_elements
from django.contrib.sessions.backends.db import SessionStore
from djust.tests.test_lazy_provenance_http_3252 import Child, Page, get_ids, post, TAG


class Hidden(Child):
    mounts = 0


HIDDEN = '<div dj-view="' + __name__ + '.Hidden" dj-lazy></div>'


def run(tpl, state):
    class P(Page):
        template = tpl

        def mount(self, request, **kw):
            for k, v in state.items():
                setattr(self, k, v)

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, P)
    st = [post(session, i, "djust_lazy_mount", P).status_code for i in ids]
    return ids, st, Hidden.mounts


# straddle revival over HTTP: TAG is the always-live control container
@pytest.mark.django_db
@pytest.mark.parametrize("c", ["/script", "x"])
def test_value_assembled_context_http_documented_behaviour(c):
    # docs/lazy-http-provenance.md: value-assembled hiding is not modelled.
    ids, st, m = run(
        "<div dj-root>" + TAG + "<{{ t }}>x<{{ c }}>" + HIDDEN + "</div>", {"t": "script", "c": c}
    )
    expected = 2 if c == "/script" else 1
    assert len(ids) == expected
    assert st == [200] * expected
    assert m == expected - 1


# false negatives: realistic content BEFORE two ordinary containers (expect 2 ids each)
PRE = {
    "baseline": "",
    "escaped value with quote/<": "<p>{{ name }} paid {{ price }}</p>",
    "value with --": "<p>{{ season }}</p>",
    "csrf form": "<form method=post>{% csrf_token %}<button>Logout</button></form>",
    "markdown |safe": "<article>{{ body|safe }}</article>",
    "client_config head": "{% load live_tags %}{% djust_client_config %}",
    "json_script": "{{ data|json_script:'d' }}",
    "if element branch": "{% if flag %}<b>x</b>{% endif %}",
    "with literal attr": '{% with c="btn" %}<a class="{{ c }}">x</a>{% endwith %}',
    "urlize": "<p>{{ note|urlize }}</p>",
    "linebreaks": "<p>{{ note|linebreaks }}</p>",
}
STATE = {
    "name": 'O\'Brien "Jr"',
    "price": "<5",
    "season": "2025--2026",
    "body": "<h2>Hi</h2><p>text</p>",
    "data": {"a": 1},
    "flag": True,
    "note": "see https://example.com now\nnext paragraph",
    "csrf_token": "a" * 64,
}


@pytest.mark.django_db
@pytest.mark.parametrize("label", list(PRE))
def test_i1_ordinary_content_registers_following_containers(label):
    ids, st, _ = run("<div dj-root>" + PRE[label] + TAG + TAG + "</div>", STATE)
    assert len(ids) == 2
    assert st == [200, 200]


def test_real_demo_registers_all_five_containers():
    root = Path(__file__).resolve().parents[3] / "examples" / "demo_project"
    source = (root / "djust_demos/templates/demos/multi_view.html").read_text()
    rust = RustLiveView(source, [str(root / "templates"), str(root / "djust_demos/templates")])
    rust.update_state({"clicks": 0, "pushes": 0})
    html, spans = rust.render_with_provenance()
    assert html.count("dj-lazy=") == 5
    assert len(authored_lazy_elements(html, spans)) == 5


@pytest.mark.parametrize(
    "source,state",
    [
        ("<{{ t }}>x<{{ c }}>", {"t": "script", "c": "/script"}),
        ("<!-{{ d }}- a>-{{ e }}>", {"d": "-", "e": "-"}),
    ],
)
def test_m1_boundary_assembled_context_documented_behaviour(source, state):
    # docs/lazy-http-provenance.md: literal/value boundaries are not modelled.
    rust = RustLiveView(source + TAG + "-->", [])
    rust.update_state(state)
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


C = TAG
Q = "{{ q|safe }}"
REVIVAL_CASES = {
    # literal-derived openers transformed by filters (revival q='-->' expected 0)
    "add literal": '{{ "<"|add:"!--"|safe }}' + Q + C + "-->",
    "stringformat": '{{ "<!--"|stringformat:"s"|safe }}' + Q + C + "-->",
    "join chars": '{{ "<!--"|join:""|safe }}' + Q + C + "-->",
    "safe entity": '{{ "&lt;!--"|safe }}' + Q + C + "-->",
    "upper of opener": '{{ "<!--"|upper|safe }}' + Q + C + "-->",
    "default literal": '{{ missing|default:"<!--"|safe }}' + Q + C + "-->",
    "now": '{% now "<!--" %}' + Q + C + "-->",
    "templatetag": "{% templatetag openblock %}<!--" + Q + C + "-->",
    "verbatim": "{% verbatim %}<!--{% endverbatim %}" + Q + C + "-->",
    "widthratio": "{% widthratio 1 1 1 %}<!--" + Q + C + "-->",
    "lorem": "{% lorem 1 w %}" + Q + C,
    "trans": '{% load i18n %}{% trans "<!--" %}' + Q + C + "-->",
    "blocktrans": "{% load i18n %}{% blocktrans %}<!--{% endblocktrans %}" + Q + C + "-->",
    "_() literal": '{{ _("<!--") }}' + Q + C + "-->",
    "cycle literal": '{% cycle "<!--" "x" %}' + Q + C + "-->",
    "cycle as + reuse": '{% cycle "<!--" "x" as o silent %}{{ o }}' + Q + C + "-->",
    "with->for shadow": '{% with o="<!--" %}{{ o }}{% for o in qs %}{{ o|safe }}{% endfor %}{% endwith %}'
    + C
    + "-->",
    "with->firstof shadow": '{% with o="<!--" %}{{ o }}{% firstof q as o %}{{ o|safe }}{% endwith %}'
    + C
    + "-->",
    "with->cycle upward": '{% with o="<!--" %}{{ o }}{% cycle q "y" as o silent %}{{ o|safe }}{% endwith %}'
    + C
    + "-->",
    "include with literal then value": '{% with o="<!--" %}{{ o }}{% include "incq.html" %}{% endwith %}'
    + C
    + "-->",
    "block.super filtered w/ value": '{% extends "basev.html" %}{% block a %}{{ block.super|lower }}{% endblock %}{% block b %}'
    + C
    + "-->{% endblock %}",
    "block.super filtered w/ if+value": '{% extends "basev2.html" %}{% block a %}{{ block.super|safe }}{% endblock %}{% block b %}'
    + C
    + "-->{% endblock %}",
    "block.super filtered literal": '{% extends "base.html" %}{% block a %}{{ block.super|safe }}'
    + Q
    + "{% endblock %}{% block b %}"
    + C
    + "-->{% endblock %}",
    # straddle: context formed across literal/opaque boundary
    "straddle tag": "<{{ t }}>" + C + "<{{ c }}>",
    "straddle tag2": "<{{ t }}>x<{{ c }}>" + C,
    "straddle comment": "<!-{{ d }}- a>-{{ e }}>" + C + "-->",
    # in-attr literal opener
    "attr quote literal": '<p title={{ "\'"|safe }}>' + Q + C + "'>",
}


@pytest.mark.parametrize("label", list(REVIVAL_CASES))
def test_review_corpus_authored_liveness(tmp_path, label):
    (tmp_path / "base.html").write_text(
        "{% block a %}<!--{% endblock %}{% block b %}{% endblock %}"
    )
    (tmp_path / "basev.html").write_text(
        "{% block a %}<!--{{ q|safe }}{% endblock %}{% block b %}{% endblock %}"
    )
    (tmp_path / "basev2.html").write_text(
        "{% block a %}<!--{% if t %}<b>{{ q|safe }}</b>{% endif %}{% endblock %}{% block b %}{% endblock %}"
    )
    (tmp_path / "incq.html").write_text("{{ q|safe }}")
    rust = RustLiveView(REVIVAL_CASES[label], [str(tmp_path)])
    rust.update_state(
        {"q": "-->", "qs": ["-->"], "t": "script", "c": "/script", "d": "-", "e": "-"}
    )
    html, spans = rust.render_with_provenance()
    # docs/lazy-http-provenance.md: value-derived filters, translations and
    # environment tags are opaque; straddled hiding contexts are not modelled.
    expected = (
        1
        if label
        in {
            "safe entity",
            "lorem",
            "join chars",
            "straddle tag2",
            "straddle comment",
            "default literal",
            "now",
            "trans",
            "_() literal",
        }
        else 0
    )
    assert len(authored_lazy_elements(html, spans)) == expected


@pytest.mark.django_db
@pytest.mark.parametrize("label", list(REVIVAL_CASES))
def test_http_review_corpus_authored_liveness(tmp_path, settings, label):
    (tmp_path / "base.html").write_text(
        "{% block a %}<!--{% endblock %}{% block b %}{% endblock %}"
    )
    (tmp_path / "basev.html").write_text(
        "{% block a %}<!--{{ q|safe }}{% endblock %}{% block b %}{% endblock %}"
    )
    (tmp_path / "basev2.html").write_text(
        "{% block a %}<!--{% if t %}<b>{{ q|safe }}</b>{% endif %}{% endblock %}"
        "{% block b %}{% endblock %}"
    )
    (tmp_path / "incq.html").write_text("{{ q|safe }}")
    (tmp_path / "candidate.html").write_text(REVIVAL_CASES[label].replace(TAG, HIDDEN))
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    ids, statuses, mounts = run(
        "<div dj-root>" + TAG + '{% include "candidate.html" %}</div>',
        {"q": "-->", "qs": ["-->"], "t": "script", "c": "/script", "d": "-", "e": "-"},
    )
    # docs/lazy-http-provenance.md: value-derived filters, translations and
    # environment tags are opaque; straddled hiding contexts are not modelled.
    control = label in {
        "safe entity",
        "lorem",
        "join chars",
        "straddle tag2",
        "straddle comment",
        "default literal",
        "now",
        "trans",
        "_() literal",
    }
    assert len(ids) == (2 if control else 1)
    assert statuses == [200] * len(ids)
    assert mounts == (1 if control else 0)


@pytest.mark.parametrize(
    "prefix,emitted",
    [
        ("{% csrf_token %}", '<input type="hidden"'),
        ("{% load live_tags %}{% djust_client_config %}", "<script"),
    ],
)
def test_framework_output_is_nonempty_and_preserves_authority(prefix, emitted):
    rust = RustLiveView(prefix + TAG, [])
    rust.update_state({"csrf_token": "a" * 64})
    html, spans = rust.render_with_provenance()
    assert emitted in html
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.parametrize(
    "before,value,after",
    [
        ("<p title='", "'>", "</p>"),
        ("<script>", "</script>", "</script>"),
        ("<textarea>", "</textarea>", "</textarea>"),
        ("<template>", "</template>", "</template>"),
        ("<style>", "</style>", "</style>"),
        ("<script><!--", "<script>", "</script>"),
    ],
)
def test_m1_value_exit_documented_behaviour(before, value, after):
    # docs/lazy-http-provenance.md: value-generated context changes are blank.
    rust = RustLiveView(TAG + before + "{{ q|safe }}" + after + TAG, [])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    # The authored quote remains open when the value is blank; script's
    # double-escaped final context also fails E. Other cases survive both.
    expected = 1 if before in {"<p title='", "<script><!--"} else 2
    assert len(authored_lazy_elements(html, spans)) == expected


@pytest.mark.parametrize(
    "value", ["<b>x</b>", "<script>x</script>", "<!--x-->", "2025--2026", "&gt;"]
)
def test_complete_opaque_markup_preserves_following_context(value):
    rust = RustLiveView("{{ q|safe }}" + TAG, [])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_m1_separate_values_documented_behaviour():
    # docs/lazy-http-provenance.md: value-generated hiding is not modelled.
    rust = RustLiveView("{{ a|safe }}{{ b|safe }}" + TAG, [])
    rust.update_state({"a": "<!--", "b": "-->"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


def test_m1_value_annotation_encoding_documented_behaviour():
    # docs/lazy-http-provenance.md: value-generated context changes are blank.
    rust = RustLiveView(
        '<math><annotation-xml encoding="{{ q }}">' + TAG + "</annotation-xml></math>" + TAG, []
    )
    rust.update_state({"q": "text/html"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "opener",
    [
        '{{ "<!--" }}',
        '{% firstof "<!--" %}',
        '{% with o="<!--" %}{{ o }}{% endwith %}',
        "<!--",
        "{% if flag %}<!--{% endif %}",
    ],
)
@pytest.mark.parametrize("q", ["-->", "x"])
def test_exact_rev3429_http_cases_never_mount_hidden(opener, q):
    ids, statuses, mounts = run(
        "<div dj-root>" + TAG + opener + "{{ q|safe }}" + HIDDEN + "--></div>",
        {"q": q, "flag": True},
    )
    assert len(ids) == 1
    assert statuses == [200]
    assert mounts == 0
