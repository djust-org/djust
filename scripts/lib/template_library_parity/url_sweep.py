"""The URL-scheme sweep over djust's own template libraries (#3665).

``{% avatar src=u %}`` once emitted ``javascript:alert(1)`` verbatim under the
Django engine while the native handler emitted ``#``. The audit that found it
only drove parameters NAMED like URLs, so a parameter called ``photo`` or
``cover`` was never probed. This module removes the name from the question.

What it does, for every tag of every library in :data:`SWEPT_LIBRARIES`:

1. **Drive every parameter the tag can read**, one at a time, with every payload
   in :data:`PAYLOADS`, in four shapes (a string, a list of strings, a list of
   dicts whose every key is the payload, one dict) — not only the parameters
   whose name looks like a URL. The other parameters get a typical value, the
   way ``variants`` does.
2. **Render through BOTH real engines**: Django's (the Python tag) and
   ``DjustTemplateBackend`` (the Rust engine; a native handler wins, and a tag
   with none falls back to the Python tag).
3. **Read the FINAL emitted attributes** of each output — every attribute of
   every element, not a list of "URL-looking" parameter names — and report each
   whose browser-normalised value starts with any scheme but a navigation scheme
   (:data:`NAVIGATION_SCHEMES`, #3677: ``livescript:``, ``blob:``, ``file:``, ... as
   well as ``javascript:``, ``vbscript:`` and a non-image ``data:``), with the
   element and attribute it landed on.

The question is asked of the output, not of the sanitiser, so a tag that
reaches its sink by a path no sanitiser guards is found whatever it is called.
Percent-encoded, entity-encoded, case-folded and whitespace/control-character
obfuscated forms are in the corpus because a sanitiser that validates before a
decode step is defeated by the encoded form (the #1825 lesson).

It also reports, per (tag, parameter, shape, payload), whether the two engines
AGREE on what survived, so that the Python and Rust accept/reject sets are
compared on the same input rather than argued about.

Like ``harness``, this configures Django and replaces ``uuid`` / ``datetime``:
it only ever runs in a FRESH process (``scripts/template-library-url-sweep.py``).
"""

from __future__ import annotations

import contextlib
import dataclasses
import html
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional

from . import harness, variants

#: Every library of djust that renders markup from template arguments.
SWEPT_LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
    "theme_components": "djust.theming.templatetags.theme_components",
    "theme_pages": "djust.theming.templatetags.theme_pages",
    "theme_form_tags": "djust.theming.templatetags.theme_form_tags",
}

#: A token that survives any rewrite of the URL around it, so "did the value
#: reach the attribute" is a substring test.
MARK = "zq7"

#: ``(label, payload)`` — the attack forms. Each must be neutralised (``#`` or
#: dropped) at every sink that is not an image ``src``.
DANGEROUS = (
    ("js", "javascript:alert(%s)" % MARK),
    ("js-upper", "JAVASCRIPT:alert(%s)" % MARK),
    ("js-mixed", "JaVaScRiPt:alert(%s)" % MARK),
    ("js-lead-space", "  javascript:alert(%s)" % MARK),
    ("js-lead-newline", "\n\tjavascript:alert(%s)" % MARK),
    ("js-embedded-tab", "java\tscript:alert(%s)" % MARK),
    ("js-embedded-newline", "java\nscript:alert(%s)" % MARK),
    ("js-embedded-cr", "java\rscript:alert(%s)" % MARK),
    ("js-nul", "java\x00script:alert(%s)" % MARK),
    ("js-control-lead", "\x01\x02javascript:alert(%s)" % MARK),
    ("js-entity-hex", "jav&#x61;script:alert(%s)" % MARK),
    ("js-entity-dec", "&#106;avascript:alert(%s)" % MARK),
    ("js-entity-nosemi", "&#x6A&#x61vascript:alert(%s)" % MARK),
    ("js-entity-colon", "javascript&colon;alert(%s)" % MARK),
    ("js-percent-colon", "javascript%%3Aalert(%s)" % MARK),
    ("vbs", "vbscript:msgbox(%s)" % MARK),
    ("vbs-upper", "VBScript:msgbox(%s)" % MARK),
    ("data-html", "data:text/html,<script>alert(%s)</script>" % MARK),
    ("data-html-b64", "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==#%s" % MARK),
    ("data-upper", "DATA:text/html,<script>alert(%s)</script>" % MARK),
    ("data-svg", "data:image/svg+xml,<svg onload=alert(%s)>" % MARK),
    ("data-js", "data:application/javascript,alert(%s)" % MARK),
    # A ``srcset`` is a list, and a parser that nests parentheses (the spec's does not)
    # reads these as ONE candidate with odd descriptors while a browser reads the second
    # candidate as ``javascript:`` / ``data:text/html``. Relative URLs anywhere else.
    ("srcset-parens", "/a.png 10w ((), javascript:alert(%s) 100w" % MARK),
    ("srcset-parens-data", "/a.png 10w ((), data:text/html,<script>alert(%s)</script> 100w" % MARK),
    ("srcset-comma", "/a.png 1x,javascript:alert(%s) 2x" % MARK),
    ("srcset-formfeed", "/a.png 1x,\fjavascript:alert(%s) 2x" % MARK),
)

#: Schemes that are not script schemes in a current browser but are not navigation
#: schemes either (#3677): ``djust.components.utils`` allows only
#: :data:`NAVIGATION_SCHEMES`, so each of these must be neutralised exactly like
#: ``javascript:``. Written in the forms a browser still reads as that scheme
#: (case, leading space / C0 control, embedded tab or newline, a numeric entity).
NON_NAVIGATION = (
    ("livescript", "livescript:alert(%s)" % MARK),
    ("livescript-mixed", "LiveScript:alert(%s)" % MARK),
    ("mhtml", "mhtml:file://C:/%s.mht!x" % MARK),
    ("blob", "blob:https://example.com/%s" % MARK),
    ("blob-lead-space", "  blob:https://example.com/%s" % MARK),
    ("blob-lead-control", "\x01blob:https://example.com/%s" % MARK),
    # a leading C0 control (or NUL) is stripped by ``urlsplit`` and by a browser before the
    # scheme is read, for EVERY scheme, not only ``blob`` (#3748: the Rust avatar missed it)
    ("blob-lead-nul", "\x00blob:https://example.com/%s" % MARK),
    ("file-lead-control", "\x01file:///etc/%s" % MARK),
    ("intent-lead-control", "\x1fintent://example.com/%s#Intent;scheme=https;end" % MARK),
    ("blob-embedded-tab", "bl\tob:https://example.com/%s" % MARK),
    ("blob-embedded-newline", "bl\nob:https://example.com/%s" % MARK),
    ("blob-entity-hex", "&#x62;lob:https://example.com/%s" % MARK),
    ("file", "file:///etc/%s" % MARK),
    ("jar", "jar:https://example.com/a.jar!/%s" % MARK),
    ("view-source", "view-source:https://example.com/%s" % MARK),
    ("intent", "intent://example.com/%s#Intent;scheme=https;end" % MARK),
    ("ms-word", "ms-word:ofe|u|https://example.com/%s.docx" % MARK),
    ("custom", "x-%s-app://open/1" % MARK),
    ("feed", "feed:https://example.com/%s.xml" % MARK),
    ("ws", "ws://example.com/%s" % MARK),
    ("git", "git://example.com/%s.git" % MARK),
    ("sms", "sms:+15551234567?body=%s" % MARK),
    ("geo", "geo:40.7,-74.0?q=%s" % MARK),
)
DANGEROUS = DANGEROUS + NON_NAVIGATION

