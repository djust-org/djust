"""Literal-only and final-page parser liveness, including flattened runs (#3430)."""

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
    # Empty values can join authored bytes into a comment; parse the joined text.
    rust = RustLiveView(source + TAG + "-->", [])
    rust.update_state(state)
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == (0 if "<!-" in source else 1)


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
    # environment tags are opaque; empty values join the remaining literal bytes.
    expected = (
        1
        if label
        in {
            "safe entity",
            "lorem",
            "join chars",
            "straddle tag2",
            "default literal",
            "now",
            "trans",
            "blocktrans",
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
    # environment tags are opaque; empty values join the remaining literal bytes.
    control = label in {
        "safe entity",
        "lorem",
        "join chars",
        "straddle tag2",
        "default literal",
        "now",
        "trans",
        "blocktrans",
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


@pytest.mark.django_db
@pytest.mark.parametrize("q", ["-->", "x"])
@pytest.mark.parametrize(
    "parent,flt",
    [
        ("<!--y", "lower"),  # Byte-identical control retains context boundaries.
        ("<!--Y", "lower"),
        ("<!--y", 'cut:"y"'),
        ("<!--y", "title"),
        ("<!--y", "striptags"),
        ("<!--y", 'ljust:"20"'),
    ],
)
def test_http_changed_mixed_super_never_mounts_hidden(tmp_path, settings, parent, flt, q):
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "parent.html").write_text(
        "{% block body %}" + parent + "{{ q|safe }}{% endblock %}"
    )
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}{{ block.super|'
        + flt
        + " }}"
        + HIDDEN
        + "-->{% endblock %}"
    )
    ids, statuses, mounts = run(
        "<div dj-root>" + TAG + '{% include "child.html" %}</div>', {"q": q}
    )
    assert len(ids) == 1
    assert statuses == [200]
    assert mounts == 0


@pytest.mark.parametrize("outer_filter", [None, "", "|safe", '|yesno:"y-->,n,m"|safe'])
@pytest.mark.parametrize("value", ["x", "-->"])
def test_changed_mixed_super_literal_closer_restores_authority(tmp_path, outer_filter, value):
    # UTF-8 before the include tests byte-offset shifting. A literal closer
    # restores authority in the complete literal-only and final-page trees.
    (tmp_path / "parent.html").write_text("{% block body %}<!--Y{{ q|safe }}{% endblock %}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}{{ block.super|lower }}'
        + ("" if outer_filter and "yesno" in outer_filter else TAG)
        + "{% endblock %}"
    )
    (tmp_path / "grandchild.html").write_text(
        '{% extends "child.html" %}{% block body %}{{ block.super'
        + (outer_filter or "")
        + " }}{% endblock %}"
    )
    target = "grandchild.html" if outer_filter is not None else "child.html"
    rust = RustLiveView("é" + TAG + '{% include "' + target + '" %}<!-- -->' + TAG, [str(tmp_path)])
    rust.update_state({"q": value})
    html, spans = rust.render_with_provenance()
    assert html.count("dj-lazy") == (2 if outer_filter and "yesno" in outer_filter else 3)
    assert len(authored_lazy_elements(html, spans)) == 2


def test_changed_literal_only_super_keeps_following_authority(tmp_path):
    (tmp_path / "parent.html").write_text("{% block body %}Y{% endblock %}")
    rust = RustLiveView(
        '{% extends "parent.html" %}{% block body %}{{ block.super|lower }}'
        + TAG
        + "{% endblock %}",
        [str(tmp_path)],
    )
    html, spans = rust.render_with_provenance()
    assert html.startswith("y")
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("q", ["-->", "x"])
@pytest.mark.parametrize(
    "flt",
    [
        "truncatechars:200",
        "truncatewords:50",
        "default:q",
        "default_if_none:q",
        "safe|truncatechars:200",
        "ljust:n",
        "round4_identity",
    ],
)
def test_http_any_flattened_mixed_super_fails_closed(tmp_path, settings, q, flt):
    if flt == "round4_identity":
        from djust.template_filters import register_django_filter

        def identity(value):
            return value

        register_django_filter(flt, identity)
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "parent.html").write_text("{% block body %}<!--y{{ q|safe }}{% endblock %}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}{{ block.super|'
        + flt
        + " }}"
        + HIDDEN
        + "-->{% endblock %}"
    )
    ids, statuses, mounts = run(
        "<div dj-root>" + TAG + '{% include "child.html" %}</div>', {"q": q, "n": 1}
    )
    assert len(ids) == 1
    assert statuses == [200]
    assert mounts == 0


MIXED_WRAPPERS = [
    '{% spaceless %}{% include "mixed.html" %}{% endspaceless %}',
    '{% filter lower %}{% include "mixed.html" %}{% endfilter %}',
    '{% filter upper|lower %}{% include "mixed.html" %}{% endfilter %}',
    "{% spaceless %}{{ block.super }}{% endspaceless %}",
    "{% filter lower %}{{ block.super }}{% endfilter %}",
    "{% with s=block.super %}{{ s }}{% endwith %}",
    "{% with s=block.super|truncatechars:200 %}{% with t=s %}{{ t }}{% endwith %}{% endwith %}",
    '{% include "bound.html" with s=block.super %}',
    "{% firstof block.super as s %}{{ s }}",
    "{% cycle block.super as s silent %}{{ s }}",
    '{% filter lower %}{% spaceless %}{% include "mixed.html" %}{% endspaceless %}{% endfilter %}',
    '{% spaceless %}{% filter lower %}{% include "mixed.html" %}{% endfilter %}{% endspaceless %}',
]


@pytest.mark.django_db
@pytest.mark.parametrize("q", ["-->", "x"])
@pytest.mark.parametrize("wrapper", MIXED_WRAPPERS)
def test_http_mixed_capture_wrappers_fail_closed(tmp_path, settings, q, wrapper):
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "mixed.html").write_text("<!--y{{ q|safe }}")
    (tmp_path / "parent.html").write_text("{% block body %}<!--y{{ q|safe }}{% endblock %}")
    (tmp_path / "bound.html").write_text("{{ s }}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}' + wrapper + HIDDEN + "-->{% endblock %}"
    )
    ids, statuses, mounts = run(
        "<div dj-root>" + TAG + '{% include "child.html" %}</div>', {"q": q}
    )
    assert len(ids) == 1
    assert statuses == [200]
    assert mounts == 0


