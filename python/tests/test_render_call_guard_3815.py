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

#3880 applies the same rule where a view's or component's context reaches Django's stock
engine (child views, streamed fragments, pending layouts, component fallbacks): see the
"Child templates" section at the end.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any, Callable

import pytest
from django.template import Context as DjangoContext
from django.template import Engine

from djust import _rust
from djust.template import DjustTemplateBackend

from django import template as django_template


@pytest.fixture(autouse=True)
def _fresh_refusal_log():
    """The refusal is logged once per (type, method) per process: start each test clean."""
    from djust._template_guards import _reset_refusal_log

    _reset_refusal_log()
    yield
    _reset_refusal_log()


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
        exposure_policy = "legacy"

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
        exposure_policy = "legacy"

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
]

# djust 2.0: a LiveView refuses to include ANY template object it cannot prove is a stock,
# built-in-only Django template (``template/operands.py::is_safe_dynamic_template_object``), so
# on these three entries an object with its own ``render`` never reaches the #3815 guard: the
# render fails before the method is read. Stricter than the guard, and still "not called".
LIVE_ENGINES = [
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


@pytest.mark.parametrize("engine", LIVE_ENGINES)
@pytest.mark.parametrize("kind", KINDS_REFUSED + KINDS_ALLOWED)
def test_a_liveview_refuses_an_object_with_its_own_render_before_calling_it(engine, kind):
    obj = _payload(kind)()
    with pytest.raises(Exception, match="Dynamic template object cannot be verified"):
        engine("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 0, f"render() ran {type(obj).calls} time(s) on {engine.__name__}"


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
    with caplog.at_level(logging.WARNING, logger="djust._template_guards"):
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


# ---------------------------------------------------------------------------
# Bind ONCE: the callable whose markers are read is the callable that is called
# ---------------------------------------------------------------------------
class _Shifty:
    """``render`` is a property: the FIRST read is harmless, every later read is flagged.

    A check-then-call that reads the attribute twice checks the harmless one and calls the
    flagged one. Records which callable ran and how many times the attribute was read.
    """

    def __init__(self) -> None:
        self.reads = 0
        self.ran: list[str] = []

    @property
    def render(self) -> Callable[..., str]:
        self.reads += 1
        label = "first" if self.reads == 1 else "later"

        def render(context: Any = None) -> str:
            self.ran.append(label)
            return label

        if label == "later":
            render.alters_data = True  # type: ignore[attr-defined]
        return render


def test_include_reads_render_once_and_calls_what_it_checked():
    from djust.template.operands import render_template_object

    obj = _Shifty()
    assert render_template_object(obj, {}, True) == "first"
    assert obj.reads == 1
    assert obj.ran == ["first"]


class _ShiftyUser:
    def __init__(self) -> None:
        self.reads = 0
        self.ran: list[str] = []

    @property
    def get_full_name(self) -> Callable[..., str]:
        self.reads += 1
        label = "first" if self.reads == 1 else "later"

        def get_full_name() -> str:
            self.ran.append(label)
            return label

        if label == "later":
            get_full_name.alters_data = True  # type: ignore[attr-defined]
        return get_full_name


def test_call_method_for_template_reads_once_and_calls_what_it_checked():
    from djust._template_guards import call_method_for_template

    user = _ShiftyUser()
    assert call_method_for_template(user, "get_full_name") == "first"
    assert user.reads == 1
    assert user.ran == ["first"]


# ---------------------------------------------------------------------------
# What counts as a marker: TRUTHINESS, as in Django's getattr(...) and the Rust guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["yes", 1, [0], object()])
@pytest.mark.parametrize("marker", ["alters_data", "do_not_call_in_templates"])
def test_a_truthy_non_true_marker_refuses(marker, value):
    from djust.template.operands import render_template_object

    obj = _payload("plain")()
    setattr(type(obj).render, marker, value)
    assert render_template_object(obj, {}, True) == ""
    assert type(obj).calls == 0


@pytest.mark.parametrize("value", [0, "", None, [], False])
def test_a_falsy_marker_does_not_refuse(value):
    from djust.template.operands import render_template_object

    obj = _payload("plain")()
    type(obj).render.alters_data = value
    type(obj).render.do_not_call_in_templates = value
    assert render_template_object(obj, {}, True) == "RENDERED"
    assert type(obj).calls == 1


# ---------------------------------------------------------------------------
# get_full_name that is not callable; a real django User
# ---------------------------------------------------------------------------
class _NotCallableName:
    get_full_name = "Not A Method"

    def __str__(self) -> str:
        return "username"


@pytest.mark.parametrize("sink", FULL_NAME_SINKS)
def test_a_non_callable_get_full_name_falls_back_to_str(sink):
    out = sink(_NotCallableName())
    assert "username" in out
    assert "Not A Method" not in out


@pytest.mark.django_db
@pytest.mark.parametrize("sink", FULL_NAME_SINKS)
def test_a_real_django_user_still_prints_its_full_name(sink):
    from django.contrib.auth.models import User

    user = User.objects.create(username="jr", first_name="Jordan", last_name="Reyes")
    assert "Jordan Reyes" in sink(user)


@pytest.mark.django_db
def test_a_real_user_whose_get_full_name_is_flagged_is_not_called(monkeypatch):
    from django.contrib.auth.models import User

    calls = []

    def get_full_name(self: Any) -> str:
        calls.append(1)
        return "Jordan Reyes"

    get_full_name.alters_data = True  # type: ignore[attr-defined]
    monkeypatch.setattr(User, "get_full_name", get_full_name)
    user = User.objects.create(username="jr", first_name="Jordan", last_name="Reyes")
    out = _avatar_group_class(user)
    assert calls == []
    assert "Jordan Reyes" not in out


# ---------------------------------------------------------------------------
# One WARNING per (type, method) per process, for BOTH refusals
# ---------------------------------------------------------------------------
def test_include_refusal_logs_once_per_type(caplog):
    obj = _payload("alters_data")()
    with caplog.at_level(logging.WARNING, logger="djust._template_guards"):
        for _ in range(3):
            plain_render("[{% include obj %}]", {"obj": obj})
    assert len([r for r in caplog.records if "alters_data" in r.getMessage()]) == 1


def test_get_full_name_refusal_logs_once_per_type_and_names_no_object(caplog):
    user = _user("alters_data")
    with caplog.at_level(logging.WARNING, logger="djust._template_guards"):
        for _ in range(3):
            _avatar_group_class(user)
    records = [r for r in caplog.records if "get_full_name" in r.getMessage()]
    assert len(records) == 1
    assert "alters_data" in records[0].getMessage()
    assert "username" not in records[0].getMessage()  # str(user) is never logged


# ---------------------------------------------------------------------------
# Child templates (#3880): Django's stock engine, guarded where it renders
# ---------------------------------------------------------------------------
# An embedded child, a sticky child, a ``{% live_render %}`` child, a streamed fragment, a
# pending layout, a component's Django-engine fallback and the test client's Django engine are
# rendered by Django's own engine, not the five entries above. The engine is unchanged (same
# filters, tags, escaping); the guard is applied where those renders happen
# (``djust._include_guard.include_guard_scope``), so a flagged ``render`` named by
# ``{% include obj %}`` is refused there too -- and ONLY there: a render that no djust view or
# component is making is Django's, untouched (``test_stock_django_..._divergence`` above and
# ``test_a_render_outside_the_scope_is_stock_django`` below).
_TEMPLATES: dict[str, str] = {}

register = django_template.Library()


@register.inclusion_tag("inclusion_3880.html", takes_context=True)
def inclusion_3880(context):
    """A third-party-shaped inclusion tag: Django renders ITS template with the page's operands."""
    return {"obj": context["obj"], "objs": context.get("objs", [])}


@pytest.fixture(autouse=True)
def _locmem_templates():
    """Named templates the child sites load: ``_TEMPLATES`` is the loader's dict, so a test adds one."""
    from django.test import override_settings

    from djust._include_guard import uninstall

    # Start every test with Django's own IncludeNode.render, so a site is guarded because IT
    # entered the scope (the scope installs the patch), not because an earlier test installed it.
    uninstall()
    _TEMPLATES.clear()
    config = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", _TEMPLATES)]},
        }
    ]
    with override_settings(TEMPLATES=config):
        yield
    _TEMPLATES.clear()


