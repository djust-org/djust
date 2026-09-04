"""URL operands resolve once, retaining Django types and request namespaces."""

import pytest
from django.http import HttpResponse
from django.template import Context, Engine, RequestContext, TemplateSyntaxError
from django.test import RequestFactory, override_settings
from django.urls import include, path, resolve

from djust import _rust
from djust.template import DjustTemplateBackend


def view(request, value=""):
    return HttpResponse(value)


app_patterns = [path("<str:value>/", view, name="detail")]
urlpatterns = [
    path("item/<str:value>/", view, name="item"),
    path("one/", include((app_patterns, "app"), namespace="one")),
    path("two/", include((app_patterns, "app"), namespace="two")),
]


def backend():
    return DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})


@pytest.mark.parametrize("entry", ["backend", "native", "liveview"])
@pytest.mark.parametrize(
    "source,context",
    [
        ("{% url name value %}", {"name": "item", "item": {"wrong": 1}, "value": "ok"}),
        ("{% url 'item' value %}", {"value": "other", "other": "wrong"}),
        ("{% url 'item' value=value %}", {"value": "other", "other": "wrong"}),
        ("{% url 'item' value %}", {"value": "001"}),
        ("{% url 'item' value %}", {"value": None}),
        ("{% url 'item' value %}", {"value": "x=y"}),
        (
            "{% with value='inner' %}{% url 'item' value %}{% endwith %}{% url 'item' value %}",
            {"value": "outer"},
        ),
        (
            "{% for value in values %}{% url 'item' value %}{% endfor %}{% url 'item' value %}",
            {"value": "outer", "values": ["a", "b"]},
        ),
        ("{% url 'item' 'first' as value %}{% url 'item' value|length %}", {"value": "outer"}),
    ],
)
def test_url_operands_resolve_once(entry, source, context):
    with override_settings(ROOT_URLCONF=__name__):
        expected = Engine().from_string(source).render(Context(context))
        if entry == "backend":
            actual = backend().from_string(source).render(context)
        elif entry == "native":
            actual = _rust.render_template_with_dirs(source, context, [])
        else:
            liveview = _rust.RustLiveView(source, [])
            liveview.set_raw_py_values(context)
            actual = liveview.render()
        assert actual == expected


@pytest.mark.parametrize("current_app", ["absent", None, "one", "two"])
def test_request_namespace_matches_django(current_app):
    source = "{% url 'app:detail' 'x' %}"
    with override_settings(ROOT_URLCONF=__name__):
        request = RequestFactory().get("/one/x/")
        request.resolver_match = resolve("/one/x/")
        if current_app != "absent":
            request.current_app = current_app
        expected = Engine().from_string(source).render(RequestContext(request))
        assert backend().from_string(source).render(RequestContext(request)) == expected


def test_empty_url_fails_when_compiling():
    with pytest.raises(TemplateSyntaxError):
        backend().from_string("{% url %}")
