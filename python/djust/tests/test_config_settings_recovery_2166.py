"""The config singleton recovers when settings were unreadable at import (#2164/#2166).

`djust.config.config` is built at MODULE IMPORT and reads Django settings once.
If `djust` is imported before `DJANGO_SETTINGS_MODULE` is set, that read raises
`ImproperlyConfigured`, a broad `except` swallows it, and the singleton serves
pure **defaults** for the life of the process — so every `LIVEVIEW_CONFIG` key
is silently ignored. `DjustConfig.ready()` now re-reads once to recover.

**These tests must run in a subprocess.** The failure is a property of module
import ORDER relative to an environment variable, and by the time pytest has
imported anything `djust.config` is long since loaded with settings available.
An in-process test cannot reproduce it — which is exactly why the original
`virtual_keyed_ops` bug shipped green: the tests monkeypatched `config._config`
and never exercised the order a server actually has.

Scope correction (#2164 review): this is NOT "every ASGI server". The
`djust new` scaffold sets the env var first and is unaffected. It bites the
projects whose `asgi.py` imports djust above the `setdefault` — which is what
`examples/demo_project/demo_project/asgi.py` did.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

# Values chosen to be the OPPOSITE of each key's default, so "the settings were
# read" and "the defaults were kept" are distinguishable. A probe using a value
# equal to the default could not tell the two apart.
PROBE_SETTINGS = """
SECRET_KEY = "config-recovery-probe"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "djust"]
DATABASES = {}
LIVEVIEW_CONFIG = {
    "virtual_keyed_ops": True,          # default False
    "loop_render_cache_enabled": False,  # default True  (shipped kill-switch)
    "template_auto_call": False,         # default True
    "filter_bridge_warm": False,         # default True
}
"""

_REPORT = """
from djust.config import config
from djust import _rust
import json
print("PROBE" + json.dumps({
    "virtual_keyed_ops": config.get("virtual_keyed_ops"),
    "loop_render_cache_enabled": config.get("loop_render_cache_enabled"),
    "template_auto_call": config.get("template_auto_call"),
    "filter_bridge_warm": config.get("filter_bridge_warm"),
    "rust_flag": _rust.virtual_keyed_ops_enabled(),
}))
"""

# The bug: djust imported BEFORE the settings module is named.
BROKEN_ORDER = (
    """
import os, sys
sys.path.insert(0, "__PROBE_DIR__")
import djust.config                      # <-- the #2164 window
os.environ["DJANGO_SETTINGS_MODULE"] = "recovery_probe_settings"
import django
django.setup()
"""
    + _REPORT
)

# The `djust new` scaffold's order — the control arm.
SCAFFOLD_ORDER = (
    """
import os, sys
sys.path.insert(0, "__PROBE_DIR__")
os.environ["DJANGO_SETTINGS_MODULE"] = "recovery_probe_settings"
import django
django.setup()
import djust.config
"""
    + _REPORT
)

EXPECTED = {
    "virtual_keyed_ops": True,
    "loop_render_cache_enabled": False,
    "template_auto_call": False,
    "filter_bridge_warm": False,
    "rust_flag": True,
}


def _run(script: str, tmp_path) -> dict:
    (tmp_path / "recovery_probe_settings.py").write_text(PROBE_SETTINGS)
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script).replace("__PROBE_DIR__", str(tmp_path))],
        capture_output=True,
        text=True,
        timeout=180,
    )
    # djust prints unrelated startup lines (hot reload, warnings), so key off the
    # marker rather than assuming the payload is the last line.
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[len("PROBE") :])
    raise AssertionError(
        f"probe produced no PROBE line.\nrc={proc.returncode}\n"
        f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    )


@pytest.mark.slow
def test_settings_are_honoured_when_djust_is_imported_before_the_env_var(tmp_path):
    """The regression. Every one of these was silently ignored before the fix.

    `loop_render_cache_enabled` is the sharp one: it is DEFAULT-ON as of #2062,
    so this kill-switch is a user's only way to opt out of the loop render
    cache — and it did nothing.
    """
    got = _run(BROKEN_ORDER, tmp_path)
    assert got == EXPECTED, (
        "config keys were ignored when djust was imported before "
        f"DJANGO_SETTINGS_MODULE was set.\n  wanted {EXPECTED}\n  got    {got}"
    )


@pytest.mark.slow
def test_the_scaffold_import_order_was_already_correct_and_still_is(tmp_path):
    """Control arm — and the scope check on the claim.

    A failing control is not a baseline, it is a second bug. This arm proves the
    probe can distinguish the two orders, and pins the reviewed correction that
    the `djust new` scaffold was never affected.
    """
    got = _run(SCAFFOLD_ORDER, tmp_path)
    assert got == EXPECTED, f"the scaffold order regressed: {got}"


def test_recovery_is_a_no_op_once_settings_have_loaded():
    """It must not clobber programmatic config — why this is not `reset()`.

    `reset()` restores `_defaults` first, so an `AppConfig.ready()` ordered
    before djust's that called `config.update(...)` would have its value thrown
    away. `ensure_settings_loaded()` returns immediately in the healthy path.
    """
    from djust.config import LiveViewConfig

    cfg = LiveViewConfig()
    assert cfg._settings_loaded is True, "settings are readable under pytest"

    cfg.update({"css_framework": "programmatically-set"})
    assert cfg.ensure_settings_loaded() is False, "must report it did nothing"
    assert cfg.get("css_framework") == "programmatically-set", (
        "the recovery path clobbered a programmatic value — use "
        "ensure_settings_loaded(), not reset()"
    )


def test_recovery_reloads_and_reports_when_the_import_time_load_failed():
    """The recovery arm, driven directly rather than through a subprocess."""
    from django.test import override_settings

    from djust.config import LiveViewConfig

    cfg = LiveViewConfig()
    # Simulate the #2164 process: the import-time read raised, so nothing loaded.
    cfg._config = cfg._defaults.copy()
    cfg._settings_loaded = False

    with override_settings(LIVEVIEW_CONFIG={"virtual_keyed_ops": True}):
        assert cfg.ensure_settings_loaded() is True, "must report it recovered"
    assert cfg.get("virtual_keyed_ops") is True
    assert cfg._settings_loaded is True
    # Second call is now a no-op.
    assert cfg.ensure_settings_loaded() is False


def test_ready_calls_the_recovery_before_anything_reads_config():
    """Ordering pin: a recovery that runs after the readers fixes nothing.

    Asserted on the real source rather than a mock because the property is
    positional — `ensure_settings_loaded` must precede the first `config.get`.
    """
    import inspect

    from djust.apps import DjustConfig

    src = inspect.getsource(DjustConfig.ready)
    # Strip comments: this repo has shipped seven source-greps satisfied by the
    # comment explaining the thing they pin (see `code_only` in
    # tests/test_benchmark_enforcement_2156.py).
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "ensure_settings_loaded" in code, "ready() must recover the config load"

    recovery_at = code.index("ensure_settings_loaded")
    # Every config-dependent thing ready() goes on to do. Each must come after
    # the recovery, or it reads the stale singleton.
    for reader in ("set_virtual_keyed_ops", "hot_reload_auto_enable", "_warm_filter_bridge"):
        assert reader in code, f"expected ready() to still perform {reader!r}"
        assert recovery_at < code.index(reader), (
            f"{reader} reads config BEFORE the recovery — it would see defaults"
        )