@pytest.mark.parametrize("wrapper", MIXED_WRAPPERS)
@pytest.mark.parametrize(
    "parent,q,expected",
    [
        ("é{{ q|safe }}", "v", 2),  # Data state remains safe after flattening.
        ("<!--y", "v", 1),
        ("literal", "v", 2),
        ("{{ q|safe }}", "v", 2),
        ("{{ q|safe }}", "", 2),
    ],
)
def test_flattened_literal_context_is_parsed_with_the_page(tmp_path, wrapper, parent, q, expected):
    (tmp_path / "mixed.html").write_text(parent)
    (tmp_path / "parent.html").write_text("{% block body %}" + parent + "{% endblock %}")
    (tmp_path / "bound.html").write_text("{{ s }}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}' + wrapper + "{% endblock %}"
    )
    rust = RustLiveView("é" + TAG + '{% include "child.html" %}' + TAG, [str(tmp_path)])
    rust.update_state({"q": q})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == expected


@pytest.mark.parametrize("lazy", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_custom_body_captures_preserve_literal_context(tmp_path, lazy, empty):
    from djust._rust import register_block_tag_handler, unregister_block_tag_handler

    class Capture:
        LAZY_BODY = lazy

        def render(self, args, content, context):
            return "" if empty else content

        def before_body(self, args, context):
            return None, None

        def after_body(self, args, content, context, state):
            return "" if empty else content

    register_block_tag_handler("round4_capture", "endround4_capture", Capture())
    try:
        (tmp_path / "mixed.html").write_text("<!--y{{ q|safe }}")
        rust = RustLiveView(
            TAG + '{% round4_capture %}{% include "mixed.html" %}{% endround4_capture %}' + TAG,
            [str(tmp_path)],
        )
        rust.update_state({"q": "v"})
        html, spans = rust.render_with_provenance()
        assert len(authored_lazy_elements(html, spans)) == 1
    finally:
        unregister_block_tag_handler("round4_capture")


@pytest.mark.parametrize(
    "wrapper",
    [
        "{% spaceless %}{{ block.super }}{% endspaceless %}",
        '{% filter cut:"y" %}{{ block.super }}{% endfilter %}',
        "{% with s=block.super %}{{ s }}{% endwith %}",
    ],
)
def test_wrapping_flattened_output_keeps_original_literal_context(tmp_path, wrapper):
    (tmp_path / "parent.html").write_text("{% block body %}<!--Y{{ q|safe }}{% endblock %}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}{{ block.super|lower }}{% endblock %}'
    )
    (tmp_path / "grandchild.html").write_text(
        '{% extends "child.html" %}{% block body %}' + wrapper + "{% endblock %}"
    )
    rust = RustLiveView(
        "é" + TAG + '{% include "grandchild.html" %}<!-- -->' + TAG, [str(tmp_path)]
    )
    rust.update_state({"q": "v"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 2


ROUND5_MIXED = [
    "{% spaceless %}<nav><a>{{ q }}</a></nav>{% endspaceless %}",
    "{% filter lower %}Title {{ q }}{% endfilter %}",
    "{% load cache %}{% cache 60 round5_mixed %}<div>{{ q }}</div>{% endcache %}",
    "{{ block.super|upper }}",
    "{{ block.super|truncatechars:50 }}",
    "{% with s=block.super %}{{ s }}{% endwith %}",
]
ROUND5_OPEN = [
    '{% spaceless %}{% include "open.html" %}{% endspaceless %}',
    '{% filter lower %}{% include "open.html" %}{% endfilter %}',
    '{% filter truncatechars:200 %}{% include "open.html" %}{% endfilter %}',
    "{{ block.super|truncatechars:200 }}",
    "{% with s=block.super %}{{ s }}{% endwith %}",
    "{% firstof block.super %}",
    "{% load cache %}{% cache 60 round5_open %}<!--y{{ w }}{% endcache %}",
    "{% load cache %}{% cache 60 round5_open_literal %}<!--y{% endcache %}",
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body,expected", [(b, 2) for b in ROUND5_MIXED] + [(b, 1) for b in ROUND5_OPEN]
)
def test_round5_http_flatten_context_and_cache_hits(tmp_path, settings, body, expected):
    from django.core.cache import caches

    caches["default"].clear()
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "open.html").write_text("<!--y")
    parent = "<nav>{{ q }}</nav>" if expected == 2 else "<!--y"
    (tmp_path / "parent.html").write_text("{% block body %}" + parent + "{% endblock %}")
    (tmp_path / "child.html").write_text(
        '{% extends "parent.html" %}{% block body %}' + body + "{% endblock %}"
    )
    template = "<div dj-root>" + TAG + '{% include "child.html" %}'
    template += ("{{ q|safe }}" if expected == 1 else "") + HIDDEN
    template += ("-->" if expected == 1 else "") + "</div>"
    # Two complete HTTP GET + lazy-mount POST cycles cover miss and hit.
    for attempt in range(2):
        ids, statuses, mounts = run(
            template, {"q": "-->" if expected == 1 else "hello", "w": "x-->"}
        )
        assert len(ids) == expected, (body, attempt, ids)
        assert statuses == [200] * expected
        assert mounts == expected - 1, (body, attempt, mounts)
        if "{% cache" in body:
            from django.core.cache.utils import make_template_fragment_key
            from djust.template_libraries import _CachedProvenanceFragment

            name = body.split("{% cache 60 ", 1)[1].split()[0]
            cached = caches["default"].get(make_template_fragment_key(name, []))
            assert isinstance(cached, _CachedProvenanceFragment)
            assert cached.literal_only is not None
            assert ("<!--" in cached.literal_only) is (expected == 1)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["legacy", "open", "same-byte-overwrite"])
def test_round5_unknown_or_open_cache_metadata_fails_closed(kind):
    from django.core.cache import caches
    from django.core.cache.utils import make_template_fragment_key
    from djust.template_libraries import _CachedProvenanceFragment

    backend = caches["default"]
    backend.clear()
    key = make_template_fragment_key("round5_legacy", [])
    fragment = _CachedProvenanceFragment("<!--cached-->")
    fragment.literal_only = "<!--cached" if kind == "open" else "cached"
    backend.set(key, fragment if kind != "legacy" else str(fragment), 60)
    if kind == "same-byte-overwrite":
        # A closed tracked result must not survive an ordinary external writer
        # merely because the new bytes equal the old cached bytes.
        backend.set(key, str(fragment), 60)
    template = "<div dj-root>" + TAG
    template += "{% load cache %}{% cache 60 round5_legacy %}not rendered{% endcache %}"
    template += HIDDEN + "</div>"
    ids, statuses, mounts = run(template, {})
    assert len(ids) == (1 if kind == "open" else 0)
    assert statuses == ([200] if kind == "open" else [])
    assert mounts == 0


ROUND6_ROWS = {
    # family A: quote outside a value position in an included (other-template) tag
    "A control include": ("", '{% include "o.html" %}', "", '<input value=5" size=">', '">'),
    "A filter lower{include}": (
        "",
        '{% filter lower %}{% include "o.html" %}{% endfilter %}',
        "",
        '<input value=5" size=">',
        '">',
    ),
    "A spaceless{include}": (
        "",
        '{% spaceless %} {% include "o.html" %}{% endspaceless %}',
        "",
        '<input value=5" size=">',
        '">',
    ),
    "A attr-name quote": (
        "",
        '{% filter lower %}{% include "o.html" %}{% endfilter %}',
        "",
        '<a "=">',
        '">',
    ),
    # family B: run starts in foreign content opened by the enclosing template
    "B control include": (
        "<svg>",
        '{% include "o.html" %}',
        "</svg>",
        "<style><!--</style>",
        "-->",
    ),
    "B filter lower{include}": (
        "<svg>",
        '{% filter lower %}{% include "o.html" %}{% endfilter %}',
        "</svg>",
        "<style><!--</style>",
        "-->",
    ),
    "B spaceless{include}": (
        "<svg>",
        '{% spaceless %} {% include "o.html" %}{% endspaceless %}',
        "</svg>",
        "<style><!--</style>",
        "-->",
    ),
    "B svg title": (
        "<svg>",
        '{% filter lower %}{% include "o.html" %}{% endfilter %}',
        "</svg>",
        "<title><!--</title>",
        "-->",
    ),
    "B math textarea": (
        "<math>",
        '{% filter lower %}{% include "o.html" %}{% endfilter %}',
        "</math>",
        "<textarea><!--</textarea>",
        "-->",
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize("closer", [True, False])
@pytest.mark.parametrize("label", list(ROUND6_ROWS))
def test_round6_http_literal_parser(tmp_path, settings, label, closer):
    pre, body, post_, inc, q = ROUND6_ROWS[label]
    q = q if closer else "x"
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "o.html").write_text(inc)

    class P(Page):
        template = "<div dj-root>" + TAG + pre + body + post_ + "{{ q|safe }}" + HIDDEN + "</div>"

        def mount(self, request, **kw):
            self.q = q

    session = SessionStore()
    session.create()
    Hidden.mounts = 0
    ids = get_ids(session, P)
    st = [post(session, i, "djust_lazy_mount", P).status_code for i in ids]
    assert len(ids) == 1, (label, q, ids)
    assert st == [200]
    assert Hidden.mounts == 0


SIDEBAR = "{% load cache %}{% cache 500 sidebar %}<nav><a>{{ user_label }}</a></nav>{% endcache %}"


@pytest.mark.django_db
@pytest.mark.parametrize("order", ["lazy page first", "plain page fills cache first"])
def test_round6_cache_shared_fragment(order):
    from django.core.cache import cache

    cache.clear()

    class Plain(Page):
        template = "<div dj-root>" + SIDEBAR + "<main>plain</main></div>"

        def mount(self, request, **kw):
            self.user_label = "u"

    class Lazy(Page):
        template = "<div dj-root>" + SIDEBAR + TAG + "</div>"

        def mount(self, request, **kw):
            self.user_label = "u"

    session = SessionStore()
    session.create()
    if order != "lazy page first":
        get_ids(session, Plain)
    ids = get_ids(session, Lazy)
    ids2 = get_ids(session, Lazy)
    assert (len(ids), len(ids2)) == (1, 1)


@pytest.mark.parametrize("mode", ["render", "render_with_diff"])
@pytest.mark.parametrize(
    "body,expected", [("<nav>{{ w }}</nav>", 1), ("<!--y{{ w }}", 0), ("<!--y", 0)]
)
def test_round6_cache_untracked_fill(mode, body, expected):
    from django.core.cache import cache
    from django.core.cache.utils import make_template_fragment_key
    from djust.template_libraries import _CachedProvenanceFragment

    cache.clear()
    source = "{% load cache %}{% cache 500 round6 %}" + body + "{% endcache %}{{ q|safe }}" + TAG
    rust = RustLiveView(source, [])
    rust.update_state({"w": "v", "q": "-->"})
    getattr(rust, mode)()
    cached = cache.get(make_template_fragment_key("round6"))
    assert isinstance(cached, _CachedProvenanceFragment)
    assert cached.literal_only == body.replace("{{ w }}", "")
    for _ in range(2):
        html, spans = rust.render_with_provenance()
        assert len(authored_lazy_elements(html, spans)) == expected


def test_round6_cache_literal_bytes_are_local_to_body():
    from django.core.cache import cache
    from django.core.cache.utils import make_template_fragment_key

    cache.clear()
    body = "{% load cache %}{% cache 500 round6_local %}<nav>{{ w }}</nav>{% endcache %}"
    rust = RustLiveView(TAG + "<!--" + body + "{{ q|safe }}" + TAG, [])
    rust.update_state({"w": "v", "q": "-->"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1
    assert cache.get(make_template_fragment_key("round6_local")).literal_only == "<nav></nav>"
    # The same cached body can be used outside the caller's open comment.
    rust = RustLiveView(body + TAG, [])
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "wrapper",
    [
        '{% include "run.html" %}',
        '{% filter lower %}{% include "run.html" %}{% endfilter %}',
        '{% spaceless %}{% include "run.html" %}{% endspaceless %}',
        '{% load cache %}{% cache 500 latest %}{% include "run.html" %}{% endcache %}',
    ],
)
def test_latest_svg_title_blocker(tmp_path, settings, wrapper):
    from django.core.cache import cache

    cache.clear()
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "run.html").write_text(
        "c=d<svg><desc><table><desc>x</DESC><math><svg/></svg x='>'>"
    )
    template = (
        "<div dj-root>"
        + TAG
        + "<svg><title>"
        + wrapper
        + "<script><!--</script>{{ q|safe }}"
        + HIDDEN
        + "</div>"
    )
    for value in ("x", "-->", "-->"):
        ids, status, mounts = run(template, {"q": value})
        assert len(ids) == 1 and status == [200] and mounts == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "wrapper",
    [
        '{% include "nav.html" %}',
        '{% load cache %}{% cache 500 svg_nav %}{% include "nav.html" %}{% endcache %}',
        '{% spaceless %}{% include "nav.html" %}{% endspaceless %}',
    ],
)
def test_svg_icon_navigation_registers_after_each_capture(tmp_path, settings, wrapper):
    from django.core.cache import cache

    cache.clear()
    settings.TEMPLATES = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    (tmp_path / "nav.html").write_text(
        '<nav><a href="/"><svg viewBox="0 0 24 24"><path d="M3 12l9-9 9 9"/></svg>Home</a></nav>'
    )
    for _ in range(2):
        ids, statuses, mounts = run("<div dj-root>" + wrapper + HIDDEN + "</div>", {})
        assert len(ids) == 1 and statuses == [200] and mounts == 1


@pytest.mark.parametrize(
    "capture,emit",
    [
        ("{% with s=block.super %}", "{{ s }}{% endwith %}"),
        ("{% with s=block.super %}{% with t=s %}", "{{ t }}{% endwith %}{% endwith %}"),
        ("{% firstof block.super as s %}", "{{ s }}"),
        ("{% cycle block.super as s silent %}", "{{ s }}"),
        ('{% include "capture.html" with s=block.super %}', ""),
        ('{% include "capture.html" with s=block.super only %}', ""),
    ],
)
@pytest.mark.parametrize("closer", ["", "</script>"])
def test_captured_literals_follow_emission_position(tmp_path, capture, emit, closer):
    (tmp_path / "parent.html").write_text("{% block body %}<script>{{ q|safe }}{% endblock %}")
    if "include" in capture:
        (tmp_path / "capture.html").write_text("{{ empty }}</script>{{ s }}" + closer + TAG)
        body = capture
    else:
        body = capture + "{{ empty }}</script>" + emit + closer + TAG
    rust = RustLiveView(
        '{% extends "parent.html" %}{% block body %}' + body + "{% endblock %}", [str(tmp_path)]
    )
    rust.update_state({"q": "</script>", "empty": ""})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, spans)) == (1 if closer else 0)


