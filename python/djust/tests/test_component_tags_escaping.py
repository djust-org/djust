"""Values passed to the ``djust_components`` template tags are HTML-escaped.

Covers the tags whose text, attribute and ``<img src>`` values are built from
tag kwargs / context data: plain strings come out HTML-escaped, values marked
safe (``mark_safe``) pass through unchanged, and image URLs with a
non-navigation scheme (``javascript:``) become ``#``.
"""

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

_ENGINE = Engine(libraries={"djust_components": "djust.components.templatetags.djust_components"})

MARKUP = "<img src=x onerror=alert(1)>"
ATTR_BREAK = 'x" onmouseover="y'


def _render(tag_src, **ctx):
    return _ENGINE.from_string("{% load djust_components %}" + tag_src).render(Context(ctx))


# ---------------------------------------------------------------------------
# Text / attribute values
# ---------------------------------------------------------------------------

# (id, template, context builder taking the value)
TEXT_CASES = [
    (
        "dashboard_grid_panel_content",
        "{% dashboard_grid panels=panels %}{% enddashboard_grid %}",
        lambda v: {"panels": [{"id": "p1", "title": "T", "content": v}]},
    ),
    (
        "masonry_grid_item_content",
        "{% masonry_grid items=items %}",
        lambda v: {"items": [{"content": v}]},
    ),
    (
        "meter_legend_value",
        "{% meter segments=segs %}",
        lambda v: {"segs": [{"label": "a", "value": v}]},
    ),
    (
        "audit_log_column_class",
        "{% audit_log entries=entries columns=cols %}",
        lambda v: {"entries": [{v: "row"}], "cols": [v]},
    ),
    (
        "file_dropzone_id",
        "{% file_dropzone name=v %}",
        lambda v: {"v": v},
    ),
]


@pytest.mark.parametrize("value", [MARKUP, ATTR_BREAK], ids=["tag", "attr"])
@pytest.mark.parametrize(
    "tag_src,build", [c[1:] for c in TEXT_CASES], ids=[c[0] for c in TEXT_CASES]
)
def test_value_is_html_escaped(tag_src, build, value):
    out = _render(tag_src, **build(value))
    assert value not in out
    if value == MARKUP:
        assert "&lt;img src=x onerror=alert(1)&gt;" in out
    else:
        assert "x&quot; onmouseover=&quot;y" in out


MARKED_SAFE_CASES = [c for c in TEXT_CASES if c[0] != "audit_log_column_class"]


@pytest.mark.parametrize(
    "tag_src,build",
    [c[1:] for c in MARKED_SAFE_CASES if c[0] != "file_dropzone_id"],
    ids=[c[0] for c in MARKED_SAFE_CASES if c[0] != "file_dropzone_id"],
)
def test_marked_safe_value_passes_through(tag_src, build):
    out = _render(tag_src, **build(mark_safe("<b>ok</b>")))
    assert "<b>ok</b>" in out


def test_plain_text_value_is_unchanged():
    out = _render("{% meter segments=segs %}", segs=[{"label": "a", "value": 42}])
    assert '<span class="dj-meter__legend-value">42</span>' in out
    out = _render("{% file_dropzone name=v %}", v="upload")
    assert 'id="dz-upload"' in out


def test_kanban_move_event_is_escaped_for_the_js_string():
    cols = [{"id": "todo", "title": "To Do", "cards": []}]
    out = _render("{% kanban_board columns=cols move_event=ev %}", cols=cols, ev="x');alert(1);//")
    assert "x');alert(1)" not in out
    assert "x\\u0027)\\u003Balert(1)\\u003B//" in out
    # A plain event name is emitted unchanged.
    out = _render("{% kanban_board columns=cols move_event='move_card' %}", cols=cols)
    assert "handleEvent('move_card'," in out


# ---------------------------------------------------------------------------
# <img src> values
# ---------------------------------------------------------------------------