#: The schemes a navigation URL may carry. An INDEPENDENT copy of
#: ``djust.components.utils._SAFE_URL_SCHEMES``: the scanner that decides whether an
#: output is dangerous must not read the allowlist under test as its oracle (a widened
#: production list would otherwise widen the scanner with it).
#: ``python/tests/test_url_scheme_sweep_3665.py`` pins the two equal.
NAVIGATION_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "ftps"})

#: Values every sink must keep. ``preserved`` is compared between the engines.
SAFE = (
    ("https", "https://example.com/%s?a=1&b=2" % MARK),
    ("http", "http://example.com/%s" % MARK),
    ("relative", "/rel/%s/path" % MARK),
    ("relative-dots", "../up/%s.png" % MARK),
    ("anchor", "#%s" % MARK),
    ("query", "?q=%s" % MARK),
    ("scheme-relative", "//cdn.example.com/%s.png" % MARK),
    ("mailto", "mailto:a@example.com?subject=%s" % MARK),
    ("tel", "tel:+15551234567;%s" % MARK),
    ("colon-in-path", "/a:b/%s" % MARK),
    ("data-png", "data:image/png;base64,iVBORw0KGgo=#%s" % MARK),
)

PAYLOADS = DANGEROUS + SAFE

#: The shapes an argument can take; see :func:`build_context`. The first four
#: drive ONE parameter at a time; ``keyref`` drives ALL of them at once with the
#: payload stored under a key that other arguments NAME (``columns=[{"link":
#: "u"}]``, ``rows=[{"u": payload}]``, ``row_url="u"``), which is how a table
#: reaches a URL it was never handed directly.
SHAPES = ("scalar", "list_str", "list_dict", "dict")
KEYREF = "keyref"
#: A ``mark_safe`` payload, driven separately: ``url_attr`` documents that a
#: value the developer marked safe is emitted as written (the author's explicit
#: opt-out, the same contract as ``{{ x|safe }}``). Recorded, never a hole.
SAFE_SHAPE = "scalar_safe"
KEYREF_KEY = "u"


def passthrough_params() -> tuple[str, ...]:
    """The attribute names a tag that takes ``**attrs`` / ``**kwargs`` is driven with, in
    addition to its named parameters (#3749).

    A ``**attrs`` tag has no parameter NAMED like a URL, but it may print any name it is
    given (``{% theme_input name="q" ping=u %}`` -> ``<input ping="...">``), so each URL
    attribute the scanner reads is a parameter such a tag can have. Derived from
    :data:`URL_ATTRS` (an XML name cannot be a keyword, so ``xlink:href`` is not here), the
    framework's own URL-valued directives (``dj-navigate``, ``dj-patch``, ``dj-patch-reload``)
    and one author-defined ``data-*`` name the scanner reads (``data-url``). A tag that ignores
    them is driven for nothing and costs a render; one that prints them is a sink.
    """
    names = {a.replace("-", "_") for a in URL_ATTRS if ":" not in a}
    return tuple(sorted(names | {"dj_navigate", "dj_patch", "dj_patch_reload", "data_url"}))


def takes_varkw(info: variants.TagInfo) -> bool:
    """Whether the tag's Python signature has a ``**kwargs`` parameter."""
    return any(s[2] == "VAR_KEYWORD" for s in info.sig)


#: Keys a dict argument is given beyond the ones the tag's own source reads: the
#: names URLs travel under, so a tag that reads one through a helper this
#: module cannot follow is still driven. The Rust engine serialises the whole
#: context on every render, so this stays small.
COMMON_KEYS = (
    "url", "href", "src", "link", "image", "avatar", "thumbnail", "logo", "icon", "photo",
    "poster", "action", "label", "name", "title", "text", "id", "value",
)  # fmt: skip

#: Keys whose value is itself a list of the same kind of dict (one level).
NESTED_KEYS = (
    "children", "items", "links", "actions", "options", "tabs", "menu", "submenu", "nodes",
    "sub", "entries", "cells", "columns", "users", "members", "slides", "images", "sections",
    "steps", "events", "crumbs",
)  # fmt: skip


# --------------------------------------------------------------------------- #
# Reading the emitted attributes

