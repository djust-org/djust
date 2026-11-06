"""The attribute slots of a ``Component``'s hybrid ``template`` (#3900).

``Component.render`` draws a component three ways: the crate's Rust instance, else the class's
``template`` (the hybrid path, ``{{ x }}``), else ``_render_custom()``. The crate constructor
takes plain text only, so a value it refuses (a lazy string is one) leaves a class that has a
``template`` to be drawn by it, and a class with no crate counterpart is drawn by it always.

The two other paths treat what lands in a quoted attribute alike: it is escaped whatever it is
marked as (:func:`djust.components.utils.attr_text`), and a URL is checked against the scheme
allowlist (:func:`djust.components.utils.data_url_attr`). A template written ``{{ x }}`` does
neither: autoescape leaves a value alone when its text form is marked safe, and there is no
scheme check at all. This module is the one place the hybrid path is made to follow the rule. It
reads the TEMPLATE (not a list of classes or keys) once per template string (cached).

What is a slot
    A ``{{ ... }}`` in the VALUE of an attribute of a start tag, found with the HTML tokenizer's
    own states (attribute name, optional whitespace, ``=``, optional whitespace, a double-quoted,
    single-quoted or unquoted value). ``{% verbatim %}`` and ``{% comment %}`` blocks are not
    templates and are left alone. A ``{{ }}`` in the NAME position of an attribute
    (``<a {{ attrs }}>``), in an end tag and between tags is not a slot and is not rewritten.

What happens to a slot
    * Every slot gets ``|force_escape`` as its last filter (``|escape`` becomes
      ``|force_escape``). Unlike autoescape it escapes whatever the value is, however the value
      yields its text (a marked-safe string, a lazy one, an object whose text form is marked
      safe, a callable the engine calls), and it holds under ``{% autoescape off %}``. It also
      means a number in an attribute prints in its canonical form (``33.5``), not localized
      (``33,5``), for a plain name, a dotted path and a filtered expression alike.
    * A slot in a URL attribute (``href``, ``src``, ``action`` ... ; ``srcset`` is a list) whose
      value can choose the scheme is judged with the allowlist the other paths use, on the WHOLE
      value as it will be assembled (literal text and every slot), never on one slot alone. Where
      that is and is not done:

      - the slot is first in the value, or only characters that could be part of a scheme
        (letters, digits, ``+ - .``) precede it: judged, as one value with whatever follows it
        and with every other slot of the value (``href="{{ u }}"``, ``href="{{ s }}:x"``,
        ``href="{{ a }}{{ b }}"``);
      - a literal character that cannot be part of a scheme comes first (``href="/search?q={{ q }}"``,
        ``href="/u/{{ name }}"``, ``href="http://{{ host }}/"``): the literal already fixed the
        scheme, so nothing in the slot can change it: escaped, not judged;
      - ``srcset``: judged candidate by candidate, but only when the slot is the whole value.
        With literal text or several slots it is escaped, not judged.
      - NOT judged, only escaped, and listed in :attr:`Plan.unchecked_urls` (a warning is logged
        once per template): a value that can choose the scheme and has a slot that is not a plain
        top-level name (a dotted path, a filtered expression, a ``{% for %}`` / ``{% with %}``
        variable, so ``{% for l in links %}<a href="{{ l.url }}">``) or a tag that prints text
        (``{% url %}``). Those values are not in the context to be judged.
    * An unquoted value (``title={{ x }}``) cannot be made safe by HTML escaping (a space ends
      it); it is escaped and listed in :attr:`Plan.unquoted`, with the same warning.

Text slots (``<span>{{ text }}</span>``) are untouched.
"""

from __future__ import annotations

import functools
import logging
import re
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

