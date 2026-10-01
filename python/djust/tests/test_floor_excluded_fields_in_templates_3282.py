"""#3282: ``{% if request.user.is_staff %}`` is silently false in a LiveView template.

The serializer withholds ``is_staff``, ``is_superuser`` and ``password`` from every
template context (``serialization._ALWAYS_EXCLUDED_FIELDS``). Naming one renders
empty with no error, so ``djust.T024`` warns, and the documented fix, a derived
boolean, is pinned here to actually render.
"""

from __future__ import annotations

import gc
import importlib.util
import sys
import textwrap

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, override_settings

from djust.checks.bindings import check_event_bindings

MODULE = "floor_field_fixture_3282"

SOURCE = """
from djust import LiveView


class Nav(LiveView):
    template_name = "floor/page.html"
    login_required = False


class Inline(LiveView):
    login_required = False
    template = '''<div dj-root>
{% if request.user.is_staff %}admin{% endif %}
{% with u=request.user %}{{ u.is_superuser }}{% endwith %}
{% with request.user as me %}{{ me.password }}{% endwith %}
{% for person in others %}{{ person.is_staff }}{% endfor %}
{{ form.is_staff }}
</div>'''
"""

TEMPLATES = {
    "floor/base.html": (
        "<html><body>\n"
        "{% if request.user.is_staff %}<a>admin</a>{% endif %}\n"
        "{# {% if user.is_superuser %} commented out #}\n"
        "<main dj-root>{% block content %}{% endblock %}</main>\n"
        "</body></html>"
    ),
    "floor/page.html": (
        '{% extends "floor/base.html" %}\n'
        "{% block content %}\n"
        "{{ user.is_superuser|yesno }}\n"
        "{{ form.password }} {{ user.username }} {{ can_manage }}\n"
        "{# noqa: T024 -- shown deliberately #}{{ request.user.password }}\n"
        "{% endblock %}"
    ),
}


@pytest.fixture
def fixture(tmp_path):
    templates = tmp_path / "templates"
    for name, text in TEMPLATES.items():
        target = templates / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    path = tmp_path / ("%s.py" % MODULE)
    path.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(MODULE, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    config = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(templates)],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ]
    try:
        with override_settings(TEMPLATES=config):
            yield templates
    finally:
        sys.modules.pop(MODULE, None)
        module.Nav.abstract = True
        module.Inline.abstract = True
        gc.collect()


def _t024():
    return [m for m in check_event_bindings(None) if m.id == "djust.T024"]


def test_user_floor_fields_in_the_page_and_its_parent_are_reported(fixture):
    found = [m for m in _t024() if m.file_path.endswith(".html")]
    assert sorted((m.file_path.rsplit("/", 1)[1], m.line_number) for m in found) == [
        ("base.html", 2),
        ("page.html", 3),
    ]
    assert "request.user.is_staff" in found[0].msg or "request.user.is_staff" in found[1].msg
    assert all("derived boolean" in m.hint for m in found)


def test_an_inline_template_is_checked_at_its_line_in_the_python_file(fixture):
    inline = [m for m in _t024() if m.file_path.endswith(".py")]
    source = open(inline[0].file_path, encoding="utf-8").read().splitlines()
    expected = [
        next(i for i, line in enumerate(source, 1) if text in line)
        for text in ("{% if request.user.is_staff", "{% with u=request.user", "as me %}")
    ]
    assert sorted(m.line_number for m in inline) == expected
    messages = " ".join(m.msg for m in inline)
    assert "request.user.is_staff" in messages
    assert "u.is_superuser" in messages and "me.password" in messages  # {% with %} aliases
    # A loop variable and a form field are not user paths.
    assert "person.is_staff" not in messages
    assert "form.is_staff" not in messages


def test_form_fields_commented_out_and_noqa_references_are_not_reported(fixture):
    lines = {(m.file_path.rsplit("/", 1)[1], m.line_number) for m in _t024()}
    assert ("base.html", 3) not in lines  # inside {# #}
    assert ("page.html", 4) not in lines  # form.password: not a user path
    assert ("page.html", 5) not in lines  # {# noqa: T024 -- reason #}


def test_the_check_can_be_suppressed_globally(fixture, settings):
    settings.DJUST_CONFIG = {"suppress_checks": ["T024"]}
    assert _t024() == []


@pytest.mark.django_db
def test_a_derived_boolean_renders_where_the_floor_field_does_not(tmp_path):
    """The documented fix: expose ``can_manage`` from the view or a context processor."""
    (tmp_path / "derived.html").write_text(
        "<html><body>shell:[{% if request.user.is_staff %}RAW{% endif %}"
        "{% if can_manage %}SHELL-DERIVED{% endif %}]"
        "<div dj-root>root:[{% if request.user.is_staff %}RAW{% endif %}"
        "{% if can_manage %}ROOT-DERIVED{% endif %}]</div></body></html>"
    )
    config = [
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": [str(tmp_path)],
            "APP_DIRS": False,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "djust.tests._floor_derived_processor_3282.can_manage",
                ]
            },
        }
    ]
    staff = User.objects.create_user("boss", password="x", is_staff=True)

    from djust import LiveView

    class Derived(LiveView):
        template_name = "derived.html"
        login_required = False

        def mount(self, request, **kwargs):
            self.noop = 1

    from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
    from djust.utils import clear_template_dirs_cache

    with override_settings(TEMPLATES=config):
        clear_template_dirs_cache()
        _resolved_processors_cache.clear()
        _context_processors_cache.clear()
        request = RequestFactory().get("/")
        request.user = staff
        request.session = SessionStore()
        html = Derived.as_view()(request).content.decode()
    assert "RAW" not in html
    assert "SHELL-DERIVED" in html and "ROOT-DERIVED" in html, html