_TAG = re.compile(r"<([A-Za-z][A-Za-z0-9:-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ATTR = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_STRIP = re.compile(r"[\x00-\x20]+")
_DATA_IMAGE = re.compile(r"^data:image/[a-z0-9.+-]+[;,]")
#: Attributes a browser itself follows when activated or loaded.
_EXEC = frozenset(
    {
        ("a", "href"), ("area", "href"), ("form", "action"), ("button", "formaction"),
        ("input", "formaction"), ("iframe", "src"), ("frame", "src"), ("embed", "src"),
        ("object", "data"), ("script", "src"), ("base", "href"),
    }
)  # fmt: skip


@dataclass(frozen=True)
class Attr:
    element: str
    name: str
    value: str  # entity-decoded, as the browser reads it


def attributes(markup: str) -> Iterator[Attr]:
    """Every attribute of every start tag in ``markup``, values entity-decoded.

    Deliberately regex + ``html.unescape`` and not ``html.parser`` (see
    ``classify``'s module docstring: that parser's behaviour moves with the
    interpreter's security releases)."""
    for tag in _TAG.finditer(_COMMENT.sub("", markup)):
        element = tag.group(1).lower()
        for m in _ATTR.finditer(tag.group(2)):
            raw = next((g for g in m.groups()[1:] if g is not None), "")
            yield Attr(element, m.group(1).lower(), html.unescape(raw))


class _Reader:
    """A cursor over a string: the HTML spec's "position" and its "collect a sequence
    of code points" helper, nothing else."""

    ASCII_WHITESPACE = frozenset("\t\n\f\r ")

    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def at_end(self) -> bool:
        return self.position >= len(self.text)

    def current(self) -> Optional[str]:
        return None if self.at_end() else self.text[self.position]

    def collect(self, keep: Callable[[str], bool]) -> str:
        start = self.position
        while not self.at_end() and keep(self.text[self.position]):
            self.position += 1
        return self.text[start : self.position]


def spec_srcset(text: str) -> list[tuple[str, list[str]]]:
    """``[(url, descriptors)]`` per the HTML spec's "parse a srcset attribute".

    AN INDEPENDENT PORT, written from the spec text
    (https://html.spec.whatwg.org/multipage/images.html#parse-a-srcset-attribute)
    and deliberately sharing no code with ``djust.components.utils._srcset_candidates``:
    the scanner that decides whether a ``srcset`` is dangerous, and the tests that
    check production's parser, must not use the parser under test as their oracle (a
    shared bug would then be invisible to both). ``test_url_scheme_sweep_3665`` fuzzes
    the two against each other.

    Like production it returns every candidate's URL, including candidates a browser
    would drop for invalid descriptors: a URL that reaches the browser through a
    candidate the checker skipped is the bypass to find.
    """
    ws = _Reader.ASCII_WHITESPACE
    reader = _Reader(text)
    candidates: list[tuple[str, list[str]]] = []
    while True:
        # 4. splitting loop
        reader.collect(lambda ch: ch in ws or ch == ",")
        if reader.at_end():  # 5.
            return candidates
        url = reader.collect(lambda ch: ch not in ws)  # 6.
        descriptors: list[str] = []  # 7.
        if url.endswith(","):  # 8.
            url = url.rstrip(",")
        else:
            # 8.1 descriptor tokenizer
            reader.collect(lambda ch: ch in ws)
            current = ""
            state = "in descriptor"
            while True:
                c = reader.current()  # None is the spec's EOF
                if state == "in descriptor":
                    if c is None:
                        if current:
                            descriptors.append(current)
                        break
                    elif c in ws:
                        if current:
                            descriptors.append(current)
                            current = ""
                        state = "after descriptor"
                    elif c == ",":
                        reader.position += 1
                        if current:
                            descriptors.append(current)
                        break
                    elif c == "(":
                        current += c
                        state = "in parens"
                    else:
                        current += c
                elif state == "in parens":
                    if c is None:
                        descriptors.append(current)
                        break
                    elif c == ")":
                        current += c
                        state = "in descriptor"
                    else:
                        current += c
                else:  # "after descriptor"
                    if c is None:
                        break
                    elif c in ws:
                        pass
                    else:
                        state = "in descriptor"
                        reader.position -= 1
                reader.position += 1
        candidates.append((url, descriptors))


_C0_AND_SPACE = "".join(chr(c) for c in range(0x21))
_TAB_NEWLINE = re.compile(r"[\t\n\r]")
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):")


def scheme_of(value: str) -> str:
    """The scheme a browser would act on that is not a navigation scheme, ``""`` for none.

    ``javascript:`` / ``vbscript:`` / ``data:`` are found the lenient way (every control
    character and space removed, as legacy engines did), so ``java<NUL>script:`` counts.
    Any other scheme is read the way the URL standard reads it: leading and trailing C0
    controls and spaces are dropped, tabs and newlines are removed anywhere, and what
    precedes the first ``:`` is a scheme only if it is ``[a-z][a-z0-9+.-]*``. A scheme in
    :data:`NAVIGATION_SCHEMES` is allowed, so it reads as ``""`` (#3677)."""
    probe = _STRIP.sub("", value).lower()
    for scheme in ("javascript:", "vbscript:", "data:"):
        if probe.startswith(scheme):
            return scheme[:-1]
    m = _SCHEME.match(_TAB_NEWLINE.sub("", value.strip(_C0_AND_SPACE)).lower())
    if m and m.group(1) not in NAVIGATION_SCHEMES:
        return m.group(1)
    return ""


#: The attributes HTML defines as holding a URL, plus ``xlink:href``. Every other
#: standard attribute (``aria-label``, ``alt``, ``title``, ``value`` ...) is text:
#: a payload that lands there is a label, not a link. ``data-*`` and ``dj-*`` are
#: author-defined and may be read by script, so they count (class ``js``).
URL_ATTRS = frozenset(
    {
        "href", "src", "action", "formaction", "data", "poster", "srcset", "background", "cite",
        "longdesc", "manifest", "ping", "codebase", "usemap", "icon", "profile", "xlink:href",
        "archive", "classid", "itemid", "itemtype",
    }
)  # fmt: skip


#: Words that make a custom ``data-*`` / ``dj-*`` attribute a URL. The rest
#: (``dj-click`` and ``data-*-event`` carry an event NAME, ``data-id`` /
#: ``data-value`` an identifier, ``data-target`` a selector or a date, ...) are
#: opaque strings the page sends to the server or looks up; a payload there is
#: not navigated.
_URL_WORDS = frozenset(
    {
        "url", "href", "src", "link", "uri", "redirect", "endpoint", "image", "img", "avatar",
        "poster", "background", "thumbnail", "thumb", "logo", "asset", "download",
        "navigate", "location", "action",
    }
)  # fmt: skip


def is_custom_attr(name: str) -> bool:
    return name.startswith(("data-", "dj-"))


def is_url_attr(name: str) -> bool:
    if name in URL_ATTRS:
        return True
    if not is_custom_attr(name):
        return False
    words = set(re.split(r"[-.]", name))
    return bool(words & _URL_WORDS) and "event" not in words


def is_dangerous(attr: Attr) -> bool:
    """Whether ``attr`` carries a URL whose scheme is not a navigation scheme.

    ``javascript:`` / ``vbscript:`` and every other scheme outside
    :data:`NAVIGATION_SCHEMES` (#3677) anywhere; ``data:`` unless it is an image
    URI on an attribute that only ever loads an image (``<img src>``,
    ``poster``, ``srcset`` on any element) or the resource a ``<link>`` loads (a stylesheet,
    manifest or icon) — there ``data:`` is legitimate and runs no script.
    """
    if attr.name == "srcset":
        # A candidate's URL ends at whitespace, so ``java<TAB>script:`` is the
        # URL ``java`` and a descriptor here, unlike in an ``href``. Split by the
        # INDEPENDENT port of the spec (``spec_srcset``), never by production's parser.
        # A ``srcset`` is only ever fetched AS AN IMAGE (on ``<img>`` / ``<source>``; on any
        # other element no browser fetches it at all), so each candidate is judged as an image
        # source whatever the element: ``data:image/...`` passes, ``data:text/html`` and the
        # script schemes do not. That is production's decision too (``url_allowed(url,
        # image=True)`` for every ``srcset`` in ``djust/_attr_policy.py``), recorded in #4130.
        return any(is_dangerous(Attr("img", "src", url)) for url, _desc in spec_srcset(attr.value))
    scheme = scheme_of(attr.value)
    if not scheme or not is_url_attr(attr.name):
        return False
    if scheme == "data":
        if attr.element == "link":
            return False  # a stylesheet, manifest or icon URI: a resource, never a document
        image_sink = (
            attr.element in ("img", "source", "image") and attr.name in ("src", "srcset")
        ) or (attr.name == "poster")
        probe = _STRIP.sub("", attr.value).lower()
        return not (image_sink and _DATA_IMAGE.match(probe))
    return True


