"""#3936 / #3949: ``state_models_as_handles`` is ON by default since 2.0 (maintainer decision
2026-11-22), and ``False`` is the kill-switch.

Pattern: ``python/djust/tests/test_loop_cache_default_on_2062.py``. Each default-on assertion runs
with NO config override; the gate-offs patch the default back to ``False`` and turn them red.
Real ``User`` / ``Group`` rows (#1650); Django on real instances is the oracle (#2727).
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.test import override_settings

from djust.config import LiveViewConfig
from djust.mixins.context import state_models_as_handles_enabled
from djust.testing import LiveViewTestClient
from python.tests.test_state_models_as_handles_3936 import (  # noqa: F401
    FLAG_OFF,
    _body,
    _ctx,
    _library,
    _SpyRustView,
    _view,
    people,
    world,
)

REBINDING = "{% with q=u|default:u %}{{ q.groups.count }}{% endwith %}"


def _render_default(source: str, factory, policy: str) -> str:
    """No ``LIVEVIEW_CONFIG`` override: whatever the shipped default is."""
    client = LiveViewTestClient(_view(source, factory, policy))
    client.mount()
    return _body(client.render())


def test_the_default_config_turns_the_overlay_on():
    assert LiveViewConfig._defaults["state_models_as_handles"] is True
    with override_settings(LIVEVIEW_CONFIG={}):
        assert state_models_as_handles_enabled() is True


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_default_config_reads_a_relation_through_a_filtered_rebinding(people, policy):
    expected = DjangoTemplate(REBINDING).render(DjangoContext(_ctx()))
    assert expected == "2"
    assert _render_default(REBINDING, _ctx, policy) == expected


def test_gate_off_a_false_default_renders_the_rebinding_empty(people, monkeypatch):
    """Gate-off: with the default patched back to ``False`` the explicit view loses the read (the
    1.x gap), so the default-on cells above are not vacuous."""
    from djust.config import config

    from djust.mixins import rust_bridge

    monkeypatch.setitem(LiveViewConfig._defaults, "state_models_as_handles", False)
    # #4290's JIT of a single Model answers the rebinding as well; its gate off.
    monkeypatch.setattr(rust_bridge, "_plain_rows_take_the_jit", lambda *a: False)
    config._rebuild_from_settings(keep_programmatic=True)
    try:
        assert state_models_as_handles_enabled() is False
        assert _render_default(REBINDING, _ctx, "explicit") == ""
    finally:
        monkeypatch.undo()
        config._rebuild_from_settings(keep_programmatic=True)
    assert state_models_as_handles_enabled() is True


def test_explicit_false_is_the_kill_switch(people, monkeypatch):
    """``False`` restores the dict: the rebinding renders empty, a custom filter gets a ``dict``,
    and a sync sends no overlay to a view that never had one."""
    import json

    from djust.mixins import rust_bridge

    # #4290's JIT of a single Model answers the rebinding as well; its gate off.
    monkeypatch.setattr(rust_bridge, "_plain_rows_take_the_jit", lambda *a: False)

    with override_settings(LIVEVIEW_CONFIG=FLAG_OFF):
        assert _render_default(REBINDING, _ctx, "explicit") == ""
        out = _render_default("{% load cc3949 %}{{ u|cc3949_filter }}", _ctx, "explicit")
        assert json.loads(out)["type"] == "dict"
        client = LiveViewTestClient(_view(REBINDING, _ctx))
        client.mount()
        client.render()
        view = client.view_instance
        spy = _SpyRustView(view._rust_view)
        view._rust_view = spy
        view._sync_state_to_rust()
        assert spy.calls == []
    # and by default the same filter is handed the floor proxy
    out = _render_default("{% load cc3949 %}{{ u|cc3949_filter }}", _ctx, "explicit")
    assert json.loads(out)["type"] == "_SidecarModelProxy"


def test_a_config_error_falls_back_to_the_dict_path(monkeypatch):
    """``state_models_as_handles_enabled`` fails safe: a config that raises means no overlay."""
    import djust.config as config_module

    def boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config_module, "get_config", boom)
    assert state_models_as_handles_enabled() is False


def test_the_users_model_is_a_real_user(people):
    assert isinstance(_ctx()["u"], User)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("false", False),
        ("0", False),
        ("off", False),
        (0, False),
        ("true", True),
        (1, True),
        ("maybe", True),  # not a boolean spelling: the default, with a warning
        (None, True),
    ],
)
def test_the_kill_switch_is_read_strictly(value, expected, caplog):
    """Review N1: ``bool("false")`` is ``True``; the switch is parsed as a boolean spelling, so a
    kill-switch read from the environment cannot silently fail to engage."""
    import logging

    with caplog.at_level(logging.WARNING, logger="djust.config"):
        with override_settings(LIVEVIEW_CONFIG={"state_models_as_handles": value}):
            assert state_models_as_handles_enabled() is expected
    assert any("state_models_as_handles" in r.getMessage() for r in caplog.records)


def test_a_bool_switch_written_in_code_is_read_strictly_too():
    from djust.config import config

    config.set("state_models_as_handles", "false")
    try:
        assert config.get("state_models_as_handles") is False
        assert state_models_as_handles_enabled() is False
    finally:
        # drop the code-written value, so no later test inherits it
        config._programmatic.pop("state_models_as_handles", None)
        config.reload_from_settings()
    assert state_models_as_handles_enabled() is True
