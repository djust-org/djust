"""HTML tokenizer error recovery at the root selection/stamping boundaries."""

import pytest

from djust.mixins.template import (
    TemplateMixin,
    _DJ_ROOT_RE,
    _DJ_VIEW_RE,
    _search_dj_root_open,
)


@pytest.mark.parametrize(
    "html, opening",
    [
        (
            '<div a=b"<section dj-root>">inside</div><main dj-root>later</main>',
            '<div a=b"<section dj-root>',
        ),
        ('<?x "<div dj-root>" ?><main dj-root>real</main>', "<main dj-root>"),
        ('<!bogus "<div dj-root>"><main dj-root>real</main>', "<main dj-root>"),
        ('<p title="never><main dj-root>phantom</main>', None),
        ("<p a=never<main dj-root", None),
    ],
)
def test_root_tokenizer_error_recovery(html, opening):
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert (html[match.start() : match.end()] if match else None) == opening


@pytest.mark.parametrize(
    "body",
    [
        '<p title="> </main> phantom">real</p>',
        "<?bogus </main> tail?>real",
        "<!bogus </main> tail>real",
    ],
)
def test_closing_walk_skips_attribute_values_and_bogus_comments(body):
    html = "<main dj-root>" + body + "</main><footer>end</footer>"
    start = len("<main dj-root>")
    close, end = TemplateMixin._find_closing_tag_pos(html, start, "main")
    assert html[start:close] == body
    assert end == start + len(body) + len("</main>")


def test_stamp_targets_the_real_attribute_after_an_unquoted_quote():
    html = '<div a=b"<section dj-root>body</div>'
    assert TemplateMixin._stamp_dj_view(html, "views.Page") == (
        '<div a=b"<section dj-root dj-view="views.Page">body</div>'
    )


def test_stamp_does_not_touch_bogus_comments_or_unterminated_tags():
    html = '<?x "<div dj-root>" ?><p title="never><main dj-root>'
    assert TemplateMixin._stamp_dj_view(html, "views.Page") == html


@pytest.mark.parametrize(
    "template, tag",
    [
        ('<div a=b"<section dj-root><p>{{ n }}</p></div><main dj-root>later</main>', "div"),
        ('<?x "<div dj-root>" ?><main dj-root><p>{{ n }}</p></main>', "main"),
        ('<main dj-root><p title="> </main> phantom">{{ n }}</p></main>', "main"),
    ],
)
def test_native_render_and_update_keep_the_html5ever_root(template, tag):
    import json

    from djust._rust import RustLiveView

    view = RustLiveView(template)
    view.update_state({"n": "first"})
    initial, _, _ = view.render_with_diff()
    assert initial.startswith("<" + tag + " ")
    assert ">first</p>" in initial
    assert "later" not in initial
    view.update_state({"n": "second"})
    updated, patches, _ = view.render_with_diff()
    assert updated.startswith("<" + tag + " ")
    assert ">second</p>" in updated
    assert json.loads(patches) == [{"type": "SetText", "path": [0, 0], "text": "second"}]


@pytest.mark.django_db
def test_http_get_stamps_the_tokenizer_owned_root():
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from djust import LiveView

    class Page(LiveView):
        template = '<?bogus "<div dj-root>" ?><main dj-root><p>real</p></main>'

    request = RequestFactory().get("/tokenizer-3054/")
    request.session = SessionStore()
    request.session.create()
    response = Page.as_view()(request)
    html = response.content.decode()
    assert '<?bogus "<div dj-root>" ?>' in html
    assert '<main dj-root dj-view="' in html
    assert html.count('dj-view="') == 1


def test_malformed_child_wrapper_still_owns_its_roots():
    child = (
        '<div a=b" data-djust-embedded="c" dj-view="views.Child"><main dj-root>child</main></div>'
    )
    html = child + "<main dj-root>page</main>"
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match.start() == len(child)
    stamped = TemplateMixin._stamp_dj_view(html, "views.Page")
    assert stamped.startswith(child)
    assert stamped.endswith('<main dj-root dj-view="views.Page">page</main>')


