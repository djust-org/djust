"""Django's missing filter-argument exception survives every render boundary."""

import django
import pytest
from django.template import Context, Engine, VariableDoesNotExist

from djust import _rust
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "source",
    [
        "{{ blank|default:missing }}",
        "{% with value=blank|default:missing %}{{ value }}{% endwith %}",
        "{% for value in blank|default:missing %}{{ value }}{% endfor %}",
    ],
)
def test_missing_filter_argument_propagates_django_exception(source):
    context = {"blank": ""}
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    view = _rust.RustLiveView(source, [])
    view.update_state(context)
    for render in (
        lambda: Engine().from_string(source).render(Context(context)),
        lambda: _rust.render_template(source, context),
        lambda: _rust.render_template_with_dirs(source, context, []),
        lambda: backend.from_string(source).render(context),
        view.render,
        view.render_with_diff,
    ):
        with pytest.raises(VariableDoesNotExist):
            render()


@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django partials require Django 6")
def test_missing_filter_argument_inside_partial_propagates_django_exception():
    source = "{% partialdef item %}{{ blank|default:missing }}{% endpartialdef %}{% partial item %}"
    test_missing_filter_argument_propagates_django_exception(source)


def test_if_still_catches_missing_filter_argument():
    source = "{% if blank|default:missing %}yes{% else %}no{% endif %}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    assert backend.from_string(source).render({"blank": ""}) == "no"


def test_missing_filter_argument_in_include_preserves_loaded_template_location(tmp_path):
    (tmp_path / "card.html").write_text("<p>{{ blank|default:missing }}</p>", encoding="utf-8")
    backend = DjustTemplateBackend(
        {
            "NAME": "test",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {"debug": True},
        }
    )
    template = backend.from_string('{% include "card.html" %}')
    with pytest.raises(VariableDoesNotExist) as raised:
        template.render({"blank": ""})
    assert raised.value.template_debug["name"] == str(tmp_path / "card.html")
    assert raised.value.template_debug["during"] == "{{ blank|default:missing }}"
    assert "Failed lookup" in raised.value.template_debug["message"]


@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django partials require Django 6")
def test_recursive_partial_backend_preserves_recursion_error():
    source = "{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    with pytest.raises(RecursionError):
        backend.from_string(source).render({})
