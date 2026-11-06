"""The attribute slots of a ``Component``'s hybrid ``template`` (#3900).

``Component.render`` draws a component three ways: the crate's Rust instance, else the class's
``template`` (the hybrid path, ``{{ x }}``), else ``_render_custom()``. The crate constructor
takes plain text only, so a value it refuses (a lazy string is one) leaves a class that has a
``template`` to be drawn by it, and a class with no crate counterpart is drawn by it always.

The two other paths treat what lands in a quoted attribute alike: it is escaped whatever it is
marked as (:func:`djust.components.utils.attr_text`), and a URL is checked against the scheme
allowlist (:func:`djust.components.utils.data_url_attr`). A template written ``{{ x }}`` does
neither: autoescape leaves a marked-safe value (a lazy ``mark_safe`` string, a ``SafeString``,
anything with ``__html__``) alone, and there is no scheme check at all. So the same
``src="javascript:alert(1)"`` drew ``<img src="#">`` as a plain string and a live script URL as a
lazy one, and ``size='md" onclick="x'`` left its attribute.

This module is the one place the hybrid path is made to follow the rule. It reads the TEMPLATE
(not a list of classes or keys): every ``{{ ... }}`` that sits inside a start tag is an attribute
slot, and each is rewritten once per template string (cached):

* ``{{ name }}`` (a plain top-level name) is renamed to a synthetic context key that
  :func:`harden_context` fills with the value made plain text (the marker dropped, so the
  engine's autoescape escapes it once), and, in a URL attribute (``href``, ``src``, ``action``
  ...), checked with ``data_url_attr`` / ``srcset_attr`` first. A value that is not text (a
  number, ``None``, a bool) is left exactly as it was: nothing is localized or reformatted.
* any other expression (a filter, a dotted path, a ``{% for %}`` / ``{% with %}`` variable)
  gets ``|force_escape`` appended (``|escape`` becomes ``|force_escape``): ``escape`` leaves a
  marked-safe value as it is, ``force_escape`` does not. Such a URL slot is escaped but its
  scheme is NOT checked (the value is not in the context to be judged); it is listed in
  :attr:`Plan.unchecked_urls` and no template shipped in this package has one.

Text slots (``<span>{{ text }}</span>``) are untouched.
"""

from __future__ import annotations

import functools
import re
from typing import Any, NamedTuple

from django.utils.functional import Promise

#: Attributes HTML defines as holding a URL (plus ``xlink:href``); ``srcset`` is a list of them.
URL_ATTRS = frozenset(
    {
        "href", "src", "action", "formaction", "data", "poster", "background", "cite",
        "longdesc", "manifest", "ping", "codebase", "usemap", "icon", "profile", "xlink:href",
        "archive", "classid", "itemid", "itemtype",
    }
)  # fmt: skip
#: ``<img src>`` may be an inline ``data:image/...`` URI.
_IMAGE_TAGS = frozenset({"img", "source", "image"})
#: Elements whose content is text, not markup: a ``<`` inside is not the start of a tag.
_RAW_TEXT = frozenset({"script", "style", "textarea", "title"})

#: Names a template reads as literals, not as context keys.
_LITERALS = frozenset({"True", "False", "None"})

_TOKEN = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_TAG_OPEN = re.compile(r"<([A-Za-z][^\s/>]*)")
_NAME = re.compile(r"[A-Za-z_]\w*")
_FOR = re.compile(r"\{%-?\s*for\s+(.+?)\s+in\s", re.S)
_WITH = re.compile(r"\{%-?\s*with\s+(.+?)-?%\}", re.S)
_AS = re.compile(r"\sas\s+(\w+)\s*-?%\}")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


class Slot(NamedTuple):
    """One ``{{ ... }}`` inside a start tag: where it is, what it holds, whose attribute."""

    start: int
    end: int
    expr: str
    tag: str
    attr: str