def _child(source: str, ctx: dict, name: str | None = None) -> Any:
    """A mounted child view that renders ``source`` (inline, or from the named template)."""
    from djust.live_view import LiveView

    def get_context_data(self: Any, **kwargs: Any) -> dict:
        return dict(ctx)

    attrs: dict[str, Any] = {"get_context_data": get_context_data}
    if name is None:
        attrs["template"] = source
    else:
        _TEMPLATES[name] = source
        attrs["template_name"] = name
    child = type("Child3880", (LiveView,), {"exposure_policy": "legacy", **attrs})()
    child.mount(None)
    return child


def _unwrap(html: Any) -> str:
    """The inner HTML of the ``<div dj-view ...>`` wrapper a sticky / explicit child renders into."""
    match = re.match(r"<div dj-view[^>]*>(.*)</div>$", str(html), re.S)
    assert match is not None, html
    return match.group(1)


def embedded_site(source: str, ctx: dict) -> str:
    from djust.websocket import render_embedded_child_html

    return render_embedded_child_html(_child(source, ctx))


def sticky_inline_site(source: str, ctx: dict) -> str:
    from djust.templatetags.live_tags import _render_sticky_child_html

    child = _child(source, ctx)
    return _unwrap(_render_sticky_child_html(child, "v1", "sid", None, "x.Child"))


def sticky_named_site(source: str, ctx: dict) -> str:
    from djust.templatetags.live_tags import _render_sticky_child_html

    child = _child(source, ctx, name="sticky_child_3880.html")
    return _unwrap(_render_sticky_child_html(child, "v1", "sid", None, "x.Child"))


def transient_inline_site(source: str, ctx: dict) -> str:
    """``sticky_id_value=None``: the transient explicit child's plain wrapper."""
    from djust.templatetags.live_tags import _render_sticky_child_html

    child = _child(source, ctx)
    return _unwrap(_render_sticky_child_html(child, "v1", None, None, "x.Child"))


def transient_named_site(source: str, ctx: dict) -> str:
    from djust.templatetags.live_tags import _render_sticky_child_html

    child = _child(source, ctx, name="transient_child_3880.html")
    return _unwrap(_render_sticky_child_html(child, "v1", None, None, "x.Child"))


def _live_render_site(tag_args: str, named: bool = False) -> Callable[[str, dict], str]:
    """A page whose ``{% live_render %}`` mounts a child that renders ``source``.

    Drives the tag end to end (the page GET), so the two branches that render inside the tag
    itself -- the legacy non-sticky render and the lazy fill -- are reached the way a user
    reaches them. (``sticky=True`` and explicit children call ``_render_sticky_child_html``,
    which the ``sticky-*`` / ``transient-*`` sites call directly: a page GET renders a sticky
    child twice and the second pass reuses the live instance under the request's auth.)
    """

    def site(source: str, ctx: dict) -> str:
        import sys

        from django.contrib.auth.models import AnonymousUser
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory, override_settings

        from djust.live_view import LiveView

        def get_context_data(self: Any, **kwargs: Any) -> dict:
            return dict(ctx)

        attrs: dict[str, Any] = {
            "get_context_data": get_context_data,
            "mount": lambda self, request, **kw: None,
        }
        if named:
            _TEMPLATES["live_render_child_3880.html"] = source
            attrs["template_name"] = "live_render_child_3880.html"
        else:
            attrs["template"] = source
        child_cls = type("LiveRenderChild3880", (LiveView,), {"exposure_policy": "legacy", **attrs})
        setattr(sys.modules[__name__], "LiveRenderChild3880", child_cls)
        page_cls = type(
            "LiveRenderPage3880",
            (LiveView,),
            {
                "exposure_policy": "legacy",
                "template": (
                    "{% load live_tags %}<html><body><div dj-root>"
                    '{% live_render "' + __name__ + '.LiveRenderChild3880" ' + tag_args + " %}"
                    "</div></body></html>"
                ),
                "mount": lambda self, request, **kw: None,
            },
        )
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        view = page_cls()
        view.streaming_render = False  # the page-POST fallback shape: lazy slots are filled
        with override_settings(DJUST_LIVE_RENDER_ALLOWED_MODULES=[__name__]):
            html = view.get(request).content.decode("utf-8")
        match = re.search(r'data-djust-embedded="[^"]*">(.*?)</div>', html, re.S)
        assert match is not None, html
        return match.group(1)

    return site


live_render_plain_site = _live_render_site("")
live_render_lazy_site = _live_render_site("lazy=True")
live_render_plain_named_site = _live_render_site("", named=True)
live_render_lazy_named_site = _live_render_site("lazy=True", named=True)


CHILD_SITES = [
    pytest.param(embedded_site, id="embedded-child"),
    pytest.param(sticky_inline_site, id="sticky-inline"),
    pytest.param(sticky_named_site, id="sticky-named"),
    pytest.param(transient_inline_site, id="transient-inline"),
    pytest.param(transient_named_site, id="transient-named"),
    pytest.param(live_render_plain_site, id="live_render-plain", marks=pytest.mark.django_db),
    pytest.param(live_render_lazy_site, id="live_render-lazy", marks=pytest.mark.django_db),
    pytest.param(
        live_render_plain_named_site, id="live_render-plain-named", marks=pytest.mark.django_db
    ),
    pytest.param(
        live_render_lazy_named_site, id="live_render-lazy-named", marks=pytest.mark.django_db
    ),
]