#: Attributes HTML defines as holding a URL (plus ``xlink:href``); ``srcset`` is a list of them.
URL_ATTRS = frozenset(
    {
        "href", "src", "action", "formaction", "data", "poster", "background", "cite",
        "longdesc", "manifest", "ping", "codebase", "usemap", "icon", "profile", "xlink:href",
        "archive", "classid", "itemid", "itemtype",
        # the framework's own URL-valued directives (in-app paths; the client also validates them)
        "dj-navigate", "dj-patch", "dj-patch-reload",
    }
)  # fmt: skip
#: ``<img src>`` may be an inline ``data:image/...`` URI.
_IMAGE_TAGS = frozenset({"img", "source", "image"})
#: Elements whose content is text, not markup: a ``<`` inside is not the start of a tag.
_RAW_TEXT = frozenset({"script", "style", "textarea", "title"})
#: Names a template reads as literals, not as context keys.
_LITERALS = frozenset({"True", "False", "None"})
#: Characters a URL scheme is made of (RFC 3986: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )).
_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.")
#: Block tags that print nothing themselves: between the parts of a value they change nothing.
_SILENT_TAGS = frozenset(
    {
        "if", "elif", "else", "endif", "for", "empty", "endfor", "with", "endwith", "load",
        "comment", "endcomment", "spaceless", "endspaceless", "autoescape", "endautoescape",
        "block", "endblock", "ifchanged", "endifchanged", "verbatim", "endverbatim",
    }
)  # fmt: skip

_TOKEN = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_TAG_OPEN = re.compile(r"<([A-Za-z][^\s/>]*)")
_END_TAG = re.compile(r"</[A-Za-z]")
_NAME = re.compile(r"[A-Za-z_]\w*")
_TAG_NAME = re.compile(r"\{%-?\s*(\w+)")
_FOR = re.compile(r"\{%-?\s*for\s+(.+?)\s+in\s", re.S)
_WITH = re.compile(r"\{%-?\s*with\s+(.+?)-?%\}", re.S)
_AS = re.compile(r"\sas\s+(\w+)\s*-?%\}")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


class Part(NamedTuple):
    """One piece of an attribute value: literal text, a ``{{ }}``, or a ``{% %}`` / ``{# #}``."""

    kind: str  # "lit" | "var" | "tag"
    text: str  # the literal, the whole token
    start: int = -1
    end: int = -1


class Value(NamedTuple):
    """The value of one attribute of a start tag, in template order."""

    tag: str
    attr: str
    quoted: bool
    parts: tuple[Part, ...]
    #: the attribute NAME itself contained a ``{{ }}`` (it is not known)
    dynamic: bool


class Slot(NamedTuple):
    """One ``{{ ... }}`` in an attribute value: where it is, what it holds, whose attribute."""

    start: int
    end: int
    expr: str
    tag: str
    attr: str


