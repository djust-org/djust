"""``ForeignKeySelect`` and ``ManyToManySelect`` HTML-escape option labels and
values read from model instances, and the other values they interpolate.

A label or value that is marked safe is emitted as-is. Each case runs under
every CSS-framework renderer (both components render Tailwind markup for
``tailwind`` and Bootstrap markup otherwise) and, for ``ManyToManySelect``,
under both displays.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils.safestring import mark_safe

from djust import config as config_module
from djust.components.forms.foreign_key import ForeignKeySelect, ManyToManySelect

FRAMEWORKS = ("bootstrap5", "tailwind", "plain")
DISPLAYS = ("checkboxes", "select")

MARKUP = "</option></select><img src=x onerror=alert(1)>"
MARKUP_ESCAPED = "&lt;/option&gt;&lt;/select&gt;&lt;img src=x onerror=alert(1)&gt;"
QUOTED = 'x" onmouseover="y'
QUOTED_ESCAPED = "x&quot; onmouseover=&quot;y"
SAFE = "<b>ok</b>"


def _render(component, framework: str) -> str:
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(component.render())


def _fk(label="Ada", value="1", **kwargs):
    component = ForeignKeySelect(name="author", label="Author", **kwargs)
    component.get_options = lambda: [{"value": value, "label": label}]
    return component


def _m2m(label="Ada", value="1", display="checkboxes", **kwargs):
    component = ManyToManySelect(name="tags", label="Tags", display=display, **kwargs)
    component.get_options = lambda: [{"value": value, "label": label, "selected": False}]
    return component


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_fk_option_label_is_html_escaped(framework):
    html = _render(_fk(label=MARKUP), framework)
    assert MARKUP not in html
    assert MARKUP_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_fk_option_value_is_html_escaped(framework):
    html = _render(_fk(value=QUOTED), framework)
    assert QUOTED not in html
    assert f'value="{QUOTED_ESCAPED}"' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize(
    "field", ["label", "help_text", "validation_message", "empty_label", "name"]
)
def test_fk_field_values_are_html_escaped(framework, field):
    component = _fk()
    setattr(component, field, MARKUP)
    html = _render(component, framework)
    assert MARKUP not in html
    assert MARKUP_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_fk_search_query_is_html_escaped(framework):
    component = _fk()
    component.searchable = True
    component.search_query = QUOTED
    html = _render(component, framework)
    assert QUOTED not in html
    assert QUOTED_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_fk_marked_safe_label_passes_through(framework):
    assert SAFE in _render(_fk(label=mark_safe(SAFE)), framework)


@pytest.mark.parametrize("framework", FRAMEWORKS)
def test_fk_plain_options_render_unchanged(framework):
    html = _render(_fk(label="Ada Lovelace", value=7), framework)
    assert '<option value="7">Ada Lovelace</option>' in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("display", DISPLAYS)
def test_m2m_option_label_is_html_escaped(framework, display):
    html = _render(_m2m(label=MARKUP, display=display), framework)
    assert MARKUP not in html
    assert MARKUP_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("display", DISPLAYS)
def test_m2m_option_value_is_html_escaped(framework, display):
    html = _render(_m2m(value=QUOTED, display=display), framework)
    assert QUOTED not in html
    assert QUOTED_ESCAPED in html


@pytest.mark.parametrize("framework", FRAMEWORKS)
@pytest.mark.parametrize("display", DISPLAYS)
def test_m2m_marked_safe_label_passes_through(framework, display):
    assert SAFE in _render(_m2m(label=mark_safe(SAFE), display=display), framework)


@pytest.mark.django_db
def test_fk_label_read_from_a_model_instance_is_html_escaped():
    from django.contrib.auth.models import User

    User.objects.create(username="u1", first_name=MARKUP)
    for component in (
        ForeignKeySelect(queryset=User.objects.all(), label_field="first_name"),
        ManyToManySelect(queryset=User.objects.all(), label_field="first_name"),
    ):
        html = str(component.render())
        assert MARKUP not in html
        assert MARKUP_ESCAPED in html