def danger_class(attr: Attr) -> str:
    """How an unsafe URL on ``attr`` is reached: a browser follows it (``exec``),
    djust's or the page's JavaScript may read it (``js``), or it is inert (``inert``)."""
    if (attr.element, attr.name) in _EXEC or attr.name in ("xlink:href",):
        return "exec"
    if attr.name.startswith(("data-", "dj-")) or attr.name in ("srcdoc", "style", "on"):
        return "js"
    if attr.name.startswith("on"):
        return "exec"
    return "inert"


def findings_in(attrs: Iterable[Attr]) -> list[tuple[str, str, str, str]]:
    """:func:`findings` over attributes already read from the markup."""
    return sorted(
        {(a.element, a.name, danger_class(a), scheme_of(a.value)) for a in attrs if is_dangerous(a)}
    )


def findings(markup: str) -> list[tuple[str, str, str, str]]:
    """``[(element, attribute, class, scheme)]`` of every dangerous attribute, sorted."""
    return findings_in(attributes(markup))


def marked_attrs(attrs: Iterable[Attr]) -> list[Attr]:
    """The URL attributes whose value holds :data:`MARK`: the only ones :func:`carries_in` reads."""
    return [a for a in attrs if is_url_attr(a.name) and MARK in a.value]


def carries_in(marked: Iterable[Attr], payload: str) -> bool:
    """:func:`carries` over the output of :func:`marked_attrs`."""
    head = _STRIP.sub("", html.unescape(payload)).lower()[:5]
    return any(_STRIP.sub("", a.value).lower().startswith(head) for a in marked)


def carries(markup: str, payload: str) -> bool:
    """Whether some attribute's value is (a rewrite of) ``payload`` — it survived.

    Matched on the unique :data:`MARK` plus the payload's own first characters,
    so alt text that merely echoes the value does not count.
    """
    return carries_in(marked_attrs(attributes(markup)), payload)


# --------------------------------------------------------------------------- #
# Driving the tags


def dict_of(payload: str, keys: Iterable[str], depth: int = 1) -> dict[str, Any]:
    """A dict whose every key is ``payload``; the nested-list keys the tag reads
    hold one dict of the same kind."""
    keys = set(keys)
    out: dict[str, Any] = {k: payload for k in keys}
    if depth:
        for k in keys & set(NESTED_KEYS):
            out[k] = [dict_of(payload, keys - set(NESTED_KEYS), depth - 1)]
    return out


def build_context(payload: str, keys: Iterable[str]) -> dict[str, Any]:
    from django.utils.safestring import mark_safe

    keys = sorted(set(keys))
    row = {k: KEYREF_KEY for k in keys}
    row[KEYREF_KEY] = payload
    return {
        "p_scalar_safe": mark_safe(payload),
        "p_keyref_list": [dict(row), dict(row)],
        "p_keyref_str": KEYREF_KEY,
        "p_scalar": payload,
        "p_list_str": [payload, payload],
        "p_list_dict": [dict_of(payload, keys), dict_of(payload, keys)],
        "p_dict": dict_of(payload, keys),
        "evil": harness.EVIL,
        "lst_dict": [],
        "lst_str": ["alpha", "beta"],
        "none": None,
        "empty": "",
        "n": 3,
    }


def keys_read(info: variants.TagInfo, fn: Any) -> set[str]:
    """Dict keys the tag's Python implementation and its native handler read."""
    keys = set(info.py_keys) | set(info.rust_keys or ())
    if fn is not None:
        found, params = harness.discover(fn)
        keys |= found | params
    return keys


#: Block tags whose body must itself contain a tag to compile.
BODIES = {"split_pane": "LEFT{% pane %}RIGHT"}


def end_tag(info: variants.TagInfo) -> str:
    """The closing tag of a block tag. A closing tag is not registered (the opening
    tag's compile function consumes it), so it is derived from the two conventions
    djust uses: ``end<name>`` (``{% endmodal %}``) and, for the theming library's
    block forms, ``end_<name>`` (``{% end_theme_card_block %}``)."""
    if info.lib.startswith("theme_") and info.name.endswith("_block"):
        return "end_" + info.name
    return "end" + info.name


def wrap(info: variants.TagInfo, args: str, parent: Optional[str], parent_args: str) -> str:
    head = "{% " + info.name + (" " + args if args else "") + " %}"
    inner = head
    if info.is_block:
        inner = head + BODIES.get(info.name, variants.BODY) + "{% " + end_tag(info) + " %}"
    if parent:
        open_tag = "{% " + parent + (" " + parent_args if parent_args else "") + " %}"
        inner = open_tag + inner + "{% end" + parent + " %}"
    return "{% load " + info.lib + " %}" + inner


def typical_args(info: variants.TagInfo, skip: str) -> list[str]:
    """The booleans and required parameters a tag needs in order to render."""
    names = variants.passed_names(info)
    required = [s[0] for s in info.sig if s[1] == "req" and s[0] != "context"]
    if info.name.endswith("_block"):
        required = []  # the block between the tags fills the one the inline form requires
    fixed = list(dict.fromkeys([n for n in names if variants.kind_of(n) == "bool"] + required))
    return ["%s=%s" % (m, variants.token(m, "typical")) for m in fixed if m != skip and m in names]


@dataclass
class Cell:
    """One (tag, parameter, shape, payload) measured in both engines."""

    tag: str
    param: str
    shape: str
    payload: str
    label: str
    python: Optional[list[tuple[str, str, str, str]]] = None  # None: did not render
    rust: Optional[list[tuple[str, str, str, str]]] = None
    #: whether the value survived into a URL attribute; ``None`` where nothing reads it (see
    #: :func:`needs_carries`), which is every cell of a tag with no native handler that is not
    #: driven with the ``https`` value
    python_carries: Optional[bool] = None
    rust_carries: Optional[bool] = None
    native: bool = False

    @property
    def rendered_both(self) -> bool:
        return self.python is not None and self.rust is not None


