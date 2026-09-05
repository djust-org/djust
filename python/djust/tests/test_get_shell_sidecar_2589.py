"""#2589 — the LiveView GET page shell renders with the same raw-Python
sidecar the WS mount gets.

``render_full_template`` rendered the ``{% extends %}`` shell through a
throwaway ``RustLiveView`` with NO ``set_raw_py_values`` sidecar and with every
``HttpRequest`` dropped from the context, so on the shell:

* ``{% querystring %}`` (no explicit query dict) raised ``'Context' object has
  no attribute 'request'`` while the WS path rendered ``?a=2&b=x``;
* ``{{ obj.prop }}`` (a property / method reached only through the #2501
  attribute walk) rendered empty while the dj-root rendered it.

Reproduced through the real path the issue names: ``as_view()`` GET over
real template files (``base.html`` + ``{% extends %}`` page).
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache

_FACTORY = RequestFactory()


class _Presenter:
    """A plain object: ``.label`` resolves only through the sidecar walk."""

    @property
    def label(self) -> str:
        return "walked-2589"


class ShellView(LiveView):
    template_name = "shell_2589/page.html"

    def mount(self, request, **kwargs):
        self._presenter = _Presenter()
        self.x = 1

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["presenter"] = self._presenter
        return ctx


@pytest.fixture
def templates(tmp_path, settings):
    d = tmp_path / "templates" / "shell_2589"
    d.mkdir(parents=True)
    (d / "base.html").write_text(
        "<!DOCTYPE html><html><body><nav>{% block nav %}{% endblock %}</nav>"
        "{% block content %}{% endblock %}</body></html>"
    )
    (d / "page.html").write_text(
        '{% extends "shell_2589/base.html" %}'
        "{% block nav %}qs=[{% querystring a=2 %}] prop=[{{ presenter.label }}]{% endblock %}"
        '{% block content %}<div dj-root dj-view="djust.tests.test_get_shell_sidecar_2589.ShellView">'
        "root=[{{ presenter.label }}]</div>{% endblock %}"
    )
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ]
    ):
        # `get_template_dirs()` is lru-cached (#1801): drop the cache on both
        # sides so this DIRS override neither sees nor leaves a stale value.
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()


def _get(path: str) -> str:
    from django.contrib.sessions.middleware import SessionMiddleware

    request = _FACTORY.get(path)
    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()
    response = ShellView.as_view()(request)
    return response.content.decode()


@pytest.mark.django_db
class TestGetShellHasTheSidecar:
    def test_querystring_on_the_shell_reads_the_request(self, templates):
        html = _get("/?a=1&b=x")
        assert "qs=[?a=2&amp;b=x]" in html, html

    def test_attribute_walk_on_the_shell_matches_the_dj_root(self, templates):
        html = _get("/")
        assert "root=[walked-2589]" in html, html
        assert "prop=[walked-2589]" in html, html