@pytest.mark.parametrize("site", CHILD_SITES)
@pytest.mark.parametrize("kind", KINDS_REFUSED)
def test_child_include_of_a_flagged_render_is_not_called(site, kind):
    """The issue's reproducer, on every site, for every way a render can be flagged."""
    obj = _payload(kind)()
    out = site("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 0, f"render() ran {type(obj).calls} time(s) on {site.__name__}"
    assert out == "[]"


@pytest.mark.parametrize("site", CHILD_SITES)
@pytest.mark.parametrize("kind", KINDS_ALLOWED)
def test_child_include_of_an_unflagged_render_still_renders(site, kind):
    obj = _payload(kind)()
    out = site("[{% include obj %}]", {"obj": obj})
    assert out == "[RENDERED]"
    assert type(obj).calls >= 1  # a page GET renders its child more than once


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_nested_in_a_block_tag_is_refused(site):
    obj = _payload("alters_data")()
    out = site(
        "{% if obj %}{% with x=1 %}[{% include obj %}]{% endwith %}{% endif %}", {"obj": obj}
    )
    assert type(obj).calls == 0
    assert out == "[]"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_inside_a_loop_is_refused_for_every_item(site):
    flagged, other = _payload("alters_data")(), _payload("do_not_call_in_templates")()
    out = site(
        "{% for o in objs %}[{% include o %}]{% endfor %}",
        {"objs": [flagged, other, flagged]},
    )
    assert type(flagged).calls == 0 and type(other).calls == 0
    assert out == "[][][]"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_loop_refuses_the_flagged_item_and_renders_the_plain_one(site):
    """A refusal is per object: it neither skips the next item nor poisons the loop."""
    flagged, plain = _payload("alters_data")(), _payload("plain")()
    out = site(
        "{% for o in objs %}[{% include o %}]{% endfor %}",
        {"objs": [flagged, plain, flagged, plain]},
    )
    assert out == "[][RENDERED][][RENDERED]"
    assert type(flagged).calls == 0
    assert type(plain).calls >= 2


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_inside_an_included_template_is_refused(site):
    """The operand is reached through ``{% include "inner.html" %}``: a NAMED include is not a bypass."""
    _TEMPLATES["inner_3880.html"] = "<{% include obj %}>"
    obj = _payload("alters_data")()
    out = site('[{% include "inner_3880.html" %}]', {"obj": obj})
    assert type(obj).calls == 0
    assert out == "[<>]"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_inside_an_extended_template_is_refused(site):
    """A block of a child template that ``{% extends %}`` a loader-found parent is guarded too."""
    _TEMPLATES["base_3880.html"] = "<main>{% block b %}{% endblock %}</main>"
    obj = _payload("alters_data")()
    out = site(
        '{% extends "base_3880.html" %}{% block b %}[{% include obj %}]{% endblock %}', {"obj": obj}
    )
    assert type(obj).calls == 0
    assert out == "<main>[]</main>"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_with_only_and_with_still_refuses(site):
    obj = _payload("alters_data")()
    out = site("[{% include obj with a=1 only %}]", {"obj": obj})
    assert type(obj).calls == 0
    assert out == "[]"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_of_a_template_name_and_of_a_template_object_still_work(site):
    """The guard only touches an operand that quacks like a template and is flagged."""
    _TEMPLATES["greet_3880.html"] = "hi {{ who }}"
    tpl = Engine().from_string("T {{ who }}")
    out = site(
        '[{% include "greet_3880.html" with who="a" %}][{% include t with who="b" %}]'
        '[{% include names with who="c" %}]',
        {"t": tpl, "names": ["missing_3880.html", "greet_3880.html"]},
    )
    assert out == "[hi a][T b][hi c]"


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_include_cache_serves_a_loop_of_template_names(site):
    """Django caches a name's template per render in the node's slot: still one per name."""
    _TEMPLATES["row_3880.html"] = "<{{ n }}>"
    out = site('{% for n in ns %}{% include "row_3880.html" %}{% endfor %}', {"ns": [1, 2, 3]})
    assert out == "<1><2><3>"


SINGLE_RENDER_SITES = [
    pytest.param(embedded_site, id="embedded-child"),
    pytest.param(sticky_inline_site, id="sticky-inline"),
    pytest.param(sticky_named_site, id="sticky-named"),
]


@pytest.mark.parametrize("site", SINGLE_RENDER_SITES)
def test_child_include_operand_is_resolved_once(site):
    """A computed operand is evaluated once, as in Django: the guard must not read it twice."""

    class Picker:
        calls = 0

        def __call__(self) -> Any:
            type(self).calls += 1
            return Engine().from_string("PICKED")

    out = site("[{% include pick %}]", {"pick": Picker()})
    assert out == "[PICKED]"
    assert Picker.calls == 1


@pytest.mark.parametrize("site", SINGLE_RENDER_SITES)
def test_child_include_loads_a_named_template_once_per_render(site):
    """Django keeps a name's template in a per-render cache keyed on the ``{% include %}`` node.

    The guard hands Django a stand-in for the node; that stand-in must be the same cache key, or
    every iteration of a loop would load the template again.
    """
    from unittest.mock import patch

    from django.template.loaders.locmem import Loader

    _TEMPLATES["row_3880.html"] = "<{{ n }}>"
    real = Loader.get_contents
    loads: list[str] = []

    def counting(self: Any, origin: Any) -> str:
        loads.append(origin.name)
        return real(self, origin)

    with patch.object(Loader, "get_contents", counting):
        out = site('{% for n in ns %}{% include "row_3880.html" %}{% endfor %}', {"ns": [1, 2, 3]})
    assert out == "<1><2><3>"
    assert loads.count("row_3880.html") == 1, loads


# --- the other guard cases #3815 covers, on the child sites --------------------------------
class _CallablePayload:
    """A callable object a template may name as ``{{ obj }}``: ``__call__`` counts."""

    calls = 0

    def __call__(self) -> str:
        type(self).calls += 1
        return "CALLED"

    def __str__(self) -> str:
        return "OBJ"


def _callable(kind: str) -> Any:
    cls = type(f"Callable_{kind}", (_CallablePayload,), {"calls": 0})
    if kind != "plain":
        setattr(cls, kind, True)
    return cls()


@pytest.mark.parametrize("site", CHILD_SITES)
@pytest.mark.parametrize(
    "kind,expected",
    [("alters_data", "[]"), ("do_not_call_in_templates", "[OBJ]")],
)
def test_child_callable_operand_in_a_variable_is_not_called_when_flagged(site, kind, expected):
    """``{{ obj }}`` of a flagged callable: Django's own variable rule holds on the child sites."""
    obj = _callable(kind)
    out = site("[{{ obj }}]", {"obj": obj})
    assert type(obj).calls == 0
    assert out == expected


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_callable_operand_in_a_variable_is_called_when_unflagged(site):
    obj = _callable("plain")
    out = site("[{{ obj }}]", {"obj": obj})
    assert out == "[CALLED]"
    assert type(obj).calls >= 1


@pytest.mark.parametrize("site", CHILD_SITES)
def test_child_a_flagged_render_logs_one_warning_and_does_not_raise(site, caplog):
    obj = _payload("alters_data")()
    with caplog.at_level(logging.WARNING, logger="djust._template_guards"):
        site("[{% include obj %}]", {"obj": obj})
        site("[{% include obj %}]", {"obj": obj})
    assert len([r for r in caplog.records if "alters_data" in r.getMessage()]) == 1


# --- the two tests that PINNED the old behaviour, flipped on purpose (#3880) ----------------
def test_embedded_child_template_does_not_call_a_flagged_render():
    """Was ``test_PIN_embedded_child_template_still_calls_a_flagged_render`` (``"[RENDERED]"``, 1 call)."""
    from djust.websocket import render_embedded_child_html

    obj = _payload("alters_data")()
    assert render_embedded_child_html(_child_view(obj)) == "[]"
    assert type(obj).calls == 0


def test_sticky_child_template_does_not_call_a_flagged_render():
    """Was ``test_PIN_sticky_child_template_still_calls_a_flagged_render`` (``"[RENDERED]"``, 1 call)."""
    from djust.templatetags.live_tags import _render_sticky_child_html

    obj = _payload("alters_data")()
    html = str(_render_sticky_child_html(_child_view(obj), "v1", None, None, "x.Child"))
    assert "RENDERED" not in html
    assert "[]" in html
    assert type(obj).calls == 0


def _child_view(obj: Any) -> Any:
    return _child("[{% include obj %}]", {"obj": obj})


# --- the guard's own mechanics -------------------------------------------------------------
def test_the_embedded_child_reproducer_from_the_issue():
    from djust.live_view import LiveView
    from djust.websocket import render_embedded_child_html

    class P:
        calls = 0

        def render(self, ctx=None):
            type(self).calls += 1
            return "RENDERED"

        render.alters_data = True

    class C(LiveView):
        exposure_policy = "legacy"
        template = "[{% include obj %}]"

        def mount(self, request, **kw):
            self.obj = P()

        def get_context_data(self, **kw):
            return {"obj": self.obj}

    c = C()
    c.mount(None)
    assert render_embedded_child_html(c) == "[]"
    assert P.calls == 0


def test_install_patches_once_and_holds_a_later_wrapper_to_the_guard(caplog):
    """A ``functools.wraps`` wrapper put on top of the guard keeps running, and the next scope entry
    puts a guard over it (only the OUTERMOST render is trusted, review of #4068); nothing is
    logged at WARNING."""
    import functools

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import guarded_include_render, include_guard_scope, install

    install()
    install()  # idempotent
    assert IncludeNode.render is guarded_include_render
    below = IncludeNode.render
    seen: list[int] = []

    @functools.wraps(below)
    def wrapper(self: Any, context: Any) -> Any:
        seen.append(1)
        return below(self, context)

    IncludeNode.render = wrapper  # type: ignore[method-assign]
    obj = _payload("alters_data")()
    plain = _payload("plain")()
    with caplog.at_level(logging.WARNING, logger="djust._include_guard"):
        with include_guard_scope():
            out = Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj}))
            assert _include(plain) == "[RENDERED]"
    assert not [r for r in caplog.records if r.name == "djust._include_guard"]
    assert IncludeNode.render.__wrapped__ is wrapper  # type: ignore[attr-defined]
    assert seen == [1], "the wrapper still renders what the guard lets through"
    assert out == "[]"
    assert type(obj).calls == 0


