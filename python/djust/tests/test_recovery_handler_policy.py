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


class SafeHtmlRecovery(_RecoveryBase):
    template_name = "rec_r1/safe.html"


class SafeHtmlDynamicRecovery(_RecoveryBase):
    template_name = "rec_r1/safe_dynamic.html"


class FlashRecovery(_RecoveryBase):
    template_name = "rec_r1/flash.html"


class ActivityRecovery(_RecoveryBase):
    """The recovery form sits inside a djust block tag's body."""

    template_name = "rec_r1/activity.html"


class InstanceTemplateNameRecovery(_RecoveryBase):
    """The class names a template without targets; mount() picks one with."""

    template_name = "rec_r1/plain.html"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.template_name = "rec_r1/other.html"


class InstanceTemplateRecovery(_RecoveryBase):
    template_name = "rec_r1/plain.html"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.template = '<div dj-root><div dj-auto-recover="pick"></div><p>{{ note }}</p></div>'


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
    (d / "safe.html").write_text(
        f'<div dj-root dj-view="{MOD}.SafeHtmlRecovery">{{% include "rec_r1/form.html" %}}'
        "<p>{{ note|safe }}</p></div>"
    )
    (d / "safe_dynamic.html").write_text(
        f'<div dj-root dj-view="{MOD}.SafeHtmlDynamicRecovery">'
        '<div dj-auto-recover="{{ target }}"></div><p>{{ note|safe }}</p></div>'
    )
    (d / "flash.html").write_text(
        "{% load djust_flash %}"
        f'<div dj-root dj-view="{MOD}.FlashRecovery">{{% include "rec_r1/form.html" %}}'
        "{% dj_flash %}<p>{{ note|safe }}</p></div>"
    )
    (d / "activity.html").write_text(
        "{% load live_tags %}"
        f'<div dj-root dj-view="{MOD}.ActivityRecovery">'
        '{% dj_activity "panel" visible=True %}{% include "rec_r1/form.html" %}{% enddj_activity %}'
        "<p>{{ note|safe }}</p></div>"
    )
    (d / "plain.html").write_text("<div dj-root>" + form + "<p>{{ note|safe }}</p></div>")
    (d / "other.html").write_text(
        '<div dj-root><div dj-auto-recover="pick"></div><p>{{ note|safe }}</p></div>'
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
        from djust.validation import _RECOVERY_HANDLERS

        # The per-class scan cache outlives settings: a check that ran without
        # these templates (the V019 tests above) must not answer for them.
        _RECOVERY_HANDLERS.clear()
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()
            _RECOVERY_HANDLERS.clear()


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


@pytest.mark.django_db
def test_rendered_user_html_cannot_claim_the_downgrade(templates):
    """#3127: user HTML rendered with ``|safe`` (a sanitizer that keeps unknown
    attributes) is real markup in the render, but the template declares every
    recovery target literally, so only those count."""
    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(SafeHtmlRecovery)
    _send(runtime, "echo", {"text": '<div dj-auto-recover="pick"></div>'})
    assert '<div dj-auto-recover="pick" dj-id=' in view.render_with_diff()[0]
    assert get_handler_parameter_policy(view.pick) == "strict"
    assert get_handler_parameter_policy(view.restore_state) == "legacy"
    _send(runtime, "pick", dict(ENVELOPE))
    assert CALLS == []
    _send(runtime, "restore_state", dict(ENVELOPE))
    assert CALLS == [("restore_state", ENVELOPE)]


def test_a_djust_block_tags_body_is_scanned(templates):
    """A djust block tag's own output is transparent, and its body is the
    template's markup: a target declared inside it is seen, and the scan
    stays complete (so rendered user HTML is not read)."""
    from djust.validation import _recovery_scan

    assert _recovery_scan(ActivityRecovery) == (frozenset({"restore_state"}), True)


@pytest.mark.django_db
def test_a_djust_tag_does_not_reopen_the_downgrade(templates):
    """PR #3159 review: ``{% dj_flash %}`` made the scan a gap, so the page went
    back to trusting rendered user HTML. djust's own tags are transparent to
    the recovery scan (none of them emits ``dj-auto-recover``)."""
    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(FlashRecovery)
    _send(runtime, "echo", {"text": '<div dj-auto-recover="pick"></div>'})
    assert 'dj-auto-recover="pick"' in view.render_with_diff()[0]
    assert get_handler_parameter_policy(view.pick) == "strict"
    assert get_handler_parameter_policy(view.restore_state) == "legacy"


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [InstanceTemplateNameRecovery, InstanceTemplateRecovery])
def test_a_template_chosen_on_the_instance_keeps_its_targets(templates, view_class):
    """PR #3159 review: the class-level scan describes the class template, not
    one mount() assigns, so the render is read for such a view (as on main)."""
    config.set("event_parameter_policy", "strict")
    view, _runtime = _mounted(view_class)
    assert 'dj-auto-recover="pick"' in view.render_with_diff()[0]
    assert get_handler_parameter_policy(view.pick) == "legacy"


@pytest.mark.django_db
def test_a_computed_target_still_reads_the_render(templates):
    """The documented limit of #3127: where the template computes a target, the
    render is the only source, so ``|safe`` user HTML there must not keep
    ``dj-*`` attributes."""
    config.set("event_parameter_policy", "strict")
    view, runtime = _mounted(SafeHtmlDynamicRecovery)
    assert get_handler_parameter_policy(view.restore_state) == "legacy"
    _send(runtime, "echo", {"text": '<div dj-auto-recover="pick"></div>'})
    assert get_handler_parameter_policy(view.pick) == "legacy"


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
    """ADR-037 row 19: the per-render scan is kept for cost, pinned to the parser.

    It runs only where a recovery target can change a policy (strict), so the
    parity check does too (PR #3122 review).
    """
    from djust._template_bindings import EVENT_NAME, markup_bindings
    from djust.config import config
    from djust.validation import _RENDERED_RECOVERY, note_rendered_recovery_targets

    config.set("event_parameter_policy", "strict")

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


def test_no_djust_tag_or_template_emits_a_recovery_target():
    """The premise that lets the recovery scan treat djust's own tags as
    transparent (PR #3159 review): no markup djust renders carries
    ``dj-auto-recover``. The only files that mention it read or document the
    attribute; a new one fails here and must be reviewed against that premise.
    """
    from pathlib import Path

    import djust

    package = Path(djust.__file__).parent
    readers = {
        "_template_bindings.py",
        "checks/bindings.py",
        "checks/parameters.py",
        "schema.py",
        "validation.py",
    }
    found = set()
    for path in package.rglob("*"):
        relative = path.relative_to(package).as_posix()
        if not path.is_file() or relative.startswith(("tests/", "static/")):
            continue
        if path.suffix not in (".py", ".html", ".txt", ".jinja", ".j2"):
            continue
        if "dj-auto-recover" in path.read_text(encoding="utf-8", errors="ignore"):
            found.add(relative)
    assert found == readers
    crates = package.parents[1] / "crates"
    if crates.is_dir():
        rust = [p for p in crates.rglob("*.rs") if "auto-recover" in p.read_text(errors="ignore")]
        assert rust == []