@dataclass
class Sweep:
    cells: list[Cell] = field(default_factory=list)
    #: ``"<lib>:<tag>"`` -> parameters driven
    driven: dict[str, list[str]] = field(default_factory=dict)
    #: tags with no parameter at all, nothing to drive
    no_params: list[str] = field(default_factory=list)
    #: tags none of whose calls rendered in any engine, so nothing was learned
    unrendered: list[str] = field(default_factory=list)
    probe: dict[str, str] = field(default_factory=dict)
    #: how many context variables the renders were given in all (a full context repeats the
    #: whole set for every call), so a run says whether it took the shortcut
    context_names: int = 0

    @property
    def carries_measured(self) -> int:
        """How many cells had ``carries`` measured (every cell, under ``exhaustive``)."""
        return sum(1 for c in self.cells if c.python_carries is not None)


def inventory() -> dict[str, variants.TagInfo]:
    """:func:`harness.inventory` over :data:`SWEPT_LIBRARIES`."""
    saved = dict(harness.LIBRARIES)
    harness.LIBRARIES.clear()
    harness.LIBRARIES.update(SWEPT_LIBRARIES)
    try:
        return harness.inventory()
    finally:
        harness.LIBRARIES.clear()
        harness.LIBRARIES.update(saved)


def _fn_of(info: variants.TagInfo) -> Any:
    from django.template.library import import_library

    library = import_library(SWEPT_LIBRARIES[info.lib])
    fn = library.tags[info.name]
    return getattr(fn, "__wrapped__", fn)


def _render(engine: Any, src: str, ctx: dict[str, Any]) -> Optional[str]:
    harness.reset_ids()
    try:
        return str(engine.from_string(src).render(ctx))
    except Exception:  # noqa: BLE001 - a call a tag rejects tells us nothing
        return None


#: What a planted hole emits: a link that runs script.
PLANT = '<a href="javascript:alert(%s)" data-url-sweep-plant>x</a>' % MARK


@contextlib.contextmanager
def planted_hole(key: str, when: Optional[str] = None, markup: str = PLANT) -> Iterator[None]:
    """Make the PYTHON tag ``"<label>:<name>"`` emit :data:`PLANT` (gate-off).

    Wraps the library's compile function the way ``harness.planted_difference``
    does. The sweep must report a Python hole for the tag, which proves a hole
    that exists is found end to end (payload -> engine -> scan -> report).
    Restored on exit.

    ``when`` plants only in calls that pass that parameter (``cite`` -> a call with ``cite=``)
    and ``markup`` replaces :data:`PLANT`: together they put a hole on ONE parameter, which is
    how the decided-hole baseline is checked to excuse its decision and nothing else (#4130)."""
    from django.template.library import import_library

    label, name = key.split(":", 1)
    library = import_library(SWEPT_LIBRARIES[label])
    original = library.tags[name]

    def planted(parser: Any, token: Any) -> Any:
        node = original(parser, token)
        if when is not None and not any(
            bit.startswith(when + "=") for bit in token.split_contents()[1:]
        ):
            return node
        inner = node.render
        node.render = lambda context: inner(context) + markup
        return node

    library.tags[name] = planted
    try:
        yield
    finally:
        library.tags[name] = original


def calls_for(
    info: variants.TagInfo, shapes: Iterable[str]
) -> Iterator[tuple[str, str, list[str]]]:
    """``(parameter, shape, argument list)`` for every call driven for ``info``.

    One call per (parameter, shape): the parameter under test takes the shaped
    payload, the booleans and required parameters take a typical value. Then
    ``keyref``: every parameter at once, list-valued ones (by name) holding rows
    with the payload under :data:`KEYREF_KEY`, the rest naming that key."""
    names = variants.passed_names(info)
    for name in names:
        for shape in (*shapes, SAFE_SHAPE):
            yield name, shape, typical_args(info, name) + ["%s=p_%s" % (name, shape)]
    if takes_varkw(info):
        for extra in passthrough_params():
            if extra in names:
                continue
            # an attribute VALUE is a scalar (a list or a dict is not what a caller writes
            # after ``ping=``), so only the plain and the marked-safe shapes are driven
            for shape in ("scalar", SAFE_SHAPE):
                if shape == SAFE_SHAPE or shape in shapes:
                    yield extra, shape, typical_args(info, extra) + ["%s=p_%s" % (extra, shape)]
    if names:
        keyref = [
            "%s=%s" % (n, "p_keyref_list" if variants.kind_of(n) == "list" else "p_keyref_str")
            for n in names
        ]
        yield "*", KEYREF, keyref
        yield "*", KEYREF + "_lists", ["%s=p_keyref_list" % n for n in names]


def referenced_names(names: Iterable[str], src: str) -> tuple[str, ...]:
    """The context names ``src`` can read: each of ``names`` that appears in it as a word.

    A call's template names the context variables it reads (``p_scalar``, ``bound_form``,
    ``lst_str`` ...), and a variable it does not name cannot reach its output. The Rust engine
    serialises the WHOLE context on every render (a recursive walk of every dict, 3-4 ms for
    ``data_table``'s 127 keys, about 85% of the sweep's time), so each call is given only the
    variables it names. Over-inclusion is harmless (a name found inside a string literal is
    passed and unused); a name this test misses would be dropped, which is why
    ``python/tests/test_url_scheme_sweep_capacity.py`` renders both ways and compares."""
    return tuple(n for n in names if re.search(r"\b%s\b" % re.escape(n), src))


def needs_carries(native: bool, label: str) -> bool:
    """Whether :attr:`Cell.python_carries` / :attr:`Cell.rust_carries` are read for a cell.

    Only :func:`disagreements` (a tag with a native handler) and the ``reached`` set (the
    ``https`` value arriving at an attribute) read them, so measuring them for the rest, which
    is most cells, costs a scan of the output for nothing."""
    return native or label == "https"


