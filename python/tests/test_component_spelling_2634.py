"""#2634 — ``{{ component }}`` is the canonical spelling; ``{{ component.render }}``
still resolves but is the fragile one.

Pins the claims the docs sweep rests on (#1046: pin the claim, do not trust
the prose):

1. ``str(c) == c.render()`` for a ``Component`` and a mounted ``LiveComponent``
   that override neither ``__str__`` nor ``render`` (the BASE-class path,
   #1947/#1952).
2. Through a LiveView's HTTP GET, ``{{ c }}`` and ``{{ c.render }}`` land
   byte-identical in the page.
3. With ``LIVEVIEW_CONFIG['template_auto_call'] = False``, ``{{ c }}`` still
   renders while ``{{ c.render }}`` degrades to the bound-method repr — the
   reason ``{{ c }}`` is what the docs teach.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-2634",
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust"],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
        MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"],
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    django.setup()

from django.contrib.sessions.middleware import SessionMiddleware  # noqa: E402
from django.test import RequestFactory  # noqa: E402

from djust.components.base import Component, LiveComponent  # noqa: E402
from djust.live_view import LiveView  # noqa: E402


class Box(Component):
    """Overrides only ``template`` — ``__str__`` and ``render`` are the base class's."""

    template = '<p class="box">{{ text }}</p>'

    def __init__(self, text: str = "plain") -> None:
        super().__init__(text=text)
        self.text = text

    def get_context_data(self):
        return {"text": self.text}


class LiveBox(LiveComponent):
    """Same for ``LiveComponent``: only ``template`` and ``mount`` are ours."""

    template = "<p>live {{ n }}</p>"

    def mount(self, n: int = 1) -> None:
        self.n = n

    def get_context_data(self):
        return {"n": self.n}


class TestStrIsRender:
    def test_component_str_equals_render(self):
        c = Box("hello")
        assert str(c) == c.render()
        assert "hello" in str(c)
        assert "Box" not in str(c), "a repr leaked instead of markup"

    def test_live_component_str_equals_render(self):
        c = LiveBox()
        c.mount(n=7)
        assert str(c) == c.render()
        assert "live 7" in str(c)

    def test_neither_class_overrides_the_pair(self):
        # The assertion above is about the base classes only if the fixtures
        # do not shadow them.
        for cls in (Box, LiveBox):
            assert "__str__" not in cls.__dict__ and "render" not in cls.__dict__


class SpellingView(LiveView):
    template = '<div dj-root><i id="bare">{{ c }}</i><i id="dotted">{{ c.render }}</i></div>'

    def mount(self, request, **kwargs):
        self.title = "x"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["c"] = Box("via-get")
        return ctx


def _get(view_cls) -> str:
    request = RequestFactory().get("/spelling/")
    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    return response.content.decode("utf-8")


def _slot(html: str, slot: str) -> str:
    m = re.search(rf'<i id="{slot}"[^>]*>(.*?)</i>', html, re.S)
    assert m, f"slot {slot!r} missing from {html!r}"
    return m.group(1)


class TestLiveViewGetDifferential:
    @pytest.mark.django_db
    def test_both_spellings_render_identically_through_get(self):
        html = _get(SpellingView)
        bare, dotted = _slot(html, "bare"), _slot(html, "dotted")
        assert "via-get" in bare, f"{{{{ c }}}} rendered nothing useful: {bare!r}"
        assert bare == dotted, f"{{{{ c }}}} != {{{{ c.render }}}}:\n{bare!r}\n{dotted!r}"


class TestAutoCallOffIsWhyBareIsCanonical:
    def test_bare_survives_but_dotted_degrades(self, monkeypatch):
        from djust import config as djust_config
        from djust.template_backend import DjustTemplateBackend

        class _Cfg:
            def get(self, key, default=None):
                return False if key == "template_auto_call" else default

        monkeypatch.setattr(djust_config, "get_config", lambda: _Cfg())
        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )

        def render(source: str) -> str:
            return backend.from_string(source).render(context={"c": Box("off")}, request=None)

        assert "off" in render("{{ c|safe }}")
        assert "bound method" in render("{{ c.render|safe }}")
        # Non-vacuity: with the default config the dotted form renders markup.
        monkeypatch.undo()
        assert render("{{ c.render|safe }}") == render("{{ c|safe }}")
        assert "bound method" not in render("{{ c.render|safe }}")
