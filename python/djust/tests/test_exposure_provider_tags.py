"""ADR-038 E2-2: template tags that need the rendering view, under explicit.

An explicit render context never carries the raw ``view``. ``{% dj_activity %}``
registered its activity through ``context.get("view")``, so under explicit it
silently registered nothing; ``{% colocated_hook %}`` silently fell back to the
global hook name; the form tags and filters rendered their "no FormMixin"
error. They now resolve the view from the render's active-view thread-local.

The Rust engine has no handler for any of these tags (a LiveView root using
one fails with "Invalid block tag"), so they run only where djust renders a
LiveView template with Django's engine: embedded and sticky children. These
tests mount such a child through the real ``ViewRuntime`` and re-render it
through a real child event. Each explicit case has a legacy control.
"""

import json

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.test import override_settings

from djust import LiveView, event_handler
from djust.decorators import state
from djust.forms import FormMixin
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = "djust.tests.test_exposure_provider_tags"


class TagChild(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "panel_child"
    count = state(0)
    template = (
        "{% load live_tags %}<div>"
        '{% dj_activity "drawer" visible=False %}<b>drawer</b>{% enddj_activity %}'
        '{% colocated_hook "Chart" %}hook{% endcolocated_hook %}'
        "</div>"
    )

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1


class ContactForm(forms.Form):
    email = forms.EmailField(initial="FORM_VALUE_MARKER")


class FormChild(FormMixin, LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "form_child"
    form_class = ContactForm
    template = (
        "{% load live_tags %}<div>"
        '<i>{{ view|field_value:"email" }}</i>'
        '{% if view|has_errors:"email" %}<s>HAS_ERRORS</s>{% endif %}'
        '{% live_errors view "email" %}'
        '{% live_field view "email" %}'
        "{% live_form view %}"
        "</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.field_errors = {"email": ["FIELD_ERROR_MARKER"]}


def _parent(child):
    return type(
        child.__name__ + "Parent",
        (LiveView,),
        {
            "__module__": MODULE,
            "exposure_policy": "explicit",
            "template": (
                "<div dj-root>{% load live_tags %}{% live_render "
                f'"{MODULE}.{child.__name__}" sticky=True %}}</div>'
            ),
        },
    )


TagChildParent = _parent(TagChild)
FormChildParent = _parent(FormChild)


@pytest.fixture(autouse=True)
def staged(monkeypatch, settings):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [MODULE]


def _policy(monkeypatch, policy, *classes):
    for view_class in classes:
        monkeypatch.setattr(view_class, "exposure_policy", policy)


async def _mount(parent):
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": MODULE + "." + parent.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport


def _mount_html(transport):
    return next(frame["html"] for frame in transport.sent if frame.get("type") == "mount")


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_dj_activity_registers_on_the_rendering_view(monkeypatch, policy):
    _policy(monkeypatch, policy, TagChild, TagChildParent)
    runtime, transport = await _mount(TagChildParent)
    child = runtime.view_instance._get_child_view("panel_child")
    html = _mount_html(transport)
    assert 'data-djust-activity="drawer"' in html and "hidden" in html
    # The tag registers the declared state the server-side gate reads.
    assert child._djust_activities.get("drawer", {}).get("visible") is False
    assert child.is_activity_visible("drawer") is False

    if policy == "legacy":
        # Legacy stops here: the event re-render path (render_embedded_child_html)
        # gives a legacy child no ``view`` in context, so it never re-registered
        # there. That is unchanged by construction; see the E2-2 report.
        return
    # A child event re-renders through render_embedded_child_html; the
    # registration must hold on that path too.
    child._djust_activities = {}
    transport.sent.clear()
    await runtime.dispatch_event(
        {"type": "event", "event": "bump", "params": {"view_id": "panel_child"}}
    )
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "embedded_update" for frame in transport.sent)
    assert child.count == 1
    assert child.is_activity_visible("drawer") is False


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
@pytest.mark.parametrize("namespacing", ["strict", "off"])
async def test_colocated_hook_namespacing_uses_the_rendering_view(
    monkeypatch, settings, policy, namespacing
):
    settings.DJUST_CONFIG = {"hook_namespacing": namespacing} if namespacing == "strict" else {}
    _policy(monkeypatch, policy, TagChild, TagChildParent)
    runtime, transport = await _mount(TagChildParent)
    html = _mount_html(transport)
    if namespacing == "strict":
        assert f'data-hook="{MODULE}.TagChild.Chart"' in html
    else:
        assert 'data-hook="Chart"' in html

    if policy == "legacy":
        return  # As above: the legacy re-render path has no view in context.
    transport.sent.clear()
    await runtime.dispatch_event(
        {"type": "event", "event": "bump", "params": {"view_id": "panel_child"}}
    )
    update = json.dumps([f for f in transport.sent if f.get("type") == "embedded_update"])
    assert update != "[]"
    expected = f"{MODULE}.TagChild.Chart" if namespacing == "strict" else "Chart"
    assert f'data-hook=\\"{expected}\\"' in update


@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_form_tags_and_filters_resolve_the_rendering_view(monkeypatch, policy):
    _policy(monkeypatch, policy, FormChild, FormChildParent)
    runtime, transport = await _mount(FormChildParent)
    html = _mount_html(transport)
    assert "ERROR: View does not have" not in html
    assert ">FORM_VALUE_MARKER</i>" in html  # field_value
    assert ">HAS_ERRORS</s>" in html  # has_errors
    # live_errors, then live_field and live_form: each renders the error once.
    assert html.count("FIELD_ERROR_MARKER") == 3
    # live_field and live_form each render the input.
    assert html.count('name="email"') == 2


async def test_active_view_is_not_written_into_the_explicit_context(monkeypatch):
    """The fallback reads a thread-local; the raw view never enters context."""
    seen = []

    def spy(self, **kwargs):
        context = LiveView.get_context_data(self, **kwargs)
        seen.append(dict(context))
        return context

    monkeypatch.setattr(TagChild, "get_context_data", spy)
    runtime, _ = await _mount(TagChildParent)
    child = runtime.view_instance._get_child_view("panel_child")
    assert child.is_activity_visible("drawer") is False
    assert seen and all("view" not in context for context in seen)
    assert all(value is not child for context in seen for value in context.values())
