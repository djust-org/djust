"""Rust template engine registration for djust-components.

One render path per component tag (S3 of ``docs/reviews/2026-11-11-djust-2-consolidated.md``, decided
2026-11-12, completed by #4331): every ``djust_components`` tag has exactly one renderer, and no tag
is drawn by two implementations that can drift. Either

* the tag renders under the Rust engine through the ``{% load %}`` bridge (#2547,
  ``djust.template_libraries``), which runs the Python tag's OWN compile function and renders the
  Django node it returns, so the output is the Python tag's by construction (registered here, at
  ``ready()``, so a Rust template uses the tag without ``{% load djust_components %}``); or
* the tag has a native handler in this module, and the Django tag is a thin wrapper: it resolves its
  operands, renders its body where it has one, and calls the handler (``render_kwargs`` /
  ``python_operands``), so both engines draw the same bytes. These are:

  * the core layout tags (``app_shell``, ``app_sidebar``, ``app_header``, ``app_content``): the
    root-proof protocol (``_render_root_parts``, ``core_root_layout_object`` in
    ``crates/djust_templates/src/registry.rs``), which a bridged node cannot take part in;
  * ``badge``, ``avatar``, ``progress``, ``pagination``: through the bridge each use rendered an
    inclusion template through the backend, measured 4 to 8x slower; the templates are gone;
  * ``data_table`` (#3869): the Django tag calls :func:`render_data_table`, which hands
    :class:`DataTableHandler` the same operands the Rust engine does; there is no
    ``djust_components/table.html``;
  * the block tags whose node reads its children (``accordion``, ``sidebar``, ``nav_menu``,
    ``toolbar``, ``filter_bar``, ``page_header``) and their child tags: the bridge can only serve
    them by handing the body to Django's engine, which cannot compile a Rust-only tag
    (``{% call %}``, ``{% component %}``, ``{% slot %}``, ``{% dj_suspense %}``) and loses the
    ``{% if %}`` boundary markers the Rust renderer emits.

``tabs``, ``split_pane`` and ``form_array`` take the raw-body route (``RAW_BODY_TAGS``): Django's
engine compiles the body. ``form_array`` repeats its body once per row with the row pushed, which a
body rendered once cannot do; its markup is drawn by one handler, :class:`FormArrayHandler`.

The parity ratchet (``scripts/template-library-parity-baseline.csv``) is ``IDENTICAL`` for every tag
but ``filter_search`` outside a bar (``KNOWN_DIVERGENCES`` in
``python/tests/test_component_single_render_path.py``).

Inline handlers implement: render(self, args, context) -> str
Block handlers implement:  render(self, args, content, context) -> str
"""

import decimal
import json as _json
import math
import re as _re_filters
from collections.abc import Mapping
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Any, cast

from django.utils.functional import Promise
from django.utils.html import conditional_escape, escape
from django.utils.safestring import SafeData, mark_safe


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
    format_cell as _format_cell_util,
    interpolate_color_gradient,
    url_attr,
    data_url_attr,
)
from djust.components.utils import (
    attr_text,
    component_id_attr,
    row_pointer_style,
    style_attrs,
    table_column,
    table_rows,
    table_value_is_url,
)
from djust.template_tags import TYPED_ARGS_KEY
from djust.components._operands import STANDIN_TYPES, plain_operand

from djust.components import _operands

# Kept for importers of this module: defined here before #3883 moved them to `_operands`.
NamedTupleValue = _operands.NamedTupleValue
TupleValue = _operands.TupleValue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quoted(text: str) -> bool:
    """``text`` is a quoted literal: it opens and closes with the same quote character."""
    return len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]


def _parse_args(args: list[str], context: dict[str, object]) -> dict[str, object]:
    """The operands of a native handler call, ``["key=<operand>", ...]``, as a dict.

    The engine has already resolved every operand, and hands each one that resolved to a scalar,
    a tuple or an object a second time as the VALUE it is (``TYPED_ARGS_KEY``, declared for every
    native handler by ``_TypedOperands``). That value is taken through ``plain_operand``, the
    one conversion of a resolved value into what a handler reads, and the text is not looked at:
    a resolved string is never re-read as source (#3883: ``"false"`` stayed ``"false"``, not
    ``False``; ``"007"`` not ``7``; ``'"x"'`` kept its quotes) and never looked up in the context
    a second time (#3882: a value that is also a variable's name is that value, not the
    variable's).

    What reaches the text path is what the engine did not hand over as a value: a quoted literal
    the template wrote (its quotes are stripped), a list or dict the engine encoded as JSON (it
    is decoded; a large one is not materialised twice), an operand that did not resolve (empty).
    A bare word is read as the literal it spells (``True``, ``None``, a number) and otherwise kept
    as the text; it is never looked up in the context.

    The text is read exactly as it arrived (#3872): a source token never carries whitespace.
    """

    result: dict[str, object] = {}
    # Read once, and consulted BEFORE the text is parsed: the typed value replaces the operand, so
    # decoding its text (for a 2,000,000-element tuple, a JSON list built and thrown away) is work
    # for nothing (#3849). The key keeps its place in ``result``.
    typed = _typed_args(context)
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip()
        if key in typed:
            value = typed[key]
            if isinstance(value, _TAKEN_TYPES):
                result[key] = plain_operand(value)
                continue
            # An object the engine also gave a text for (a model as its floored dict, a date as
            # its ``str``): the text is kept as it is, as before, but never re-read as a literal
            # or a name. The type a Python tag would see is #3768's question.
            result[key] = _decoded_container(val)
            continue
        if _quoted(val):
            result[key] = val[1:-1]
        elif _looks_like_container(val):
            result[key] = _decoded_container(val)
        elif val in ("True", "true"):
            result[key] = True
        elif val in ("False", "false"):
            result[key] = False
        elif val in ("None", "null"):
            result[key] = None
        elif val == "" or val != val.strip():
            # Padding is part of a value (#3872): ``int(" 5 ")`` is 5, and ``" 5 "`` is not.
            result[key] = val
        else:
            try:
                result[key] = int(val)
            except ValueError:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val
    return result


class NotANumber(ValueError):
    """A numeric-by-contract parameter given something that is not a finite number.

    A ``ValueError``, so every handler that already falls back to a default for a bad number
    (``except (ValueError, TypeError)``) falls back for this too.
    """


#: The text a numeric parameter takes: ASCII digits, an optional sign and fraction, bounded in
#: length. Not Python's own ``int`` / ``float`` grammar, which also takes ``"inf"``, ``"nan"``,
#: ``"1e999"``, ``"1_000"`` and non-ASCII digits (``"\u0662"``), and a 4,301-digit string that
#: ``int`` refuses with a different error.
_NUMBER_TEXT = _re_filters.compile(r"-?[0-9]{1,18}(?:\.[0-9]{1,18})?")


