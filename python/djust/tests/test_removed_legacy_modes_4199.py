"""2.0 refuses the legacy exposure and parameter policies and the 1.3 settings (#4199).

The legacy ``exposure_policy`` value, ``@event_handler(parameter_policy="legacy")``,
``cancel_async_on_disconnect = True`` and the ``event_parameter_policy`` /
``reauth_on_event`` / ``reauth_server_turn_interval`` config keys each raise
``ImproperlyConfigured`` naming the replacement. Only
``djust_exposure_inventory`` may import 1.3 code, inside
``djust._removed_settings.upgrade_inspection()``, to read and rewrite it.

The legacy statement is built from a name, never written out, so no test file
states it literally.
"""

from __future__ import annotations

import sys
import threading
import warnings
from contextlib import nullcontext
from io import StringIO
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from djust import LiveView
from djust._removed_settings import (
    MIGRATION_GUIDE_URL,
    removed_settings_in_use,
    upgrade_inspection,
    upgrade_inspection_active,
)
from djust.decorators import event_handler, server_function

LEGACY = "leg" + "acy"
REPO = Path(__file__).resolve().parents[3]


def _view(name: str = "OldView", bases: tuple = (LiveView,), **body):
    """A view class built with ``type()``, as a class statement would build it."""
    return type(name, bases, {"__module__": __name__, "template": "<div dj-root></div>", **body})


# --------------------------------------------------------------------------
# exposure_policy
# --------------------------------------------------------------------------


def test_a_legacy_exposure_policy_is_refused_when_the_class_is_created():
    with pytest.raises(ImproperlyConfigured) as refused:
        _view("CartView", exposure_policy=LEGACY)
    message = str(refused.value)
    assert "CartView" in message and "exposure_policy" in message
    assert "removed in djust 2.0" in message
    # The replacement, not just the refusal.
    assert "get_context_data()" in message and "state()" in message
    assert "djust_exposure_inventory --fix" in message
    assert MIGRATION_GUIDE_URL in message


def test_a_legacy_exposure_policy_in_source_is_refused():
    source = "class FromSource(LiveView):\n    exposure_policy = %r\n" % LEGACY
    with pytest.raises(ImproperlyConfigured, match="FromSource"):
        exec(source, {"LiveView": LiveView, "__name__": __name__})  # noqa: S102


def test_a_mixin_that_states_the_legacy_policy_is_refused_where_it_meets_liveview():
    mixin = type("LegacyMixin", (), {"exposure_policy": LEGACY})
    with pytest.raises(ImproperlyConfigured, match="removed in djust 2.0"):
        _view("Mixed", (mixin, LiveView))


@pytest.mark.parametrize("inherited", [False, True])
def test_explicit_is_accepted_without_a_warning(inherited):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        base = _view("Base", exposure_policy="explicit")
        view_class = _view("Child", (base,)) if inherited else base
        assert view_class() is not None


@pytest.mark.parametrize(
    "value, shown",
    [
        ("explict", "'explict'"),
        ("", "''"),
        (None, "a value of type NoneType"),
        (True, "a value of type bool"),
        (property(lambda self: "SECRET_SENTINEL"), "a value of type property"),
    ],
)
def test_an_unknown_policy_is_refused_without_the_repr_of_a_non_string(value, shown):
    with pytest.raises(ImproperlyConfigured) as refused:
        _view("Odd", exposure_policy=value)
    message = str(refused.value)
    assert "an unknown value" in message and shown in message
    assert "'explicit' is the only exposure_policy" in message
    assert "SECRET_SENTINEL" not in message


def test_a_near_miss_of_the_legacy_spelling_names_the_replacement():
    with pytest.raises(ImproperlyConfigured) as refused:
        _view("Near", exposure_policy="Legacy ")
    assert "removed in djust 2.0" in str(refused.value)
    assert "get_context_data()" in str(refused.value)


def test_a_policy_assigned_after_the_class_statement_is_refused_on_construction():
    view_class = _view("Late", exposure_policy="explicit")
    view_class.exposure_policy = LEGACY
    with pytest.raises(ImproperlyConfigured, match="Late"):
        view_class()
    # The permit lets a class be created; it never lets one be constructed.
    with upgrade_inspection(), pytest.raises(ImproperlyConfigured, match="Late"):
        view_class()


