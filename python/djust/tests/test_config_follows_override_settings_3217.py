"""djust's config singleton follows ``override_settings`` (#3217).

``test_event_reauth_1777`` called ``config.reset()`` inside
``@override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True})``. The reset
re-read the override into the process-wide cache, and nothing re-read it when
the override exited. ``reauth_on_event=True`` then closed the socket on the
first event of ``test_model_form_acceptance_adr035``'s reconnect test whenever
the two ran in that order on one worker:

    pytest -p no:randomly \\
      "python/djust/tests/test_event_reauth_1777.py::test_reauth_on_closes_socket_when_user_logged_out_midsession" \\
      python/djust/tests/test_model_form_acceptance_adr035.py::test_reconnect_keeps_unsaved_input_and_revalidates_it
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from djust.config import config


def test_a_reset_inside_an_override_does_not_outlive_it():
    """The polluter's exact shape, in-process."""
    before = config.get("reauth_on_event")
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": not before}):
        config.reset()
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_an_override_takes_effect_without_a_manual_reset():
    before = config.get("reauth_on_event")
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": not before}):
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_djust_config_is_followed_too():
    before = config.get("reauth_on_event")
    with override_settings(DJUST_CONFIG={"reauth_on_event": not before}):
        assert config.get("reauth_on_event") is (not before)
    assert config.get("reauth_on_event") == before


def test_an_unrelated_setting_change_does_not_reload_the_config():
    """Only the settings the config reads trigger a reload, so a value set
    programmatically survives other tests' ``override_settings``."""
    config.set("probe_3217", "kept")
    try:
        with override_settings(LIVEVIEW_ALLOWED_MODULES=["x"]):
            assert config.get("probe_3217") == "kept"
        assert config.get("probe_3217") == "kept"
    finally:
        config.reset()


# --- #3213 re-review: code-set values survive reloads -----------------------


def test_a_code_set_value_survives_an_override_of_liveview_config():
    """The re-review probe: ``config.set`` stands in for a project's
    ``AppConfig.ready()``. One override of LIVEVIEW_CONFIG itself must not
    drop it, neither inside nor after."""
    config.set("css_framework", "tailwind_probe")
    try:
        with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": False}):
            assert config.get("css_framework") == "tailwind_probe"
        assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_an_override_that_sets_the_key_wins_inside_and_the_code_value_returns():
    config.set("css_framework", "tailwind_probe")
    try:
        with override_settings(LIVEVIEW_CONFIG={"css_framework": "plain"}):
            assert config.get("css_framework") == "plain"
        assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_an_override_copying_the_base_settings_keeps_the_code_value():
    """``{**settings.LIVEVIEW_CONFIG, other: x}`` repeats the base value for
    the key; that is not the override setting it."""
    from django.conf import settings

    config.set("css_framework", "tailwind_probe")
    try:
        base = dict(getattr(settings, "LIVEVIEW_CONFIG", None) or {})
        with override_settings(LIVEVIEW_CONFIG={**base, "reauth_on_event": True}):
            assert config.get("css_framework") == "tailwind_probe"
    finally:
        config.reset()


def test_update_and_dotted_set_survive_too():
    config.update({"render_labels": False})
    config.set("bootstrap5.field_class", "probe-control")
    try:
        with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
            assert config.get("render_labels") is False
            assert config.get("bootstrap5.field_class") == "probe-control"
        assert config.get("render_labels") is False
        assert config.get("bootstrap5.field_class") == "probe-control"
    finally:
        config.reset()


def test_an_explicit_reset_still_discards_code_set_values():
    before = config.get("css_framework")
    config.set("css_framework", "tailwind_probe")
    config.reset()
    assert config.get("css_framework") == before
    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        assert config.get("css_framework") == before


#: Settings ``djust/config.py`` reads that deliberately do NOT reload the
#: config, each with the reason. Anything else it reads must be in
#: ``_CONFIG_SETTINGS``.
_READ_WITHOUT_RELOAD = {
    "DEBUG": "_validate_config: only decides whether to log production warnings",
}


