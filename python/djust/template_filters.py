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
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Filters we never want to forward — built-ins that the Rust engine
# already implements natively. Forwarding would slow them down and
# would never trip the Rust unknown-filter fallback anyway, but
# explicit skipping keeps the registry clean.
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
        "length_is",
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
        filters_dict = getattr(library, "filters", None)
        if not filters_dict:
            continue
        for filter_name, filter_callable in filters_dict.items():
            try:
                if register_django_filter(filter_name, filter_callable):
                    count += 1
            except Exception:  # pragma: no cover — defensive
                logger.exception(
                    "Failed to bridge custom filter '%s' to Rust registry; "
                    "the filter will still work in Python-rendered paths",
                    filter_name,
                )
    if count:
        logger.debug("Bridged %d custom Django filters to Rust template engine", count)
    return count
