"""Process-global reset helper for test isolation (#1883).

djust keeps a handful of *process-global* mutable singletons (module-level
caches, counters, and the Channels layer manager). Under pytest-xdist each
worker is a long-lived process that runs many tests in sequence, so any of
those globals left dirty by one test can pollute a later test in the SAME
worker — an order-fragile flake that passes in isolation and fails under
``-n auto`` depending on which tests share a worker.

Three such flakes surfaced in two milestones, all the same class:

- #1862 — ``ROOT_URLCONF`` leak broke ``TestDemoRegistration`` (fixed PR #1874)
- #1875 — ``djust_hotreload`` channel-layer group pollution (fixed PR #1881)
- #1882 — process-global wire-version drift: a stray ``djust_hotreload`` frame
  in the shared ``InMemoryChannelLayer`` re-renders on the victim consumer and
  bumps its per-connection ``_next_version()`` counter, so a time-travel jump
  lands at version 4 instead of 3 (``test_time_travel_jump_recovery_version_is_current``).

Each was previously whack-a-moled with a per-test reset. ``reset_djust_globals``
is the SYSTEMIC cure: one cheap function, called by an ``autouse`` fixture in
both test roots (``tests/conftest.py`` and ``python/djust/tests/conftest.py``),
that resets djust's process-globals BEFORE each test so every test starts from a
clean slate. That retires the entire flaky-class instead of patching one test at
a time, and prevents the next instance.

Design constraints (this runs on EVERY test):

- **Cheap** — only clears / re-inits lazily-rebuilt state; no heavy work.
- **Conservative** — resets ONLY state that genuinely *leaks* across tests AND
  is lazily re-derived on next use. It does NOT touch state a test legitimately
  configures via ``override_settings`` / its own fixtures (Django restores
  settings itself), nor self-invalidating keyed caches.
- **Pre-test (pre-yield)** — clears so each test STARTS clean; tests that set up
  their own global state in their body still work.
- **Optional-dep safe** — Channels may be absent; every reset is wrapped so a
  missing optional dependency never errors the fixture.

Globals reset (and why):

- **Channels layer manager** (``channel_layers.backends``) — the #1875/#1882
  class. ``LiveViewConsumer.connect`` joins the process-global
  ``djust_hotreload`` group; the cached ``InMemoryChannelLayer`` retains group
  membership + buffered frames across tests. Dropping the cached backend makes
  each test connect to a fresh, unpolluted layer. Lazily re-created on next
  ``get_channel_layer()``.
- **URLconf caches** (``clear_url_caches()`` + ``set_urlconf(None)``) — the
  #1862 class. A test that swaps ``ROOT_URLCONF`` can leave Django's resolver
  cache + the thread-local urlconf pinned at the test URLconf. Lazily rebuilt.
- **djust route-map cache** (``_reset_route_map_cache()``) — the URLconf-derived
  route map djust caches for ``dj-navigate`` resolution; #1862-adjacent. Lazily
  re-derived from the current URLconf.
- **Child-view id counter** (``mixins.sticky._view_id_counter``) and
  **tooltip id counter** (``components.templatetags.djust_components._tooltip_id_counter``)
  — module-level ``itertools.count`` singletons. Resetting to a fresh
  ``count(1)`` makes auto-generated ``child_N`` / tooltip ids deterministic
  per test (no cross-test drift).
- **Rust tag-handler registry** (theme + component ``ready()``-time handlers)
  — the #1928 class. The process-global Rust tag-handler registry
  (``crates/djust_templates/src/registry.rs``) is shared across an xdist
  worker. ``DjustThemingConfig.ready()`` / ``DjustComponentsConfig.ready()``
  register the ``{% theme_X %}`` / ``{% component %}`` etc. handlers — but
  ``ready()`` runs only ONCE per process. Any test that calls
  ``clear_tag_handlers()`` (the benchmark / unit tag-registry suites) and
  restores only the ``djust.template_tags`` built-ins — or any test that
  ``django.setup()``s without ``djust.theming`` — leaves those app-registered
  handlers gone for the rest of the worker, so the 17 ``#1721`` theme-tag
  tests 500 with "Unsupported template tag" under ``-n auto``. Re-asserting
  both registrars BEFORE each test restores them regardless of which polluter
  ran. Both registrars are idempotent (the theming one guards on
  ``has_tag_handler``; the component one overwrites) and no-op without the
  Rust extension, so the reset is cheap. This is the systemic cure for the
  same class #1771 patched only in ``tests/unit/test_tag_registry.py`` — the
  twin polluter in ``tests/benchmarks/test_tag_registry.py`` (parallel-path
  drift, #1646) is covered here for the whole worker.
- **Built-in template-tag handlers** (``djust.template_tags`` ``url`` /
  ``static`` / ``regroup`` …) — same #1928 class as the app-registered
  handlers above, but for the built-ins registered at ``djust`` import via
  ``@register`` / ``@register_assign`` (which also run only once per
  process). A ``clear_tag_handlers()`` / ``clear_assign_tag_handlers()``
  polluter wipes them for the rest of the worker; re-asserting them via
  ``reregister_builtins()`` restores them before each test.

Explicitly NOT reset (would be too aggressive / not a leak):

- ``state_backend`` — already isolated by the existing ``cleanup_session_cache``
  autouse fixture in ``tests/conftest.py``.
- ``session_utils._jit_serializer_cache`` / ``_get_model_hash`` — keyed by model
  class + structure hash, self-invalidating; not a cross-test leak.
- ``utils._get_template_dirs_cached`` — tests that mutate ``settings.TEMPLATES``
  manage this themselves; a blanket clear would add cost without fixing a known
  leak and could mask a test's own setup ordering.
- ``rust_bridge._CUSTOM_FILTERS_BRIDGED`` — a one-shot idempotent bootstrap;
  resetting it would needlessly re-bridge filters every test.
- ``StickyChildRegistry._child_views`` — per-LiveView-instance state, not a
  process-global; a fresh view instance starts empty.
"""

