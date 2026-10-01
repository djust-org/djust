"""#3317: ``{% csrf_token %}`` outside ``[dj-root]`` with no ``csrf`` processor listed.

The issue reported an empty token in the page shell unless
``django.template.context_processors.csrf`` was listed in ``TEMPLATES``. #3294
fixed it by feeding the shell the same ``csrf_token`` the root gets. This pins
the reported configurations explicitly: the processor is never listed, under the
djust backend, under Django's own backend (which adds ``csrf`` implicitly for
its own renders, but not for djust's Rust shell), and with no ``OPTIONS`` at all.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
from djust.utils import clear_template_dirs_cache

_TOKEN = re.compile(r'name="csrfmiddlewaretoken" value="([^"]+)"')
_PAGE = (
    "<html><body>"
    '<form method="post" action="/logout/">{% csrf_token %}<button>out</button></form>'
    '<div dj-root><form method="post" action="/x/">{% csrf_token %}<button>in</button></form></div>'
    "</body></html>"
)
_CSRF_PROCESSOR = "django.template.context_processors.csrf"
_OPTIONS = {
    "listed-others-only": {
        "context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
        ]
    },
    "no-options": {},
}
_BACKENDS = {
    "djust": "djust.template_backend.DjustTemplateBackend",
    "django": "django.template.backends.django.DjangoTemplates",
}


@pytest.fixture
def make_templates():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "page_3317.html").write_text(_PAGE)

    def _caches():
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()

    def _build(backend: str, options: dict) -> list:
        listed = options.get("context_processors", [])
        assert _CSRF_PROCESSOR not in listed, "the csrf processor must stay unlisted"
        return [
            {
                "BACKEND": _BACKENDS[backend],
                "DIRS": [str(tmp)],
                "APP_DIRS": False,
                "OPTIONS": options,
            }
        ]

    yield _build, _caches
    _caches()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.parametrize("backend", sorted(_BACKENDS))
@pytest.mark.parametrize("options", sorted(_OPTIONS))
def test_shell_csrf_token_without_the_csrf_processor(make_templates, backend, options):
    build, reset = make_templates
    with override_settings(TEMPLATES=build(backend, _OPTIONS[options])):
        reset()
        view_cls = type(
            "V3317",
            (LiveView,),
            {
                "template_name": "page_3317.html",
                "login_required": False,
                "mount": lambda self, request, **kwargs: setattr(self, "n", 1),
            },
        )
        request = RequestFactory().get("/page-3317/")
        request.user = AnonymousUser()
        request.session = SessionStore()
        CsrfViewMiddleware(lambda r: None).process_request(request)
        html = view_cls.as_view()(request).content.decode()

    shell, _, root = html.partition("dj-root")
    shell_tokens, root_tokens = _TOKEN.findall(shell), _TOKEN.findall(root)
    assert len(shell_tokens) == 1, "empty csrf_token in the page shell: %s" % html
    assert shell_tokens == root_tokens, "the shell and the root must carry the same token"
    assert shell_tokens[0], "the token must not be empty"