def _settings_reads(source: str) -> set:
    """Every Django setting ``source`` reads, whatever function it is in (#3218).

    Covers ``settings.X``, ``getattr(settings, "X")`` and ``hasattr(settings,
    "X")`` under any alias ``django.conf.settings`` is imported as, and a
    ``getattr(settings, name)`` whose ``name`` is the loop variable of a
    ``for`` over a module-level literal table (``_SERVICE_WORKER_ALIASES``).
    Any other non-literal read fails, because the scan cannot tell what it
    reads.
    """
    import ast

    tree = ast.parse(source)
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "django.conf"
        for alias in node.names
        if alias.name == "settings"
    }
    assert aliases, "the scan found no `from django.conf import settings`"
    tables = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }
    # loop variable -> the names it takes, for `for a, b, c in <TABLE>:` loops
    loop_values: dict = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.iter, ast.Name)):
            continue
        table = tables.get(node.iter.id)
        if not isinstance(table, (ast.Tuple, ast.List)):
            continue
        targets = node.target.elts if isinstance(node.target, ast.Tuple) else [node.target]
        for position, target in enumerate(targets):
            if not isinstance(target, ast.Name):
                continue
            cells = [
                row.elts[position] if isinstance(row, (ast.Tuple, ast.List)) else row
                for row in table.elts
            ]
            loop_values[target.id] = {
                cell.value
                for cell in cells
                if isinstance(cell, ast.Constant) and isinstance(cell.value, str)
            }

    read: set = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
            and node.attr.isupper()
        ):
            read.add(node.attr)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("getattr", "hasattr")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in aliases
        ):
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                read.add(name.value)
            elif isinstance(name, ast.Name) and name.id in loop_values:
                read |= {v for v in loop_values[name.id] if isinstance(v, str)}
            else:
                raise AssertionError(
                    "unresolvable settings read at line %d: %s" % (node.lineno, ast.unparse(node))
                )
    return read


def test_every_setting_the_config_reads_triggers_a_reload():
    """The key set is derived from the source, not restated: a new settings
    read anywhere in ``djust/config.py`` (a helper included, #3218) that is
    neither in ``_CONFIG_SETTINGS`` nor listed above fails here."""
    import inspect

    from djust import config as config_module

    read = _settings_reads(inspect.getsource(config_module))
    assert read - set(_READ_WITHOUT_RELOAD) == config_module._CONFIG_SETTINGS
    assert not config_module._CONFIG_SETTINGS & set(_READ_WITHOUT_RELOAD)


@pytest.mark.parametrize(
    "source",
    [
        # a read moved into a helper
        "from django.conf import settings\n"
        "def _helper():\n    return getattr(settings, 'DJUST_NEW_3218', None)\n",
        # an aliased import
        "from django.conf import settings as s\ndef f():\n    return s.DJUST_NEW_3218\n",
        "from django.conf import settings\ndef f():\n    return hasattr(settings, 'DJUST_NEW_3218')\n",
        # a new row in a literal alias table
        "from django.conf import settings\n"
        "_TABLE = (('DJUST_NEW_3218', 'key', bool),)\n"
        "def f():\n    for name, key, cast in _TABLE:\n        getattr(settings, name)\n",
    ],
)
def test_the_scan_finds_a_read_wherever_it_is(source):
    """Canary for the scan above: each shape of read is found."""
    assert "DJUST_NEW_3218" in _settings_reads(source)


def test_the_scan_refuses_a_read_it_cannot_resolve():
    source = "from django.conf import settings\ndef f(name):\n    return getattr(settings, name)\n"
    with pytest.raises(AssertionError, match="unresolvable settings read"):
        _settings_reads(source)


def test_a_flat_alias_override_reaches_the_config_and_is_undone():
    before = config.get("websocket_compression")
    with override_settings(DJUST_WS_COMPRESSION=not before):
        assert config.get("websocket_compression") is (not before)
    assert config.get("websocket_compression") == before
    ttl = config.get("service_worker.vdom_cache_ttl_seconds")
    with override_settings(DJUST_VDOM_CACHE_TTL_SECONDS=12345):
        assert config.get("service_worker.vdom_cache_ttl_seconds") == 12345
    assert config.get("service_worker.vdom_cache_ttl_seconds") == ttl


