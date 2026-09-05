"""Block discovery and replacement traverse every native body container."""

import pytest
from django.template import Context, Engine, Library

from djust.template import DjustTemplateBackend

register = Library()


@register.simple_block_tag
def panel(content):
    return content


WRAPPERS = [
    ("{% spaceless %}", "{% endspaceless %}"),
    ("{% if value %}", "{% endif %}"),
    ("{% for item in items %}", "{% endfor %}"),
    ("{% for item in empty_items %}{% empty %}", "{% endfor %}"),
    ("{% with item=value %}", "{% endwith %}"),
    ("{% autoescape off %}", "{% endautoescape %}"),
    ("{% filter lower %}", "{% endfilter %}"),
    ("{% ifchanged value %}", "{% endifchanged %}"),
    (
        "{% for item in repeated %}{% ifchanged item %}first{% else %}",
        "{% endifchanged %}{% endfor %}",
    ),
    ("{% language 'fr' %}", "{% endlanguage %}"),
    ("{% localize off %}", "{% endlocalize %}"),
    ("{% localtime off %}", "{% endlocaltime %}"),
    ("{% timezone 'Europe/Paris' %}", "{% endtimezone %}"),
    ("{% panel %}", "{% endpanel %}"),
]


@pytest.mark.parametrize("prefix,suffix", WRAPPERS)
@pytest.mark.parametrize("wrapped", ["parent", "child"])
def test_wrapped_block_overrides_match_django(tmp_path, prefix, suffix, wrapped):
    libraries = {
        "i18n": "django.templatetags.i18n",
        "l10n": "django.templatetags.l10n",
        "tz": "django.templatetags.tz",
        "nesting": __name__,
    }
    loads = "{% load i18n l10n tz nesting %}"
    base = "{% block body %}parent{% endblock %}"
    child = "{% block body %}child{% endblock %}"
    if wrapped == "parent":
        base = prefix + base + suffix
    else:
        child = prefix + child + suffix
    (tmp_path / "base.html").write_text(loads + base)
    (tmp_path / "child.html").write_text('{% extends "base.html" %}' + loads + child)
    context = {"value": 1, "items": [1], "empty_items": [], "repeated": [1, 1]}
    expected = (
        Engine(dirs=[str(tmp_path)], libraries=libraries)
        .get_template("child.html")
        .render(Context(context))
    )
    backend = DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": libraries},
        }
    )
    assert backend.get_template("child.html").render(context) == expected