def test_a_policy_set_on_the_instance_is_refused():
    view_class = _view("Kwarg", exposure_policy="explicit")
    with pytest.raises(ImproperlyConfigured, match="Kwarg sets exposure_policy = %r" % LEGACY):
        view_class(exposure_policy=LEGACY)


def test_a_str_subclass_is_refused_without_running_its_repr():
    class Sly(str):
        def __repr__(self):
            raise AssertionError("SECRET_REPR_SENTINEL")

    with pytest.raises(ImproperlyConfigured) as refused:
        _view("Subclassed", exposure_policy=Sly("explicit"))
    message = str(refused.value)
    assert "a value of type Sly (a str subclass, which is not accepted)" in message
    assert "SECRET_REPR_SENTINEL" not in message


# --------------------------------------------------------------------------
# parameter_policy
# --------------------------------------------------------------------------


def _handler(self, value: str = ""):
    pass


def _fresh_handler():
    """A new function each call: the decorators mark the function they apply to."""

    def _handler(self, value: str = ""):
        pass

    return _handler


@pytest.mark.parametrize("decorator", [event_handler, server_function])
def test_a_legacy_parameter_policy_is_refused_when_the_decorator_applies(decorator):
    with pytest.raises(ImproperlyConfigured) as refused:
        decorator(parameter_policy=LEGACY)(_handler)
    message = str(refused.value)
    assert "%s.%s" % (__name__, _handler.__qualname__) in message
    assert "parameter_policy" in message and "removed in djust 2.0" in message
    assert "dj-value-*" in message and MIGRATION_GUIDE_URL in message


@pytest.mark.parametrize("decorator", [event_handler, server_function])
@pytest.mark.parametrize("policy", ["strict", None])
def test_strict_and_none_are_accepted(decorator, policy):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert callable(decorator(parameter_policy=policy)(_fresh_handler()))


def test_an_unknown_parameter_policy_is_a_value_error():
    with pytest.raises(ValueError, match="parameter_policy"):
        event_handler(parameter_policy="bogus")(_fresh_handler())


# --------------------------------------------------------------------------
# Config keys
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, replacement",
    [
        ("event_parameter_policy", "strict event parameters are the only policy"),
        ("reauth_on_event", "re-authorized against a freshly loaded session"),
        ("reauth_server_turn_interval", "every server turn is re-authorized"),
    ],
)
@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
def test_each_removed_config_key_is_named(setting, key, replacement):
    class Settings:
        pass

    settings = Settings()
    setattr(settings, setting, {key: "anything"})
    problems = removed_settings_in_use(settings)
    assert len(problems) == 1, problems
    assert "%s[%r] was removed in djust 2.0" % (setting, key) in problems[0]
    assert replacement in problems[0]


def test_a_clean_project_starts():
    from django.apps import apps
    from django.conf import settings

    assert removed_settings_in_use(settings) == []
    apps.get_app_config("djust").ready()


# --------------------------------------------------------------------------
# cancel_async_on_disconnect
# --------------------------------------------------------------------------


def test_cancel_async_on_disconnect_is_refused_when_the_class_is_created():
    with pytest.raises(ImproperlyConfigured) as refused:
        _view("Tasks", exposure_policy="explicit", cancel_async_on_disconnect=True)
    message = str(refused.value)
    assert "cancel_async_on_disconnect" in message and "removed in djust 2.0" in message
    assert "survive_disconnect=True" in message


def test_cancel_async_on_disconnect_false_is_accepted():
    assert _view("Off", exposure_policy="explicit", cancel_async_on_disconnect=False)()


