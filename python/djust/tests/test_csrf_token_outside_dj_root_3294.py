"""#3294: ``{% csrf_token %}`` outside ``[dj-root]`` rendered empty on the initial GET.

The page shell (everything outside the root) is rendered by its own Rust
renderer. The dj-root render injected ``csrf_token`` when no processor
supplied it (#696); the shell render did not, so a logout form in a shared nav
posted without a token. Both must carry the same token.
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

_STANDALONE = (
    "<html><body>"
    '<form method="post" action="/logout/">{% csrf_token %}<button>out</button></form>'
    '<div dj-root><form method="post" action="/x/">{% csrf_token %}<button>in</button></form></div>'
    "</body></html>"
)
_BASE = (
    "<html><body>"
    '<form method="post" action="/logout/">{% csrf_token %}<button>out</button></form>'
    "{% block content %}{% endblock %}</body></html>"
)
_CHILD = (
    '{% extends "base_3294.html" %}{% block content %}'
    '<div dj-root><form method="post" action="/x/">{% csrf_token %}<button>in</button></form></div>'
    "{% endblock %}"
)

# What ``djust new`` / startproject write: no ``csrf`` processor.
_PROCESSORS = [
    "django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth",
]


@pytest.fixture
def template_dir():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "standalone_3294.html").write_text(_STANDALONE)
    (tmp / "base_3294.html").write_text(_BASE)
    (tmp / "child_3294.html").write_text(_CHILD)
    templates = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp)],
            "APP_DIRS": False,
            "OPTIONS": {"context_processors": _PROCESSORS},
        }
    ]
    with override_settings(TEMPLATES=templates):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        yield tmp
    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    shutil.rmtree(tmp, ignore_errors=True)


def _get(template_name: str) -> str:
    view_cls = type(
        "V3294_" + template_name.split(".")[0],
        (LiveView,),
        {
            "template_name": template_name,
            "login_required": False,
            "mount": lambda self, request, **kwargs: setattr(self, "n", 1),
        },
    )
    request = RequestFactory().get("/page-3294/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    CsrfViewMiddleware(lambda r: None).process_request(request)
    return view_cls.as_view()(request).content.decode()


@pytest.mark.parametrize("template_name", ["standalone_3294.html", "child_3294.html"])
def test_csrf_token_renders_outside_and_inside_the_root(template_dir, template_name):
    html = _get(template_name)
    shell, _, root = html.partition("dj-root")
    shell_tokens, root_tokens = _TOKEN.findall(shell), _TOKEN.findall(root)
    assert len(shell_tokens) == 1, "no token on the logout form outside dj-root: %s" % html
    assert len(root_tokens) == 1, html
    assert shell_tokens == root_tokens, "the shell and the root must carry the same token"
