"""Errors compiling loaded templates retain the failing source and exception."""

import pytest
from django.template import Engine, Context, Library, TemplateSyntaxError
from djust.template import DjustTemplateBackend

register = Library()
raised = []


@register.tag
def badtag(parser, token):
    error = TemplateSyntaxError("compiler failed")
    raised.append(error)
    raise error


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("relationship", ["include", "extends"])
@pytest.mark.parametrize("tag", ["{% badtag %}", "{% unknown_tag %}"])
def test_loaded_compile_error_location_matches_django(tmp_path, debug, relationship, tag):
    source = "{% load failures %}\nUnicode café α\n" + tag + "\n"
    (tmp_path / "loaded.html").write_text(source)
    root = "{% " + relationship + ' "loaded.html" %}'
    libraries = {"failures": __name__}
    engines = [
        Engine(dirs=[tmp_path], libraries=libraries, debug=debug),
        DjustTemplateBackend(
            {
                "NAME": "debug",
                "DIRS": [tmp_path],
                "APP_DIRS": False,
                "OPTIONS": {"libraries": libraries, "debug": debug},
            }
        ),
    ]
    errors = []
    for engine in engines:
        template = engine.from_string(root)
        with pytest.raises(TemplateSyntaxError) as caught:
            template.render(Context())
        error = caught.value
        if tag == "{% badtag %}":
            assert error is raised[-1]
        errors.append(error)
    if debug:
        for key in ["name", "line", "during", "start", "end", "source_lines"]:
            assert errors[1].template_debug[key] == errors[0].template_debug[key]
        assert errors[1].template_debug["during"] == tag
    else:
        assert not getattr(errors[1], "template_debug", None)


def test_nested_include_reports_innermost_source(tmp_path):
    (tmp_path / "middle.html").write_text('{% include "bad.html" %}')
    (tmp_path / "bad.html").write_text("{% load failures %}\n{% badtag %}")
    backend = DjustTemplateBackend(
        {
            "NAME": "debug",
            "DIRS": [tmp_path],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": {"failures": __name__}, "debug": True},
        }
    )
    with pytest.raises(TemplateSyntaxError) as caught:
        backend.from_string('{% include "middle.html" %}').render({})
    assert caught.value is raised[-1]
    assert caught.value.template_debug["name"] == str(tmp_path / "bad.html")
    assert caught.value.template_debug["during"] == "{% badtag %}"
