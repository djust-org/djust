"""Static event bindings of a template, with source locations (ADR-037 D2).

A template is compiled with Django's own parser, never rendered. Its node tree
is flattened into the markup it can produce: ``{% extends %}`` and constant
``{% include %}`` are followed, every ``{% if %}`` branch and ``{% for %}`` body
is kept, and anything whose output is not known statically (a variable, a
dynamic include, a third-party tag that renders markup) becomes an opaque
placeholder. That markup is then parsed as HTML, so a binding is an attribute
on an element, not a regex match, and each keeps the file and line it came from.

What the browser sends for each directive is described once, in
``DIRECTIVES`` below, from the client sources it names. The handler side is
never re-derived here: ``checks/bindings.py`` resolves names through
``_parameter_metadata.declared_handlers`` and arguments through the runtime's
own policy, coercion and strict contract.
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

# Placeholders for output that is not known statically. Private-use code
# points never occur in templates, contain no newline, and are opaque to the
# HTML parser: a value containing one is dynamic.
VALUE = "\ue000"  # A variable or value-producing tag.
BRANCH = "\ue001"  # The boundary of an {% if %} branch or {% for %} body.
OPAQUE = "\ue002"  # A tag whose rendered markup is unknown.
_PLACEHOLDERS = (VALUE, BRANCH, OPAQUE)

_MAX_DEPTH = 24

# The server's event-name guard (``security/event_guard.py``).
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
# The strict collector's rule for a dj-value-* name (08-event-parsing.js).
STRICT_VALUE_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
_UNSAFE_KEYS = frozenset({"__proto__", "constructor", "prototype"})
# Routing context: the server routes on these, they are never arguments.
ROUTING_KEYS = frozenset({"view_id", "component_id"})

# Wire hints (08-event-parsing.js ``extractTypedParams`` / strict collector).
LEGACY_HINTS = frozenset(
    {"int", "integer", "float", "number", "bool", "boolean", "json", "object", "array", "list"}
)
# The declared types each hint may produce under the strict policy
# (11-event-handler.js ``_WIRE_HINT_TYPES``); ``json`` fits any type.
STRICT_HINT_TYPES = {
    "int": ("int", "float", "Decimal"),
    "integer": ("int", "float", "Decimal"),
    "float": ("float",),
    "number": ("float",),
    "bool": ("bool",),
    "boolean": ("bool",),
    "array": ("list",),
    "list": ("list",),
    "json": None,
    "object": ("Any",),
}


@dataclass(frozen=True)
class Directive:
    """How the browser turns one ``dj-*`` attribute into an event."""

    #: ``call``: ``name`` or ``name(args)``; ``raw``: the whole value is the
    #: event name; ``shortcut``: ``[mod+]key:name[:prevent]`` entries.
    grammar: str
    #: ``element``: data-*, dj-params and dj-value-* of the element (legacy);
    #: ``field``: the triggering form field; ``form``: the form's fields;
    #: ``fixed``: only ``generated``.
    payload: str
    #: Generated names the client adds.
    generated: tuple[str, ...] = ()
    #: Generated only under the legacy policy (the strict binder never sends it).
    legacy_only: tuple[str, ...] = ()
    #: Whether the client attaches owner context (component or embedded view).
    #: Without it the event always reaches the page's root view.
    owner_context: bool = True


_FIELD = ("value", "field")
DIRECTIVES: dict[str, Directive] = {
    # 09-event-binding.js; parseEventHandler grammar.
    "dj-click": Directive("call", "element"),
    "dj-mouseenter": Directive("call", "element"),
    "dj-mouseleave": Directive("call", "element"),
    "dj-poll": Directive("call", "element", owner_context=False),
    "dj-change": Directive("call", "field", _FIELD, ("_target",)),
    "dj-input": Directive("call", "field", _FIELD, ("_target",)),
    "dj-blur": Directive("call", "field", _FIELD),
    "dj-focus": Directive("call", "field", _FIELD),
    "dj-paste": Directive("call", "fixed", ("text", "html", "has_files", "files")),
    "dj-window-keydown": Directive("call", "element", ("key", "code")),
    "dj-window-keyup": Directive("call", "element", ("key", "code")),
    "dj-window-click": Directive("call", "element", ("clientX", "clientY")),
    "dj-window-scroll": Directive("call", "element", ("scrollX", "scrollY")),
    "dj-window-resize": Directive("call", "element", ("innerWidth", "innerHeight")),
    "dj-document-keydown": Directive("call", "element", ("key", "code")),
    "dj-document-keyup": Directive("call", "element", ("key", "code")),
    "dj-document-click": Directive("call", "element", ("clientX", "clientY")),
    # Raw grammar: the whole value is the event name.
    "dj-submit": Directive("raw", "form", (), ("_target",)),
    "dj-keydown": Directive("raw", "field", ("key", "code", "value", "field")),
    "dj-keyup": Directive("raw", "field", ("key", "code", "value", "field")),
    "dj-click-away": Directive("raw", "element"),
    "dj-mounted": Directive("raw", "element"),
    "dj-shortcut": Directive("shortcut", "element", ("key", "code", "shortcut")),
    "dj-auto-recover": Directive(
        "raw", "fixed", ("_form_values", "_data_attrs"), owner_context=False
    ),
    "dj-dialog-close-event": Directive("raw", "fixed", owner_context=False),
    "dj-viewport-top": Directive("raw", "fixed", ("edge",), owner_context=False),
    "dj-viewport-bottom": Directive("raw", "fixed", ("edge",), owner_context=False),
    "dj-mutation": Directive("raw", "fixed", ("mutation",), owner_context=False),
    "dj-copy-event": Directive("raw", "fixed", ("text",), owner_context=False),
}

#: Every other ``dj-*`` attribute the client reads. Their values are not
#: server event names (a JS hook, URL, field, selector, option or modifier);
#: a test pins that each attribute the client reads is in exactly one table.
NON_EVENT_ATTRIBUTES = frozenset(
    {
        "dj-audio",
        "dj-cloak",
        "dj-confirm",
        "dj-copy",
        "dj-copy-class",
        "dj-copy-feedback",
        "dj-debounce",
        "dj-dialog",
        "dj-disable-with",
        "dj-flip",
        "dj-force-value",
        "dj-form-pending",
        "dj-hook",
        "dj-id",
        "dj-ignore-attrs",
        "dj-key",
        "dj-lazy",
        "dj-liveview-root",
        "dj-loading",
        "dj-lock",
        "dj-model",
        "dj-mutation-attr",
        "dj-mutation-debounce",
        "dj-navigate",
        "dj-no-recover",
        "dj-no-submit",
        "dj-offline-hide",
        "dj-params",
        "dj-paste-suppress",
        "dj-patch",
        "dj-patch-reload",
        "dj-poll-interval",
        "dj-prefetch",
        "dj-remove",
        "dj-remove-duration",
        "dj-root",
        "dj-scroll-into-view",
        "dj-shortcut-in-input",
        "dj-sticky-root",
        "dj-sticky-scroll",
        "dj-sticky-slot",
        "dj-sticky-view",
        "dj-stream-mode",
        "dj-target",
        "dj-throttle",
        "dj-track-static",
        "dj-transition",
        "dj-trigger-action",
        "dj-update",
        "dj-upload",
        "dj-upload-drop",
        "dj-view",
        "dj-view-transitions",
        "dj-viewport",
        "dj-virtual",
        "dj-virtual-key-attr",
    }
)

#: Only these support a JS command list (``[["push", {...}], ...]``).
_JS_COMMAND_DIRECTIVES = frozenset({"dj-click"})
#: Payloads that are not fully known: every name the client may add is listed,
#: but some are sent only sometimes (e.g. ``attrs`` or ``added``/``removed``).
_PARTIAL_FIXED = frozenset({"dj-mutation"})
_FORM_FIELD_TAGS = frozenset({"input", "select", "textarea"})
_NON_DATA_INPUTS = frozenset({"submit", "button", "reset", "image"})
_VOID = frozenset(
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


def directive_for(attribute: str) -> Optional[tuple[str, Directive]]:
    """The directive an attribute name selects (``dj-keydown.enter`` -> keydown)."""
    base = attribute.split(".", 1)[0]
    directive = DIRECTIVES.get(base)
    return (base, directive) if directive is not None else None


def is_dynamic(text: str) -> bool:
    return any(mark in text for mark in _PLACEHOLDERS)


# ---------------------------------------------------------------------------
# Value grammar (08-event-parsing.js parseEventHandler / parseArguments)
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def _single_argument(text: str) -> Any:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return re.sub(
            r"\\(.)",
            lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)),
            text[1:-1],
        )
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "null":
        return None
    if _NUMBER.match(text):
        return float(text) if any(c in text for c in ".eE") else int(text)
    return text


def _split_arguments(text: str) -> list[Any]:
    args: list[Any] = []
    current = ""
    quote: Optional[str] = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == "\\" and i + 1 < len(text):
                current += text[i : i + 2]
                i += 2
                continue
            if char == quote:
                quote = None
            current += char
        elif char in "'\"":
            quote = char
            current += char
        elif char == ",":
            if current.strip():
                args.append(_single_argument(current.strip()))
            current = ""
        else:
            current += char
        i += 1
    if current.strip():
        args.append(_single_argument(current.strip()))
    return args


def parse_call(value: str) -> tuple[str, list[Any]]:
    """``(name, positional args)`` exactly as the client parses a call value."""
    text = value.strip()
    paren = text.find("(")
    if paren == -1:
        return text, []
    name = text[:paren].strip()
    close = text.rfind(")")
    if not _IDENTIFIER.match(name) or close < paren:
        return text, []
    inner = text[paren + 1 : close].strip()
    return name, _split_arguments(inner) if inner else []


# ---------------------------------------------------------------------------
# Flattening the compiled template into producible markup
# ---------------------------------------------------------------------------


@dataclass
class Gap:
    """Markup the scan could not see: the page's event graph is incomplete."""

    file: str
    line: int
    reason: str


