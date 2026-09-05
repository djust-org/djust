"""URL reversal and escaping happen at the node's actual render position (#2616)."""

import re

import pytest
from django.http import HttpResponse
from django.template import Context, Engine
from django.test import override_settings
from django.urls import path

from djust import _rust
from djust.template import DjustTemplateBackend

urlpatterns = [path("u/<str:value>/", lambda request, value: HttpResponse(value), name="u")]


def render(source, context, entry):
    if entry == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        return backend.from_string(source).render(dict(context))
    if entry == "native":
        return _rust.render_template_with_dirs(source, dict(context), [])
    view = _rust.RustLiveView(source, [])
    view.set_raw_py_values(dict(context))
    # LiveView wraps conditionals in reconciliation markers.
    return re.sub(r"<!--/?dj-if\b.*?-->", "", view.render())


@pytest.mark.parametrize("entry", ["backend", "native", "liveview"])
@pytest.mark.parametrize(
    "source",
    [
        "{% url 'u' value %}",
        "{% autoescape off %}{% url 'u' value %}{% endautoescape %}",
        "{% autoescape off %}{% autoescape on %}{% url 'u' value %}{% endautoescape %}{% endautoescape %}",
        "{% if missing %}{% url 'unknown-name' %}{% else %}ok{% endif %}",
        "{% comment %}{% url 'unknown-name' %}{% endcomment %}",
        "{% verbatim %}{% url 'unknown-name' %}{% endverbatim %}",
        "[{{ target }}]{% url 'u' value as target %}[{{ target }}]",
        "{% with value='inner' %}{% url 'u' value %}{% endwith %}",
    ],
)
def test_url_uses_render_scope(entry, source):
    context = {"value": "&lt;b&gt;", "target": "before"}
    with override_settings(ROOT_URLCONF=__name__):
        expected = Engine().from_string(source).render(Context(dict(context)))
        assert render(source, context, entry) == expected
