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
    :func:`harden_context` logs, once per component class and set of findings, what it could
    not make safe: the class name, the tag / attribute / element NAMES the template spells (an
    odd one is logged as ``?``; a name is template text, so keep names static in a template
    built per instance), the names of the shapes below and a count. Never an attribute value,
    text between tags or anything from the context. The plan cache is a bounded LRU and the set
    of warnings given is bounded too. An ``{% include %}`` / ``{% extends %}`` of a literal name
    whose source the loaders find and which has nothing to harden is not reported.

Escaped text (#3914)
    The content of ``<textarea>`` and ``<title>`` is RCDATA: text in which character references
    are decoded, ended by the first matching end tag. HTML escaping is its right encoding, and
    markup in a value written there can end the element. So a ``{{ }}`` in it, and in an HTML
    comment (``-->`` ends a comment the same way), is escaped even when the value is marked safe:

    * a plain top-level name: a string, number, date, ``None`` ... is handed to the engine as it
      is, so an ordinary value prints byte for byte as before; a string of another type (a
      marked-safe one) is handed over as the exact ``str``; any other value (a lazy string, a
      callable, an object with its own text form) is escaped with ``|force_escape`` where the
      engine reaches the slot (:func:`unmarked`), so a callable in a branch that never renders
      is not called;
    * any other expression (a dotted path, a filter chain, a loop variable), and every slot of a
      template with ``{% autoescape %}``, ends in ``|force_escape`` as an attribute slot does,
      so a date or number there prints in its canonical form;
    * a ``{% blocktranslate %}`` block is rewritten like an attribute one (below).

    NOT escaped, and named by the warning: a ``{% %}`` tag in that text that prints text
    (``{% firstof %}``, ``{% cycle %}`` ...).

    ``<script>``, ``<style>``, ``<xmp>``, ``<iframe>``, ``<noembed>``, ``<noframes>``,
    ``<plaintext>`` and ``<noscript>`` are NOT escaped: entities are not decoded in them, so HTML
    escaping is the wrong encoding (JSON written with ``|safe`` into a script would stop
    parsing), and their markup is the author's own. The scanner still knows the first seven as
    raw text (their content is not markup, so an ``<a href="{{ u }}">`` inside ``<xmp>`` is not
    read as an attribute); ``<noscript>`` is markup with scripting off and is scanned as it
    always was.

Translation blocks
    ``{% blocktranslate %}`` (and ``{% blocktrans %}``) variables are keyed by the translation
    catalog (``%(name)s``), so the body is never rewritten (renaming a variable would change the
    msgid, and ``name|force_escape`` is not a variable). The block is escaped as a whole in an
    attribute value and in escaped text instead: a variable the block reads from the context is
    rebound around it (``{% with name=name|force_escape %}``), a ``with`` argument gets
    ``|force_escape`` appended, a ``count`` argument is a number and is left alone, and the
    literal text prints as written. A block with ``asvar`` prints nothing (the variable it sets
    is a marked-safe string, escaped again where an attribute prints it). The legacy
    ``with expr as name`` form cannot be rewritten and is named by the warning. A block in a URL
    attribute is escaped and not judged.

Shapes the scanner cannot follow like a browser does (named by the warning, fail open for the
shape only): an element name built by a tag or a ``{{ }}``; a raw-text element whose end tag is
inside another block than its start tag (``{% if closed %}</textarea>{% endif %}``); the
``<script>`` double-escape state (``<!--<script>``), which does not matter for the escaped text
of ``<textarea>`` / ``<title>``. A ``<textarea>`` opened inside ``{% verbatim %}`` is followed.

Every OTHER text slot (``<span>{{ text }}</span>``) is untouched: it keeps a marked-safe value's
markup, as a Django template does (the #3898 rule; the difference to the Rust and custom paths of
the same class is tracked in #3932).
"""

from __future__ import annotations

import datetime
import decimal
import functools
import html
import inspect
import logging
import re
import uuid
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
#: Elements whose content is text, not markup: a ``<`` inside is not the start of a tag, and the
#: element ends at the first matching end tag (``<plaintext>`` has none). The scanner must know
#: all of them so that a ``<a href="{{ u }}">`` written inside ``<xmp>`` is not read as an
#: attribute. ``<noscript>`` is not here: with scripting off it is markup, and its content has
#: always been scanned as markup.
_RAW_TEXT = frozenset(
    {"script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext"}
)
#: The raw-text elements whose content is RCDATA: text with character references, so HTML
#: escaping is the right encoding and a value that ends the element is a breakout (#3914). In
#: ``<script>``, ``<style>``, ``<xmp>`` ... entities are not decoded, escaping is the WRONG
#: encoding (JSON in a script would stop parsing), and the markup there is the author's own.
_ESCAPED_TEXT = frozenset({"textarea", "title"})
#: Tags that print nothing, or only a path / date from the project's own configuration: a
#: ``{% %}`` of any other name inside escaped text may print text this module cannot escape.
#: ``{% include %}`` / ``{% extends %}`` are counted apart (``Plan.includes``).
_NON_PRINTING = frozenset(
    {
        "#", "if", "elif", "else", "endif", "for", "empty", "endfor", "with", "endwith", "load",
        "comment", "endcomment", "verbatim", "endverbatim", "autoescape", "endautoescape",
        "spaceless", "endspaceless", "ifchanged", "endifchanged", "ifequal", "endifequal",
        "ifnotequal", "endifnotequal", "block", "endblock", "url", "static", "now", "csrf_token",
        "templatetag", "include", "extends",
    }
)  # fmt: skip
#: Block tags whose variables are keyed by a translation catalog (``%(name)s``): their body is
#: never rewritten, only escaped as a whole (see ``_rewrite_block``).
_BLOCKTRANS = ("blocktrans", "blocktranslate")
#: Tags that open a block closed by ``end<name>`` (for the nesting depth of a conditional end).
_OPENERS = frozenset(
    {"if", "for", "with", "block", "ifchanged", "ifequal", "ifnotequal", "spaceless",
     "autoescape", "filter", "comment", "verbatim", *_BLOCKTRANS}
)  # fmt: skip
#: ASCII whitespace, the only whitespace HTML ends a tag name with.
_HTML_WS = " \t\n\f\r"
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
_END_TAG = re.compile(r"</([A-Za-z][^\s/>]*)")
_NAME = re.compile(r"[A-Za-z_]\w*")
_TAG_NAME = re.compile(r"\{%-?\s*(\w+)")
_FOR = re.compile(r"\{%-?\s*for\s+(.+?)\s+in\s", re.S)
_WITH = re.compile(r"\{%-?\s*with\s+(.+?)-?%\}", re.S)
_AS = re.compile(r"\sas\s+(\w+)\s*-?%\}")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_OPEN_TAG = re.compile(r"\{%-?\s*(?:blocktrans|blocktranslate)\b.*?-?%\}", re.S)


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
        #: ``{{ }}`` in escaped text: the content of ``<textarea>`` / ``<title>`` or of an HTML
        #: comment (``-->`` ends a comment as an end tag ends an element)
        self.raw_slots: list[Slot] = []
        #: a ``{% blocktranslate %}`` block in that text (``expr`` holds the whole block)
        self.raw_blocks: list[Slot] = []
        #: ``(element, tag name)`` of each ``{% %}`` in that text that may print text
        self.raw_tags: list[tuple[str, str]] = []
        #: shapes this scanner cannot follow like a browser does (named by the warning)
        self.shapes: list[str] = []
        #: the template block nesting depth (set by ``_scan``) and the depth a raw-text element
        #: was opened at: an end tag at another depth is conditional
        self.depth = 0
        self.raw_depth = 0
        #: the last literal text ended with ``<``: a template token right after it builds the
        #: element name
        self.lt = False

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
            self.raw, self.raw_depth = self.tag, self.depth

    # -- input ----------------------------------------------------------------

    def feed(self, text: str) -> None:
        """Literal template text between tokens."""
        if not text:
            return
        self.lt = False
        self._feed(text)
        self.lt = text.endswith("<") and self.state == "text" and not self.raw and not self.comment

    def _feed(self, text: str) -> None:
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
                    if self.raw == "plaintext":  # no end tag: the rest of the document
                        return
                    # ASCII case and whitespace only, as the HTML tokenizer reads an end tag
                    m = re.compile(rf"</{self.raw}(?=[{_HTML_WS}/>])", re.I | re.A).search(text, i)
                    if m is None:
                        return
                    if self.depth != self.raw_depth:
                        self.shapes.append("conditional end tag")
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

    def _raw_atom(self, token: str, start: int, end: int, kind: str, where: str) -> None:
        """A ``{{ }}`` / ``{% %}`` in escaped text (see ``raw_slots``)."""
        if kind == "var":
            self.raw_slots.append(Slot(start, end, token[2:-2], where, ""))
        elif kind == "blk":
            self.raw_blocks.append(Slot(start, end, token, where, ""))
        else:
            name = "#" if token.startswith("{#") else _tag_name(token)
            if name not in _NON_PRINTING and not _AS_VAR.search(token):
                self.raw_tags.append((where, name))

    def block(self, text: str, start: int, end: int) -> None:
        """A whole ``{% blocktranslate %}`` block, as ONE atom. Its variables are keyed by the
        translation catalog, so its body is never read as slots; a block that stores its result
        (``asvar``) prints nothing here and is a silent tag."""
        if re.search(r"\sasvar\s", _OPEN_TAG.match(text).group(0)):  # type: ignore[union-attr]
            self.atom("{# asvar #}", start, end)
        else:
            self.atom(text, start, end, opaque=True)

    def atom(self, token: str, start: int, end: int, opaque: bool = False) -> None:
        kind = "blk" if opaque else ("var" if token.startswith("{{") else "tag")
        s = self.state
        lt, self.lt = self.lt, False
        if self.comment:
            self._raw_atom(token, start, end, kind, "comment")
            return
        if s == "end_tag":
            return
        if s == "text":
            if lt and not self.raw:
                self.shapes.append("element name built by a tag")
            if self.raw in _ESCAPED_TEXT:
                self._raw_atom(token, start, end, kind, self.raw)
            return
        if s in ("dq", "sq", "unq"):
            self._flush()
            self.parts.append(Part(kind, token, start, end))
        elif s == "before_value":
            if kind in ("var", "blk"):
                self._open_value("")
                self.parts.append(Part(kind, token, start, end))
        elif kind in ("var", "blk"):  # a {{ }} where an attribute NAME is: not a value
            self.state = "name"
            if s in ("before_name", "after_name"):
                self.name = ""
            self.dynamic = True
        elif s == "name":  # a tag inside a name (``{% if x %}href{% else %}title{% endif %}=``)
            self.dynamic = True


def _scan(template: str) -> _Scanner:
    """The tokenizer's pass over ``template``: attribute values, raw-text slots and tags."""
    scanner, pos = _Scanner(), 0
    matches = list(_TOKEN.finditer(template))

    def closing(i: int, name: str) -> int | None:
        """The index of the ``{% end<name> %}`` token that closes the block opened at ``i``."""
        for j in range(i + 1, len(matches)):
            if (
                matches[j].group().startswith("{%")
                and _tag_name(matches[j].group()) == "end" + name
            ):
                return j
        return None

    i = 0
    while i < len(matches):
        m = matches[i]
        token = m.group()
        name = _tag_name(token) if token.startswith("{%") else ""
        if name in ("verbatim", "comment") or name in _BLOCKTRANS:
            j = closing(i, name)
            if j is None and name not in _BLOCKTRANS:
                scanner.feed(template[pos : m.start()])
                return scanner  # an unterminated verbatim / comment swallows the rest
            if j is not None:  # (an unterminated blocktranslate is an ordinary tag Django refuses)
                scanner.feed(template[pos : m.start()])
                if name == "verbatim":
                    # output as written, so the tokenizer's picture of the document follows it
                    scanner.feed(template[m.end() : matches[j].start()])
                elif name in _BLOCKTRANS:
                    scanner.block(
                        template[m.start() : matches[j].end()], m.start(), matches[j].end()
                    )
                pos, i = matches[j].end(), j + 1
                continue
        scanner.feed(template[pos : m.start()])
        pos = m.end()
        if name in _OPENERS:
            scanner.depth += 1
        elif name.startswith("end") and name[3:] in _OPENERS:
            scanner.depth -= 1
        scanner.atom(token, m.start(), m.end())
        i += 1
    scanner.feed(template[pos:])
    return scanner


def values(template: str) -> list[Value]:
    """Every attribute value of every start tag of ``template``, in order."""
    return _scan(template).values


def raw_slots(template: str) -> list[Slot]:
    """Every ``{{ ... }}`` of ``template`` in escaped text: the content of ``<textarea>`` /
    ``<title>`` and HTML comments (see :data:`_ESCAPED_TEXT`), in order."""
    return _scan(template).raw_slots


def _includes(template: str) -> tuple[int, tuple[str, ...]]:
    """``(how many, the literal names)`` of the ``{% include %}`` / ``{% extends %}`` tags of
    ``template`` outside a ``{% verbatim %}`` / ``{% comment %}`` block: the templates they name
    are not scanned. A name that is not a quoted literal is counted but not listed."""
    count, names, skipping = 0, [], ""
    for m in _TOKEN.finditer(template):
        token = m.group()
        if not token.startswith("{%"):
            continue
        name = _tag_name(token)
        if skipping:
            if name == "end" + skipping:
                skipping = ""
        elif name in ("verbatim", "comment"):
            skipping = name
        elif name in ("include", "extends"):
            count += 1
            literal = re.match(r"\{%-?\s*\w+\s+(?:\"([^\"]+)\"|'([^']+)')", token)
            if literal:
                names.append(literal.group(1) or literal.group(2))
    return count, tuple(names)


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
    names.update(re.findall(r"\sasvar\s+(\w+)", template))
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
    #: ``(synthetic context key, context name)`` of each plain-name slot in escaped text (the
    #: content of ``<textarea>`` / ``<title>`` and HTML comments): the value is read from the
    #: context and stripped of a "this is markup" claim (:func:`unmarked`)
    raw: tuple[tuple[str, str], ...] = ()
    #: ``(element, tag name)`` of each ``{% %}`` in that text that may print text and that this
    #: module cannot escape (``{% firstof %}``, ``{% cycle %}`` ...)
    raw_tags: tuple[tuple[str, str], ...] = ()
    #: how many ``{% include %}`` / ``{% extends %}`` tags the template has (not scanned) ...
    includes: int = 0
    #: ... and the literal template names among them
    include_names: tuple[str, ...] = ()
    #: shapes the scanner cannot follow like a browser does: an element name built by a tag, an
    #: end tag inside a different block than its start tag, a translation block it cannot rewrite
    shapes: tuple[str, ...] = ()


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
    scan = _scan(template)
    found = scan.values
    raw_tags = tuple(scan.raw_tags)
    includes, include_names = _includes(template)
    shapes = list(scan.shapes)
    has_slots = any(p.kind in ("var", "blk") for v in found for p in v.parts)
    if not (has_slots or scan.raw_slots or scan.raw_blocks):
        return Plan(template, (), (), (), (), raw_tags, includes, include_names, _named(shapes))
    bound = _bound_names(template)
    edits: list[tuple[int, int, str]] = []
    groups: list[Group] = []
    unchecked: list[tuple[str, str, str]] = []
    unquoted: list[tuple[str, str, str]] = []

    def block_edit(part_or_slot_start: int, end: int, text: str) -> None:
        new, ok = _rewrite_block(text)
        if not ok:
            shapes.append("translation block it cannot rewrite")
        edits.append((part_or_slot_start, end, new))

    for value in found:
        variables = [p for p in value.parts if p.kind in ("var", "blk")]
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
            if part.kind == "blk":
                block_edit(part.start, part.end, part.text)
            elif group is not None:
                filt = "safe" if group.kind == "srcset" else "force_escape"
                edits.append((part.start, part.end, "{{ " + key + "|" + filt + " }}"))
            else:
                expr = part.text[2:-2].strip()
                edits.append((part.start, part.end, "{{ " + _force_escaped(expr) + " }}"))
        if group is not None:
            groups.append(group)
    raw: list[tuple[str, str]] = []
    # Under ``{% autoescape off %}`` a plain string is not escaped by the engine, so a plain name
    # cannot be left to it: every slot of such a template ends in ``|force_escape``.
    engine_escapes = not re.search(r"\{%-?\s*autoescape\b", template)
    for slot in scan.raw_slots:
        expr = slot.expr.strip()
        if engine_escapes and _simple(expr, bound):
            # a plain name: when the context hands over a plain string, a number, a date ...
            # (or a marked-safe string, made plain) the engine prints it as it always did, byte
            # for byte; in every other case (a callable, a lazy value, an object, a name the
            # template itself binds, a missing name) the slot ends in `|force_escape`, which the
            # engine evaluates only WHEN IT REACHES THE SLOT (see `unmarked`)
            key = f"djust_raw_{slot.start}"
            raw.append((key, expr))
            ready = "{% if " + key + "_p %}{{ " + key + " }}{% else %}"
            edits.append(
                (slot.start, slot.end, ready + "{{ " + expr + "|force_escape }}{% endif %}")
            )
        else:
            edits.append((slot.start, slot.end, "{{ " + _force_escaped(expr) + " }}"))
    for slot in scan.raw_blocks:
        block_edit(slot.start, slot.end, slot.expr)
    out, pos = [], 0
    for start, end, text in sorted(edits):
        out.append(template[pos:start])
        out.append(text)
        pos = end
    out.append(template[pos:])
    return Plan(
        "".join(out),
        tuple(groups),
        tuple(unchecked),
        tuple(unquoted),
        tuple(raw),
        raw_tags,
        includes,
        include_names,
        _named(shapes),
    )


def _named(shapes: list[str]) -> tuple[str, ...]:
    """The shapes, each named once, in order of first appearance."""
    return tuple(dict.fromkeys(shapes))


def _rewrite_block(text: str) -> tuple[str, bool]:
    """``(rewritten block, ok)`` for a whole ``{% blocktranslate %}`` block placed in an attribute
    value or in escaped text.

    The body is never touched: its variables (``%(name)s``) are the translation catalog's keys.
    What the block prints is escaped instead, variable by variable and exactly once: a variable
    the block reads from the context is rebound around it (``{% with name=name|force_escape %}``),
    a ``with`` argument gets ``|force_escape`` appended, a ``count`` argument is a number and is
    left alone. Literal text is the author's and prints as written. ``ok`` is False for a block
    this cannot rewrite (the legacy ``with expr as name`` form), which is left as it is and named
    by the warning.
    """
    from django.utils.text import smart_split

    opened = _OPEN_TAG.match(text)
    closed = re.search(r"\{%-?\s*end(?:blocktrans|blocktranslate)\s*-?%\}\s*$", text)
    if opened is None or closed is None:
        return text, False
    name = _tag_name(opened.group(0))
    inner = re.sub(r"^\{%-?\s*\w+|-?%\}$", "", opened.group(0))
    bound: set[str] = set()
    out: list[str] = []
    section = ""
    for token in smart_split(inner):
        if section == "context":
            out.append(token)
            section = ""
        elif token in ("with", "count", "context", "trimmed", "noop"):
            out.append(token)
            section = "" if token in ("trimmed", "noop") else token
        elif section in ("with", "count"):
            pair = re.fullmatch(r"(\w+)=(.+)", token, re.S)
            if pair is None:  # the legacy ``with expr as name`` form
                return text, False
            bound.add(pair.group(1))
            out.append(
                f"{pair.group(1)}={pair.group(2)}|force_escape" if section == "with" else token
            )
        else:
            out.append(token)
    body = text[opened.end() : closed.start()]
    names = [n for n in dict.fromkeys(re.findall(r"\{\{\s*(\w+)\s*\}\}", body)) if n not in bound]
    head = "{% " + " ".join([name, *out]) + " %}"
    rewritten = head + body + text[closed.start() :]
    if names:
        rebinding = " ".join(f"{n}={n}|force_escape" for n in names)
        rewritten = "{% with " + rebinding + " %}" + rewritten + "{% endwith %}"
    return rewritten, True


def _simple(name: str, bound: frozenset[str]) -> bool:
    """A plain top-level context name (not a loop / ``with`` variable, not a literal)."""
    return bool(
        _NAME.fullmatch(name)
        and name not in bound
        and name not in _LITERALS
        and not name.startswith("_")
    )


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
    return _exact_str(_called(value))


def _called(value: Any) -> Any:
    """``value`` after the engine's auto-call: a callable is called (when the project's setting
    is on, and the value does not opt out), a callable that ``alters_data`` or needs arguments
    gives ``""``. Anything else comes back as it is."""
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
    return value


#: Types whose text form cannot be a marked-safe string: left to the engine as they are, so a
#: date is localised and a number formatted the way the engine always did.
_ORDINARY_TYPES = frozenset(
    {
        bool, int, float, complex, type(None), bytes, list, tuple, dict, set, frozenset, range,
        datetime.date, datetime.datetime, datetime.time, datetime.timedelta, decimal.Decimal,
        uuid.UUID,
    }
)  # fmt: skip


def unmarked(value: Any) -> tuple[bool, Any]:
    """``(deferred, value)``: how a plain-name slot in escaped text (``<textarea>``, ``<title>``,
    an HTML comment) is handed to the engine.

    * An exact ``str``, or a value of an ordinary type (a number, a date, ``None``, a list ...),
      is handed over as it is, so the engine formats and escapes it exactly as it always did.
    * A string of another type (a marked-safe string is one) is handed over as the EXACT ``str``
      type, so the engine escapes it.
    * Anything else (a lazy string, a callable, an object with its own text form or
      ``__html__``) is ``deferred``: the template escapes it with ``|force_escape`` where the
      engine reaches the slot. It is NOT evaluated here, so a callable in a branch that never
      renders is not called and an expensive ``__str__`` is not run for nothing.
    """
    from djust.components.utils import _exact_str

    if type(value) is str or type(value) in _ORDINARY_TYPES:
        return False, value
    if isinstance(value, str):
        return False, _exact_str(value)
    return True, value


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
_WARNED: set[tuple[Any, ...]] = set()
_MAX_WARNED = 512
_PLAIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9:_.-]{0,40}")


def _include_source(name: str) -> str | None:
    """The source of the template an ``{% include "name" %}`` names, or None when the project's
    loaders cannot find it."""
    from djust.components.base import _load_template_source

    try:
        return _load_template_source(name)
    except Exception:  # noqa: BLE001 - not found / a loader error: "cannot resolve", never fatal
        return None


def _unscanned_includes(rewrite: Plan) -> int:
    """How many ``{% include %}`` / ``{% extends %}`` tags name a template whose slots would need
    this module's rule. A quoted literal name that the loaders find, whose source has no
    attribute slot, no escaped-text slot and no translation block, needs nothing and is not
    counted; a name that is not a literal, or that cannot be found, is."""
    unresolved = rewrite.includes - len(rewrite.include_names)
    for name in rewrite.include_names:
        source = _include_source(name)
        if source is None:
            unresolved += 1
            continue
        found = _scan(source)
        if any(p.kind in ("var", "blk") for v in found.values for p in v.parts) or (
            found.raw_slots or found.raw_blocks
        ):
            unresolved += 1
    return unresolved


def _warn_once(owner: str, rewrite: Plan) -> None:
    """Name, once per class and set of findings, what djust could not make safe in a template:
    URL values escaped but not judged, unquoted values, tags that print text inside escaped
    text, shapes the scanner cannot follow like a browser does, and the ``{% include %}`` /
    ``{% extends %}`` tags whose templates hold slots it did not scan.

    What is logged is the class name, the tag / attribute / element NAMES the template itself
    spells (when they look like plain names: an odd one is logged as ``?``), the shape names
    above and a count. It is never an attribute VALUE, text between tags or anything from the
    context. An attribute NAME is template text: a template built per instance can carry
    runtime text in a name, so keep names static.
    """
    if not (
        rewrite.unchecked_urls
        or rewrite.unquoted
        or rewrite.raw_tags
        or rewrite.includes
        or rewrite.shapes
    ):
        return

    def clean(name: str) -> str:
        return name if _PLAIN_NAME.fullmatch(name) else "?"

    unchecked = tuple((clean(t), clean(a)) for t, a, _w in rewrite.unchecked_urls)
    unquoted = tuple((clean(t), clean(a)) for t, a, _w in rewrite.unquoted)
    raw_tags = tuple((clean(e), clean(n)) for e, n in rewrite.raw_tags)
    key = (
        owner,
        unchecked,
        unquoted,
        raw_tags,
        rewrite.includes,
        rewrite.include_names,
        rewrite.shapes,
    )
    if key in _WARNED or len(_WARNED) >= _MAX_WARNED:
        return
    _WARNED.add(key)
    includes = _unscanned_includes(rewrite) if rewrite.includes else 0
    fragments: list[str] = []
    advice: list[str] = []
    args: list[Any] = [owner]
    if unchecked:
        fragments.append("URL values escaped but not scheme-checked %s")
        args.append([f"{t}[{a}]" for t, a in unchecked])
        advice.append(
            "give a URL that can choose the scheme to the template as a plain top-level name"
        )
    if unquoted:
        fragments.append("unquoted values %s")
        args.append([f"{t}[{a}]" for t, a in unquoted])
        advice.append("quote an attribute value")
    if raw_tags:
        fragments.append(
            "tags that print text inside <textarea> / <title> / a comment, not escaped %s"
        )
        args.append([f"{e}:{n}" for e, n in raw_tags])
        advice.append("escape what a tag prints")
    if rewrite.shapes:
        fragments.append("shapes it cannot follow like a browser does %s")
        args.append(list(rewrite.shapes))
    if includes:
        fragments.append(
            "%d {%% include %%} / {%% extends %%} tag(s) whose templates hold slots that are not scanned"
        )
        args.append(includes)
        advice.append("write the attribute and escaped-text slots in the template itself")
    if not fragments:
        return
    logger.warning(
        "%s: hybrid template parts djust cannot make safe: "
        + "; ".join(fragments)
        + (". To fix: " + ", ".join(advice) + "." if advice else "."),
        *args,
    )


def harden_context(
    template: str, context: dict[str, Any], owner: str = ""
) -> tuple[str, dict[str, Any]]:
    """``(template, context)`` to hand the engine: the attribute slots of ``template`` rewritten
    and the values of the URL groups judged. ``context`` itself is not modified. ``owner`` is the
    component class name, for the warning."""
    rewrite = plan(template)
    _warn_once(owner, rewrite)
    if not (rewrite.groups or rewrite.raw):
        return rewrite.template, context
    hardened = dict(context)
    for key, name in rewrite.raw:
        value = context.get(name, _MISSING)
        if value is not _MISSING:
            deferred, shown = unmarked(value)
            if not deferred:
                hardened[key + "_p"] = True
                hardened[key] = shown
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
