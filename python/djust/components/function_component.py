"""Function components — the ``@component`` decorator and ``{% call %}`` tag.

A *function component* is a stateless Python callable that receives an
``assigns`` dict and returns an HTML string (or Django-safe equivalent).
It is the lightweight counterpart to :class:`LiveComponent`: no WebSocket,
no state, no lifecycle.

Example::

    from djust import component

    @component
    def button(assigns):
        variant = assigns.get("variant", "default")
        return f'<button class="btn btn-{variant}">{assigns["children"]}</button>'

    # In a template:
    # {% call button variant="primary" %}Click me{% endcall %}
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Callable, Optional, Union

from .assigns import (
    Assign,
    AssignValidationError,
    Slot,
    merge_assign_declarations,
    merge_slot_declarations,
    validate_assigns,
    validate_slots,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------


_COMPONENT_REGISTRY: dict[str, Union[Callable[..., str], type]] = {}


def register_component(name: str, target: Union[Callable[..., str], type]) -> None:
    """Register a component explicitly (rarely needed — use ``@component``)."""

    _COMPONENT_REGISTRY[name] = target


def get_component(name: str) -> Optional[Union[Callable[..., str], type]]:
    """Look up a registered component by name (``None`` if missing)."""

    return _COMPONENT_REGISTRY.get(name)


def clear_components() -> None:
    """Test-only: clear the component registry."""

    _COMPONENT_REGISTRY.clear()


def get_registered_components() -> dict[str, Union[Callable[..., str], type]]:
    """Return a copy of the current registry."""

    return dict(_COMPONENT_REGISTRY)


# ---------------------------------------------------------------------------
# @component decorator
# ---------------------------------------------------------------------------


def component(
    fn=None,
    *,
    name: Optional[str] = None,
    assigns: Optional[list[Assign]] = None,
    slots: Optional[list[Slot]] = None,
):
    """Register a function as a template-invokable component.

    Usage::

        @component
        def card(assigns): ...

        @component(name="fancy_card", assigns=[Assign("title", str, required=True)])
        def card_impl(assigns): ...

    Args:
        fn: Function being decorated (supplied automatically in bare form).
        name: Optional registry name override. Defaults to the function's
            ``__name__``.
        assigns: Optional list of :class:`~djust.Assign` declarations.
        slots: Optional list of :class:`~djust.Slot` declarations.

    Returns:
        The original function, unmodified aside from attached metadata.
    """

    def _wrap(target):
        component_name = name or target.__name__
        target._djust_assigns = assigns or []
        target._djust_slots = slots or []
        target._djust_component_name = component_name
        _COMPONENT_REGISTRY[component_name] = target
        return target

    if fn is not None and callable(fn):
        # Bare @component usage.
        return _wrap(fn)
    return _wrap


# ---------------------------------------------------------------------------
# Arg parsing — local (sub-)copy of the rust_handlers._parse_args pattern.
#
# Keeping a local copy avoids a circular import between function_component and
# rust_handlers. The semantics match exactly.
# ---------------------------------------------------------------------------


def _parse_call_args(
    args: list[str], context: dict[str, Any]
) -> tuple[Optional[str], dict[str, Any]]:
    """Split ``args`` from a ``{% call NAME key=val ... %}`` tag.

    Returns ``(component_name, kwargs_dict)``. ``component_name`` is stripped
    of surrounding quotes when present.
    """

    if not args:
        return None, {}

    name_raw = args[0].strip()
    if (name_raw.startswith('"') and name_raw.endswith('"')) or (
        name_raw.startswith("'") and name_raw.endswith("'")
    ):
        name = name_raw[1:-1]
    elif "=" in name_raw:
        # Caller forgot the component name.
        return None, {}
    else:
        # Bareword — may be a context variable, but most callers write a
        # literal. Try context lookup first, fall back to the word itself.
        value = context.get(name_raw)
        name = value if isinstance(value, str) else name_raw

    kwargs = _parse_kwargs(args[1:], context)
    return name, kwargs


def _parse_kwargs(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
    """Parse ``key=val`` pairs from the argv-like list emitted by the Rust lexer.

    Mirrors :func:`djust.components.rust_handlers._parse_args` so that
    semantics (string literals, JSON, numeric, variable lookup) stay
    consistent with existing handlers.
    """

    result: dict[str, Any] = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            result[key] = val[1:-1]
        elif (val.startswith("[") and val.endswith("]")) or (
            val.startswith("{") and val.endswith("}")
        ):
            try:
                result[key] = json.loads(val)
            except (ValueError, TypeError):
                result[key] = context.get(val, val)
        elif val in ("True", "true"):
            result[key] = True
        elif val in ("False", "false"):
            result[key] = False
        elif val == "":
            result[key] = ""
        elif val in ("None", "null"):
            result[key] = None
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = context.get(val, val)
    return result


# ---------------------------------------------------------------------------
# Slot sentinel protocol (see Phase 3)
# ---------------------------------------------------------------------------

# Unique marker. The raw string is unlikely to appear in user content by
# accident, and the escaped JSON payload cannot contain unescaped '-->' so
# the regex is unambiguous.
_SLOT_SENTINEL_PREFIX = "<!--DJUST_SLOT_V1:"
_SLOT_SENTINEL_SUFFIX = "-->"
_SLOT_SENTINEL_RE = re.compile(
    re.escape(_SLOT_SENTINEL_PREFIX) + r"(.*?)" + re.escape(_SLOT_SENTINEL_SUFFIX),
    re.DOTALL,
)


def _emit_slot_sentinel(payload: dict[str, Any]) -> str:
    """Emit an HTML-comment-wrapped, JSON-encoded slot marker."""

    raw = json.dumps(payload, ensure_ascii=False)
    return f"{_SLOT_SENTINEL_PREFIX}{html.escape(raw)}{_SLOT_SENTINEL_SUFFIX}"


def _extract_slots(content: str) -> tuple[dict[str, list[dict[str, Any]]], str]:
    """Scan ``content`` for slot sentinels; return ``(slots_map, remainder)``.

    ``slots_map`` is ``{slot_name: [slot_dict, ...]}`` preserving order.
    ``remainder`` is ``content`` with the sentinels removed — the remainder
    becomes the default slot / ``children`` / ``inner_block``.
    """

    slots: dict[str, list[dict[str, Any]]] = {}

    def _consume(match: re.Match[str]) -> str:
        encoded = match.group(1)
        try:
            payload = json.loads(html.unescape(encoded))
        except (ValueError, TypeError):
            # Malformed sentinel — leave raw bytes in place.
            return match.group(0)
        if not isinstance(payload, dict) or "name" not in payload:
            return match.group(0)
        slot_name = str(payload["name"])
        slots.setdefault(slot_name, []).append(
            {
                "name": slot_name,
                "attrs": payload.get("attrs", {}),
                "content": payload.get("content", ""),
            }
        )
        return ""

    remainder = _SLOT_SENTINEL_RE.sub(_consume, content)
    return slots, remainder


# ---------------------------------------------------------------------------
# Tag handlers
# ---------------------------------------------------------------------------


class CallTagHandler:
    """Implements ``{% call NAME key=val %}body{% endcall %}`` / ``{% component %}``.

    The first positional argument is the component name. Remaining arguments
    are parsed as ``key=val`` kwargs. The block body (``content``) is
    searched for ``{% slot %}`` sentinels; extracted slots are injected
    into the assigns dict alongside a ``children`` / ``inner_block`` string
    holding the non-slot remainder.
    """

    def render(self, args: list[str], content: str, context: dict[str, Any]) -> str:
        # ``args`` arrives as a list of strings. Convert non-string entries
        # defensively since some Rust paths pass non-string tokens.
        str_args = [str(a) for a in args]

        name, kwargs = _parse_call_args(str_args, context)
        if not name:
            return "<!-- djust: {% call %} missing component name -->"

        target = _COMPONENT_REGISTRY.get(name)
        if target is None:
            raise RuntimeError(
                f"Component '{name}' is not registered. Use @component to register it."
            )

        # Pull slots out of the body first — remaining content is the default slot.
        slots, default_content = _extract_slots(content)

        # Collect declarations if the target has them.
        declared_assigns: list[Assign] = []
        declared_slots: list[Slot] = []
        if isinstance(target, type):
            declared_assigns = merge_assign_declarations(target)
            declared_slots = merge_slot_declarations(target)
        else:
            declared_assigns = list(getattr(target, "_djust_assigns", []) or [])
            declared_slots = list(getattr(target, "_djust_slots", []) or [])

        # Validate assigns.
        try:
            if declared_assigns:
                kwargs = validate_assigns(declared_assigns, kwargs)
            if declared_slots:
                validate_slots(declared_slots, slots)
        except AssignValidationError as exc:
            # Block handlers are called from the Rust renderer; raising here
            # bubbles a clean error message back up.
            raise RuntimeError(f"Component '{name}' validation failed: {exc}") from exc

        # Build the full assigns mapping passed into the component. Body
        # content wins over any caller-supplied children/inner_block kwargs
        # (Phoenix convention: the block body is the content). Slots
        # likewise cannot be overridden by kwargs since they come from the
        # block body's {% slot %} tags.
        assigns: dict[str, Any] = dict(kwargs)
        assigns["children"] = default_content
        assigns["inner_block"] = default_content
        assigns["slots"] = slots

        # Dispatch.
        if isinstance(target, type):
            # Only LiveComponent subclasses are valid class targets. Other
            # classes would instantiate confusingly and fail on .render().
            from .base import LiveComponent

            if not issubclass(target, LiveComponent):
                raise RuntimeError(
                    f"Component '{name}' is a class but not a LiveComponent "
                    f"subclass. Only LiveComponent subclasses and @component-"
                    f"decorated functions can be invoked via {{% call %}}."
                )
            instance = target(**{k: v for k, v in kwargs.items() if not k.startswith("_")})
            # Expose slots + children on the instance for template access.
            instance._slots = slots
            instance._children = default_content
            html_out = instance.render()
            return html_out

        # Plain callable.
        try:
            result = target(assigns)
        except TypeError:
            # Support legacy function components that take **kwargs.
            result = target(**assigns)
        if result is None:
            return ""
        return str(result)


class SlotTagHandler:
    """Implements ``{% slot NAME key=val %}body{% endslot %}``.

    Emits a sentinel that :class:`CallTagHandler` collects and converts into
    the ``slots`` mapping. When used outside a ``{% call %}`` context the
    sentinels remain in the output — guard against this by rendering slots
    only inside component invocations.
    """

    def render(self, args: list[str], content: str, context: dict[str, Any]) -> str:
        str_args = [str(a) for a in args]
        if not str_args:
            name = "default"
            rest: list[str] = []
        else:
            first = str_args[0]
            if "=" in first:
                name = "default"
                rest = str_args
            else:
                raw = first.strip()
                if (raw.startswith('"') and raw.endswith('"')) or (
                    raw.startswith("'") and raw.endswith("'")
                ):
                    name = raw[1:-1]
                else:
                    name = raw
                rest = str_args[1:]

        attrs = _parse_kwargs(rest, context)
        return _emit_slot_sentinel({"name": name, "attrs": attrs, "content": content})


# Matches a bare identifier or a dotted chain of identifiers: `slot`,
# `slot.0`, `slots.col.0.content`. Used by RenderSlotTagHandler to decide
# whether a pre-resolved arg is still an unresolved path (resolution
# failed upstream) vs. a resolved scalar value. See #861.
_LOOKS_LIKE_PATH = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_0-9][\w]*)*$")


class RenderSlotTagHandler:
    """Inline tag: ``{% render_slot REF %}``.

    Resolves ``REF`` against the current context (supporting dotted paths
    like ``slots.col.0``). If the resolved value is a dict it is assumed to
    be a single slot entry; if a list, the first entry is emitted. The
    content is returned verbatim (already-escaped HTML from the parent).

    **Dual-caller contract (#861)**: this handler is called from two paths
    with different arg shapes:

    1. **Rust template engine** — the engine pre-resolves variable args
       before calling handlers. For `{% render_slot slots.col.0 %}`,
       ``args[0]`` arrives already as the resolved slot dict, JSON-encoded
       (because ``value_to_arg_string`` JSON-serializes ``List``/``Object``
       values for transport across the FFI boundary). The handler's
       ``_resolve_context_path`` call on a JSON string would then fail,
       producing silent empty output — the exact #861 symptom.

    2. **Direct Python call** — ``RenderSlotTagHandler().render(["slots.col.0"], ctx)``
       passes the literal dotted-path string; the handler resolves against
       ``context`` itself.

    Resolution: try a JSON parse first (shape 1 — Rust-engine output).
    If that yields a structured value, extract from it. Otherwise fall
    back to the path-resolution semantics (shape 2 — direct callers).
    This keeps end-to-end Rust rendering of named slots working and
    preserves the existing direct-caller contract.
    """

    def render(self, args: list[str], context: dict[str, Any]) -> str:
        if not args:
            return ""
        raw = str(args[0])

        # Shape 1: Rust-engine pre-resolved structured value (JSON string).
        # Only treat as JSON if it parses AS a list or dict — bare strings,
        # numbers, and bools pass through below.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, (list, dict)):
                return self._render_value(parsed)
        except (ValueError, TypeError):
            # Not valid JSON; fall through to other resolution shapes below.
            pass

        # Shape 2: the arg still LOOKS like a dotted path (no dots → simple
        # identifier; with dots → multi-segment identifier). Under the Rust
        # engine, an arg that still looks like an unresolved path means
        # resolution failed upstream — preserve the old direct-caller
        # contract and return empty on miss. This also handles the direct-
        # Python-caller case where args[0] is a literal path.
        if _LOOKS_LIKE_PATH.match(raw):
            value = _resolve_context_path(raw, context)
            if value is None:
                return ""
            return self._render_value(value)

        # Shape 3: a pre-resolved scalar (Rust engine stringified a number,
        # bool, or string). Emit as-is — this is the value the user asked
        # for. Covers `{% render_slot slot.content %}` where the content is
        # a string that doesn't itself look like a dotted identifier.
        return raw

    @staticmethod
    def _render_value(value: Any) -> str:
        if isinstance(value, list):
            if not value:
                return ""
            value = value[0]
        if isinstance(value, dict):
            return str(value.get("content", ""))
        return str(value)


def _resolve_context_path(path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted path (``slots.col.0.content``) against ``context``.

    Supports dict keys, list indices (numeric path segments) and attribute
    access. Missing segments return ``None``.
    """

    parts = path.split(".")
    current: Any = context
    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            if part in current:
                current = current[part]
                continue
            # Numeric key as string for dicts with int keys.
            try:
                int_key = int(part)
                if int_key in current:
                    current = current[int_key]
                    continue
            except ValueError:
                # `part` isn't an integer key; fall through to return None below.
                pass
            return None
        if isinstance(current, (list, tuple)):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
            continue
        # Fallback — attribute access.
        try:
            current = getattr(current, part)
        except AttributeError:
            return None
    return current
