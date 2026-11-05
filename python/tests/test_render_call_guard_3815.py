"""#3815 -- a template-reachable ``render()`` / ``get_full_name()`` is not called when it is flagged.

``{% include obj %}`` renders an object that has a ``render`` method, and
``python/djust/template/operands.py::render_template_object`` called it with no
look at ``alters_data`` / ``do_not_call_in_templates``. Stock Django does the same,
so this is a deliberate divergence: djust is stricter than Django here, on the rule
the serializers already apply through ``djust_core::template_call_guard`` (#3772,
#3778) -- a callable that carries either marker is never called by a template.

Every payload counts its calls in a class-level counter, so "was it called" is a
measurement and not an inference from the output.

What a refusal looks like: ``{% include obj %}`` of a refused object renders the
empty string (the engine's ``string_if_invalid`` for an ``alters_data`` variable is
empty too), with a WARNING; it does not raise. ``{{ user.get_full_name }}`` in the
component tags falls back to ``str(user)``, the branch they already take for a
falsy name.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

import pytest
from django.template import Context as DjangoContext
from django.template import Engine

from djust import _rust
from djust.template import DjustTemplateBackend


# ---------------------------------------------------------------------------
# Payloads: a real object whose render() mutates (a counter) and may be flagged
# ---------------------------------------------------------------------------
class _Base:
    calls = 0

    def render(self, context: Any = None) -> str:
        type(self).calls += 1
        return "RENDERED"


def _payload(kind: str) -> Any:
    """A fresh class per call, so the counter is private to one assertion."""

    def render(
        self: Any, context: Any = None
    ) -> str:  # a fresh function per class: markers are per-function
        type(self).calls += 1
        return "RENDERED"

    cls = type(f"Payload_{kind}", (_Base,), {"calls": 0, "render": render})
    if kind == "alters_data":
        cls.render.alters_data = True  # type: ignore[attr-defined]
    elif kind == "do_not_call_in_templates":
        cls.render.do_not_call_in_templates = True  # type: ignore[attr-defined]
    elif kind == "both":
        cls.render.alters_data = True  # type: ignore[attr-defined]
        cls.render.do_not_call_in_templates = True  # type: ignore[attr-defined]
    elif kind == "marker_false":
        cls.render.alters_data = False  # type: ignore[attr-defined]
        cls.render.do_not_call_in_templates = False  # type: ignore[attr-defined]
    elif kind == "raising_marker":
        # A marker read that RAISES something other than AttributeError: fail closed.
        original = cls.render

        class _Raises:
            """A bound-render stand-in whose ``alters_data`` read raises."""

            def __init__(self, bound: Callable[..., Any]) -> None:
                self._bound = bound

            def __call__(self, *a: Any, **k: Any) -> Any:
                return self._bound(*a, **k)

            @property
            def alters_data(self) -> bool:
                raise RuntimeError("marker read raised")

        cls.render = property(lambda self: _Raises(original.__get__(self)))  # type: ignore[assignment]
    else:
        assert kind == "plain", kind
    return cls


KINDS_REFUSED = ["alters_data", "do_not_call_in_templates", "both", "raising_marker"]
KINDS_ALLOWED = ["plain", "marker_false"]


# ---------------------------------------------------------------------------
# The entries a template takes
# ---------------------------------------------------------------------------
def plain_render(source: str, ctx: dict) -> str:
    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return str(backend.from_string(source).render(context=dict(ctx), request=None))


def raw_render(source: str, ctx: dict) -> str:
    return _rust.render_template(source, dict(ctx))


def rust_live_view_render(source: str, ctx: dict) -> str:
    view = _rust.RustLiveView(source)
    view.set_raw_py_values(dict(ctx))
    view.update_state(dict(ctx))
    return view.render()


def liveview_render(source: str, ctx: dict) -> str:
    from djust.live_view import LiveView
    from djust.testing import LiveViewTestClient

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for name, value in ctx.items():
                setattr(self, name, value)

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


def http_get_render(source: str, ctx: dict) -> str:
    from django.test import RequestFactory

    from djust.live_view import LiveView

    class _V(LiveView):
        def mount(self, request, **kwargs):
            for name, value in ctx.items():
                setattr(self, name, value)

    _V.template = f"<div dj-root>{source}</div>"
    from django.contrib.sessions.middleware import SessionMiddleware

    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: None).process_request(request)
    response = _V.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    html = response.content.decode()
    match = re.search(r"<div dj-root[^>]*>(.*?)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


def django_render(source: str, ctx: dict) -> str:
    return Engine().from_string(source).render(DjangoContext(dict(ctx)))


ENGINES = [
    pytest.param(plain_render, id="DjustTemplateBackend"),
    pytest.param(raw_render, id="render_template"),
    pytest.param(rust_live_view_render, id="RustLiveView"),
    pytest.param(liveview_render, id="LiveView-WS"),
    pytest.param(http_get_render, id="LiveView-GET", marks=pytest.mark.django_db),
]


# ---------------------------------------------------------------------------
# {% include obj %}
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind", KINDS_REFUSED)
def test_include_of_a_flagged_render_is_not_called(engine, kind):
    obj = _payload(kind)()
    out = engine("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 0, f"render() ran {type(obj).calls} time(s) on {engine.__name__}"
    assert out == "[]"


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind", KINDS_ALLOWED)
def test_include_of_an_unflagged_render_still_renders(engine, kind):
    obj = _payload(kind)()
    out = engine("[{% include obj %}]", {"obj": obj})
    assert out == "[RENDERED]"
    # An HTTP GET renders the page twice (the full document, then the dj-root), so "called" is
    # `>= 1` here; the refused cells above pin the exact count, `0`.
    assert type(obj).calls >= 1


@pytest.mark.parametrize("engine", ENGINES)
def test_include_with_only_and_with_still_refuses(engine):
    obj = _payload("alters_data")()
    out = engine("[{% include obj with a=1 only %}]", {"obj": obj})
    assert type(obj).calls == 0
    assert out == "[]"


@pytest.mark.parametrize("kind", KINDS_REFUSED)
def test_extends_never_calls_an_arbitrary_render(kind):
    """``{% extends obj %}`` takes a compiled Template's SOURCE; it never calls ``.render``."""
    obj = _payload(kind)()
    try:
        plain_render("{% extends obj %}", {"obj": obj})
    except Exception:  # an object that is not a template is not a valid parent
        pass
    assert type(obj).calls == 0


