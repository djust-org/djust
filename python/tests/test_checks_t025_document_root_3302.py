"""Tests for djust system check T025 (#3302).

T025 (WARNING) flags ``dj-view`` / ``dj-root`` on ``<html>``, ``<head>`` or
``<body>``. The HTTP render of such a page is complete, but the WebSocket
mount looks for the root *inside* ``<body>`` and otherwise falls back to the
first element child, so a page with two body children mounts with only the
first one. T002 used to say this shape was fine ("the page still connects").
"""

import os

import pytest

import time

from djust.checks import _document_root_scan_text, _find_document_roots


def _hits(markup):
    return [(tag, attr) for _, tag, attr in _find_document_roots(markup)]


@pytest.mark.parametrize("tag", ["html", "head"])
@pytest.mark.parametrize("attr", ["dj-view", "dj-root"])
def test_regex_matches_document_elements(tag, attr):
    assert _hits('<%s lang="en" %s="x.V">' % (tag, attr)) == [(tag, attr)]


def test_regex_is_case_insensitive_and_tolerates_bare_attribute():
    assert _hits("<HTML DJ-ROOT>") == [("html", "dj-root")]


@pytest.mark.parametrize("attr", ["dj-view", "dj-root"])
def test_body_is_a_root_not_a_t025_hit(attr):
    # #3302: <body> is a supported root; its children are the page's siblings.
    assert _hits('<body lang="en" %s="x.V">' % attr) == []
    assert _hits("<BODY %s>" % attr.upper()) == []


@pytest.mark.parametrize(
    "markup",
    [
        '<div dj-view="x.V">',
        "<main dj-root>",
        '<html lang="en"><body><div dj-root>',
        "<header dj-root>",  # `<head` must not match a longer tag name
        "<htmlx dj-root>",
        "<body-wrapper dj-root>",
        '<body class="a">',
        '<body title="dj-root">',  # attribute VALUE text is not an attribute
        "<body data-x='dj-view'>",
    ],
)
def test_regex_ignores_everything_else(markup):
    assert _hits(markup) == []


def test_regex_walks_quoted_values_so_a_gt_does_not_end_the_tag():
    assert _hits('<html data-x="a>b" dj-root>') == [("html", "dj-root")]
    assert _hits("<html data-x='a>b' dj-view='x.V'>") == [("html", "dj-view")]


@pytest.mark.parametrize(
    "markup",
    [
        "<html {% if x %}dj-root{% endif %}>",
        "<html {% if a > b %}dj-root{% endif %} class='c'>",
        '<html class="{% if x %}a{% endif %}" {{ extra }}dj-root>',
    ],
)
def test_a_template_tag_in_the_tag_is_walked_whole(markup):
    assert _hits(markup) == [("html", "dj-root")]


def test_a_template_tag_does_not_hide_the_real_end_of_the_tag():
    assert _hits("<html {% if a > b %}class='x'{% endif %}><div dj-root>") == []


def _scan(tmp_path, settings, body, ids=("djust.T025",)):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(exist_ok=True)
    (tpl_dir / "t.html").write_text(body)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": False,
        }
    ]
    from djust.checks import check_templates

    return [e for e in check_templates(None) if e.id in ids]


def test_fires_on_the_issues_repro(tmp_path, settings):
    found = _scan(
        tmp_path,
        settings,
        '<html dj-view="app.V"><body><header>h</header><main>m</main></body></html>',
    )
    assert len(found) == 1
    msg = found[0]
    assert msg.level == 30  # WARNING
    assert "<html>" in msg.msg and "dj-view" in msg.msg
    assert ":1" in msg.msg
    assert "<div dj-root>" in msg.hint or "wrapping" in msg.hint


def test_fires_on_html_root_and_names_the_line(tmp_path, settings):
    found = _scan(
        tmp_path, settings, "<!DOCTYPE html>\n<html dj-root>\n<body><p>x</p></body></html>"
    )
    assert len(found) == 1
    assert "<html>" in found[0].msg
    assert ":2" in found[0].msg


def test_does_not_fire_on_a_body_root(tmp_path, settings):
    # #3302: body-as-root is supported.
    for tag in (
        "<html><body dj-root><p>x</p></body></html>",
        '<html><body dj-view="a.V"><header>h</header><main>m</main></body></html>',
    ):
        assert _scan(tmp_path, settings, tag) == []


def test_does_not_fire_on_div_root_inside_body(tmp_path, settings):
    assert (
        _scan(
            tmp_path,
            settings,
            '<html><body><div dj-root dj-view="a.V"><p>x</p></div></body></html>',
        )
        == []
    )


def test_ignores_examples_inside_verbatim(tmp_path, settings):
    assert (
        _scan(
            tmp_path,
            settings,
            '<div>{% verbatim %}<html dj-view="a.V">{% endverbatim %}</div>',
        )
        == []
    )


@pytest.mark.parametrize("ident", ["T025", "djust.T025"])
def test_suppressible(tmp_path, settings, ident):
    settings.DJUST_CONFIG = {"suppress_checks": [ident]}
    assert _scan(tmp_path, settings, '<html dj-view="a.V"><body><p>x</p></body></html>') == []


def test_t002_no_longer_reassures_for_a_document_root(tmp_path, settings):
    # The shape is T025's now: T002 ("the page still connects") stays silent.
    found = _scan(
        tmp_path,
        settings,
        '<html dj-view="app.V"><body><p>a</p><p>b</p></body></html>',
        ids=("djust.T002", "djust.T025"),
    )
    assert [e.id for e in found] == ["djust.T025"]