def test_a_wraps_wrapper_naming_the_guard_but_calling_djangos_render_is_held_to_it():
    """Review of #4068: ``functools.wraps(guard)`` gives a ``__wrapped__`` link to the guard, but the
    wrapper may call Django's own ``render``; a chain walk trusted it and the guard was off."""
    import functools

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, guarded_include_render, include_guard_scope

    @functools.wraps(guarded_include_render)
    def impostor(self: Any, context: Any) -> Any:
        return _ORIGINAL(self, context)

    IncludeNode.render = impostor  # type: ignore[method-assign]
    obj = _payload("alters_data")()
    with include_guard_scope():
        assert _include(obj) == "[]"
    assert type(obj).calls == 0


def test_a_patch_swapped_in_during_an_active_scope_is_unguarded_until_the_next_entry():
    """A KNOWN GAP, pinned as current behaviour: the guard is (re)installed on scope ENTRY, so a
    patch of ``IncludeNode.render`` made while a scope is active renders unguarded until the next
    entry. If this starts failing because the include is refused, the gap closed: update the
    docstring of ``install`` and ``docs/SECURE_DEFAULTS.md``."""
    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, include_guard_scope

    def replacement(self: Any, context: Any) -> Any:
        return _ORIGINAL(self, context)

    obj = _payload("alters_data")()
    with include_guard_scope():
        IncludeNode.render = replacement  # type: ignore[method-assign]
        assert _include(obj) == "[RENDERED]"  # the gap
    assert type(obj).calls == 1
    with include_guard_scope():  # the next entry holds it to the guard again
        assert _include(obj) == "[]"
    assert type(obj).calls == 1


def test_a_reloaded_style_wrapper_over_the_guard_gets_one_guard_on_top(caplog):
    """A ``functools.wraps`` wrapper over the guard (what a reload or instrumentation leaves) gets
    ONE guard on top, not one per scope entry, and nothing is logged at WARNING."""
    import functools

    from django.template.loader_tags import IncludeNode

    import djust._include_guard as guard

    guard.install()
    wrapped = functools.wraps(guard.guarded_include_render)(
        lambda self, context: guard.guarded_include_render(self, context)
    )
    IncludeNode.render = wrapped  # type: ignore[method-assign]
    with caplog.at_level(logging.WARNING, logger="djust._include_guard"):
        guard.install()
        top = IncludeNode.render
        guard.install()
        guard.install()
    assert IncludeNode.render is top
    assert top.__wrapped__ is wrapped  # type: ignore[attr-defined]
    assert not [r for r in caplog.records if r.name == "djust._include_guard"]


def test_the_guard_is_back_after_a_mock_restores_djangos_render():
    """The class is the only state: a scope entered while a mock replaced ``render`` writes
    nothing, and the entry after the mock exits installs the guard (no cached belief to go stale)."""
    from unittest.mock import patch

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import include_guard_scope

    with patch.object(IncludeNode, "render", autospec=True):
        with include_guard_scope():
            pass
    obj = _payload("alters_data")()
    with include_guard_scope():
        out = Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj}))
    assert out == "[]"
    assert type(obj).calls == 0


def test_uninstall_restores_djangos_own_render():
    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, install, uninstall

    install()
    assert IncludeNode.render is not _ORIGINAL
    uninstall()
    assert IncludeNode.render is _ORIGINAL


def test_the_guard_is_back_after_something_else_restores_djangos_render():
    from djust._include_guard import include_guard_scope, install, uninstall

    install()
    uninstall()  # what a reload of loader_tags, or a test's own cleanup, amounts to
    obj = _payload("alters_data")()
    with include_guard_scope():
        out = Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj}))
    assert out == "[]"
    assert type(obj).calls == 0


def _include(obj: Any) -> str:
    return Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj}))


def test_a_replacement_that_does_not_wrap_the_guard_is_held_to_it(caplog):
    """#3931: a patch of ``render`` that calls Django's own (not the guard) no longer switches the
    guard off. The guard goes on top of it and delegates to it: the patch still runs, a flagged
    operand is refused inside a scope, and nothing is logged at WARNING."""
    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, _note_wrapped, include_guard_scope

    _note_wrapped.cache_clear()
    ran: list[int] = []

    def replacement(self: Any, context: Any) -> Any:
        ran.append(1)
        return _ORIGINAL(self, context)  # Django's render, not the guard

    IncludeNode.render = replacement  # type: ignore[method-assign]
    flagged = _payload("alters_data")()
    plain = _payload("plain")()
    with caplog.at_level(logging.DEBUG, logger="djust._include_guard"):
        for _ in range(3):
            with include_guard_scope():
                assert _include(flagged) == "[]"
                assert _include(plain) == "[RENDERED]"
    assert type(flagged).calls == 0
    assert type(plain).calls == 3
    # the replacement renders every include the guard lets through (a refused one renders "")
    assert len(ran) == 3, "the replacement is still what renders the include"
    assert IncludeNode.render is not replacement
    assert IncludeNode.render.__wrapped__ is replacement  # type: ignore[attr-defined]
    # outside a scope the replacement runs exactly as before, flagged operand and all
    assert _include(flagged) == "[RENDERED]" and len(ran) == 4
    records = [r for r in caplog.records if r.name == "djust._include_guard"]
    assert not [r for r in records if r.levelno >= logging.WARNING], "no warning on any entry"
    assert len(records) == 1, "one note for one replacement, not one per scope entry"
    message = records[0].getMessage()
    assert "function" in message and "RENDERED" not in message  # the type name, nothing else