def run_sweep(
    payloads: Iterable[tuple[str, str]] = PAYLOADS,
    only: Optional[set[str]] = None,
    shapes: Iterable[str] = SHAPES,
    on_tag: Optional[Callable[[str], None]] = None,
    plant: Optional[str] = None,
    exhaustive: bool = False,
    plant_when: Optional[str] = None,
    plant_markup: str = PLANT,
) -> Sweep:
    """Drive every parameter of every tag in both engines. Fresh process only.

    ``plant`` is ``"<label>:<tag>"``: see :func:`planted_hole`.

    ``exhaustive`` turns off the three shortcuts that make the sweep affordable: every call
    gets the WHOLE context (:func:`referenced_names`), every output is read again, and
    ``carries`` is measured for every cell (:func:`needs_carries`). The shortcuts must change
    nothing, so ``python/tests/test_url_scheme_sweep_capacity.py`` runs both and
    compares :func:`digest`."""
    from django.template import engines

    python_engine = engines["django"]
    rust_engine = engines["djust"]
    infos = inventory()
    sweep = Sweep(probe=harness._probe())
    payloads = tuple(payloads)
    shapes = tuple(shapes)
    for key, info in infos.items():
        if only and key not in only and info.name not in only:
            continue
        if info.name.startswith("end") and info.name[3:].lstrip("_") in {
            i.name.removesuffix("_block") for i in infos.values() if i.lib == info.lib
        }:
            continue  # a closing tag, driven as part of its opening tag
        if on_tag:
            on_tag(key)
        base = infos.get("%s:%s" % (info.lib, info.name.removesuffix("_block")))
        if info.name.endswith("_block") and base is not None and base is not info:
            # ``theme_card_block`` takes the parameters of ``theme_card`` (its body
            # fills one); its own compile function exposes no names to discover.
            info = dataclasses.replace(
                info,
                py_kind=base.py_kind,
                sig=base.sig,
                py_keys=base.py_keys,
                rust_keys=base.rust_keys,
            )
        names = variants.passed_names(info)
        sweep.driven[key] = list(names) + (
            [x for x in passthrough_params() if x not in names] if takes_varkw(info) else []
        )
        if not names:
            sweep.no_params.append(key)
            continue
        keys = keys_read(info, _fn_of(info)) | set(COMMON_KEYS)
        parent = variants.PARENT.get(info.name)
        parent_info = infos.get("%s:%s" % (info.lib, parent)) if parent else None
        parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""
        calls = [
            (param, shape, wrap(info, " ".join(args), parent, parent_args))
            for param, shape, args in calls_for(info, shapes)
        ]
        any_rendered = False
        context_names = tuple(build_context("", keys)) + ("bound_form",)
        needed = [referenced_names(context_names, src) for _param, _shape, src in calls]
        # Reading an output (every attribute of every element, entity-decoded) is most of what
        # is left once the render is cheap, and many cells of one tag print the same markup (a
        # parameter the tag ignores prints the same page for every payload), so each distinct
        # output is read once and the cells share the result. Per tag, so a large tag's outputs
        # are not all held at once.
        read: dict[str, tuple[list[tuple[str, str, str, str]], list[Attr]]] = {}

        def reading(out: str) -> tuple[list[tuple[str, str, str, str]], list[Attr]]:
            if exhaustive or out not in read:
                attrs = list(attributes(out))
                read[out] = (findings_in(attrs), marked_attrs(attrs))
            return read[out]

        def scan(out: Optional[str]) -> Optional[list[tuple[str, str, str, str]]]:
            return None if out is None else list(reading(out)[0])

        def survived(out: Optional[str], payload: str) -> bool:
            return out is not None and carries_in(reading(out)[1], payload)

        for label, payload in payloads:
            ctx = build_context(payload, keys)
            if "form" in names:
                ctx["bound_form"] = harness._bound_form()
            for (param, shape, src), reads in zip(calls, needed):
                if "form" in names:
                    # a bound Django form, not a string: the rest of the call still runs
                    src = re.sub(r"\bform=p_\w+", "form=bound_form", src)
                    reads = tuple(dict.fromkeys((*reads, "bound_form")))
                call_ctx = ctx if exhaustive else {n: ctx[n] for n in reads if n in ctx}
                sweep.context_names += len(call_ctx)
                with (
                    planted_hole(plant, plant_when, plant_markup)
                    if plant == key
                    else contextlib.nullcontext()
                ):
                    py_out = _render(python_engine, src, call_ctx)
                rs_out = _render(rust_engine, src, call_ctx)
                any_rendered |= py_out is not None or rs_out is not None
                carried = exhaustive or needs_carries(bool(info.native), label)
                sweep.cells.append(
                    Cell(
                        key,
                        param,
                        shape,
                        payload,
                        label,
                        scan(py_out),
                        scan(rs_out),
                        survived(py_out, payload) if carried else None,
                        survived(rs_out, payload) if carried else None,
                        bool(info.native),
                    )
                )
        if not any_rendered:
            sweep.unrendered.append(key)
    return sweep


# --------------------------------------------------------------------------- #
# Reading a sweep


def holes(sweep: Sweep) -> dict[tuple[str, str, str, str, str], set[str]]:
    """``{(engine, tag, param, element, attribute): {labels}}`` for every
    dangerous payload that reached a dangerous attribute.

    Safe payloads cannot be holes by definition; ``data-image`` style values on
    an image sink are not dangerous (:func:`is_dangerous`)."""
    dangerous_labels = {label for label, _ in DANGEROUS}
    out: dict[tuple[str, str, str, str, str], set[str]] = {}
    for cell in sweep.cells:
        if cell.label not in dangerous_labels or cell.shape == SAFE_SHAPE:
            continue
        for engine, found in (("python", cell.python), ("rust", cell.rust)):
            for element, attr, cls, _scheme in found or ():
                out.setdefault(
                    (engine, cell.tag, cell.param, element, "%s:%s" % (attr, cls)), set()
                ).add(cell.label)
    return out


def digest(sweep: Sweep) -> str:
    """A hash of everything the sweep measured: every cell's findings in both engines and, where
    something reads it (:func:`needs_carries`), whether the value survived.

    Two sweeps with the same digest found the same thing in every cell, which is how the
    shortcuts of :func:`run_sweep` are shown to change nothing."""
    import hashlib

    h = hashlib.sha256()
    for c in sweep.cells:
        read = needs_carries(c.native, c.label)
        h.update(
            repr(
                (
                    c.tag,
                    c.param,
                    c.shape,
                    c.label,
                    c.python,
                    c.rust,
                    c.native,
                    bool(c.python_carries) if read else None,
                    bool(c.rust_carries) if read else None,
                )
            ).encode()
        )
    return h.hexdigest()


def disagreements(sweep: Sweep) -> list[Cell]:
    """Cells both engines rendered where the Python tag and the native handler
    disagree on whether the value survived (``carries``) or on whether the
    output is safe — for a tag that HAS a native handler (without one both
    engines run the same Python tag)."""
    out = []
    for cell in sweep.cells:
        if not (cell.native and cell.rendered_both) or cell.shape == SAFE_SHAPE:
            continue
        if cell.python_carries != cell.rust_carries or bool(cell.python) != bool(cell.rust):
            out.append(cell)
    return out