class _Scanner:
    """The HTML tokenizer's attribute states, with template tokens as atoms.

    ``feed`` takes the literal text between template tokens, ``atom`` a ``{{ }}`` / ``{% %}`` /
    ``{# #}``. A token is never split: ``>`` in ``{% if a > b %}`` ends nothing, and an
    ``{% if %}`` around an attribute leaves the tag open across it.
    """

    def __init__(self) -> None:
        self.state = "text"
        self.tag = ""
        self.name = ""
        self.dynamic = False
        self.quote = ""
        self.lit = ""
        self.parts: list[Part] = []
        self.values: list[Value] = []
        self.comment = False
        self.raw = ""

    # -- value bookkeeping --------------------------------------------------

    def _open_value(self, quote: str) -> None:
        self.state = {'"': "dq", "'": "sq", "": "unq"}[quote]
        self.quote = quote
        self.parts = []
        self.lit = ""

    def _flush(self) -> None:
        if self.lit:
            self.parts.append(Part("lit", self.lit))
            self.lit = ""

    def _end_value(self) -> None:
        self._flush()
        self.values.append(
            Value(self.tag, self.name, bool(self.quote), tuple(self.parts), self.dynamic)
        )
        self.state, self.name, self.dynamic = "before_name", "", False

    def _close_tag(self) -> None:
        self.state = "text"
        if self.tag in _RAW_TEXT:
            self.raw = self.tag

    # -- input ----------------------------------------------------------------

    def feed(self, text: str) -> None:
        i, n = 0, len(text)
        while i < n:
            c, s = text[i], self.state
            if self.comment:
                j = text.find("-->", i)
                if j < 0:
                    return
                self.comment, i = False, j + 3
                continue
            if s == "text":
                if self.raw:
                    m = re.compile(rf"</{self.raw}\b", re.I).search(text, i)
                    if m is None:
                        return
                    self.raw, i = "", m.start()
                    continue
                if c == "<":
                    if text.startswith("<!--", i):
                        self.comment, i = True, i + 4
                        continue
                    m = _TAG_OPEN.match(text, i)
                    if m:
                        self.state, self.tag = "before_name", m.group(1).lower()
                        self.name, self.dynamic = "", False
                        i = m.end()
                        continue
                    m = _END_TAG.match(text, i)
                    if m:
                        self.state = "end_tag"
                        i = m.end()
                        continue
                i += 1
            elif s == "end_tag":
                if c == ">":
                    self.state = "text"
                i += 1
            elif s == "before_name":
                if c == ">":
                    self._close_tag()
                elif not (c.isspace() or c == "/"):
                    self.state, self.name, self.dynamic = "name", c.lower(), False
                i += 1
            elif s == "name":
                if c == "=":
                    self.state = "before_value"
                elif c == ">":
                    self._close_tag()
                elif c == "/":
                    self.state = "before_name"
                elif c.isspace():
                    self.state = "after_name"
                else:
                    self.name += c.lower()
                i += 1
            elif s == "after_name":
                if c == "=":
                    self.state = "before_value"
                elif c == ">":
                    self._close_tag()
                elif c == "/":
                    self.state = "before_name"
                elif not c.isspace():
                    self.state, self.name, self.dynamic = "name", c.lower(), False
                i += 1
            elif s == "before_value":
                if c in "\"'":
                    self._open_value(c)
                elif c == ">":
                    self._close_tag()
                elif not c.isspace():
                    self._open_value("")
                    self.lit = c
                i += 1
            elif s in ("dq", "sq"):
                if c == self.quote:
                    self._end_value()
                else:
                    self.lit += c
                i += 1
            else:  # unq
                if c == ">":
                    self._end_value()
                    self._close_tag()
                elif c.isspace():
                    self._end_value()
                else:
                    self.lit += c
                i += 1

    def atom(self, token: str, start: int, end: int) -> None:
        kind = "var" if token.startswith("{{") else "tag"
        s = self.state
        if self.comment or s in ("text", "end_tag"):
            return
        if s in ("dq", "sq", "unq"):
            self._flush()
            self.parts.append(Part(kind, token, start, end))
        elif s == "before_value":
            if kind == "var":
                self._open_value("")
                self.parts.append(Part(kind, token, start, end))
        elif kind == "var":  # a {{ }} where an attribute NAME is: not a value
            self.state = "name"
            if s in ("before_name", "after_name"):
                self.name = ""
            self.dynamic = True


def values(template: str) -> list[Value]:
    """Every attribute value of every start tag of ``template``, in order."""
    scanner, pos, skipping = _Scanner(), 0, ""
    for m in _TOKEN.finditer(template):
        token = m.group()
        if skipping:
            if token.startswith("{%") and _tag_name(token) == "end" + skipping:
                skipping, pos = "", m.end()
            continue
        scanner.feed(template[pos : m.start()])
        pos = m.end()
        if token.startswith("{%") and _tag_name(token) in ("verbatim", "comment"):
            skipping = _tag_name(token)
            continue
        scanner.atom(token, m.start(), m.end())
    if not skipping:
        scanner.feed(template[pos:])
    return scanner.values


def _tag_name(token: str) -> str:
    m = _TAG_NAME.match(token)
    return m.group(1) if m else ""


def slots(template: str) -> list[Slot]:
    """Every ``{{ ... }}`` of ``template`` that sits in an attribute value, in order."""
    return [
        Slot(p.start, p.end, p.text[2:-2], v.tag, v.attr)
        for v in values(template)
        for p in v.parts
        if p.kind == "var"
    ]


def _bound_names(template: str) -> frozenset[str]:
    """Names a ``{% for %}`` / ``{% with %}`` / ``{% ... as x %}`` binds: not context keys."""
    names: set[str] = set()
    for m in _FOR.finditer(template):
        names.update(_NAME.findall(m.group(1)))
    for m in _WITH.finditer(template):
        names.update(re.findall(r"(\w+)\s*=", m.group(1)))
        names.update(re.findall(r"\sas\s+(\w+)", " " + m.group(1)))
    names.update(_AS.findall(template))
    return frozenset(names)