def test_a_wraps_wrapper_over_djangos_render_installed_before_the_first_scope_is_guarded():
    """#3931: instrumentation that ``functools.wraps`` Django's own ``render`` before any scope ran
    used to leave the guard off; its ``__wrapped__`` is Django's, not the guard."""
    import functools

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, include_guard_scope

    seen: list[int] = []

    @functools.wraps(_ORIGINAL)
    def instrumented(self: Any, context: Any) -> Any:
        seen.append(1)
        return _ORIGINAL(self, context)

    IncludeNode.render = instrumented  # type: ignore[method-assign]
    obj = _payload("alters_data")()
    with include_guard_scope():
        assert _include(obj) == "[]"
    assert type(obj).calls == 0


def test_a_replacement_whose_wrapped_link_raises_does_not_break_scope_entry():
    """#3931: reading ``__wrapped__`` off the current render must not make every scope raise."""
    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, include_guard_scope

    class _Hostile:
        @property
        def __wrapped__(self) -> Any:
            raise RuntimeError("no")

        def __get__(self, node: Any, owner: Any = None) -> Any:
            return lambda context: _ORIGINAL(node, context)

    IncludeNode.render = _Hostile()  # type: ignore[method-assign,assignment]
    obj = _payload("alters_data")()
    with include_guard_scope():
        assert _include(obj) == "[]"
    assert type(obj).calls == 0


def test_a_deep_wraps_chain_over_the_guard_still_refuses(caplog):
    """#3931: a chain deeper than 16 links was warned about; now the outermost is held to the guard
    whatever is below it, without a WARNING."""
    import functools

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import include_guard_scope, install

    install()
    top = IncludeNode.render
    for _ in range(40):
        below = top

        @functools.wraps(below)
        def layer(self: Any, context: Any, _below: Any = below) -> Any:
            return _below(self, context)

        top = layer
    IncludeNode.render = top  # type: ignore[method-assign]
    obj = _payload("alters_data")()
    with caplog.at_level(logging.WARNING, logger="djust._include_guard"):
        with include_guard_scope():
            assert _include(obj) == "[]"
    assert not [r for r in caplog.records if r.name == "djust._include_guard"]
    assert type(obj).calls == 0


def test_repeated_scope_entries_over_the_guard_log_nothing(caplog):
    from djust._include_guard import include_guard_scope

    with caplog.at_level(logging.DEBUG, logger="djust._include_guard"):
        for _ in range(5):
            with include_guard_scope():
                pass
    assert not [r for r in caplog.records if r.name == "djust._include_guard"]


def test_a_guard_left_by_a_reloaded_copy_of_the_module_is_not_taken_for_this_ones():
    """A guard function from another copy of the module reads that copy's scope flag, which this
    copy's scopes do not set: it is wrapped, not trusted (#3931)."""
    import contextvars

    from django.template.loader_tags import IncludeNode

    from djust._include_guard import _ORIGINAL, include_guard_scope

    other_flag: contextvars.ContextVar[bool] = contextvars.ContextVar("other", default=False)

    def stale_guard(self: Any, context: Any) -> Any:
        return _ORIGINAL(self, context)  # inactive: its own flag is never set

    stale_guard._djust_include_guard = stale_guard  # type: ignore[attr-defined]
    stale_guard._djust_active = other_flag  # type: ignore[attr-defined]
    IncludeNode.render = stale_guard  # type: ignore[method-assign]
    obj = _payload("alters_data")()
    with include_guard_scope():
        assert _include(obj) == "[]"
    assert type(obj).calls == 0


def test_a_staticmethod_replacement_is_called_as_one():
    from django.template.loader_tags import IncludeNode

    from djust._include_guard import include_guard_scope

    calls: list[Any] = []

    def render(context: Any) -> str:
        calls.append(context)
        return "STATIC"

    IncludeNode.render = staticmethod(render)  # type: ignore[method-assign,assignment]
    with include_guard_scope():
        assert _include(_payload("plain")()) == "[STATIC]"
        assert _include(_payload("alters_data")()) == "[]"
    assert len(calls) == 1


def test_a_render_outside_the_scope_is_stock_django():
    """Installing the patch (a child was rendered) changes nothing for a render no view makes."""
    from djust._include_guard import install

    install()
    obj = _payload("alters_data")()
    out = django_render("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 1, out


def test_the_scope_ends_with_the_render_even_when_it_raises():
    from djust._include_guard import include_guard_scope, install

    install()
    with pytest.raises(RuntimeError):
        with include_guard_scope():
            raise RuntimeError("boom")
    obj = _payload("alters_data")()
    django_render("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 1  # no longer guarded: the scope was reset


def test_scopes_nest():
    from djust._include_guard import include_guard_scope

    obj = _payload("alters_data")()
    with include_guard_scope():
        with include_guard_scope():
            pass
        assert django_render("[{% include obj %}]", {"obj": obj}) == "[]"  # outer still active
    assert type(obj).calls == 0


def test_the_scope_is_per_thread():
    """A render on another thread is not made stricter because this thread is in a scope."""
    import threading

    from djust._include_guard import include_guard_scope

    obj = _payload("alters_data")()
    seen: list[str] = []
    with include_guard_scope():
        t = threading.Thread(
            target=lambda: seen.append(django_render("[{% include obj %}]", {"obj": obj}))
        )
        t.start()
        t.join()
    assert seen == ["[RENDERED]"]
    assert type(obj).calls == 1


def test_a_backend_template_wrapper_is_checked_on_its_inner_render():
    """Django calls ``obj.template.render``; that is the callable whose markers count."""
    from django.template import Template
    from django.template.backends.django import Template as BackendTemplate

    from djust._include_guard import include_guard_scope

    calls: list[int] = []

    class Flagged(Template):
        def render(self, context):
            calls.append(1)
            return super().render(context)

        render.alters_data = True  # type: ignore[attr-defined]

    engine = Engine()
    inner = Flagged("INNER", engine=engine)
    wrapper = BackendTemplate(inner, backend=None)  # type: ignore[arg-type]
    with include_guard_scope():
        assert (
            Engine().from_string("[{% include t %}]").render(DjangoContext({"t": wrapper})) == "[]"
        )
    assert calls == []
    # an unflagged inner template behind the same wrapper type still renders
    plain = BackendTemplate(engine.from_string("OK"), backend=None)  # type: ignore[arg-type]
    with include_guard_scope():
        assert (
            Engine().from_string("[{% include t %}]").render(DjangoContext({"t": plain})) == "[OK]"
        )


class _ShiftyAfterTwoReads:
    """``render`` is a property: reads 1 and 2 are harmless, every later read is flagged.

    Django's own "does it quack" test is read 1, so the read a guard checks is read 2. A guard
    that checks read 2 and then reads again to CALL would run a flagged callable; one that binds
    once runs read 2's.
    """

    def __init__(self) -> None:
        self.reads = 0
        self.ran: list[tuple[str, bool]] = []

    @property
    def render(self) -> Callable[..., str]:
        self.reads += 1
        label = f"read{self.reads}"
        flagged = self.reads >= 3

        def render(context: Any = None) -> str:
            self.ran.append((label, flagged))
            return label

        if flagged:
            render.alters_data = True  # type: ignore[attr-defined]
        return render


def test_the_checked_render_is_the_one_that_runs():
    from djust._include_guard import include_guard_scope

    obj = _ShiftyAfterTwoReads()
    with include_guard_scope():
        out = Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj}))
    assert not any(flagged for _label, flagged in obj.ran), obj.ran
    assert out == "[read2]"
    assert obj.ran == [("read2", False)]