from __future__ import annotations

from typing import Callable, Optional


def _reset_channel_layer() -> None:
    """Drop the cached Channels backend so each test gets a fresh layer.

    The #1875/#1882 class: the process-global ``InMemoryChannelLayer`` retains
    ``djust_hotreload`` group membership + buffered frames across tests in the
    same xdist worker; a stray ``hotreload`` frame re-renders on a later
    consumer and bumps its wire-version counter. ``channel_layers`` lazily
    re-instantiates the backend on the next ``get_channel_layer()``.
    """
    try:
        from channels.layers import channel_layers
    except Exception:  # noqa: BLE001 — Channels is an optional dependency.
        return
    try:
        channel_layers.backends.clear()
    except Exception:  # noqa: BLE001 — never let cleanup break the fixture.
        pass


def _reset_urlconf_caches() -> None:
    """Clear Django's resolver cache + thread-local urlconf (the #1862 class).

    A test that swaps ``ROOT_URLCONF`` (via ``@override_settings`` racing the
    ``settings`` fixture, etc.) can leave the resolver cache populated and the
    thread-local urlconf pinned at the test URLconf for the rest of the worker.
    Both are lazily rebuilt from the current settings on next use.
    """
    try:
        from django.urls import clear_url_caches, set_urlconf
    except Exception:  # noqa: BLE001 — defensive; Django should always import.
        return
    try:
        clear_url_caches()
        set_urlconf(None)
    except Exception:  # noqa: BLE001
        pass


def _reset_route_map_cache() -> None:
    """Clear djust's URLconf-derived route-map cache (#1862-adjacent)."""
    try:
        from djust.routing import _reset_route_map_cache as _reset
    except Exception:  # noqa: BLE001 — module shape may change; stay defensive.
        return
    try:
        _reset()
    except Exception:  # noqa: BLE001
        pass


