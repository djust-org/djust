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
reads the TEMPLATE (not a list of classes or keys) once per template string (a bounded LRU).

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
      safe, a callable the engine calls), and it holds under ``{% autoescape off %}``.
      It also means that in an attribute a number prints in its canonical form (``33.5``, not a
      localized ``33,5``) and a date in its ISO form (``2026-01-02``), for a plain name, a dotted
      path and a filtered expression alike. A value whose ``str()`` raises raises in a URL slot
      (as the engine does); in a plain slot the Rust engine's ``force_escape`` prints the
      escaped default repr where Django's raises, which is the crate's filter and not changed here.
    * A slot in a URL attribute (``href``, ``src``, ``action`` ... ; ``srcset`` is a list) whose
      value can choose the scheme is judged with the allowlist the other paths use, on the WHOLE
      value as it will be assembled (literal text and every slot), never on one slot alone. Where
      that is and is not done:

      - the value is followed in EVERY way it can come out: a literal inside ``{% if %}`` /
        ``{% for %}`` may be skipped, so each branch is a path of its own (a loop runs 0, 1 or
        2 times; at most 64 paths) and only a literal outside every block is always there. A path
        is judged unless a literal already fixes it. A path that is refused loses its slots (the
        first becomes ``#``, the rest nothing), so a slot is judged as if a block that adds a
        literal before it were skipped: in the branch where it is taken a good ``foo:bar`` is
        replaced as well (it fails closed);
      - a path is fixed when the literal text, read the way a browser reads it (character
        references decoded, tab / newline / carriage return dropped, leading whitespace
        dropped), reaches a character that cannot be part of a scheme before any slot
        (``href="/search?q={{ q }}"``, ``href="http://{{ host }}/"``): nothing in the slot can
        change it, so it is escaped, not judged. ``{% url %}`` and ``{% static %}`` print a path
        from the project's configuration and count as such a literal when outside every block;
        comments and ``... as var`` tags print nothing and are skipped;
      - a path whose slot is first, or after only scheme characters (``href="{{ s }}:x"``,
        ``href="java{{ b }}"``, ``href="&#106;ava{{ s }}"``), is judged as one value with the
        text after it and with the other slots;
      - ``srcset``: judged candidate by candidate, but only when the slot is the whole value.
        With literal text or several slots it is escaped, not judged.
      - NOT judged, only escaped, and listed in :attr:`Plan.unchecked_urls`: a value that can
        choose the scheme and has a slot that is not a plain top-level name (a dotted path, a
        filtered expression, a ``{% for %}`` / ``{% with %}`` variable, so
        ``{% for l in links %}<a href="{{ l.url }}">``), a tag that prints text this module does
        not know (``{% trans %}``, ``{% now %}``), more than 64 paths, or an attribute NAME built
        by tags or a ``{{ }}`` (``<a {% if x %}href{% else %}title{% endif %}="{{ u }}">``: it
        may be a URL attribute). Those values are not in the context to be judged.
    * An unquoted value (``title={{ x }}``) cannot be made safe by HTML escaping (a space ends
      it); it is escaped and listed in :attr:`Plan.unquoted`.

The warning
    :func:`harden_context` logs, once per component class and set of attributes, the tag and
    attribute NAMES of the two lists above and the class name, never the template's text (a
    template built per instance may carry runtime values). The plan cache is a bounded LRU and
    the set of warnings given is bounded too.

