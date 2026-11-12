"""Rust template engine registration for djust-components.

One render path per component tag (S3 of ``docs/reviews/2026-11-11-djust-2-consolidated.md``,
decided 2026-11-12). Every ``djust_components`` tag renders under the Rust engine through the
``{% load %}`` bridge (#2547, ``djust.template_libraries``): the engine calls the Python tag's OWN
compile function on a synthetic parser and renders the Django node it returns, so the output is
the Python tag's by construction, under ``DjangoTemplates``, ``DjustTemplateBackend`` and a
LiveView alike. The tags are registered here, at ``ready()``, so a Rust template keeps using them
without ``{% load djust_components %}``, exactly as it did when each had a native twin.

Until 2.0 about 190 tags also had a hand-written native handler in this module (about 11,000
lines) that re-implemented the Python tag; the two drifted (#3663: 117 of 208 tags rendered
different markup), and the argument wrappers that made the twins accept what the Python tags
accept were a third copy of each tag's grammar. Those handlers are gone.

What is left native, and why
----------------------------
A native handler stays only where a benchmark earns it or the bridge cannot serve the tag, and
then with a parity test against the single path:

* ``app_shell`` / ``app_sidebar`` / ``app_header`` / ``app_content`` (``NATIVE_BLOCK_HANDLERS``).
  They implement the core-layout root-proof protocol (``_render_root_parts``): the engine calls
  them in place of ``render`` when it tracks the page root and accepts only exact copies of body
  slices, which is what lets a partial update prove what it replaces
  (``crates/djust_templates/src/registry.rs``, ``core_root_layout_object``). A bridged Django node
  cannot take part in that protocol, and the protocol is not changed here. Their parity with the
  Python tags is pinned in ``python/tests/test_component_single_render_path.py``.

How each tag is bridged
-----------------------
* An inline tag (``simple_tag``, ``inclusion_tag``, a raw tag that builds its node from its own
  token): ``LibraryTagHandler``.
* A block tag whose node renders its body as it is (a wrapper, conditional or not):
  ``LibraryBlockTagHandler``. The Rust engine renders the body first, as it did for the native
  handler, and Django's node wraps it.
* A block tag whose node READS ITS CHILDREN (``RAW_BODY_TAGS``: it iterates its nodelist for child
  nodes, parses more than one segment, or renders its body in a pushed context):
  ``LibraryRawBlockTagHandler``. The body is handed over un-rendered and Django parses and renders
  it, with this library added to the parser so the child tags resolve without a ``{% load %}``.
  This is the route ``tabs`` and ``split_pane`` already took. Cost: the body is rendered by Django's
  engine, so a tag only the Rust engine knows cannot be used inside it.
"""

from __future__ import annotations

import re as _re
from html.parser import HTMLParser
from typing import Any, cast

from django.utils.safestring import mark_safe

from djust.components.utils import attr_text
from djust.template_tags import TYPED_ARGS_KEY


def _safe(html: str) -> str:
    """Typed wrapper over Django's ``mark_safe`` (``mark_safe`` is untyped, see ``djust._html``)."""
    return cast(str, mark_safe(html))


# ---------------------------------------------------------------------------
# Operands of the native layout handlers
# ---------------------------------------------------------------------------


def _quoted(text: str) -> bool:
    """``text`` is a quoted literal: it opens and closes with the same quote character."""
    return len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]


def _parse_args(args: list[str], context: dict[str, object]) -> dict[str, object]:
    """The ``key=value`` operands of a native handler, as values.

    An operand the engine resolved is taken from the typed channel (``TYPED_ARGS_KEY``): the
    handlers are wrapped in ``_TypedBlockOperands``, which declares ``TYPED_ARGS``, so the engine
    hands over every operand that resolved as the Python value it is. That is the value, not its
    text: it is never parsed again (``"false"`` stays a string, ``"007"`` stays ``"007"``, #3883)
    and never looked up in the context a second time (a value that happens to be a variable's name
    stays the value, #3882). Only a quoted literal (``id="shell"``) and a bare literal the engine
    hands over as written (``True``, a number) are read from the text.
    """
    typed = context.get(TYPED_ARGS_KEY)
    if not isinstance(typed, dict):
        typed = {}
    result: dict[str, object] = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip()
        if key in typed:
            result[key] = typed[key]
        elif _quoted(val):
            result[key] = val[1:-1]
        elif val in ("True", "true"):
            result[key] = True
        elif val in ("False", "false"):
            result[key] = False
        elif val in ("None", "null"):
            result[key] = None
        else:
            result[key] = val
    return result