class _AssignsInMount(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>tasks</div>"

    def mount(self, request, **kwargs):
        self.cancel_async_on_disconnect = True


@override_settings(DEBUG=True)
@pytest.mark.django_db
def test_cancel_async_on_disconnect_set_in_mount_is_refused_on_the_mount_path(rf):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = rf.get("/tasks/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.session.create()
    with pytest.raises(ImproperlyConfigured, match="cancel_async_on_disconnect"):
        _AssignsInMount.as_view()(request)


# --------------------------------------------------------------------------
# The permit
# --------------------------------------------------------------------------


def test_the_permit_creates_each_1_3_spelling_and_never_constructs_it():
    with upgrade_inspection():
        assert upgrade_inspection_active()
        policy = _view("Policy", exposure_policy=LEGACY)
        tasks = _view("Tasks", exposure_policy="explicit", cancel_async_on_disconnect=True)
        handler = event_handler(parameter_policy=LEGACY)(_fresh_handler())
    assert not upgrade_inspection_active()
    assert handler._djust_decorators["event_handler"]["parameter_policy"] == LEGACY
    for view_class in (policy, tasks):
        with pytest.raises(ImproperlyConfigured):
            view_class()


def test_the_permit_lets_through_only_cancel_async_on_disconnect():
    from djust._removed_settings import UPGRADE_PERMITTED_VIEW_ATTRIBUTES

    assert UPGRADE_PERMITTED_VIEW_ATTRIBUTES == frozenset({"cancel_async_on_disconnect"})
    # Refused before the permit existed (#4209): still refused inside it.
    with upgrade_inspection(), pytest.raises(ImproperlyConfigured, match="use_actors"):
        _view("Actors", exposure_policy="explicit", use_actors=True)


def _plain_component_view():
    from djust.components.components.badge import Badge

    return _view("SharedBadge", exposure_policy=LEGACY, badge=Badge(label="shared"))


def test_a_1_3_plain_component_is_created_under_the_permit_and_refused_on_mount(rf):
    """1.3 views declare plain components in the class body (#4045); the permit
    creates such a class so --fix can read it, and the mount path refuses it."""
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust._exposure import ExposureConfigurationError
    from djust.components.components.badge import Badge

    with pytest.raises(ExposureConfigurationError, match="badge"):
        _view("Outside", exposure_policy="explicit", badge=Badge(label="x"))
    with upgrade_inspection():
        view_class = _plain_component_view()
    view_class.exposure_policy = "explicit"  # what --fix leaves; the component stays
    request = rf.get("/shared/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with override_settings(DEBUG=True), pytest.raises(ExposureConfigurationError, match="badge"):
        view_class.as_view()(request)


def test_the_permit_resets_when_its_block_raises():
    with pytest.raises(RuntimeError):
        with upgrade_inspection():
            raise RuntimeError("inside")
    assert not upgrade_inspection_active()
    with pytest.raises(ImproperlyConfigured):
        _view("After", exposure_policy=LEGACY)


def test_a_thread_started_inside_the_permit_does_not_inherit_it():
    seen = []
    with upgrade_inspection():
        thread = threading.Thread(target=lambda: seen.append(upgrade_inspection_active()))
        thread.start()
        thread.join()
    assert seen == [False]


def test_only_the_refusals_and_the_inventory_command_name_the_permit():
    package = REPO / "python" / "djust"
    listed = [
        path.relative_to(REPO).as_posix()
        for path in package.rglob("*.py")
        if "tests" not in path.relative_to(package).parts
        and "upgrade_inspection" in path.read_text(encoding="utf-8")
    ]
    assert set(listed) == {
        "python/djust/_removed_settings.py",
        "python/djust/live_view.py",
        "python/djust/decorators.py",
        "python/djust/management/commands/djust_exposure_inventory.py",
    }


# --------------------------------------------------------------------------
# The command imports 1.3 code under the permit
# --------------------------------------------------------------------------

OLD_MODULE = """\
from djust import LiveView


class CartView(LiveView):
    POLICY
    template = "<div dj-root>{{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0
"""


@pytest.fixture
def old_module(tmp_path, monkeypatch, request):
    name = "old13_%s" % request.node.name.replace("[", "_").replace("]", "")
    (tmp_path / ("%s.py" % name)).write_text(
        OLD_MODULE.replace("POLICY", "exposure_policy = %r" % LEGACY)
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path, name
    sys.modules.pop(name, None)


def _fix_dry_run(root: Path, name: str) -> str:
    out = StringIO()
    call_command(
        "djust_exposure_inventory",
        "--fix",
        "--dry-run",
        "--root",
        str(root),
        "--view",
        "%s.CartView" % name,
        stdout=out,
    )
    return out.getvalue()


def test_the_inventory_command_reads_and_fixes_1_3_code(old_module):
    root, name = old_module
    assert name not in sys.modules  # the command's own import_string runs under the permit
    output = _fix_dry_run(root, name)
    assert "-    exposure_policy = %r" % LEGACY in output, output
    assert not upgrade_inspection_active()


def test_gate_off_without_the_permit_the_command_cannot_import_it(old_module, monkeypatch):
    root, name = old_module
    monkeypatch.setattr("djust._removed_settings.upgrade_inspection", nullcontext)
    with pytest.raises(ImproperlyConfigured, match="removed in djust 2.0"):
        _fix_dry_run(root, name)