IMAGE_CASES = [
    ("carousel", "{% carousel images=imgs %}", lambda u: {"imgs": [{"src": u, "alt": "a"}]}),
    ("carousel_plain", "{% carousel images=imgs %}", lambda u: {"imgs": [u]}),
    (
        "rich_select_image",
        "{% rich_select options=opts value='a' %}",
        lambda u: {"opts": [{"value": "a", "label": "A", "image": u}]},
    ),
    ("responsive_image_src", "{% responsive_image src=u %}", lambda u: {"u": u}),
    (
        "responsive_image_placeholder",
        "{% responsive_image src='/ok.png' placeholder=u %}",
        lambda u: {"u": u},
    ),
    (
        "avatar_group",
        "{% avatar_group users=users %}",
        lambda u: {"users": [{"name": "A B", "avatar": u}]},
    ),
    (
        "chat_bubble",
        "{% chat_bubble message=m %}",
        lambda u: {"m": {"name": "A", "text": "t", "avatar": u}},
    ),
    (
        "presence_avatars",
        "{% presence_avatars users=users %}",
        lambda u: {"users": [{"name": "A", "avatar": u}]},
    ),
    (
        "mentions_input",
        "{% mentions_input users=users %}",
        lambda u: {"users": [{"id": 1, "name": "A", "avatar": u}]},
    ),
    (
        "sortable_grid",
        "{% sortable_grid items=items %}",
        lambda u: {"items": [{"id": 1, "label": "A", "thumbnail": u}]},
    ),
    ("image_cropper", "{% image_cropper src=u %}", lambda u: {"u": u}),
    (
        "lightbox",
        "{% lightbox images=imgs open=is_open %}",
        lambda u: {"imgs": [{"src": u, "alt": "a"}], "is_open": True},
    ),
    (
        "org_chart",
        "{% org_chart nodes=nodes %}",
        lambda u: {"nodes": [{"id": "1", "name": "A", "avatar": u}]},
    ),
    (
        "live_indicator",
        "{% live_indicator user=user %}",
        lambda u: {"user": {"name": "A", "avatar": u}},
    ),
    (
        "activity_feed",
        "{% activity_feed events=events %}",
        lambda u: {"events": [{"user": "A", "action": "did", "avatar": u}]},
    ),
    ("image_upload_preview", "{% image_upload_preview previews=p %}", lambda u: {"p": [u]}),
    (
        "data_card_grid",
        "{% data_card_grid items=items %}",
        lambda u: {"items": [{"title": "A", "image": u}]},
    ),
]

_IMG_IDS = [c[0] for c in IMAGE_CASES]
_IMG_PARAMS = [c[1:] for c in IMAGE_CASES]


@pytest.mark.parametrize("tag_src,build", _IMG_PARAMS, ids=_IMG_IDS)
def test_image_src_javascript_scheme_becomes_hash(tag_src, build):
    out = _render(tag_src, **build("javascript:alert(1)"))
    assert 'src="javascript:' not in out
    assert 'src="#"' in out


@pytest.mark.parametrize("tag_src,build", _IMG_PARAMS, ids=_IMG_IDS)
def test_image_src_is_html_escaped(tag_src, build):
    out = _render(tag_src, **build('/a.png" onerror="y'))
    assert '/a.png" onerror="y' not in out
    assert "/a.png&quot; onerror=&quot;y" in out


@pytest.mark.parametrize("tag_src,build", _IMG_PARAMS, ids=_IMG_IDS)
def test_image_src_relative_path_is_kept(tag_src, build):
    out = _render(tag_src, **build("/path?a=1"))
    assert 'src="/path?a=1"' in out


@pytest.mark.parametrize("tag_src,build", _IMG_PARAMS, ids=_IMG_IDS)
def test_image_src_data_image_uri_is_kept(tag_src, build):
    out = _render(tag_src, **build("data:image/png;base64,AAAA"))
    assert 'src="data:image/png;base64,AAAA"' in out


@pytest.mark.parametrize("tag_src,build", _IMG_PARAMS, ids=_IMG_IDS)
def test_marked_safe_image_src_passes_through(tag_src, build):
    out = _render(tag_src, **build(mark_safe("/safe.png?a=1&amp;b=2")))
    assert 'src="/safe.png?a=1&amp;b=2"' in out