def test_t002_for_a_root_inside_body_limits_its_reassurance(tmp_path, settings):
    found = _scan(
        tmp_path,
        settings,
        '<html><body><section dj-view="app.V"><p>a</p></section></body></html>',
        ids=("djust.T002", "djust.T025"),
    )
    assert [e.id for e in found] == ["djust.T002"]
    assert "inside <body>" in found[0].msg
    assert os.path.basename(found[0].msg.split(" -- ")[0]) == "t.html"


# -- text a browser never reads as markup, and the noqa pragma ---------------


@pytest.mark.parametrize(
    "body",
    [
        "<!-- <html dj-root> -->\n<div><p>x</p></div>",
        "<!--\n<html dj-view='a.V'>\n--><div>x</div>",
        '<div></div><script>var s = "<html dj-root>";</script>',
        "<script type='text/x'>\n<html dj-view='a.V'>\n</script><div>x</div>",
        # A browser ends a script at `</script` plus whitespace and junk...
        "<script>\n<html dj-root>\n</script\t\n bar><div>x</div>",
        "<script>\n<html dj-root>\n</SCRIPT/><div>x</div>",
        # ...and a comment at `--!>` as well as `-->`.
        "<!-- <html dj-root> --!><div>x</div>",
    ],
)
def test_comments_and_script_bodies_are_not_markup(tmp_path, settings, body):
    assert _scan(tmp_path, settings, body) == []


def test_a_script_tag_itself_is_still_read(tmp_path, settings):
    found = _scan(tmp_path, settings, "<html dj-root><script>var a = 1;</script></html>")
    assert len(found) == 1


def test_an_unterminated_comment_runs_to_the_end(tmp_path, settings):
    assert _scan(tmp_path, settings, "<div>x</div><!-- <html dj-root>") == []


def test_line_numbers_survive_blanking(tmp_path, settings):
    found = _scan(
        tmp_path,
        settings,
        "<!--\nline two\n-->\n<script>\nvar a;\n</script>\n<html dj-root>\n<p>x</p></html>",
    )
    assert len(found) == 1 and ":7" in found[0].msg


def test_t002_still_fires_when_a_comment_mentions_a_document_root(tmp_path, settings):
    found = _scan(
        tmp_path,
        settings,
        '<!-- <html dj-view="x.V"> --><section dj-view="app.V"><p>a</p></section>',
        ids=("djust.T002", "djust.T025"),
    )
    assert [e.id for e in found] == ["djust.T002"]


def test_noqa_with_a_reason_suppresses_one_match(tmp_path, settings):
    found = _scan(
        tmp_path,
        settings,
        "{# noqa: T025 -- single-child page, mounts whole #}\n<html dj-root><body><p>x</p></body></html>",
    )
    assert found == []


def test_noqa_on_the_same_line_suppresses(tmp_path, settings):
    assert (
        _scan(tmp_path, settings, "<html dj-root>{# noqa: T025 -- legacy shell #}<p>x</p></html>")
        == []
    )


def test_noqa_without_a_reason_does_not_suppress(tmp_path, settings):
    found = _scan(tmp_path, settings, "{# noqa: T025 #}\n<html dj-root><p>x</p></html>")
    assert len(found) == 1
    assert "without a reason" in found[0].hint


def test_noqa_for_another_check_does_not_suppress(tmp_path, settings):
    found = _scan(tmp_path, settings, "{# noqa: T024 -- other #}\n<html dj-root><p>x</p></html>")
    assert len(found) == 1


def test_script_closing_needs_a_tag_name_boundary(tmp_path, settings):
    # `</scriptx>` does not end the script, so the string literal stays hidden.
    body = '<script>"<html dj-root>"</scriptx></script><div>x</div>'
    assert _scan(tmp_path, settings, body) == []


def test_markup_after_a_terminated_comment_and_script_is_still_read(tmp_path, settings):
    body = "<!-- c --!><script>var a;</script\t><html dj-root><p>x</p></html>"
    assert len(_scan(tmp_path, settings, body)) == 1


# -- linear on unterminated input (a nested-quantifier regexp took 14-43 s) ---


@pytest.mark.parametrize(
    "markup",
    [
        "<script>\n" * 20000,
        "<script \n" * 20000,
        "<!-- \n" * 20000,
        "<html x='\n" * 20000,
        '<html x="\n' * 20000,
        "<html {% if \n" * 20000,
        "<html {{ \n" * 20000,
        "</script \n" * 20000,
    ],
)
def test_unterminated_input_is_scanned_in_one_pass(markup):
    started = time.perf_counter()
    _find_document_roots(_document_root_scan_text(markup))
    assert time.perf_counter() - started < 3.0


def test_message_says_it_is_unsupported_and_ignored_and_what_to_do(tmp_path, settings):
    # The fail-safe: the attribute is ignored, nothing mounts, the page stays HTTP.
    found = _scan(tmp_path, settings, '<html dj-view="a.V"><body><p>x</p></body></html>')
    assert len(found) == 1
    msg = found[0].msg
    assert "not supported and is ignored" in msg
    assert "no live view is mounted" in msg and "plain HTTP page" in msg
    assert "Put it on <body> or a <div>" in msg
    assert "<body>" in found[0].hint