def test_condition_capture_does_not_emit_literal_bytes(tmp_path):
    (tmp_path / "parent.html").write_text("{% block body %}</script>{% endblock %}")
    rust = RustLiveView(
        '{% extends "parent.html" %}{% block body %}<script>{% if block.super %}{{ empty }}'
        + TAG
        + "{% endif %}{% endblock %}",
        [str(tmp_path)],
    )
    html, spans = rust.render_with_provenance()
    assert not authored_lazy_elements(html, spans)


@pytest.mark.parametrize(
    "emit",
    [
        "{{ q|default:block.super|safe }}",
        "{% with s=q|default:block.super|safe %}{{ s }}{% endwith %}",
        "{% firstof q|default:block.super|safe %}",
        '{% cycle q|default:block.super|safe "x" %}',
    ],
)
def test_unused_filter_argument_capture_never_closes_literal_context(tmp_path, emit):
    (tmp_path / "parent.html").write_text("{% block body %}</script>{% endblock %}")
    rust = RustLiveView(
        '{% extends "parent.html" %}{% block body %}<script>' + emit + TAG + "{% endblock %}",
        [str(tmp_path)],
    )
    rust.update_state({"q": "</script>"})
    html, spans = rust.render_with_provenance()
    assert not authored_lazy_elements(html, spans)