Text slots (``<span>{{ text }}</span>``) are untouched.
"""

from __future__ import annotations

import functools
import html
import inspect
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
#: Tags that open a branch inside a block.
_BRANCH = frozenset({"else", "elif", "empty"})
#: Block tags whose content may be skipped (``{% for %}``: run 0, 1 or 2 times, handled apart).
_CONDITIONAL = frozenset({"if", "ifchanged", "ifequal", "ifnotequal"})
#: Tags that print a path from the project's own configuration (URLconf, ``STATIC_URL``).
_PATH_TAGS = frozenset({"url", "static"})
#: The most ways one URL value may come out before it is treated as one this cannot follow.
_MAX_PATHS = 64
#: Characters a browser drops from a URL before it reads the scheme.
_DROP_WS = {ord("\t"): None, ord("\n"): None, ord("\r"): None}
#: A tag that assigns its output to a variable instead of printing it (``{% url "x" as u %}``).
_AS_VAR = re.compile(r"\sas\s+\w+\s*-?%\}$")

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
        elif s == "name":  # a tag inside a name (``{% if x %}href{% else %}title{% endif %}=``)
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
    #: EVERY way the value can come out: each ``{% if %}`` branch taken or skipped, a loop run
    #: 0, 1 or 2 times. A path is a tuple of ``("lit", text)`` (normalised the way a browser
    #: reads it) and ``("slot", synthetic key)``.
    paths: tuple[tuple[tuple[str, str], ...], ...]
    #: ``(synthetic context key, context name)`` of each slot, in template order
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


# -- the shape of a value ---------------------------------------------------------------------


class _Unknown(Exception):
    """The value has a part this module cannot follow (see ``_paths``)."""


def _normal(text: str) -> str:
    """A literal as a browser reads it before it looks for a scheme: character references
    decoded, tab / newline / carriage return dropped."""
    return html.unescape(text).translate(_DROP_WS)


def _token_name(part: Part) -> str:
    """The tag name of a ``{% %}`` part, ``"#"`` for a ``{# #}`` comment."""
    return "#" if part.text.startswith("{#") else _tag_name(part.text)


def _is_silent(part: Part, name: str) -> bool:
    """A tag that prints nothing and changes no branch: a comment, ``{% load %}``, an assignment
    (``{% url "x" as u %}``, ``{% static "x" as s %}``)."""
    return name in ("#", "load", "comment", "endcomment") or bool(_AS_VAR.search(part.text))


def _openers(parts: tuple[Part, ...]) -> frozenset[str]:
    """The block tags of this value: every ``X`` for which an ``endX`` tag is in it."""
    names = {_token_name(p) for p in parts if p.kind == "tag"}
    return frozenset(n[3:] for n in names if n.startswith("end") and n[3:])


def _fixed(path: tuple[tuple[str, str], ...]) -> bool:
    """Whether a literal at the start of ``path`` already fixes the value, before any slot.

    Browsers drop leading whitespace and control characters; a scheme starts with a letter and
    goes on with letters, digits and ``+ - .`` up to the ``:``. The first literal character
    that is not part of that (``/``, ``?``, ``:``, a digit first ...) fixes the value as a
    relative URL or as the scheme the literal itself spells, whatever follows. A slot reached
    while everything before it could still be the start of a scheme can choose the scheme.
    """
    started = False
    for kind, text in path:
        if kind != "lit":
            return False
        for ch in text:
            if not started:
                if ch <= " ":
                    continue
                if not (ch.isascii() and ch.isalpha()):
                    return True
                started = True
            elif ch not in _SCHEME_CHARS:
                return True
    return True


def _paths(
    parts: tuple[Part, ...], bound: frozenset[str]
) -> tuple[list[tuple[tuple[str, str], ...]], list[tuple[str, str]]]:
    """Every way the value can come out, and its slots (``(key, name)``, template order).

    Only a literal at the value's top level, outside every block, is always there; one inside
    ``{% if %}`` / ``{% for %}`` may be skipped, so each branch is a path of its own. A path
    set is capped; a value this cannot follow (a printing tag it does not know, a slot that is
    not a plain top-level name, an unbalanced block) raises :class:`_Unknown`.
    """
    openers = _openers(parts)
    slots_: list[tuple[str, str]] = []

    def cap(paths: list) -> list:
        if len(paths) > _MAX_PATHS:
            raise _Unknown
        return paths

    def block(i: int) -> tuple[list, int, str]:
        paths: list[tuple[tuple[str, str], ...]] = [()]
        while i < len(parts):
            part = parts[i]
            if part.kind == "lit":
                paths = [p + (("lit", _normal(part.text)),) for p in paths]
            elif part.kind == "var":
                name = part.text[2:-2].strip()
                if (
                    not _NAME.fullmatch(name)
                    or name in bound
                    or name in _LITERALS
                    or name.startswith("_")
                ):
                    raise _Unknown
                key = f"djust_attr_{part.start}"
                if (key, name) not in slots_:
                    slots_.append((key, name))
                paths = [p + (("slot", key),) for p in paths]
            else:
                name = _token_name(part)
                if name in _BRANCH or (name.startswith("end") and name[3:] in openers):
                    return paths, i, name
                if name in openers:
                    alternatives, i = branches(i, name)
                    paths = cap([p + alt for p in paths for alt in alternatives])
                    continue
                if _is_silent(part, name):
                    pass
                elif name in _PATH_TAGS:
                    # a path from the project's own URL configuration or STATIC_URL: it fixes
                    # the value (a relative URL, or the scheme the project chose)
                    paths = [p + (("lit", "/"),) for p in paths]
                else:
                    raise _Unknown
            i += 1
        return paths, i, ""

    def branches(i: int, name: str) -> tuple[list, int]:
        body, i, term = block(i + 1)
        bodies = [body]
        has_else = False
        while term in _BRANCH:
            has_else = has_else or term in ("else", "empty")
            nxt, i, term = block(i + 1)
            bodies.append(nxt)
        if term != "end" + name:
            raise _Unknown
        if name == "for":
            first, rest = bodies[0], bodies[1:]
            skipped = [alt for b in rest for alt in b] or [()]
            twice = [a + b for a in first for b in first]
            alternatives = skipped + first + twice
        elif name in _CONDITIONAL:
            alternatives = [alt for b in bodies for alt in b]
            if not has_else:
                alternatives.append(())
        else:  # with, spaceless, autoescape, block ...: the content is always there
            alternatives = bodies[0]
        return cap(alternatives), i + 1

    paths, _end, term = block(0)
    if term:
        raise _Unknown
    return cap(paths), slots_


def _maybe_chooses(parts: tuple[Part, ...]) -> bool:
    """For a value whose shape ``_paths`` cannot follow: whether it can still choose the scheme.

    Conservative: a literal fixes the value only at the top level (outside every block), and a
    slot, or a printing tag this module does not know, can choose it.
    """
    openers = _openers(parts)
    depth, started = 0, False
    for part in parts:
        if part.kind == "var":
            return True
        if part.kind == "lit":
            if depth:
                continue
            for ch in _normal(part.text):
                if not started:
                    if ch <= " ":
                        continue
                    if not (ch.isascii() and ch.isalpha()):
                        return False
                    started = True
                elif ch not in _SCHEME_CHARS:
                    return False
            continue
        name = _token_name(part)
        if name in openers:
            depth += 1
        elif name.startswith("end") and name[3:] in openers:
            depth -= 1
        elif name in _BRANCH or _is_silent(part, name):
            continue
        elif name in _PATH_TAGS:
            if not depth:
                return False
        else:
            return True
    return False


@functools.lru_cache(maxsize=512)
def plan(template: str) -> Plan:
    """The rewrite of ``template`` (cached per template string, a bounded LRU)."""
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
        if kind != "text":
            group, listed = _group(value, kind, variables, bound)
            if listed:
                unchecked.append((value.tag, value.attr, written))
        elif value.dynamic and _maybe_chooses(value.parts):
            # an attribute NAME built by tags or a {{ }}: it may be a URL attribute; the slot is
            # escaped but cannot be judged
            unchecked.append((value.tag, value.attr, written))
        for part in variables:
            key = f"djust_attr_{part.start}"
            if group is not None:
                filt = "safe" if group.kind == "srcset" else "force_escape"
                edits.append((part.start, part.end, "{{ " + key + "|" + filt + " }}"))
            else:
                expr = part.text[2:-2].strip()
                edits.append((part.start, part.end, "{{ " + _force_escaped(expr) + " }}"))
        if group is not None:
            groups.append(group)
    out, pos = [], 0
    for start, end, text in sorted(edits):
        out.append(template[pos:start])
        out.append(text)
        pos = end
    out.append(template[pos:])
    return Plan("".join(out), tuple(groups), tuple(unchecked), tuple(unquoted))


def _group(
    value: Value, kind: str, variables: list[Part], bound: frozenset[str]
) -> tuple[Group | None, bool]:
    """``(group to judge or None, whether the value is listed as escaped but not judged)``."""
    if kind == "srcset":
        # judged candidate by candidate, but only when the slot is the whole value
        name = variables[0].text[2:-2].strip()
        printed_elsewhere = any(
            (p.kind == "lit" and p.text.strip())
            or (p.kind == "tag" and not _is_silent(p, _token_name(p)))
            for p in value.parts
        )
        simple = (
            _NAME.fullmatch(name)
            and name not in bound
            and name not in _LITERALS
            and not name.startswith("_")
        )
        if len(variables) != 1 or printed_elsewhere or not simple:
            return None, True
        key = f"djust_attr_{variables[0].start}"
        return Group(value.attr, kind, ((("slot", key),),), ((key, name),)), False
    try:
        paths, slots_ = _paths(value.parts, bound)
    except _Unknown:
        return None, _maybe_chooses(value.parts)
    if all(_fixed(p) for p in paths):
        return None, False
    return Group(value.attr, kind, tuple(paths), tuple(slots_)), False


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
                # Django's rule: a TypeError because the callable NEEDS arguments prints nothing
                # (``string_if_invalid``); any other TypeError is the callable's own and goes on.
                try:
                    inspect.signature(value).bind()
                except TypeError:
                    return ""
                except ValueError:
                    # no signature to bind (a builtin, say): it is the callable's own TypeError
                    pass
                raise
    return _exact_str(value)


def _allowed(attr: str, kind: str, assembled: str) -> bool:
    from djust.components.utils import _NOOP_HREFS, url_allowed

    if attr == "href" and assembled.strip().lower() in _NOOP_HREFS:
        return True
    return url_allowed(assembled, image=kind == "image")


def _refused(group: Group, path: tuple[tuple[str, str], ...], texts: dict[str, str]) -> bool:
    if _fixed(path):
        return False
    assembled = "".join(v if k == "lit" else texts[v] for k, v in path)
    return not _allowed(group.attr, group.kind, assembled)


#: Warnings already given, by (class, attributes): bounded, and never carrying template text.
_WARNED: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
_MAX_WARNED = 512
_PLAIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9:_.-]{0,40}")


