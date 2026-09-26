"""Bridge: project-defined Django ``@register.filter`` callables → Rust engine.

Issue #1121: the Rust template renderer's filter dispatch was a hardcoded
match against Django's 57 built-in filter names. Project-level custom
filters registered via ``@register.filter`` in a Django app's
``templatetags/`` package worked in the Python render path but raised
``RuntimeError: Template error: Unknown filter: <name>`` under the Rust
``RustLiveView`` render path.

This module bridges the gap. It:

1. Walks Django's per-engine ``template_libraries`` (the registries
   populated by ``@register.filter``) and forwards every filter to the
   Rust engine's filter registry.
2. Honours ``filter.is_safe`` and ``filter.needs_autoescape`` so the Rust
   renderer's auto-escape policy treats project filters identically to
   Python-side rendering.
3. Re-bootstraps idempotently (``bootstrap_django_filters`` is safe to
   call multiple times — late-loaded apps' filters are picked up on the
   next call).

Bootstrap is invoked by the Rust bridge's ``_initialize_rust_view``
hook the first time a LiveView renders, so projects don't need to call
anything explicitly.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator

logger = logging.getLogger(__name__)

# One-shot guard for ``_ensure_custom_filters_bridged``. Lives here, next to
# the bootstrap it guards, so the templates-only render path and
# ``DjustConfig.ready()`` can arm the bridge without importing the
# ``djust.mixins`` package (every LiveView mixin) for one function (#2565).
_CUSTOM_FILTERS_BRIDGED = False
# What the bootstrap registered, and the Rust registry generation it left
# behind (#3208). The flag alone said "bridged" after anything emptied the
# global registry (``clear_custom_filters()``), and every later render of a
# bridged filter failed with "Invalid filter" for the rest of the process. The
# registry, not the flag, is the truth — the rule
# ``template_libraries._still_bridged`` applies to ``{% load %}``.
_BRIDGED_FILTER_NAMES: tuple = ()
_BRIDGED_AT_GENERATION: int | None = None


@contextmanager
def _global_registry_namespace() -> Iterator[None]:
    """Run the block against the GLOBAL Rust registry (namespace 0) (#3208).

    The bootstrap is process-wide: its filters are the fallback every backend
    namespace sees. It runs lazily on the first render, and that render may be
    inside ``rendering_with_backend(backend)`` — a ``DjustTemplateBackend`` of
    its own. Registering in that backend's namespace left the filters visible
    to that backend only, while the flag told every other render (a root
    LiveView's included) they were bridged: ``Invalid filter: 'field_value'``
    for the rest of the process.
    """
    try:
        from djust._rust import set_registry_namespace
    except ImportError:
        yield
        return
    previous = set_registry_namespace(0)
    try:
        yield
    finally:
        set_registry_namespace(previous)


def _bridged_filters_still_registered() -> bool:
    """Whether every filter the bootstrap registered is still in the global
    Rust registry. One generation read per call; the per-name probes run only
    after the registry changed."""
    global _BRIDGED_AT_GENERATION
    try:
        from djust._rust import registry_entry_is_local, registry_generation
    except ImportError:
        return True
    generation = registry_generation()
    if generation == _BRIDGED_AT_GENERATION:
        return True
    with _global_registry_namespace():
        intact = all(registry_entry_is_local(name, "filter") for name in _BRIDGED_FILTER_NAMES)
    if intact:
        _BRIDGED_AT_GENERATION = generation
    return intact


def _library_filter_names() -> tuple:
    return tuple(
        sorted(
            {
                name
                for library in _iter_django_libraries()
                for name in (getattr(library, "filters", None) or {})
                if name not in _BUILTIN_NAMES
            }
        )
    )


def _ensure_custom_filters_bridged() -> None:
    """Bootstrap that forwards Django's ``@register.filter`` callables to the
    global Rust filter registry: once, and again if the registry lost them
    since (#3208). Idempotent and non-fatal on failure — filters still work in
    the Python render path even if the Rust bridge is unavailable.
    """
    global _CUSTOM_FILTERS_BRIDGED, _BRIDGED_FILTER_NAMES, _BRIDGED_AT_GENERATION
    if _CUSTOM_FILTERS_BRIDGED and _bridged_filters_still_registered():
        return
    names: tuple = ()
    try:
        with _global_registry_namespace():
            bootstrap_django_filters()
        names = _library_filter_names()
    except Exception:  # noqa: BLE001 — defensive; never block render
        logger.warning(
            "Failed to bridge Django custom filters to Rust template engine; "
            "filters will still work in the Python render path",
            exc_info=True,
        )
    finally:
        # Set the guard whether bootstrap succeeded or threw — we never
        # want to re-attempt on every render and re-log the warning. After
        # a failure no names are recorded, so nothing triggers a retry.
        _CUSTOM_FILTERS_BRIDGED = True
        _BRIDGED_FILTER_NAMES = names
        try:
            from djust._rust import registry_generation

            _BRIDGED_AT_GENERATION = registry_generation()
        except ImportError:
            _BRIDGED_AT_GENERATION = None


# Filters we never want to forward — built-ins that the Rust engine
# already implements natively. Forwarding would slow them down and
# would never trip the Rust unknown-filter fallback anyway, but
# explicit skipping keeps the registry clean. Pinned equal to the engine's
# ``ARITY`` table (crates/djust_templates/src/filter_arity.rs) by
# tests/test_generate_template_backend_lists.py (#2540).
_BUILTIN_NAMES = frozenset(
    {
        "add",
        "addslashes",
        "capfirst",
        "center",
        "cut",
        "date",
        "default",
        "default_if_none",
        "dictsort",
        "dictsortreversed",
        "divisibleby",
        "escape",
        "escapejs",
        "escapeseq",
        "filesizeformat",
        "first",
        "floatformat",
        "force_escape",
        "get_digit",
        "iriencode",
        "join",
        "json_script",
        "last",
        "length",
        "linebreaks",
        "linebreaksbr",
        "linenumbers",
        "ljust",
        "lower",
        "make_list",
        "phone2numeric",
        "pluralize",
        "pprint",
        "random",
        "rjust",
        "safe",
        "safeseq",
        "slice",
        "slugify",
        "stringformat",
        "striptags",
        "time",
        "timesince",
        "timeuntil",
        "title",
        "truncatechars",
        "truncatechars_html",
        "truncatewords",
        "truncatewords_html",
        "unordered_list",
        "upper",
        "urlencode",
        "urlize",
        "urlizetrunc",
        "wordcount",
        "wordwrap",
        "yesno",
    }
)


def _filter_meta(callable_obj: Callable[..., Any]) -> tuple[bool, bool]:
    """Extract ``is_safe`` and ``needs_autoescape`` from a Django filter.

    Django sets these as plain attributes on the callable when the
    ``@register.filter`` decorator runs. Defaults match Django's:
    both are ``False`` when not set.
    """
    is_safe = bool(getattr(callable_obj, "is_safe", False))
    needs_autoescape = bool(getattr(callable_obj, "needs_autoescape", False))
    return is_safe, needs_autoescape


def register_django_filter(
    name: str,
    callable_obj: Callable[..., Any],
    *,
    is_safe: bool | None = None,
    needs_autoescape: bool | None = None,
    skip_builtins: bool = True,
) -> bool:
    """Forward a single Django filter callable to the Rust filter registry.

    Returns ``True`` if the filter was registered, ``False`` if skipped
    (e.g. because the name is a built-in or the Rust extension isn't
    available in this environment).

    :param name: filter name as used in templates (``{{ x|name }}``).
    :param callable_obj: the Django filter callable
        (``(value, arg=None) -> str``).
    :param is_safe: override Django's ``filter.is_safe`` attribute. When
        ``None`` (default), the attribute is read off the callable.
    :param needs_autoescape: override Django's ``filter.needs_autoescape``
        attribute. When ``None`` (default), the attribute is read off
        the callable.
    :param skip_builtins: when ``True`` (default), do not forward filter
        names that the Rust engine already implements natively. Set to
        ``False`` to allow project-side overrides of built-ins.
    """
    if skip_builtins and name in _BUILTIN_NAMES:
        return False

    if is_safe is None or needs_autoescape is None:
        attr_safe, attr_ae = _filter_meta(callable_obj)
        is_safe = attr_safe if is_safe is None else is_safe
        needs_autoescape = attr_ae if needs_autoescape is None else needs_autoescape

    try:
        from djust._rust import register_custom_filter
    except ImportError:
        logger.warning(
            "djust._rust extension not available; custom filter '%s' will not work in Rust render",
            name,
        )
        return False

    register_custom_filter(name, callable_obj, is_safe, needs_autoescape)
    return True


def _iter_django_libraries() -> Iterable:
    """Yield every Django ``template.Library`` instance the engine knows about.

    Walks ``template.engines['django'].engine.template_libraries`` —
    the canonical per-engine map populated by Django's
    ``import_library`` for every ``templatetags/<x>.py`` module that's
    been ``{% load %}``-ed or auto-discovered. Falls back gracefully
    when no Django engine is configured (e.g. during certain test
    bootstrap orderings).
    """
    try:
        from django.template import engines
    except ImportError:
        return

    for engine in engines.all():
        # Only DjangoTemplates engines have ``template_libraries``;
        # the Rust engine and other backends don't.
        engine_inner = getattr(engine, "engine", None)
        if engine_inner is None:
            continue
        libraries = getattr(engine_inner, "template_libraries", None)
        if not libraries:
            continue
        for library in libraries.values():
            yield library


def bootstrap_django_filters() -> int:
    """Walk Django's filter registries and forward every filter to Rust.

    Safe to call repeatedly — re-registering an existing name in the
    Rust registry overwrites, so late-loaded apps' filters are picked
    up on the next call. Returns the number of filters forwarded.

    Built-in Django filters (the ones the Rust engine already implements
    natively) are skipped to keep the registry compact.
    """
    count = 0
    for library in _iter_django_libraries():
        count += bridge_library_filters(library)
    if count:
        logger.debug("Bridged %d custom Django filters to Rust template engine", count)
    return count


def _filter_refusal(filter_name: str, filter_callable: Any) -> Any:
    """A loud stand-in for a library filter the Rust engine cannot serve
    (#2558): raises Django's ``TemplateSyntaxError`` naming the filter, so a
    template using it gets an error instead of a silent ``""``. No filter is
    refused today — the ``tz`` three that were bridge verbatim since #2541
    (``template_libraries._FILTER_REFUSALS``); the channel stays for the next
    one that genuinely cannot cross."""
    from django.template import TemplateSyntaxError

    module = getattr(filter_callable, "__module__", "<unknown>")

    def refusal(value: Any, arg: Any = None) -> Any:
        raise TemplateSyntaxError(
            "filter %r from %r cannot be served by the Rust engine (#2558)" % (filter_name, module)
        )

    return refusal


def bridge_library_filters(library: Any, refuse: frozenset = frozenset()) -> int:
    """Forward every filter of ONE ``template.Library`` to Rust.

    The loop body ``bootstrap_django_filters`` always had, lifted out so the
    ``{% load app_tags %}`` loader (#2547, ``djust.template_libraries``)
    bridges a library's filters through the SAME rule — ``is_safe`` /
    ``needs_autoescape`` read off the callable, built-ins skipped — rather
    than a hand-copied twin (#1646). Returns the number forwarded.

    ``refuse`` (#2558): names to bridge as LOUD refusals instead — a filter
    the Rust engine structurally cannot serve raises Django's
    ``TemplateSyntaxError`` when used, rather than answering ``""``. Empty
    since #2541 (the ``tz`` filters bridge verbatim now that a datetime
    crosses the boundary as a typed value).
    """
    count = 0
    filters_dict = getattr(library, "filters", None)
    if not filters_dict:
        return 0
    for filter_name, filter_callable in filters_dict.items():
        try:
            if filter_name in refuse:
                if register_django_filter(
                    filter_name, _filter_refusal(filter_name, filter_callable)
                ):
                    count += 1
                continue
            if register_django_filter(filter_name, filter_callable):
                count += 1
        except Exception:  # pragma: no cover — defensive
            logger.exception(
                "Failed to bridge custom filter '%s' to Rust registry; "
                "the filter will still work in Python-rendered paths",
                filter_name,
            )
    return count
