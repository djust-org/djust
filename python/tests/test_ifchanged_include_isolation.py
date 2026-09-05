"""Include state follows template identity and Django's render/loop boundaries."""

import pytest
from django.template import Context, Engine
from djust import _rust
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "tag",
    ["{% ifchanged %}{{ x }}{% endifchanged %}", "{% ifchanged x %}{{ x }}{% endifchanged %}"],
)
@pytest.mark.parametrize("second", ["a.html", "b.html"])
@pytest.mark.parametrize("only", ["", " with x=x only"])
@pytest.mark.parametrize("loop", [False, True])
@pytest.mark.parametrize("entry", ["backend", "rust"])
def test_include_state_matches_cached_django(tmp_path, tag, second, only, loop, entry):
    for name in ["a.html", "b.html"]:
        (tmp_path / name).write_text(tag)
    source = '{% include "a.html"' + only + ' %}{% include "' + second + '"' + only + " %}"
    if loop:
        source = "{% for x in xs %}" + source + "{% endfor %}"
    data = {"x": 1, "xs": [1, 1, 2, 2, 3, 3]}
    expected = Engine(dirs=[tmp_path]).from_string(source).render(Context(data))
    backend = DjustTemplateBackend(
        {"NAME": "ifchanged", "DIRS": [tmp_path], "APP_DIRS": False, "OPTIONS": {}}
    )
    for _ in range(2):
        if entry == "backend":
            actual = backend.from_string(source).render(data)
        else:
            actual = _rust.render_template_with_dirs(source, data, [str(tmp_path)])
        assert actual == expected


def test_inherited_identical_parent_bodies_keep_separate_state(tmp_path):
    for name in ["a.html", "b.html"]:
        (tmp_path / name).write_text(
            "{% block body %}{% ifchanged x %}{{ x }}{% endifchanged %}{% endblock %}"
        )
    for name, parent in [("first.html", "a.html"), ("second.html", "b.html")]:
        (tmp_path / name).write_text('{% extends "' + parent + '" %}')
    source = '{% for x in xs %}{% include "first.html" %}{% include "second.html" %}{% endfor %}'
    data = {"xs": [1, 1, 2, 2, 3, 3]}
    expected = Engine(dirs=[tmp_path]).from_string(source).render(Context(data))
    assert expected == "112233"
    actual = _rust.render_template_with_dirs(source, data, [str(tmp_path)])
    assert actual == expected
