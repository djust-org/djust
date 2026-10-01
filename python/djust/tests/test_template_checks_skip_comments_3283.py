"""#3283 -- template checks must not match markup inside template comments.

Each case drives the real check over a template written to disk: a pattern
that only appears inside ``{# #}`` or ``{% comment %}`` must not be reported,
the same pattern outside a comment must, and a finding after a comment keeps
its line number.
"""

import pytest

import time

from djust.checks import check_accessibility, check_inline_script_csp, check_templates
from djust.checks.assets import check_undeclared_origins
from djust.checks.configuration import _check_manual_client_js, _check_tailwind_cdn_in_production
from djust.checks.utils import _blank_template_comments


def _write(settings, tmp_path, html, name="page.html"):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(exist_ok=True)
    (tpl_dir / name).write_text(html)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]
    settings.MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]


def _ids(errors, check_id):
    return [e for e in errors if e.id == check_id]


COMMENTS = [
    pytest.param("{# <input name='q'> #}", id="inline"),
    pytest.param("{% comment %}\n<input name='q'>\n{% endcomment %}", id="block"),
    pytest.param("{% comment 'why' %}<input name='q'>{% endcomment %}", id="block-with-note"),
]


@pytest.mark.parametrize("comment", COMMENTS)
def test_y003_ignores_an_input_inside_a_comment(tmp_path, settings, comment):
    _write(settings, tmp_path, "<p>x</p>\n%s\n" % comment)
    assert _ids(check_accessibility(None), "djust.Y003") == []


def test_y003_still_flags_the_same_input_outside_a_comment(tmp_path, settings):
    _write(settings, tmp_path, "{# note #}\n<input name='q'>\n")
    assert len(_ids(check_accessibility(None), "djust.Y003")) == 1


def test_a_finding_after_a_multiline_comment_keeps_its_line_number(tmp_path, settings):
    _write(
        settings,
        tmp_path,
        "{% comment %}\nline 2\nline 3\n{% endcomment %}\n<input name='q'>\n",
    )
    (y003,) = _ids(check_accessibility(None), "djust.Y003")
    assert y003.line_number == 5


@pytest.mark.parametrize(
    "comment",
    [
        pytest.param("{# <div dj-root> #}", id="inline"),
        pytest.param("{% comment %}<div dj-root>{% endcomment %}", id="block"),
    ],
)
def test_s011_ignores_dj_root_and_script_inside_comments(tmp_path, settings, comment):
    """A comment naming dj-root must not make the file a LiveView template."""
    _write(settings, tmp_path, "%s\n<script>var x = 1;</script>\n" % comment)
    assert _ids(check_inline_script_csp(None), "djust.S011") == []


def test_s011_ignores_a_script_inside_a_comment_within_the_real_root(tmp_path, settings):
    _write(
        settings,
        tmp_path,
        "<div dj-root>\n{% comment %}<script>var x = 1;</script>{% endcomment %}\n</div>\n",
    )
    assert _ids(check_inline_script_csp(None), "djust.S011") == []


def test_s011_still_flags_a_real_script_in_the_root(tmp_path, settings):
    _write(settings, tmp_path, "{# a note #}\n<div dj-root>\n<script>var x = 1;</script>\n</div>\n")
    assert len(_ids(check_inline_script_csp(None), "djust.S011")) == 1


def test_t001_ignores_a_deprecated_attribute_inside_a_comment(tmp_path, settings):
    _write(settings, tmp_path, "<div dj-root>{# old: <a @click='go'> #}</div>\n")
    assert _ids(check_templates(None), "djust.T001") == []
    _write(settings, tmp_path, "<div dj-root><a @click='go'>x</a></div>\n")
    assert len(_ids(check_templates(None), "djust.T001")) == 1


def test_pragmas_in_comments_are_still_honoured(tmp_path, settings):
    """``{# djust:partial #}`` and ``{# noqa: T003 #}`` live in comments; blanking
    must not hide them."""
    unrooted = '<button dj-click="go">x</button>\n'
    _write(settings, tmp_path, unrooted)
    assert _ids(check_templates(None), "djust.T012"), "fixture must trigger T012"
    _write(settings, tmp_path, "{# djust:partial #}\n" + unrooted)
    assert _ids(check_templates(None), "djust.T012") == []

    wrapper = '{% extends "base.html" %}{% block content %}{% include "liveview_x.html" %}{% endblock %}\n'
    _write(settings, tmp_path, wrapper + "<div dj-root></div>")
    assert _ids(check_templates(None), "djust.T003"), "fixture must trigger T003"
    _write(settings, tmp_path, "{# noqa: T003 #}\n" + wrapper + "<div dj-root></div>")
    assert _ids(check_templates(None), "djust.T003") == []