def _number(value: object, tag: str = "", param: str = "") -> object:
    """A parameter that is numeric by contract (``page=``, ``value=``, ``min=``), as a number.

    A string operand now arrives as the string it is (#3883), so ``{% pagination page=p %}`` with
    ``p = request.GET["page"]`` (``"2"``) is handed ``"2"``. Text that spells a number
    (``_NUMBER_TEXT``) becomes that number (``int``, else ``float``). This is a conversion of the
    parameter's own value, never a context lookup (#3882).

    Any other string, and a float that is not finite, raises ``NotANumber`` naming the tag and
    parameter: a caller with a default catches it (it is a ``ValueError``) and falls back, the
    others let the message reach the developer. Any other value is returned as it is.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise NotANumber("{%% %s %%}: %s= takes a finite number, got %r" % (tag, param, value))
    if not isinstance(value, str):
        return value
    if _NUMBER_TEXT.fullmatch(value):
        return int(value) if "." not in value else float(value)
    raise NotANumber("{%% %s %%}: %s= takes a number, got %r" % (tag, param, value[:40]))


def _looks_like_container(text: str) -> bool:
    return (text.startswith("[") and text.endswith("]")) or (
        text.startswith("{") and text.endswith("}")
    )


def _decoded_container(text: str) -> object:
    """``text`` decoded when it is the JSON the engine encodes a list or dict as, else ``text``."""
    if _looks_like_container(text):
        try:
            return _json.loads(text)
        except (ValueError, TypeError):
            pass
    return text


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
    # The context is the second way a value reaches a handler; it goes through the same
    # conversion as an argument does (``plain_operand``).
    return plain_operand(context.get(key if key is not None else name, default))


#: What ``_parse_args`` takes off the typed channel (through ``plain_operand``): a string, marked
#: safe or lazy (#3883), a number, ``None``, a tuple or namedtuple (#3768, #3849) and the stand-in
#: objects (``UUID``, ``Path``). A list or dict is read from its JSON text (the engine hands an
#: inline handler a container only as text, so a large one is not materialised twice); any other
#: object keeps the engine's text for it.
_TAKEN_TYPES = (str, Promise, int, float, type(None), decimal.Decimal, tuple, *STANDIN_TYPES)


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
    values they are, and ``_parse_args`` takes them (``_TAKEN_TYPES``, #3883). Done here, once,
    for every inline handler, rather than as a class attribute on each of ~130 (one change instead
    of 130 that can drift); ``json_viewer`` declares it itself and is not wrapped.

    Everything else the engine reads off a handler (``RESOLVE_ARG_POSITIONS``, ...) is forwarded.
    """

    TYPED_ARGS = True
    #: Hand over every operand but a list or dict (a scalar, a tuple, an object), not every one: a
    #: 100,000-row list is not materialised a second time as Python objects for nothing; it is
    #: read from its JSON text.
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
    context answers the wrong question: it re-resolves a resolved value (#3882).

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


#: How an ``{% accordion_item %}`` hands the ``{% accordion %}`` that holds it what it was given
#: (#4331). The items render before the accordion, and the parent decides what an item cannot:
#: the accordion's ``id`` (the trigger / panel ids), its ``active`` (which item is open), its
#: ``event`` (the default for an item without one), its ``component_id=`` and an item's position
#: (the ``item-N`` id of an item without one). So each item writes this comment, carrying the hex
#: of a JSON list (only ``0-9a-f``: no value can end the comment, open another or read as markup),
#: then draws itself as a standalone item; the accordion redraws every item it finds whose
#: standalone markup follows its comment exactly. Outside an accordion the comment is invisible.
_ACCORDION_ITEM_MARK = _re_filters.compile(r"<!--djust-accordion-item:([0-9a-f]*)-->")

#: The id a standalone item's trigger and panel ids start with: the accordion's default ``id``.
_ACCORDION_DEFAULT_ID = "accordion"
_ACCORDION_DEFAULT_EVENT = "accordion_toggle"


def _accordion_item_parts(
    accordion_id: str,
    index: int,
    item_id: object,
    title: str,
    is_open: bool,
    event: object,
    cid_attr: str,
) -> tuple[str, str]:
    """The markup of one accordion item around its body: ``(before, after)``.

    WAI-ARIA accordion pattern: the trigger names the panel it controls (``aria-controls``), and
    that panel is always drawn, ``hidden`` while closed, so the reference never dangles (#4331:
    the Django tag pointed at a panel it drew only when open; the native one had no
    ``aria-controls``, panel ``id`` or ``role="region"``). ``accordion_id`` is escaped already.
    """
    iid = attr_text(item_id or f"item-{index}")
    trigger_id = f"{accordion_id}-trigger-{iid}"
    panel_id = f"{accordion_id}-panel-{iid}"
    open_cls = " dj-accordion-item--open" if is_open else ""
    chevron_cls = " dj-accordion__chevron--open" if is_open else ""
    before = (
        f'<div class="dj-accordion-item{open_cls}">'
        f'<button class="dj-accordion__trigger" id="{trigger_id}" '
        f'aria-expanded="{"true" if is_open else "false"}" aria-controls="{panel_id}" '
        f'dj-click="{attr_text(event)}" dj-value-value="{iid}"{cid_attr}>'
        f"<span>{escape(title)}</span>"
        f'<span class="dj-accordion__chevron{chevron_cls}" aria-hidden="true">&#9662;</span>'
        f"</button>"
        f'<div class="dj-accordion__content" id="{panel_id}" role="region" '
        f'aria-labelledby="{trigger_id}"{"" if is_open else " hidden"}>'
    )
    return before, "</div></div>"


def _accordion_item_standalone(payload: list[Any]) -> tuple[str, str]:
    """An item as it draws itself, outside any accordion, from its marker's payload."""
    item_id, title, own_open, own_event, _length = payload
    return _accordion_item_parts(
        _ACCORDION_DEFAULT_ID,
        0,
        item_id,
        title,
        bool(own_open),
        own_event or _ACCORDION_DEFAULT_EVENT,
        "",
    )


def _accordion_item_payload(text: str) -> list[Any] | None:
    """The marker's payload, or ``None`` when it is not one an item wrote."""
    try:
        payload = _json.loads(bytes.fromhex(text).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    if (
        isinstance(payload, list)
        and len(payload) == 5
        and isinstance(payload[1], str)
        and isinstance(payload[4], int)
        and not isinstance(payload[4], bool)
        and payload[4] >= 0
    ):
        return payload
    return None


class AccordionHandler:
    """``{% accordion %}``: the one renderer on both engines (#4331).

    The Django node renders its body and calls :meth:`render_kwargs`, as the Rust engine calls
    :meth:`render`. Every item of THIS accordion (at any depth of its body, inside ``{% if %}`` or
    ``{% for %}`` too, but not inside a nested accordion, which redrew its own) is redrawn with the
    accordion's ids, ``active``, ``event`` and ``component_id=``; any other body is kept as it is.
    An item is open when the accordion's ``active`` names it or its own ``open=`` is true; its own
    ``event=`` overrides the accordion's.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: dict[str, object], content: str) -> str:
        accordion_id = attr_text(kw.get("id", _ACCORDION_DEFAULT_ID))
        active = kw.get("active", "")
        event = kw.get("event", _ACCORDION_DEFAULT_EVENT)
        cid_attr = component_id_attr(kw.get("component_id"))
        parts: list[str] = []
        pos = 0
        index = 0
        while True:
            match = _ACCORDION_ITEM_MARK.search(content, pos)
            if match is None:
                break
            payload = _accordion_item_payload(match.group(1))
            if payload is None:
                parts.append(content[pos : match.end()])
                pos = match.end()
                continue
            before, after = _accordion_item_standalone(payload)
            body_start = match.end() + len(before)
            body_end = body_start + payload[4]
            if not (
                content.startswith(before, match.end()) and content.startswith(after, body_end)
            ):
                # Not the markup the marker's item drew: kept as it is.
                parts.append(content[pos : match.end()])
                pos = match.end()
                continue
            item_id, title, own_open, own_event, _length = payload
            is_open = bool(own_open) or (item_id or f"item-{index}") == active
            new_before, new_after = _accordion_item_parts(
                accordion_id, index, item_id, title, is_open, own_event or event, cid_attr
            )
            parts.append(content[pos : match.start()])
            parts.append(new_before)
            parts.append(content[body_start:body_end])
            parts.append(new_after)
            pos = body_end + len(after)
            index += 1
        parts.append(content[pos:])
        return _safe(f'<div class="dj-accordion" id="{accordion_id}">{"".join(parts)}</div>')


class AccordionItemHandler:
    """``{% accordion_item %}``: the one renderer on both engines (#4331). It draws itself as a
    standalone item behind a marker its ``{% accordion %}`` reads (``_ACCORDION_ITEM_MARK``)."""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: dict[str, object], content: str) -> str:
        title = kw.get("title", "")
        own_event = kw.get("event") or None
        text = _json.dumps(
            [kw.get("id", ""), str(title), bool(kw.get("open", False)), own_event, len(content)],
            default=str,
            separators=(",", ":"),
        )
        # Drawn from the payload read back, as the accordion reads it, so the two always agree.
        before, after = _accordion_item_standalone(_json.loads(text))
        key = text.encode("utf-8").hex()
        return _safe(f"<!--djust-accordion-item:{key}-->{before}{content}{after}")


_CLASS_TOKEN = _re_filters.compile(r"[A-Za-z0-9_-]+")


def _modifier(value: object, default: str) -> str:
    """``value`` as the suffix of a BEM modifier class (``dj-progress__fill--<value>``).

    One class token, escaped. A value that is not a token (a hex colour ``#336699``, a name with
    a space, markup) would leak into the class list as junk or as extra classes (#4331), so the
    ``default`` is drawn and the value is ignored: a colour is chosen by the theme's modifier
    classes, never by an arbitrary string."""
    from djust.components.utils import _exact_str

    text = _exact_str(value)
    return text if _CLASS_TOKEN.fullmatch(text) else default


def progress_value(value: object) -> int:
    """The percentage ``{% progress %}`` draws: ``value`` clamped to 0..100 (#3667).

    A ``value`` that is not a number is refused (``None``: TypeError, ``"abc"``: ValueError)
    and not drawn as 0%; text that spells a number is that number (#3883)."""
    return max(0, min(100, int(cast("str | int | float", _number(value, "progress", "value")))))


class ProgressHandler:
    """``{% progress %}``: the one implementation of the tag (#4331). The Rust engine calls
    :meth:`render`; the Django engine's simple tag calls :func:`render_progress`."""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), context)

    def render_kwargs(self, kw: dict[str, object], context: dict[str, object]) -> str:
        value = progress_value(kw.get("value", 0))
        raw_label = kw.get("label", "")
        label = conditional_escape(raw_label)
        size = attr_text(kw.get("size", "md"))
        color = attr_text(_modifier(kw.get("color", "primary"), "primary"))
        show_label = kw.get("show_label", True)
        header = ""
        if raw_label or show_label:
            label_part = f'<span class="dj-progress__label">{label}</span>' if raw_label else ""
            pct_part = f'<span class="dj-progress__value">{value}%</span>' if show_label else ""
            header = f'<div class="dj-progress__header">{label_part}{pct_part}</div>'
        # The fill's width is a class, not ``style="width:N%"``: a ``style-src`` without
        # ``'unsafe-inline'`` refuses the attribute and the bar drew empty (#3969). The track
        # carries the role, the value and a name (an attribute: ``escape`` always, #3898).
        name_attr = f' aria-label="{attr_text(raw_label)}"' if raw_label else ""
        return _safe(
            f'<div class="dj-progress dj-progress--{size}">{header}'
            f'<div class="dj-progress__track" role="progressbar" aria-valuenow="{value}" '
            f'aria-valuemin="0" aria-valuemax="100"{name_attr}>'
            f'<div class="dj-progress__fill dj-progress__fill--{color} dj-progress-w-{value}">'
            f"</div></div></div>"
        )


class BadgeHandler:
    """``{% badge %}``: the one implementation of the tag (#4331)."""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), context)

    def render_kwargs(self, kw: dict[str, object], context: dict[str, object]) -> str:
        label = conditional_escape(kw.get("label", ""))
        raw_status = kw.get("status", "default")
        status = attr_text(raw_status)
        pulse_cls = " dj-badge__dot--pulse" if kw.get("pulse", False) else ""
        # Colour alone does not say what a badge means: a status other than the default is
        # also written for a screen reader (the native markup dropped it, #4331).
        sr = (
            f'<span class="sr-only">Status: {conditional_escape(raw_status)}</span>'
            if raw_status and raw_status != "default"
            else ""
        )
        return _safe(
            f'<span class="dj-badge dj-badge--{status}">'
            f'<span class="dj-badge__dot{pulse_cls}" aria-hidden="true"></span>{sr}'
            f'<span class="dj-badge__label">{label}</span></span>'
        )


class PaginationHandler:
    """``{% pagination %}``: the one implementation of the tag (#4331)."""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), context)

    def render_kwargs(self, kw: dict[str, object], context: dict[str, object]) -> str:
        from djust.components.templatetags.djust_components import pagination_pages

        kw = dict(kw)
        for numeric in ("page", "total_pages"):
            if numeric in kw:
                kw[numeric] = _number(kw[numeric], "pagination", numeric)
        # The page list first: whatever it raises for these numbers (``total_pages=None``:
        # ``None + 1``) is raised here too (#3667).
        shown = pagination_pages(kw.get("page", 1), kw.get("total_pages", 1))
        try:
            page = int(cast("str | int | float", _number(kw.get("page", 1))))
            total_pages = int(cast("str | int | float", _number(kw.get("total_pages", 1))))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        prev_event = attr_text(kw.get("prev_event", "page_prev"))
        next_event = attr_text(kw.get("next_event", "page_next"))
        prev_disabled = " disabled" if page <= 1 else ""
        next_disabled = " disabled" if page >= total_pages else ""
        numbered = ""
        for p in shown:
            if p == "...":
                numbered += (
                    '<span class="dj-pagination__ellipsis" aria-hidden="true">&hellip;</span>'
                )
                continue
            active = " dj-pagination__btn--active" if p == page else ""
            current = ' aria-current="page"' if p == page else ""
            number = conditional_escape(p)
            a_number = attr_text(p)
            numbered += (
                f'<button class="dj-pagination__btn{active}"{current} aria-label="Page {a_number}" '
                f'dj-click="{next_event}" dj-value-value="{a_number}">{number}</button>'
            )
        return _safe(
            f'<nav class="dj-pagination" aria-label="Pagination">'
            f'<button class="dj-pagination__btn" aria-label="Previous page" '
            f'dj-click="{prev_event}"{prev_disabled}>&larr;</button>'
            f"{numbered}"
            f'<button class="dj-pagination__btn" aria-label="Next page" '
            f'dj-click="{next_event}"{next_disabled}>&rarr;</button></nav>'
        )


