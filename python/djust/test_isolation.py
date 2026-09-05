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
all three test roots (``tests/``, ``python/tests/`` and ``python/djust/tests/``),
that resets djust's process-globals BEFORE each test so every test starts from a
clean slate. That retires the entire flaky-class instead of patching one test at
a time, and prevents the next instance.

Design constraints (this runs on EVERY test):

- **Cheap** — only clears / re-inits lazily-rebuilt state; no heavy work.
- **Conservative** — resets ONLY state that genuinely *leaks* across tests AND
  is lazily re-derived on next use. It does NOT touch state a test legitimately
  configures via ``override_settings`` (Django restores settings itself), nor
  self-invalidating keyed caches.

  **One deliberate exception, added in #2234**: the Django language/timezone
  thread-locals ARE reset, which means a MODULE- or SESSION-scoped fixture that
  calls ``activate()`` once would have its activation undone before each test in
  that module. No such fixture exists in the suite today (verified: every
  ``activate()`` call is inside a test body), and the alternative — leaving them
  alone — is what let #2222's leak silently change how every later test in a
  worker rendered. A module-scoped activation is the supported-but-unused
  pattern being traded away; a per-test ``activate()`` inside the test body is
  unaffected and is what the suite actually uses. Said out loud because the
  constraint above would otherwise read as covering a case it no longer does.
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
- **Bug-capture snapshot-store cache** (``bug_capture_store._STORE_CACHE``)
  — the resolved store is memoized so an encode doesn't rebuild (and, for
  Redis, re-handshake) per capture. It self-invalidates when the config value
  changes, so ``override_settings`` is already safe; the reset covers the two
  cases the config key can't see — a test that installs a store *instance*
  directly, and a test asserting "no store is configured" that would otherwise
  inherit a live store (and a live Redis socket) from an earlier test in the
  worker. Lazily rebuilt on the next ``get_store()``.
- **Child-view id counter** (``mixins.sticky._view_id_counter``) and
  **tooltip id counter** (``components.templatetags.djust_components._tooltip_id_counter``)
  — module-level ``itertools.count`` singletons. Resetting to a fresh
  ``count(1)`` makes auto-generated ``child_N`` / tooltip ids deterministic
  per test (no cross-test drift).