def test_a_comment_naming_dj_click_does_not_trigger_t012(tmp_path, settings):
    _write(settings, tmp_path, '{# <button dj-click="go"> #}\n<p>x</p>\n')
    assert _ids(check_templates(None), "djust.T012") == []


def test_blanking_preserves_length_and_newlines():
    source = "a {# x #} b\n{% comment %}\nq\n{% endcomment %}\nc {# multi\nline #} d"
    blanked = _blank_template_comments(source)
    assert len(blanked) == len(source)
    assert [i for i, ch in enumerate(blanked) if ch == "\n"] == [
        i for i, ch in enumerate(source) if ch == "\n"
    ]
    assert "x" not in blanked and "q" not in blanked
    # Django renders a multi-line {# #} as literal text, so it is not a comment.
    assert "multi" in blanked


def test_a_comment_tag_note_may_contain_a_percent_sign():
    """Django's lexer closes the tag at the first ``%}``; a quoted ``%`` is no exception."""
    blanked = _blank_template_comments('a {% comment "100% sure" %}<input>{% endcomment %} b')
    assert "input" not in blanked and blanked.startswith("a ") and blanked.endswith(" b")


def test_blanking_keeps_every_line_break_the_scans_split_on():
    source = "x {# a\r b #} {% comment %}\x0b\r\n{% endcomment %} y"
    assert len(_blank_template_comments(source).splitlines()) == len(source.splitlines())


@pytest.mark.parametrize(
    "source",
    [
        "{# x " * 50_000,
        "{% comment %}" * 20_000,
        "{# x {% comment %}" * 20_000,
        "{% comment " * 30_000,
        "{# x\n" * 50_000 + "#}",
    ],
    ids=["hash", "comment-open", "mixed", "comment-no-close", "hash-lines"],
)
def test_blanking_is_linear_on_unterminated_openers(source):
    start = time.perf_counter()
    _blank_template_comments(source)
    assert time.perf_counter() - start < 2


# -- the sibling scanners that read template source (C010, C012, B010) ----------

_COMMENTED_ASSETS = (
    "{# <script src=\"{% static 'djust/client.js' %}\"></script> #}\n"
    '{# <script src="https://cdn.tailwindcss.com"></script> #}\n'
    "{% comment %}\n"
    "<script src=\"{% static 'djust/client.js' %}\"></script>\n"
    '<script src="https://cdn.tailwindcss.com"></script>\n'
    "{% endcomment %}\n"
)
_LIVE_ASSETS = (
    "<script src=\"{% static 'djust/client.js' %}\"></script>\n"
    '<script src="https://cdn.tailwindcss.com"></script>\n'
)


def test_c010_and_c012_ignore_comments_and_still_fire_outside_them(tmp_path, settings):
    _write(settings, tmp_path, _COMMENTED_ASSETS, name="base.html")
    found = []
    _check_tailwind_cdn_in_production(found)
    _check_manual_client_js(found)
    assert found == []

    _write(settings, tmp_path, _COMMENTED_ASSETS + _LIVE_ASSETS, name="base.html")
    _check_tailwind_cdn_in_production(found)
    _check_manual_client_js(found)
    assert sorted(m.id for m in found) == ["djust.C010", "djust.C012"]
    (c012,) = [m for m in found if m.id == "djust.C012"]
    assert c012.line_number == 7  # lines after the comments keep their numbers


def test_b010_ignores_comments_but_reads_its_noqa_from_them(tmp_path, settings):
    _write(
        settings,
        tmp_path,
        '{# <script src="https://cdn.other.example/x.js"></script> #}\n'
        '<script src="https://cdn.other.example/y.js"></script> {# noqa: B010 #}\n'
        '{% comment %}<link rel="stylesheet" href="https://cdn.other.example/z.css">{% endcomment %}\n'
        '<script src="https://cdn.other.example/live.js"></script>\n',
    )
    found = check_undeclared_origins(None)
    assert [m.id for m in found] == ["djust.B010"]
    assert "page.html:4" in found[0].msg