def _warn_once(owner: str, rewrite: Plan) -> None:
    """Name, once per class and set of attributes, the values that are escaped but not judged.

    Only the class name and the tag / attribute NAMES are logged, and only when they are plain
    names: the template's own text is never logged (a template built per instance may carry
    runtime values in its attributes).
    """
    if not (rewrite.unchecked_urls or rewrite.unquoted):
        return

    def named(items: tuple[tuple[str, str, str], ...]) -> tuple[tuple[str, str], ...]:
        return tuple(
            (t if _PLAIN_NAME.fullmatch(t) else "?", a if _PLAIN_NAME.fullmatch(a) else "?")
            for t, a, _w in items
        )

    unchecked, unquoted = named(rewrite.unchecked_urls), named(rewrite.unquoted)
    key = (owner, unchecked + unquoted)
    if key in _WARNED or len(_WARNED) >= _MAX_WARNED:
        return
    _WARNED.add(key)
    logger.warning(
        "%s: hybrid template attribute values djust cannot make safe: URL values escaped but not "
        "scheme-checked %s; unquoted values %s. Quote the value, and give a URL that can choose "
        "the scheme to the template as a plain top-level name.",
        owner,
        [f"{t}[{a}]" for t, a in unchecked],
        [f"{t}[{a}]" for t, a in unquoted],
    )