class _Scanner:
    """A forward scan of a template's text for the ``{{ }}`` tokens inside start tags.

    ``{% %}`` and ``{# #}`` tokens are skipped whole (a ``>`` in ``{% if a > b %}`` ends
    nothing), so an ``{% if %}`` that wraps an attribute leaves the tag open across it.
    """

    def __init__(self) -> None:
        self.in_tag = False
        self.quote = ""
        self.tag = ""
        self.attr = ""
        self.word = ""
        self.comment = False
        self.raw = ""

    def feed(self, text: str) -> None:
        i, n = 0, len(text)
        while i < n:
            c = text[i]
            if self.comment:
                j = text.find("-->", i)
                if j < 0:
                    return
                self.comment, i = False, j + 3
                continue
            if self.raw and not self.in_tag:
                m = re.compile(rf"</{self.raw}\b", re.I).search(text, i)
                if m is None:
                    return
                self.raw, i = "", m.start()
                continue
            if not self.in_tag:
                if c == "<":
                    if text.startswith("<!--", i):
                        self.comment, i = True, i + 4
                        continue
                    m = _TAG_OPEN.match(text, i)
                    if m:
                        self.in_tag, self.tag = True, m.group(1).lower()
                        self.attr = self.word = ""
                        i = m.end()
                        continue
                i += 1
                continue
            if self.quote:
                if c == self.quote:
                    self.quote, self.attr = "", ""
                i += 1
                continue
            if c in "\"'":
                self.quote = c
            elif c == ">":
                self.in_tag = False
                if self.tag in _RAW_TEXT:
                    self.raw = self.tag
            elif c == "=":
                self.attr, self.word = self.word.lower(), ""
            elif c.isspace() or c == "/":
                self.word = ""
            else:
                self.word += c
            i += 1


def slots(template: str) -> list[Slot]:
    """Every ``{{ ... }}`` of ``template`` that sits inside a start tag, in order."""
    scanner, found, pos = _Scanner(), [], 0
    for m in _TOKEN.finditer(template):
        scanner.feed(template[pos : m.start()])
        token = m.group()
        if token.startswith("{{") and scanner.in_tag:
            found.append(Slot(m.start(), m.end(), token[2:-2], scanner.tag, scanner.attr))
        pos = m.end()
    return found


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
    if "force_escape" in names:
        return expr
    if "escape" in names:
        masked = _QUOTED.sub(lambda m: '"' + "x" * (len(m.group()) - 2) + '"', expr)
        found = re.search(r"\|\s*escape\b", masked)
        if found is not None:
            return expr[: found.start()] + "|force_escape" + expr[found.end() :]
    return expr.rstrip() + "|force_escape"


class Sub(NamedTuple):
    """A synthetic context key and the context name it is filled from."""

    key: str
    name: str
    #: ``"text"`` (made plain text), ``"url"`` / ``"image"`` (checked), ``"srcset"``.
    kind: str


class Plan(NamedTuple):
    template: str
    subs: tuple[Sub, ...]
    #: ``(tag, attr, expr)`` of each URL slot that could be escaped but not checked.
    unchecked_urls: tuple[tuple[str, str, str], ...]


def _kind(slot: Slot) -> str:
    if slot.attr == "srcset":
        return "srcset"
    if slot.attr in URL_ATTRS:
        return "image" if slot.attr == "src" and slot.tag in _IMAGE_TAGS else "url"
    return "text"


@functools.lru_cache(maxsize=512)
def plan(template: str) -> Plan:
    """The rewrite of ``template`` (cached per template string)."""
    found = slots(template)
    if not found:
        return Plan(template, (), ())
    bound = _bound_names(template)
    out: list[str] = []
    subs: list[Sub] = []
    unchecked: list[tuple[str, str, str]] = []
    pos = 0
    for slot in found:
        kind = _kind(slot)
        out.append(template[pos : slot.start])
        pos = slot.end
        name = slot.expr.strip()
        if (
            _NAME.fullmatch(name)
            and name not in bound
            and name not in _LITERALS
            and not name.startswith("_")
        ):
            key = f"djust_attr_{len(subs)}"
            subs.append(Sub(key, name, kind))
            out.append("{{ " + key + " }}")
            continue
        out.append("{{ " + _force_escaped(slot.expr.strip()) + " }}")
        if kind != "text":
            unchecked.append((slot.tag, slot.attr, slot.expr.strip()))
    out.append(template[pos:])
    return Plan("".join(out), tuple(subs), tuple(unchecked))


def _is_text_like(value: Any) -> bool:
    return isinstance(value, (str, Promise)) or hasattr(value, "__html__")


def neutralise(value: Any, kind: str) -> Any:
    """``value`` made safe for the attribute slot ``kind``: text becomes plain text (checked
    first in a URL attribute); anything that is not text is returned as it was."""
    if not _is_text_like(value):
        return value
    from djust.components.utils import _exact_str, data_url_attr, srcset_attr

    if kind == "text":
        return _exact_str(value)
    if kind == "srcset":
        return srcset_attr(value, honor_safe=False)
    return data_url_attr(value, image=kind == "image")


def harden_context(template: str, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(template, context)`` to hand the engine: the attribute slots of ``template`` rewritten
    and their values made plain text (URLs checked). ``context`` itself is not modified."""
    rewrite = plan(template)
    if not rewrite.subs:
        return rewrite.template, context
    hardened = dict(context)
    for sub in rewrite.subs:
        if sub.name in context:
            hardened[sub.key] = neutralise(context[sub.name], sub.kind)
    return rewrite.template, hardened
