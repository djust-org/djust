"""Rust template engine registration for djust-components.

One render path per component tag where it costs nothing (S3 of
``docs/reviews/2026-11-11-djust-2-consolidated.md``, decided 2026-11-12): a ``djust_components`` tag
renders under the Rust engine through the ``{% load %}`` bridge (#2547, ``djust.template_libraries``),
which runs the Python tag's OWN compile function and renders the Django node it returns, so the output
is the Python tag's by construction. The tags are registered here, at ``ready()``, so a Rust template
uses them without ``{% load djust_components %}``.

A native twin (a hand-written handler in this module) is kept only where a benchmark earns it or the
bridge lacks a capability the tag needs, and each kept twin is pinned against its Python tag
(``python/tests/test_component_single_render_path.py``), its known divergences listed there:

* the core layout tags (``app_shell``, ``app_sidebar``, ``app_header``, ``app_content``): the
  root-proof protocol (``_render_root_parts``, ``core_root_layout_object`` in
  ``crates/djust_templates/src/registry.rs``), which a bridged node cannot take part in;
* the inclusion tags ``badge``, ``avatar``, ``progress``, ``pagination``, ``data_table``: through the
  bridge each use renders an inclusion template through the backend, measured 4 to 8x slower (and
  ``data_table`` carries native-only features whose fate is #3869);
* the block tags whose Django node reads its children (``accordion``, ``sidebar``, ``nav_menu``,
  ``toolbar``, ``filter_bar``, ``form_array``, ``page_header``) and their child tags: the bridge can
  only serve them by handing the body to Django's engine, which cannot compile a Rust-only tag
  (``{% call %}``, ``{% component %}``, ``{% slot %}``, ``{% dj_suspense %}``) and loses the
  ``{% if %}`` boundary markers the Rust renderer emits. ``tabs`` and ``split_pane`` already took that
  raw-body route before (``RAW_BODY_TAGS``).

Inline handlers implement: render(self, args, context) -> str
Block handlers implement:  render(self, args, content, context) -> str
"""

import json as _json
import re as _re_filters
import pathlib as _pathlib
import uuid as _uuid
from html.parser import HTMLParser
from typing import Any, cast

from django.utils.html import conditional_escape, escape
from django.utils.safestring import mark_safe


def _safe(html: str) -> str:
    """Typed wrapper over Django's ``mark_safe``.

    ``mark_safe`` is decorated with ``@keep_lazy`` (untyped), so mypy infers
    its return as ``Any`` when Django ships without type stubs — which would
    leak ``Any`` out of every handler's ``-> str`` return (``no-any-return``).
    This wrapper pins the return to the concrete built-in ``str`` (``mark_safe`` returns a ``SafeString``,
    a ``str`` subclass — the runtime value is unchanged) so the strict island stays clean. Behaviorally identical to
    calling ``mark_safe`` directly — the HTML bytes are unchanged.
    """
    return cast(str, mark_safe(html))


from djust.components.utils import (
    safe_css_color,
    safe_css_length,
    format_cell as _format_cell_util,
    interpolate_color_gradient,
    url_attr,
    data_url_attr,
)
from djust.components.utils import attr_text
from djust.template_tags import TYPED_ARGS_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quoted(text: str) -> bool:
    """``text`` is a quoted literal: it opens and closes with the same quote character."""
    return len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]


def _parse_args(args: list[str], context: dict[str, object]) -> dict[str, object]:
    """Parse handler arg list ["key='val'", "key2=var"] into a dict.

    The engine has already resolved every operand, so ``val`` is the resolved TEXT (a quoted
    literal arrives as written). Values that are JSON-encoded lists/objects (from the Rust
    engine's variable resolution) are deserialized automatically.

    The text is read exactly as it arrived. It used to be stripped (``val.strip()``), which also
    stripped a RESOLVED value: ``ev = "x "`` reached the page as ``x`` where the Django tag keeps
    ``"x "`` (#3872). A source token never carries whitespace (the engine splits a tag on it), so
    nothing but a resolved value was ever stripped.

    A name the engine could not resolve never gets here as a name: an inline tag's argument arrives
    empty, and ``_TypedBlockOperands`` makes a block tag's the same (#3871). What is left of the
    ``context.get(val, val)`` fallback is the one thing the engine does not do: a handler called
    DIRECTLY with a raw token (``render(["users=users"], {"users": ...})``, which is how ~400 tests
    call one). It also looks a RESOLVED value up a second time, so ``ev = "name"`` becomes whatever
    the variable ``name`` holds; that is a separate defect, pinned in
    ``test_a_value_that_is_also_a_variable_name_is_resolved_a_second_time``.
    """

    result: dict[str, object] = {}
    # The operands the engine could only hand over as a stand-in (#3768): a tuple, a namedtuple,
    # a UUID, a Path. Read once, and consulted BEFORE the text is parsed: the typed value replaces
    # the operand, so decoding the stand-in (for a 2,000,000-element tuple, a JSON list built and
    # thrown away) is work for nothing (#3849). The key keeps its place in ``result``.
    typed = context.get(TYPED_ARGS_KEY)
    if not isinstance(typed, dict):
        typed = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip()
        if key in typed and isinstance(typed[key], _RECOVERED_TYPES):
            result[key] = _from_typed(typed[key])
            continue
        # String literal — strip quotes
        if _quoted(val):
            result[key] = val[1:-1]
        # JSON array or object (from Rust variable resolution)
        elif (val.startswith("[") and val.endswith("]")) or (
            val.startswith("{") and val.endswith("}")
        ):
            try:
                result[key] = _json.loads(val)
            except (ValueError, TypeError):
                result[key] = context.get(val, val)
        # Boolean
        elif val in ("True", "true"):
            result[key] = True
        elif val in ("False", "false"):
            result[key] = False
        elif val == "":
            result[key] = ""
        # None
        elif val in ("None", "null"):
            result[key] = None
        elif val != val.strip():
            # Padding is part of a resolved value (#3872): ``int(" 5 ")`` is 5, and a text that
            # was ``" 5 "`` is not the number.
            result[key] = val
        else:
            # Try numeric before falling back to the text
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    # Variable reference — look up in context
                    result[key] = context.get(val, val)
    return result


def _passed_or_context(
    kw: dict[str, object],
    name: str,
    context: dict[str, object],
    key: str | None = None,
    default: object = None,
) -> object:
    """``kw[name]`` when the argument was PASSED (even empty, ``None`` or ``False``); only when it
    is ABSENT, the render context's ``key`` (default: ``name``).

    This is the one place a handler reads the context as a stand-in for an argument. It decides on
    PRESENCE, never on truthiness: ``kw.get(name) or context.get(key)`` used the context's
    ``rows`` / ``options`` / ``steps`` for ``rows=[]``, where the Python tag uses the empty value
    it was given.
    """
    if name in kw:
        return kw[name]
    return context.get(key if key is not None else name, default)


class TupleValue(list):  # type: ignore[type-arg]
    """A tuple a native handler was given (#3768).

    The engine hands a handler a ``tuple`` as a JSON list, and a handler reads a list slot with
    ``isinstance(rows, list)`` (~55 places): a real ``tuple`` there would be dropped as "not a
    list", turning ``rows=my_tuple_of_dicts`` (an ordinary thing to pass) into an empty table. So
    the handler keeps getting a ``list``, a subclass that prints the way the tuple does. A text
    slot then reads ``(1, 2)`` as Django's tag prints it, where it read ``[1, 2]``.

    ``NamedTupleValue`` is the same for a namedtuple (#3849).
    """

    __slots__ = ()

    def __repr__(self) -> str:
        # Iterative, like ``_from_typed``: a tuple nested a few hundred deep is legal input (it
        # printed under Django and under this engine before #3768), and a recursive ``repr`` here
        # would raise RecursionError one level at a time. Only TupleValue members recurse in
        # this walk; any other member is printed by its own ``repr``.
        out = [_opening(self)]
        stack: list[tuple[TupleValue, int]] = [(self, 0)]
        while stack:
            node, index = stack[-1]
            if index == len(node):
                if len(node) == 1 and not isinstance(node, NamedTupleValue):
                    out.append(",")
                out.append(")")
                stack.pop()
                continue
            stack[-1] = (node, index + 1)
            if index:
                out.append(", ")
            if isinstance(node, NamedTupleValue) and index < len(node._fields):
                out.append(node._fields[index] + "=")
            item = node[index]
            if isinstance(item, TupleValue):
                out.append(_opening(item))
                stack.append((item, 0))
            else:
                out.append(repr(item))
        return "".join(out)

    __str__ = __repr__


class NamedTupleValue(TupleValue):
    """A namedtuple a native handler was given, still a ``list`` (#3849).

    Prints as the namedtuple does (``P(x=1, y=2)``, which is what Django's tag prints), and is a
    ``list`` for the reason ``TupleValue`` is. The class it came from is gone (the engine holds a
    namedtuple as a name, its fields and its items), so a ``__repr__`` the class defined for itself
    is not reproduced: this prints the default one.
    """

    __slots__ = ("_name", "_fields")

    def __init__(self, items: Any = (), name: str = "tuple", fields: tuple[str, ...] = ()) -> None:
        super().__init__(items)
        self._name = name
        self._fields = fields


def _opening(node: TupleValue) -> str:
    """The text a ``TupleValue`` opens with: ``(`` for a tuple, ``P(`` for a namedtuple."""
    return node._name + "(" if isinstance(node, NamedTupleValue) else "("


def _namedtuple_shape(source: tuple[Any, ...]) -> tuple[str, tuple[str, ...]] | None:
    """``(name, fields)`` when ``source`` is a namedtuple, else ``None``.

    A ``_fields`` tuple with one name per member. This is NARROWER than the engine's rule when it
    reads a Python object (``Value::NamedTuple`` also accepts a ``list`` ``_fields``): it does not
    need to be wider, because the typed channel hands a handler the class the engine REBUILT with
    ``collections.namedtuple``, whose ``_fields`` is always a ``tuple``.
    """
    fields = getattr(source, "_fields", None)
    if type(fields) is tuple and len(fields) == len(source):
        return type(source).__name__, fields
    return None


def _empty_like(source: tuple[Any, ...]) -> TupleValue:
    shape = _namedtuple_shape(source)
    return TupleValue() if shape is None else NamedTupleValue((), *shape)


def _from_typed(value: object) -> object:
    """A typed operand as a handler is handed it: a ``tuple`` becomes a ``TupleValue``, a
    namedtuple a ``NamedTupleValue`` (#3849); anything else is returned as it is.

    Iterative, not recursive: the members of a tuple are converted too (a row of a tuple of
    tuples must be a list), and a recursive walk raised RecursionError for a tuple nested 500
    deep that rendered before. An explicit stack has no depth limit but memory.

    A tuple with no tuple among its members, the usual case and the only one that can be large, is
    copied in one C-level pass (``list(value)``): the member types are collected with
    ``set(map(type, value))``, which runs no Python bytecode per member, and a per-member Python
    loop on a 2,000,000-element tuple was the cost the review of #3843 measured (#3849).
    """
    if not isinstance(value, tuple):
        return value
    if not any(issubclass(kind, tuple) for kind in set(map(type, value))):
        shape = _namedtuple_shape(value)
        return TupleValue(value) if shape is None else NamedTupleValue(value, *shape)
    root = _empty_like(value)
    pending = [(value, root)]
    while pending:
        source, target = pending.pop()
        for item in source:
            if isinstance(item, tuple):
                child = _empty_like(item)
                target.append(child)
                pending.append((item, child))
            else:
                target.append(item)
    return root


#: The types a native handler is handed a stand-in for, which a Python tag tells apart from the
#: stand-in (#3768): a ``UUID`` or a ``Path`` arrives as its ``str`` (``int(uuid)`` works in
#: Python, ``int("...")`` does not), and a ``tuple`` (not in this tuple: see ``TupleValue``) as
#: the JSON list ``[1, 2]`` where Django's tag prints ``(1, 2)``. The engine resolves the operand
#: through the floored walk ``{{ }}`` uses and, for a handler that declares ``TYPED_ARGS``, passes
#: the real value (``TYPED_ARGS_KEY``); ``_parse_args`` puts it back. Only these types, so a list,
#: dict, number, string or object reaches a handler exactly as before.
#:
#: NOT a ``bytes``, ``set`` or ``frozenset``: they are iterable, and an iterable crosses to Python
#: as the engine's ``TemplateObject`` wrapper, not as itself (``value_into_handler_pyobject``), so
#: they cannot be recovered here (recorded in ``KNOWN_TYPE_LOSS``, #3768).
STANDIN_TYPES = (_uuid.UUID, _pathlib.PurePath)

#: What ``_parse_args`` takes off the typed channel: ``STANDIN_TYPES`` and any ``tuple`` (a plain
#: one, and a namedtuple, #3849: the engine hands a namedtuple over as its rebuilt class).
_RECOVERED_TYPES = (tuple, *STANDIN_TYPES)