# --------------------------------------------------------------------------- #
# The class-based components
#
# ``{% avatar %}`` has a sibling, ``djust.components.components.avatar.Avatar``:
# the same markup built by a ``Component`` subclass that a view renders with
# ``.render()``. They are separate implementations, so a sanitiser added to one
# does not reach the other, and the tag sweep above never touches them. The same
# question is asked of them: every constructor parameter, every payload, every
# shape, the FINAL attributes.

#: Packages of ``djust.components`` that hold renderable classes. ``ttyd`` holds
#: ``TtydTerminalView``, whose WebSocket target came from ``?url=`` unchecked
#: (#3679); the top-level modules ``_interactive`` and ``interactive_examples``
#: (``DropdownMenu``, ``DropdownMenuExample``) are not packages and print no URL.
FRAMEWORKS = ("bootstrap5", "tailwind", "plain")
COMPONENT_PACKAGES = (
    "components", "ui", "data", "layout", "forms", "descriptors", "gallery", "ttyd",
)  # fmt: skip
#: A value that satisfies a required constructor parameter without being a URL.
FILLER = {
    "label": "x", "id": "x", "name": "x", "text": "x", "title": "x", "value": "x", "content": "x",
    "key": "x", "icon": "x", "body": "x", "message": "x",
}  # fmt: skip


def filler_for(name: str) -> Any:
    """A value that satisfies a required parameter without being a URL: a list of
    plain dicts for a list, ``1`` for a number or a page count, else ``"x"``."""
    kind = variants.kind_of(name)
    if kind == "list":
        return [dict(FILLER)]
    if kind == "num" or name.endswith(("_page", "_pages", "_count", "_value")):
        return 1
    return "x"


def _all_component_classes() -> dict[str, type]:
    """Every class the packages define that renders markup: a ``Component``, a
    ``LiveComponent`` or a ``LiveView`` (the gallery's pages), whether or not it
    can be driven."""
    import importlib
    import inspect
    import pkgutil

    from djust import Component, LiveView
    from djust.components.base import LiveComponent

    found: dict[str, type] = {}
    for sub in COMPONENT_PACKAGES:
        package = importlib.import_module("djust.components." + sub)
        for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            module = importlib.import_module(info.name)
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, (Component, LiveComponent, LiveView))
                    and obj.__module__ == module.__name__
                ):
                    found[f"{obj.__module__}.{name}"] = obj
    return dict(sorted(found.items()))


def undriven_reason(cls: type) -> Optional[str]:
    """Why ``cls`` cannot be driven generically, or ``None`` when it can be.

    Derived from the class, not listed by name, so a new class lands in the
    driven set by default and is excluded only for one of these reasons."""
    from djust import LiveView
    from djust.components.base import LiveComponent

    if issubclass(cls, LiveView):
        return "a LiveView page: it renders through a request and a template, not a constructor"
    if issubclass(cls, LiveComponent) and cls.__module__.startswith(
        "djust.components.descriptors."
    ):
        return (
            "a descriptor component: its markup is the CALLER's template (it will not "
            "construct without one) and the class prints no URL itself"
        )
    return None


def component_classes() -> dict[str, type]:
    """``{"module.Class": cls}`` for every class the sweep can drive."""
    return {k: c for k, c in _all_component_classes().items() if undriven_reason(c) is None}


def undriven_classes() -> dict[str, str]:
    """``{"module.Class": reason}`` for the classes the sweep cannot drive."""
    return {
        k: reason
        for k, c in _all_component_classes().items()
        if (reason := undriven_reason(c)) is not None
    }


def component_params(cls: type) -> tuple[list[str], dict[str, Any]]:
    """``(every parameter, filler for the required ones)``.

    A ``Component`` takes its parameters in ``__init__``. A ``LiveComponent``
    takes ``**kwargs`` and reads them in ``mount`` and its helpers, so its
    parameters are the keys the class source reads (``kwargs.get("brand_href")``,
    followed through helpers), plus its ``State`` fields."""
    import inspect

    from djust.components.base import LiveComponent

    if issubclass(cls, LiveComponent):
        keys, params = harness.discover(cls)
        state = getattr(cls, "State", None)
        state_names = {n for n in vars(state) if not n.startswith("_")} if state else set()
        return sorted((keys | params | state_names) - {"self", "kwargs", "args"}), {}
    params = inspect.signature(cls.__init__).parameters  # type: ignore[misc]
    names, required = [], {}
    for n, p in params.items():
        if n == "self" or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        names.append(n)
        if p.default is inspect.Parameter.empty:
            required[n] = filler_for(n)
    return names, required


@dataclass
class ComponentCell:
    cls: str
    param: str
    shape: str
    label: str
    found: list[tuple[str, str, str, str]]


def run_component_sweep(
    payloads: Iterable[tuple[str, str]] = PAYLOADS,
    only: Optional[set[str]] = None,
    shapes: Iterable[str] = SHAPES,
    plant: Optional[tuple[str, str]] = None,
    plant_markup: str = PLANT,
) -> tuple[list[ComponentCell], dict[str, list[str]], list[str]]:
    """``(cells, {class: parameters driven}, classes that never rendered)``.

    ``plant`` is ``(class key, parameter)``: the output of every call driving that parameter
    gets ``plant_markup`` appended (gate-off for the decided-hole baseline, #4130)."""
    from django.utils.safestring import mark_safe

    cells: list[ComponentCell] = []
    driven: dict[str, list[str]] = {}
    unrendered: list[str] = []
    keys = set(COMMON_KEYS)
    for key, cls in component_classes().items():
        if only and key not in only and cls.__name__ not in only:
            continue
        names, required = component_params(cls)
        driven[key] = names
        any_rendered = False
        for label, payload in payloads:
            values = {
                "scalar": payload,
                "list_str": [payload, payload],
                "list_dict": [dict_of(payload, keys)],
                "dict": dict_of(payload, keys),
            }
            calls: list[tuple[str, str, dict[str, Any]]] = []
            for name in names:
                for shape in (*shapes, SAFE_SHAPE):
                    value = mark_safe(payload) if shape == SAFE_SHAPE else values[shape]
                    calls.append((name, shape, {**required, name: value}))
            row = {k: KEYREF_KEY for k in keys}
            row[KEYREF_KEY] = payload
            calls.append(
                (
                    "*",
                    KEYREF,
                    {
                        n: [dict(row)] if variants.kind_of(n) == "list" else KEYREF_KEY
                        for n in names
                    },
                )
            )
            # A class that takes ``css_framework`` renders different markup under each
            # of the three renderers, so each is driven under all of them.
            frameworks = FRAMEWORKS if "css_framework" in names else (None,)
            for name, shape, kwargs in calls:
                for framework in frameworks:
                    given = (
                        kwargs
                        if framework is None or name == "css_framework"
                        else {**kwargs, "css_framework": framework}
                    )
                    harness.reset_ids()
                    try:
                        out = str(cls(**given).render())  # type: ignore[call-arg]
                    except Exception:  # noqa: BLE001 - a call the component rejects tells us nothing
                        continue
                    if plant == (key, name):
                        out += plant_markup
                    any_rendered = True
                    cells.append(ComponentCell(key, name, shape, label, findings(out)))
        if not any_rendered:
            unrendered.append(key)
    return cells, driven, unrendered