- **Django's active language and timezone** (``translation._active`` /
  ``timezone._active``) — the #2234 class. Django's own thread-locals, which
  nothing here reset, so a test calling ``activate()`` changed how every later
  test in the worker rendered. The subtle case is ``deactivate_all()``, which
  reads like the thorough reset and leaves ``get_language()`` as ``None`` —
  making ``get_format`` fall back to ``global_settings``, where
  ``NUMBER_GROUPING`` is ``0``. ``deactivate()`` restores the settings default,
  which is what resetting should mean.
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
  ``reregister_builtins()`` also strips each built-in from the OTHER
  (wrong) registry (#2053): a polluter that blindly re-registers every
  built-in via ``register_tag_handler`` without checking
  ``isinstance(handler, AssignTagHandler)`` plants an assign-only
  built-in (``regroup``) in the plain registry, which wins at parse time
  regardless of the assign registry being correct — merely re-asserting
  the correct registration does not heal that.

Explicitly NOT reset (would be too aggressive / not a leak):

- ``state_backend`` — already isolated by the existing ``cleanup_session_cache``
  autouse fixture in ``tests/conftest.py``.
- ``session_utils._jit_serializer_cache`` / ``_get_model_hash`` — keyed by model
  class + structure hash, self-invalidating; not a cross-test leak.
- ``utils._get_template_dirs_cached`` — tests that mutate ``settings.TEMPLATES``
  manage this themselves; a blanket clear would add cost without fixing a known
  leak and could mask a test's own setup ordering.
- ``template_filters._CUSTOM_FILTERS_BRIDGED`` — a one-shot idempotent bootstrap;
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


def _reset_bug_capture_store_cache() -> None:
    """Drop the cached bug-capture snapshot store (#1561).

    ``djust.bug_capture_store`` memoizes the resolved store so an encode
    doesn't rebuild (and, for Redis, re-handshake) per capture. The cache is
    keyed on the config value that built it, so it self-invalidates under
    ``override_settings`` — but a test that installs a store *instance*
    directly, or one that asserts "no store is configured", must not inherit a
    live store (or a live Redis socket) from a previous test in the worker.
    """
    try:
        from djust.bug_capture_store import reset_store_cache
    except Exception:  # noqa: BLE001 — module shape may change; stay defensive.
        return
    try:
        reset_store_cache()
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


def _reset_template_libraries() -> None:
    """Re-assert every ``{% load %}``-bridged Django library tag (#2547).

    Sibling to ``_reset_builtin_template_tags`` for the tags
    ``djust.template_libraries`` registered on a ``{% load %}``. A test that
    cleared the Rust registries (the #1928 class above) leaves them gone —
    and because the Rust ``TEMPLATE_CACHE`` is keyed by source, a template
    parsed while they were registered is served from cache with nodes that
    no longer resolve and never re-runs its ``{% load %}``. Re-registering
    the same handler objects BEFORE each test restores every cached
    template. The loader hook itself is re-armed by ``reregister_builtins``.
    """
    try:
        from djust.template_libraries import reassert
    except Exception:  # noqa: BLE001 — optional; never break the fixture.
        return
    try:
        reassert()
    except Exception:  # noqa: BLE001
        pass


def _reset_django_thread_locals() -> None:
    """Normalise Django's ACTIVE language and timezone (#2234).

    The other resets here cover djust's own process-globals. Django keeps two
    of its own in thread-locals — ``translation._active`` and
    ``timezone._active`` — and nothing reset them, so a test that called
    ``activate()`` and forgot to undo it changed how every later test in that
    worker rendered.

    The shape that motivated this is subtler than a forgotten ``activate``:
    ``translation.deactivate_all()`` reads like the THOROUGH reset and is the
    one that leaks. It installs a ``NullTranslations`` and leaves
    ``get_language()`` returning ``None``, so ``get_format`` skips the locale
    modules and falls back to ``global_settings`` — where ``NUMBER_GROUPING``
    is ``0``:

        fresh                  get_language()='en-us'  NUMBER_GROUPING=3
        after deactivate()     get_language()='en-us'  NUMBER_GROUPING=3
        after deactivate_all() get_language()=None     NUMBER_GROUPING=0

    Number grouping was silently off for the rest of the worker. It shipped in
    the PR that added the localization tests and poisoned a test two PRs later
    (#2233), caught only because that test happened to land in the same worker.

    ``deactivate()`` — not ``deactivate_all()`` — restores
    ``settings.LANGUAGE_CODE`` and ``settings.TIME_ZONE``, which is what
    "reset" should mean. Doing it here means a test that forgets cannot poison
    its neighbours, rather than every test file needing its own fixture to
    remember.

    Cheap (two thread-local deletes) and safe when i18n is disabled.
    """
    # One guard PER action, matching every sibling helper here. A single shared
    # `try` would let a raising `translation.deactivate()` silently skip the
    # timezone reset — two independent resets sharing one failure path is how
    # half a cleanup goes missing without anyone noticing.
    try:
        from django.utils import translation

        translation.deactivate()
    except Exception:  # noqa: BLE001 - i18n is optional; a failure here must not
        pass  # break every test in the suite via an autouse fixture
    try:
        from django.utils import timezone

        timezone.deactivate()
    except Exception:  # noqa: BLE001 - same, independently: see above
        pass


def reset_djust_globals() -> None:
    """Reset every leak-prone djust process-global. Call BEFORE each test.

    Cheap, idempotent, and optional-dependency safe. Wired into an ``autouse``
    fixture in all three test roots so every test starts from a clean slate. See
    the module docstring for the full inventory + the conservative-inclusion
    rationale.
    """
    _reset_channel_layer()
    _reset_urlconf_caches()
    _reset_route_map_cache()
    _reset_bug_capture_store_cache()
    _reset_id_counters()
    _reset_rust_tag_handlers()
    _reset_template_libraries()
    _reset_builtin_template_tags()
    _reset_django_thread_locals()


__all__ = ["reset_djust_globals"]