class AvatarHandler:
    """``{% avatar %}``: the one implementation of the tag (#4331)."""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), context)

    def render_kwargs(self, kw: dict[str, object], context: dict[str, object]) -> str:
        src = kw.get("src", "")
        raw_alt = kw.get("alt", "") or ""
        alt = attr_text(raw_alt)
        # The initials are taken from the text, then escaped: slicing the ESCAPED text cut an
        # entity in half (``O'Brien`` -> ``O&``, #3883).
        raw_initials = kw.get("initials", "") or (str(raw_alt)[:2].upper() if raw_alt else "")
        initials = conditional_escape(raw_initials)
        size = attr_text(kw.get("size", "md"))
        status = attr_text(kw.get("status", ""))
        if src:
            body = (
                f'<img class="dj-avatar__img" src="{data_url_attr(src, image=True)}" alt="{alt}">'
            )
        else:
            name = attr_text(raw_alt or raw_initials)
            body = (
                f'<span class="dj-avatar__initials" role="img" aria-label="{name}">'
                f"{initials}</span>"
            )
        status_html = (
            f'<span class="dj-avatar__status dj-avatar__status--{status}" aria-hidden="true">'
            f"</span>"
            if status
            else ""
        )
        return _safe(f'<div class="dj-avatar dj-avatar--{size}">{body}{status_html}</div>')


# Backward-compatible aliases — delegated to djust_components.utils
_format_cell = _format_cell_util


_interpolate_color_simple = interpolate_color_gradient


#: What the Python table loads to make a clickable row navigate: it reads ``data-href`` and does
#: the ``location.assign`` (and the Enter / Space keys, and the nested-control guard). The path is
#: ``table.html``'s, character for character (#3887).
_ROW_CLICK_SCRIPT = '<script src="/static/djust_components/data-table-row-click.js" defer></script>'


#: What makes a clickable row reachable by keyboard and announced as a control (``table.html``).
#: Its pointer cursor follows :func:`row_pointer_style` (#4139): inline by default, the
#: ``data-table-row-clickable`` class's under a strict CSP.
_ROW_NAV_A11Y = 'role="button" tabindex="0"'


_ABSENT = object()


def _row_get(row: Mapping[Any, object], key: object, default: object = "") -> object:
    """``row[key]`` the way the Python tag reads it (``table_cell``: the key as it is, #3895).

    A key that cannot be one (a list, a dict: unhashable in Python) reads ``default``. A row whose
    keys are not all text reaches the handler as itself (``_with_typed``), so ``{1: "a", "1": "b"}``
    reads ``a`` for ``1`` and ``b`` for ``"1"``, as in Django. Only when that lookup misses, a
    key that is not text is tried as its text: a row that crossed as JSON has text keys only, and
    the text channel re-types a key it is handed (``"5"`` arrives as ``5``, #3882).
    """
    try:
        value = row.get(key, _ABSENT)
    except TypeError:  # an unhashable key
        return default
    if value is _ABSENT and not isinstance(key, str):
        value = row.get(str(key), _ABSENT)
    return default if value is _ABSENT else value


