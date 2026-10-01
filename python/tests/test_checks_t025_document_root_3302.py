"""Tests for djust system check T025 (#3302).

T025 (WARNING) flags ``dj-view`` / ``dj-root`` on ``<html>``, ``<head>`` or
``<body>``. The HTTP render of such a page is complete, but the WebSocket
mount looks for the root *inside* ``<body>`` and otherwise falls back to the
first element child, so a page with two body children mounts with only the
first one. T002 used to say this shape was fine ("the page still connects").
"""

import os

import pytest

from djust.checks import _DJ_DOCUMENT_ROOT_RE


@pytest.mark.parametrize("tag", ["html", "head", "body"])
@pytest.mark.parametrize("attr", ["dj-view", "dj-root"])
def test_regex_matches_document_elements(tag, attr):
    assert _DJ_DOCUMENT_ROOT_RE.search('<%s lang="en" %s="x.V">' % (tag, attr)) is not None


def test_regex_is_case_insensitive_and_tolerates_bare_attribute():
    assert _DJ_DOCUMENT_ROOT_RE.search("<BODY DJ-ROOT>") is not None


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
    ],
)
def test_regex_ignores_everything_else(markup):
    assert _DJ_DOCUMENT_ROOT_RE.search(markup) is None


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


def test_fires_on_body_root_and_names_the_line(tmp_path, settings):
    found = _scan(tmp_path, settings, "<html>\n<body dj-root>\n<p>x</p></body></html>")
    assert len(found) == 1
    assert "<body>" in found[0].msg
    assert ":2" in found[0].msg


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
    assert _scan(tmp_path, settings, '<body dj-view="a.V"><p>x</p></body>') == []


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
