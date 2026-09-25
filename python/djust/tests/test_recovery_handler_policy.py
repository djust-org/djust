"""ADR-036 owner decision R1: dj-auto-recover handlers run under legacy policy.

A handler that a literal ``dj-auto-recover`` in the view's own template targets
receives the ``_form_values`` / ``_data_attrs`` envelope, which no strict
signature can declare. Dispatch therefore resolves it to the legacy policy even
in a strict project or when it is declared strict, and the startup check warns
about an explicit strict declaration (V019). The template is server-owned, so
a client cannot claim this downgrade for any other handler.
"""

import pytest

from djust import LiveView
from djust.config import config
from djust.decorators import event_handler
from djust.validation import get_handler_parameter_policy, validate_handler_params

CALLS: list = []
ENVELOPE = {"_form_values": {"title": "T"}, "_data_attrs": {"canvas-id": "main"}}


class RecoveryView(LiveView):
    template = (
        '<div dj-root><div dj-auto-recover="restore_state" data-canvas-id="main">'
        '<input name="title"></div><button dj-click="pick">x</button></div>'
    )

    def mount(self, request, **kwargs):
        self.restored = None

    @event_handler
    def restore_state(self, **kwargs):
        CALLS.append(("restore_state", kwargs))

    @event_handler
    def pick(self, item_id: int):
        CALLS.append(("pick", item_id))


class DeclaredStrictRecoveryView(RecoveryView):
    @event_handler(parameter_policy="strict")
    def restore_state(self, **kwargs):
        CALLS.append(("restore_state", kwargs))


@pytest.fixture(autouse=True)
def _reset():
    old = config.get("event_parameter_policy", "legacy")
    CALLS.clear()
    yield
    config.set("event_parameter_policy", old)
    CALLS.clear()


def _dispatch(view_class, event, params):
    from asgiref.sync import async_to_sync
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    view = view_class()
    view.mount(None)
    runtime, transport = _make_runtime_with_view(view)
    async_to_sync(runtime.dispatch_event)({"type": "event", "event": event, "params": params})
    return transport


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [RecoveryView, DeclaredStrictRecoveryView])
def test_recovery_still_works_in_a_strict_project(view_class):
    config.set("event_parameter_policy", "strict")
    transport = _dispatch(view_class, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)], transport.sent
    view = view_class()
    assert get_handler_parameter_policy(view.restore_state) == "legacy"


@pytest.mark.django_db
def test_other_handlers_of_the_view_stay_strict():
    config.set("event_parameter_policy", "strict")
    view = RecoveryView()
    assert get_handler_parameter_policy(view.pick) == "strict"
    assert not validate_handler_params(view.pick, {"item_id": "7x"}, "pick")["valid"]
    _dispatch(RecoveryView, "pick", {"item_id": "7"})
    assert CALLS == [("pick", 7)]


@pytest.mark.django_db
def test_the_public_manifest_advertises_recovery_handlers_as_legacy():
    from djust._parameter_metadata import parameter_contract_manifest

    config.set("event_parameter_policy", "strict")
    view = RecoveryView()
    handlers = parameter_contract_manifest(view)["owners"][0]["handlers"]
    assert handlers["restore_state"] == {"policy": "legacy"}
    assert handlers["pick"]["policy"] == "strict"


def _messages():
    from djust.checks.parameters import check_event_parameter_contracts

    return [
        (m.id, m.level)
        for m in check_event_parameter_contracts(None)
        if __name__ in m.msg and "restore_state" in m.msg
    ]


def test_explicit_strict_recovery_handler_is_a_startup_warning():
    assert _messages() == [("djust.V019", 30)]


def test_strict_project_reports_nothing_else_for_recovery_handlers():
    config.set("event_parameter_policy", "strict")
    # The unannotated **kwargs recovery handler would be a V016 error if it
    # were strict; it is not, and only the explicit declaration warns.
    assert _messages() == [("djust.V019", 30)]


@pytest.mark.django_db
def test_legacy_project_is_unchanged():
    transport = _dispatch(RecoveryView, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)], transport.sent
    assert get_handler_parameter_policy(RecoveryView().pick) == "legacy"


# --- targets discovered from what the server rendered ------------------------

MOD = __name__


class _RecoveryBase(LiveView):
    def mount(self, request, **kwargs):
        self.show = False
        self.target = "restore_state"
        self.note = ""

    def get_context_data(self, **kwargs):
        return {"show": self.show, "target": self.target, "note": self.note}

    @event_handler
    def restore_state(self, **kwargs):
        CALLS.append(("restore_state", kwargs))

    @event_handler(parameter_policy="strict")
    def pick(self, item_id: int):
        CALLS.append(("pick", item_id))

    @event_handler(parameter_policy="legacy")
    def reveal(self, **kwargs):
        self.show = True

    @event_handler(parameter_policy="legacy")
    def echo(self, text="", **kwargs):
        self.note = text


class IncludedRecovery(_RecoveryBase):
    template_name = "rec_r1/included.html"