def test_a_reload_swaps_the_config_in_one_step():
    """Readers on other threads must never see defaults mid-reload: while the
    new config is being built, the live one still holds the old values."""
    from unittest.mock import patch

    from djust.config import LiveViewConfig

    seen = []
    real = LiveViewConfig._apply_settings

    def spy(self, target):
        seen.append(config.get("reauth_on_event"))
        return real(self, target)

    with override_settings(LIVEVIEW_CONFIG={"reauth_on_event": True}):
        with patch.object(LiveViewConfig, "_apply_settings", spy):
            config.reset()  # inside the override the live value is True
    assert seen == [True]


def test_debug_vdom_trace_env_var_is_undone_when_the_override_exits(monkeypatch):
    import os

    monkeypatch.delenv("DJUST_VDOM_TRACE", raising=False)
    config.reset()
    with override_settings(LIVEVIEW_CONFIG={"debug_vdom": True}):
        assert os.environ.get("DJUST_VDOM_TRACE") == "1"
    assert "DJUST_VDOM_TRACE" not in os.environ


def test_debug_vdom_trace_set_by_the_environment_is_left_alone(monkeypatch):
    import os

    monkeypatch.setenv("DJUST_VDOM_TRACE", "1")
    with override_settings(LIVEVIEW_CONFIG={"debug_vdom": True}):
        pass
    assert os.environ.get("DJUST_VDOM_TRACE") == "1"


# --- #3218: follow-ups from the #3213 re-review ------------------------------


def test_editing_a_returned_dict_in_place_does_not_change_the_config():
    """#3218 item 2: an in-place edit of a dict ``get()`` returned used to
    change the live config without being recorded, so the first reload
    (any ``override_settings`` of a config setting) silently undid it.
    ``get()`` now returns a copy of container values; ``set()`` is the way."""
    rate = config.get("rate_limit.rate")
    returned = config.get("rate_limit")
    returned["rate"] = rate + 4242
    assert config.get("rate_limit.rate") == rate
    classes = config.get("loading_grouping_classes")
    classes.append("probe-3218")
    assert "probe-3218" not in config.get("loading_grouping_classes")


def test_editing_as_dict_in_place_does_not_change_the_config():
    rate = config.get("rate_limit.rate")
    config.as_dict()["rate_limit"]["rate"] = rate + 4242
    assert config.get("rate_limit.rate") == rate


def test_set_copies_the_value_it_stores():
    """The other half of item 2: a caller keeping the dict it passed to
    ``set()`` must not be able to edit the live config through it."""
    value = {"rate": 7, "burst": 8}
    config.set("probe_3218", value)
    try:
        value["rate"] = 999
        assert config.get("probe_3218.rate") == 7
    finally:
        config.reset()


def _reload_during(monkeypatch, write):
    """Run ``write()`` while another thread reloads the config in the window
    between the write to the live dict and the recording of the value
    (#3218 item 3). The reload thread is given 0.5 s; a writer that holds
    the config's lock for the whole write makes it wait until the write is
    done."""
    import threading

    real = config._record_programmatic
    reloads = []

    def reload_in_the_window(key, value):
        thread = threading.Thread(target=config.reload_from_settings)
        thread.start()
        thread.join(timeout=0.5)
        reloads.append(thread)
        real(key, value)

    monkeypatch.setattr(config, "_record_programmatic", reload_in_the_window)
    write()
    monkeypatch.undo()
    for thread in reloads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert reloads


def test_a_reload_racing_set_does_not_lose_the_value(monkeypatch):
    try:
        _reload_during(monkeypatch, lambda: config.set("css_framework", "race_probe_3218"))
        assert config.get("css_framework") == "race_probe_3218"
    finally:
        config.reset()


def test_a_reload_racing_update_does_not_lose_the_value(monkeypatch):
    try:
        _reload_during(monkeypatch, lambda: config.update({"css_framework": "race_probe_3218"}))
        assert config.get("css_framework") == "race_probe_3218"
    finally:
        config.reset()
