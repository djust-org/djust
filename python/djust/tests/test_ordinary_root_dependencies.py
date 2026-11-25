"""Ordinary roots reached through includes remain ordinary LiveViews on Django 6."""

import django
import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.decorators import state
from djust.testing import live_session, with_live_session
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
        exposure_policy = "explicit"
        template_name = "page.html"
        name = state("value")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["chooser"] = chooser
            return context

    request = with_live_session(RequestFactory().get("/"))
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
    # 2.0: the baseline is the view's own; nothing is persisted in the backend.
    assert backend._cache == {}


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
        exposure_policy = "explicit"
        template_name = "page.html"
        name = state("before")
        shell_only = state("shell")

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
        exposure_policy = "explicit"
        template_name = "page.html"
        name = state("before")

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
        exposure_policy = "explicit"
        template_name = "page.html"
        name = state("before")
        prefix = state("")

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
        exposure_policy = "explicit"
        template_name = "page.html"
        injected = state("<aside dj-root>injected</aside>")

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
def test_ordinary_wrapper_competing_root_does_not_advance_baseline(tmp_path, monkeypatch, caplog):
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
        exposure_policy = "explicit"
        template_name = "page.html"
        wrapper_template = "wrapper.html"
        value = state("before")

    session = live_session()

    def request(content):
        req = RequestFactory().get("/test/")
        req.session = session  # one saved session for every request
        req.wrapper_content = content
        return with_live_session(req)

    with override_settings(TEMPLATES=templates):
        assert View.as_view()(request("<header>OK</header>")).status_code == 200
        # The page answers the refusal with the project's 500; the log names the guard.
        with override_settings(LIVEVIEW_CONFIG={"log_exception_detail": True}):
            refused = View.as_view()(request('<div dj-root dj-view="outside.Page">OUTSIDE</div>'))
        assert refused.status_code == 500
        assert "ImproperlyConfigured" in caplog.text
        assert "OUTSIDE" not in refused.content.decode()
        # 2.0: no legacy baseline is kept in the backend to advance at all.
        assert backend._cache == {}
