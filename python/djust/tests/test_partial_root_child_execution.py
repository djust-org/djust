import django
import pytest
from django.test import override_settings, RequestFactory
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from djust import LiveView

pytestmark = pytest.mark.skipif(django.VERSION < (6, 0), reason="Django partial-root planning")
MOUNTS = []


class Shell(LiveView):
    template = "<span>SHELL</span>"

    def mount(self, request, **kwargs):
        MOUNTS.append("shell")


class RootChild(LiveView):
    template = "<span>CHILD</span>"

    def mount(self, request, **kwargs):
        MOUNTS.append("child")


@pytest.mark.parametrize("partial", [False, True])
def test_only_selected_root_children_mount(tmp_path, partial):
    MOUNTS.clear()
    child = '{% live_render "djust.tests.test_partial_root_child_execution.RootChild" %}'
    root = "<main dj-root>ROOT" + child + "</main>"
    if partial:
        root = "{% partialdef root %}" + root + "{% endpartialdef %}{% partial root %}"
    (tmp_path / "page.html").write_text(
        '{% load live_tags %}{% live_render "djust.tests.test_partial_root_child_execution.Shell" %}'
        + root
    )

    class Page(LiveView):
        template = '{% include "page.html" %}'

    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    req.session = SessionStore()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        page = Page()
        page.request = req
        html, _, _ = page.render_with_diff(req)
        assert MOUNTS == ["child"]
        assert "CHILD" in html and "SHELL" not in html


class RawOpener(LiveView):
    template = "{{ payload|safe }}"

    def mount(self, request, **kwargs):
        MOUNTS.append("opener")
        self.payload = "<textarea>"


def test_a_child_cannot_cause_an_inert_sibling_to_mount(tmp_path):
    from django.core.exceptions import ImproperlyConfigured

    MOUNTS.clear()
    (tmp_path / "page.html").write_text(
        "{% load live_tags %}<main dj-root>"
        '{% live_render "djust.tests.test_partial_root_child_execution.RawOpener" %}'
        '{% live_render "djust.tests.test_partial_root_child_execution.RootChild" %}'
        "</main>"
    )

    class Page(LiveView):
        template = '{% include "page.html" %}'

    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    req.session = SessionStore()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        page = Page()
        page.request = req
        try:
            page.render_with_diff(req)
        except ImproperlyConfigured:
            pass  # Refusing the malformed final root is also safe.
        assert MOUNTS == ["opener"]


@pytest.mark.parametrize(
    "container",
    ["template", "script", "textarea", "title", "noscript", "style", "xmp", "iframe", "noembed"],
)
def test_inert_children_do_not_mount(tmp_path, container):
    MOUNTS.clear()
    (tmp_path / "page.html").write_text(
        "{% load live_tags %}<main dj-root><" + container + ">"
        '{% live_render "djust.tests.test_partial_root_child_execution.RootChild" %}'
        "</" + container + "></main>"
    )

    class Page(LiveView):
        template = '{% include "page.html" %}'

    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    req.session = SessionStore()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        page = Page()
        page.request = req
        html, _, _ = page.render_with_diff(req)
        assert MOUNTS == []
        assert "CHILD" not in html


def test_bindings_after_a_deferred_child_keep_their_source_proof(tmp_path):
    MOUNTS.clear()
    (tmp_path / "page.html").write_text(
        "{% load live_tags %}<main dj-root>é😀"
        '{% live_render "djust.tests.test_partial_root_child_execution.RootChild" %}'
        '<input dj-model="name"><b>{{ name }}</b></main>'
    )

    class Page(LiveView):
        template = '{% include "page.html" %}'
        name = "before"

    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    req.session = SessionStore()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        page = Page()
        page.request = req
        html, _, _ = page.render_with_diff(req)
        assert MOUNTS == ["child"]
        assert "CHILD" in html and "é😀" in html
        assert "name" in page._dj_model_fields
        page.update_model(field="name", value="after")
        assert page.name == "after"


@pytest.mark.parametrize("change", ["duplicate", "rewrite", "consume"])
def test_changed_occurrences_are_rejected_before_any_mount(monkeypatch, change):
    from django.template import Context
    from djust._deferred_children import (
        defer_root_children,
        defer_child_render,
        materialize_deferred_children,
    )
    from djust._exposure import ExposureError
    from djust.templatetags import live_tags

    calls = []
    monkeypatch.setattr(
        live_tags,
        "live_render",
        lambda *args, **kwargs: calls.append("mounted") or "<div>child</div>",
    )
    parent = object()
    with pytest.raises(ExposureError, match="Deferred child occurrence changed"):
        with defer_root_children(parent, True):
            marker = defer_child_render(parent, Context(), "ignored.Child", {})
            assert marker is not None
            if change == "duplicate":
                marker = marker + marker
            elif change == "rewrite":
                marker = marker.replace("<div ", "<section ")
            else:
                marker = str(len(marker))
            materialize_deferred_children("<main dj-root>" + marker + "</main>", True)
    assert calls == []


def test_native_scope_is_restored_on_nested_error():
    from djust._deferred_children import defer_root_children
    from djust._rust import set_deferred_child_scope

    previous = set_deferred_child_scope(False)
    try:
        with pytest.raises(RuntimeError, match="scope failure"):
            with defer_root_children(object(), True):
                assert set_deferred_child_scope(True) is True
                with defer_root_children(object(), True):
                    raise RuntimeError("scope failure")
        assert set_deferred_child_scope(False) is False
    finally:
        set_deferred_child_scope(previous)


def test_namespace_switch_is_rejected_before_mounting_new_children(tmp_path):
    MOUNTS.clear()
    (tmp_path / "html.html").write_text("<main dj-root>initial</main>")
    (tmp_path / "svg.html").write_text(
        "{% load live_tags %}<svg dj-root><foreignObject>"
        '{% live_render "djust.tests.test_partial_root_child_execution.RootChild" %}'
        "</foreignObject></svg>"
    )

    class Page(LiveView):
        template = "{% include selected %}"

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
        page = Page()
        page.request = request
        page.selected = "html.html"
        page.render_with_diff(request)
        page.selected = "svg.html"
        page._cached_context = None
        page._sync_done_this_cycle = False
        with pytest.raises(RuntimeError, match="root namespace changed"):
            page.render_with_diff(request)
        assert MOUNTS == []