def harden_context(
    template: str, context: dict[str, Any], owner: str = ""
) -> tuple[str, dict[str, Any]]:
    """``(template, context)`` to hand the engine: the attribute slots of ``template`` rewritten
    and the values of the URL groups judged. ``context`` itself is not modified. ``owner`` is the
    component class name, for the warning."""
    rewrite = plan(template)
    _warn_once(owner, rewrite)
    if not rewrite.groups:
        return rewrite.template, context
    hardened = dict(context)
    for group in rewrite.groups:
        texts = {key: slot_text(context.get(name, _MISSING)) for key, name in group.slots}
        if group.kind == "srcset":
            from djust.components.utils import srcset_attr

            (key,) = texts
            texts[key] = srcset_attr(texts[key], honor_safe=False)
        else:
            # A path whose assembled value is refused loses its slots: the first becomes "#" and
            # the rest nothing, so no value is left that completes a refused scheme. Repeat until
            # every path is allowed (each round neutralises a slot, so it ends).
            for _ in range(len(group.slots) + 1):
                bad = [p for p in group.paths if _refused(group, p, texts)]
                if not bad:
                    break
                for path in bad:
                    first = True
                    for kind, v in path:
                        if kind == "slot":
                            texts[v] = "#" if first else ""
                            first = False
        hardened.update(texts)
    return rewrite.template, hardened