def _row_text(row: Mapping[Any, object], key: object) -> str:
    """``row[key]`` as text, the way the Python tag reads it (``table_cell``, #3870).

    A key the row lacks, and a key that cannot be one, are ``""``. A value that is not text is
    written with ``str()``: ``None`` is ``"None"``, a number is not localised. The key is read as
    :func:`_row_get` reads it.
    """
    value = _row_get(row, key)
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
    have), ``None``, blank text or ``NaN`` / an infinity. The Python tag's ``table_has_href``
    (both ask ``table_value_is_url``): a link column draws plain text and a ``row_url`` row is not
    clickable, instead of ``href="None"`` / ``href=""`` / ``href="nan"``."""
    return table_value_is_url(_row_get(row, key, None))


#: The types whose ``str()`` never holds a character HTML escaping changes (#3895 performance).
_NO_MARKUP_TYPES = frozenset({int, float, bool, type(None)})


def _text_lost(value: object) -> bool:
    """A cell value its row's JSON text cannot carry (#3895): a developer's ``mark_safe`` string
    (the text is plain) and ``NaN`` / an infinity (the text is ``null``)."""
    if isinstance(value, float):
        return not math.isfinite(value)
    return isinstance(value, str) and isinstance(value, SafeData)


def _with_typed(text_value: object, typed_value: object) -> object:
    """``text_value`` (what the handler parsed from its string argument) with what its JSON text
    lost put back from ``typed_value``, the same operand as the engine resolved it (#3895).

    The engine hands that typed value over only for a list or dict holding such a member
    (``text_loses_a_member``, ``crates/djust_templates/src/renderer.rs``). Item by item:

    * a dict item keeps its text, plus each value of the typed one that is :func:`_text_lost`;
    * an item the text could not hold at all (a namedtuple became a list, an object its
      ``str()``) is the typed item itself, which ``table_row`` / ``table_column`` then read.
    """
    if not isinstance(text_value, list) or not isinstance(typed_value, (list, tuple)):
        return text_value
    if len(typed_value) != len(text_value):
        return text_value
    merged: list[object] = []
    for text_item, typed_item in zip(text_value, typed_value):
        if isinstance(text_item, dict) and isinstance(typed_item, Mapping):
            if any(not isinstance(k, str) for k in typed_item):
                # A key that is not text was made text by JSON (``1`` and ``"1"`` are one key
                # there): the row is read with its own keys, as Django reads it.
                merged.append(dict(typed_item))
                continue
            lost = {
                (k if isinstance(k, str) else str(k)): v
                for k, v in typed_item.items()
                if isinstance(k, (str, int)) and not isinstance(k, bool) and _text_lost(v)
            }
            merged.append({**text_item, **lost} if lost else text_item)
        elif not isinstance(text_item, dict) and not isinstance(
            typed_item, (str, int, float, type(None))
        ):
            merged.append(typed_item)
        else:
            merged.append(text_item)
    return merged


class DataTableHandler:
    """``{% data_table %}``: the one implementation of the tag (#3869).

    The Rust engine calls :meth:`render` with its resolved operands; the Django engine's
    ``data_table`` simple tag calls :func:`render_data_table`, which hands :meth:`render_kwargs` the
    same operands as Python values. Both engines draw the same markup.
    """

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), context)

    def render_kwargs(self, kw: dict[str, object], context: dict[str, object]) -> str:
        """Draw the table from its operands ``kw``, as :func:`_parse_args` reads them. ``context``
        is the render context: ``TYPED_ARGS_KEY`` in it carries what the text of ``rows`` /
        ``columns`` lost (#3895); ``rows`` / ``columns`` absent from ``kw`` are read from it."""
        rows = _passed_or_context(kw, "rows", context, default=[])
        columns = _passed_or_context(kw, "columns", context, default=[])
        sort_by = conditional_escape(str(kw.get("sort_by", "")))
        sort_desc = kw.get("sort_desc", False)
        sort_event = escape(kw.get("sort_event", "on_table_sort"))

        # Phase 1 parameters (all opt-in)
        selectable = kw.get("selectable", False)
        selected_rows = kw.get("selected_rows") or []
        select_event = escape(kw.get("select_event", "on_table_select"))
        row_key = str(kw.get("row_key", "id"))
        search = kw.get("search", False)
        search_query = escape(str(kw.get("search_query", "")))
        search_event = escape(kw.get("search_event", "on_table_search"))
        try:
            search_debounce = int(
                cast("str | int | float", _number(kw.get("search_debounce", 300)))
            )
        except (ValueError, TypeError):
            search_debounce = 300
        filters = kw.get("filters") or {}
        filter_event = escape(kw.get("filter_event", "on_table_filter"))
        loading = kw.get("loading", False)
        empty_title = conditional_escape(str(kw.get("empty_title", "No data")))
        empty_description = conditional_escape(str(kw.get("empty_description", "")))
        empty_icon = conditional_escape(str(kw.get("empty_icon", "")))
        paginate = kw.get("paginate", False)
        try:
            page = int(cast("str | int | float", _number(kw.get("page", 1))))
            total_pages = int(cast("str | int | float", _number(kw.get("total_pages", 1))))
        except (ValueError, TypeError):
            page, total_pages = 1, 1
        page_event = escape(kw.get("page_event", "on_table_page"))
        striped = kw.get("striped", False)
        compact = kw.get("compact", False)
        # Row navigation: a clickable row fires ``row_click_event`` (#1111). The Python
        # template wires it; the native table did not (#3669).
        # Decided on the value, not on its escaped text: ``row_click_event=None`` wires nothing
        # in the Python template, and "None" is truthy.
        _rce = kw.get("row_click_event", "")
        row_click_event = escape(_rce) if _rce else ""
        row_click_value_key = str(kw.get("row_click_value_key", "id"))
        # ``row_url`` names the row key that holds the row's URL (``data-href``, which the script
        # below navigates to). A row click wins over it, as in the Python template's ``elif``, and
        # a falsy ``row_url`` is not the key "None": decided on the value (#3887).
        _ru = kw.get("row_url", "")
        row_url = _ru if _ru else ""
        row_nav_script = _ROW_CLICK_SCRIPT if (row_click_event or row_url) else ""
        # Backward-compatible pager (``total_pages > 1`` without ``paginate``), as the Python
        # template draws it (#3669).
        prev_event = escape(kw.get("prev_event", "on_table_prev"))
        next_event = escape(kw.get("next_event", "on_table_next"))

        # Phase 2 parameters (all opt-in)
        editable_columns = kw.get("editable_columns") or []
        edit_event = escape(kw.get("edit_event", "on_table_cell_edit"))
        resizable = kw.get("resizable", False)
        reorderable = kw.get("reorderable", False)
        reorder_event = escape(kw.get("reorder_event", "on_table_reorder"))
        try:
            frozen_left = int(cast("str | int | float", _number(kw.get("frozen_left", 0))))
        except (ValueError, TypeError):
            frozen_left = 0
        try:
            frozen_right = int(cast("str | int | float", _number(kw.get("frozen_right", 0))))
        except (ValueError, TypeError):
            frozen_right = 0
        column_visibility = kw.get("column_visibility", False)
        visibility_event = escape(kw.get("visibility_event", "on_table_visibility"))
        density = conditional_escape(str(kw.get("density", "comfortable")))
        density_toggle = kw.get("density_toggle", False)
        density_event = escape(kw.get("density_event", "on_table_density"))
        responsive_cards = kw.get("responsive_cards", False)
        editable_rows = kw.get("editable_rows", False)
        edit_row_event = escape(kw.get("edit_row_event", "on_table_row_edit"))
        save_row_event = escape(kw.get("save_row_event", "on_table_row_save"))
        cancel_row_event = escape(kw.get("cancel_row_event", "on_table_row_cancel"))
        editing_rows = kw.get("editing_rows") or []

        # Phase 3 parameters (all opt-in)
        expandable = kw.get("expandable", False)
        expand_event = escape(kw.get("expand_event", "on_table_expand"))
        expanded_rows = kw.get("expanded_rows") or []
        bulk_actions = kw.get("bulk_actions") or []
        bulk_action_event = escape(kw.get("bulk_action_event", "on_table_bulk_action"))
        exportable = kw.get("exportable", False)
        export_event = escape(kw.get("export_event", "on_table_export"))
        export_formats = kw.get("export_formats") or ["csv", "json"]
        group_by = str(kw.get("group_by", ""))
        group_toggle_event = escape(kw.get("group_toggle_event", "on_table_group_toggle"))
        collapsible_groups = kw.get("collapsible_groups", True)
        collapsed_groups = kw.get("collapsed_groups") or []
        keyboard_nav = kw.get("keyboard_nav", False)
        virtual_scroll = kw.get("virtual_scroll", False)
        try:
            virtual_row_height = int(
                cast("str | int | float", _number(kw.get("virtual_row_height", 40)))
            )
        except (ValueError, TypeError):
            virtual_row_height = 40
        try:
            virtual_buffer = int(cast("str | int | float", _number(kw.get("virtual_buffer", 5))))
        except (ValueError, TypeError):
            virtual_buffer = 5
        server_mode = kw.get("server_mode", False)
        facets = kw.get("facets", False)
        facet_counts = kw.get("facet_counts") or {}
        persist_key = escape(str(kw.get("persist_key", "")))
        printable = kw.get("printable", False)
        column_stats = kw.get("column_stats") or {}

        # Phase 4 parameters (all opt-in)
        footer_aggregations = kw.get("footer_aggregations") or {}
        row_class_map = kw.get("row_class_map") or {}
        column_groups = kw.get("column_groups") or []
        row_drag = kw.get("row_drag", False)
        row_drag_event = escape(kw.get("row_drag_event", "on_table_row_drag"))
        copyable = kw.get("copyable", False)
        copy_event = escape(kw.get("copy_event", "on_table_copy"))
        copy_format = escape(str(kw.get("copy_format", "csv")))

        # Phase 5 parameters (all opt-in)
        importable = kw.get("importable", False)
        import_event = escape(kw.get("import_event", "on_table_import"))
        import_formats = kw.get("import_formats") or ["csv", "json"]
        import_preview_data = kw.get("import_preview_data") or []
        import_errors = kw.get("import_errors") or []
        import_pending = kw.get("import_pending", False)
        computed_columns = kw.get("computed_columns") or []
        cell_merge_key = str(kw.get("cell_merge_key", "_merge"))
        column_expressions = kw.get("column_expressions") or {}
        expression_event = escape(kw.get("expression_event", "on_table_expression"))
        active_expressions = kw.get("active_expressions") or {}
        conditional_formatting = kw.get("conditional_formatting") or []

        if not isinstance(rows, (list, tuple)):
            rows = []
        if not isinstance(columns, (list, tuple)):
            columns = []
        # What the JSON text of ``rows`` / ``columns`` lost, from the typed channel (#3895): a
        # ``mark_safe`` cell, ``NaN``, a namedtuple or object column, an object row. Then both
        # read the way the Python tag reads them (``table_column`` / ``table_rows``).
        typed = _typed_args(context)
        typed_columns = typed.get("columns") if "columns" in kw else None
        if isinstance(typed_columns, (list, tuple)) and len(typed_columns) == len(columns):
            # A column list is short: when its text lost anything (a function, a namedtuple, an
            # object, a ``mark_safe`` label), the typed columns are read whole, as Django does.
            columns = list(typed_columns)
        columns = [table_column(col) for col in columns]
        if "rows" in kw:
            rows = _with_typed(rows, typed.get("rows"))
        row_keys: list[object] = ["id", row_key, row_url, row_click_value_key]
        for col in columns:
            if isinstance(col, dict):
                row_keys += [col.get("key"), col.get("link")]
        rows = table_rows(rows, row_keys)
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
                    ckey = escape(str(col.get("key", "")))
                    clabel = escape(str(col.get("label", col.get("key", ""))))
                else:
                    ckey = clabel = escape(str(col))
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
                    f' dj-click="{density_event}" dj-value-value="{val}">{label}</button>'
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
                fmt_esc = escape(str(fmt))
                label = fmt_esc.upper()
                export_btns += (
                    f'<button type="button" class="data-table-export-btn"'
                    f' dj-click="{export_event}" dj-value-value="{fmt_esc}">'
                    f"Export {label}</button>"
                )
            toolbar_parts.append(f'<div class="data-table-export">{export_btns}</div>')

        if copyable:
            toolbar_parts.append(
                f'<div class="data-table-copy">'
                f'<button type="button" class="data-table-copy-btn"'
                f' dj-click="{copy_event}" dj-value-value="selected">'
                f"Copy</button>"
                f"</div>"
            )

        if importable:
            import_btns = ""
            for ifmt in import_formats:
                ifmt_esc = escape(str(ifmt))
                label = ifmt_esc.upper()
                import_btns += (
                    f'<button type="button" class="data-table-import-btn"'
                    f' data-import-format="{ifmt_esc}">'
                    f"Import {label}</button>"
                )
            toolbar_parts.append(
                f'<div class="data-table-import">'
                f"{import_btns}"
                f'<input type="file" class="data-table-import-file" hidden'
                f' accept=".csv,.json">'
                f"</div>"
            )

        # Bulk actions bar (rendered separately, shown conditionally)
        bulk_actions_html = ""
        if bulk_actions and selected_rows:
            ba_btns = ""
            for ba in bulk_actions:
                if isinstance(ba, dict):
                    ba_key = escape(str(ba.get("key", "")))
                    ba_label = escape(str(ba.get("label", ba.get("key", ""))))
                else:
                    ba_key = ba_label = escape(str(ba))
                ba_btns += (
                    f'<button type="button" class="data-table-bulk-btn"'
                    f' dj-click="{bulk_action_event}" dj-value-value="{ba_key}">'
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
                '<div class="skeleton skeleton-line dj-progress-w-100"></div>' for _ in range(5)
            )
            return _safe(
                f'<div class="{" ".join(wrapper_classes)}" role="grid"'
                f' aria-label="Data table" aria-busy="true"{wrapper_attrs_str}>'
                f"{toolbar_html}"
                f"{search_html}"
                f'<div class="data-table-loading skeleton-table" aria-live="polite">'
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
                # The key is only ever an attribute value: escaped whatever it is marked. The label
                # is the header's text, where a ``mark_safe`` label is kept as ``table.html``
                # keeps it (``{{ col.label }}``), and escaped again where it is an attribute.
                raw_label = col.get("label", str(col.get("key", "")))
                key = escape(str(col.get("key", "")))
                col_label = conditional_escape(str(raw_label))
                col_label_attr = escape(str(raw_label))
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
                key = col_label = col_label_attr = escape(str(col))
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

            # The width, applied by the client: a `style=` is refused by a strict CSP (#3979).
            width_attr = style_attrs(width=width, css="width:{}")

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
                    # The direction glyph is decorative: the state is ``aria-sort`` (#3869).
                    glyph = "&#8595;" if sort_desc else "&#8593;"
                    arrow = (
                        f' <span class="data-table-sort-glyph" aria-hidden="true">{glyph}</span>'
                    )
                    aria_sort = "descending" if sort_desc else "ascending"
                else:
                    arrow = ""
                    aria_sort = "none"
                header_cells.append(
                    f'<th class="sortable{active}{frozen_cls}" role="columnheader" tabindex="0"'
                    f' aria-sort="{aria_sort}"'
                    f' dj-click="{sort_event}" dj-value-value="{key}"{width_attr}{extra_attrs}>'
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
                    filter_val = escape(str(filters.get(key, "")))
                    if filter_type == "select":
                        opts_html = '<option value="">All</option>'
                        for opt in filter_options:
                            if isinstance(opt, dict):
                                opt_val = escape(str(opt.get("value", "")))
                                opt_label = escape(str(opt.get("label", opt.get("value", ""))))
                            else:
                                opt_val = opt_label = escape(str(opt))
                            selected = " selected" if opt_val == filter_val else ""
                            opts_html += f'<option value="{opt_val}"{selected}>{opt_label}</option>'
                        filter_cells.append(
                            f'<th{frozen_f}><select class="data-table-filter"'
                            f' aria-label="Filter {col_label_attr}"'
                            f' dj-input="{filter_event}" dj-value-column="{key}">'
                            f"{opts_html}"
                            f"</select></th>"
                        )
                    else:
                        filter_cells.append(
                            f'<th{frozen_f}><input type="text" class="data-table-filter"'
                            f' aria-label="Filter {col_label_attr}"'
                            f' placeholder="Filter..."'
                            f' value="{filter_val}"'
                            f' dj-input="{filter_event}" dj-value-column="{key}">'
                            f"</th>"
                        )
                else:
                    filter_cells.append(f"<th{frozen_f}></th>")

        # Phase 5: Append computed column headers
        if computed_columns:
            for cc in computed_columns:
                if not isinstance(cc, dict):
                    continue
                cc_key = escape(str(cc.get("key", "")))
                cc_label = escape(str(cc.get("label", cc.get("key", ""))))
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
                key_esc = escape(str(key))
                if key in column_expressions:
                    expr_val = escape(str(active_expressions.get(key, "")))
                    placeholder = escape(str(column_expressions.get(key, "Expression...")))
                    expression_cells.append(
                        f'<th><input type="text" class="data-table-expression"'
                        f' aria-label="Expression filter {key_esc}"'
                        f' placeholder="{placeholder}"'
                        f' value="{expr_val}"'
                        f' dj-value-column="{key_esc}"'
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
                f' dj-click="{select_event}" dj-value-value="__all__"></th>',
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

        # Phase 5: every renderable column (base + computed), and what each cell reads off its
        # column, worked out once per table rather than once per row.
        all_columns = list(columns)
        if computed_columns:
            all_columns = list(columns) + [cc for cc in computed_columns if isinstance(cc, dict)]
        column_keys: list[tuple[object, str, object]] = []
        for col in all_columns:
            col_k = col.get("key", col) if isinstance(col, dict) else col
            column_keys.append(
                (col_k, str(col_k), col.get("type", "") if isinstance(col, dict) else "")
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
                            row_classes.append(escape(str(rcm_map[rcm_val])))
            row_attrs += f' data-row-key="{escape(row_id)}"'
            if row_click_event:
                row_classes.append("data-table-row-clickable")
                row_attrs += (
                    f' dj-click="{row_click_event}"'
                    f' dj-value-value="{escape(str(row.get(row_click_value_key, "")))}"'
                    # The row's key, which differs from ``value`` when ``row_click_value_key``
                    # is not the key; ``data-row-key`` stays for data-table.js (#4186).
                    f' dj-value-row-key="{escape(row_id)}"'
                    f" {_ROW_NAV_A11Y}{row_pointer_style()}"
                )
            elif row_url and _has_row_href(row, row_url):
                # The row's URL (#3887). ``data-href`` is read by ``data-table-row-click.js``.
                # A row with no URL under the key is not clickable (#3895).
                row_classes.append("data-table-row-clickable")
                row_attrs += f' data-href="{_row_href(row, row_url)}" {_ROW_NAV_A11Y}'
                row_attrs += row_pointer_style()

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
                    f' dj-value-value="{escape(row_id)}">{exp_icon}</button>'
                    f"</td>"
                )

            if selectable:
                checked = " checked" if is_selected else ""
                cells += (
                    f'<td><input type="checkbox" class="data-table-checkbox"'
                    f' aria-label="Select row"'
                    f"{checked}"
                    f' dj-click="{select_event}"'
                    f' dj-value-value="{escape(row_id)}"></td>'
                )

            # Phase 5: cell merge data for this row
            merge_data = row.get(cell_merge_key, {}) if isinstance(row, dict) else {}
            if not isinstance(merge_data, dict):
                merge_data = {}
            skip_cols = set()  # column indices to skip (merged into previous cell)

            # Pre-compute which columns are merged away
            if merge_data:
                for col_idx, (_k, col_k_str, _t) in enumerate(column_keys):
                    colspan = merge_data.get(col_k_str, 1)
                    if isinstance(colspan, int) and colspan > 1:
                        for offset in range(1, colspan):
                            if col_idx + offset < len(all_columns):
                                skip_cols.add(col_idx + offset)

            for col_idx, col in enumerate(all_columns):
                if col_idx in skip_cols:
                    continue  # This cell is merged into a previous colspan
                col_k, col_k_str, col_type = column_keys[col_idx]
                try:  # the common case, a key the row has, without a call
                    raw_val = row[col_k]
                except (KeyError, TypeError):
                    raw_val = _row_get(row, col_k)
                # Phase 4: column type formatting (``col_type``, read once per column)
                if col_type and raw_val is not None and raw_val != "":
                    cell_text = _format_cell(raw_val, col)
                else:
                    cell_text = str(raw_val)
                # The cell's text keeps a developer's ``mark_safe`` (``table.html`` writes
                # ``{{ row|table_cell:col.key }}``, #3895); as an attribute value (only a
                # ``cell_template`` writes one) it is escaped. The text of a number, a bool or
                # ``None`` has no character to escape: it is written as it is (most cells).
                if not col_type and type(raw_val) in _NO_MARKUP_TYPES:
                    cell_val = cell_text
                else:
                    cell_val = conditional_escape(cell_text)
                col_label_for_card = ""
                if responsive_cards and isinstance(col, dict):
                    col_label_for_card = escape(str(col.get("label", col_k_str)))

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
                    td_classes.append(f"data-table-type-{escape(col_type)}")
                # Phase 4: cell_class from column config
                cell_class = col.get("cell_class", "") if isinstance(col, dict) else ""
                if cell_class:
                    # cell_class can be a string or a dict {value: class}
                    if isinstance(cell_class, dict):
                        cc_val = str(raw_val)
                        if cc_val in cell_class:
                            td_classes.append(escape(str(cell_class[cc_val])))
                    elif isinstance(cell_class, str):
                        td_classes.append(escape(cell_class))

                # Phase 5: conditional formatting
                cf_html = ""
                if (
                    conditional_formatting
                    and raw_val is not None
                    and raw_val != ""
                    and not (isinstance(raw_val, float) and not math.isfinite(raw_val))
                ):
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
                            # A width class, not `style="width:N%"`, which a strict
                            # `style-src` refuses (#3979; the `{% progress %}` pattern).
                            cf_html = (
                                f'<div class="data-table-data-bar'
                                f' dj-progress-w-{round(cf_pct)}"></div>'
                            )
                        elif cf_type == "color_scale":
                            cf_ratio = max(0.0, min(1.0, (cf_num - cf_min) / cf_span))
                            cf_colors = cf_preset.get("colors", ["#ff0000", "#00ff00"])
                            cf_color = _interpolate_color_simple(cf_colors, cf_ratio)
                            td_classes.append("data-table-cf-color-scale")
                            cf_html = style_attrs(
                                background_color=cf_color, css="background-color:{}"
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
                    cell_tpl_esc = escape(str(cell_template))
                    cell_val = (
                        f'<span class="cell-renderer cell-renderer-{cell_tpl_esc}"'
                        f' data-value="{escape(cell_text)}">{cell_val}</span>'
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
                # color_scale cf_html is a `data-dj-style-*` attr, handled below

                # color_scale extra style
                extra_style = ""
                if cf_html and "data-table-cf-color-scale" in " ".join(td_classes):
                    extra_style = cf_html  # the `data-dj-style-background-color` attr

                # Editable cell (inline editing)
                is_col_editable = col_k_str in editable_col_set

                # Editable row mode: all cells become inputs when row is editing
                if editable_rows and is_editing:
                    raw_val = escape(str(_row_get(row, col_k)))
                    cells += (
                        f"<td{td_cls_str}{label_attr}{colspan_attr}>"
                        f'<input type="text" value="{raw_val}"'
                        f' name="{escape(col_k_str)}"'
                        f' aria-label="Edit {escape(col_k_str)}">'
                        f"</td>"
                    )
                elif is_col_editable:
                    cells += (
                        f'<td data-editable="true"'
                        f' data-col-key="{escape(col_k_str)}"'
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
                        f' dj-value-value="{escape(row_id)}">Save</button>'
                        f' <button class="cancel-btn"'
                        f' dj-click="{cancel_row_event}"'
                        f' dj-value-value="{escape(row_id)}">Cancel</button>'
                        f"</td>"
                    )
                else:
                    cells += (
                        f'<td class="data-table-row-actions">'
                        f'<button dj-click="{edit_row_event}"'
                        f' dj-value-value="{escape(row_id)}">Edit</button>'
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
                    f' data-row-key="{escape(row_id)}"></div>'
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
                    g_esc = escape(gval)
                    is_collapsed = gval in collapsed_group_set
                    toggle_attr = ""
                    if collapsible_groups:
                        toggle_attr = f' dj-click="{group_toggle_event}" dj-value-value="{g_esc}"'
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
                            # ``NaN`` / an infinity was ``None`` before #3895 handed it over: still
                            # left out of the aggregate, as then.
                            if v is not None and not (
                                isinstance(v, float) and not math.isfinite(v)
                            ):
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
                        f'<td class="data-table-footer-agg" data-agg-type="{escape(str(agg_type))}">'
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
                f' dj-click="{page_event}" dj-value-value="{prev_page}">&#8592;</button>'
                f'<span class="pagination-info">Page {page} of {total_pages}</span>'
                f'<button class="pagination-btn"{next_disabled}'
                f' dj-click="{page_event}" dj-value-value="{next_page}">&#8594;</button>'
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
                f'<button class="data-table-reorder-trigger" hidden'
                f' dj-click="{reorder_event}"></button>'
            )
        if editable_columns:
            triggers_html += (
                f'<button class="data-table-edit-trigger" hidden dj-click="{edit_event}"></button>'
            )
        if column_visibility:
            triggers_html += (
                f'<button class="data-table-visibility-trigger" hidden'
                f' dj-click="{visibility_event}"></button>'
            )
        if row_drag:
            triggers_html += (
                f'<button class="data-table-drag-trigger" hidden'
                f' dj-click="{row_drag_event}"></button>'
            )
        if copyable:
            triggers_html += (
                f'<button class="data-table-copy-trigger" hidden dj-click="{copy_event}"></button>'
            )
        if importable:
            triggers_html += (
                f'<button class="data-table-import-trigger" hidden'
                f' dj-click="{import_event}"></button>'
            )
        if column_expressions:
            triggers_html += (
                f'<button class="data-table-expression-trigger" hidden'
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
                    f" dj-value-value='{{\"confirm\":true}}'>Confirm Import</button>"
                    f'<button type="button" class="data-table-import-cancel"'
                    f' dj-click="{import_event}"'
                    f" dj-value-value='{{\"cancel\":true}}'>Cancel</button>"
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
            facet_attr = f" data-facet-counts='{escape(_json.dumps(facet_counts))}'"

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


_PYTHON_SCALARS = (str, Promise, int, float, type(None), decimal.Decimal, tuple, *STANDIN_TYPES)


def _python_operand(value: object) -> object:
    """One argument of the Django engine's ``{% data_table %}`` as the Rust engine hands it to
    :class:`DataTableHandler`: a scalar, a tuple, a lazy string through ``plain_operand``, and a
    list, dict, QuerySet, model or other object as the JSON-able value the engine's JSON text for
    it decodes to (``normalize_django_value``, the normalizer every context goes through, which
    keeps the serialization floor: a model is its floored dict, never its attributes). A date, a
    ``Decimal``, a ``UUID`` and the like take their ``str``, as they do in the engine's text."""
    if isinstance(value, _PYTHON_SCALARS):
        return plain_operand(value)
    from djust.serialization import normalize_django_value

    return _json.loads(_json.dumps(normalize_django_value(value), default=str))


def _materialised(value: object) -> object:
    """``rows`` / ``columns`` given as a generator, a set, a QuerySet or another iterable is the
    list of its items (the Django engine's table read any iterable)."""
    if isinstance(value, (str, bytes, list, tuple, Mapping)) or not hasattr(value, "__iter__"):
        return value
    return list(cast("Any", value))


def render_data_table(**params: object) -> str:
    """The Django engine's ``{% data_table %}``: the same table the Rust engine draws (#3869).

    ``params`` are the tag's arguments as Python values. What the JSON text of ``rows`` /
    ``columns`` loses (a ``mark_safe`` cell, ``NaN``, a namedtuple column) is handed back from the
    original value exactly as the Rust engine's typed channel does (``TYPED_ARGS_KEY``, #3895).
    """
    for key in ("rows", "columns"):
        if key in params:
            params[key] = _materialised(params[key])
    kw = {key: _python_operand(value) for key, value in params.items()}
    typed = {
        key: params[key]
        for key in ("rows", "columns")
        if isinstance(params.get(key), (list, tuple))
    }
    return DataTableHandler().render_kwargs(kw, {TYPED_ARGS_KEY: typed})


def _render_simple(handler: Any, params: dict[str, object]) -> str:
    """One Django-engine call of a single-implementation tag: ``params`` as Python values, handed
    to the handler as the Rust engine's resolved operands are (``_python_operand``, #4331)."""
    return cast(str, handler.render_kwargs({k: _python_operand(v) for k, v in params.items()}, {}))


def render_badge(**params: object) -> str:
    """The Django engine's ``{% badge %}``: the Rust engine's markup, one renderer (#4331)."""
    return _render_simple(BadgeHandler(), params)


def render_avatar(**params: object) -> str:
    """The Django engine's ``{% avatar %}`` (#4331)."""
    return _render_simple(AvatarHandler(), params)


def render_progress(**params: object) -> str:
    """The Django engine's ``{% progress %}`` (#4331)."""
    return _render_simple(ProgressHandler(), params)


def render_pagination(**params: object) -> str:
    """The Django engine's ``{% pagination %}`` (#4331)."""
    return _render_simple(PaginationHandler(), params)


class PageHeaderActionsHandler:
    """Block handler for {% page_header_actions %}...{% endpage_header_actions %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        # Just wrap actions content in its container div
        return _safe(f'<div class="dj-page-header__actions">{content}</div>')


_EMPTY_ACTIONS = _re_filters.compile(r'<div class="dj-page-header__actions">\s*</div>\s*')


def render_page_header(kw: Mapping[str, object], breadcrumb_content: str, actions_html: str) -> str:
    """The one ``{% page_header %}`` renderer, called by the handler and the Django tag (#4331).

    ``kw`` holds plain operands (a ``mark_safe`` title is escaped, D3). ``actions_html`` is the
    inner content of the actions slot; a slot that drew nothing draws no ``<div>`` at all."""
    e_title = conditional_escape(str(kw.get("title", "")))
    e_subtitle = conditional_escape(str(kw.get("subtitle", "")))
    e_description = conditional_escape(str(kw.get("description", "")))
    e_custom_class = attr_text(str(kw.get("custom_class", "")))

    cls = "dj-page-header"
    if e_custom_class:
        cls += f" {e_custom_class}"

    breadcrumb_html = ""
    if breadcrumb_content.strip():
        breadcrumb_html = f'<div class="dj-page-header__breadcrumb">{breadcrumb_content}</div>'
    title_html = f'<h1 class="dj-page-header__title">{e_title}</h1>' if e_title else ""
    subtitle_html = f'<p class="dj-page-header__subtitle">{e_subtitle}</p>' if e_subtitle else ""
    description_html = (
        f'<p class="dj-page-header__description">{e_description}</p>' if e_description else ""
    )
    actions_section = ""
    if actions_html.strip():
        actions_section = PageHeaderActionsHandler().render([], actions_html, {})

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


class PageHeaderHandler:
    """Block handler for {% page_header title=... %}...{% endpage_header %}"""

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        kw = _parse_args(args, context)
        # In the Rust engine, nested block tags render inline: page_header_actions draws its own
        # wrapper div, so the actions are told from the breadcrumb content by that wrapper.
        marker = '<div class="dj-page-header__actions">'
        breadcrumb_content, actions_section = content, ""
        if marker in content:
            idx = content.index(marker)
            breadcrumb_content, actions_section = content[:idx], content[idx:]
        if _EMPTY_ACTIONS.fullmatch(actions_section):
            actions_section = ""
        html = render_page_header(kw, breadcrumb_content, "")
        # The slot sits last in the row, after the text block.
        tail = "</div></header>"
        if actions_section and html.endswith(tail):
            html = html[: -len(tail)] + actions_section + tail
        return _safe(html)


def python_operands(params: Mapping[str, object]) -> dict[str, object]:
    """A Django node's resolved arguments as a native handler's ``render_kwargs`` reads them
    (#4331): what ``_parse_args`` makes of the same operands under the Rust engine (a scalar, a
    tuple, a lazy or marked-safe string through ``plain_operand``; a list, dict or object as the
    JSON-able value of the engine's text for it). An unresolved variable is ``""`` on both sides.
    """
    return {key: _python_operand(value) for key, value in params.items()}


#: How a ``{% sidebar_item %}`` with no ``active=`` of its own asks the ``{% sidebar %}`` that
#: holds it whether the sidebar's ``active`` names it (#4331), as ``{% nav_item %}`` asks its menu
#: (``_NAV_ACTIVE_MARK``). The item is rendered before the sidebar, so it leaves the hex of its
#: ``[id, href]`` pair (only ``0-9a-f``: no value can end the comment, open another, or read as
#: markup) in a comment right before its link; the sidebar reads and removes every one, adding the
#: active class to the matching links. Outside a sidebar the comment is invisible.
_SIDEBAR_ACTIVE_MARK = _re_filters.compile(
    r'<!--djust-sidebar-key:([0-9a-f]*)--><(a|button) class="dj-sidebar__link"'
)


class SidebarHandler:
    """Block handler for {% sidebar %}...{% endsidebar %}

    Renders a collapsible sidebar navigation with menu items, sections,
    and mobile drawer support. The one renderer of the tag on both engines (#4331): the
    Django node hands ``render_kwargs`` its resolved arguments and rendered body.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: Mapping[str, object], content: str) -> str:
        sidebar_id = attr_text(kw.get("id", "sidebar"))
        collapsed = kw.get("collapsed", False)
        title = kw.get("title", "")
        toggle_event = attr_text(kw.get("toggle_event", "toggle_sidebar"))
        custom_class = attr_text(kw.get("class", ""))
        # ``component_id=`` names which ``Sidebar`` the toggle drives (#4134).
        cid_attr = component_id_attr(kw.get("component_id"))

        collapsed_cls = " dj-sidebar--collapsed" if collapsed else ""
        cls = f"dj-sidebar{collapsed_cls}"
        if custom_class:
            cls += f" {custom_class}"

        header_html = ""
        if title:
            header_html = (
                f'<div class="dj-sidebar__header">'
                f'<span class="dj-sidebar__title">{conditional_escape(title)}</span>'
                f'<button class="dj-sidebar__toggle" dj-click="{toggle_event}"{cid_attr}>'
                f"&#9776;</button></div>"
            )

        backdrop = f'<div class="dj-sidebar__backdrop" dj-click="{toggle_event}"{cid_attr}></div>'

        # The sidebar's ``active`` reaches the items that left a marker: each marker and the
        # opening of the link after it become that opening, with the active class for a match.
        # Only literals are written, so ``content`` stays the rendered body.
        active = kw.get("active", "")

        def _link(match: Any) -> str:
            hit = _nav_key_matches(match.group(1), active)
            element = "a" if match.group(2) == "a" else "button"
            return f'<{element} class="dj-sidebar__link' + (
                ' dj-sidebar__item--active"' if hit else '"'
            )

        content = _SIDEBAR_ACTIVE_MARK.sub(_link, content)

        return _safe(
            f'<nav class="{cls}" id="{sidebar_id}" role="navigation">'
            f"{header_html}"
            f'<ul class="dj-sidebar__menu">{content}</ul>'
            f"{backdrop}</nav>"
        )


class SidebarItemHandler:
    """Block handler for {% sidebar_item %}...{% endsidebar_item %}

    A non-blank body is the item's submenu, whatever it holds (nested items at any depth, or
    other markup). The item is active when its own ``active=`` is ``True`` or names its ``id`` /
    ``href``; without ``active=`` the enclosing ``{% sidebar active=... %}`` decides
    (``_SIDEBAR_ACTIVE_MARK``). One renderer on both engines (#4331).
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: Mapping[str, object], content: str) -> str:
        label = conditional_escape(kw.get("label", ""))
        href = conditional_escape(kw.get("href", "#"))
        icon = kw.get("icon", "")
        item_id = conditional_escape(kw.get("id", ""))
        event = kw.get("event", "")
        active = conditional_escape(kw.get("active", ""))

        is_active = (
            kw.get("active") is True or (item_id and item_id == active) or (href and href == active)
        )
        active_cls = " dj-sidebar__item--active" if is_active else ""
        # No ``active=`` of its own: the enclosing sidebar's ``active`` decides. Written as a
        # literal around the escaped hex key, which only holds ``0-9a-f``.
        mark = ""
        if "active" not in kw:
            mark = (
                "<!--djust-sidebar-key:"
                f"{conditional_escape(_nav_active_key(kw.get('id', ''), kw.get('href', '#')))}-->"
            )

        icon_html = ""
        if icon:
            icon_html = f'<span class="dj-sidebar__icon">{conditional_escape(icon)}</span>'

        has_children = content.strip() != ""

        if event:
            trigger = (
                f'<button class="dj-sidebar__link{active_cls}" '
                f'dj-click="{attr_text(event)}">'
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
                f"{mark}{trigger}"
                f'<ul class="dj-sidebar__submenu">{content}</ul></li>'
            )

        return _safe(f'<li class="dj-sidebar__item">{mark}{trigger}</li>')


class SidebarSectionHandler:
    """Inline handler for {% sidebar_section label="..." %}"""

    def render(self, args: list[str], context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context))

    def render_kwargs(self, kw: Mapping[str, object]) -> str:
        label = conditional_escape(kw.get("label", ""))
        return _safe(
            f'<li class="dj-sidebar__section">'
            f'<span class="dj-sidebar__section-label">{label}</span></li>'
        )


class NavMenuHandler:
    """Block handler for {% nav_menu %}...{% endnav_menu %}

    Renders a top horizontal navigation bar with brand, hamburger toggle,
    and responsive mobile collapse. The one renderer on both engines (#4331).
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: Mapping[str, object], content: str) -> str:
        nav_id = attr_text(kw.get("id", "nav-menu"))
        brand = kw.get("brand", "")
        brand_href = data_url_attr(kw.get("brand_href", "/"))
        toggle_event = attr_text(kw.get("toggle_event", "toggle_nav"))
        mobile_open = kw.get("mobile_open", False)
        # A falsy ``class`` (``None``, ``False``) adds nothing, as in the Django tag (#4127);
        # escaping it first would turn ``None`` into the class ``None``.
        raw_class = kw.get("class", "")
        custom_class = attr_text(raw_class) if raw_class else ""

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
            f'<button class="dj-nav__hamburger" dj-click="{toggle_event}"'
            f'{component_id_attr(kw.get("component_id"))} aria-label="Toggle navigation">'
            f"&#9776;</button>"
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
    The one renderer on both engines (#4331).
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: Mapping[str, object], content: str) -> str:
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
        # Any other body is ignored, so an item with an ``event`` and a text body stays a
        # clickable button; "any non-blank body" dropped that ``dj-click`` (#3669).
        # The body is rendered HTML (on both engines: the Django node calls ``render_kwargs``,
        # #4331), so a child is recognised by the markup this handler draws (a literal
        # ``<li class="dj-nav__item">`` in the body matches too). A sub-item at any depth, or
        # inside ``{% if %}`` / ``{% for %}``, counts (#3873 / #3878).
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
                f'<button class="dj-nav__link" dj-click="{attr_text(event)}">'
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

    #: The whole body is placed in the output, so its dj-model bindings count.
    EMITS_BODY = True

    def _render_root_parts(
        self, args: list[str], content: str, context: dict[str, object]
    ) -> _RootLayoutParts:
        return self._root_parts(_parse_args(args, context), content)

    def _root_parts(self, kw: dict[str, object], content: str) -> _RootLayoutParts:
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

    def render_kwargs(self, kw: dict[str, object], content: str) -> str:
        """The Django engine's ``{% app_shell %}`` (#4331): the same markup from the tag's operands
        as Python values. It takes no part in the Rust engine's root-proof protocol, which calls
        ``_render_root_parts`` on the Rust engine's own render of the tag."""
        return _root_layout_html(content, self._root_parts(kw, content))


class AppSidebarHandler:
    """Block handler for the layout sidebar."""

    #: The whole body is placed in the output, so its dj-model bindings count.
    EMITS_BODY = True

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

    #: The whole body is placed in the output, so its dj-model bindings count.
    EMITS_BODY = True

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

    #: The whole body is placed in the output, so its dj-model bindings count.
    EMITS_BODY = True

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


class _ToolbarPartsParser(HTMLParser):
    """The top-level ``{% toolbar_separator %}`` / ``{% toolbar_overflow %}`` elements of a
    rendered toolbar body, as ``(start, end)`` offsets (#4331).

    Only an element at the top of the body counts: a separator a template puts inside its own
    ``<span>`` belongs to that element. The body is read, never reserialized; an element left
    open runs to the end of the body.
    """

    _VOID_TAGS = _AppShellSlotParser._VOID_TAGS
    _CLASSES = frozenset({"dj-toolbar__separator", "dj-toolbar__overflow"})

    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=False)
        self._content = content
        self._line_offsets = [0]
        for line in content.split("\n"):
            self._line_offsets.append(self._line_offsets[-1] + len(line) + 1)
        self._stack: list[str] = []
        self._open: int | None = None
        self.parts: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._VOID_TAGS:
            return
        if not self._stack and tag == "div" and dict(attrs).get("class") in self._CLASSES:
            self._open = self._offset()
        self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._stack:
            return
        index = len(self._stack) - 1 - self._stack[::-1].index(tag)
        del self._stack[index:]
        if not self._stack and self._open is not None:
            end = self._content.find(">", self._offset())
            self.parts.append((self._open, len(self._content) if end < 0 else end + 1))
            self._open = None

    def finish(self) -> list[tuple[int, int]]:
        self.feed(self._content)
        self.close()
        if self._open is not None:
            self.parts.append((self._open, len(self._content)))
            self._open = None
        return self.parts


#: An HTML comment in a toolbar body: the Rust engine's ``{% if %}`` boundary markers, an author's.
_HTML_COMMENT = _re_filters.compile(r"<!--.*?-->", _re_filters.DOTALL)


def _toolbar_groups(content: str) -> str:
    """``content`` with each run of children between two separators (or a separator and an
    overflow menu, or an end of the body) wrapped in one ``dj-toolbar__group`` (#4331). A run that
    is only whitespace and HTML comments draws no group: an ``{% if %}`` that rendered nothing
    leaves the Rust engine's ``<!--dj-if ...-->`` pair there, which is kept (VDOM patching reads
    it) but not wrapped, so no empty group is drawn where the Django engine draws none. The
    separators and the overflow menu stay where they are, outside every group. Only literal
    markup is added around slices of ``content``."""
    out: list[str] = []
    pos = 0
    for start, end in [*_ToolbarPartsParser(content).finish(), (len(content), len(content))]:
        run = content[pos:start]
        if _HTML_COMMENT.sub("", run).strip():
            out.append('<div class="dj-toolbar__group">' + run + "</div>")
        else:
            out.extend(_HTML_COMMENT.findall(run))
        out.append(content[start:end])
        pos = end
    return "".join(out)


class ToolbarHandler:
    """Block handler for {% toolbar %}...{% endtoolbar %}

    Horizontal action bar with grouped buttons, separators, overflow menu. The one renderer
    on both engines (#4331): the children between two ``{% toolbar_separator %}`` form one
    ``dj-toolbar__group``. With no ``id=`` the id is derived from the arguments
    (``toolbar-<hash>``), so it repeats on every render of the same call (#4108) and is the same
    on both engines.
    """

    def render(self, args: list[str], content: str, context: dict[str, object]) -> str:
        return self.render_kwargs(_parse_args(args, context), content)

    def render_kwargs(self, kw: Mapping[str, object], content: str) -> str:
        if "id" in kw:
            toolbar_id = attr_text(kw["id"])
        else:
            # The Django tags' rule for a default id (#4108), over the operands as both engines
            # hand them here; there is no per-render count to tell two identical calls apart.
            from djust.components.templatetags.djust_components import _stable_id

            toolbar_id = attr_text(_stable_id("toolbar", sorted(kw.items(), key=repr)))
        custom_class = attr_text(kw.get("class", ""))
        size = attr_text(kw.get("size", "md"))
        variant = attr_text(kw.get("variant", "default"))

        cls = f"dj-toolbar dj-toolbar--{size} dj-toolbar--{variant}"
        if custom_class:
            cls += f" {custom_class}"

        body = _toolbar_groups(content)
        return _safe(f'<div class="{cls}" id="{toolbar_id}" role="toolbar">{body}</div>')


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
    #: Parameters that are numeric by contract (``_number``).
    NUMERIC: tuple[str, ...] = ()

    def render(self, args: list[str], context: dict[str, object]) -> str:
        import base64

        kw = _parse_args(args, context)
        for numeric in self.NUMERIC:
            if numeric in kw:
                kw[numeric] = _number(kw[numeric], "filter_" + self.KIND, numeric)
        payload = _json.dumps(kw, default=str, separators=(",", ":"))
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
    NUMERIC = ("debounce",)


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
    """``{% form_array %}``: the one renderer of its markup on both engines (#4331).

    The tag repeats its body once per row, with ``row``, ``row_index`` and ``forloop`` pushed
    (#930), so its body cannot be rendered first: it takes the raw-body route on the Rust engine
    (``RAW_BODY_TAGS``), where the Django node renders each row's body and hands the rendered rows
    to :meth:`render_kwargs`, as it does under Django's engine. ``row_bodies`` is ``None`` for a
    blank body: each row is then the default text input.
    """

    def render_kwargs(
        self, kw: dict[str, object], rows: list[Any], row_bodies: list[str] | None
    ) -> str:
        name = kw.get("name", "items")
        min_rows = kw.get("min", 1)
        max_rows = kw.get("max", 10)
        add_event = kw.get("add_event", "add_row")
        remove_event = kw.get("remove_event", "remove_row")
        add_label = kw.get("add_label", "Add row")
        custom_class = kw.get("class", "")

        e_name = attr_text(name)
        e_add_event = attr_text(add_event)
        e_remove_event = attr_text(remove_event)
        e_add_label = conditional_escape(str(add_label))
        e_class = attr_text(custom_class)

        try:
            min_rows = int(cast("str | int | float", _number(min_rows)))
        except (ValueError, TypeError):
            min_rows = 1
        try:
            max_rows = int(cast("str | int | float", _number(max_rows)))
        except (ValueError, TypeError):
            max_rows = 10

        classes = ["dj-form-array"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        row_count = len(rows)
        can_add = row_count < max_rows
        can_remove = row_count > min_rows

        rows_html = []
        for i, row in enumerate(rows):
            remove_html = ""
            if can_remove:
                remove_html = (
                    f'<button class="dj-form-array__remove" type="button" '
                    f'dj-click="{e_remove_event}" dj-value-value="{i}" '
                    f'aria-label="Remove row {i + 1}">&times;</button>'
                )
            if row_bodies is not None:
                # The row's body, rendered by the template engine with the row pushed.
                cell = row_bodies[i]
            else:
                # ``value="..."`` is an attribute: escaped whatever the member is marked (#3904).
                val = attr_text(row.get("value", "") if isinstance(row, Mapping) else row)
                cell = (
                    f'<input type="text" name="{e_name}[{i}]" value="{val}" '
                    f'class="dj-form-array__input">'
                )
            rows_html.append(
                f'<div class="dj-form-array__row" data-index="{i}">{cell}{remove_html}</div>'
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
    ("page_header", "endpage_header", PageHeaderHandler()),
    ("page_header_actions", "endpage_header_actions", PageHeaderActionsHandler()),
]

#: Block tags that take the raw-body route (their Django node reads its children, so the Rust engine
#: cannot render the body first): ``tabs`` keeps only its ``tab`` children, ``split_pane`` parses two
#: segments (both registered this way before 2.0), ``form_array`` renders its body once per row with
#: the row pushed (#930; its native twin, which drew an input and dropped the body, is gone,
#: #4331). The other children-reading parents keep their native twins (see the module docstring).
#: Pinned by
#: ``test_raw_body_tags_are_exactly_the_bridged_tags_that_read_their_children``.
RAW_BODY_TAGS = frozenset({"form_array", "split_pane", "tabs"})

#: Separator tokens of a raw-body tag (``{% pane %}`` inside ``{% split_pane %}``). For template path
#: extraction the segments are one body rendered with the same context, so the separator is dropped.
RAW_BODY_SEGMENT_TAGS: Mapping[str, str] = MappingProxyType({"split_pane": "pane"})


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

    # Handed their operands typed, as the component handlers are (#3882, #3883).
    _call_handler = _with_typed_block_operands(CallTagHandler())
    register_block_tag_handler("call", "endcall", _call_handler)
    register_block_tag_handler("component", "endcomponent", _call_handler)
    register_block_tag_handler("slot", "endslot", _with_typed_block_operands(SlotTagHandler()))
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
