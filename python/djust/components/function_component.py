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
from typing import Any, Callable, ClassVar, Optional, Union

from .._html import safe_html
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
    fn: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    assigns: Optional[list[Assign]] = None,
    slots: Optional[list[Slot]] = None,
) -> Callable[..., Any]:
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

    def _wrap(target: Callable[..., Any]) -> Callable[..., Any]:
        component_name = name or target.__name__
        # Attach @component metadata onto the callable (the standard djust
        # decorator-metadata pattern); mypy can't model dynamic attrs on a
        # plain Callable.
        target._djust_assigns = assigns or []  # type: ignore[attr-defined]
        target._djust_slots = slots or []  # type: ignore[attr-defined]
        target._djust_component_name = component_name  # type: ignore[attr-defined]
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
        """Mark the component's rendered HTML safe, at the ONE exit (#2379).

        Since #2379 the Rust bridge escapes a tag handler's return unless it
        carries ``__html__`` — Django's ``SimpleNode.render`` rule. A
        COMPONENT's return is its rendered markup by contract, the same status
        Django gives ``{% include %}``'s output rather than a ``simple_tag``'s
        return value, so it is marked rather than escaped. What this does NOT
        change is where the responsibility sits: a component that interpolates
        user data into its own markup escapes it itself, exactly as a template
        author does — that was true before this change and is true after.

        Marked here rather than at the four returns of ``_render_component``:
        N sites need N tests (#1104), and one boundary needs one.
        """
        return safe_html(self._render_component(args, content, context))

    def _render_component(self, args: list[str], content: str, context: dict[str, Any]) -> str:
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
            # Expose slots + children on the instance for template access. These
            # are dynamic per-invocation attributes (distinct from the class-level
            # ``slots`` declaration list), attached only for the template render.
            instance._slots = slots  # type: ignore[attr-defined]
            instance._children = default_content  # type: ignore[attr-defined]
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
        # `mark_safe` since #2379: the bridge now escapes a handler return
        # without `__html__`, and this one is a `<!--DJUST_SLOT_V1:…-->`
        # SENTINEL that `CallTagHandler` parses back out of the rendered
        # body. Escaping it would turn the comment into visible text and
        # break slot collection outright — the sentinel's own payload is
        # already JSON-encoded and HTML-escaped by `_emit_slot_sentinel`.
        return safe_html(_emit_slot_sentinel({"name": name, "attrs": attrs, "content": content}))


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

    **This handler resolves its OWN operand (#2423).** It declares
    :attr:`RESOLVE_ARG_POSITIONS` as the empty set, so the Rust engine passes
    ``args[0]`` as the LITERAL token the template wrote — the inline-tag twin
    of the policy ``{% regroup %}`` uses to keep its keyword operands literal
    (#2041).

    That is what makes the trust question answerable. Once the engine has
    resolved the operand, ``{% render_slot slots.col.0.content %}`` (a slot
    body the PARENT already rendered and escaped) and ``{% render_slot p %}``
    over a hostile context string are the SAME opaque string, and the exit had
    to escape both — over-escaping the first, which is #2423. With the path in
    hand the two are structurally distinct: one terminates at the ``content``
    key of a ``{"name", "attrs", "content"}`` slot entry, the other at a bare
    context value.

    It also retires the #861 dual-caller split rather than patching it: the
    engine now hands this handler exactly what a direct Python caller does —
    ``RenderSlotTagHandler().render(["slots.col.0"], ctx)`` — so there is ONE
    arg shape instead of two. The JSON-decode arm stays only for a caller that
    passes a pre-resolved structure of its own.
    """

    #: Resolve NOTHING — see the class docstring. `frozenset()` and not
    #: `None`: `None` means "no policy, resolve everything", which is the
    #: default this handler is opting OUT of.
    RESOLVE_ARG_POSITIONS: ClassVar[frozenset[int]] = frozenset()

    def render(self, args: list[str], context: dict[str, Any]) -> str:
        if not args:
            return ""
        raw = str(args[0]).strip()
        literal = _strip_quotes(raw)
        if literal is not None:
            raw = literal

        # A caller that passed a pre-resolved structure of its own. NOT the
        # engine any more (#2423) — it hands over the literal token — so this
        # arm is reachable only from Python, and only for a caller that chose
        # to encode. Kept because dropping it would break that caller for no
        # gain; every template spelling takes the path arm below.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, (list, dict)):
                return self._render_value(parsed)
        except (ValueError, TypeError):
            # Not JSON. That is the ORDINARY case, not an error: every template
            # spelling of this operand arrives as a dotted path, which the arm
            # below resolves. Only the legacy caller noted above sends JSON, so
            # a parse failure here means "try the path", never "something broke".
            pass

        # The operand as a dotted path, resolved HERE. A miss is the empty
        # string, which is the contract this handler has always had.
        if not _LOOKS_LIKE_PATH.match(raw):
            # Not a path and not a structure — nothing to resolve. Emitted as
            # a plain `str` so the #2379 bridge escapes it: this is a value
            # some caller handed over directly, and nothing has vouched for it.
            return raw
        value = _resolve_context_path(raw, context)
        if value is None:
            return ""
        if isinstance(value, str) and _terminates_in_slot_content(raw, context):
            # `{% render_slot slots.col.0.content %}` — the ONE scalar spelling
            # that is a slot body rather than a context value (#2423). Marked
            # for exactly the reason `_render_value`'s dict exit is: the parent
            # engine rendered and escaped it, so the bridge must not escape it
            # again. The discriminator is STRUCTURAL and comes from before
            # resolution — the path terminates at the `content` key of a slot
            # entry — which is the information the pre-resolved string had
            # already lost.
            return safe_html(value)
        return self._render_value(value)

    @staticmethod
    def _render_value(value: Any) -> str:
        """The ONE already-escaped exit is the slot entry's ``content`` (#2421).

        Since #2379 the Rust bridge escapes a handler's return unless it
        carries ``__html__``. That is right for a value this handler merely
        echoes out of the render context, and wrong for the one value it
        echoes that the PARENT already rendered — so the two are separated
        here rather than at the call sites (#1104: mark at one exit, not N).

        **``value["content"]`` — marked.** A slot entry reaches this handler
        as ``{"name", "attrs", "content"}``, built by ``_extract_slots`` from
        the ``<!--DJUST_SLOT_V1:…-->`` sentinel that ``SlotTagHandler`` emits.
        ``content`` there is the *pre-rendered* block body the engine handed
        the block handler, which is verified — not merely documented — to be
        already-escaped: ``{% slot h %}{{ evil }}{% endslot %}`` with
        ``evil = "<img src=x onerror=alert(1)>"`` puts
        ``&lt;img src=x onerror=alert(1)&gt;`` in the entry, while literal
        markup written in the block body survives raw. That is exactly the
        status Django gives ``{% include %}``'s output, and escaping it again
        is the #2421 regression: every function component and named slot
        renders its own markup as visible text, and any context data inside
        it double-escapes to ``&amp;lt;``.

        **The trailing ``str(value)`` — NOT marked.** That branch is reached
        when ``REF`` resolves to a bare context value rather than a slot
        entry, e.g. ``{% render_slot p %}`` with ``p = "<img src=x
        onerror=alert(1)>"``. Nothing has escaped it, so it must stay a plain
        ``str`` and let the bridge escape it — that is the framework-reachable
        half of the #2379 XSS, live on 1.0.0 / 1.0.8 / 1.1.0 with no
        ``|safe``, no ``mark_safe`` and no app-written handler. Marking the
        whole return would restore it.

        ``render()``'s `.content`-path exit is marked for the SAME reason this
        one is, and its discriminator is the un-resolved path (#2423); its
        remaining ``return raw`` — a caller-supplied non-path, non-structure
        string — stays unmarked, because nothing has vouched for that.
        """
        if isinstance(value, list):
            if not value:
                return ""
            value = value[0]
        if isinstance(value, dict):
            return safe_html(str(value.get("content", "")))
        return str(value)


#: The exact key set ``_extract_slots`` builds a slot entry with. Membership is
#: what licenses the mark in :meth:`RenderSlotTagHandler.render`, so it is
#: derived from that builder rather than guessed — see
#: ``test_the_slot_entry_shape_is_the_builders_own``.
_SLOT_ENTRY_KEYS = frozenset({"name", "attrs", "content"})


def _strip_quotes(token: str) -> Optional[str]:
    """The text inside a matching pair of quotes, or ``None``."""

    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return None


def _terminates_in_slot_content(path: str, context: dict[str, Any]) -> bool:
    """Does ``path`` end at the ``content`` key of a SLOT ENTRY? (#2423)

    The discriminator the pre-resolved string could not carry. ``True`` only
    when the last segment is literally ``content`` and the segment before it
    resolves to a dict with exactly the key set ``_extract_slots`` builds —
    ``{"name", "attrs", "content"}``.

    That set is the whole security argument, so it is deliberately EXACT
    rather than a superset test: a hostile context dict would have to be
    shaped as a slot entry AND be reached through a template the author wrote
    as ``{% render_slot d.content %}``, which is the same trust
    ``{% render_slot d %}`` already extends to ``d``'s ``content`` at
    ``_render_value``'s dict exit (#2421). So this widens nothing that exit
    does not already grant; it only lets the ``.content`` SPELLING reach it.
    """

    parent_path, _, last = path.rpartition(".")
    if last != "content" or not parent_path:
        return False
    parent = _resolve_context_path(parent_path, context)
    return isinstance(parent, dict) and frozenset(parent) == _SLOT_ENTRY_KEYS


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