def test_include_filename_capture_does_not_emit_literal_bytes(tmp_path):
    (tmp_path / "parent.html").write_text("{% block body %}</script>{% endblock %}")
    (tmp_path / "<").mkdir()
    (tmp_path / "</script>").write_text("{{ q|safe }}" + TAG)
    rust = RustLiveView(
        '{% extends "parent.html" %}{% block body %}<script>{% include block.super %}{% endblock %}',
        [str(tmp_path)],
    )
    rust.update_state({"q": "</script>"})
    html, spans = rust.render_with_provenance()
    assert not authored_lazy_elements(html, spans)


def test_raw_translation_unused_parent_capture_is_not_emitted(tmp_path):
    (tmp_path / "parent.html").write_text("{% block body %}</script>{% endblock %}")
    rust = RustLiveView(
        '{% extends "parent.html" %}{% load i18n %}{% block body %}<script>'
        "{% autoescape off %}{% blocktrans with x=block.super %}{{ q }}{% endblocktrans %}{% endautoescape %}"
        + TAG
        + "{% endblock %}",
        [str(tmp_path)],
    )
    rust.update_state({"q": "</script>"})
    html, spans = rust.render_with_provenance()
    assert len(authored_lazy_elements(html, [(0, len(html.encode()))])) == 1
    assert not authored_lazy_elements(html, spans)


def test_nested_raw_parent_read_preserves_eager_body_projection(tmp_path):
    from djust._rust import register_block_tag_handler, unregister_block_tag_handler

    class Capture:
        def render(self, args, content, context):
            context["block"].super()
            return content

    register_block_tag_handler("nested_parent_read", "endnested_parent_read", Capture())
    try:
        (tmp_path / "parent.html").write_text(
            "{% load i18n %}{% block body %}{% blocktrans %}x{% endblocktrans %}{% endblock %}"
        )
        rust = RustLiveView(
            '{% extends "parent.html" %}{% block body %}'
            "{% nested_parent_read %}<script>{{ q|safe }}{% endnested_parent_read %}"
            + TAG
            + "{% endblock %}",
            [str(tmp_path)],
        )
        rust.update_state({"q": "</script>"})
        html, spans = rust.render_with_provenance()
        assert len(authored_lazy_elements(html, [(0, len(html.encode()))])) == 1
        assert not authored_lazy_elements(html, spans)
    finally:
        unregister_block_tag_handler("nested_parent_read")