def _filter_names(expr: str) -> list[str]:
    masked = _QUOTED.sub(lambda m: '"' + "x" * (len(m.group()) - 2) + '"', expr)
    return re.findall(r"\|\s*(\w+)", masked)


def _force_escaped(expr: str) -> str:
    """``expr`` with ``|force_escape`` as its last filter (``|escape`` becomes it)."""
    names = _filter_names(expr)
    if names and names[-1] == "force_escape":
        return expr
    if "escape" in names:
        masked = _QUOTED.sub(lambda m: '"' + "x" * (len(m.group()) - 2) + '"', expr)
        found = re.search(r"\|\s*escape\b", masked)
        if found is not None:
            return expr[: found.start()] + "|force_escape" + expr[found.end() :]
    return expr.rstrip() + "|force_escape"


class Group(NamedTuple):
    """The value of a URL attribute that is judged as a whole (see the module docstring)."""

    attr: str
    #: ``"url"`` | ``"image"`` (``<img src>``: ``data:image/...`` allowed) | ``"srcset"``
    kind: str
    #: ``("lit", text)`` / ``("slot", key)`` / ``("gap", "")`` (a tag that prints nothing)
    parts: tuple[tuple[str, str], ...]
    #: ``(synthetic context key, context name)`` of each slot, in order
    slots: tuple[tuple[str, str], ...]


class Plan(NamedTuple):
    template: str
    groups: tuple[Group, ...]
    #: ``(tag, attr, value as written)`` of each URL value that is escaped but not judged
    unchecked_urls: tuple[tuple[str, str, str], ...]
    #: ``(tag, attr, value as written)`` of each unquoted value
    unquoted: tuple[tuple[str, str, str], ...]


def _kind(value: Value) -> str:
    if value.dynamic:
        return "text"
    if value.attr == "srcset":
        return "srcset"
    if value.attr in URL_ATTRS:
        return "image" if value.attr == "src" and value.tag in _IMAGE_TAGS else "url"
    return "text"


def _can_choose_scheme(parts: tuple[Part, ...]) -> bool:
    """Whether something that is not literal text can end up where the scheme is.

    Browsers drop leading whitespace and control characters; a scheme starts with a letter and
    goes on with letters, digits and ``+ - .`` up to the ``:``. The first literal character
    that is not part of that (``/``, ``?``, ``:``, a digit first ...) fixes the value as a
    relative URL or as the scheme the literal itself spells, whatever follows. So a slot is
    judged only if everything before it, in the value, could still be the start of a scheme.
    """
    started = False
    for part in parts:
        if part.kind == "lit":
            for ch in part.text:
                if not started:
                    if ch <= " ":
                        continue
                    if not (ch.isascii() and ch.isalpha()):
                        return False
                    started = True
                elif ch not in _SCHEME_CHARS:
                    return False
        elif part.kind == "tag" and _tag_name(part.text) in _SILENT_TAGS:
            continue
        else:
            return True
    return False


@functools.lru_cache(maxsize=512)
def plan(template: str) -> Plan:
    """The rewrite of ``template`` (cached per template string)."""
    found = values(template)
    if not any(p.kind == "var" for v in found for p in v.parts):
        return Plan(template, (), (), ())
    bound = _bound_names(template)
    edits: list[tuple[int, int, str]] = []
    groups: list[Group] = []
    unchecked: list[tuple[str, str, str]] = []
    unquoted: list[tuple[str, str, str]] = []
    for value in found:
        variables = [p for p in value.parts if p.kind == "var"]
        if not variables:
            continue
        written = "".join(p.text for p in value.parts)
        if not value.quoted:
            unquoted.append((value.tag, value.attr, written))
        kind = _kind(value)
        group = None
        if kind == "srcset" or (kind != "text" and _can_choose_scheme(value.parts)):
            group = _group(value, kind, variables, bound)
            if group is None:
                unchecked.append((value.tag, value.attr, written))
        for part in variables:
            expr = part.text[2:-2].strip()
            key = f"djust_attr_{part.start}"
            if group is not None:
                filt = "safe" if group.kind == "srcset" else "force_escape"
                edits.append((part.start, part.end, "{{ " + key + "|" + filt + " }}"))
            else:
                edits.append((part.start, part.end, "{{ " + _force_escaped(expr) + " }}"))
        if group is not None:
            groups.append(group)
    out, pos = [], 0
    for start, end, text in sorted(edits):
        out.append(template[pos:start])
        out.append(text)
        pos = end
    out.append(template[pos:])
    result = Plan("".join(out), tuple(groups), tuple(unchecked), tuple(unquoted))
    if result.unchecked_urls or result.unquoted:
        logger.warning(
            "A Component template has attribute values djust cannot make safe: URL values "
            "escaped but not scheme-checked %s; unquoted values %s. Quote the value and "
            "give a URL that can choose the scheme to the template as a plain top-level name.",
            [f"{t} {a}={w}" for t, a, w in result.unchecked_urls],
            [f"{t} {a}={w}" for t, a, w in result.unquoted],
        )
    return result


