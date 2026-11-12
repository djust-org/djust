"""ADR-038 Step R, PR 0c: the mixins djust ships render under explicit exposure.

Each of these mixins publishes public attributes that the legacy attribute
walk turned into template context. An explicit view renders nothing it is not
given, so before this change a template that read ``{{ online_count }}``,
``{{ table_page }}`` or ``{{ tutorial_running }}`` silently rendered empty.

Every test mounts a real view through ``ViewRuntime``, reads the mixin's names
out of the mount frame, and fires one of the mixin's (or the view's) events
through ``dispatch_event``. Each has a legacy control, so the two policies are
shown to render the same text.
"""

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.forms import formset_factory
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureError, provider_owners
from djust.components.mixins.data_table import DataTableMixin
from djust.formsets import FormSetHelpersMixin
from djust.presence import PresenceMixin
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_runtime import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport
from djust.theming.mixins import ThemeMixin
from djust.tutorials import TutorialMixin, TutorialStep

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

MODULE = __name__
POLICIES = ["legacy", "explicit"]


async def _mount(view_class):
    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": MODULE + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    assert runtime.view_instance is not None
    return runtime, transport


def _mount_html(transport):
    return next(frame["html"] for frame in transport.sent if frame.get("type") == "mount")


async def _event(runtime, transport, name, **params):
    transport.sent.clear()
    await runtime.dispatch_event({"type": "event", "event": name, "params": params})
    assert not transport.errors, transport.errors
    return [frame for frame in transport.sent if frame.get("event_name") == name]


def _texts(frames):
    return [
        patch.get("text")
        for frame in frames
        for patch in frame.get("patches", ())
        if patch.get("type") == "SetText"
    ]


# --------------------------------------------------------------------------
# ThemeMixin
# --------------------------------------------------------------------------


class ThemeView(ThemeMixin, LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><i>{{ theme_mode }}|{{ theme_preset }}|{{ theme_resolved_mode }}</i>"
        "<b>{% if theme_head %}HEAD{% endif %}</b><u>{{ n }}</u></div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.n = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("processors", ["none", "project"])
async def test_theme_mixin_renders_its_state_names(monkeypatch, policy, processors):
    """``theme_mode`` / ``theme_preset`` / ``theme_resolved_mode`` render.

    ``processors="none"`` removes every context processor, so the values can
    only come from the mixin: a project is not required to install
    ``theme_context``. ``"project"`` keeps the demo project's processors,
    ``theme_context`` among them, to show the two sources do not collide.
    """
    monkeypatch.setattr(ThemeView, "exposure_policy", policy)
    if processors == "none":
        monkeypatch.setattr(ThemeView, "_get_context_processors", lambda self: [])
    runtime, transport = await _mount(ThemeView)
    html = _mount_html(transport)
    assert "<i" in html and ">system|default|light</i>" in html, html
    assert ">HEAD</b>" in html, html

    frames = await _event(runtime, transport, "bump")
    assert _texts(frames) == ["1"], transport.sent


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("processors", ["none", "project"])
async def test_theme_mixin_view_value_wins_after_a_switch(monkeypatch, policy, processors):
    """A mode switched over the socket renders on the next render.

    ``set_theme_mode`` skips its own render (the switch is applied in the
    browser), so the following event is what re-renders the template. With
    the project's ``theme_context`` processor installed the view's value
    still renders, and the two sources do not collide.
    """
    monkeypatch.setattr(ThemeView, "exposure_policy", policy)
    if processors == "none":
        monkeypatch.setattr(ThemeView, "_get_context_processors", lambda self: [])
    runtime, transport = await _mount(ThemeView)
    await _event(runtime, transport, "set_theme_mode", mode="dark")
    assert runtime.view_instance.theme_mode == "dark"
    frames = await _event(runtime, transport, "bump")
    assert "dark|default|dark" in _texts(frames), transport.sent


# --------------------------------------------------------------------------
# PresenceMixin
# --------------------------------------------------------------------------


class PresenceView(PresenceMixin, LiveView):
    exposure_policy = "explicit"
    presence_key = "explicit-framework-mixins"
    template = "<div dj-root><i>[{{ online_count }}]</i><u>{{ n }}</u></div>"

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)

    @event_handler()
    def arrive(self, **kwargs):
        self.n += 1
        self.online_count = 3


@pytest.mark.parametrize("policy", POLICIES)
async def test_presence_mixin_renders_online_count(monkeypatch, policy):
    monkeypatch.setattr(PresenceView, "exposure_policy", policy)
    runtime, transport = await _mount(PresenceView)
    # Not set before the mixin sets it: the walk rendered nothing either.
    assert ">[]</i>" in _mount_html(transport)

    frames = await _event(runtime, transport, "arrive")
    assert "[3]" in _texts(frames), transport.sent


async def test_presence_provider_is_render_only():
    assert provider_owners(PresenceView)["online_count"] == "djust.presence"
    runtime, transport = await _mount(PresenceView)
    await _event(runtime, transport, "arrive")
    view = runtime.view_instance
    # Rendered, but not browser-readable data.
    assert "online_count" not in view.get_state()


# --------------------------------------------------------------------------
# TutorialMixin
# --------------------------------------------------------------------------