class ExtendedRecovery(_RecoveryBase):
    template_name = "rec_r1/child.html"


class ToggledRecovery(_RecoveryBase):
    template_name = "rec_r1/toggled.html"


class DynamicRecovery(_RecoveryBase):
    template_name = "rec_r1/dynamic.html"


@pytest.fixture
def templates(tmp_path):
    from django.test import override_settings
    from djust.utils import clear_template_dirs_cache

    d = tmp_path / "templates" / "rec_r1"
    d.mkdir(parents=True)
    form = '<div dj-auto-recover="restore_state"><input name="title"></div>'
    (d / "form.html").write_text(form)
    (d / "included.html").write_text(
        f'<div dj-root dj-view="{MOD}.IncludedRecovery">{{% include "rec_r1/form.html" %}}'
        "<p>{{ note }}</p></div>"
    )
    (d / "base.html").write_text(
        f'<div dj-root dj-view="{MOD}.ExtendedRecovery">{form}{{% block body %}}{{% endblock %}}</div>'
    )
    (d / "child.html").write_text(
        '{% extends "rec_r1/base.html" %}{% block body %}<p>{{ note }}</p>{% endblock %}'
    )
    (d / "toggled.html").write_text(
        f'<div dj-root dj-view="{MOD}.ToggledRecovery">'
        '{% if show %}{% include "rec_r1/form.html" %}{% endif %}<p>{{ note }}</p></div>'
    )
    (d / "dynamic.html").write_text(
        f'<div dj-root dj-view="{MOD}.DynamicRecovery">'
        '<div dj-auto-recover="{{ target }}"></div><p>{{ note }}</p></div>'
    )
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
        LIVEVIEW_ALLOWED_MODULES=[MOD],
    ):
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()


def _mounted(view_class):
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    view = view_class()
    view.mount(None)
    view.render_with_diff()
    return view, _make_runtime_with_view(view)[0]


def _send(runtime, event, params):
    from asgiref.sync import async_to_sync

    async_to_sync(runtime.dispatch_event)({"type": "event", "event": event, "params": params})


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [IncludedRecovery, ExtendedRecovery, DynamicRecovery])
def test_rendered_recovery_targets_follow_includes_extends_and_dynamic_values(
    templates, view_class
):
    from djust.validation import recovery_handler_names

    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(view_class)
    # The class-level scan follows includes and parents (ADR-037 row 18); a
    # computed target is seen only in the render.
    assert ("restore_state" in recovery_handler_names(view_class)) is (
        view_class is not DynamicRecovery
    )
    assert get_handler_parameter_policy(view.restore_state) == "legacy"
    _send(runtime, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)]
    assert get_handler_parameter_policy(view.pick) == "strict"


@pytest.mark.django_db
def test_a_recovery_form_in_a_conditional_include_is_legacy_from_mount(templates):
    """The class-level scan keeps every {% if %} branch (ADR-037 row 18), so a
    recovery form an event reveals later is already a legacy target at mount."""
    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(ToggledRecovery)
    assert get_handler_parameter_policy(view.restore_state) == "legacy"
    _send(runtime, "reveal", {})
    assert get_handler_parameter_policy(view.restore_state) == "legacy"
    _send(runtime, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)]


@pytest.mark.django_db
def test_a_client_cannot_claim_the_downgrade(templates):
    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(IncludedRecovery)
    # Neither an envelope-shaped payload nor client text that spells the
    # attribute (rendered escaped) makes another handler a recovery target.
    _send(runtime, "echo", {"text": '<div dj-auto-recover="pick"></div>'})
    # The rendered text spells the attribute (a regex over the HTML would be
    # fooled); only parsed element attributes count.
    assert '&lt;div dj-auto-recover="pick"&gt;' in view.render_with_diff()[0]
    assert get_handler_parameter_policy(view.pick) == "strict"
    _send(runtime, "pick", dict(ENVELOPE))
    assert CALLS == []


@pytest.mark.parametrize(
    "html",
    [
        '<div dj-auto-recover="restore_state"></div>',
        "<div DJ-AUTO-RECOVER='restore_state'><p dj-auto-recover=\"other\"></p></div>",
        '<pre>&lt;div dj-auto-recover="escaped"&gt;</pre>',
        '<div dj-auto-recover=""></div><div dj-click="restore_state"></div>',
        '<div dj-auto-recover="not valid"></div>',
    ],
)
def test_the_render_scan_agrees_with_the_binding_parser(html):
    """ADR-037 row 19: the per-render scan is kept for cost, pinned to the parser."""
    from djust._template_bindings import EVENT_NAME, markup_bindings
    from djust.validation import _RENDERED_RECOVERY, note_rendered_recovery_targets

    class Probe:
        pass

    probe = Probe()
    note_rendered_recovery_targets(probe, html)
    parsed = {
        b.name
        for b in markup_bindings(html)
        if b.directive == "dj-auto-recover" and b.name and EVENT_NAME.match(b.name)
    }
    assert _RENDERED_RECOVERY[probe] == parsed