def test_the_check_does_not_call_the_method():
    from djust._include_guard import include_guard_scope

    obj = _payload("raising_marker")()
    with include_guard_scope():
        assert (
            Engine().from_string("[{% include obj %}]").render(DjangoContext({"obj": obj})) == "[]"
        )
    assert type(obj).calls == 0


# --- the other places a view's or a component's context reaches Django's engine --------------
def _view_with(source: str, ctx: dict, name: str | None = None) -> Any:
    return _child(source, ctx, name=name)


def streaming_inline_site(source: str, ctx: dict) -> str:
    from asgiref.sync import async_to_sync

    view = _view_with(f"<div dj-stream='s'>{source}</div>", ctx)
    return str(async_to_sync(view._render_stream_fragment)("s"))


def streaming_named_site(source: str, ctx: dict) -> str:
    from asgiref.sync import async_to_sync

    view = _view_with(f"<div dj-stream='s'>{source}</div>", ctx, name="stream_3880.html")
    return str(async_to_sync(view._render_stream_fragment)("s"))


def layout_site(source: str, ctx: dict) -> str:
    from djust.mixins.layout import render_pending_layout

    view = _view_with("unused", ctx)
    _TEMPLATES["layout_3880.html"] = source
    return render_pending_layout(view, "layout_3880.html")


def testing_client_site(source: str, ctx: dict) -> str:
    from djust.live_view import LiveView
    from djust.testing import LiveViewTestClient

    _TEMPLATES["client_3880.html"] = source

    class _V(LiveView):
        exposure_policy = "legacy"
        template_name = "client_3880.html"

        def mount(self, request, **kwargs):
            for k, v in ctx.items():
                setattr(self, k, v)

        def get_context_data(self, **kwargs):
            return dict(ctx)

    client = LiveViewTestClient(_V)
    client.mount()
    return client.render(engine="django")


def native_site(source: str, ctx: dict) -> str:
    from unittest.mock import MagicMock, patch

    from djust.renderers import ComposeRenderer

    _TEMPLATES["native_3880.html"] = f"<Stack><Text>{source}</Text></Stack>"
    view = MagicMock()
    view.template_name = "native_3880.html"
    renderer = ComposeRenderer(view)
    with patch.object(renderer, "resolve_template", return_value="native_3880.html"):
        _html, patches_json, _version = renderer.render_with_diff(preloaded_context=dict(ctx))
    import json

    patch_list = json.loads(patches_json)
    return str(patch_list[0]["node"]["children"][0]["text"])


def suspense_site(source: str, ctx: dict) -> str:
    from djust.components.suspense import _render_fallback

    _TEMPLATES["suspense_3880.html"] = source
    return _render_fallback("suspense_3880.html", dict(ctx))


def component_fallback_site(source: str, ctx: dict) -> str:
    """``_render_template_string`` when the Rust entry is unavailable."""
    from unittest.mock import patch

    from djust import _rust
    from djust.components.base import _render_template_string

    with patch.object(_rust, "render_template", side_effect=RuntimeError("no rust")):
        return str(_render_template_string(source, dict(ctx)))


def live_component_extends_site(source: str, ctx: dict) -> str:
    """A ``LiveComponent`` whose ``template_name`` uses ``{% extends %}`` renders via ``render_to_string``."""
    from djust.components.base import LiveComponent

    _TEMPLATES["comp_base_3880.html"] = "<c>{% block body %}{% endblock %}</c>"
    _TEMPLATES["comp_3880.html"] = (
        '{% extends "comp_base_3880.html" %}{% block body %}' + source + "{% endblock %}"
    )

    class Comp(LiveComponent):
        template_name = "comp_3880.html"

        def get_context_data(self) -> dict:
            return dict(ctx)

    comp = Comp()
    comp.mount()
    html = str(comp.render())
    match = re.search(r"<c>(.*)</c>", html, re.S)
    assert match is not None, html
    return match.group(1)


def bound_component_extends_site(source: str, ctx: dict) -> str:
    """A descriptor (ADR-031) component whose ``template_name`` uses ``{% extends %}``.

    It renders through ``render_to_string`` too (the marker entry cannot follow an
    ``{% extends %}``), in the bound component's own render rather than ``LiveComponent``'s.
    """
    from djust.components.descriptors.base import LiveComponent, TypedState
    from djust.live_view import LiveView

    _TEMPLATES["bound_base_3880.html"] = "<c>{% block body %}{% endblock %}</c>"
    _TEMPLATES["bound_3880.html"] = (
        '{% extends "bound_base_3880.html" %}{% block body %}' + source + "{% endblock %}"
    )
    state = type("State", (TypedState,), {"__annotations__": {"obj": Any}, "obj": None})
    comp = type("Nav3880", (LiveComponent,), {"State": state, "template_name": "bound_3880.html"})
    page = type("Page3880", (LiveView,), {"exposure_policy": "legacy", "nav": comp(obj=ctx["obj"])})
    html = str(page().nav)
    match = re.search(r"<c>(.*)</c>", html, re.S)
    assert match is not None, html
    return match.group(1)


def bridged_inclusion_tag_site(source: str, ctx: dict) -> str:
    """An inclusion tag bridged into a LiveView's Rust render: Django renders the tag's template.

    With no djust backend among the project's engines the tag's template comes from Django's
    own loader and engine (``template_libraries._LoaderBackend``), not from a guarded entry.
    """
    from unittest.mock import patch

    from djust import template_libraries

    _TEMPLATES["inclusion_3880.html"] = source
    with patch.dict(template_libraries._extra_libraries, {"probe3880": __name__}):
        return liveview_render("{% load probe3880 %}{% inclusion_3880 %}", ctx)


def gallery_site(source: str, ctx: dict) -> str:
    """The component gallery renders a snippet with the gallery LiveView's mixin state."""
    from djust.components.gallery.views import _render_component_cards

    html = _render_component_cards(
        [
            {
                "name": "Probe",
                "label": "Probe",
                "type": "tag",
                "variants": [{"name": "v", "template": source, "context": ctx}],
            }
        ]
    )
    match = re.search(r'<div class="variant-preview">(.*?)</div>', html, re.S)
    assert match is not None, html
    return match.group(1)


EXTRA_SITES = [
    pytest.param(streaming_inline_site, id="stream-fragment-inline"),
    pytest.param(streaming_named_site, id="stream-fragment-named"),
    pytest.param(layout_site, id="pending-layout"),
    pytest.param(testing_client_site, id="test-client-django-engine"),
    pytest.param(native_site, id="native-renderer"),
    pytest.param(suspense_site, id="suspense-fallback"),
    pytest.param(component_fallback_site, id="component-django-fallback"),
    pytest.param(live_component_extends_site, id="live-component-extends"),
    pytest.param(bound_component_extends_site, id="bound-component-extends"),
    pytest.param(bridged_inclusion_tag_site, id="bridged-inclusion-tag"),
    pytest.param(gallery_site, id="component-gallery"),
]