def _typed_args(context: dict[str, object]) -> dict[str, object]:
    """The operands the engine resolved, as typed Python values, by parameter name (#3756)."""
    typed = context.get(TYPED_ARGS_KEY)
    return typed if isinstance(typed, dict) else {}


class _TypedOperands:
    """``handler``, declaring ``TYPED_ARGS`` so the engine hands it its operands as values.

    Everything else the engine reads off a handler (``RESOLVE_ARG_POSITIONS``, ...) is forwarded.
    """

    TYPED_ARGS = True
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
        ``__getattr__`` to the handler and skip ``_adjust``.
        """
        args, kwargs = self._adjust(args, kwargs)
        return self._handler._render_root_parts(*args, **kwargs)

    def _adjust(self, args: tuple, kwargs: dict) -> tuple[tuple, dict]:
        return args, kwargs

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not found on the wrapper itself.
        if name == "_handler":
            raise AttributeError(name)
        return getattr(self._handler, name)


class _TypedBlockOperands(_TypedOperands):
    """A BLOCK handler, handed an unresolved operand the way an inline handler is (#3871).

    A block tag's operands go through a resolver whose contract is "unresolved => keep the raw
    token". The engine hands every operand that DID resolve on the typed channel, keyed by
    parameter name; this wrapper blanks the rest, so ``{% app_shell id=nope %}`` reads ``id`` as
    empty, as the Django tag does, rather than as the variable's name.

    The engine's core-layout check peels this exact class (``core_root_layout_object``): it must
    keep its name and module.
    """

    TYPED_ARGS_STANDINS_ONLY = False

    def _adjust(self, args: tuple, kwargs: dict) -> tuple[tuple, dict]:
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


_KEYWORD = _re.compile(r"\w+")


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
# The core layout handlers (kept native: the root-proof protocol)
# ---------------------------------------------------------------------------


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


#: The native handlers that remain (see the module docstring): the four core layout tags.
NATIVE_BLOCK_HANDLERS = [
    ("app_shell", "endapp_shell", AppShellHandler()),
    ("app_sidebar", "endapp_sidebar", AppSidebarHandler()),
    ("app_header", "endapp_header", AppHeaderHandler()),
    ("app_content", "endapp_content", AppContentHandler()),
]

#: Block tags whose Django node reads its children, so the Rust engine cannot render the body
#: first: it iterates its nodelist for child nodes (``accordion`` keeps its ``accordion_item``s,
#: ``sidebar`` its sections and items, ``nav_menu`` its ``nav_item``s, ``toolbar`` its separators
#: and overflow, ``filter_bar`` its direct children, ``tabs`` its ``tab``s), parses more than one
#: body segment (``split_pane``'s ``{% pane %}``), renders its body once per row in a pushed
#: context (``form_array``), or reads back what a child wrote into the context while the body
#: rendered (``page_header`` and its ``page_header_actions``). They take the raw-body route. The set is derived from the tags'
#: source and pinned by ``test_raw_body_tags_are_exactly_the_tags_that_read_their_children``.
RAW_BODY_TAGS = frozenset(
    {
        "accordion",
        "filter_bar",
        "form_array",
        "nav_menu",
        "page_header",
        "sidebar",
        "split_pane",
        "tabs",
        "toolbar",
    }
)


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

    native = {name for name, _end, _handler in NATIVE_BLOCK_HANDLERS}
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

    # The core layout handlers: each operand accepted exactly as the Python tag accepts it
    # (#3676), and handed over as a value (#3871). The engine's core-layout check peels exactly
    # these two wrappers.
    for tag_name, end_tag, handler in NATIVE_BLOCK_HANDLERS:
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
