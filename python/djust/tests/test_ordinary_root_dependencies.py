"""Ordinary roots reached through includes remain ordinary LiveViews on Django 6."""

import django
import msgpack
import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.state_backends.memory import InMemoryStateBackend


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_stateful_ordinary_include_uses_one_page_and_baseline(tmp_path, monkeypatch):
    (tmp_path / "page.html").write_text("{% include chooser.selected %}")
    for filename, label in (("first.html", "FIRST"), ("second.html", "SECOND")):
        (tmp_path / filename).write_text(
            f'<main dj-root><input dj-model="name"><b>{label}</b></main>'
        )

    class Chooser:
        reads = 0

        @property
        def selected(self):
            self.reads += 1
            return "first.html" if self.reads % 2 else "second.html"

    chooser = Chooser()
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)

    class OrdinaryIncludeView(LiveView):
        template_name = "page.html"
        name = "value"

        def mount(self, request, **kwargs):
            self.chooser = chooser

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]
    with override_settings(TEMPLATES=templates):
        response = OrdinaryIncludeView.as_view()(request)
    assert response.status_code == 200
    assert "FIRST" in response.content.decode()
    assert "SECOND" not in response.content.decode()
    assert chooser.reads == 1
    assert backend._cache
    snapshots = [
        msgpack.unpackb(value[0], raw=False, strict_map_key=False)
        for value in backend._cache.values()
    ]
    assert any(
        "FIRST" in str(snapshot[2]) and "SECOND" not in str(snapshot[2]) for snapshot in snapshots
    )


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_ordinary_include_retains_root_fields_and_diff(tmp_path):
    (tmp_path / "page.html").write_text(
        '<header><input dj-model="shell_only"></header>{% include "root.html" %}'
    )
    (tmp_path / "root.html").write_text(
        '<main dj-root><input dj-model="name"><b>{{ name }}</b></main>'
    )

    class OrdinaryView(LiveView):
        template_name = "page.html"
        name = "before"
        shell_only = "shell"

    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]
    with override_settings(TEMPLATES=templates):
        view = OrdinaryView()
        html, _, _ = view.render_with_diff(None)
        assert "before" in html
        assert "name" in view._dj_model_fields
        assert "shell_only" not in view._dj_model_fields
        view.name = "after"
        html, patches, _ = view.render_with_diff(None)
        assert "after" in html and patches


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_ordinary_root_bindings_after_opaque_tag_output(tmp_path):
    (tmp_path / "page.html").write_text(
        '{% load theme_tags %}{% theme_switcher %}{% include "root.html" %}'
    )
    (tmp_path / "root.html").write_text(
        '<main dj-root><input dj-model="name"><b>{{ name }}</b></main>'
    )

    class OrdinaryView(LiveView):
        template_name = "page.html"
        name = "before"

    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": True,
        }
    ]
    with override_settings(TEMPLATES=templates):
        view = OrdinaryView()
        html, _, _ = view.render_with_diff(None)
        assert "before" in html
        assert "name" in view._dj_model_fields
        view.update_model(field="name", value="after")
        assert view.name == "after"


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_ordinary_root_cannot_switch_to_untrusted_competing_root(tmp_path):
    from django.core.exceptions import ImproperlyConfigured

    (tmp_path / "page.html").write_text('{{ prefix|safe }}{% include "root.html" %}')
    (tmp_path / "root.html").write_text(
        '<main dj-root><input dj-model="name"><b>{{ name }}</b></main>'
    )

    class OrdinaryView(LiveView):
        template_name = "page.html"
        name = "before"
        prefix = ""

    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]
    with override_settings(TEMPLATES=templates):
        view = OrdinaryView()
        first, _, version = view.render_with_diff(None)
        assert "before" in first
        view.prefix = "<div dj-root>INJECTED</div>"
        with pytest.raises(ImproperlyConfigured):
            view.render_with_diff(None)
        view.prefix = ""
        restored, _, next_version = view.render_with_diff(None)
        assert "INJECTED" not in restored
        assert next_version == version + 1


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_static_include_gaining_partial_replans_before_render(tmp_path):
    import os

    from django.core.exceptions import ImproperlyConfigured

    (tmp_path / "page.html").write_text('{% include "root.html" %}')
    path = tmp_path / "root.html"
    path.write_text("<main dj-root>first</main>")

    class Page(LiveView):
        template_name = "page.html"
        injected = "<aside dj-root>injected</aside>"

    templates = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
        }
    ]
    with override_settings(TEMPLATES=templates):
        view = Page()
        first, _, _ = view.render_with_diff(None)
        assert "first" in first
        path.write_text(
            "{% partialdef page %}<main dj-root>second</main>{% endpartialdef %}"
            "{{ injected|safe }}{% partial page %}"
        )
        # Ensure the loader notices the changed dependency even on filesystems
        # with coarse timestamp granularity.
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        with pytest.raises(ImproperlyConfigured):
            view.render_with_diff(None)


@pytest.mark.django_db
@pytest.mark.skipif(django.VERSION < (6, 0), reason="Django 6 dependency planning")
def test_ordinary_wrapper_competing_root_does_not_advance_baseline(tmp_path, monkeypatch):
    from django.core.exceptions import ImproperlyConfigured
    from djust.tests.test_liveview_partial_root_parity import templates_for, write

    write(tmp_path, "page.html", '{% include "root.html" %}')
    write(tmp_path, "root.html", "<main dj-root><b>{{ value }}</b></main>")
    write(
        tmp_path,
        "wrapper.html",
        "<html><body>{{ wrapper_content|safe }}{{ liveview_content|safe }}</body></html>",
    )
    templates = templates_for(tmp_path)
    templates[0]["OPTIONS"] = {
        "context_processors": [
            "djust.tests.test_liveview_partial_root_parity.wrapper_context_processor"
        ]
    }
    backend = InMemoryStateBackend()
    monkeypatch.setattr("djust.state_backends.get_backend", lambda: backend)

    class View(LiveView):
        template_name = "page.html"
        wrapper_template = "wrapper.html"

        def mount(self, request, **kwargs):
            self.value = "before"

    session = SessionStore()
    session.create()

    def request(content):
        req = RequestFactory().get("/test/")
        req.user = AnonymousUser()
        req.session = session
        req.wrapper_content = content
        return req

    with override_settings(TEMPLATES=templates):
        assert View.as_view()(request("<header>OK</header>")).status_code == 200
        before = {
            key: msgpack.unpackb(value[0], raw=False)[2:4] for key, value in backend._cache.items()
        }
        assert before
        with pytest.raises(ImproperlyConfigured):
            View.as_view()(request('<div dj-root dj-view="outside.Page">OUTSIDE</div>'))
        # Snapshot timestamps may refresh during state synchronization; the delivered
        # VDOM and its wire version must remain exactly the last successful baseline.
        assert {
            key: msgpack.unpackb(value[0], raw=False)[2:4] for key, value in backend._cache.items()
        } == before