def component_holes(cells: Iterable[ComponentCell]) -> dict[tuple[str, str, str, str], set[str]]:
    """``{(class, param, element, attribute): {labels}}``, dangerous payloads only."""
    dangerous_labels = {label for label, _ in DANGEROUS}
    out: dict[tuple[str, str, str, str], set[str]] = {}
    for cell in cells:
        if cell.label not in dangerous_labels or cell.shape == SAFE_SHAPE:
            continue
        for element, attr, cls, _scheme in cell.found:
            out.setdefault((cell.cls, cell.param, element, "%s:%s" % (attr, cls)), set()).add(
                cell.label
            )
    return out


# --------------------------------------------------------------------------- #
# Decided holes: the one baseline the tests and the script share (#4130)
#
# A "hole" is a dangerous payload that reached an attribute the scanner NAMES as a URL. Some of
# those were judged and decided to be printed as written; they are recorded here, with the
# reason, so ``python/tests/test_url_scheme_sweep_3665.py`` and
# ``scripts/template-library-url-sweep.py`` excuse exactly the same set. Exit 1 from the script
# means an UNDECIDED hole. Each entry is pinned by a test that fails once it stops occurring.

#: The theming tags whose ``**attrs`` reach the element as ``name="value"`` pairs (pinned against
#: the templates that print ``{{ extra_attrs }}`` by ``test_theming_url_hardening_3749.py``).
PASSTHROUGH_TAGS = frozenset({"theme_button", "theme_input", "theme_select", "theme_progress"})

#: Names the scanner reads as URL attributes that theming deliberately does NOT judge, with the
#: decision (#3765). HTML URL attributes that no browser follows on the elements these tags print
#: (a citation, an obsolete object attribute, an icon NAME, a microdata identifier), and
#: ``data-url``, one author-defined name of the ``data-*`` family: all printed as written.
UNJUDGED_PASSTHROUGH = {
    "cite": "a citation URL no browser follows; also used as text (`Smith: 2020`)",
    "longdesc": "obsolete; modern browsers do not follow it",
    "manifest": "obsolete AppCache attribute of <html>",
    "codebase": "obsolete <object>/<applet> attribute",
    "usemap": "a fragment reference to a <map>, not a fetch",
    "icon": "an icon NAME in design systems (`mdi:home`); obsolete <menuitem> attribute in HTML",
    "profile": "obsolete <head> attribute",
    "archive": "obsolete <object>/<applet> attribute",
    "classid": "obsolete <object> attribute",
    "itemid": "a microdata identifier, not fetched",
    "itemtype": "a microdata vocabulary identifier, not fetched",
    "data_url": "author-defined `data-*`: the page author's own script reads it",
}

#: Class components whose ``data=`` mapping becomes ``data-<key>`` attributes: the CALLER names
#: the attribute, and djust prints the value it was given (escaped). The sweep's dict has
#: URL-worded keys, so ``data-href`` / ``data-url`` appear; there is no framework URL parameter
#: here, and no djust script navigates on them (a page script that does must validate the value).
KNOWN_AUTHOR_NAMED = frozenset(
    {
        ("djust.components.components.button.Button", "data"),
        ("djust.components.components.card.Card", "data"),
    }
)

#: Gaps in a RUST mirror of ``url_attr``: ``(class, parameter, payload label)`` for a payload one
#: Rust component still lets through where the Python rule refuses it. Empty since #3748.
KNOWN_RUST_MIRROR_GAPS: frozenset[tuple[str, str, str]] = frozenset()


#: The danger classes a decision can excuse. ``exec`` (a browser follows the URL itself) is never
#: decided: a listed parameter that starts landing in ``<a href>`` is a new hole, not the old one.
_DECIDABLE_CLASSES = frozenset({"js", "inert"})


def _attr_and_class(cell: str) -> tuple[str, str]:
    """``"data-url:js"`` -> ``("data-url", "js")`` (the attribute name may hold a colon)."""
    attr, _, cls = cell.rpartition(":")
    return attr, cls


def decided_tag_hole(hole: tuple[str, ...]) -> bool:
    """Whether a tag hole ``(engine, "<library>:<tag>", param, element, "attr:class")`` is a
    pass-through name theming DECIDED not to judge (:data:`UNJUDGED_PASSTHROUGH`).

    Only the decision itself is excused: the payload printed in the parameter's OWN attribute
    (``cite=`` -> ``cite``, ``data_url=`` -> ``data-url``), with the class that attribute has
    (``js`` for a ``data-*``, ``inert`` otherwise). The same parameter reaching any other
    attribute, and any ``exec`` class, is a hole (#4130 review)."""
    tag = hole[1].split(":", 1)[1] if ":" in hole[1] else hole[1]
    param = hole[2]
    if tag not in PASSTHROUGH_TAGS or param not in UNJUDGED_PASSTHROUGH:
        return False
    attr, cls = _attr_and_class(hole[4])
    own = param.replace("_", "-")
    return attr == own and cls == ("js" if own.startswith("data-") else "inert")


def decided_component_hole(hole: tuple[str, ...], labels: Iterable[str]) -> bool:
    """Whether a component hole ``(class, param, element, "attr:class")`` reached by ``labels``
    is decided: an author-named ``data-*`` (:data:`KNOWN_AUTHOR_NAMED`: a ``data-`` attribute
    of a non-executing class, never ``exec``) or a recorded Rust mirror gap for every one of
    its payloads."""
    attr, cls = _attr_and_class(hole[3])
    if (hole[0], hole[1]) in KNOWN_AUTHOR_NAMED:
        return attr.startswith("data-") and cls in _DECIDABLE_CLASSES
    labels = list(labels)
    return bool(labels) and all(
        (hole[0], hole[1], label) in KNOWN_RUST_MIRROR_GAPS for label in labels
    )
