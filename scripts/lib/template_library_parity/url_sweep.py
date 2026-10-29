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
   whose browser-normalised value starts with ``javascript:``, ``vbscript:`` or
   a non-image ``data:``, with the element and attribute it landed on.

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
)

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


def scheme_of(value: str) -> str:
    """The scheme a browser would act on, ``""`` for none."""
    probe = _STRIP.sub("", value).lower()
    for scheme in ("javascript:", "vbscript:", "data:"):
        if probe.startswith(scheme):
            return scheme[:-1]
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
    """Whether ``attr`` carries a script-capable URL.

    ``javascript:`` / ``vbscript:`` anywhere; ``data:`` unless it is an image
    URI on an attribute that only ever loads an image (``<img src>``,
    ``poster``, ``srcset``) or the resource a ``<link>`` loads (a stylesheet,
    manifest or icon) — there ``data:`` is legitimate and runs no script.
    """
    if attr.name == "srcset":
        # A candidate's URL ends at whitespace, so ``java<TAB>script:`` is the
        # URL ``java`` and a descriptor here, unlike in an ``href``.
        from djust.components.utils import _srcset_candidates

        return any(
            is_dangerous(Attr(attr.element, "src", url))
            for url, _desc in _srcset_candidates(attr.value)
        )
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


def findings(markup: str) -> list[tuple[str, str, str, str]]:
    """``[(element, attribute, class, scheme)]`` of every dangerous attribute, sorted."""
    return sorted(
        {
            (a.element, a.name, danger_class(a), scheme_of(a.value))
            for a in attributes(markup)
            if is_dangerous(a)
        }
    )


def carries(markup: str, payload: str) -> bool:
    """Whether some attribute's value is (a rewrite of) ``payload`` — it survived.

    Matched on the unique :data:`MARK` plus the payload's own first characters,
    so alt text that merely echoes the value does not count.
    """
    head = _STRIP.sub("", html.unescape(payload)).lower()[:5]
    for a in attributes(markup):
        if (
            is_url_attr(a.name)
            and MARK in a.value
            and _STRIP.sub("", a.value).lower().startswith(head)
        ):
            return True
    return False


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
    python_carries: bool = False
    rust_carries: bool = False
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
def planted_hole(key: str) -> Iterator[None]:
    """Make the PYTHON tag ``"<label>:<name>"`` emit :data:`PLANT` (gate-off).

    Wraps the library's compile function the way ``harness.planted_difference``
    does. The sweep must report a Python hole for the tag, which proves a hole
    that exists is found end to end (payload -> engine -> scan -> report).
    Restored on exit."""
    from django.template.library import import_library

    label, name = key.split(":", 1)
    library = import_library(SWEPT_LIBRARIES[label])
    original = library.tags[name]

    def planted(parser: Any, token: Any) -> Any:
        node = original(parser, token)
        inner = node.render
        node.render = lambda context: inner(context) + PLANT
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
    if names:
        keyref = [
            "%s=%s" % (n, "p_keyref_list" if variants.kind_of(n) == "list" else "p_keyref_str")
            for n in names
        ]
        yield "*", KEYREF, keyref
        yield "*", KEYREF + "_lists", ["%s=p_keyref_list" % n for n in names]


def run_sweep(
    payloads: Iterable[tuple[str, str]] = PAYLOADS,
    only: Optional[set[str]] = None,
    shapes: Iterable[str] = SHAPES,
    on_tag: Optional[Callable[[str], None]] = None,
    plant: Optional[str] = None,
) -> Sweep:
    """Drive every parameter of every tag in both engines. Fresh process only.

    ``plant`` is ``"<label>:<tag>"``: see :func:`planted_hole`."""
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
        sweep.driven[key] = list(names)
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
        for label, payload in payloads:
            ctx = build_context(payload, keys)
            if "form" in names:
                ctx["bound_form"] = harness._bound_form()
            for param, shape, src in calls:
                if "form" in names:
                    # a bound Django form, not a string: the rest of the call still runs
                    src = re.sub(r"\bform=p_\w+", "form=bound_form", src)
                with planted_hole(plant) if plant == key else contextlib.nullcontext():
                    py_out = _render(python_engine, src, ctx)
                rs_out = _render(rust_engine, src, ctx)
                any_rendered |= py_out is not None or rs_out is not None
                sweep.cells.append(
                    Cell(
                        key,
                        param,
                        shape,
                        payload,
                        label,
                        None if py_out is None else findings(py_out),
                        None if rs_out is None else findings(rs_out),
                        py_out is not None and carries(py_out, payload),
                        rs_out is not None and carries(rs_out, payload),
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

#: Packages of ``djust.components`` that hold ``Component`` subclasses.
COMPONENT_PACKAGES = ("components", "ui", "data", "layout", "forms")
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


def component_classes() -> dict[str, type]:
    """``{"module.Class": cls}`` for every ``Component`` subclass the packages define."""
    import importlib
    import inspect
    import pkgutil

    from djust import Component

    found: dict[str, type] = {}
    for sub in COMPONENT_PACKAGES:
        package = importlib.import_module("djust.components." + sub)
        for info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            module = importlib.import_module(info.name)
            for name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, Component)
                    and obj.__module__ == module.__name__
                ):
                    found[f"{obj.__module__}.{name}"] = obj
    return dict(sorted(found.items()))


def component_params(cls: type) -> tuple[list[str], dict[str, Any]]:
    """``(every constructor parameter, filler for the required ones)``."""
    import inspect

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
) -> tuple[list[ComponentCell], dict[str, list[str]], list[str]]:
    """``(cells, {class: parameters driven}, classes that never rendered)``."""
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
            for name, shape, kwargs in calls:
                harness.reset_ids()
                try:
                    out = str(cls(**kwargs).render())  # type: ignore[call-arg]
                except Exception:  # noqa: BLE001 - a call the component rejects tells us nothing
                    continue
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
