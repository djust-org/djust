"""Settings 2.0 removed fail startup with their replacement (#3500)."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from djust._removed_settings import (
    REMOVED_CONFIG_KEYS,
    REMOVED_FLAT_SETTINGS,
    reject_removed_settings,
    removed_settings_in_use,
)
from djust.config import LiveViewConfig


@pytest.mark.parametrize("name", sorted(REMOVED_FLAT_SETTINGS))
def test_a_flat_alias_names_its_nested_replacement(name):
    [problem] = removed_settings_in_use(SimpleNamespace(**{name: "x"}))
    assert "settings.%s was removed in djust 2.0" % name in problem
    assert REMOVED_FLAT_SETTINGS[name] in problem


@pytest.mark.parametrize("setting", ["LIVEVIEW_CONFIG", "DJUST_CONFIG"])
@pytest.mark.parametrize("key", sorted(REMOVED_CONFIG_KEYS))
def test_a_kill_switch_key_is_reported_in_either_dict(setting, key):
    # True as well as False: the key does nothing either way now.
    for value in (False, True):
        [problem] = removed_settings_in_use(SimpleNamespace(**{setting: {key: value}}))
        assert "%s[%r] was removed in djust 2.0" % (setting, key) in problem


def test_live_settings_are_not_reported():
    settings = SimpleNamespace(
        LIVEVIEW_CONFIG={"websocket_compression": False, "loop_render_cache_enabled": False},
        DJUST_CONFIG={"STATE_BACKEND": "redis", "REDIS_URL": "redis://h:6379/0"},
        DJUST_STATE_SNAPSHOT_ENABLED=False,
    )
    assert removed_settings_in_use(settings) == []


def test_reject_raises_with_every_problem():
    with override_settings(
        DJUST_STATE_BACKEND="redis://h:6379/0",
        LIVEVIEW_CONFIG={"template_auto_call": False},
    ):
        with pytest.raises(ImproperlyConfigured) as exc:
            reject_removed_settings()
    message = str(exc.value)
    assert "settings.DJUST_STATE_BACKEND" in message
    assert "DJUST_CONFIG['STATE_BACKEND']" in message
    assert "LIVEVIEW_CONFIG['template_auto_call']" in message


def test_reject_is_silent_for_a_clean_project():
    reject_removed_settings()


def test_the_removed_keys_have_no_defaults():
    defaults = LiveViewConfig._defaults
    assert not set(REMOVED_CONFIG_KEYS) & set(defaults)
    assert "vdom_cache_ttl_seconds" in defaults["service_worker"], "control"


def test_ready_rejects_before_registering_checks():
    """``ready()`` must call the guard; ASGI servers never run system checks."""
    import inspect

    from djust.apps import DjustConfig

    code = "\n".join(
        ln
        for ln in inspect.getsource(DjustConfig.ready).splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "reject_removed_settings()" in code
    assert code.index("reject_removed_settings()") < code.index("import djust.checks")


_REMOVED_RUST_SWITCHES = (
    "set_template_auto_call",
    "template_auto_call_enabled",
    "set_virtual_keyed_ops",
    "virtual_keyed_ops_enabled",
    "set_django_value_repr",
    "django_value_repr_enabled",
)


def test_the_rust_kill_switches_are_gone():
    from djust import _rust

    for name in _REMOVED_RUST_SWITCHES:
        assert not hasattr(_rust, name), name
        assert not hasattr(_rust.RustLiveView, name), name


def test_no_framework_module_wires_the_removed_kill_switches():
    import pathlib

    import djust

    root = pathlib.Path(djust.__file__).parent
    offenders = [
        f"{path.relative_to(root)}: {name}"
        for path in root.rglob("*.py")
        if "tests" not in path.parts
        for name in _REMOVED_RUST_SWITCHES
        if name in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert offenders == []