class TutorialView(TutorialMixin, LiveView):
    exposure_policy = "explicit"
    tutorial_steps = [TutorialStep(target="#a", message="hi", timeout=0.01)]
    template = (
        "<div dj-root><i>{{ tutorial_running }}|{{ tutorial_current_step }}|"
        "{{ tutorial_total_steps }}|{{ default_highlight_class }}</i>"
        "<s>[{{ tutorial_steps }}]</s><u>{{ n }}</u></div>"
    )

    def mount(self, request, **kwargs):
        self.n = 0

    def get_context_data(self, **kwargs):
        return super().get_context_data(n=self.n, **kwargs)

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1
        self.tutorial_current_step = 0
        self.tutorial_running = True


@pytest.mark.parametrize("policy", POLICIES)
async def test_tutorial_mixin_renders_its_progress(monkeypatch, policy):
    monkeypatch.setattr(TutorialView, "exposure_policy", policy)
    runtime, transport = await _mount(TutorialView)
    html = _mount_html(transport)
    assert ">False|-1|1|tour-highlight</i>" in html, html
    # The step objects are not template data under either policy (#694).
    assert ">[]</s>" in html, html

    # One of the mixin's own handlers, then a render that shows new progress.
    await _event(runtime, transport, "cancel_tutorial")
    frames = await _event(runtime, transport, "bump")
    assert "True|0|1|tour-highlight" in _texts(frames), transport.sent


# --------------------------------------------------------------------------
# DataTableMixin
# --------------------------------------------------------------------------


class TableView(DataTableMixin, LiveView):
    exposure_policy = "explicit"
    table_columns = [{"key": "name", "label": "Name"}]
    table_page_size = 1
    table_model = dict  # configuration, never rendered
    template = (
        "<div dj-root><i>{{ table_page }}/{{ table_total_pages }}</i>"
        "<p>{% for row in table_rows %}{{ row.name }}{% endfor %}</p>"
        "<u>{{ table_page_event }}|{{ table_page_size }}</u>"
        "<s>[{{ table_model }}{{ table_queryset }}]</s></div>"
    )

    def mount(self, request, **kwargs):
        self.init_table_state()
        self.table_total_pages = 3
        self.refresh_table()

    def refresh_table(self):
        self.table_rows = [{"name": "row%d" % self.table_page}]


@pytest.mark.parametrize("policy", POLICIES)
async def test_data_table_mixin_renders_state_and_configuration(monkeypatch, policy):
    monkeypatch.setattr(TableView, "exposure_policy", policy)
    runtime, transport = await _mount(TableView)
    html = _mount_html(transport)
    assert ">1/3</i>" in html and ">row1</p>" in html, html
    assert ">on_table_page|1</u>" in html, html

    frames = await _event(runtime, transport, "on_table_page", value="2")
    assert sorted(_texts(frames)) == ["2/3", "row2"], transport.sent


async def test_data_table_provider_never_renders_model_or_queryset():
    owners = provider_owners(TableView)
    assert owners["table_rows"] == owners["table_page_event"] == "djust.data_table"
    assert "table_model" not in owners and "table_queryset" not in owners
    runtime, transport = await _mount(TableView)
    assert ">[]</s>" in _mount_html(transport)
    context = runtime.view_instance.get_context_data()
    assert "table_model" not in context and "table_queryset" not in context
    assert "table_rows" not in runtime.view_instance.get_state()


async def test_data_table_names_are_reserved_for_the_provider():
    """An application write of a provider key is a collision, not a silent override."""
    runtime, _transport = await _mount(TableView)
    with pytest.raises(ExposureError):
        await sync_to_async(runtime.view_instance.get_context_data)(table_rows=[])


# --------------------------------------------------------------------------
# FormSetHelpersMixin
# --------------------------------------------------------------------------


class AddressForm(forms.Form):
    street = forms.CharField(required=False)


AddressFormSet = formset_factory(AddressForm, extra=0)


class FormSetView(FormSetHelpersMixin, LiveView):
    exposure_policy = "explicit"
    formset_classes = {"addresses": AddressFormSet}
    template = "<div dj-root><i>rows={{ addresses.total_form_count }}</i><s>[{{ other }}]</s></div>"

    def mount(self, request, **kwargs):
        self.addresses = AddressFormSet(prefix="addresses")
        self.other = "UNDECLARED"
        self._formset_data = {}


@pytest.mark.parametrize("policy", POLICIES)
async def test_formset_helpers_mixin_renders_the_declared_formset(monkeypatch, policy):
    monkeypatch.setattr(FormSetView, "exposure_policy", policy)
    runtime, transport = await _mount(FormSetView)
    assert ">rows=0</i>" in _mount_html(transport), _mount_html(transport)

    frames = await _event(runtime, transport, "add_row", formset="addresses")
    assert "rows=1" in _texts(frames), transport.sent


async def test_formset_provider_renders_only_declared_names():
    assert set(provider_owners(FormSetView)) >= {"addresses"}
    assert "other" not in provider_owners(FormSetView)
    runtime, transport = await _mount(FormSetView)
    # A public attribute the mixin does not own is not rendered for it.
    assert ">[]</s>" in _mount_html(transport)