def _group(value: Value, kind: str, variables: list[Part], bound: frozenset[str]) -> Group | None:
    """The group to judge for ``value``, or ``None`` if it cannot be judged (see the docstring)."""
    pieces: list[tuple[str, str]] = []
    keyed: list[tuple[str, str]] = []
    for part in value.parts:
        if part.kind == "lit":
            pieces.append(("lit", part.text))
        elif part.kind == "tag":
            if _tag_name(part.text) not in _SILENT_TAGS:
                return None
            pieces.append(("gap", ""))
        else:
            name = part.text[2:-2].strip()
            if (
                not _NAME.fullmatch(name)
                or name in bound
                or name in _LITERALS
                or name.startswith("_")
            ):
                return None
            key = f"djust_attr_{part.start}"
            keyed.append((key, name))
            pieces.append(("slot", key))
    if kind == "srcset" and (
        len(variables) != 1 or any(k == "lit" and t.strip() for k, t in pieces)
    ):
        return None
    return Group(value.attr, kind, tuple(pieces), tuple(keyed))


_MISSING = object()


def slot_text(value: Any) -> str:
    """The text a slot holds, as the engine would print it, but as the EXACT ``str`` type.

    A callable is called the way the engine calls it (when the project's auto-call setting is
    on, and the value does not opt out); everything is then converted by ``_exact_str``, which
    takes the text from ``str(value)`` whatever the value is marked as, so a value that is not
    text but whose text form is marked safe comes out as plain text like any other.
    """
    from djust.components.utils import _exact_str

    if value is _MISSING:
        return ""
    if (
        callable(value)
        and not isinstance(value, type)
        and not getattr(value, "do_not_call_in_templates", False)
    ):
        from djust.config import template_auto_call_enabled

        if getattr(value, "alters_data", False):
            return ""
        if template_auto_call_enabled():
            try:
                value = value()
            except TypeError:
                # The callable cannot be called without arguments: it is left as it is, and
                # whatever text it has is what _exact_str gives (and the attribute escapes).
                pass
    return _exact_str(value)


def _allowed(group: Group, assembled: str) -> bool:
    from djust.components.utils import _NOOP_HREFS, url_allowed

    if group.attr == "href" and assembled.strip().lower() in _NOOP_HREFS:
        return True
    return url_allowed(assembled, image=group.kind == "image")


def harden_context(template: str, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(template, context)`` to hand the engine: the attribute slots of ``template`` rewritten
    and the values of the URL groups judged. ``context`` itself is not modified."""
    rewrite = plan(template)
    if not rewrite.groups:
        return rewrite.template, context
    hardened = dict(context)
    for group in rewrite.groups:
        texts = [slot_text(context.get(name, _MISSING)) for _key, name in group.slots]
        if group.kind == "srcset":
            from djust.components.utils import srcset_attr

            texts = [srcset_attr(texts[0], honor_safe=False)]
        else:
            by_key = {key: text for (key, _name), text in zip(group.slots, texts)}
            assembled = "".join(
                by_key[v] if k == "slot" else (v if k == "lit" else "") for k, v in group.parts
            )
            if not _allowed(group, assembled):
                texts = ["#"] + [""] * (len(texts) - 1)
        for (key, _name), text in zip(group.slots, texts):
            hardened[key] = text
    return rewrite.template, hardened