@dataclass
class _Flat:
    text: list[str] = field(default_factory=list)
    length: int = 0
    # (offset in the flattened text, source file, source line at that offset)
    segments: list[tuple[int, str, int]] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # file -> source

    def emit(self, text: str, file: str = "", line: int = 0) -> None:
        if not text:
            return
        if file:
            self.segments.append((self.length, file, line))
        self.text.append(text)
        self.length += len(text)


#: djust tags whose output carries no binding this owner handles: an embedded
#: child view is checked as its own owner, and client configuration has none.
_SELF_CHECKED_TAGS = frozenset({"live_render", "djust_client_config"})

_TRANSPARENT_MODULES = (
    "django.template.defaulttags",
    "django.template.loader_tags",
    "django.templatetags.",
)


def _node_line(node: Any) -> int:
    token = getattr(node, "token", None)
    return getattr(token, "lineno", 0) or 0


def _node_file(node: Any, default: str) -> str:
    origin = getattr(node, "origin", None)
    name = getattr(origin, "name", None)
    return name if isinstance(name, str) and not name.startswith("<") else default


class _Flattener:
    def __init__(self, engine: Any, flat: _Flat) -> None:
        self.engine = engine
        self.flat = flat

    def template(self, template: Any, file: str, blocks: dict[str, Any], depth: int) -> None:
        from django.template.loader_tags import ExtendsNode

        nodelist = template.nodelist
        extends = next((n for n in nodelist if isinstance(n, ExtendsNode)), None)
        if extends is None:
            self.nodes(nodelist, file, blocks, depth)
            return
        parent = extends.parent_name
        name = parent.var if not parent.filters else None
        line = _node_line(extends)
        if not isinstance(name, str) or depth >= _MAX_DEPTH:
            self.flat.gaps.append(Gap(file, line, "{% extends %} names a dynamic template"))
            # Only this template's own blocks are known.
            for block in extends.blocks.values():
                self.nodes(block.nodelist, file, blocks, depth + 1)
            return
        merged = {**extends.blocks, **blocks}
        loaded = self.load(str(name), file, line)
        if loaded is not None:
            self.template(loaded, _template_file(loaded, str(name)), merged, depth + 1)

    def load(self, name: str, file: str, line: int) -> Any:
        try:
            template = self.engine.get_template(name)
        except Exception as exc:  # noqa: BLE001 -- any loader or syntax failure is a gap
            self.flat.gaps.append(
                Gap(file, line, "cannot load %r: %s" % (name, type(exc).__name__))
            )
            return None
        path = _template_file(template, name)
        if path not in self.flat.files:
            self.flat.files[path] = getattr(template, "source", "") or ""
        return template

    def nodes(self, nodelist: Any, file: str, blocks: dict[str, Any], depth: int) -> None:
        from django.template.base import TextNode, VariableNode
        from django.template.defaulttags import (
            CommentNode,
            CsrfTokenNode,
            ForNode,
            IfNode,
            LoadNode,
            VerbatimNode,
        )
        from django.template.loader_tags import BlockNode, ExtendsNode, IncludeNode

        flat = self.flat
        for node in nodelist:
            node_file = _node_file(node, file)
            line = _node_line(node)
            if isinstance(node, TextNode):
                flat.emit(node.s, node_file, line)
            elif isinstance(node, VariableNode):
                flat.emit(VALUE)
            elif isinstance(node, (CommentNode, LoadNode, ExtendsNode)):
                continue
            elif isinstance(node, VerbatimNode):
                flat.emit(node.content, node_file, line)
            elif isinstance(node, CsrfTokenNode):
                flat.emit(
                    '<input type="hidden" name="csrfmiddlewaretoken" value="%s">' % VALUE,
                    node_file,
                    line,
                )
            elif isinstance(node, BlockNode):
                chosen = blocks.get(node.name, node)
                self.nodes(chosen.nodelist, _node_file(chosen, node_file), blocks, depth + 1)
            elif isinstance(node, IncludeNode):
                self.include(node, node_file, line, depth)
            elif isinstance(node, IfNode):
                for _condition, branch in node.conditions_nodelists:
                    flat.emit(BRANCH)
                    self.nodes(branch, node_file, blocks, depth + 1)
                flat.emit(BRANCH)
            elif isinstance(node, ForNode):
                flat.emit(BRANCH)
                self.nodes(node.nodelist_loop, node_file, blocks, depth + 1)
                flat.emit(BRANCH)
                if node.nodelist_empty:
                    self.nodes(node.nodelist_empty, node_file, blocks, depth + 1)
                    flat.emit(BRANCH)
            elif getattr(type(node), "__module__", "").startswith(_TRANSPARENT_MODULES):
                children = [
                    getattr(node, name, None) for name in getattr(node, "child_nodelists", ())
                ]
                children = [c for c in children if c]
                if children:
                    flat.emit(BRANCH)
                    for child in children:
                        self.nodes(child, node_file, blocks, depth + 1)
                    flat.emit(BRANCH)
                else:
                    flat.emit(VALUE)
            else:
                tag = (getattr(getattr(node, "token", None), "contents", "") or "").split()
                if tag and tag[0] in _SELF_CHECKED_TAGS:
                    flat.emit(VALUE)
                    continue
                flat.gaps.append(
                    Gap(
                        node_file,
                        line,
                        "{%% %s %%} renders markup this check cannot see"
                        % (tag[0] if tag else type(node).__name__),
                    )
                )
                flat.emit(OPAQUE)

    def include(self, node: Any, file: str, line: int, depth: int) -> None:
        expression = node.template
        name = getattr(expression, "var", None)
        if not isinstance(name, str) or expression.filters or depth >= _MAX_DEPTH:
            self.flat.gaps.append(Gap(file, line, "{% include %} names a dynamic template"))
            self.flat.emit(OPAQUE)
            return
        loaded = self.load(name, file, line)
        if loaded is None:
            self.flat.emit(OPAQUE)
            return
        self.template(loaded, _template_file(loaded, name), {}, depth + 1)


