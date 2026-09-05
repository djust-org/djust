"""Django Context formatting flags survive inline and nested inclusion tags."""

import pytest
from django.template import Context, Engine, Library
from djust.template import DjustTemplateBackend

register = Library()


@register.simple_tag(takes_context=True)
def show_flags(context):
    return f"{context.use_l10n}/{context.use_tz}"


@register.inclusion_tag("flags.html")
def include_flags():
    return {}


@pytest.mark.parametrize("use_l10n", [None, False, True])
@pytest.mark.parametrize("use_tz", [None, False, True])
def test_format_flags_reach_nested_tags_and_do_not_leak(tmp_path, use_l10n, use_tz):
    libraries = {"flags": __name__}
    (tmp_path / "flags.html").write_text("{% load flags %}{% show_flags %}")
    source = "{% load flags %}{% show_flags %}|{% include_flags %}|{% show_flags %}"
    django_template = Engine(dirs=[str(tmp_path)], libraries=libraries).from_string(source)
    backend = DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": libraries},
        }
    )
    template = backend.from_string(source)
    expected = django_template.render(Context({}, use_l10n=use_l10n, use_tz=use_tz))
    assert template.render(Context({}, use_l10n=use_l10n, use_tz=use_tz)) == expected
    assert template.render({}) == django_template.render(Context({}))
