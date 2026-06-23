"""Rust template-engine tag handlers for djust theming (#1721).

The theming guide documents the theme switcher as the template tag
``{% theme_panel %}`` (with ``{% load theme_tags %}``). Those tags are
Django ``@register.simple_tag`` functions in
:mod:`djust.theming.templatetags.theme_tags` — they work in the Django
template engine, but the **Rust** template engine (the one djust uses to
render LiveView templates) does not know about Django's tag library and
raised::

    RuntimeError: Template error: Unsupported template tag '{% theme_panel %}'.

The ``{{ theme_panel }}`` context-string form (#1435) renders, but the
documented ``{% theme_X %}`` form did not — docs and engine disagreed.

This module registers a thin :class:`~djust.template_tags.TagHandler` bridge
for each documented theme tag so the ``{% theme_X %}`` form works in the Rust
engine. Each handler:

- receives the Rust engine's ``args`` (a list of ``"key=value"`` strings and
  positional values, already variable-resolved) plus the render ``context``;
- parses the args into kwargs matching the simple_tag's signature;
- builds a ``{"request": request}`` context (``request`` pulled from the
  render context; ``None`` is fine — the theme manager falls back to
  defaults, exactly as the context-string form does); and
- calls the **same** ``theme_tags.py`` simple_tag body the ``{{ }}`` form
  uses, returning its ``mark_safe`` HTML string.

This makes the two forms produce equivalent output for default args and
supports the customization-with-args form
(``{% theme_panel show_packs=False %}``) — the documented reason to prefer
the tag form.

Performance note (documented tradeoff): like every Rust custom-tag handler,
each ``{% theme_X %}`` invocation crosses the PyO3 boundary and runs a Python
sidecar (~tens of µs + the tag body's own cost). The ``{{ theme_X }}``
context-string form (#1435) pre-renders once per request and is cheaper when
the same tag appears multiple times on a page. The tag form exists for
customization-with-args and docs parity.

Registration is wired in :meth:`DjustThemingConfig.ready`. It degrades
gracefully when the Rust extension is unavailable (Django-engine templates
keep working via ``{% load theme_tags %}``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# (tag_name, simple_tag function name) for every documented user-facing tag.
# Mirrors the @register.simple_tag bodies in theme_tags.py. Order matches the
# documented tags in #1721 / #1722 first, then the remaining documented ones.
_THEME_TAG_NAMES: List[str] = [
    # The five tags called out in #1721 / companion #1722.
    "theme_panel",
    "theme_head",
    "theme_switcher",
    "theme_mode_toggle",
    "theme_preset_selector",
    # Remaining documented user-facing tags (theme_tags.py).
    "theme_css",
    "theme_css_link",
    "theme_framework_overrides",
    "theme_preset",
    "theme_mode",
    "theme_resolved_mode",
]


def _coerce_value(val: str, context: Dict[str, Any]) -> Any:
    """Coerce a raw Rust-engine arg value string to a Python value.

    The Rust engine resolves context variables before calling the handler,
    so ``val`` is typically a literal. Handles the shapes a theme tag kwarg
    can take: quoted strings, booleans, None, ints, floats, else a context
    lookup (falling back to the raw string).
    """
    val = val.strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    if val in ("True", "true"):
        return True
    if val in ("False", "false"):
        return False
    if val in ("None", "null"):
        return None
    if val == "":
        return ""
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    # Variable reference — look up in context, else keep the raw token.
    return context.get(val, val)


def _parse_kwargs(args: List[str], context: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a Rust-engine ``args`` list into kwargs for a simple_tag.

    Only ``key=value`` args are kept — the documented theme tags take
    keyword-only customization params (``show_packs=False``,
    ``layout="grid"``, etc.). Positional args are ignored (none of the
    documented theme tags take positional args).
    """
    kwargs: Dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        kwargs[key.strip()] = _coerce_value(val, context)
    return kwargs


class ThemeTagHandler:
    """Rust-engine tag handler that delegates to a theme simple_tag body.

    Implements the :class:`~djust.template_tags.TagHandler` contract
    (``render(self, args, context) -> str``) the Rust engine invokes via the
    ``CustomTag`` callback. Delegates to the matching ``theme_tags.py``
    ``@register.simple_tag(takes_context=True)`` function so the tag form and
    the ``{{ }}`` context-string form share one implementation.
    """

    def __init__(self, tag_name: str):
        self.tag_name = tag_name

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        # Lazy import: theme_tags pulls in Django template machinery, which
        # must not be imported at module load (apps not yet ready).
        from .templatetags import theme_tags

        fn = getattr(theme_tags, self.tag_name)

        # The simple_tag bodies take ``context`` first (takes_context=True),
        # then read ``context.get("request")``. The Rust render context may
        # carry ``request``; if not, None is fine — get_theme_manager(None)
        # returns a default-state manager, exactly as the {{ }} form does.
        tag_context = {"request": context.get("request")}

        kwargs = _parse_kwargs(args, context)
        try:
            result = fn(tag_context, **kwargs)
        except TypeError:
            # An unexpected kwarg (e.g. a typo in the template) would raise
            # TypeError. Fall back to a default-args render rather than
            # 500-ing the whole page — the tag still produces output.
            logger.warning(
                "Theme tag '%s' rejected kwargs %s; rendering with defaults",
                self.tag_name,
                sorted(kwargs),
            )
            result = fn(tag_context)
        return "" if result is None else str(result)


def register_with_rust_engine() -> None:
    """Register every documented theme tag as a Rust tag handler.

    Called from :meth:`DjustThemingConfig.ready`. Idempotent — re-registering
    overwrites the existing handler (and is guarded by ``has_tag_handler`` to
    avoid redundant PyO3 round-trips). Degrades to a no-op when the Rust
    extension is unavailable so Django-engine templates keep working via
    ``{% load theme_tags %}``.
    """
    try:
        from djust._rust import has_tag_handler, register_tag_handler
    except ImportError as exc:  # pragma: no cover - exercised only sans-Rust
        logger.debug(
            "Rust extension unavailable; theme tags work via the Django "
            "template engine only ({%% load theme_tags %%}): %s",
            exc,
        )
        return

    for tag_name in _THEME_TAG_NAMES:
        if has_tag_handler(tag_name):
            continue
        register_tag_handler(tag_name, ThemeTagHandler(tag_name))
        logger.debug("Registered Rust theme tag handler: %s", tag_name)