@pytest.mark.parametrize("site", EXTRA_SITES)
@pytest.mark.parametrize("kind", KINDS_REFUSED)
def test_other_view_and_component_renders_refuse_a_flagged_render(site, kind):
    obj = _payload(kind)()
    out = site("[{% include obj %}]", {"obj": obj})
    assert type(obj).calls == 0, f"render() ran on {site.__name__}"
    assert out == "[]"


@pytest.mark.parametrize("site", EXTRA_SITES)
@pytest.mark.parametrize("kind", KINDS_ALLOWED)
def test_other_view_and_component_renders_still_render_an_unflagged_render(site, kind):
    obj = _payload(kind)()
    out = site("[{% include obj %}]", {"obj": obj})
    assert out == "[RENDERED]"
    assert type(obj).calls >= 1


# --- the net: a new Django-engine render must be inside the scope, or say why not -------------
#: Sites under ``python/djust`` that render through Django's engine and are deliberately NOT inside
#: ``include_guard_scope``, each with its reason. A key is ``"dir/"`` (everything under it),
#: ``"file.py"`` (everything in it) or ``"file.py::Qualified.function"``. A template in one of these
#: is shipped by the framework or rendered for a non-view caller; no view or component state
#: reaches an ``{% include %}`` in it.
_UNGUARDED_ENGINE_RENDERS = {
    "admin_ext/plugins.py": "an admin dashboard widget: Django-admin Python builds the context, no LiveView",
    "bug_capture_views.py": "a framework-owned replay page rendered with framework data",
    "management/commands/generate_sw.py": "a build-time management command writing a file",
    "template_tags/pwa.py": "the PWA tags render fixed framework source with string operands only",
    "theming/": "framework-owned theme templates, rendered from theme tokens and tag arguments",
    "mixins/request.py::RequestMixin._get_turn_body": (
        "`wrapper_template` receives only `liveview_content`, an already-rendered string"
    ),
}

