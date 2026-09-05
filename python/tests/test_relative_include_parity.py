"""Origin-aware include paths, including inherited and nested definitions."""

import pytest
from django.template.backends.django import DjangoTemplates
from django.template import TemplateDoesNotExist
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "name,files,context",
    [
        (
            "dir/page.html",
            {
                "dir/page.html": '{% include "./part.html" %}',
                "dir/part.html": "literal",
                "wrong.html": "wrong",
            },
            {"./part.html": "wrong.html"},
        ),
        (
            "dir/page.html",
            {
                "dir/page.html": '{% include target|default:"./part.html" %}',
                "dir/part.html": "filtered",
            },
            {},
        ),
        (
            "dir/page.html",
            {"dir/page.html": '{% include "./part.html" %}', "dir/part.html": "local"},
            {},
        ),
        (
            "dir/page.html",
            {"dir/page.html": "{% include target %}", "dir/part.html": "variable"},
            {"target": "./part.html"},
        ),
        (
            "dir/sub/page.html",
            {"dir/sub/page.html": '{% include "../../part.html" %}', "part.html": "root"},
            {},
        ),
        (
            "dir/page.html",
            {
                "dir/page.html": '{% include "./sub/part.html" %}',
                "dir/sub/part.html": '{% include "../leaf.html" %}',
                "dir/leaf.html": "nested",
            },
            {},
        ),
        (
            "child/page.html",
            {
                "child/page.html": '{% extends "base/page.html" %}',
                "base/page.html": '{% include "./part.html" %}',
                "base/part.html": "base",
            },
            {},
        ),
        (
            "child/page.html",
            {
                "child/page.html": '{% extends "base/page.html" %}{% block c %}{% include "./part.html" %}{% endblock %}',
                "base/page.html": "{% block c %}base{% endblock %}",
                "child/part.html": "child",
            },
            {},
        ),
        (
            "dir/page.html",
            {
                "dir/page.html": '{% if nested %}leaf{% else %}{% include "./page.html" with nested=True only %}{% endif %}'
            },
            {},
        ),
        (
            "dir/page.html",
            {"dir/page.html": "{% include target %}", "dir/part.html": "list"},
            {"target": ["./part.html"]},
        ),
    ],
)
def test_relative_include_origin(tmp_path, name, files, context):
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    django = DjangoTemplates(params)
    djust = DjustTemplateBackend(params)
    if isinstance(context.get("target"), list):
        # Django normalizes string operands, not each entry in a list.
        for engine in (django, djust):
            with pytest.raises(TemplateDoesNotExist):
                engine.get_template(name).render(context)
    else:
        expected = django.get_template(name).render(context)
        assert djust.get_template(name).render(context) == expected


def test_cached_source_keeps_distinct_template_origins(tmp_path):
    for folder in ["one", "two"]:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "page.html").write_text('{% include "./part.html" %}')
        (tmp_path / folder / "part.html").write_text(folder)
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    backend = DjustTemplateBackend(params)
    for name in ["one", "two", "one", "two"]:
        assert backend.get_template(name + "/page.html").render({}) == name


@pytest.mark.parametrize(
    "tag,target,literal",
    [
        ("include", "../outside.html", True),
        ("include", "../outside.html", False),
        ("extends", "../outside.html", True),
        ("extends", "../outside.html", False),
        ("extends", "./page.html", True),
        ("include", "./page.html", False),
    ],
)
def test_relative_path_errors_match_django(tmp_path, tag, target, literal):
    from django.template import TemplateSyntaxError

    operand = '"' + target + '"' if literal else "target"
    (tmp_path / "page.html").write_text("{% " + tag + " " + operand + " %}")
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    expected_type = (
        TemplateDoesNotExist if tag == "extends" and not literal else TemplateSyntaxError
    )
    messages = []
    for engine in [DjangoTemplates(params), DjustTemplateBackend(params)]:
        with pytest.raises(expected_type) as error:
            engine.get_template("page.html").render({"target": target})
        messages.append(str(error.value))
    assert messages[0] == messages[1]


@pytest.mark.parametrize(
    "source",
    [
        '{% include "../outside.html" %}',
        '{% if never %}{% include "../outside.html" %}{% endif %}',
        '{% extends "../outside.html" %}',
        '{% extends "./page.html" %}',
    ],
)
def test_literal_relative_errors_refuse_at_load(tmp_path, source):
    from django.template import TemplateSyntaxError

    (tmp_path / "page.html").write_text(source)
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    messages = []
    for engine in [DjangoTemplates(params), DjustTemplateBackend(params)]:
        with pytest.raises(TemplateSyntaxError) as error:
            engine.get_template("page.html")
        messages.append(str(error.value))
    assert messages[0] == messages[1]


@pytest.mark.parametrize("valid_first", [False, True])
def test_origin_validation_cannot_be_bypassed_by_source_cache(tmp_path, valid_first):
    from django.template import TemplateSyntaxError

    (tmp_path / "nested").mkdir()
    source = '{% include "../part.html" %}'
    (tmp_path / "nested/page.html").write_text(source)
    (tmp_path / "page.html").write_text(source)
    (tmp_path / "part.html").write_text("valid")
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    backend = DjustTemplateBackend(params)
    for valid in [valid_first, not valid_first]:
        if valid:
            assert backend.get_template("nested/page.html").render({}) == "valid"
        else:
            with pytest.raises(TemplateSyntaxError):
                backend.get_template("page.html")


def test_loaded_parent_validates_even_overridden_block(tmp_path):
    from django.template import TemplateSyntaxError

    (tmp_path / "base.html").write_text(
        '{% block c %}{% include "../outside.html" %}{% endblock %}'
    )
    (tmp_path / "child.html").write_text(
        '{% extends "base.html" %}{% block c %}replacement{% endblock %}'
    )
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    for engine in [DjangoTemplates(params), DjustTemplateBackend(params)]:
        child = engine.get_template("child.html")
        with pytest.raises(TemplateSyntaxError):
            child.render({})


@pytest.mark.parametrize("tag", ["include", "extends"])
def test_escaped_quote_in_relative_literal_filename(tmp_path, tag):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir/page.html").write_text(r'{% TAG "./say\"hello.html" %}'.replace("TAG", tag))
    (tmp_path / 'dir/say"hello.html').write_text("escaped")
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    expected = DjangoTemplates(params).get_template("dir/page.html").render({})
    assert DjustTemplateBackend(params).get_template("dir/page.html").render({}) == expected


def test_include_candidate_list_uses_first_available_name(tmp_path):
    (tmp_path / "part.html").write_text("selected")
    source = "{% include choices %}"
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    data = {"choices": ["missing.html", "part.html"]}
    expected = DjangoTemplates(params).from_string(source).render(data)
    assert DjustTemplateBackend(params).from_string(source).render(data) == expected