@pytest.mark.parametrize(
    "prefix",
    [
        '<script\f>"<div dj-root>fake</div>"</script>',
        '<style\f>"<div dj-root>fake</div>"</style>',
        "<div\vdj-root>fake</div>",
        "<div\u00a0dj-root>fake</div>",
        "<div a\vdj-root>fake</div>",
        "<div a\u00a0dj-root>fake</div>",
        "<!-->",
        "<!--->",
        "<!--x--!>",
    ],
)
def test_html_whitespace_and_comment_error_recovery(prefix):
    html = prefix + "<main dj-root>real</main>"
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match.start() == len(prefix)
    assert TemplateMixin()._extract_liveview_content(html) == "real"
    stamped = TemplateMixin._stamp_dj_view(html, "views.Page")
    assert stamped.startswith(prefix)
    assert stamped.endswith('<main dj-root dj-view="views.Page">real</main>')


@pytest.mark.parametrize("closing", ["</main ignored='>'>", "</main/>"])
def test_end_tag_attributes_and_self_closing_flag_still_close_the_root(closing):
    html = "<main dj-root>real" + closing + "<footer>tail</footer>"
    close, end = TemplateMixin._find_closing_tag_pos(html, len("<main dj-root>"), "main")
    assert close == len("<main dj-root>real")
    assert end == close + len(closing)
    assert TemplateMixin()._extract_liveview_content(html) == "real"


@pytest.mark.parametrize("opening", ["<main/dj-root>", '<main a="x"dj-root>'])
def test_attribute_error_recovery_still_exposes_real_names(opening):
    from djust._rust import RustLiveView

    html = opening + "real</main>"
    match = _search_dj_root_open(html, _DJ_ROOT_RE)
    assert match.start() == 0
    assert match.end() == len(opening)
    assert RustLiveView(html).render_with_diff()[0].startswith("<main ")
    assert TemplateMixin()._extract_liveview_content(html) == "real"
    assert 'dj-root dj-view="views.Page"' in TemplateMixin._stamp_dj_view(html, "views.Page")


def test_unicode_casefold_does_not_make_text_into_an_html_tag():
    from djust._rust import RustLiveView

    prefix = "<ſection dj-root>fake</ſection>"
    html = prefix + "<main dj-root>real</main>"
    assert RustLiveView(html).render_with_diff()[0].startswith("<main ")
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match.start() == len(prefix)
    assert TemplateMixin()._extract_liveview_content(html) == "real"
    assert TemplateMixin._stamp_dj_view(html, "views.Page").startswith(prefix)


@pytest.mark.parametrize(
    "opening,closing",
    [
        ("<textarea>", "</textarea>"),
        ("<TEXTAREA data-note='>'>", "</TeXtArEa >"),
        ("<textarea/>", "</textarea>"),
    ],
)
def test_body_root_ignores_textarea_fake_roots(opening, closing):
    html = (
        "<body dj-root>"
        + opening
        + "<section dj-root>fake</section></textareax>"
        + closing
        + "<main>real</main></body>"
    )
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match is not None and match.start() == 0
    stamped = TemplateMixin._stamp_dj_view(html, "views.Page")
    assert stamped == html.replace("<body dj-root>", '<body dj-root dj-view="views.Page">', 1)


def test_unterminated_textarea_contains_no_real_inner_root():
    html = "<body dj-root><textarea><section dj-root>fake</section>"
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match is not None and match.start() == 0
    assert TemplateMixin._stamp_dj_view(html, "views.Page") == html.replace(
        "<body dj-root>", '<body dj-root dj-view="views.Page">', 1
    )


def test_textarea_itself_remains_a_root_candidate():
    html = "<body dj-root><textarea dj-root>literal <textarea> text</textarea></body>"
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match is not None and html[match.start() : match.end()] == "<textarea dj-root>"
    assert TemplateMixin()._extract_liveview_content(html) == "literal <textarea> text"


def test_textarea_end_tag_attributes_do_not_expose_a_phantom_root():
    html = (
        '<body dj-root><textarea>text</textarea title="<section dj-root>"><main>real</main></body>'
    )
    match = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert match is not None and match.start() == 0