#: A call that LOADS a Django template; its result's ``.render`` is a render.
_LOADERS = {"from_string", "get_template", "select_template"}
_ENGINE_CTORS = {"Template"}
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _unguarded_engine_renders(source: str) -> list[tuple[str, int]]:
    """``(qualified function, line)`` of each Django-engine render in ``source`` outside the scope.

    A render is: a reference to ``render_to_string`` (called, handed to ``sync_to_async``, or taken
    off a module: ``loader.render_to_string``); a ``.render`` taken off a template loader's result
    (``get_template(..).render``, ``Template(..).render``) or off a name bound to one in the same
    function or an enclosing one (``t = get_template(..)`` then ``t.render(c)``). ``from x import
    Template as T`` and the like are resolved. "Inside the scope" is lexical: the node sits in the
    body of a ``with`` whose items call ``include_guard_scope``, in the SAME function: a function
    (or lambda) defined inside the block runs later, outside it.
    """
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    # `from django.template import Template as T` -> {"T": "Template"}
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name

    def call_name(call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Name):
            return aliases.get(func.id, func.id)
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def is_loader_call(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and call_name(node) in (_LOADERS | _ENGINE_CTORS)

    def function_of(node: ast.AST) -> ast.AST | None:
        while node in parents:
            node = parents[node]
            if isinstance(node, _FUNCTIONS):
                return node
        return None

    def qualname(node: ast.AST) -> str:
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                names.append(node.name)
        return ".".join(reversed(names)) or "<module>"

    def dotted(node: ast.AST) -> str | None:
        """``self.tpl`` -> ``"self.tpl"``; ``None`` for anything that is not a name chain."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    def bound_targets(node: ast.AST) -> list[tuple[ast.AST, ast.expr]]:
        """``(target, value)`` pairs a statement or expression binds, every syntax (#3931): ``=``,
        an annotated or a walrus binding, a tuple unpack (each element, conservatively), a loop
        variable and a ``with ... as`` target."""
        if isinstance(node, ast.Assign):
            return [(t, node.value) for t in node.targets]
        if isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            return [(node.target, node.value)]
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            return [(node.target, node.iter)]
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return [
                (item.optional_vars, item.context_expr)
                for item in node.items
                if item.optional_vars is not None
            ]
        return []

    def flatten(target: ast.AST) -> list[ast.AST]:
        if isinstance(target, (ast.Tuple, ast.List)):
            return [n for elt in target.elts for n in flatten(elt)]
        if isinstance(target, ast.Starred):
            return flatten(target.value)
        return [target]

    # names a function (or the module) bound to a loaded template; attribute chains
    # (``self.tpl = get_template(..)``) for the whole file, since any method may read them
    template_names: dict[ast.AST | None, set[str]] = {}
    template_attributes: set[str] = set()
    for node in ast.walk(tree):
        for target, value in bound_targets(node):
            if not any(is_loader_call(n) for n in ast.walk(value)):
                continue
            owner = function_of(node)
            for leaf in flatten(target):
                if isinstance(leaf, ast.Name):
                    template_names.setdefault(owner, set()).add(leaf.id)
                elif isinstance(leaf, ast.Attribute) and dotted(leaf):
                    template_attributes.add(dotted(leaf))  # type: ignore[arg-type]

    def bound_to_a_template(name: str, node: ast.AST) -> bool:
        owner = function_of(node)
        while True:  # this function, then each enclosing one, then the module
            if name in template_names.get(owner, set()):
                return True
            if owner is None:
                return False
            owner = function_of(owner)

    def in_scope(node: ast.AST) -> bool:
        child = node
        while child in parents:
            parent = parents[child]
            if isinstance(parent, _FUNCTIONS) and child in parent.body:
                return False
            if isinstance(parent, ast.Lambda) and child is parent.body:
                return False
            if isinstance(parent, (ast.With, ast.AsyncWith)) and child in parent.body:
                for item in parent.items:
                    expr = item.context_expr
                    if isinstance(expr, ast.Call) and call_name(expr) == "include_guard_scope":
                        return True
            child = parent
        return False

    def is_template_receiver(receiver: ast.AST, node: ast.AST) -> bool:
        if is_loader_call(receiver):
            return True
        if isinstance(receiver, ast.Name):
            # ``Template.render(t, c)``: the method taken off the class and CALLED unbound (a bare
            # ``Template.render`` compared by identity, as ``template/operands.py`` does, is not)
            if aliases.get(receiver.id, receiver.id) in _ENGINE_CTORS:
                parent = parents.get(node)
                return isinstance(parent, ast.Call) and parent.func is node
            return bound_to_a_template(receiver.id, node)
        if isinstance(receiver, ast.Attribute):
            return dotted(receiver) in template_attributes
        return False

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        site = False
        if isinstance(node, ast.Name) and aliases.get(node.id, node.id) == "render_to_string":
            site = isinstance(node.ctx, ast.Load)
        elif isinstance(node, ast.Attribute) and node.attr == "render_to_string":
            site = isinstance(node.ctx, ast.Load)
        elif isinstance(node, ast.Attribute) and node.attr == "render":
            site = is_template_receiver(node.value, node)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "render"
        ):
            site = is_template_receiver(node.args[0], node)  # getattr(t, "render")(c)
        if site and not in_scope(node):
            found.append((qualname(node), node.lineno))
    return found


def _exempt(rel: str, qual: str) -> bool:
    for key in _UNGUARDED_ENGINE_RENDERS:
        if key.endswith("/") and rel.startswith(key):
            return True
        if key == rel or key == f"{rel}::{qual}":
            return True
    return False


def test_every_django_engine_render_is_inside_the_scope_or_says_why_not():
    """Per site, not per file: a new unguarded render fails here even in a file that guards others.

    Walks the AST of every module under ``python/djust`` (tests excluded) for the shapes of a
    Django-engine render (see :func:`_unguarded_engine_renders`). A shape it cannot see -- a render
    reached through a helper that takes the template as an argument -- is not covered; that is the
    limit of a static net, and the behavioural matrix above is what proves each guarded site.
    """
    root = Path(__file__).resolve().parents[1] / "djust"
    undecided = []
    guarded_files = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in "/" + rel or path.name.startswith("test_") or rel == "_include_guard.py":
            continue
        text = path.read_text()
        if "include_guard_scope" in text:
            guarded_files += 1
        for qual, line in _unguarded_engine_renders(text):
            if not _exempt(rel, qual):
                undecided.append(f"{rel}::{qual} (line {line})")
    assert not undecided, (
        "renders through Django's engine outside include_guard_scope (#3880): "
        f"{undecided} -- wrap the render, or list it in _UNGUARDED_ENGINE_RENDERS with a reason"
    )
    assert guarded_files >= 8, "the scan found almost no guarded file: the root is wrong"
    stale = [k for k in _UNGUARDED_ENGINE_RENDERS if not (root / k.split("::")[0]).exists()]
    assert not stale, stale


@pytest.mark.parametrize(
    "source,expected",
    [
        pytest.param(
            "from django.template.loader import render_to_string\n"
            "def a(c):\n    with include_guard_scope():\n        return render_to_string('x', c)\n"
            "def b(c):\n    return render_to_string('y', c)\n",
            [("b", 6)],
            id="a-new-function-in-a-file-that-already-guards",
        ),
        pytest.param(
            "# include_guard_scope\ndef a(c):\n    return render_to_string('x', c)\n",
            [("a", 3)],
            id="the-word-in-a-comment-guards-nothing",
        ),
        pytest.param(
            "def a(c):\n    t = get_template('x')\n    return t.render(c)\n",
            [("a", 3)],
            id="a-name-bound-to-a-loaded-template",
        ),
        pytest.param(
            "def a(c):\n    loaded = engine.from_string('x')\n    return loaded.render(c)\n",
            [("a", 3)],
            id="any-variable-name-bound-to-from_string",
        ),
        pytest.param(
            "def a(c):\n    return Template('x').render(c)\n",
            [("a", 2)],
            id="a-template-constructor-render",
        ),
        pytest.param(
            "async def a(c):\n    return await sync_to_async(render_to_string)('x', c)\n",
            [("a", 2)],
            id="render_to_string-handed-to-a-thread",
        ),
        pytest.param(
            "def a(c):\n    t = get_template('x')\n    with include_guard_scope():\n        return t.render(c)\n",
            [],
            id="a-guarded-loaded-template",
        ),
        pytest.param(
            "def a(c):\n    with other():\n        return render_to_string('x', c)\n",
            [("a", 3)],
            id="a-with-block-that-is-not-the-scope",
        ),
        pytest.param(
            "def a(c):\n    with include_guard_scope():\n        pass\n    return render_to_string('x', c)\n",
            [("a", 4)],
            id="after-the-scope-block-ends",
        ),
        pytest.param(
            "def a(c):\n    with other(render_to_string('x', c)), include_guard_scope():\n        pass\n",
            [("a", 2)],
            id="a-render-in-a-with-item-runs-before-the-scope",
        ),
        pytest.param(
            "from django.template import Template as T\ndef a(c):\n    return T('x').render(c)\n",
            [("a", 3)],
            id="an-aliased-template-constructor",
        ),
        pytest.param(
            "from django.template.loader import render_to_string as rts\ndef a(c):\n    return rts('x', c)\n",
            [("a", 3)],
            id="an-aliased-render_to_string",
        ),
        pytest.param(
            "from django.template import loader\ndef a(c):\n    return loader.render_to_string('x', c)\n",
            [("a", 3)],
            id="render_to_string-as-a-module-attribute",
        ),
        pytest.param(
            "from django.template.loader import get_template as gt\ndef a(c):\n    t = gt('x')\n    return t.render(c)\n",
            [("a", 4)],
            id="an-aliased-loader-bound-to-a-name",
        ),
        pytest.param(
            "def a(c):\n    t = get_template('x')\n    def b():\n        return t.render(c)\n    return b\n",
            [("a.b", 4)],
            id="a-template-bound-in-the-enclosing-function",
        ),
        pytest.param(
            "def a(c):\n    with include_guard_scope():\n        def later():\n            return render_to_string('x', c)\n    return later\n",
            [("a.later", 4)],
            id="a-function-defined-in-the-scope-runs-outside-it",
        ),
        pytest.param(
            "def a(c):\n    with include_guard_scope():\n        f = lambda: render_to_string('x', c)\n    return f\n",
            [("a", 3)],
            id="a-lambda-defined-in-the-scope-runs-outside-it",
        ),
        pytest.param(
            "from django.template import Template as T\ndef a(c):\n    with include_guard_scope():\n        return T('x').render(c)\n",
            [],
            id="an-aliased-constructor-inside-the-scope",
        ),
        pytest.param(
            "def a(node, c):\n    return node.render(c)\n",
            [],
            id="a-node-render-is-not-an-engine-render",
        ),
        pytest.param(
            "def a(c):\n    t = get_template('x')\n    return getattr(t, 'render')(c)\n",
            [("a", 3)],
            id="render-taken-with-getattr",
        ),
        pytest.param(
            "from django.template import Template\ndef a(t, c):\n    return Template.render(t, c)\n",
            [("a", 3)],
            id="an-unbound-template-render",
        ),
        pytest.param(
            "class V:\n    def setup(self):\n        self.tpl = get_template('x')\n"
            "    def show(self, c):\n        return self.tpl.render(c)\n",
            [("V.show", 5)],
            id="a-template-held-on-self",
        ),
        pytest.param(
            "def a(c):\n    if (t := get_template('x')):\n        return t.render(c)\n",
            [("a", 3)],
            id="a-walrus-binding",
        ),
        pytest.param(
            "def a(c):\n    t, n = get_template('x'), 1\n    return t.render(c)\n",
            [("a", 3)],
            id="a-tuple-unpack-binding",
        ),
        pytest.param(
            "def a(c):\n    for t in (get_template('x'), get_template('y')):\n        t.render(c)\n",
            [("a", 3)],
            id="a-loop-variable-binding",
        ),
        pytest.param(
            "def a(c):\n    t: Any = get_template('x')\n    return t.render(c)\n",
            [("a", 3)],
            id="an-annotated-binding",
        ),
        pytest.param(
            "class V:\n    def show(self, c):\n        return self.other.render(c)\n",
            [],
            id="an-attribute-never-bound-to-a-template-is-not-a-site",
        ),
        pytest.param(
            "from django.template import Template\ndef a(f):\n    return f is Template.render\n",
            [],
            id="an-unbound-render-compared-not-called",
        ),
    ],
)
def test_the_census_goes_red_for_a_synthetic_unguarded_site(source, expected):
    """The net can fail: each synthetic site is one a new contributor could add."""
    assert _unguarded_engine_renders(source) == expected