def _template_file(template: Any, name: str) -> str:
    origin = getattr(template, "origin", None)
    path = getattr(origin, "name", None)
    return path if isinstance(path, str) and not path.startswith("<") else name


# ---------------------------------------------------------------------------
# Markup parsing
# ---------------------------------------------------------------------------


@dataclass
class Element:
    tag: str
    attrs: list[tuple[str, str]]
    raw: str
    offset: int
    parent: Optional["Element"]
    children: list["Element"] = field(default_factory=list)
    #: Whether the attribute list itself has template output in it, so some
    #: attributes may be absent or unknown (``{% if %}data-x{% endif %}``).
    dynamic_attrs: bool = False

    def attr(self, name: str) -> Optional[str]:
        for key, value in self.attrs:
            if key == name:
                return value
        return None

    def has(self, name: str) -> bool:
        return any(key == name for key, _ in self.attrs)

    def ancestors(self) -> list["Element"]:
        result = []
        node = self.parent
        while node is not None:
            result.append(node)
            node = node.parent
        return result

    def descendants(self) -> list["Element"]:
        result = []
        for child in self.children:
            result.append(child)
            result.extend(child.descendants())
        return result


class _Markup(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.root = Element("#root", [], "", 0, None)
        self.stack = [self.root]
        self.elements: list[Element] = []
        self.opaque_content: list[Element] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        raw = self.get_starttag_text() or ""
        head = raw[len(tag) + 1 :]
        # Template output outside attribute values makes the attribute set
        # conditional; output inside a value only makes that value dynamic.
        outside = re.sub(r"""=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""", "", head)
        element = Element(
            tag,
            [(name, value or "") for name, value in attrs],
            raw,
            self._offset(),
            self.stack[-1],
            dynamic_attrs=is_dynamic(outside),
        )
        self.stack[-1].children.append(element)
        self.elements.append(element)
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if OPAQUE in data or VALUE in data:
            # Markup may be rendered here: the element's content is not fully known.
            self.opaque_content.append(self.stack[-1])


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


@dataclass
class Payload:
    """What one binding sends, as far as the markup says."""

    #: Legacy keys: name -> (attribute, literal value or None when dynamic, hint)
    legacy: dict[str, tuple[str, Optional[str], Optional[str]]] = field(default_factory=dict)
    legacy_complete: bool = True
    #: dj-value-* keys (the only markup the strict binder reads).
    values: dict[str, tuple[str, Optional[str], Optional[str]]] = field(default_factory=dict)
    values_complete: bool = True
    #: Names the client generates (value, field, form fields, ...).
    generated: tuple[str, ...] = ()
    generated_complete: bool = True
    #: Generated names only the legacy binder sends (``_target``).
    legacy_generated: tuple[str, ...] = ()
    #: Positional ``name(args)`` literals, None when not statically known.
    positional: Optional[list[Any]] = field(default_factory=list)
    #: (attribute, normalized name) pairs that collide in the client's merge.
    legacy_duplicates: list[tuple[str, str, str]] = field(default_factory=list)
    #: dj-value-* attributes the strict collector rejects outright.
    strict_invalid: list[tuple[str, str]] = field(default_factory=list)
    #: data-* attributes that name keys (legacy only), for migration hints.
    data_keys: dict[str, str] = field(default_factory=dict)
    #: The field name a form event reports as ``field``, when static.
    field_name: Optional[str] = None


@dataclass
class Binding:
    attribute: str
    directive: str
    spec: Directive
    raw_value: str
    #: The event name, or None when not statically known.
    name: Optional[str]
    file: str
    line: int
    payload: Payload
    #: "live": owned by the template's owner; "component" / "embedded": inside
    #: markup another owner handles; "outside": not under the live root.
    region: str = "live"
    #: Why the name is not checkable (JS command list, dynamic value, ...).
    unresolved: Optional[str] = None
    #: A value grammar misuse (arguments on a raw directive, invalid name).
    malformed: Optional[str] = None

    @property
    def label(self) -> str:
        return '%s="%s"' % (self.attribute, self.raw_value.replace(VALUE, "{{…}}"))


@dataclass
class TemplateScan:
    label: str
    file: str
    bindings: list[Binding]
    gaps: list[Gap]
    files: dict[str, str]
    error: Optional[str] = None


def _data_key(attribute: str) -> Optional[tuple[str, Optional[str]]]:
    if not attribute.startswith("data-"):
        return None
    if attribute.startswith(("data-liveview", "data-live-", "data-djust")) or attribute in (
        "data-loading",
        "data-component-id",
    ):
        return None
    raw, hint = _split_hint(attribute[5:])
    key = raw.replace("-", "_")
    if key.startswith("dj_"):
        key = key[3:]
    return (None if key in _UNSAFE_KEYS else key), hint


def _split_hint(rest: str) -> tuple[str, Optional[str]]:
    """``item-id:int`` -> (``item-id``, ``int``): the hint follows the last colon."""
    if ":" not in rest:
        return rest, None
    raw, hint = rest.rsplit(":", 1)
    return raw, hint or None


def _value_key(attribute: str) -> Optional[tuple[str, Optional[str]]]:
    if not attribute.startswith("dj-value-"):
        return None
    raw, hint = _split_hint(attribute[9:])
    key = raw.replace("-", "_")
    return (None if key in _UNSAFE_KEYS else key), hint


def _literal(value: str) -> Optional[str]:
    return None if is_dynamic(value) else value


def _element_payload(element: Element, payload: Payload) -> None:
    """data-*, dj-params and dj-value-* of one element (extractTypedParams)."""
    legacy = payload.legacy
    for attribute, value in element.attrs:
        parsed = _data_key(attribute)
        if parsed is None or parsed[0] is None:
            continue
        key, hint = parsed
        payload.data_keys.setdefault(key, attribute)
        if key in legacy:
            payload.legacy_duplicates.append((legacy[key][0], attribute, key))
        legacy[key] = (attribute, _literal(value), hint)
    params = element.attr("dj-params")
    if params is not None:
        if is_dynamic(params):
            payload.legacy_complete = False
        elif params.strip():
            try:
                parsed_params = json.loads(params)
            except ValueError:
                parsed_params = None
            if isinstance(parsed_params, dict):
                for key in parsed_params:
                    if key in _UNSAFE_KEYS:
                        continue
                    if key in legacy:
                        payload.legacy_duplicates.append((legacy[key][0], "dj-params", key))
                        continue
                    literal = parsed_params[key]
                    legacy[key] = ("dj-params", literal if isinstance(literal, str) else None, None)
    _value_payload(element, payload, into_legacy=True)


def _value_payload(element: Element, payload: Payload, into_legacy: bool) -> None:
    for attribute, value in element.attrs:
        parsed = _value_key(attribute)
        if parsed is None or parsed[0] is None:
            continue
        key, hint = parsed
        rest = attribute[9:]
        if (
            rest.count(":") > 1
            or rest.endswith(":")
            or not STRICT_VALUE_NAME.match(key)
            or key in ROUTING_KEYS
        ):
            payload.strict_invalid.append((attribute, key))
        if key in payload.values:
            payload.strict_invalid.append((attribute, key))
        payload.values[key] = (attribute, _literal(value), hint)
        if into_legacy:
            if key in payload.legacy and payload.legacy[key][0] != attribute:
                payload.legacy_duplicates.append((payload.legacy[key][0], attribute, key))
            payload.legacy[key] = (attribute, _literal(value), hint)
    if element.dynamic_attrs:
        payload.values_complete = False
        if into_legacy:
            payload.legacy_complete = False


def _field_name(element: Element) -> Optional[str]:
    """getFieldName: data-field, then name, then id without ``id_``."""
    for attribute in ("data-field", "name", "id"):
        value = element.attr(attribute)
        if value:
            if is_dynamic(value):
                return None
            return value[3:] if attribute == "id" and value.startswith("id_") else value
    return None


def _form_fields(form: Element, opaque: set[int]) -> tuple[list[str], bool]:
    names: list[str] = []
    complete = id(form) not in opaque
    for element in form.descendants():
        if id(element) in opaque:
            complete = False
        if element.tag not in _FORM_FIELD_TAGS:
            continue
        if element.dynamic_attrs:
            complete = False
        name = element.attr("name")
        if not name or element.has("disabled"):
            continue
        if element.tag == "input" and (element.attr("type") or "").lower() in _NON_DATA_INPUTS:
            continue
        if is_dynamic(name):
            complete = False
            continue
        if name not in names:
            names.append(name)
    return names, complete


def _payload(
    base: str, spec: Directive, element: Element, opaque: set[int], positional: Optional[list[Any]]
) -> Payload:
    payload = Payload(
        generated=spec.generated, legacy_generated=spec.legacy_only, positional=positional
    )
    if spec.payload == "element":
        _element_payload(element, payload)
    elif spec.payload == "field":
        # dj-value-* come from the field that triggers the event. On a field
        # itself that is this element; on a container it is any descendant.
        if element.tag in _FORM_FIELD_TAGS:
            _value_payload(element, payload, into_legacy=True)
            payload.field_name = _field_name(element)
        else:
            payload.values_complete = payload.legacy_complete = False
    elif spec.payload == "form":
        _value_payload(element, payload, into_legacy=True)
        if element.tag == "form":
            names, complete = _form_fields(element, opaque)
            payload.generated = tuple(names)
            payload.generated_complete = complete
        else:
            payload.generated_complete = False
    elif base in _PARTIAL_FIXED:
        payload.generated_complete = False
    return payload


def _bindings_of(element: Element, opaque: set[int]) -> list[Binding]:
    """Each event binding on ``element``; the caller sets the location."""
    found = []
    for attribute, value in element.attrs:
        if not attribute.startswith("dj-"):
            continue
        selected = directive_for(attribute)
        if selected is None:
            continue
        base, spec = selected
        if not value.strip():
            continue
        entries: list[tuple[str, Optional[list[Any]], Optional[str], Optional[str]]] = []
        if is_dynamic(value):
            entries.append(("", None, "the event name is computed by the template", None))
        elif spec.grammar == "shortcut":
            for part in value.split(","):
                pieces = part.strip().split(":")
                if len(pieces) >= 2 and pieces[1].strip():
                    entries.append((pieces[1].strip(), [], None, None))
        elif base in _JS_COMMAND_DIRECTIVES and value.lstrip().startswith("["):
            entries.extend(_js_commands(value))
        elif spec.grammar == "call":
            name, args = parse_call(value)
            entries.append((name, args, None, None))
        else:
            name = value.strip()
            malformed = None
            if "(" in name:
                malformed = (
                    "%s does not take arguments: the whole value is sent as the event name" % base
                )
            entries.append((name, [], None, malformed))
        for name, args, unresolved, malformed in entries:
            payload = _payload(base, spec, element, opaque, args)
            if malformed is None and name and not unresolved and not EVENT_NAME.match(name):
                malformed = (
                    "%r is not a valid event name: the server accepts lowercase letters, "
                    "digits and underscores, starting with a letter" % name
                )
            found.append(
                Binding(
                    attribute,
                    base,
                    spec,
                    value,
                    name or None,
                    "",
                    0,
                    payload,
                    unresolved=unresolved,
                    malformed=malformed,
                )
            )
    return found


def _js_commands(value: str) -> list[tuple[str, Optional[list[Any]], Optional[str], Optional[str]]]:
    try:
        commands = json.loads(value)
    except ValueError:
        return [(value.strip(), [], None, None)]
    if not isinstance(commands, list) or not all(isinstance(c, list) for c in commands):
        return [(value.strip(), [], None, None)]
    entries: list[tuple[str, Optional[list[Any]], Optional[str], Optional[str]]] = []
    for command in commands:
        if len(command) >= 2 and command[0] == "push" and isinstance(command[1], dict):
            event = command[1].get("event")
            if isinstance(event, str):
                entries.append((event, [], None, None))
    return entries


def markup_bindings(html: str) -> list[Binding]:
    """The event bindings of rendered markup, with no template to follow."""
    parser = _Markup(html)
    parser.feed(html)
    parser.close()
    return [b for element in parser.elements for b in _bindings_of(element, set())]


def markup_event_names(html: str) -> list[str]:
    """The event names rendered markup sends, in order of first appearance.

    For markup a component has already rendered: the same element and
    directive parsing as a template scan.
    """
    names: list[str] = []
    for binding in markup_bindings(html):
        if binding.name and binding.name not in names:
            names.append(binding.name)
    return names


def scan_source(engine: Any, template: Any, label: str, file: str, source: str) -> TemplateScan:
    """Scan one compiled Django template. ``file``/``source`` label its own lines."""
    flat = _Flat()
    flat.files[file] = source
    _Flattener(engine, flat).template(template, file, {}, 0)
    return _scan_flat(flat, label, file)


def scan_tokens(source: str, label: str, file: str, reason: str) -> TemplateScan:
    """Scan a template Django cannot compile (djust-only syntax) from its tokens.

    Text is kept, every tag is a branch boundary and every variable is a value;
    ``{% include %}`` and ``{% extends %}`` are not followed, which is recorded.
    """
    from django.template.base import Lexer, TokenType

    flat = _Flat()
    flat.files[file] = source
    flat.gaps.append(
        Gap(
            file,
            1,
            "Django cannot compile this template (%s); includes and "
            "parents are not followed" % reason,
        )
    )
    skip_until: Optional[str] = None
    for token in Lexer(source).tokenize():
        contents = token.contents.strip()
        if skip_until is not None:
            if token.token_type == TokenType.BLOCK and contents == skip_until:
                skip_until = None
            elif skip_until == "endverbatim":
                flat.emit(
                    token.contents if token.token_type == TokenType.TEXT else VALUE,
                    file,
                    token.lineno,
                )
            continue
        if token.token_type == TokenType.TEXT:
            flat.emit(token.contents, file, token.lineno)
        elif token.token_type == TokenType.VAR:
            flat.emit(VALUE)
        elif token.token_type == TokenType.BLOCK:
            tag = contents.split()[0] if contents else ""
            if tag in ("comment", "verbatim"):
                skip_until = "end" + tag
            else:
                flat.emit(BRANCH)
    return _scan_flat(flat, label, file)


def _scan_flat(flat: _Flat, label: str, file: str) -> TemplateScan:
    text = "".join(flat.text)
    parser = _Markup(text)
    parser.feed(text)
    parser.close()

    offsets = [segment[0] for segment in flat.segments]

    def locate(offset: int) -> tuple[str, int]:
        index = bisect.bisect_right(offsets, offset) - 1
        if index < 0:
            return file, 0
        start, origin, line = flat.segments[index]
        return origin, line + text.count("\n", start, offset)

    live_roots = {id(e) for e in parser.elements if e.has("dj-root") or e.has("dj-view")}
    opaque = {id(e) for e in parser.opaque_content}
    bindings: list[Binding] = []
    for element in parser.elements:
        chain = [element, *element.ancestors()]
        for binding in _bindings_of(element, opaque):
            match = re.search(
                r"(?<![\w:-])%s\s*=" % re.escape(binding.attribute), element.raw, re.IGNORECASE
            )
            binding.file, binding.line = locate(element.offset + (match.start() if match else 0))
            if live_roots and not any(id(e) in live_roots for e in chain):
                binding.region = "outside"
            elif any(e.has("data-component-id") for e in chain):
                binding.region = "component"
            elif any(e.has("data-djust-embedded") for e in chain):
                binding.region = "embedded"
            bindings.append(binding)
    return TemplateScan(label, file, bindings, flat.gaps, flat.files)


# ---------------------------------------------------------------------------
# A class's own template
# ---------------------------------------------------------------------------


def django_engine() -> Any:
    """The project's first Django template engine, or None."""
    from django.template import engines

    for backend in engines.all():
        engine = getattr(backend, "engine", None)
        if engine is not None and hasattr(engine, "get_template"):
            return engine
    return None


def _inline_location(cls: type) -> tuple[str, int]:
    """The file of ``cls`` and the line its ``template`` string starts on."""
    import inspect

    try:
        file = inspect.getsourcefile(cls) or ""
        lines, start = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return "<%s.%s.template>" % (cls.__module__, cls.__qualname__), 1
    for index, text in enumerate(lines):
        if re.match(r"\s*template\s*(:[^=]*)?=", text):
            return file, start + index
    return file, start


def _uncompiled(engine: Any, name: str, exc: Exception) -> Any:
    """Tokens of a template Django finds but cannot compile, else the failure."""
    from django.template import TemplateSyntaxError

    if not isinstance(exc, TemplateSyntaxError):
        return type(exc).__name__
    for loader in engine.template_loaders:
        for origin in loader.get_template_sources(name):
            try:
                source = loader.get_contents(origin)
            except Exception:  # noqa: BLE001, S112 -- try the next source, as Django's loader does
                continue
            return scan_tokens(source, name, origin.name, type(exc).__name__)
    return type(exc).__name__


def scan_class_template(
    cls: type, engine: Any, cache: dict[Any, Any]
) -> tuple[str, Optional[tuple[Any, ...]]]:
    """Scan the ``template`` or ``template_name`` a class declares, into ``cache``.

    Returns ``(label, cache key)``; the cached value is a ``TemplateScan``, or
    the name of the failure when the template cannot be loaded. The key is None
    when the class declares neither.
    """
    inline = getattr(cls, "template", None)
    name = getattr(cls, "template_name", None)
    if isinstance(inline, str) and inline:
        file, first_line = _inline_location(cls)
        key: tuple[Any, ...] = ("inline", inline, file, first_line)
        if key not in cache:
            try:
                template = engine.from_string(inline)
            except Exception as exc:  # noqa: BLE001 -- djust-only syntax falls back to tokens
                scan = scan_tokens(inline, "<inline>", file, type(exc).__name__)
            else:
                scan = scan_source(engine, template, "<inline>", file, inline)
            offset = first_line - 1
            for item in [*scan.bindings, *scan.gaps]:
                if item.file == file:
                    item.line += offset
            cache[key] = scan
        return "%s.template" % cls.__qualname__, key
    if isinstance(name, str) and name:
        key = ("file", name)
        if key not in cache:
            try:
                template = engine.get_template(name)
            except Exception as exc:  # noqa: BLE001 -- a loader or syntax failure is examined here
                cache[key] = _uncompiled(engine, name, exc)
            else:
                path = getattr(getattr(template, "origin", None), "name", None) or name
                cache[key] = scan_source(engine, template, name, path, template.source)
        return name, key
    return "", None


def recovery_scan(cls: type) -> tuple[frozenset[str], bool]:
    """The literal ``dj-auto-recover`` targets of the class's template, including
    its includes and parents (ADR-037 row 18), and whether they are all of them.

    The flag is False when the scan cannot see every target the page may
    render: no Django engine, no declared template, a template that did not
    load, markup the scan cannot follow (a dynamic include or extends, a tag
    that renders markup), or a computed ``dj-auto-recover`` value. Only then
    do the targets of each render count too (#3127).
    """
    engine = django_engine()
    if engine is None:
        return frozenset(), False
    cache: dict[Any, Any] = {}
    _label, key = scan_class_template(cls, engine, cache)
    scan = cache.get(key) if key is not None else None
    if not isinstance(scan, TemplateScan):
        return frozenset(), False
    recover = [b for b in scan.bindings if b.directive == "dj-auto-recover"]
    names = frozenset(b.name for b in recover if b.name)
    complete = not scan.gaps and scan.error is None and all(b.name for b in recover)
    return names, complete


def recovery_targets(cls: type) -> frozenset[str]:
    """Handlers a literal ``dj-auto-recover`` in the class's template targets,
    including its includes and parents (ADR-037 row 18)."""
    return recovery_scan(cls)[0]