def test_the_refusal_is_logged_not_raised(caplog):
    obj = _payload("alters_data")()
    with caplog.at_level(logging.WARNING, logger="djust.template.operands"):
        assert plain_render("[{% include obj %}]", {"obj": obj}) == "[]"
    assert any("alters_data" in r.getMessage() for r in caplog.records), caplog.text


def test_a_guard_check_does_not_call_the_method():
    """Reading the markers must not call ``render`` (nor trigger any other call)."""
    obj = _payload("alters_data")()
    from djust.template.operands import render_template_object

    assert render_template_object(obj, {}, True) == ""
    assert type(obj).calls == 0


def test_a_flagged_template_subclass_is_refused_on_include():
    """A real ``django.template.Template`` subclass whose ``render`` is flagged."""
    from django.template import Template

    calls = []

    class Flagged(Template):
        def render(self, context):
            calls.append(1)
            return super().render(context)

        render.alters_data = True  # type: ignore[attr-defined]

    tpl = Flagged("INNER", engine=Engine())
    assert plain_render("[{% include t %}]", {"t": tpl}) == "[]"
    assert calls == []


def test_a_plain_django_template_object_still_includes():
    tpl = Engine().from_string("INNER {{ x }}")
    assert plain_render("[{% include t %}]", {"t": tpl, "x": 1}) == "[INNER 1]"


def test_stock_django_calls_a_flagged_render__the_divergence_is_on_purpose():
    """Pins the premise: the stock engine runs it (Django parity), djust refuses (#3815)."""
    obj = _payload("alters_data")()
    out = django_render("[{% include obj %}]", {"obj": obj})
    # Django calls render(); whether it then accepts the str is its business.
    assert type(obj).calls == 1, out


# ---------------------------------------------------------------------------
# get_full_name() in the component tags / classes
# ---------------------------------------------------------------------------
class _User:
    calls = 0
    flagged: str | None = None

    def __init__(self, flag: str | None) -> None:
        self.flag = flag

    def get_full_name(self) -> str:
        type(self).calls += 1
        return "Full Name"

    def __str__(self) -> str:
        return "username"


def _user(flag: str | None) -> Any:
    def get_full_name(self: Any) -> str:  # a fresh function per class: markers are per-function
        type(self).calls += 1
        return "Full Name"

    cls = type("U", (_User,), {"calls": 0, "get_full_name": get_full_name})
    if flag:
        setattr(cls.get_full_name, flag, True)
    return cls(flag)


def _avatar_group_class(user: Any) -> str:
    from djust.components.components.avatar_group import AvatarGroup

    return str(AvatarGroup(users=[user]).render())


def _live_indicator_class(user: Any) -> str:
    from djust.components.components.live_indicator import LiveIndicator

    return str(LiveIndicator(user=user).render())


def _presence_avatars_class(user: Any) -> str:
    from djust.components.components.presence_avatars import PresenceAvatars

    return str(PresenceAvatars(users=[user]).render())


def _avatar_group_tag(user: Any) -> str:
    return plain_render(
        "{% load djust_components %}{% avatar_group users=users %}", {"users": [user]}
    )


FULL_NAME_SINKS = [
    pytest.param(_avatar_group_class, id="AvatarGroup"),
    pytest.param(_live_indicator_class, id="LiveIndicator"),
    pytest.param(_presence_avatars_class, id="PresenceAvatars"),
    pytest.param(_avatar_group_tag, id="avatar_group-tag"),
]


@pytest.mark.parametrize("sink", FULL_NAME_SINKS)
@pytest.mark.parametrize("flag", ["alters_data", "do_not_call_in_templates"])
def test_flagged_get_full_name_is_not_called(sink, flag):
    user = _user(flag)
    out = sink(user)
    assert type(user).calls == 0, f"get_full_name ran in {sink.__name__}"
    assert "username" in out
    assert "Full Name" not in out


@pytest.mark.parametrize("sink", FULL_NAME_SINKS)
def test_unflagged_get_full_name_still_prints(sink):
    user = _user(None)
    out = sink(user)
    assert type(user).calls == 1
    assert "Full Name" in out