def _reset_id_counters() -> None:
    """Reset module-level ``itertools.count`` singletons to a fresh ``count(1)``.

    Makes auto-generated child-view ids (``child_N``) and tooltip ids
    deterministic per test so they don't drift across the worker.
    """
    import itertools

    try:
        from djust.mixins import sticky

        sticky._view_id_counter = itertools.count(1)
    except Exception:  # noqa: BLE001
        pass

    try:
        from djust.components.templatetags import djust_components

        djust_components._tooltip_id_counter = itertools.count(1)
    except Exception:  # noqa: BLE001
        pass


def _reset_rust_tag_handlers() -> None:
    """Re-assert the theme + component ``ready()``-time Rust tag handlers (#1928).

    The process-global Rust tag-handler registry is shared across an xdist
    worker. ``DjustThemingConfig.ready()`` / ``DjustComponentsConfig.ready()``
    register the ``{% theme_X %}`` / component tag handlers, but ``ready()``
    runs only once per process. A test that calls ``clear_tag_handlers()`` and
    restores only the ``djust.template_tags`` built-ins (the benchmark /
    unit tag-registry suites), or that ``django.setup()``s without
    ``djust.theming``, leaves those handlers gone for the rest of the worker —
    so the #1721 theme-tag tests then 500 with "Unsupported template tag".

    Re-running both registrars BEFORE each test restores the handlers no matter
    which polluter ran. Both are idempotent (the theming one guards on
    ``has_tag_handler``; the component one overwrites) and no-op when the Rust
    extension is unavailable, so this is cheap. Same class #1771 patched only
    in ``tests/unit/test_tag_registry.py``; this is the worker-wide cure.
    """
    _theme: Optional[Callable[[], None]]
    try:
        from djust.theming.rust_handlers import register_with_rust_engine as _theme
    except Exception:  # noqa: BLE001 — theming is optional; never break the fixture.
        _theme = None
    if _theme is not None:
        try:
            _theme()
        except Exception:  # noqa: BLE001
            pass

    _components: Optional[Callable[[], None]]
    try:
        from djust.components.rust_handlers import register_with_rust_engine as _components
    except Exception:  # noqa: BLE001 — components are optional; stay defensive.
        _components = None
    if _components is not None:
        try:
            _components()
        except Exception:  # noqa: BLE001
            pass


def _reset_builtin_template_tags() -> None:
    """Re-assert the ``djust.template_tags`` built-in handlers (#1928 class).

    Sibling to ``_reset_rust_tag_handlers`` for the built-in ``url`` /
    ``static`` / ``regroup`` … handlers registered at ``djust`` import via
    ``@register`` / ``@register_assign``. Those run only ONCE per process,
    so a test that calls ``clear_tag_handlers()`` /
    ``clear_assign_tag_handlers()`` (the unit tag-registry / assign-tag
    suites) leaves them gone for the rest of the worker — e.g. the
    ``{% regroup %}`` assign handler, unregistered, would then render as an
    unsupported tag. Re-registering BEFORE each test restores them no
    matter which polluter ran. Idempotent and no-op without the Rust
    extension, so it is cheap.
    """
    try:
        from djust.template_tags import reregister_builtins
    except Exception:  # noqa: BLE001 — template_tags is optional; never break the fixture.
        return
    try:
        reregister_builtins()
    except Exception:  # noqa: BLE001
        pass


def reset_djust_globals() -> None:
    """Reset every leak-prone djust process-global. Call BEFORE each test.

    Cheap, idempotent, and optional-dependency safe. Wired into an ``autouse``
    fixture in both test roots so every test starts from a clean slate. See the
    module docstring for the full inventory + the conservative-inclusion
    rationale.
    """
    _reset_channel_layer()
    _reset_urlconf_caches()
    _reset_route_map_cache()
    _reset_id_counters()
    _reset_rust_tag_handlers()
    _reset_builtin_template_tags()


__all__ = ["reset_djust_globals"]