def _typed_args(context: dict[str, object]) -> dict[str, object]:
    """The operands the engine resolved, as typed Python values, by parameter name (#3756).

    They arrive under ``TYPED_ARGS_KEY``, the reserved context key the engine fills for a
    handler that declares ``TYPED_ARGS`` (``crates/djust_templates/src/renderer.rs``,
    ``TYPED_ARGS_KEY``). The constant is defined in ``djust.template_tags`` (``{% live_render %}``
    reads it too, #3838) and imported above.
    """
    typed = context.get(TYPED_ARGS_KEY)
    return typed if isinstance(typed, dict) else {}


class _TypedOperands:
    """``handler``, declaring ``TYPED_ARGS`` so its ``_parse_args`` gets the types back (#3768).

    A native inline handler reads its operands through ``_parse_args``, which sees the engine's
    stand-ins (text, JSON). Declaring ``TYPED_ARGS`` makes the engine hand it the operands as the
    values they are, and ``_parse_args`` takes back the ones in ``STANDIN_TYPES``. Done here, once,
    for every inline handler, rather than as a class attribute on each of ~130 (one change instead
    of 130 that can drift); ``json_viewer`` declares it itself and is not wrapped.

    Everything else the engine reads off a handler (``RESOLVE_ARG_POSITIONS``, ...) is forwarded.
    """

    TYPED_ARGS = True
    #: Hand over only the operands ``_parse_args`` takes back (a tuple, an object), not every one:
    #: a 100,000-row list is not materialised a second time as Python objects for nothing.
    TYPED_ARGS_STANDINS_ONLY = True

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    def render(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._adjust(args, kwargs)
        return self._handler.render(*args, **kwargs)

    def _render_root_parts(self, *args: Any, **kwargs: Any) -> Any:
        """The core layout protocol (2.0), with the same arguments ``render`` hands on.

        The engine calls this in place of ``render`` for the four built-in layout handlers
        when it tracks the page root. Defined here so the call does not fall through
        ``__getattr__`` to the handler and skip ``_adjust``. A handler without the protocol
        raises ``AttributeError``, as it would unwrapped.
        """
        args, kwargs = self._adjust(args, kwargs)
        return self._handler._render_root_parts(*args, **kwargs)

    def _adjust(self, args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        """The arguments the wrapped handler is called with; a subclass changes them here, so
        ``render`` and ``_render_root_parts`` keep one signature for every wrapper."""
        return args, kwargs

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not found on the wrapper itself.
        if name == "_handler":
            raise AttributeError(name)
        return getattr(self._handler, name)


def _with_typed_operands(handler: Any) -> Any:
    return handler if getattr(handler, "TYPED_ARGS", False) else _TypedOperands(handler)


class _TypedBlockOperands(_TypedOperands):
    """A BLOCK handler, handed an unresolved operand the way an inline handler is (#3871).

    The engine resolves the operands of an inline tag so that a name that does not resolve is
    EMPTY (``{% dj_button event=nope %}`` -> ``event=``), as the Django tag renders it. A block
    tag's operands go through another resolver, whose contract is "unresolved => keep the raw
    token" (a ``{% regroup %}`` keyword must survive it): ``{% nav_menu toggle_event=nope %}``
    arrived as ``toggle_event=nope`` and the variable's NAME became the event name. The handler
    cannot tell that text from an operand that RESOLVED to ``nope``, and a lookup against the
    context (``context.get(val, val)``) answers the wrong question: it re-resolves a resolved
    value.

    The engine knows. A handler that declares ``TYPED_ARGS`` without ``TYPED_ARGS_STANDINS_ONLY``
    is handed every operand that resolved (``TYPED_ARGS_KEY``), keyed by parameter name, and an
    operand that did not is missing from it. This wrapper blanks exactly those, so the handler
    sees what an inline handler sees. A quoted literal has no resolution to fail (the engine skips
    it) and is left alone, as is every operand that is in the channel, whatever its value.
    Positional operands are named at compilation (``with_positional_args``), so they are keyed too.
    """

    #: Every operand, not only the types ``_parse_args`` takes back: the point is which ones are
    #: THERE. The operands of a block tag are scalars and short lists, so the second conversion
    #: ``TYPED_ARGS_STANDINS_ONLY`` avoids for a 100,000-row inline operand is not a cost here.
    TYPED_ARGS_STANDINS_ONLY = False

    def _adjust(self, args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        # A block handler is called ``render(args, content, context)``, by position (the engine) or
        # by name (a direct caller).
        names = ("args", "content", "context")
        kwargs = dict(kwargs)
        try:
            values = [*args, *(kwargs.pop(name) for name in names[len(args) :])]
        except KeyError as missing:
            raise TypeError(f"render() missing the required argument {missing}") from None
        if len(values) != len(names):
            raise TypeError(f"render() takes {len(names)} arguments, got {len(values)}")
        arg_list, content, context = values
        return (_without_unresolved(arg_list, _typed_args(context)), content, context), kwargs


_KEYWORD = _re_filters.compile(r"\w+")


def _without_unresolved(args: list[str], resolved: dict[str, object]) -> list[str]:
    """``args`` with each ``key=<operand>`` the engine could not resolve emptied to ``key=``."""
    out = []
    for arg in args:
        key, sep, val = arg.partition("=")
        text = val.strip()
        if (
            sep
            and text
            and _KEYWORD.fullmatch(key.strip())
            and not _quoted(text)
            and key.strip() not in resolved
        ):
            arg = key + "="
        out.append(arg)
    return out


def _with_typed_block_operands(handler: Any) -> Any:
    return handler if getattr(handler, "TYPED_ARGS", False) else _TypedBlockOperands(handler)


# ---------------------------------------------------------------------------
# Block handlers (wrap content — e.g. {% modal %}...{% endmodal %})
# ---------------------------------------------------------------------------


class AccordionHandler:
    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        accordion_id = conditional_escape(kw.get("id", "accordion"))
        return _safe(f'<div class="accordion" id="{accordion_id}">{content}</div>')


class AccordionItemHandler:
    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        title = conditional_escape(kw.get("title", ""))
        item_id = conditional_escape(kw.get("id", ""))
        event = conditional_escape(kw.get("event", "accordion_toggle"))
        is_open = kw.get("open", False)
        open_cls = "accordion-item--open" if is_open else ""
        expanded = "true" if is_open else "false"
        panel_hidden = "" if is_open else " hidden"
        return _safe(
            f'<div class="accordion-item {open_cls}">'
            f'<button class="accordion-trigger" aria-expanded="{expanded}" '
            f'dj-click="{event}" data-value="{item_id}">'
            f'<span class="accordion-title">{title}</span>'
            f'<svg class="accordion-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
            f'<polyline points="6 9 12 15 18 9"></polyline></svg>'
            f"</button>"
            f'<div class="accordion-panel"{panel_hidden}>{content}</div>'
            f"</div>"
        )


class ProgressHandler:
    def render(self, args: list[str], context: dict[str, object]) -> str:
        from djust.components.templatetags.djust_components import progress as _progress

        kw = _parse_args(args, context)
        # The Python tag's own clamp, so a ``value`` it refuses (``None``: TypeError, ``"abc"``:
        # ValueError) is refused here and not drawn as 0% (#3667). ``int()`` says which key of the
        # tag's context this is: the clamped number, not one of the raw ones beside it (#3847).
        value = int(_progress(value=kw.get("value", 0))["value"])
        label = conditional_escape(kw.get("label", ""))
        size = conditional_escape(kw.get("size", "md"))
        color = conditional_escape(kw.get("color", "primary"))
        show_label = kw.get("show_label", True)
        label_row = ""
        if label or show_label:
            label_part = f'<span class="progress-label">{label}</span>' if label else ""
            pct_part = f'<span class="progress-value">{value}%</span>' if show_label else ""
            label_row = f'<div class="progress-label-row">{label_part}{pct_part}</div>'
        track_size = {"sm": "progress-track-sm", "lg": "progress-track-lg"}.get(str(size), "")
        color_class = "" if color == "primary" else color
        # The fill's width is a class, not ``style="width:N%"``: a ``style-src`` without
        # ``'unsafe-inline'`` refuses the attribute and the bar drew empty (#3969). The track
        # carries the role, the value and a name, as the Python tag's does.
        # An attribute, so ``escape`` (always) and not ``conditional_escape`` (#3898). Named by
        # the label only when there is one, as the Python tag's is.
        name = escape(str(kw.get("label", "")))
        name_attr = f' aria-label="{name}"' if name else ""
        return _safe(
            f'<div class="progress-wrapper">'
            f"{label_row}"
            f'<div class="progress-track {track_size}" role="progressbar" '
            f'aria-valuenow="{value}" aria-valuemin="0" aria-valuemax="100"{name_attr}>'
            f'<div class="progress-bar {color_class} dj-progress-w-{value}"></div>'
            f"</div>"
            f"</div>"
        )


class BadgeHandler:
    def render(self, args: list[str], context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        status = conditional_escape(kw.get("status", "default"))
        pulse = kw.get("pulse", False)
        pulse_cls = " badge-pulse" if pulse else ""
        return _safe(
            f'<span class="badge badge-{status}{pulse_cls}">'
            f'<span class="badge-dot"></span>{label}'
            f"</span>"
        )


class PaginationHandler:
    def render(self, args: list[str], context: dict[str, object]) -> str:
        from djust.components.templatetags.djust_components import pagination_pages

        kw = _parse_args(args, context)
        # The Python tag's own page list, first: whatever it raises for these numbers
        # (``total_pages=None``: ``None + 1``) is raised here too (#3667), and the buttons
        # drawn below are the ones it draws (#3669).
        shown = pagination_pages(kw.get("page", 1), kw.get("total_pages", 1))
        try:
            page = int(cast("str | int | float", kw.get("page", 1)))
            total_pages = int(cast("str | int | float", kw.get("total_pages", 1)))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        prev_event = conditional_escape(kw.get("prev_event", "page_prev"))
        next_event = conditional_escape(kw.get("next_event", "page_next"))
        prev_disabled = " disabled" if page <= 1 else ""
        next_disabled = " disabled" if page >= total_pages else ""
        # One button per page number, wired like the Python tag's: ``next_event`` carrying the
        # page number as ``data-value`` (that is the event the Python template fires for a
        # number, and the native markup had no numbered buttons at all, #3669).
        numbered = ""
        for p in shown:
            if p == "...":
                numbered += '<span class="pagination-ellipsis" aria-hidden="true">&hellip;</span>'
                continue
            active = " active" if p == page else ""
            current = ' aria-current="page"' if p == page else ""
            number = conditional_escape(p)
            numbered += (
                f'<button class="pagination-btn{active}"{current} '
                f'dj-click="{next_event}" data-value="{number}">{number}</button>'
            )
        return _safe(
            f'<div class="pagination">'
            f'<button class="pagination-btn"{prev_disabled} dj-click="{prev_event}">&#8592;</button>'
            f"{numbered}"
            f'<span class="pagination-info">Page {page} of {total_pages}</span>'
            f'<button class="pagination-btn"{next_disabled} dj-click="{next_event}">&#8594;</button>'
            f"</div>"
        )


class AvatarHandler:
    def render(self, args: list[str], context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        src = kw.get("src", "")
        alt = conditional_escape(kw.get("alt", ""))
        initials = conditional_escape(kw.get("initials", "") or (alt[:2].upper() if alt else ""))
        size = conditional_escape(kw.get("size", "md"))
        status = conditional_escape(kw.get("status", ""))
        img_html = (
            f'<img class="avatar-image" src="{data_url_attr(src, image=True)}" alt="{alt}">'
            if src
            else f'<span class="avatar-initials">{initials}</span>'
        )
        status_html = (
            f'<span class="avatar-status avatar-status-{status}"></span>' if status else ""
        )
        return _safe(f'<div class="avatar avatar-{size}">{img_html}{status_html}</div>')


# Backward-compatible aliases — delegated to djust_components.utils
_format_cell = _format_cell_util


_interpolate_color_simple = interpolate_color_gradient


#: What the Python table loads to make a clickable row navigate: it reads ``data-href`` and does
#: the ``location.assign`` (and the Enter / Space keys, and the nested-control guard). The path is
#: ``table.html``'s, character for character (#3887).
_ROW_CLICK_SCRIPT = '<script src="/static/djust_components/data-table-row-click.js" defer></script>'


#: What makes a clickable row reachable by keyboard and announced as a control (``table.html``).
_ROW_NAV_A11Y = 'role="button" tabindex="0" style="cursor:pointer"'


def _row_text(row: dict[str, object], key: object) -> str:
    """``row[key]`` as text, the way the Python tag reads it (``table_cell``, #3870).

    A key the row lacks, and a key that cannot be one (a list, a dict: unhashable in Python), are
    ``""``. A value that is not text is written with ``str()``: ``None`` is ``"None"``, a number is
    not localised. The Python tag reads a row it is given; this one is handed the row after a JSON
    round trip, so a non-text key is looked up as the text its row key became (``5`` is ``"5"``).
    """
    if isinstance(key, (list, dict, set)):
        return ""
    value = row.get(key if isinstance(key, str) else str(key), "")
    return value if isinstance(value, str) else str(value)


def _row_href(row: dict[str, object], key: object) -> str:
    """``row[key]`` as a URL for ``href`` / ``data-href``: escaped, and a dangerous scheme is ``#``.

    The Python tag's ``table_href`` (``url_attr`` over ``table_cell``): the one URL rule every
    component uses, so a row's URL reaches the attribute through the same allowlist (#3887).
    """
    # ``honor_safe=False``: a marked-safe value (or one with ``__html__``) is checked and escaped
    # like any other. The engine hands a handler text, so this never differed in the engine; a
    # handler called directly with a ``SafeString`` row value must not depend on that (#3894).
    return str(url_attr(_row_text(row, key), honor_safe=False))


def _has_row_href(row: dict[str, object], key: object) -> bool:
    """Whether ``row[key]`` is a URL at all (#3895): not for a key the row lacks (or cannot
    have), ``None`` or blank text. The Python tag's ``table_has_href``: a link column draws plain
    text and a ``row_url`` row is not clickable, instead of ``href="None"`` / ``href=""``."""
    if isinstance(key, (list, dict, set)):
        return False
    value = row.get(key if isinstance(key, str) else str(key))
    return value is not None and str(value).strip() != ""


class DataTableHandler:
    def render(self, args: list[str], context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        rows = _passed_or_context(kw, "rows", context, default=[])
        columns = _passed_or_context(kw, "columns", context, default=[])
        sort_by = conditional_escape(str(kw.get("sort_by", "")))
        sort_desc = kw.get("sort_desc", False)
        sort_event = conditional_escape(kw.get("sort_event", "on_table_sort"))

        # Phase 1 parameters (all opt-in)
        selectable = kw.get("selectable", False)
        selected_rows = kw.get("selected_rows") or []
        select_event = conditional_escape(kw.get("select_event", "on_table_select"))
        row_key = str(kw.get("row_key", "id"))
        search = kw.get("search", False)
        search_query = conditional_escape(str(kw.get("search_query", "")))
        search_event = conditional_escape(kw.get("search_event", "on_table_search"))
        try:
            search_debounce = int(cast("str | int | float", kw.get("search_debounce", 300)))
        except (ValueError, TypeError):
            search_debounce = 300
        filters = kw.get("filters") or {}
        filter_event = conditional_escape(kw.get("filter_event", "on_table_filter"))
        loading = kw.get("loading", False)
        empty_title = conditional_escape(str(kw.get("empty_title", "No data")))
        empty_description = conditional_escape(str(kw.get("empty_description", "")))
        empty_icon = conditional_escape(str(kw.get("empty_icon", "")))
        paginate = kw.get("paginate", False)
        try:
            page = int(cast("str | int | float", kw.get("page", 1)))
            total_pages = int(cast("str | int | float", kw.get("total_pages", 1)))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        page_event = conditional_escape(kw.get("page_event", "on_table_page"))
        striped = kw.get("striped", False)
        compact = kw.get("compact", False)
        # Row navigation: a clickable row fires ``row_click_event`` (#1111). The Python
        # template wires it; the native table did not (#3669).
        # Decided on the value, not on its escaped text: ``row_click_event=None`` wires nothing
        # in the Python template, and "None" is truthy.
        _rce = kw.get("row_click_event", "")
        row_click_event = conditional_escape(_rce) if _rce else ""
        row_click_value_key = str(kw.get("row_click_value_key", "id"))
        # ``row_url`` names the row key that holds the row's URL (``data-href``, which the script
        # below navigates to). A row click wins over it, as in the Python template's ``elif``, and
        # a falsy ``row_url`` is not the key "None": decided on the value (#3887).
        _ru = kw.get("row_url", "")
        row_url = _ru if _ru else ""
        row_nav_script = _ROW_CLICK_SCRIPT if (row_click_event or row_url) else ""
        # Backward-compatible pager (``total_pages > 1`` without ``paginate``), as the Python
        # template draws it (#3669).
        prev_event = conditional_escape(kw.get("prev_event", "on_table_prev"))
        next_event = conditional_escape(kw.get("next_event", "on_table_next"))

        # Phase 2 parameters (all opt-in)
        editable_columns = kw.get("editable_columns") or []
        edit_event = conditional_escape(kw.get("edit_event", "on_table_cell_edit"))
        resizable = kw.get("resizable", False)
        reorderable = kw.get("reorderable", False)
        reorder_event = conditional_escape(kw.get("reorder_event", "on_table_reorder"))
        try:
            frozen_left = int(cast("str | int | float", kw.get("frozen_left", 0)))
        except (ValueError, TypeError):
            frozen_left = 0
        try:
            frozen_right = int(cast("str | int | float", kw.get("frozen_right", 0)))
        except (ValueError, TypeError):
            frozen_right = 0
        column_visibility = kw.get("column_visibility", False)
        visibility_event = conditional_escape(kw.get("visibility_event", "on_table_visibility"))
        density = conditional_escape(str(kw.get("density", "comfortable")))
        density_toggle = kw.get("density_toggle", False)
        density_event = conditional_escape(kw.get("density_event", "on_table_density"))
        responsive_cards = kw.get("responsive_cards", False)
        editable_rows = kw.get("editable_rows", False)
        edit_row_event = conditional_escape(kw.get("edit_row_event", "on_table_row_edit"))
        save_row_event = conditional_escape(kw.get("save_row_event", "on_table_row_save"))
        cancel_row_event = conditional_escape(kw.get("cancel_row_event", "on_table_row_cancel"))
        editing_rows = kw.get("editing_rows") or []

        # Phase 3 parameters (all opt-in)
        expandable = kw.get("expandable", False)
        expand_event = conditional_escape(kw.get("expand_event", "on_table_expand"))
        expanded_rows = kw.get("expanded_rows") or []
        bulk_actions = kw.get("bulk_actions") or []
        bulk_action_event = conditional_escape(kw.get("bulk_action_event", "on_table_bulk_action"))
        exportable = kw.get("exportable", False)
        export_event = conditional_escape(kw.get("export_event", "on_table_export"))
        export_formats = kw.get("export_formats") or ["csv", "json"]
        group_by = str(kw.get("group_by", ""))
        group_toggle_event = conditional_escape(
            kw.get("group_toggle_event", "on_table_group_toggle")
        )
        collapsible_groups = kw.get("collapsible_groups", True)
        collapsed_groups = kw.get("collapsed_groups") or []
        keyboard_nav = kw.get("keyboard_nav", False)
        virtual_scroll = kw.get("virtual_scroll", False)
        try:
            virtual_row_height = int(cast("str | int | float", kw.get("virtual_row_height", 40)))
        except (ValueError, TypeError):
            virtual_row_height = 40
        try:
            virtual_buffer = int(cast("str | int | float", kw.get("virtual_buffer", 5)))
        except (ValueError, TypeError):
            virtual_buffer = 5
        server_mode = kw.get("server_mode", False)
        facets = kw.get("facets", False)
        facet_counts = kw.get("facet_counts") or {}
        persist_key = conditional_escape(str(kw.get("persist_key", "")))
        printable = kw.get("printable", False)
        column_stats = kw.get("column_stats") or {}

        # Phase 4 parameters (all opt-in)
        footer_aggregations = kw.get("footer_aggregations") or {}
        row_class_map = kw.get("row_class_map") or {}
        column_groups = kw.get("column_groups") or []
        row_drag = kw.get("row_drag", False)
        row_drag_event = conditional_escape(kw.get("row_drag_event", "on_table_row_drag"))
        copyable = kw.get("copyable", False)
        copy_event = conditional_escape(kw.get("copy_event", "on_table_copy"))
        copy_format = conditional_escape(str(kw.get("copy_format", "csv")))

        # Phase 5 parameters (all opt-in)
        importable = kw.get("importable", False)
        import_event = conditional_escape(kw.get("import_event", "on_table_import"))
        import_formats = kw.get("import_formats") or ["csv", "json"]
        import_preview_data = kw.get("import_preview_data") or []
        import_errors = kw.get("import_errors") or []
        import_pending = kw.get("import_pending", False)
        computed_columns = kw.get("computed_columns") or []
        cell_merge_key = str(kw.get("cell_merge_key", "_merge"))
        column_expressions = kw.get("column_expressions") or {}
        expression_event = conditional_escape(kw.get("expression_event", "on_table_expression"))
        active_expressions = kw.get("active_expressions") or {}
        conditional_formatting = kw.get("conditional_formatting") or []

        if not isinstance(rows, (list, tuple)):
            rows = []
        if not isinstance(columns, (list, tuple)):
            columns = []
        if not isinstance(selected_rows, (list, tuple)):
            selected_rows = []
        if not isinstance(filters, dict):
            filters = {}
        if not isinstance(editable_columns, (list, tuple)):
            editable_columns = []
        if not isinstance(editing_rows, (list, tuple, set)):
            editing_rows = []
        if not isinstance(expanded_rows, (list, tuple, set)):
            expanded_rows = []
        if not isinstance(bulk_actions, (list, tuple)):
            bulk_actions = []
        if not isinstance(export_formats, (list, tuple)):
            export_formats = ["csv", "json"]
        if not isinstance(collapsed_groups, (list, tuple, set)):
            collapsed_groups = []
        if not isinstance(facet_counts, dict):
            facet_counts = {}
        if not isinstance(column_stats, dict):
            column_stats = {}
        if not isinstance(footer_aggregations, dict):
            footer_aggregations = {}
        if not isinstance(row_class_map, dict):
            row_class_map = {}
        if not isinstance(column_groups, (list, tuple)):
            column_groups = []
        if not isinstance(import_formats, (list, tuple)):
            import_formats = ["csv", "json"]
        if not isinstance(import_preview_data, (list, tuple)):
            import_preview_data = []
        if not isinstance(import_errors, (list, tuple)):
            import_errors = []
        if not isinstance(computed_columns, (list, tuple)):
            computed_columns = []
        if not isinstance(column_expressions, dict):
            column_expressions = {}
        if not isinstance(active_expressions, dict):
            active_expressions = {}
        if not isinstance(conditional_formatting, (list, tuple)):
            conditional_formatting = []

        # Convert to sets for fast lookup
        selected_set = {str(v) for v in selected_rows}
        editable_col_set = set(str(c) for c in editable_columns)
        editing_row_set = {str(v) for v in editing_rows}
        expanded_set = {str(v) for v in expanded_rows}
        collapsed_group_set = {str(v) for v in collapsed_groups}
        num_cols = len(columns)

        # --- Table classes ---
        table_classes = ["data-table"]
        if striped:
            table_classes.append("data-table-striped")
        if compact or density == "compact":
            table_classes.append("data-table-compact")
        if density == "spacious":
            table_classes.append("data-table-spacious")
        table_cls = " ".join(table_classes)

        # --- Wrapper attributes ---
        wrapper_classes = ["data-table-wrapper", "data-table-container"]
        if responsive_cards:
            wrapper_classes.append("data-table-responsive")
        if printable:
            wrapper_classes.append("data-table-printable")
        wrapper_attrs = []
        if resizable:
            wrapper_attrs.append('data-resizable="true"')
        if reorderable:
            wrapper_attrs.append('data-reorderable="true"')
            wrapper_attrs.append(f'data-reorder-event="{reorder_event}"')
        if editable_columns:
            wrapper_attrs.append(f'data-edit-event="{edit_event}"')
        if column_visibility:
            wrapper_attrs.append(f'data-visibility-event="{visibility_event}"')
        if keyboard_nav:
            wrapper_attrs.append('data-keyboard-nav="true" tabindex="0"')
        if virtual_scroll:
            wrapper_attrs.append('data-virtual-scroll="true"')
            wrapper_attrs.append(f'data-virtual-row-height="{virtual_row_height}"')
            wrapper_attrs.append(f'data-virtual-buffer="{virtual_buffer}"')
        if server_mode:
            wrapper_attrs.append('data-server-mode="true"')
        if persist_key:
            wrapper_attrs.append(f'data-persist-key="{persist_key}"')
        if row_drag:
            wrapper_attrs.append('data-row-drag="true"')
            wrapper_attrs.append(f'data-row-drag-event="{row_drag_event}"')
        if copyable:
            wrapper_attrs.append('data-copyable="true"')
            wrapper_attrs.append(f'data-copy-event="{copy_event}"')
            wrapper_attrs.append(f'data-copy-format="{copy_format}"')
        if importable:
            wrapper_attrs.append('data-importable="true"')
            wrapper_attrs.append(f'data-import-event="{import_event}"')
        if column_expressions:
            wrapper_attrs.append('data-column-expressions="true"')
            wrapper_attrs.append(f'data-expression-event="{expression_event}"')
        wrapper_attrs_str = (" " + " ".join(wrapper_attrs)) if wrapper_attrs else ""

        # --- Toolbar (column visibility + density toggle) ---
        toolbar_html = ""
        toolbar_parts = []

        if column_visibility:
            vis_items = ""
            for col in columns:
                if isinstance(col, dict):
                    ckey = conditional_escape(str(col.get("key", "")))
                    clabel = conditional_escape(str(col.get("label", ckey)))
                else:
                    ckey = clabel = conditional_escape(str(col))
                vis_items += (
                    f'<label class="data-table-visibility-item">'
                    f'<input type="checkbox" checked data-col-key="{ckey}"> {clabel}'
                    f"</label>"
                )
            toolbar_parts.append(
                f'<div class="data-table-visibility-dropdown">'
                f'<button type="button" class="data-table-visibility-btn">'
                f"&#9776; Columns</button>"
                f'<div class="data-table-visibility-menu">{vis_items}</div>'
                f"</div>"
            )

        if density_toggle:

            def _dbtn(val: str, label: str) -> str:
                active = " active" if val == density else ""
                return (
                    f'<button type="button" class="data-table-density-btn{active}"'
                    f' data-density="{val}"'
                    f' dj-click="{density_event}" data-value="{val}">{label}</button>'
                )

            toolbar_parts.append(
                f'<div class="data-table-density-toggle">'
                f"{_dbtn('compact', 'Compact')}"
                f"{_dbtn('comfortable', 'Comfortable')}"
                f"{_dbtn('spacious', 'Spacious')}"
                f"</div>"
            )

        if exportable:
            export_btns = ""
            for fmt in export_formats:
                fmt_esc = conditional_escape(str(fmt))
                label = fmt_esc.upper()
                export_btns += (
                    f'<button type="button" class="data-table-export-btn"'
                    f' dj-click="{export_event}" data-value="{fmt_esc}">'
                    f"Export {label}</button>"
                )
            toolbar_parts.append(f'<div class="data-table-export">{export_btns}</div>')

        if copyable:
            toolbar_parts.append(
                f'<div class="data-table-copy">'
                f'<button type="button" class="data-table-copy-btn"'
                f' dj-click="{copy_event}" data-value="selected">'
                f"Copy</button>"
                f"</div>"
            )

        if importable:
            import_btns = ""
            for ifmt in import_formats:
                ifmt_esc = conditional_escape(str(ifmt))
                label = ifmt_esc.upper()
                import_btns += (
                    f'<button type="button" class="data-table-import-btn"'
                    f' data-import-format="{ifmt_esc}">'
                    f"Import {label}</button>"
                )
            toolbar_parts.append(
                f'<div class="data-table-import">'
                f"{import_btns}"
                f'<input type="file" class="data-table-import-file" style="display:none"'
                f' accept=".csv,.json">'
                f"</div>"
            )

        # Bulk actions bar (rendered separately, shown conditionally)
        bulk_actions_html = ""
        if bulk_actions and selected_rows:
            ba_btns = ""
            for ba in bulk_actions:
                if isinstance(ba, dict):
                    ba_key = conditional_escape(str(ba.get("key", "")))
                    ba_label = conditional_escape(str(ba.get("label", ba_key)))
                else:
                    ba_key = ba_label = conditional_escape(str(ba))
                ba_btns += (
                    f'<button type="button" class="data-table-bulk-btn"'
                    f' dj-click="{bulk_action_event}" data-value="{ba_key}">'
                    f"{ba_label}</button>"
                )
            count = len(selected_rows)
            bulk_actions_html = (
                f'<div class="data-table-bulk-bar">'
                f'<span class="data-table-bulk-count">{count} selected</span>'
                f"{ba_btns}"
                f"</div>"
            )

        if toolbar_parts:
            toolbar_html = f'<div class="data-table-toolbar">{"".join(toolbar_parts)}</div>'

        # --- Search bar ---
        search_html = ""
        if search:
            search_html = (
                f'<div class="data-table-search">'
                f'<input type="text" role="searchbox" aria-label="Search table"'
                f' class="table-search" placeholder="Search..."'
                f' value="{search_query}"'
                f' dj-input="{search_event}" dj-debounce="{search_debounce}">'
                f"</div>"
            )

        # --- Loading state ---
        if loading:
            skeleton_rows = "".join(
                '<div class="skeleton skeleton-line" style="width:100%"></div>' for _ in range(5)
            )
            return _safe(
                f'<div class="{" ".join(wrapper_classes)}" role="grid"'
                f' aria-label="Data table" aria-busy="true"{wrapper_attrs_str}>'
                f"{toolbar_html}"
                f"{search_html}"
                f'<div class="data-table-loading skeleton-table">'
                f"{skeleton_rows}"
                f"</div>"
                f"{row_nav_script}"
                f"</div>"
            )

        # --- Header cells ---
        has_filters = any(isinstance(col, dict) and col.get("filterable", False) for col in columns)
        header_cells = []
        filter_cells = []
        for col_idx, col in enumerate(columns):
            if isinstance(col, dict):
                key = conditional_escape(str(col.get("key", "")))
                col_label = conditional_escape(str(col.get("label", key)))
                # ``None`` is "not given", as in the Python template (``default_if_none``): only
                # an explicit ``False`` (or any other falsy value) makes a column unsortable.
                sortable = col.get("sortable")
                if sortable is None:
                    sortable = True
                filterable = col.get("filterable", False)
                filter_type = col.get("filter_type", "text")
                filter_options = col.get("filter_options", [])
                width = col.get("width", "")
            else:
                key = col_label = conditional_escape(str(col))
                sortable = True
                filterable = False
                filter_type = "text"
                filter_options = []
                width = ""

            # Frozen / pinned column class
            frozen_cls = ""
            pinned = col.get("pinned", "") if isinstance(col, dict) else ""
            if pinned == "left":
                frozen_cls = " data-table-pinned-left"
            elif pinned == "right":
                frozen_cls = " data-table-pinned-right"
            elif frozen_left > 0 and col_idx < frozen_left:
                frozen_cls = " data-table-frozen-left"
            elif frozen_right > 0 and col_idx >= (num_cols - frozen_right):
                frozen_cls = " data-table-frozen-right"

            # Width style
            width = safe_css_length(width, "") if width else ""
            width_attr = f' style="width:{conditional_escape(width)}"' if width else ""

            # Resize / reorder attributes
            extra_attrs = ""
            if resizable:
                extra_attrs += ' data-resizable="true"'
            if reorderable:
                extra_attrs += f' draggable="true" data-col-key="{key}"'
            elif column_visibility:
                extra_attrs += f' data-col-key="{key}"'

            # Sort state
            if sortable:
                active = " active" if key == sort_by else ""
                if key == sort_by:
                    arrow = " &#8595;" if sort_desc else " &#8593;"
                    aria_sort = "descending" if sort_desc else "ascending"
                else:
                    arrow = ""
                    aria_sort = "none"
                header_cells.append(
                    f'<th class="sortable{active}{frozen_cls}" role="columnheader"'
                    f' aria-sort="{aria_sort}"'
                    f' dj-click="{sort_event}" data-value="{key}"{width_attr}{extra_attrs}>'
                    f"{col_label}{arrow}</th>"
                )
            else:
                header_cells.append(
                    f'<th class="{frozen_cls.strip()}" role="columnheader"{width_attr}{extra_attrs}>'
                    f"{col_label}</th>"
                )

            # Filter cell
            if has_filters:
                frozen_f = f' class="{frozen_cls.strip()}"' if frozen_cls else ""
                if filterable:
                    filter_val = conditional_escape(str(filters.get(key, "")))
                    if filter_type == "select":
                        opts_html = '<option value="">All</option>'
                        for opt in filter_options:
                            if isinstance(opt, dict):
                                opt_val = conditional_escape(str(opt.get("value", "")))
                                opt_label = conditional_escape(str(opt.get("label", opt_val)))
                            else:
                                opt_val = opt_label = conditional_escape(str(opt))
                            selected = " selected" if opt_val == filter_val else ""
                            opts_html += f'<option value="{opt_val}"{selected}>{opt_label}</option>'
                        filter_cells.append(
                            f'<th{frozen_f}><select class="data-table-filter"'
                            f' aria-label="Filter {col_label}"'
                            f' dj-input="{filter_event}" data-column="{key}">'
                            f"{opts_html}"
                            f"</select></th>"
                        )
                    else:
                        filter_cells.append(
                            f'<th{frozen_f}><input type="text" class="data-table-filter"'
                            f' aria-label="Filter {col_label}"'
                            f' placeholder="Filter..."'
                            f' value="{filter_val}"'
                            f' dj-input="{filter_event}" data-column="{key}">'
                            f"</th>"
                        )
                else:
                    filter_cells.append(f"<th{frozen_f}></th>")

        # Phase 5: Append computed column headers
        if computed_columns:
            for cc in computed_columns:
                if not isinstance(cc, dict):
                    continue
                cc_key = conditional_escape(str(cc.get("key", "")))
                cc_label = conditional_escape(str(cc.get("label", cc_key)))
                header_cells.append(
                    f'<th class="data-table-computed-header" role="columnheader"'
                    f' data-col-key="{cc_key}">{cc_label}</th>'
                )
                if has_filters:
                    filter_cells.append("<th></th>")

        # Phase 5: Expression filter row (separate from regular filters)
        expression_cells = []
        if column_expressions:
            for col in columns:
                if isinstance(col, dict):
                    key = col.get("key", "")
                else:
                    key = str(col)
                key_esc = conditional_escape(str(key))
                if key in column_expressions:
                    expr_val = conditional_escape(str(active_expressions.get(key, "")))
                    placeholder = conditional_escape(
                        str(column_expressions.get(key, "Expression..."))
                    )
                    expression_cells.append(
                        f'<th><input type="text" class="data-table-expression"'
                        f' aria-label="Expression filter {key_esc}"'
                        f' placeholder="{placeholder}"'
                        f' value="{expr_val}"'
                        f' data-column="{key_esc}"'
                        f' dj-input="{expression_event}"'
                        f' dj-debounce="500">'
                        f"</th>"
                    )
                else:
                    expression_cells.append("<th></th>")
            if computed_columns:
                for _ in computed_columns:
                    expression_cells.append("<th></th>")

        # Prepend expand column
        if expandable:
            header_cells.insert(0, '<th class="data-table-expand-col" role="columnheader"></th>')
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Prepend selection column
        if selectable:
            header_cells.insert(
                0,
                f'<th><input type="checkbox" class="data-table-select-all"'
                f' aria-label="Select all rows"'
                f' dj-click="{select_event}" data-value="__all__"></th>',
            )
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Prepend drag handle column
        if row_drag:
            header_cells.insert(0, '<th class="data-table-drag-col" role="columnheader"></th>')
            if has_filters:
                filter_cells.insert(0, "<th></th>")
            if expression_cells:
                expression_cells.insert(0, "<th></th>")

        # Append actions column header for editable rows
        if editable_rows:
            header_cells.append('<th role="columnheader">Actions</th>')
            if has_filters:
                filter_cells.append("<th></th>")
            if expression_cells:
                expression_cells.append("<th></th>")

        # --- Multi-level column group header row ---
        group_header_row = ""
        if column_groups:
            group_cells = []
            if expandable:
                group_cells.append('<th rowspan="2"></th>')
            if selectable:
                group_cells.append('<th rowspan="2"></th>')
            # Build a mapping of col_key -> group info
            grouped_keys = set()
            for grp in column_groups:
                if isinstance(grp, dict):
                    grp_cols = grp.get("columns", [])
                    grouped_keys.update(grp_cols)

            col_idx = 0
            while col_idx < len(columns):
                col = columns[col_idx]
                col_key = col.get("key", col) if isinstance(col, dict) else str(col)
                # Check if this col starts a group
                found_group = None
                for grp in column_groups:
                    if isinstance(grp, dict):
                        grp_cols = grp.get("columns", [])
                        if grp_cols and grp_cols[0] == col_key:
                            found_group = grp
                            break
                if found_group:
                    grp_label = conditional_escape(str(found_group.get("label", "")))
                    span = len(found_group.get("columns", []))
                    group_cells.append(
                        f'<th class="data-table-column-group" colspan="{span}">{grp_label}</th>'
                    )
                    col_idx += span
                elif col_key not in grouped_keys:
                    group_cells.append('<th rowspan="2"></th>')
                    col_idx += 1
                else:
                    col_idx += 1
            if editable_rows:
                group_cells.append('<th rowspan="2"></th>')
            if row_drag:
                group_cells.append('<th rowspan="2"></th>')
            group_header_row = (
                f'<tr class="data-table-group-header-row">{"".join(group_cells)}</tr>'
            )

        # --- Header rows ---
        thead_rows = ""
        if group_header_row:
            thead_rows += group_header_row
        thead_rows += f"<tr>{''.join(header_cells)}</tr>"
        if has_filters:
            thead_rows += f"<tr>{''.join(filter_cells)}</tr>"
        if expression_cells:
            thead_rows += f'<tr class="data-table-expression-row">{"".join(expression_cells)}</tr>'

        # --- Total columns (for colspan calculations) ---
        num_computed = (
            len([cc for cc in computed_columns if isinstance(cc, dict)]) if computed_columns else 0
        )
        total_cols = (
            num_cols
            + num_computed
            + (1 if selectable else 0)
            + (1 if editable_rows else 0)
            + (1 if expandable else 0)
            + (1 if row_drag else 0)
        )

        # --- Helper: render a single row ---
        def _render_row(row: object) -> str:
            if not isinstance(row, dict):
                return ""
            row_id = str(row.get(row_key, ""))
            is_selected = row_id in selected_set
            is_editing = row_id in editing_row_set
            is_expanded = row_id in expanded_set
            row_attrs = ""
            row_classes = []

            if is_editing:
                row_classes.append("data-table-row-editing")
            if is_expanded:
                row_classes.append("data-table-row-expanded")
            # Phase 4: conditional row styling via row_class_map
            if row_class_map:
                for rcm_col, rcm_map in row_class_map.items():
                    if isinstance(rcm_map, dict):
                        rcm_val = str(row.get(rcm_col, ""))
                        if rcm_val in rcm_map:
                            row_classes.append(conditional_escape(str(rcm_map[rcm_val])))
            row_attrs += f' data-row-key="{conditional_escape(row_id)}"'
            if row_click_event:
                row_classes.append("data-table-row-clickable")
                row_attrs += (
                    f' dj-click="{row_click_event}"'
                    f' data-value="{conditional_escape(str(row.get(row_click_value_key, "")))}"'
                    f" {_ROW_NAV_A11Y}"
                )
            elif row_url and _has_row_href(row, row_url):
                # The row's URL (#3887). ``data-href`` is read by ``data-table-row-click.js``.
                # A row with no URL under the key is not clickable (#3895).
                row_classes.append("data-table-row-clickable")
                row_attrs += f' data-href="{_row_href(row, row_url)}" {_ROW_NAV_A11Y}'

            cells = ""
            # Drag handle cell
            if row_drag:
                cells += (
                    '<td class="data-table-drag-handle">'
                    '<span class="data-table-grip" aria-label="Drag to reorder"'
                    ' draggable="true">&#9776;</span>'
                    "</td>"
                )

            # Expand toggle cell
            if expandable:
                exp_icon = "&#9660;" if is_expanded else "&#9654;"
                cells += (
                    f'<td class="data-table-expand-toggle">'
                    f'<button type="button" class="data-table-expand-btn"'
                    f' aria-label="Expand row" aria-expanded="{"true" if is_expanded else "false"}"'
                    f' dj-click="{expand_event}"'
                    f' data-value="{conditional_escape(row_id)}">{exp_icon}</button>'
                    f"</td>"
                )

            if selectable:
                checked = " checked" if is_selected else ""
                cells += (
                    f'<td><input type="checkbox" class="data-table-checkbox"'
                    f' aria-label="Select row"'
                    f"{checked}"
                    f' dj-click="{select_event}"'
                    f' data-value="{conditional_escape(row_id)}"></td>'
                )

            # Phase 5: build list of all renderable columns (base + computed)
            all_columns = list(columns)
            if computed_columns:
                all_columns = list(columns) + [
                    cc for cc in computed_columns if isinstance(cc, dict)
                ]

            # Phase 5: cell merge data for this row
            merge_data = row.get(cell_merge_key, {}) if isinstance(row, dict) else {}
            if not isinstance(merge_data, dict):
                merge_data = {}
            skip_cols = set()  # column indices to skip (merged into previous cell)

            # Pre-compute which columns are merged away
            for col_idx, col in enumerate(all_columns):
                col_k = col.get("key", col) if isinstance(col, dict) else col
                col_k_str = str(col_k)
                colspan = merge_data.get(col_k_str, 1)
                if isinstance(colspan, int) and colspan > 1:
                    for offset in range(1, colspan):
                        if col_idx + offset < len(all_columns):
                            skip_cols.add(col_idx + offset)

            for col_idx, col in enumerate(all_columns):
                if col_idx in skip_cols:
                    continue  # This cell is merged into a previous colspan
                col_k = col.get("key", col) if isinstance(col, dict) else col
                col_k_str = str(col_k)
                raw_val = row.get(col_k_str, "")
                # Phase 4: column type formatting
                col_type = col.get("type", "") if isinstance(col, dict) else ""
                if col_type and raw_val is not None and raw_val != "":
                    cell_val = conditional_escape(_format_cell(raw_val, col))
                else:
                    cell_val = conditional_escape(str(raw_val))
                col_label_for_card = ""
                if responsive_cards and isinstance(col, dict):
                    col_label_for_card = conditional_escape(str(col.get("label", col_k_str)))

                # Frozen / pinned class for td
                td_classes = []
                is_computed = col in computed_columns if computed_columns else False
                if is_computed:
                    td_classes.append("data-table-computed")
                if isinstance(col, dict) and col.get("pinned") == "left":
                    td_classes.append("data-table-pinned-left")
                elif isinstance(col, dict) and col.get("pinned") == "right":
                    td_classes.append("data-table-pinned-right")
                elif frozen_left > 0 and col_idx < frozen_left:
                    td_classes.append("data-table-frozen-left")
                elif frozen_right > 0 and col_idx >= (num_cols - frozen_right):
                    td_classes.append("data-table-frozen-right")
                # Phase 4: column type class
                if col_type:
                    td_classes.append(f"data-table-type-{conditional_escape(col_type)}")
                # Phase 4: cell_class from column config
                cell_class = col.get("cell_class", "") if isinstance(col, dict) else ""
                if cell_class:
                    # cell_class can be a string or a dict {value: class}
                    if isinstance(cell_class, dict):
                        cc_val = str(raw_val)
                        if cc_val in cell_class:
                            td_classes.append(conditional_escape(str(cell_class[cc_val])))
                    elif isinstance(cell_class, str):
                        td_classes.append(conditional_escape(cell_class))

                # Phase 5: conditional formatting
                cf_html = ""
                if conditional_formatting and raw_val is not None and raw_val != "":
                    for cf_preset in conditional_formatting:
                        if not isinstance(cf_preset, dict):
                            continue
                        if cf_preset.get("column") != col_k_str:
                            continue
                        try:
                            cf_num = float(raw_val)
                        except (ValueError, TypeError):
                            break
                        cf_type = cf_preset.get("type", "")
                        cf_min = float(cf_preset.get("min", 0))
                        cf_max = float(cf_preset.get("max", 100))
                        cf_span = cf_max - cf_min if cf_max != cf_min else 1
                        if cf_type == "data_bar":
                            cf_pct = max(0, min(100, ((cf_num - cf_min) / cf_span) * 100))
                            td_classes.append("data-table-cf-data-bar")
                            cf_html = (
                                f'<div class="data-table-data-bar"'
                                f' style="width:{cf_pct:.1f}%"></div>'
                            )
                        elif cf_type == "color_scale":
                            cf_ratio = max(0.0, min(1.0, (cf_num - cf_min) / cf_span))
                            cf_colors = cf_preset.get("colors", ["#ff0000", "#00ff00"])
                            cf_color = _interpolate_color_simple(cf_colors, cf_ratio)
                            td_classes.append("data-table-cf-color-scale")
                            cf_color = safe_css_color(cf_color, "")
                            cf_html = (
                                f' style="background-color:{conditional_escape(cf_color)}"'
                                if cf_color
                                else ""
                            )
                        elif cf_type == "icon_set":
                            cf_icons = cf_preset.get("icons", ["\u25bc", "\u25b6", "\u25b2"])
                            cf_thresholds = cf_preset.get("thresholds", [])
                            cf_icon = cf_icons[-1] if cf_icons else ""
                            for ci, ct in enumerate(cf_thresholds):
                                if cf_num < float(ct):
                                    cf_icon = cf_icons[ci] if ci < len(cf_icons) else cf_icons[-1]
                                    break
                            td_classes.append("data-table-cf-icon-set")
                            cf_html = f'<span class="data-table-cf-icon">{conditional_escape(cf_icon)}</span> '
                        break  # only first matching preset per column

                # Phase 5: cell merge colspan
                colspan_attr = ""
                colspan_val = merge_data.get(col_k_str, 1)
                if isinstance(colspan_val, int) and colspan_val > 1:
                    colspan_attr = f' colspan="{colspan_val}"'
                    td_classes.append("data-table-merged")

                td_cls_str = f' class="{" ".join(td_classes)}"' if td_classes else ""

                # Responsive card data-label
                label_attr = (
                    f' data-label="{col_label_for_card}"'
                    if responsive_cards and col_label_for_card
                    else ""
                )

                # Cell renderer
                cell_template = col.get("cell_template", "") if isinstance(col, dict) else ""
                if cell_template:
                    cell_tpl_esc = conditional_escape(str(cell_template))
                    cell_val = (
                        f'<span class="cell-renderer cell-renderer-{cell_tpl_esc}"'
                        f' data-value="{cell_val}">{cell_val}</span>'
                    )

                # Link column (#3887, #1110): ``col.link`` names the row key holding the href,
                # ``col.link_class`` is an optional class on the ``<a>``. Decided on the value:
                # a falsy ``link`` is plain text, as ``{% if col.link %}`` in the Python template.
                link_key = col.get("link") if isinstance(col, dict) else None
                # A row with no URL under ``link`` keeps the plain cell (#3895).
                if link_key and _has_row_href(row, link_key):
                    link_class = col.get("link_class") if isinstance(col, dict) else None
                    link_cls_attr = f' class="{escape(str(link_class))}"' if link_class else ""
                    cell_val = f'<a href="{_row_href(row, link_key)}"{link_cls_attr}>{cell_val}</a>'

                # Apply conditional formatting to cell content
                if cf_html and "data-table-cf-data-bar" in " ".join(td_classes):
                    cell_val = f"{cell_val}{cf_html}"
                elif cf_html and "data-table-cf-icon-set" in " ".join(td_classes):
                    cell_val = f"{cf_html}{cell_val}"
                # color_scale cf_html is a style attr, handled below

                # color_scale extra style
                extra_style = ""
                if cf_html and "data-table-cf-color-scale" in " ".join(td_classes):
                    extra_style = cf_html  # this is the style="..." attr

                # Editable cell (inline editing)
                is_col_editable = col_k_str in editable_col_set

                # Editable row mode: all cells become inputs when row is editing
                if editable_rows and is_editing:
                    raw_val = conditional_escape(str(row.get(col_k_str, "")))
                    cells += (
                        f"<td{td_cls_str}{label_attr}{colspan_attr}>"
                        f'<input type="text" value="{raw_val}"'
                        f' name="{conditional_escape(col_k_str)}"'
                        f' aria-label="Edit {conditional_escape(col_k_str)}">'
                        f"</td>"
                    )
                elif is_col_editable:
                    cells += (
                        f'<td data-editable="true"'
                        f' data-col-key="{conditional_escape(col_k_str)}"'
                        f"{td_cls_str}{label_attr}{colspan_attr}>"
                        f"{cell_val}</td>"
                    )
                else:
                    cells += (
                        f"<td{td_cls_str}{label_attr}{colspan_attr}{extra_style}>{cell_val}</td>"
                    )

            # Actions column for editable rows
            if editable_rows:
                if is_editing:
                    cells += (
                        f'<td class="data-table-row-actions">'
                        f'<button class="save-btn"'
                        f' dj-click="{save_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Save</button>'
                        f' <button class="cancel-btn"'
                        f' dj-click="{cancel_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Cancel</button>'
                        f"</td>"
                    )
                else:
                    cells += (
                        f'<td class="data-table-row-actions">'
                        f'<button dj-click="{edit_row_event}"'
                        f' data-value="{conditional_escape(row_id)}">Edit</button>'
                        f"</td>"
                    )

            # Row element
            result = ""
            if selectable:
                sel_attr = "true" if is_selected else "false"
                row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ""
                result += f'<tr aria-selected="{sel_attr}"{row_cls}{row_attrs}>{cells}</tr>'
            else:
                row_cls = f' class="{" ".join(row_classes)}"' if row_classes else ""
                result += f"<tr{row_cls}{row_attrs}>{cells}</tr>"

            # Expansion detail row
            if expandable and is_expanded:
                result += (
                    f'<tr class="data-table-detail-row">'
                    f'<td colspan="{total_cols}" class="data-table-detail-cell">'
                    f'<div class="data-table-detail-content"'
                    f' data-row-key="{conditional_escape(row_id)}"></div>'
                    f"</td></tr>"
                )

            return result

        # --- Body rows ---
        body_rows = []
        if rows:
            if group_by:
                # Group rows by column value
                groups: dict[object, list[object]] = {}
                group_order = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    val = str(row.get(group_by, ""))
                    if val not in groups:
                        groups[val] = []
                        group_order.append(val)
                    groups[val].append(row)

                for gval in group_order:
                    g_esc = conditional_escape(gval)
                    is_collapsed = gval in collapsed_group_set
                    toggle_attr = ""
                    if collapsible_groups:
                        toggle_attr = f' dj-click="{group_toggle_event}" data-value="{g_esc}"'
                    collapse_icon = "&#9654;" if is_collapsed else "&#9660;"
                    group_cls = "data-table-group-header"
                    if is_collapsed:
                        group_cls += " data-table-group-collapsed"
                    body_rows.append(
                        f'<tr class="{group_cls}">'
                        f'<td colspan="{total_cols}" class="data-table-group-cell">'
                        f'<button type="button" class="data-table-group-toggle"'
                        f"{toggle_attr}>{collapse_icon}</button>"
                        f' <span class="data-table-group-label">{g_esc}</span>'
                        f' <span class="data-table-group-count">({len(groups[gval])})</span>'
                        f"</td></tr>"
                    )
                    if not is_collapsed:
                        for row in groups[gval]:
                            body_rows.append(_render_row(row))
            else:
                for row in rows:
                    body_rows.append(_render_row(row))
            tbody_html = "".join(body_rows)
        else:
            # Empty state
            col_span = total_cols
            icon_html = (
                f'<div class="data-table-empty-icon">{empty_icon}</div>' if empty_icon else ""
            )
            desc_html = (
                f'<p class="data-table-empty-description">{empty_description}</p>'
                if empty_description
                else ""
            )
            tbody_html = (
                f'<tr><td colspan="{col_span}">'
                f'<div class="data-table-empty" role="status">'
                f"{icon_html}"
                f'<h3 class="data-table-empty-title">{empty_title}</h3>'
                f"{desc_html}"
                f"</div>"
                f"</td></tr>"
            )

        # --- Stats footer ---
        tfoot_html = ""
        has_stats = any(isinstance(col, dict) and col.get("stats", False) for col in columns)
        if has_stats and column_stats:
            stat_cells = []
            if expandable:
                stat_cells.append("<td></td>")
            if selectable:
                stat_cells.append("<td></td>")
            for col in columns:
                if isinstance(col, dict) and col.get("stats", False):
                    key = col.get("key", "")
                    s = column_stats.get(key, {})
                    if s and s.get("count", 0) > 0:
                        stat_cells.append(
                            f'<td class="data-table-stats-cell">'
                            f'<span class="data-table-stat" title="Min">{conditional_escape(s.get("min", ""))}</span>'
                            f'<span class="data-table-stat" title="Max">{conditional_escape(s.get("max", ""))}</span>'
                            f'<span class="data-table-stat" title="Avg">{conditional_escape(s.get("avg", ""))}</span>'
                            f"</td>"
                        )
                    else:
                        stat_cells.append('<td class="data-table-stats-cell">-</td>')
                else:
                    stat_cells.append("<td></td>")
            if editable_rows:
                stat_cells.append("<td></td>")
            tfoot_html = (
                f'<tfoot><tr class="data-table-stats-row">{"".join(stat_cells)}</tr></tfoot>'
            )

        # --- Phase 4: Footer aggregation row ---
        if footer_aggregations and rows:
            agg_cells = []
            if row_drag:
                agg_cells.append("<td></td>")
            if expandable:
                agg_cells.append("<td></td>")
            if selectable:
                agg_cells.append("<td></td>")
            for col in columns:
                col_k = col.get("key", col) if isinstance(col, dict) else str(col)
                col_k_str = str(col_k)
                agg_type = footer_aggregations.get(col_k_str, "")
                if agg_type:
                    # Compute aggregation
                    vals = []
                    for r in rows:
                        if isinstance(r, dict):
                            v = r.get(col_k_str)
                            if v is not None:
                                try:
                                    vals.append(float(v))
                                except (ValueError, TypeError):
                                    # Skip non-numeric cells during aggregation.
                                    continue
                    agg_val: object = ""
                    if vals:
                        if agg_type == "sum":
                            agg_val = sum(vals)
                        elif agg_type == "avg":
                            agg_val = round(sum(vals) / len(vals), 2)
                        elif agg_type == "count":
                            agg_val = len(vals)
                        elif agg_type == "min":
                            agg_val = min(vals)
                        elif agg_type == "max":
                            agg_val = max(vals)
                    agg_label = conditional_escape(str(agg_type).capitalize())
                    agg_cells.append(
                        f'<td class="data-table-footer-agg" data-agg-type="{conditional_escape(str(agg_type))}">'
                        f'<span class="data-table-agg-label">{agg_label}:</span> '
                        f'<span class="data-table-agg-value">{agg_val}</span>'
                        f"</td>"
                    )
                else:
                    agg_cells.append("<td></td>")
            if editable_rows:
                agg_cells.append("<td></td>")
            agg_row = f'<tr class="data-table-footer-row">{"".join(agg_cells)}</tr>'
            if tfoot_html:
                # Append inside existing tfoot
                tfoot_html = tfoot_html.replace("</tfoot>", f"{agg_row}</tfoot>")
            else:
                tfoot_html = f"<tfoot>{agg_row}</tfoot>"

        # --- Pagination ---
        pagination_html = ""
        if paginate and total_pages > 1:
            prev_disabled = " disabled" if page <= 1 else ""
            next_disabled = " disabled" if page >= total_pages else ""
            prev_page = max(1, page - 1)
            next_page = min(total_pages, page + 1)
            pagination_html = (
                f'<div class="data-table-pagination" role="navigation"'
                f' aria-label="Table pagination">'
                f'<button class="pagination-btn"{prev_disabled}'
                f' dj-click="{page_event}" data-value="{prev_page}">&#8592;</button>'
                f'<span class="pagination-info">Page {page} of {total_pages}</span>'
                f'<button class="pagination-btn"{next_disabled}'
                f' dj-click="{page_event}" data-value="{next_page}">&#8594;</button>'
                f"</div>"
            )
        elif total_pages > 1:
            prev_disabled = " disabled" if page <= 1 else ""
            next_disabled = " disabled" if page >= total_pages else ""
            pagination_html = (
                f'<div class="dj-table__pagination">'
                f'<button class="dj-table__page-btn"{prev_disabled}'
                f' dj-click="{prev_event}">&larr; Prev</button>'
                f'<span class="dj-table__page-info">{page} / {total_pages}</span>'
                f'<button class="dj-table__page-btn"{next_disabled}'
                f' dj-click="{next_event}">Next &rarr;</button>'
                f"</div>"
            )

        # --- Hidden triggers for JS events ---
        triggers_html = ""
        if reorderable:
            triggers_html += (
                f'<button class="data-table-reorder-trigger" style="display:none"'
                f' dj-click="{reorder_event}"></button>'
            )
        if editable_columns:
            triggers_html += (
                f'<button class="data-table-edit-trigger" style="display:none"'
                f' dj-click="{edit_event}"></button>'
            )
        if column_visibility:
            triggers_html += (
                f'<button class="data-table-visibility-trigger" style="display:none"'
                f' dj-click="{visibility_event}"></button>'
            )
        if row_drag:
            triggers_html += (
                f'<button class="data-table-drag-trigger" style="display:none"'
                f' dj-click="{row_drag_event}"></button>'
            )
        if copyable:
            triggers_html += (
                f'<button class="data-table-copy-trigger" style="display:none"'
                f' dj-click="{copy_event}"></button>'
            )
        if importable:
            triggers_html += (
                f'<button class="data-table-import-trigger" style="display:none"'
                f' dj-click="{import_event}"></button>'
            )
        if column_expressions:
            triggers_html += (
                f'<button class="data-table-expression-trigger" style="display:none"'
                f' dj-click="{expression_event}"></button>'
            )

        # --- Phase 5: Import preview / errors ---
        import_html = ""
        if importable:
            if import_errors:
                err_items = "".join(f"<li>{conditional_escape(str(e))}</li>" for e in import_errors)
                import_html += (
                    f'<div class="data-table-import-errors" role="alert"><ul>{err_items}</ul></div>'
                )
            if import_pending and import_preview_data:
                preview_count = len(import_preview_data)
                import_html += (
                    f'<div class="data-table-import-preview">'
                    f'<span class="data-table-import-preview-count">'
                    f"{preview_count} rows ready to import</span>"
                    f'<button type="button" class="data-table-import-confirm"'
                    f' dj-click="{import_event}"'
                    f" data-value='{{\"confirm\":true}}'>Confirm Import</button>"
                    f'<button type="button" class="data-table-import-cancel"'
                    f' dj-click="{import_event}"'
                    f" data-value='{{\"cancel\":true}}'>Cancel</button>"
                    f"</div>"
                )

        # --- Scrollable wrapper for frozen columns ---
        scroll_open = ""
        scroll_close = ""
        if frozen_left > 0 or frozen_right > 0:
            scroll_open = '<div class="data-table-scroll">'
            scroll_close = "</div>"

        # --- Facet counts display (appended to filter cells) ---
        # Facets are shown as counts next to filter options — handled via facet_counts data attr
        facet_attr = ""
        if facets and facet_counts:
            facet_attr = f" data-facet-counts='{conditional_escape(_json.dumps(facet_counts))}'"

        return _safe(
            f'<div class="{" ".join(wrapper_classes)}" role="grid"'
            f' aria-label="Data table"{wrapper_attrs_str}{facet_attr}>'
            f"{toolbar_html}"
            f"{bulk_actions_html}"
            f"{search_html}"
            f"{import_html}"
            f"{scroll_open}"
            f'<table class="{table_cls}">'
            f"<thead>{thead_rows}</thead>"
            f"<tbody>{tbody_html}</tbody>"
            f"{tfoot_html}"
            f"</table>"
            f"{scroll_close}"
            f"{pagination_html}"
            f"{triggers_html}"
            f"{row_nav_script}"
            f"</div>"
        )


class PageHeaderActionsHandler:
    """Block handler for {% page_header_actions %}...{% endpage_header_actions %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        # Just wrap actions content in its container div
        return _safe(f'<div class="dj-page-header__actions">{content}</div>')


class PageHeaderHandler:
    """Block handler for {% page_header title=... %}...{% endpage_header %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        title = kw.get("title", "")
        subtitle = kw.get("subtitle", "")
        description = kw.get("description", "")
        custom_class = kw.get("custom_class", "")

        e_title = conditional_escape(str(title))
        e_subtitle = conditional_escape(str(subtitle))
        e_description = conditional_escape(str(description))
        e_custom_class = conditional_escape(str(custom_class))

        cls = "dj-page-header"
        if e_custom_class:
            cls += f" {e_custom_class}"

        # In the Rust engine, nested block tags render inline.
        # page_header_actions renders its own wrapper div, so we need to
        # separate actions from breadcrumb content.
        actions_marker = '<div class="dj-page-header__actions">'
        actions_section = ""
        breadcrumb_content = content
        if actions_marker in content:
            idx = content.index(actions_marker)
            breadcrumb_content = content[:idx]
            actions_section = content[idx:]

        # Breadcrumb slot
        breadcrumb_html = ""
        if breadcrumb_content.strip():
            breadcrumb_html = f'<div class="dj-page-header__breadcrumb">{breadcrumb_content}</div>'

        # Title
        title_html = f'<h1 class="dj-page-header__title">{e_title}</h1>' if e_title else ""

        # Subtitle
        subtitle_html = ""
        if e_subtitle:
            subtitle_html = f'<p class="dj-page-header__subtitle">{e_subtitle}</p>'

        # Description
        description_html = ""
        if e_description:
            description_html = f'<p class="dj-page-header__description">{e_description}</p>'

        return _safe(
            f'<header class="{cls}">'
            f"{breadcrumb_html}"
            f'<div class="dj-page-header__row">'
            f'<div class="dj-page-header__text">'
            f"{title_html}"
            f"{subtitle_html}"
            f"{description_html}"
            f"</div>"
            f"{actions_section}"
            f"</div>"
            f"</header>"
        )


class SidebarHandler:
    """Block handler for {% sidebar %}...{% endsidebar %}

    Renders a collapsible sidebar navigation with menu items, sections,
    and mobile drawer support.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        sidebar_id = conditional_escape(kw.get("id", "sidebar"))
        collapsed = kw.get("collapsed", False)
        title = kw.get("title", "")
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_sidebar"))
        custom_class = conditional_escape(kw.get("class", ""))

        collapsed_cls = " dj-sidebar--collapsed" if collapsed else ""
        cls = f"dj-sidebar{collapsed_cls}"
        if custom_class:
            cls += f" {custom_class}"

        header_html = ""
        if title:
            header_html = (
                f'<div class="dj-sidebar__header">'
                f'<span class="dj-sidebar__title">{conditional_escape(title)}</span>'
                f'<button class="dj-sidebar__toggle" dj-click="{toggle_event}">'
                f"&#9776;</button></div>"
            )

        backdrop = f'<div class="dj-sidebar__backdrop" dj-click="{toggle_event}"></div>'

        return _safe(
            f'<nav class="{cls}" id="{sidebar_id}" role="navigation">'
            f"{header_html}"
            f'<ul class="dj-sidebar__menu">{content}</ul>'
            f"{backdrop}</nav>"
        )


class SidebarItemHandler:
    """Block handler for {% sidebar_item %}...{% endsidebar_item %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        href = conditional_escape(kw.get("href", "#"))
        icon = kw.get("icon", "")
        item_id = conditional_escape(kw.get("id", ""))
        event = kw.get("event", "")
        active = conditional_escape(kw.get("active", ""))

        is_active = (item_id and item_id == active) or (href and href == active)
        active_cls = " dj-sidebar__item--active" if is_active else ""

        icon_html = ""
        if icon:
            icon_html = f'<span class="dj-sidebar__icon">{conditional_escape(icon)}</span>'

        has_children = content.strip() != ""

        if event:
            trigger = (
                f'<button class="dj-sidebar__link{active_cls}" '
                f'dj-click="{conditional_escape(event)}">'
                f'{icon_html}<span class="dj-sidebar__label">{label}</span></button>'
            )
        else:
            trigger = (
                f'<a class="dj-sidebar__link{active_cls}" href="{data_url_attr(kw.get("href", "#"))}">'
                f'{icon_html}<span class="dj-sidebar__label">{label}</span></a>'
            )

        if has_children:
            return _safe(
                f'<li class="dj-sidebar__item dj-sidebar__item--parent">'
                f"{trigger}"
                f'<ul class="dj-sidebar__submenu">{content}</ul></li>'
            )

        return _safe(f'<li class="dj-sidebar__item">{trigger}</li>')


class SidebarSectionHandler:
    """Inline handler for {% sidebar_section label="..." %}"""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        return _safe(
            f'<li class="dj-sidebar__section">'
            f'<span class="dj-sidebar__section-label">{label}</span></li>'
        )


class NavMenuHandler:
    """Block handler for {% nav_menu %}...{% endnav_menu %}

    Renders a top horizontal navigation bar with brand, hamburger toggle,
    and responsive mobile collapse.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        nav_id = conditional_escape(kw.get("id", "nav-menu"))
        brand = kw.get("brand", "")
        brand_href = data_url_attr(kw.get("brand_href", "/"))
        toggle_event = conditional_escape(kw.get("toggle_event", "toggle_nav"))
        mobile_open = kw.get("mobile_open", False)
        custom_class = conditional_escape(kw.get("class", ""))

        cls = "dj-nav"
        if custom_class:
            cls += f" {custom_class}"

        mobile_cls = " dj-nav__list--open" if mobile_open else ""

        brand_html = ""
        if brand:
            brand_html = (
                f'<a class="dj-nav__brand" href="{brand_href}">{conditional_escape(brand)}</a>'
            )

        hamburger = (
            f'<button class="dj-nav__hamburger" dj-click="{toggle_event}" '
            f'aria-label="Toggle navigation">&#9776;</button>'
        )

        # The menu's ``active`` reaches the items that left a marker (#3873 follow-up). Each
        # marker sits right after its item's ``<li ...`` opening (``">`` then the comment); it is
        # replaced with that ``">``, the active class first for a match. Only literals and the
        # escaped hex key are written, so ``content`` stays the engine-rendered body.
        for key in _nav_active_keys(content):
            e_key = conditional_escape(key)
            hit = _nav_key_matches(key, kw.get("active", ""))
            content = content.replace(
                f'"><!--djust-nav-key:{e_key}-->',
                ' dj-nav__item--active">' if hit else '">',
            )

        return _safe(
            f'<nav class="{cls}" id="{nav_id}" role="navigation">'
            f'<div class="dj-nav__container">'
            f"{brand_html}{hamburger}"
            f'<ul class="dj-nav__list{mobile_cls}">{content}</ul>'
            f"</div></nav>"
        )


#: What ``NavItemHandler`` opens every item with, which is how a parent finds its child items.
_NAV_ITEM_MARKUP = '<li class="dj-nav__item'

#: How an item with no ``active=`` of its own asks the ``{% nav_menu %}`` that holds it whether
#: the menu's ``active`` names it. The item is rendered before the menu, so it leaves its ``id``
#: and ``href`` (hex of a JSON pair: only ``0-9a-f``, so no value can end the comment, open
#: another, or read as markup) in a comment right after its ``<li ...>``; the menu reads and
#: removes every one, adding the active class to the matches. Outside a menu the comment is
#: invisible. The Django node is handed the menu's ``active`` directly.
_NAV_ACTIVE_MARK = _re_filters.compile(
    r'<li class="dj-nav__item[^"]*"><!--djust-nav-key:([0-9a-f]*)-->'
)


def _nav_active_key(item_id: object, href: object) -> str:
    """The marker's payload: the hex of the ``[id, href]`` JSON pair."""
    payload = _json.dumps([item_id, href], default=str, separators=(",", ":"))
    return payload.encode("utf-8").hex()


def _nav_active_keys(content: str) -> list[str]:
    """The distinct marker keys of the items in ``content``, in order."""
    return list(dict.fromkeys(_NAV_ACTIVE_MARK.findall(content)))


def _nav_key_matches(key: str, active: object) -> bool:
    """The Django rule: a non-empty ``id`` or ``href`` equal to the menu's ``active``."""
    try:
        item_id, href = _json.loads(bytes.fromhex(key).decode("utf-8"))
    except (ValueError, TypeError):
        return False
    return bool(active) and (bool(item_id and item_id == active) or bool(href and href == active))


class NavItemHandler:
    """Block handler for {% nav_item %}...{% endnav_item %}

    Renders a navigation item. If content is present, it becomes a dropdown parent.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        label = conditional_escape(kw.get("label", ""))
        href = conditional_escape(kw.get("href", "#"))
        event = kw.get("event", "")
        item_id = conditional_escape(kw.get("id", ""))
        active = conditional_escape(kw.get("active", ""))
        mega = kw.get("mega", False)
        description = kw.get("description", "")

        is_active = (item_id and item_id == active) or (href and href == active)
        active_cls = " dj-nav__item--active" if is_active else ""
        # No ``active=`` of its own: the enclosing menu's ``active`` decides (#3873 follow-up).
        # Written as a literal around the escaped hex key, which only holds ``0-9a-f``.
        mark = ""
        if "active" not in kw:
            mark = (
                "<!--djust-nav-key:"
                f"{conditional_escape(_nav_active_key(kw.get('id', ''), kw.get('href', '#')))}-->"
            )

        # A dropdown parent only when the body holds nav ITEMS (this handler's own ``<li>``).
        # The Python tag counts child ``{% nav_item %}`` nodes and ignores any other body, so
        # an item with an ``event`` and a text body stays a clickable button there; "any
        # non-blank body" dropped that ``dj-click`` (#3669).
        # A block handler is handed rendered HTML, so a child is recognised by the markup this
        # handler draws (a literal ``<li class="dj-nav__item">`` in the body matches too). A
        # sub-item at any depth, or inside ``{% if %}`` / ``{% for %}``, counts; the Django node
        # renders its body the same way since #3873 / #3878.
        has_children = _NAV_ITEM_MARKUP in content

        if has_children:
            mega_cls = " dj-nav__dropdown--mega" if mega else ""
            return _safe(
                f'<li class="dj-nav__item dj-nav__item--has-dropdown{active_cls}">{mark}'
                f'<button class="dj-nav__link">{label}'
                f'<span class="dj-nav__caret">&#9662;</span></button>'
                f'<div class="dj-nav__dropdown{mega_cls}">'
                f'<ul class="dj-nav__dropdown-list">{content}</ul></div></li>'
            )

        desc_html = ""
        if description:
            desc_html = (
                f'<span class="dj-nav__dropdown-desc">{conditional_escape(description)}</span>'
            )

        if event:
            return _safe(
                f'<li class="dj-nav__item{active_cls}">{mark}'
                f'<button class="dj-nav__link" dj-click="{conditional_escape(event)}">'
                f"{label}{desc_html}</button></li>"
            )

        return _safe(
            f'<li class="dj-nav__item{active_cls}">{mark}'
            f'<a class="dj-nav__link" href="{data_url_attr(kw.get("href", "#"))}">'
            f"{label}{desc_html}</a></li>"
        )


class _AppShellSlotParser(HTMLParser):
    """Locate a direct sidebar without reserializing the rendered HTML."""

    _VOID_TAGS = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )

    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_offsets = [0]
        for line in content.split("\n"):
            self._line_offsets.append(self._line_offsets[-1] + len(line) + 1)
        self._stack: list[str] = []
        self.start: int | None = None
        self.end: int | None = None

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VOID_TAGS:
            return
        if (
            not self._stack
            and self.start is None
            and tag == "aside"
            and dict(attrs).get("class") == "dj-app-shell__sidebar"
        ):
            self.start = self._offset()
        self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        pass

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._stack:
            return
        # Parent closing tags also close children with optional end tags (e.g. <p>).
        index = len(self._stack) - 1 - self._stack[::-1].index(tag)
        del self._stack[index:]
        if not self._stack and tag == "aside" and self.start is not None and self.end is None:
            self.end = self._offset() + len("</aside>")


def _split_app_shell_sidebar(content: str) -> tuple[str, str]:
    """Extract only a direct sidebar slot; preserve nested or unbalanced HTML."""
    parser = _AppShellSlotParser(content)
    parser.feed(content)
    if parser.start is None or parser.end is None:
        return "", content
    return content[parser.start : parser.end], content[: parser.start] + content[parser.end :]


_RootLayoutParts = list[tuple[tuple[int, int] | None, str]]


def _layout_body_part(
    content: str, start: int = 0, end: int | None = None
) -> tuple[tuple[int, int], str]:
    if end is None:
        end = len(content)
    return (len(content[:start].encode()), len(content[:end].encode())), ""


def _root_layout_html(content: str, parts: _RootLayoutParts) -> str:
    body = content.encode()
    return _safe(
        "".join(
            body[span[0] : span[1]].decode() if span is not None else text for span, text in parts
        )
    )


class AppShellHandler:
    """Responsive layout, retaining template order and direct sidebar placement."""

    def _render_root_parts(
        self, args: list[str], content: str, context: dict[str, object]
    ) -> _RootLayoutParts:
        kw = _parse_args(args, context)
        # The Python tag's markup (``AppShellNode``): an attribute value is escaped whatever it
        # is marked as (#4009), and a falsy ``class`` adds no class (``class=None`` added "None").
        shell_id = attr_text(kw.get("id", "app-shell"))
        cls = "dj-app-shell"
        if kw.get("sidebar_collapsed", False):
            cls += " dj-app-shell--sidebar-collapsed"
        custom_class = kw.get("class", "")
        if custom_class:
            cls += f" {attr_text(custom_class)}"
        parser = _AppShellSlotParser(content)
        parser.feed(content)
        parts: _RootLayoutParts = [(None, f'<div class="{cls}" id="{shell_id}">')]
        ranges = [(0, len(content))]
        if parser.start is not None and parser.end is not None:
            parts.append(_layout_body_part(content, parser.start, parser.end))
            ranges = [(0, parser.start), (parser.end, len(content))]
        parts.append((None, '<div class="dj-app-shell__main">'))
        rest = "".join(content[a:b] for a, b in ranges)
        left, right = len(rest) - len(rest.lstrip()), len(rest.rstrip())
        cursor = 0
        for start, end in ranges:
            a, b = max(left, cursor), min(right, cursor + end - start)
            if a < b:
                parts.append(_layout_body_part(content, start + a - cursor, start + b - cursor))
            cursor += end - start
        parts.append((None, "</div></div>"))
        return parts

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return _root_layout_html(content, self._render_root_parts(args, content, context))


class AppSidebarHandler:
    """Block handler for the layout sidebar."""

    def _render_root_parts(
        self, args: list[str], content: str, context: dict[str, object]
    ) -> _RootLayoutParts:
        return [
            (None, '<aside class="dj-app-shell__sidebar">'),
            _layout_body_part(content),
            (None, "</aside>"),
        ]

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return _root_layout_html(content, self._render_root_parts(args, content, context))


class AppHeaderHandler:
    """Block handler for the layout header."""

    def _render_root_parts(
        self, args: list[str], content: str, context: dict[str, object]
    ) -> _RootLayoutParts:
        return [
            (None, '<header class="dj-app-shell__header">'),
            _layout_body_part(content),
            (None, "</header>"),
        ]

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return _root_layout_html(content, self._render_root_parts(args, content, context))


class AppContentHandler:
    """Block handler for the layout content."""

    def _render_root_parts(
        self, args: list[str], content: str, context: dict[str, object]
    ) -> _RootLayoutParts:
        return [
            (None, '<main class="dj-app-shell__content">'),
            _layout_body_part(content),
            (None, "</main>"),
        ]

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return _root_layout_html(content, self._render_root_parts(args, content, context))


class ToolbarHandler:
    """Block handler for {% toolbar %}...{% endtoolbar %}

    Horizontal action bar with grouped buttons, separators, overflow menu.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        toolbar_id = conditional_escape(kw.get("id", "toolbar"))
        custom_class = conditional_escape(kw.get("class", ""))
        size = conditional_escape(kw.get("size", "md"))
        variant = conditional_escape(kw.get("variant", "default"))

        cls = f"dj-toolbar dj-toolbar--{size} dj-toolbar--{variant}"
        if custom_class:
            cls += f" {custom_class}"

        return _safe(f'<div class="{cls}" id="{toolbar_id}" role="toolbar">{content}</div>')


class ToolbarSeparatorHandler:
    """Inline handler for {% toolbar_separator %}"""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return _safe('<div class="dj-toolbar__separator" role="separator"></div>')


class ToolbarOverflowHandler:
    """Block handler for {% toolbar_overflow %}...{% endtoolbar_overflow %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return _safe(
            f'<div class="dj-toolbar__overflow">'
            f'<button class="dj-toolbar__overflow-trigger" aria-label="More actions" '
            f'aria-expanded="false" aria-haspopup="true">'
            f'<span class="dj-toolbar__overflow-icon">&#8942;</span></button>'
            f'<div class="dj-toolbar__overflow-menu">{content}</div></div>'
        )


#: How a ``{% filter_select %}`` / ``{% filter_date_range %}`` / ``{% filter_search %}`` tells the
#: ``{% filter_bar %}`` that holds it what it was given (#3669). The Python tags render NOTHING of
#: their own: the bar reads its children's arguments and draws every control, wiring its own
#: ``event`` and ``clear_event`` onto them (a child's ``event=`` is never read). A native block
#: handler is handed its children already rendered, so a child leaves its arguments in a comment
#: the bar reads and removes; a child reached outside a bar leaves an invisible comment, where
#: the Python tag leaves nothing.
_FILTER_MARK = _re_filters.compile(
    r"<!--djust-filter:(select|date_range|search):([A-Za-z0-9_=-]*)-->"
)


class _FilterControlHandler:
    KIND = ""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        import base64

        payload = _json.dumps(_parse_args(args, context), default=str, separators=(",", ":"))
        token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
        kind = conditional_escape(self.KIND)
        return _safe(f"<!--djust-filter:{kind}:{conditional_escape(token)}-->")


class FilterSelectHandler(_FilterControlHandler):
    """Inline handler for {% filter_select name="status" options=statuses %}"""

    KIND = "select"


class FilterDateRangeHandler(_FilterControlHandler):
    """Inline handler for {% filter_date_range name="dates" %}"""

    KIND = "date_range"


class FilterSearchHandler(_FilterControlHandler):
    """Inline handler for {% filter_search name="q" %}"""

    KIND = "search"


class FilterBarHandler:
    """Block handler for {% filter_bar %}...{% endfilter_bar %}

    Draws the bar with the Python tag's own renderer (``render_filter_bar``) from the controls
    its children left, so ``event``, ``clear_event`` and the "Clear filters" button are decided
    in one place (#3669). Anything else in the bar is kept after the controls, as before.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        import base64

        from djust.components.templatetags.djust_components import render_filter_bar

        kw = _parse_args(args, context)
        controls: list[tuple[str, dict[str, Any]]] = []

        def take(match: Any) -> str:
            try:
                data = _json.loads(base64.urlsafe_b64decode(match.group(2).encode("ascii")))
            except ValueError:
                return ""
            if isinstance(data, dict):
                controls.append((match.group(1), data))
            return ""

        rest = _FILTER_MARK.sub(take, content)
        # The id keeps the constant this handler always drew: the Django tag draws a random one,
        # which would change on every socket render.
        return str(
            render_filter_bar(
                kw, controls, default_id="filter-bar", extra_html=rest if rest.strip() else ""
            )
        )


class FormArrayHandler:
    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        name = kw.get("name", "items")
        rows = kw.get("rows", [{"value": ""}])
        min_rows = kw.get("min", 1)
        max_rows = kw.get("max", 10)
        add_event = kw.get("add_event", "add_row")
        remove_event = kw.get("remove_event", "remove_row")
        add_label = kw.get("add_label", "Add row")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_add_event = conditional_escape(str(add_event))
        e_remove_event = conditional_escape(str(remove_event))
        e_add_label = conditional_escape(str(add_label))
        e_class = conditional_escape(str(custom_class))

        try:
            min_rows = int(cast("str | int | float", min_rows))
        except (ValueError, TypeError):
            min_rows = 1
        try:
            max_rows = int(cast("str | int | float", max_rows))
        except (ValueError, TypeError):
            max_rows = 10

        if not isinstance(rows, list):
            rows = [{"value": ""}]

        classes = ["dj-form-array"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        row_count = len(rows)
        can_add = row_count < max_rows
        can_remove = row_count > min_rows

        rows_html = []
        for i, row in enumerate(rows):
            val = conditional_escape(str(row.get("value", "") if isinstance(row, dict) else row))
            remove_html = ""
            if can_remove:
                remove_html = (
                    f'<button class="dj-form-array__remove" type="button" '
                    f'dj-click="{e_remove_event}" data-value="{i}" '
                    f'aria-label="Remove row {i + 1}">&times;</button>'
                )
            rows_html.append(
                f'<div class="dj-form-array__row" data-index="{i}">'
                f'<input type="text" name="{e_name}[{i}]" value="{val}" '
                f'class="dj-form-array__input">{remove_html}</div>'
            )

        add_disabled = "" if can_add else " disabled"
        add_html = (
            f'<button class="dj-form-array__add" type="button" '
            f'dj-click="{e_add_event}"{add_disabled}>{e_add_label}</button>'
        )

        return _safe(
            f'<div class="{class_str}" data-min="{min_rows}" data-max="{max_rows}">'
            f'<div class="dj-form-array__rows">{"".join(rows_html)}</div>{add_html}</div>'
        )


#: The native twins that remain (see the module docstring), restored as they were on ``main``.
#: Inline: (tag, handler); block: (tag, end tag, handler).
NATIVE_INLINE_HANDLERS = [
    ("badge", BadgeHandler()),
    ("avatar", AvatarHandler()),
    ("progress", ProgressHandler()),
    ("pagination", PaginationHandler()),
    ("data_table", DataTableHandler()),
    ("sidebar_section", SidebarSectionHandler()),
    ("toolbar_separator", ToolbarSeparatorHandler()),
    ("filter_select", FilterSelectHandler()),
    ("filter_date_range", FilterDateRangeHandler()),
    ("filter_search", FilterSearchHandler()),
]
NATIVE_BLOCK_HANDLERS = [
    ("app_shell", "endapp_shell", AppShellHandler()),
    ("app_sidebar", "endapp_sidebar", AppSidebarHandler()),
    ("app_header", "endapp_header", AppHeaderHandler()),
    ("app_content", "endapp_content", AppContentHandler()),
    ("accordion", "endaccordion", AccordionHandler()),
    ("accordion_item", "endaccordion_item", AccordionItemHandler()),
    ("sidebar", "endsidebar", SidebarHandler()),
    ("sidebar_item", "endsidebar_item", SidebarItemHandler()),
    ("nav_menu", "endnav_menu", NavMenuHandler()),
    ("nav_item", "endnav_item", NavItemHandler()),
    ("toolbar", "endtoolbar", ToolbarHandler()),
    ("toolbar_overflow", "endtoolbar_overflow", ToolbarOverflowHandler()),
    ("filter_bar", "endfilter_bar", FilterBarHandler()),
    ("form_array", "endform_array", FormArrayHandler()),
    ("page_header", "endpage_header", PageHeaderHandler()),
    ("page_header_actions", "endpage_header_actions", PageHeaderActionsHandler()),
]

#: Block tags that take the raw-body route (their Django node reads its children, so the Rust engine
#: cannot render the body first): ``tabs`` keeps only its ``tab`` children, ``split_pane`` parses two
#: segments. Both were already registered this way before 2.0. The other children-reading parents
#: keep their native twins (see the module docstring). Pinned by
#: ``test_raw_body_tags_are_exactly_the_bridged_tags_that_read_their_children``.
RAW_BODY_TAGS = frozenset({"split_pane", "tabs"})


def _checked_raw_handler(name: str, compile_func: Any, library: Any) -> Any:
    """``LibraryRawBlockTagHandler`` whose arguments are checked when the template compiles.

    A raw-body handler compiles the tag only when it renders (it needs the body), so a bad
    argument (``{% accordion val %}``, which Django refuses with "Use key=value format", #3676)
    would surface at render, or never in a branch that is not taken. ``validate_at_parse`` runs
    the tag's own compile function on its tokens and stops where it asks for the body, as
    ``LibraryBlockTagHandler.validate_at_parse`` does, so the Rust engine refuses exactly what
    Django refuses, when Django refuses it.
    """
    from djust.template_libraries import LibraryRawBlockTagHandler, _parser, _stamp, _token

    class _Checked(LibraryRawBlockTagHandler):
        def validate_at_parse(self, args: list[str]) -> None:
            class BodyReached(Exception):
                pass

            def stop_at_body(*_args: Any, **_kwargs: Any) -> Any:
                raise BodyReached

            parser = _parser([])
            parser.parse = stop_at_body  # type: ignore[method-assign]
            try:
                self.compile_func(parser, _token(self.name, args))
            except BodyReached:
                pass
            except BaseException as exc:
                _stamp(exc)
                raise

    return _Checked("djust_components", name, compile_func, libraries=(library,))


def bridged_handler(name: str, compile_func: Any, library: Any) -> tuple[str, Any]:
    """``(kind, handler)``: the ``{% load %}`` bridge handler for one ``djust_components`` tag.

    ``kind`` is ``"tag"``, ``"block"`` or ``"raw"``, the registry the handler goes in.
    """
    from djust.template_libraries import (
        LibraryBlockTagHandler,
        LibraryTagHandler,
        _classify,
        _consumes_body,
    )

    if name in RAW_BODY_TAGS:
        handler: Any = _checked_raw_handler(name, compile_func, library)
        kind = "raw"
    elif _classify(compile_func) == "simple_block_tag" or (
        _classify(compile_func) == "raw" and _consumes_body(compile_func)
    ):
        handler = LibraryBlockTagHandler("djust_components", name, compile_func)
        kind = "block"
    else:
        handler = LibraryTagHandler("djust_components", name, compile_func)
        kind = "tag"
    # The tags of one render share one Django ``RenderContext``, as the nodes of one Django
    # ``Template.render`` do (#3044): an ``inclusion_tag`` then loads its template once per
    # render, not once per use.
    handler.shares_render_context = True
    return kind, handler


def register_with_rust_engine() -> None:
    """Register every component tag with the Rust template engine.

    Called from ``DjustComponentsConfig.ready()``. Safe to call more than once: each call
    registers the same handlers again (the bridge handlers are built once per process).
    """
    from djust._rust import (  # type: ignore[import]
        register_block_tag_handler,
        register_raw_block_tag_handler,
        register_tag_handler,
        unregister_block_tag_handler,
        unregister_raw_block_tag_handler,
        unregister_tag_handler,
    )
    from djust._positional_args import with_argument_contract

    from .templatetags.djust_components import register as library

    native = {name for name, _handler in NATIVE_INLINE_HANDLERS} | {
        name for name, _end, _handler in NATIVE_BLOCK_HANDLERS
    }
    handlers = _bridged_handlers(library)
    for name, (kind, handler) in handlers.items():
        if name in native:
            continue
        # A name moves between registries only if its kind changes; clear the other two so a
        # stale registration can never shadow this one.
        if kind != "tag":
            unregister_tag_handler(name)
        if kind != "block":
            unregister_block_tag_handler(name)
        if kind != "raw":
            unregister_raw_block_tag_handler(name)
        if kind == "tag":
            register_tag_handler(name, handler)
        elif kind == "block":
            register_block_tag_handler(name, handler.end_name, handler)
        else:
            register_raw_block_tag_handler(name, handler.end_name, handler)

    # The native twins, registered as on ``main``. Arguments (#3664, #3676): a native handler
    # reads ``key=value`` strings, so each one is wrapped to take exactly the arguments its Python
    # tag takes, and handed its operands as values (#3768, #3871). The engine's core-layout check
    # peels exactly the two block wrappers.
    for tag_name, handler in NATIVE_INLINE_HANDLERS:
        unregister_block_tag_handler(tag_name)
        unregister_raw_block_tag_handler(tag_name)
        register_tag_handler(
            tag_name,
            with_argument_contract(tag_name, _with_typed_operands(handler), library),
        )
    for tag_name, end_tag, handler in NATIVE_BLOCK_HANDLERS:
        unregister_tag_handler(tag_name)
        unregister_raw_block_tag_handler(tag_name)
        register_block_tag_handler(
            tag_name,
            end_tag,
            with_argument_contract(tag_name, _with_typed_block_operands(handler), library),
        )

    # Component system (v0.5.0): {% call %}, {% component %}, {% slot %}, {% render_slot %}.
    # These are not tags of the ``djust_components`` library and have no Python twin.
    from .function_component import (
        CallTagHandler,
        RenderSlotTagHandler,
        SlotTagHandler,
    )

    _call_handler = CallTagHandler()
    register_block_tag_handler("call", "endcall", _call_handler)
    register_block_tag_handler("component", "endcomponent", _call_handler)
    register_block_tag_handler("slot", "endslot", SlotTagHandler())
    register_tag_handler("render_slot", RenderSlotTagHandler())

    # Async rendering (v0.5.0): {% dj_suspense await="..." fallback="..." %}. See
    # djust/components/suspense.py.
    from .suspense import SuspenseTagHandler

    register_block_tag_handler("dj_suspense", "enddj_suspense", SuspenseTagHandler())


#: ``library`` -> {tag name: (kind, handler)}. Built once per library object, so a re-registration
#: hands the engine the SAME handler objects and their per-argument node caches survive.
_BRIDGED: dict[int, tuple[Any, dict[str, tuple[str, Any]]]] = {}


def _bridged_handlers(library: Any) -> dict[str, tuple[str, Any]]:
    cached = _BRIDGED.get(id(library))
    if cached is not None and cached[0] is library and set(cached[1]) == set(library.tags):
        return cached[1]
    handlers = {
        name: bridged_handler(name, compile_func, library)
        for name, compile_func in library.tags.items()
    }
    _BRIDGED[id(library)] = (library, handlers)
    return handlers
