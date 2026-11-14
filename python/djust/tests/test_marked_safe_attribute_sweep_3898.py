"""A marked-safe value is not a URL and not an attribute value (#3898, #3894).

``mark_safe`` on a value (also a lazy one, or any ``str`` with ``__html__``) means "no HTML
escaping needed" in a TEXT slot. It never meant "navigable", and it never meant "write it into a
quoted attribute". So, in ``djust.components``:

* a URL is checked against the scheme allowlist and escaped whatever it is marked as (data, a
  constructor argument, a tag's own keyword, the native handlers), except the literal no-op hrefs
  ``javascript:void(0)`` / ``javascript:;``;
* an ITEM value (an entry of a list of items, an item dict) that lands in an attribute is escaped
  whatever it is marked as;
* a text slot keeps its markup, except the text of ``<textarea>`` / ``<title>`` (RCDATA) and of an
  HTML comment in a hybrid template, where markup can end the element (#3914); ``<script>``,
  ``<style>`` ... keep the author's markup (HTML escaping is the wrong encoding there); a scalar
  argument in a non-URL attribute is the developer's own.

What each class here proves, and what it does NOT:

* ``TestTheSweep`` / ``TestEveryClass``: the classes whose module names ``url_attr`` (all four
  claims, six payloads), and EVERY class the URL sweep can drive (200+; two claims, two payloads,
  item shapes only for attributes). The classes, parameters and per-class item keys are DERIVED
  from the code (``url_sweep.component_classes``, ``component_params``, ``harness.discover``), under
  all three CSS frameworks, with and without the companions that make conditional sections draw.
* ``TestTags``: the 18 tags that print a URL out of an item, on both engines.
* ``TestEveryTag``: the list parameters of the tags of the swept libraries (``djust_components``,
  ``djust_theming``), DERIVED at run time: a name is a list parameter if a list handed to it is
  walked, and each is tried with every key the tag reads from its items (recorded, one key at a
  time carries the claim and the rest are benign, numbers where a tag does arithmetic), the tag's
  other lists filled, its flags on and off, the scalars that select a branch set (``event=``,
  ``step="map"``), nested items, pairs, and an empty block body. It pins the exact list of tags
  that still leak (``KNOWN_TAG_ITEM_LEAKS``), empty since #3904. Libraries outside the sweep
  (``live_tags``, ``djust_pwa``, ``djust_auth`` ...) are not claimed.
* ``TestTheDerivationCanFail``: a library built in the test with one planted leak of each kind
  the first derivation missed, and a clean twin of each; the derivation must report exactly the
  planted ones, and nothing when its detection is switched off.
* ``TestTheStaticHunt``: the STRUCTURAL check. An AST pass over the tag modules for an escaped ITEM
  written to a quoted attribute, found without enumeration (a helper function included), whether
  the string is an f-string, a concatenation, ``%`` with a tuple or a literal mapping,
  ``.format``, ``"".join([...])`` of literal pieces, or assembled over several statements in one
  function (``s = '<a title="'`` ... ``s += v``, #3935); every finding it makes is judged in
  ``STATIC_SINK_ALLOWED``. It does not see: an unquoted attribute; ``format_html``; a helper that
  escapes a value it is handed (the item is read in the caller); a ``%`` mapping or a ``.format``
  argument built elsewhere (``% ctx``, ``% {**ctx, "k": v}``, ``.format(**ctx)``,
  ``.format_map``); a join over a name
  (``"".join(parts)`` after ``parts.append(...)``); ``s = s + x`` (a re-assignment starts the
  string over); an annotated assignment (``s: str = ...``); a string assembled in a
  ``match`` / ``case`` body; a string assembled across functions or returned and concatenated by the caller;
  ``string.Template``. None of those patterns writes an item in the tag modules today. The run-time derivation above is the SECOND
  net, not the first: a numeric key it does not know, a branch chosen by a named constant or a
  membership test, a numeric threshold, or a child tag missing from ``variants.PARENT`` can each
  hide a site from it, and the hunt does not depend on any of them.
* ``TestEveryItemParameter``: the 43 tag parameters that print an item in an attribute (#3904),
  one cell each: driven (a plain probe lands in the pinned attributes), and every way of marking a
  value safe leaves those attributes as the plain string does. ``TestFormFieldChoices`` does the
  same for a form field's choice value in ``dj_form``, under both engines (the native engine
  hands the form to the same Python tag).
* ``TestCallerSet`` fails on any ``url_attr`` / ``srcset_attr`` call (the native handlers
  included) that leaves ``honor_safe`` at its default.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import os
import re
import sys
from collections import Counter
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import patch

import pytest
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe
from djust.components.base import LiveComponent
from scripts.lib.template_library_parity import harness, url_sweep
from tests import sweep_sample

pytestmark = pytest.mark.components


ROOT = Path(__file__).resolve().parents[3]


class _SafeSub(SafeString):
    """``isinstance(x, SafeData)`` is true for a subclass too."""


class _HtmlStr(str):
    """Any ``str`` with an ``__html__`` method (what ``markupsafe.Markup`` is, without the
    dependency): Django's ``conditional_escape`` writes it as it is, though it is not ``SafeData``."""

    def __html__(self) -> str:
        return str(self)


def _lazy_safe(text: str) -> object:
    return lazy(lambda: mark_safe(text), str)()


class _TextFormMarkedSafe:
    """Not text, and not marked safe itself, but the text it gives (``str(x)``) is: a template
    engine formats a value of a type it does not know by calling ``str`` on it."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return mark_safe(self.text)


def _callable_marked_safe(text: str) -> object:
    """A callable whose result is marked safe: a template engine calls a callable it is given."""
    return lambda: mark_safe(text)


CLAIMS = {
    "mark_safe": mark_safe,
    "safestring-subclass": _SafeSub,
    "lazy-mark_safe": _lazy_safe,
    "html-object": _HtmlStr,
    "text-form-marked-safe": _TextFormMarkedSafe,
    "callable-marked-safe": _callable_marked_safe,
}
#: Claims only a template ENGINE unwraps (it calls a callable it is given). A URL helper or a
#: ``_render_custom`` f-string takes a value as it is and prints a function's repr, so the tests
#: that hand a value straight to one of those use ``VALUE_CLAIMS``; the sweeps, which draw
#: through ``Component.render``, use them all.
ENGINE_CLAIMS = frozenset({"callable-marked-safe"})
VALUE_CLAIMS = sorted(set(CLAIMS) - ENGINE_CLAIMS)

#: ``(label, payload)``: three spellings of a script scheme, a data document, and two ways out of
#: an attribute. A scheme hole is found by the URL sweep's scanner; a breakout creates ``on...``.
PAYLOADS = (
    ("js", "javascript:alert(%s)" % url_sweep.MARK),
    ("js-mixed", "JaVaScRiPt:alert(%s)" % url_sweep.MARK),
    ("js-embedded-tab", "java\tscript:alert(%s)" % url_sweep.MARK),
    ("data-html", "data:text/html,<script>alert(%s)</script>" % url_sweep.MARK),
    ("breakout-dq", '/x" onmouseover="alert(%s)" y="' % url_sweep.MARK),
    ("breakout-sq", "/x' onmouseover='alert(%s)' y='" % url_sweep.MARK),
)

#: The elements whose text a hybrid template escapes (RCDATA: entities are decoded, the first
#: matching end tag ends it). ``<script>``, ``<style>`` ... keep the author's markup: HTML
#: escaping is the wrong encoding there (#3914), so a value that ends one is not a finding.
RAW_TEXT_TAGS = ("textarea", "title")
#: Text that ends EVERY such element and starts a live element carrying the marker (#3914). It is not in
#: ``PAYLOADS``: the sweeps that cover the attribute rule over 200+ classes do not need it, and
#: the cells that do (the hybrid templates) add it explicitly.
RAW_TEXT_PAYLOAD = (
    "breakout-rawtext",
    "".join(f"</{tag}>" for tag in RAW_TEXT_TAGS) + "<b data-breakout=%s>" % url_sweep.MARK,
)


def raw_text_ends(markup: str) -> list[tuple[str, str]]:
    """``(element, "ends-raw-text")`` for each raw-text element whose content begins with the
    breakout of :data:`RAW_TEXT_PAYLOAD` printed as live markup: a chain of end tags and then the
    payload's handler element, directly after the start tag (static text may come first). The
    same text in an ordinary text slot (a ``<div>``) is not a finding: a text slot keeps its
    markup (#3898)."""
    if url_sweep.MARK not in markup:
        return []
    ends = "(?:</[a-z]+>)+"
    handler = re.escape(f"<b data-breakout={url_sweep.MARK}>")
    return [
        (tag, "ends-raw-text")
        for tag in RAW_TEXT_TAGS
        if re.search(rf"<{tag}(?=[\s/>])[^>]*>[^<]*?{ends}{handler}", markup, re.I)
    ]


class _Handlers(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        # the PAYLOAD's handler: a component's own inline ``onclick`` (the navbar toggler) is not
        # a hole this sweep made
        self.found += [(tag, n) for n, _v in attrs if n == "onmouseover"]

    handle_startendtag = handle_starttag


def holes_in(markup: str) -> list:
    """Script-capable URLs in an attribute, and event-handler attributes."""
    parser = _Handlers()
    parser.feed(markup)
    parser.close()
    return url_sweep.findings(markup) + parser.found + raw_text_ends(markup)


def url_classes() -> dict[str, type]:
    """Every driven class whose module calls ``url_attr`` (derived, not listed)."""
    out = {}
    for key, cls in url_sweep.component_classes().items():
        module = importlib.import_module(cls.__module__)
        # the NAME, not a call: a module that imports the helper and stops calling it, or calls
        # it under another name, is still swept
        if "url_attr" in inspect.getsource(module):
            out[key] = cls
    return out


#: ``MARKED_SAFE_SWEEP_ONLY=breadcrumb,carousel`` narrows the sweep to the classes whose key
#: contains one of the words and the tags of that name: the gate-off harness reverts ONE call site
#: and runs only the sweep that site belongs to. Unset (always, in CI) it is the whole sweep.
ONLY = {w for w in os.environ.get("MARKED_SAFE_SWEEP_ONLY", "").split(",") if w}

CLASSES = {
    key: cls for key, cls in url_classes().items() if not ONLY or any(word in key for word in ONLY)
}
#: EVERY class the URL sweep can drive (derived: every ``Component`` / ``LiveComponent`` in the
#: component packages), not only the ones whose module names ``url_attr``: an attribute breakout
#: needs no URL helper, so the claim "an item value never leaves an attribute" is made for all.
ALL_CLASSES = {
    key: cls
    for key, cls in url_sweep.component_classes().items()
    if not ONLY or any(word in key for word in ONLY)
}
#: The classes with a ``template``: what ``Component.render`` draws when the crate instance is
#: absent (derived, not listed). A value the crate constructor refuses (a lazy string) leaves them
#: to it, and the ones with no crate counterpart are drawn by it always (#3900).
TEMPLATE_CLASSES = {
    key: cls for key, cls in ALL_CLASSES.items() if getattr(cls, "template", None) is not None
}


# --- tiers (#4074) ---------------------------------------------------------------------------
# The class, tag and item-parameter sweeps below are ``@pytest.mark.sweep``: before merge they
# drive a seeded sample (``tests/sweep_sample.py``), every unit in the nightly run. The census
# tests (every class / pair / tag reached, the exact known-leak sets) are ``pr_tier``: they run
# in full on every pull request. ``tests/test_sweep_tiers_4074.py`` pins the split.

#: Code every class (or tag) of these sweeps runs through, and the sweep itself: a change to any
#: of them drives every unit even in a sampled run.
SHARED_PATHS = (
    "python/djust/components/base.py",
    "python/djust/components/utils.py",
    "crates/djust_components/src/html.rs",
    "scripts/lib/template_library_parity/url_sweep.py",
    "python/djust/tests/test_marked_safe_attribute_sweep_3898.py",
    "tests/sweep_sample.py",
)
#: Units each sampled run draws beyond the ones a change forces (budget: about a minute each).
#: The class budget is per check of a :func:`sweep` call, so each family (URL classes, template
#: classes, all classes) is represented and its "barely ran" floor has something to count.
CLASS_BUDGET = 3
TAG_BUDGET = 2
ITEM_PARAMETER_BUDGET = 6
#: class set of a check -> (classes of it the last ``sweep`` drove, its size), for :func:`_floor`
_DRAWS: dict[frozenset[str], tuple[int, int]] = {}


def _class_paths(key: str) -> list[str]:
    """The class's module, and the crate source of the same name if it has one."""
    cls = url_sweep.component_classes().get(key)
    if cls is None:
        return []
    out = sweep_sample.module_paths(cls)
    out.append("crates/djust_components/src/simple/%s.rs" % cls.__module__.rsplit(".", 1)[-1])
    return out


@lru_cache(maxsize=None)
def _tag_files() -> dict[str, str]:
    base = ROOT / "python/djust/components/templatetags"
    return {p.relative_to(ROOT).as_posix(): p.read_text() for p in sorted(base.rglob("*.py"))}


def _tag_paths(tag: str) -> list[str]:
    """The template-tag modules that name the tag (``lib/tag/param`` keys name it second)."""
    name = tag.split("/")[1] if "/" in tag else tag
    word = re.compile(r"\b%s\b" % re.escape(name))
    return [path for path, text in _tag_files().items() if word.search(text)]


def _floor(classes: dict, full: int) -> int:
    """The least a sweep over ``classes`` must render before its "barely ran" assertion passes.

    Unset (always, in CI) it is ``full``. Narrowed by ``MARKED_SAFE_SWEEP_ONLY`` to a class that is
    in only one family (URL-only, or template-only), the other family's sweep legitimately renders
    nothing, so it is held to no floor: otherwise a gate-off cannot tell "detected" from "the
    baseline is red for the class I chose". A family that does have a class keeps a real floor.
    """
    if not ONLY:
        return sweep_sample.floor(full, *_DRAWS.get(frozenset(classes), (0, 0)))
    return 50 if classes else -1


KEYS = set(url_sweep.COMMON_KEYS)
#: Attribute-ish item keys no URL sweep names (a lightbox image's ``alt``, a card's ``caption``).
ATTR_KEYS = frozenset(
    {"alt", "caption", "description", "subtitle", "tooltip", "class", "color", "status", "target",
     "rel", "placeholder", "aria_label", "name", "email", "key", "slug", "category", "user",
     "action", "time", "badge", "content", "message", "type", "variant", "size", "role", "value",
     "label", "title", "id", "text", "icon", "content_id", "panel", "header", "body"}
)  # fmt: skip


def class_keys(cls: type) -> set[str]:
    """The item keys ``cls`` reads, DERIVED from its source (``.get("x")`` / ``["x"]``, followed
    through its helpers by the harness), plus the common URL keys and :data:`ATTR_KEYS`."""
    try:
        found, _params = harness.discover(cls)
    except Exception:  # noqa: BLE001 - a class the harness cannot read keeps the common keys
        found = set()
    return set(KEYS) | set(ATTR_KEYS) | {k for k in found if k.isidentifier()}


#: The item keys the tags read besides the URL-shaped ones (a card's category, a feed's user, a
#: node's parent ...): derived from the tags' own keys would need the harness; these are the ones
#: the data tags' sources name, and ``test_each_tag_is_reached_by_the_driver`` shows the driver
#: gets as far as an attribute.
TAG_KEYS = (
    KEYS
    | ATTR_KEYS
    | {"category", "user", "action", "target", "time", "description", "badge", "event", "active"}
    | {"nid"}
)


#: Item attributes a component reads off an OBJECT item (``NavItem``): the dict keys plus these.
OBJ_KEYS = KEYS | {"target", "badge", "badge_variant", "active", "external", "icon", "label"}
#: A list of FIVE items, not one: a link is drawn for every item but the last, and a breadcrumb
#: collapses its middle items into a dropdown past its ``max_items``.
N_ITEMS = 5


#: Item keys whose value is not text but a CONTAINER: a list-group item's ``badge`` is a mapping
#: (``badge.get("text")``), a navbar item's ``dropdown`` is a list of items. Filling them with the
#: claim string crashes the component (``'str' has no attribute 'get'``), and a crash is a cell
#: that proved nothing: the first version of this sweep skipped every cell of ``ui.ListGroup`` and
#: ``ui.NavBar`` that way and two leaks sat behind it. So the builder is typed, and
#: ``TestEveryClassIsReached`` fails any (class, parameter) pair that no cell got through.
CONTAINER_KEYS = ("badge", "data", "dropdown")
#: ... of which these hold a mapping of the same kind of text (``badge.get("text")``,
#: ``data.items()``); ``dropdown`` holds a list of items.
MAPPING_KEYS = ("badge", "data")


def _flat(value: object, keys: set[str]) -> dict:
    """An item whose every text key is ``value`` and that has no container key."""
    return url_sweep.dict_of(value, set(keys) - set(CONTAINER_KEYS))  # type: ignore[arg-type]


#: Item keys that are FLAGS (``active``, ``disabled``, ``divider`` ...): a truthy claim in one makes
#: the component draw the inactive branch (a breadcrumb item that is ``active`` is a ``<span>``,
#: never a link), so the link's ``href`` is never reached. Derived from the URL sweep's own
#: bool-name rule, plus the item flags it does not name.
FLAG_NAMES = {"divider", "current", "selected", "hidden", "external", "separator", "dismissed"}


def _is_flag(key: str) -> bool:
    from scripts.lib.template_library_parity import variants

    return variants.kind_of(key) == "bool" or key in FLAG_NAMES


def _flags_off(node: object) -> object:
    """Every flag key of an item (and of the containers under it) set to ``False``."""
    if isinstance(node, dict):
        for k in list(node):
            if _is_flag(k):
                node[k] = False
            else:
                _flags_off(node[k])
    elif isinstance(node, list):
        for entry in node:
            _flags_off(entry)
    return node


def _item(value: object, keys: set[str] = KEYS, dropdown: bool = True, flags: bool = True) -> dict:
    """An item dict: every text key is ``value``, ``badge`` is a mapping of the same, and (when
    ``dropdown``) the navbar's ``dropdown`` holds two entries of the same kind. An item WITHOUT a
    ``dropdown`` key is what a navbar draws as a plain link. With ``flags`` False its flag keys
    (``active``, ``disabled``) are ``False``: the active / disabled branch is the other half."""
    item = _flat(value, keys)
    for k in MAPPING_KEYS:
        item[k] = _flat(value, keys)
    if dropdown:
        item["dropdown"] = [_flat(value, keys) for _ in range(2)]
    return item if flags else _flags_off(item)  # type: ignore[return-value]


def _obj(value: object, keys: set[str] = KEYS) -> SimpleNamespace:
    """An object item (``NavItem``): every attribute is ``value``, except the badge, which the
    component compares with ``> 0``."""
    return SimpleNamespace(**{**dict.fromkeys(OBJ_KEYS | keys, value), "badge": 1})


#: Item keys a component cannot take the claim for: it does ``int(item["level"])`` or
#: ``item["badge"].get(...)``. ``DROP`` leaves the key out, ``NUMBER`` gives it a number. The
#: search for them is :func:`_overrides_for`; what it finds is pinned by
#: ``TestEveryClassIsReached`` (a key the sweep cannot carry is stated, never skipped).
DROP = "<drop>"
NUMBER = 1


def _override(node: object, overrides: dict[str, object]) -> object:
    """Apply ``overrides`` to every item under ``node`` (items, objects, nested containers)."""
    if isinstance(node, dict):
        for k in list(node):
            if k in overrides:
                if overrides[k] is DROP:
                    del node[k]
                else:
                    node[k] = overrides[k]
            else:
                _override(node[k], overrides)
    elif isinstance(node, list):
        for entry in node:
            _override(entry, overrides)
    elif isinstance(node, SimpleNamespace):
        for k in list(vars(node)):
            if k in overrides:
                if overrides[k] is DROP:
                    delattr(node, k)
                else:
                    setattr(node, k, overrides[k])
    return node


SHAPES = ("scalar", "list_str", "dict", "list_dict", "list_dict_flat", "list_obj", "form_like")


def _build(
    shape: str, value: object, keys: set[str] = KEYS, overrides: dict[str, object] | None = None
) -> object:
    """One shape of ``value``: a scalar, a list of text, one item, a list of items (alternating
    with and without a ``dropdown``: a navbar draws the two differently), a list of items whose
    every key holds the claim itself (a component that prints ``badge`` as text), a list of
    object items. ``overrides`` are the keys the component cannot take the claim for."""
    if shape == "scalar":
        return value
    built: object = {
        "list_str": lambda: [value] * N_ITEMS,
        # one item: ``badge`` a mapping, no ``dropdown`` (a navbar draws a plain link)
        "dict": lambda: _item(value, keys, dropdown=False),
        # items 0..4: (dropdown, flags on) (plain, on) (dropdown, off) (plain, off) (dropdown, on)
        "list_dict": lambda: [
            _item(value, keys, dropdown=i % 2 == 0, flags=i not in (2, 3)) for i in range(N_ITEMS)
        ],
        "list_dict_flat": lambda: [
            url_sweep.dict_of(value, set(keys) - {"dropdown"})  # type: ignore[arg-type]
            for _ in range(N_ITEMS)
        ],
        "list_obj": lambda: [_obj(value, keys) for _ in range(N_ITEMS)],
        # what ``FieldError`` / ``FormErrors`` read off a Django form: ``errors`` and
        # ``non_field_errors()``
        "form_like": lambda: SimpleNamespace(errors=[value], non_field_errors=lambda: [value]),
    }[shape]()
    return _override(built, overrides) if overrides else built


#: Parameters that make a conditional section draw (a split button, a brand, an image with its
#: alt text): the second pass sets them so the attributes inside those sections are reached.
URL_COMPANIONS = {
    "src", "image", "logo", "brand_logo", "thumbnail", "avatar", "brand_href", "url", "href",
}  # fmt: skip
TEXT_COMPANIONS = {"brand", "brand_name", "alt", "label", "title", "name", "text"}
#: A ``LiveComponent`` has no signature to read a bool default from: these draw nothing until set.
BOOL_COMPANIONS = {"show", "open", "visible", "is_open"}


def _bool_default(cls: type, name: str) -> bool:
    """A constructor parameter whose default is a bool (``split=False``): the harness's name
    heuristic does not know every flag by name."""
    try:
        param = inspect.signature(cls.__init__).parameters.get(name)  # type: ignore[misc]
    except (TypeError, ValueError):
        return False
    return param is not None and isinstance(param.default, bool)


def _companions(cls: type, names: list[str], skip: str) -> dict[str, object]:
    from scripts.lib.template_library_parity import variants

    out: dict[str, object] = {}
    for n in names:
        if n == skip:
            continue
        if variants.kind_of(n) == "bool" or n in BOOL_COMPANIONS or _bool_default(cls, n):
            out[n] = True
        elif n in URL_COMPANIONS:
            out[n] = "/ok.png"
        elif n in TEXT_COMPANIONS:
            out[n] = "x"
    return out


#: What a required parameter may need besides the URL sweep's filler (``value: str`` wants text,
#: ``buttons: List[Dict]`` wants entries): tried in order until the class draws with its defaults.
REQUIRED_ALTERNATIVES = (
    "x",
    ["x"],
    [{"label": "x", "text": "x", "title": "x", "url": "/ok"}],
    {"label": "x"},
    1,
)
_REQUIRED: dict[type, dict[str, object]] = {}


def _draws(cls: type, kwargs: dict) -> bool:
    try:
        with _framework("bootstrap5"):
            obj = cls(**kwargs)  # type: ignore[call-arg]
            for attr in ("_rust_instance", "template"):
                if hasattr(obj, attr):
                    setattr(obj, attr, None)
            obj.render()
        return True
    except Exception:  # noqa: BLE001 - the answer is "no"
        return False


def class_params(cls: type) -> tuple[list[str], dict[str, object]]:
    """``(parameters, required)`` of ``cls``: the URL sweep's, with the filler of each required
    parameter repaired when the class does not draw with it. ``StatCard(value=1)`` crashed on
    ``.replace`` in every cell, so no parameter of ``StatCard`` was ever swept."""
    names, required = url_sweep.component_params(cls)
    if cls not in _REQUIRED:
        fixed = dict(required)
        if fixed and not _draws(cls, fixed):
            for name in list(fixed):
                for alternative in (fixed[name], *REQUIRED_ALTERNATIVES):
                    if _draws(cls, {**fixed, name: alternative}):
                        fixed[name] = alternative
                        break
                else:
                    continue
                break
        _REQUIRED[cls] = fixed
    # ``component_id`` is a LiveComponent's own constructor argument (it lands in ``id=``)
    if issubclass(cls, LiveComponent) and "component_id" not in names:
        names = [*names, "component_id"]
    return names, _REQUIRED[cls]


@contextlib.contextmanager
def _framework(framework: str):
    """The CSS framework a class reads from ``djust.config`` (``ui/*`` and ``layout/*`` choose
    their renderer there, not from a constructor argument): patch ONLY ``css_framework``."""
    from djust import config as config_module

    real_get = config_module.config.get

    def get(key, default=None):
        return framework if key == "css_framework" else real_get(key, default)

    with patch.object(config_module.config, "get", get):
        yield


#: A benign marked-safe value: what a cell is decided with, and what the reach test looks for.
REACH_MARK = "ZQREACH"
PROBE = mark_safe(REACH_MARK)

#: ``cell -> overrides`` (``None``: the component rejects the shape whatever the keys). A cell is
#: ``(class, parameter, shape, framework, companions, force_python)``.
_OVERRIDES: dict[tuple, dict[str, object] | None] = {}


def _search_overrides(attempt, keys: set[str]) -> dict[str, object] | None:
    """The item keys a component cannot take the claim for, found PER KEY, never per class: with
    only the common URL keys the shape must draw (else the component rejects the shape, and the
    cell is not one); then each further key is put back as the claim, else as a number, else left
    out. Whatever is left out is recorded in ``_OVERRIDES`` and pinned by the reach test."""

    def ok(overrides: dict[str, object]) -> bool:
        try:
            attempt(overrides)
        except Exception:  # noqa: BLE001 - the answer is "no"
            return False
        return True

    extras = sorted(set(keys) - set(KEYS))
    found: dict[str, object] = {k: DROP for k in extras}
    if not ok(found):
        return None
    for k in extras:
        for form in (None, NUMBER):
            trial = {name: value for name, value in found.items() if name != k}
            if form is not None:
                trial[k] = form
            if ok(trial):
                found = trial
                break
    return found


def drive(
    cls: type,
    key: str,
    names: list[str],
    required: dict[str, object],
    keys: set[str],
    name: str,
    shape: str,
    framework: str,
    companions: bool,
    value: object,
    force_python: bool,
    hybrid: bool = False,
) -> object:
    """Render ``cls`` with ``name`` set to ``shape`` of ``value``: the output, or
    ``None`` (the component rejects this shape of this parameter, with every key it could take
    left out). A crash is not the end of a cell: the keys the component cannot take the claim
    for are searched for one by one (:func:`_search_overrides`).

    ``hybrid`` draws the class through its ``template`` (``Component.render``'s second path) with
    the crate instance taken away, the way a value the crate constructor refuses (a lazy string)
    or a class with no crate counterpart is drawn (#3900). It is for classes that have a
    ``template``; ``force_python`` (``_render_custom``) wins if both are set."""

    def attempt(claimed: object, overrides: dict[str, object]) -> object:
        kwargs = {
            **required,
            **(_companions(cls, names, name) if companions else {}),
            name: _build(shape, claimed, keys, overrides),
        }
        if "css_framework" in names and name != "css_framework":
            kwargs["css_framework"] = framework
        harness.reset_ids()
        with _framework(framework):
            obj = cls(**kwargs)  # type: ignore[call-arg]
            if force_python:
                for attr in ("_rust_instance", "template"):
                    if hasattr(obj, attr):
                        setattr(obj, attr, None)
            elif hybrid:
                obj._rust_instance = None  # type: ignore[attr-defined]
                assert getattr(obj, "template", None) is not None, cls
            return str(obj.render())

    cell = (key, name, shape, framework, companions, force_python, hybrid)
    if cell not in _OVERRIDES:
        # decided ONCE per cell, with the benign probe, whatever claim and payload come first: a
        # payload a validator rejects must not be what leaves a key out of every later cell
        def with_probe(overrides: dict[str, object]) -> object:
            return attempt(PROBE, overrides)

        try:
            with_probe({})
        except Exception:  # noqa: BLE001 - searched for below, recorded, never silent
            _OVERRIDES[cell] = _search_overrides(with_probe, keys) if shape != "scalar" else None
        else:
            _OVERRIDES[cell] = {}
    overrides = _OVERRIDES[cell]
    if overrides is None:
        return None
    try:
        return attempt(value, overrides)
    except Exception:  # noqa: BLE001 - a value this payload makes the component reject says nothing
        return None


class Sweep(NamedTuple):
    classes: dict[str, type]
    claims: tuple[str, ...] = tuple(CLAIMS)
    payloads: tuple[tuple[str, str], ...] = PAYLOADS
    wide: bool = False


ITEM_ATTRIBUTE_PAYLOADS = tuple(p for p in PAYLOADS if p[0] in {"js", "breakout-dq"})


def sweep(
    *checks: Sweep,
    force_python: bool = False,
    companions: bool = False,
    hybrid: bool = False,
) -> list[tuple[list[tuple], int]]:
    """Return each check's findings and render count, sharing observations of identical cells.

    ``force_python`` renders through ``_render_custom`` (the f-string path) for every component,
    the way ``test_ui_simple_components_escaping`` does: the path this rule governs, whichever
    path the crate would have taken. Without it, the render is whatever ``Component.render`` picks
    (a value the crate constructor refuses, a lazy string, leaves a class with a ``template`` to
    draw it: that cell is counted like any other, #3900). ``hybrid`` draws every class that has a
    ``template`` through it, whatever the value (a class with no template is not driven)."""
    found: list[list[tuple]] = [[] for _ in checks]
    rendered = [0] * len(checks)
    classes = {key: cls for check in checks for key, cls in check.classes.items()}
    chosen: set[str] = set()
    for check in checks:
        drawn = sweep_sample.select(
            "marked-safe/classes",
            sorted(check.classes),
            budget=CLASS_BUDGET,
            paths=_class_paths,
            shared=SHARED_PATHS,
        )
        chosen.update(drawn)
    for check in checks:
        family = frozenset(check.classes)
        _DRAWS[family] = (len(family & chosen), len(family))
    for key, cls in classes.items():
        if key not in chosen:
            continue
        if hybrid and getattr(cls, "template", None) is None:
            continue
        cases: dict[tuple[str, str, str], list[int]] = {}
        for i, check in enumerate(checks):
            if key in check.classes:
                for claim in check.claims:
                    for label, payload in check.payloads:
                        cases.setdefault((claim, label, payload), []).append(i)
        names, required = class_params(cls)
        keys = class_keys(cls)
        # EVERY framework, for every class: the renderer is chosen by the constructor argument or
        # by ``djust.config``, so each is driven under both (a class that ignores them renders the
        # same three times)
        frameworks = url_sweep.FRAMEWORKS
        if hybrid:
            # a ``template`` is one string whatever ``css_framework`` says; the constructor
            # argument of that name is still a parameter, driven below with each framework
            frameworks = frameworks[:1]
        for (claim, label, payload), readers in cases.items():
            claimed = CLAIMS[claim](payload)
            for shape in SHAPES:
                for name in names:
                    for framework in frameworks:
                        result = drive(
                            cls, key, names, required, keys, name, shape, framework,
                            companions, claimed, force_python, hybrid,
                        )  # fmt: skip
                        if result is None:
                            continue
                        holes = holes_in(str(result))
                        for i in readers:
                            rendered[i] += 1
                            judged = holes
                            if holes and checks[i].wide:
                                # Judge script URLs a browser follows and event handlers
                                # reached by an item value. Scalar constructor arguments
                                # and author-defined data attributes have a separate policy.
                                judged = [
                                    h
                                    for h in holes
                                    if (len(h) == 4 and h[2] == "exec")
                                    or (len(h) == 2 and shape != "scalar")
                                ]
                            if judged:
                                found[i].append((key, name, shape, claim, label, tuple(judged)))
    return list(zip(found, rendered))


def _report(found: list[tuple]) -> str:
    by_site: dict[tuple[str, str], set] = {}
    for key, name, shape, claim, label, _holes in found:
        by_site.setdefault((key.replace("djust.components.", ""), name), set()).add(
            (shape, claim, label)
        )
    return "\n".join(
        f"{cls}.{param}: {len(v)} cells, e.g. {sorted(v)[0]}"
        for (cls, param), v in sorted(by_site.items())
    )


class TestTheSweep:
    @pytest.mark.sweep("marked-safe/classes")
    def test_the_default_render_of_a_template_class_lets_nothing_out_of_an_attribute(self) -> None:
        # Leave the render path selection to Component.render, including for classes
        # that fall back to a template when the crate rejects the value.
        (found, rendered), (template_found, template_rendered) = sweep(
            Sweep(CLASSES),
            Sweep(TEMPLATE_CLASSES, payloads=ITEM_ATTRIBUTE_PAYLOADS + (RAW_TEXT_PAYLOAD,)),
        )
        assert rendered > _floor(CLASSES, 10_000), f"the sweep barely ran ({rendered} renders)"
        assert ONLY or len(CLASSES) >= 20, sorted(CLASSES)
        assert not found, _report(found)
        assert template_rendered > _floor(TEMPLATE_CLASSES, 2_000), template_rendered
        assert not template_found, _report(template_found)

    @pytest.mark.sweep("marked-safe/classes")
    @pytest.mark.parametrize("companions", [False, True], ids=["bare", "companions"])
    def test_no_template_class_lets_a_marked_safe_value_out_of_its_attribute(
        self, companions: bool
    ) -> None:
        """The second path of ``Component.render`` (#3900): every class that has a ``template``,
        drawn through it, with every claim, payload, shape and parameter. The scalar shapes
        are in (a constructor argument like ``size`` or ``status`` is escaped on the crate and
        ``_render_custom`` paths, so it is here), and the claims include a lazy string, the value
        that sends a class with a crate counterpart down this path.

        Not reached here: a ``srcset`` slot (no shipped template has one; the explicit test in
        ``test_component_hybrid_template_attrs_3900.py`` covers it and
        ``test_no_shipped_template_has_a_srcset_slot_for_the_sweep_to_reach`` fails when one
        appears)."""
        # one script scheme, the data document and one breakout: the scheme spellings are the
        # URL helper's own sweep (``TestTheSweep`` above); this one proves the template path
        # calls the helper and escapes the attribute
        payloads = tuple(p for p in PAYLOADS if p[0] in {"js", "data-html", "breakout-dq"})
        # ... and the text that ends a raw-text element (#3914): the text of a ``<textarea>`` is
        # not an attribute, and a marked-safe value used to end it
        payloads += (RAW_TEXT_PAYLOAD,)
        (found, rendered), (item_found, item_rendered) = sweep(
            Sweep(TEMPLATE_CLASSES, payloads=payloads),
            # Cover item attributes such as class= inside ButtonGroup's template.
            Sweep(
                TEMPLATE_CLASSES, payloads=ITEM_ATTRIBUTE_PAYLOADS + (RAW_TEXT_PAYLOAD,), wide=True
            ),
            hybrid=True,
            companions=companions,
        )
        assert rendered > _floor(TEMPLATE_CLASSES, 2_000), (
            f"the sweep barely ran ({rendered} renders)"
        )
        assert ONLY or len(TEMPLATE_CLASSES) >= 15, sorted(TEMPLATE_CLASSES)
        assert not found, _report(found)

        assert item_rendered > _floor(TEMPLATE_CLASSES, 3_000), item_rendered
        assert not item_found, _report(item_found)

    def test_the_sweep_can_fail(self) -> None:
        """A component that prints a marked-safe value raw IS found (not a tautology)."""
        from djust.components.base import Component

        class Leaky(Component):
            def __init__(self, items: list | None = None, href: str = "") -> None:
                super().__init__()
                self.href = href

            def _render_custom(self) -> str:  # pragma: no cover - never called
                return ""

            def render(self) -> str:  # type: ignore[override]
                return f'<a href="{self.href}">x</a>'

        out = Leaky(href=mark_safe('/x" onmouseover="alert(1)" y="')).render()
        assert holes_in(out) == [("a", "onmouseover")]
        assert holes_in(Leaky(href=mark_safe("javascript:alert(1)")).render())

    @pytest.mark.parametrize("tag", RAW_TEXT_TAGS)
    def test_the_sweep_finds_a_value_that_ends_a_raw_text_element(self, tag: str) -> None:
        """The detector of #3914 is not a tautology: text that ends the element it sits in is
        found, the same text escaped is not, and an element ended once is not a finding."""
        payload = RAW_TEXT_PAYLOAD[1]
        assert (tag, "ends-raw-text") in holes_in(f"<{tag}>{payload}</{tag}>")
        escaped = payload.replace("<", "&lt;").replace(">", "&gt;")
        assert holes_in(f"<{tag}>{escaped}</{tag}>") == []
        assert holes_in(f"<{tag}>fine {url_sweep.MARK}</{tag}>") == []
        # ... and the same text in an ordinary text slot is not (a text slot keeps its markup)
        assert holes_in(f"<div>{payload}</div>") == []
        assert (tag, "ends-raw-text") in holes_in(f'<{tag} data-k="1">label {payload}</{tag}>')


def _call_sites() -> set[tuple[str, str, str]]:
    """``(file, function, first argument)`` of every ``url_attr`` / ``srcset_attr`` call that does
    not say ``honor_safe=False`` and is not the shared data helper, under ``python/djust``
    (tests excluded). The native handlers are in: they are handed text today, and that is
    the engine's doing, not a property of the handler."""
    sites: set[tuple[str, str, str]] = set()
    for path in sorted((ROOT / "python/djust").rglob("*.py")):
        rel = str(path.relative_to(ROOT / "python/djust"))
        if "/tests/" in "/" + rel or path.name.startswith("test_"):
            continue
        sites |= _honouring_calls(path.read_text(), rel)
    return sites


def _honouring_calls(source: str, rel: str = "x.py") -> set[tuple[str, str, str]]:
    """The ``url_attr`` / ``srcset_attr`` calls in ``source`` that are not ``honor_safe=False``."""
    sites: set[tuple[str, str, str]] = set()
    tree = ast.parse(source)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        if name not in ("url_attr", "srcset_attr"):
            continue
        honor = {k.arg: k.value for k in node.keywords}.get("honor_safe")
        if isinstance(honor, ast.Constant) and honor.value is False:
            continue
        fn: ast.AST = node
        while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = parents[fn]
        sites.add((rel, getattr(fn, "name", "?"), ast.unparse(node.args[0]) if node.args else ""))
    return sites


class TestCallerSet:
    """ONE rule, pinned: a marked-safe value never bypasses the URL scheme allowlist, whether the
    URL is data (an item, a row, a constructor argument) or a template tag's own keyword. So no
    call site may leave ``honor_safe`` at its default or pass ``True``."""

    @pytest.mark.pr_tier
    def test_no_url_call_site_honours_a_marked_safe_value(self) -> None:
        sites = _call_sites()
        assert not sites, (
            "a url_attr / srcset_attr call that honours a marked-safe value (use data_url_attr "
            f"or honor_safe=False): {sorted(sites)}"
        )

    def test_the_scan_sees_calls(self) -> None:
        """Not vacuous: the helper's own callers are there to be found."""
        src = "".join(
            p.read_text()
            for p in (ROOT / "python/djust/components").rglob("*.py")
            if "/tests/" not in str(p)
        )
        assert src.count("data_url_attr(") > 60

    @pytest.mark.parametrize(
        "source",
        [
            "def f(x):\n    return url_attr(x)\n",
            "def f(x):\n    return url_attr(x, image=True)\n",
            "def f(x):\n    return url_attr(x, honor_safe=True)\n",
            "def f(x):\n    return srcset_attr(x)\n",
            "def f(x, h):\n    return u.url_attr(x, honor_safe=h)\n",
        ],
    )
    def test_the_scan_can_fail(self, source: str) -> None:
        assert _honouring_calls(source), source

    @pytest.mark.parametrize(
        "source",
        [
            "def f(x):\n    return url_attr(x, honor_safe=False)\n",
            "def f(x):\n    return data_url_attr(x)\n",
            "def f(x):\n    return srcset_attr(x, honor_safe=False)\n",
        ],
    )
    def test_the_scan_passes_the_helper_and_an_explicit_false(self, source: str) -> None:
        assert not _honouring_calls(source), source


# --- the TAGS that read a URL out of an item: both engines ------------------------------------

#: The tags with a ``data_url_attr`` site (a URL read out of a dict item or a list). Each name must
#: exist in the tag inventory (a rename fails ``test_every_data_tag_exists``).
DATA_TAGS = (
    "activity_feed", "avatar_group", "carousel", "chat_bubble", "data_card_grid", "dropdown_menu",
    "image_cropper", "image_upload_preview", "lightbox", "live_indicator", "mentions_input",
    "menu_item", "org_chart", "presence_avatars", "rich_select", "sortable_grid",
    "avatar", "responsive_image",
)  # fmt: skip
#: The tags under test (all of them unless ``MARKED_SAFE_SWEEP_ONLY`` narrows the sweep).
TAGS = tuple(t for t in DATA_TAGS if not ONLY or t in ONLY)

TAG_PAYLOADS = tuple(p for p in PAYLOADS if p[0] in {"js", "js-mixed", "data-html", "breakout-dq"})
HTTPS = dict(url_sweep.SAFE)["https"]


def _inventory() -> dict:
    return {i.name: i for i in url_sweep.inventory().values() if i.lib == "djust_components"}


DATA_SHAPES = ("list_str", "list_dict", "dict")


def _tag_calls(info, shapes: tuple[str, ...] = DATA_SHAPES) -> list[tuple[str, str, str]]:
    """``(parameter, shape, template)`` for the tag, one parameter at a time."""
    from scripts.lib.template_library_parity import variants

    out = []
    names = variants.passed_names(info)
    for name in names:
        extra = " open=True" if info.name == "dropdown_menu" else ""
        base = " ".join(url_sweep.typical_args(info, name))
        # an ``event`` makes a card / node clickable (more attributes carry the item's id/title)
        if "event" in names and name != "event":
            base += ' event="ev"'
        # an org chart draws from its ``root`` id: the item ids are the payload, so is the root
        if info.name == "org_chart" and name == "nodes":
            base += " root=p_scalar"
        # DATA shapes (a list of items, an item) are swept for every hole. A SCALAR is the
        # developer's own tag argument (written in the template): its marked-safe value is
        # honoured by every tag's text and attributes (the tag-argument policy), so a scalar is
        # swept for a script-capable URL only (see ``test_no_tag_..._scalar``).
        for shape in shapes:
            args = f"{base} {name}=p_{shape}{extra}".strip()
            src = url_sweep.wrap(info, args, None, "")
            if info.name == "dropdown_menu":
                # an EMPTY body: a body replaces the items (``render_dropdown_menu``)
                src = (
                    "{% load djust_components %}{% dropdown_menu "
                    + f"{name}=p_{shape}"
                    + (" open=True" if name != "open" else "")
                    + " %}{% enddropdown_menu %}"
                )
            elif info.name == "menu_item":
                # not ``disabled`` (a disabled item draws no link) and not ``divider``
                src = (
                    "{% load djust_components %}{% menu_item "
                    + ('label="x" ' if name != "label" else "")
                    + f"{name}=p_{shape}"
                    + " %}"
                )
            out.append((name, shape, src))
    return out


def _contexts(value: object) -> dict:
    return {
        "p_scalar": value,
        "p_list_str": [value, value],
        "p_dict": url_sweep.dict_of(value, TAG_KEYS),  # type: ignore[arg-type]
        "p_list_dict": [url_sweep.dict_of(value, TAG_KEYS)] * 2,  # type: ignore[arg-type]
    }


def _both(src: str, ctx: dict) -> tuple[str | None, str | None]:
    from djust.tests.test_component_dj_attr_parity_3669 import _django
    from djust.tests.test_components_csp_aria import _rust

    out = []
    for engine in (_django, _rust):
        harness.reset_ids()
        try:
            out.append(str(engine(src.replace("{% load djust_components %}", ""), ctx)))
        except Exception:  # noqa: BLE001 - a call a tag rejects says nothing
            out.append(None)
    return out[0], out[1]


def _sample_tag(tag: str) -> None:
    sweep_sample.skip_unless_sampled(
        "marked-safe/tags", tag, TAGS, budget=TAG_BUDGET, paths=_tag_paths, shared=SHARED_PATHS
    )


class TestTags:
    @pytest.mark.pr_tier
    def test_every_data_tag_exists(self) -> None:
        inv = _inventory()
        assert [t for t in DATA_TAGS if t not in inv] == []

    @pytest.mark.pr_tier
    def test_each_tag_is_reached_by_the_driver(self) -> None:
        """The instrument sees a URL arrive at an attribute, for every tag (a sweep over tags it
        never reached would pass for nothing)."""
        inv = _inventory()
        reached: set[str] = set()
        for tag in TAGS:
            for _param, _shape, src in _tag_calls(inv[tag], (*DATA_SHAPES, "scalar")):
                py, rs = _both(src, _contexts(HTTPS))
                if any(out and url_sweep.carries(out, HTTPS) for out in (py, rs)):
                    reached.add(tag)
                    break
        assert reached == set(TAGS), sorted(set(TAGS) - reached)

    @pytest.mark.sweep("marked-safe/tags")
    @pytest.mark.parametrize("tag", TAGS)
    def test_no_tag_lets_a_marked_safe_item_value_out_of_its_attribute(self, tag: str) -> None:
        _sample_tag(tag)
        info = _inventory()[tag]
        calls = _tag_calls(info)
        holes: dict[tuple[str, str], set] = {}
        rendered = 0
        for claim, make in CLAIMS.items():
            for label, payload in TAG_PAYLOADS:
                ctx = _contexts(make(payload))
                for param, shape, src in calls:
                    for engine, out in zip(("python", "rust"), _both(src, ctx)):
                        if out is None:
                            continue
                        rendered += 1
                        found = holes_in(out)
                        if found:
                            holes.setdefault((engine, param), set()).add((shape, claim, label))
        assert rendered > 100, f"{tag}: the sweep barely ran ({rendered})"
        assert not holes, "\n".join(f"{tag} {k}: {sorted(v)[:2]}" for k, v in sorted(holes.items()))

    @pytest.mark.sweep("marked-safe/tags")
    @pytest.mark.parametrize("tag", TAGS)
    def test_no_tag_lets_a_marked_safe_scalar_url_through_as_a_script_url(self, tag: str) -> None:
        """The scalar URL arguments of these tags (``src=``, ``href=``) are checked too: the tags
        whose item URLs are data share the helper, and a script scheme is never honoured."""
        _sample_tag(tag)
        info = _inventory()[tag]
        calls = _tag_calls(info, ("scalar",))
        holes = set()
        for claim, make in CLAIMS.items():
            for label, payload in TAG_PAYLOADS:
                if label.startswith("breakout"):
                    continue
                ctx = _contexts(make(payload))
                for param, _shape, src in calls:
                    for engine, out in zip(("python", "rust"), _both(src, ctx)):
                        if out and url_sweep.findings(out):
                            holes.add((engine, param, claim, label))
        assert not holes, f"{tag}: {sorted(holes)[:4]}"


class TestTheNoOpHrefException:
    """The ONE exception, on purpose: the literal no-op hrefs of a dropdown toggle. Exact text
    (after ``strip`` / case-folding, nothing appended), in an ``href`` only."""

    @pytest.mark.parametrize("claim", ["plain", *VALUE_CLAIMS])
    @pytest.mark.parametrize(
        ("given", "kept"),
        [
            ("javascript:void(0)", "javascript:void(0)"),
            ("javascript:;", "javascript:;"),
            ("  JavaScript:Void(0)  ", "javascript:void(0)"),
            ("JAVASCRIPT:;", "javascript:;"),
            # whitespace around the literal is stripped (the rule says so); nothing inside it
            ("javascript:void(0) ", "javascript:void(0)"),
            ("\tjavascript:;\n", "javascript:;"),
        ],
    )
    def test_the_literal_is_kept(self, claim: str, given: str, kept: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value) == kept

    @pytest.mark.parametrize("claim", ["plain", *VALUE_CLAIMS])
    @pytest.mark.parametrize(
        "given",
        [
            "javascript:void(0);alert(1)",
            # the spellings a widened ``_NOOP_HREFS`` would let through: each is refused
            "javascript:void(0);",
            "JAVASCRIPT:VOID(0);",
            "javascript:void (0)",
            "javascript:void(0)\x00",
            "javascript:void(0) x",
            "javascript:;;",
            "javascript:; ;",
            "javascript:void(0),",
            "javascript:void(0)#",
            "javascript:undefined",
            "javascript:0",
            "javascript:void 0",
            "javascript:void(0) 1x",
            "javascript:void(0)//x",
            "javascript:void( 0 )",
            "javascript:void(1)",
            "javascript:;alert(1)",
            "javascript:",
            "java\tscript:void(0)",
            "\x00javascript:void(0)",
            "vbscript:void(0)",
            "data:;",
            "javascript:alert(1)",
        ],
    )
    def test_anything_else_is_neutralised_marked_or_not(self, claim: str, given: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value) == "#"

    @pytest.mark.parametrize("claim", ["plain", *VALUE_CLAIMS])
    @pytest.mark.parametrize("given", ["javascript:void(0)", "javascript:;"])
    def test_an_image_source_never_gets_the_exception(self, claim: str, given: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value, image=True) == "#"

    def test_the_class_components_keep_a_toggle_href(self) -> None:
        from djust.components.components.nav_menu import NavMenu

        out = str(NavMenu(items=[{"label": "a", "href": "javascript:void(0)"}]).render())
        assert 'href="javascript:void(0)"' in out, out


class TestTextSlotsKeepTheirMarkup:
    """The other half of the rule: what a TEXT slot did with a marked-safe value is unchanged.

    The template TAGS keep the markup of a ``mark_safe`` / ``SafeString`` / lazy marked-safe value
    in a text slot (``conditional_escape``), and escape ``Markup`` (``str()`` drops its
    ``__html__``): both as before. The class-based components have always escaped what they were
    given, a marked-safe value included, which is why they pass ``honor_safe=False``."""

    @pytest.mark.parametrize("claim", VALUE_CLAIMS)
    def test_a_class_component_escapes_a_text_slot_as_it_always_did(self, claim: str) -> None:
        from djust.components.components.breadcrumb import Breadcrumb

        out = str(
            Breadcrumb(
                items=[{"label": CLAIMS[claim]("<b>home</b>"), "url": "/"}, {"label": "x"}]
            ).render()
        )
        assert "&lt;b&gt;home&lt;/b&gt;" in out and "<b>" not in out, out
        out = str(
            Breadcrumb(items=[{"label": "a", "url": CLAIMS[claim]("javascript:alert(1)")}]).render()
        )
        assert "javascript:" not in out, out

    @pytest.mark.parametrize("claim", ["mark_safe", "safestring-subclass", "lazy-mark_safe"])
    @pytest.mark.parametrize(
        ("src", "key"),
        [
            ("{% data_card_grid items=p_list_dict %}", "title"),
            ("{% org_chart nodes=p_list_dict root=p_scalar %}", "name"),
            ("{% activity_feed events=p_list_dict %}", "user"),
            ("{% kanban_board columns=p_list_dict %}", "title"),
            ("{% audit_log entries=p_list_dict %}", "user"),
            ("{% chat_bubble message=p_dict %}", "text"),
        ],
        ids=[
            "data_card_grid", "org_chart", "activity_feed", "kanban_board", "audit_log",
            "chat_bubble",
        ],
    )  # fmt: skip
    def test_a_tag_text_slot(self, claim: str, src: str, key: str) -> None:
        from djust.tests.test_component_dj_attr_parity_3669 import _django

        marked = CLAIMS[claim]("<b>shown</b>")
        item = {
            "id": "n1",
            "title": marked,
            "name": marked,
            "user": marked,
            "text": marked,
            "action": "did",
        }
        out = _django(src, {"p_list_dict": [item], "p_dict": item, "p_scalar": "n1"})
        assert "<b>shown</b>" in out, out


class TestModelTable:
    """``model_table`` keeps a marked-safe value in its CELLS (text) and never in the attributes it
    also writes: the row id (``dj-value-row``), the column key and the filter values (#3898)."""

    @staticmethod
    def _queryset(**fields: object):
        from django.contrib.auth.models import User

        class QS(list):
            model = User

        return QS([User(**fields)])

    @staticmethod
    def _render(qs, **kw: object) -> str:
        from djust.components.templatetags._forms import model_table

        return str(model_table(queryset=qs, include=["username"], **kw))

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    @pytest.mark.parametrize(
        "payload", ['x" onmouseover="alert(1)" y="', "x' onmouseover='alert(1)"]
    )
    def test_a_marked_safe_row_id_stays_inside_dj_value_row(self, claim: str, payload: str) -> None:
        qs = self._queryset(id=CLAIMS[claim](payload), username="u")
        out = self._render(qs, selectable=True)
        assert "dj-value-row" in out
        assert holes_in(out) == [], out

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_a_marked_safe_cell_keeps_its_markup(self, claim: str) -> None:
        qs = self._queryset(id=1, username=CLAIMS[claim]("<i>mark</i>"))
        out = self._render(qs)
        # SafeString family and lazy keep the markup (``conditional_escape(str(v))``); an
        # ``__html__`` str is the one the cell code escapes, as before
        assert ("<i>mark</i>" in out) == (claim != "html-object"), out

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_a_marked_safe_filter_value_stays_inside_value(self, claim: str) -> None:
        qs = self._queryset(id=1, username="u")
        out = self._render(qs, filters={"username": CLAIMS[claim]('x" onmouseover="alert(1)" y="')})
        assert "data-table__filter-input" in out
        assert holes_in(out) == [], out

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    @pytest.mark.parametrize(
        "payload", ['x" onmouseover="alert(1)" y="', "x' onmouseover='alert(1)"]
    )
    def test_a_marked_safe_column_key_and_filter_option_stay_inside_their_attributes(
        self, claim: str, payload: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The columns are inferred from the model, but the tag writes them to attributes the
        same way as the other item values, so they get the same cells (#3904 review)."""
        from djust.components.templatetags import _forms

        marked = CLAIMS[claim](payload)
        columns = [
            {
                "key": marked,
                "label": "L",
                "sortable": True,
                "filterable": True,
                "filter_type": "select",
                "filter_options": [{"value": marked, "label": "a"}],
            }
        ]
        monkeypatch.setattr(_forms, "_infer_columns", lambda *a, **k: columns)
        monkeypatch.setattr(_forms, "_queryset_to_rows", lambda *a, **k: [])
        out = self._render(self._queryset(id=1, username="u"))
        assert "dj-value-column" in out and "<option" in out
        assert holes_in(out) == [], out


# --- EVERY tag of every library: what is covered and what is not, derived ----------------------

#: ``library/tag/parameter`` of the template tags where an ITEM value (a list of items, an item)
#: that is marked safe still leaves an attribute, DERIVED by :func:`wide_tag_leaks` (every tag of
#: the swept libraries, every parameter that takes a list, see the derivation below). Empty since
#: #3904, and kept as the pin: a new leak fails, and the list can only shrink. That the
#: derivation can find a leak at all is not assumed: ``TestTheDerivationCanFail`` plants one of
#: each kind it has missed before and requires it to be reported.
KNOWN_TAG_ITEM_LEAKS: frozenset[str] = frozenset()

#: The other list parameters of a tag, filled with these (the harness's own benign lists) while
#: one parameter is under test: ``audit_log`` draws a row's cells only when ``entries`` is filled,
#: and only then does a ``columns`` name reach a ``class=`` attribute.
COMPANIONS: tuple[str | None, ...] = (None, "lst_dict", "lst_str")

#: Item keys that hold a LIST of items of the same kind (a kanban column's ``cards``), beyond the
#: URL sweep's ``NESTED_KEYS``. A flat item fills such a key with the claim itself, and a tag
#: iterating it then walks the characters of a string (or crashes), so what is inside the nested
#: items was never reached: the ``list_nested`` shape puts one flat item into each of them.
ITEM_CONTAINERS = frozenset(
    {
        *url_sweep.NESTED_KEYS,
        "cards",
        "tasks",
        "panels",
        "rows",
        "segments",
        "series",
        "points",
        "data",
    }
)
#: A scalar parameter that makes an item "selected" or "current" when it equals the item's own
#: text (a combobox draws its input's value from the matching option, a multi-select its chips
#: from the options listed in ``selected``): filled with the item's own text in the cells that
#: ask for it. The scalar is the developer's own and is never marked safe here.
MATCHING = {"value": "p_scalar", "selected": "p_scalar_list"}
#: ... and the same scalars filled with the text of the BENIGN item (a number key reads as
#: ``1``): the item that matches is then not the one whose key carries the claim, so a key
#: written to the same attribute as the scalar (a combobox input's ``value=`` from the option
#: label) is told apart from it.
MATCHING_BENIGN = {"value": "p_scalar_benign", "selected": "p_scalar_list_benign"}
#: ``list_pair``: a list of ``(value, label)`` pairs, which several option parameters accept.
ITEM_SHAPES = (*DATA_SHAPES, "list_nested", "list_pair")
#: Shapes whose items are text (or pairs of it): the claim is the item itself, there is no key.
TEXT_SHAPES = ("list_str", "list_pair")

#: The benign text of an item key that is not under test, and the keys whose benign value is a
#: NUMBER. A tag that calls ``float()`` / ``int()`` on a key raises when it is handed text, skips
#: the item, and never reaches the keys it prints in attributes: the first derivation gave every
#: key the claim and so never saw ``pie_chart`` or ``table_of_contents``. Only the key under test
#: carries the claim now, and the rest are benign (numbers where a tag does arithmetic).
BENIGN = "ZQBENIGN"
#: ``key=EVERY_KEY`` puts the claim in every key that is not a number: the question "is this item
#: also printed as TEXT somewhere" needs no particular key.
EVERY_KEY = "<every key>"
NUMERIC_KEYS = frozenset(
    {
        "value", "count", "total", "x", "y", "width", "height", "start", "end", "duration",
        "progress", "percent", "level", "col", "row", "min", "max", "size", "step", "amount",
        "price", "quantity", "index", "order", "weight", "lat", "lng", "zoom", "depth", "unread",
        "number", "offset", "limit", "page", "rating", "score", "length", "days", "radius",
        "opacity", "angle", "r", "cx", "cy", "left", "top", "right", "bottom", "id_num",
    }
)  # fmt: skip


class ItemMode(NamedTuple):
    """Which branch of a tag a cell asks for."""

    label: str
    flags: bool  # the boolean parameters on (typical) or left out (their defaults)
    extra: tuple[str, ...]  # further arguments: matching scalars, typical scalars, one enum literal
    body: str | None  # a block tag's body: ``None`` is the harness's, ``""`` is empty


class ItemCell(NamedTuple):
    """One way of calling a tag with an item parameter filled."""

    shape: str
    companion: str | None  # the tag's other lists, filled with benign items
    mode: ItemMode
    src: str


class _Reads(set):
    """The item keys a tag read, recorded at run time (not read from its source)."""


class _RecDict(dict):
    """An item that records the keys read from it."""

    def __init__(self, data: dict, reads: _Reads) -> None:
        super().__init__(data)
        self._reads = reads

    def get(self, key, default=None):  # noqa: ANN001
        self._reads.add(key)
        return super().get(key, default)

    def __getitem__(self, key):  # noqa: ANN001
        self._reads.add(key)
        return super().__getitem__(key)

    def __contains__(self, key) -> bool:  # noqa: ANN001
        self._reads.add(key)
        return super().__contains__(key)


class _IterProbe(list):
    """A list that notes it was walked or indexed: the test for "this name is a list parameter"."""

    touched = False

    def __iter__(self):  # noqa: ANN204
        self.touched = True
        return super().__iter__()

    def __getitem__(self, index):  # noqa: ANN001
        self.touched = True
        return super().__getitem__(index)


@lru_cache(maxsize=None)
def _source_facts(module_name: str) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """``(string literals, {parameter: the literals it is compared with})`` of a tag module.

    A tag that reads ``step = kw.get("step")`` and branches on ``step == "map"`` only writes its
    attribute when the call says ``step="map"``: the second value is how a cell learns to say it."""
    module = sys.modules.get(module_name)
    try:
        tree = ast.parse(inspect.getsource(module))
    except (TypeError, OSError, SyntaxError):
        return frozenset(), {}
    literals = {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    alias: dict[str, str] = {}

    def param_of(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return alias.get(node.id, node.id)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            return node.slice.value if isinstance(node.slice.value, str) else None
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, source = node.targets[0], param_of(node.value)
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, (ast.Call, ast.Subscript))
                and source
            ):
                alias[target.id] = source
    branches: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        for i, side in enumerate(sides):
            param = param_of(side)
            if not param:
                continue
            for other in sides[:i] + sides[i + 1 :]:
                values = (
                    other.elts if isinstance(other, (ast.Tuple, ast.List, ast.Set)) else [other]
                )
                for v in values:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value:
                        branches.setdefault(param, set()).add(v.value)
    return frozenset(literals), {k: frozenset(v) for k, v in branches.items()}


def _parameter_names(info) -> tuple[list[str], list[str]]:
    """``(names to try as a list parameter, scalar names)`` of a tag.

    A simple tag has a strict signature. A tag written as a Node takes any keyword and reads some
    of them in a helper, possibly of its parent (``filter_select`` takes ``options`` and the
    parent draws them), so the names it can read are the ones its module says as a string."""
    from scripts.lib.template_library_parity import variants

    names = list(variants.passed_names(info))
    scalars = []
    if info.py_kind not in variants.SIMPLE_KINDS:
        module = getattr(url_sweep._fn_of(info), "__module__", "")
        literals, _ = _source_facts(module)
        names += sorted((variants.LIST_EXACT | set(url_sweep.NESTED_KEYS)) & literals - set(names))
        names += [n for n in info.py_keys if n not in names]
    for n in names:
        if variants.kind_of(n) not in ("list", "bool") and n not in ITEM_CONTAINERS:
            scalars.append(n)
    return names, scalars


def _wrap(info, args: str, body: str | None) -> str:
    from scripts.lib.template_library_parity import variants

    parent = variants.PARENT.get(info.name)
    head = "{% " + info.name + (" " + args if args else "") + " %}"
    inner = head
    if info.is_block:
        text = url_sweep.BODIES.get(info.name, variants.BODY) if body is None else body
        inner = head + text + "{% " + url_sweep.end_tag(info) + " %}"
    if parent:
        inner = "{% " + parent + " open=True %}" + inner + "{% end" + parent + " %}"
    return "{% load " + info.lib + " %}" + inner


def _modes(info, name: str, names: list[str], scalars: list[str]) -> list[ItemMode]:
    """The branches worth asking a tag for: flags on or off x (nothing more, the scalar that
    selects an item, every scalar at its typical value), one enum literal at a time, and a block
    tag with an empty body (its default rows)."""
    from scripts.lib.template_library_parity import variants

    typical = url_sweep.typical_args(info, name)
    bare = [a for a in typical if variants.kind_of(a.split("=")[0]) != "bool"]
    given = {a.split("=")[0] for a in typical}
    matching = tuple(f"{k}={v}" for k, v in MATCHING.items() if k in names and k != name)
    matching_benign = tuple(
        f"{k}={v}" for k, v in MATCHING_BENIGN.items() if k in names and k != name
    )
    typical_scalars = tuple(
        f"{n}={variants.token(n, 'typical')}" for n in scalars if n != name and n not in given
    )
    module = getattr(url_sweep._fn_of(info), "__module__", "")
    branches = _source_facts(module)[1]
    enums = tuple(
        f'{n}="{lit}"'
        for n in scalars
        if n != name
        for lit in sorted(branches.get(n, ()))
        if lit.replace("_", "").replace("-", "").isalnum()
    )
    modes: list[ItemMode] = []
    for flags in (True, False):
        if not flags and bare == typical:
            continue
        side = "on" if flags else "off"
        modes.append(ItemMode(f"{side}", flags, (), None))
        if matching:
            modes.append(ItemMode(f"{side}/match", flags, matching, None))
            modes.append(ItemMode(f"{side}/match-benign", flags, matching_benign, None))
        if typical_scalars:
            modes.append(ItemMode(f"{side}/typical", flags, typical_scalars, None))
    modes += [ItemMode(f"enum/{e}", True, (e,), None) for e in enums]
    if info.is_block:
        modes.append(ItemMode("empty-body", True, (), ""))
        if bare != typical:
            modes.append(ItemMode("empty-body/off", False, (), ""))
    return modes


def _item_cells(info, name: str, *, shapes: tuple[str, ...] = ITEM_SHAPES) -> list[ItemCell]:
    """Every cell for parameter ``name`` of the tag: shape x companions x mode."""
    from scripts.lib.template_library_parity import variants

    names, scalars = _parameter_names(info)
    others = [n for n in names if variants.kind_of(n) == "list" and n != name]
    typical = url_sweep.typical_args(info, name)
    bare = [a for a in typical if variants.kind_of(a.split("=")[0]) != "bool"]
    out = []
    for mode in _modes(info, name, names, scalars):
        taken = {a.split("=")[0] for a in mode.extra}
        base = [a for a in (typical if mode.flags else bare) if a.split("=")[0] not in taken]
        for shape in shapes:
            for companion in COMPANIONS:
                if companion and not others:
                    continue
                args = [*base, f"{name}=p_{shape}", *mode.extra]
                if companion:
                    args += [f"{q}={companion}" for q in others if q not in taken]
                out.append(ItemCell(shape, companion, mode, _wrap(info, " ".join(args), mode.body)))
    return out


def _item_context(
    claim: object,
    keys: set[str],
    scalar: str,
    *,
    key: str | None = None,
    reads: _Reads | None = None,
) -> dict:
    """The context of an item cell.

    ``claim`` is written to the item ``key`` and to nothing else; every other key is benign. For
    a text shape (``key`` is ``None``) ``claim`` is the item itself. ``scalar`` is the plain text
    of the claim, for the scalar that makes an item selected."""
    containers = keys & ITEM_CONTAINERS

    def record(data: dict) -> dict:
        return _RecDict(data, reads) if reads is not None else data

    every = key == EVERY_KEY

    def value_of(k: str) -> object:
        if every:
            return 1 if k in NUMERIC_KEYS else claim
        if k == key:
            return claim
        return 1 if k in NUMERIC_KEYS else BENIGN

    flat = {k: value_of(k) for k in (keys | ({key} if key and not every else set())) - containers}
    nested = {**flat, **{c: [record(dict(flat))] for c in containers}}
    text = claim if key in (None, EVERY_KEY) else BENIGN
    return {
        **harness.make_context(False),
        "p_scalar": scalar,
        "p_scalar_list": [scalar, scalar],
        "p_scalar_benign": "1",
        "p_scalar_list_benign": ["1", "1"],
        "p_list_str": [text, text],
        "p_list_pair": [(text, text)] * 2,
        "p_dict": record(flat),
        "p_list_dict": [record(dict(flat)), record(dict(flat))],
        "p_list_nested": [record(dict(nested)), record(dict(nested))],
    }


def _django_render(src: str, ctx: dict) -> str | None:
    from django.template import engines

    return url_sweep._render(engines["django"], src, ctx)


def _keys_of(info) -> set[str]:
    return url_sweep.keys_read(info, url_sweep._fn_of(info)) | set(TAG_KEYS) | set(ATTR_KEYS)


def _cell_keys(info, cell: ItemCell) -> frozenset[str | None]:
    """The item keys the tag READS in this cell, recorded at run time; ``{None}`` for a text shape.

    An empty set means the cell did not render, or the tag read nothing from its items."""
    if cell.shape in TEXT_SHAPES:
        return frozenset({None})
    reads = _Reads()
    ctx = _item_context(BENIGN, _keys_of(info), BENIGN, reads=reads)
    if _django_render(cell.src, ctx) is None:
        return frozenset()
    return frozenset(k for k in reads if isinstance(k, str) and k not in ITEM_CONTAINERS)


def _takes_items(info, name: str) -> bool:
    """Whether ``name`` is a list parameter of the tag: a list handed to it is walked or indexed
    in some branch (any mode), recorded at run time."""
    probe = _IterProbe([{k: BENIGN for k in _keys_of(info)}])
    for cell in _item_cells(info, name, shapes=("list_probe",)):
        probe.touched = False
        ctx = {**harness.make_context(False), "p_list_probe": probe}
        _django_render(cell.src, ctx)
        if probe.touched:
            return True
    return False


def item_probe_cases(info, name: str) -> list[tuple[ItemCell, str | None]]:
    """``(cell, key)``: every cell of the parameter with every key the tag reads from its items."""
    return [
        (cell, key)
        for cell in _item_cells(info, name)
        for key in sorted(_cell_keys(info, cell), key=str)
    ]


def wide_tag_leaks(libraries: frozenset[str] | None = None) -> set[str]:
    """``library/tag/parameter`` for every (tag, list parameter) where a marked-safe ITEM value
    creates an attribute of the markup the plain string does not, in Django's engine, in any
    branch of the tag (:func:`_modes`), with any key the tag reads from its items (:func:`_cell_keys`)
    holding the value while the others are benign. The scalar arguments of a tag are the
    developer's own and are not counted. ``libraries`` narrows it to the named libraries."""
    payload = 'x" onmouseover="alert(zq7)" y="'
    leaks: set[str] = set()
    for _key, info in sorted(url_sweep.inventory().items()):
        if info.name.startswith("end") or (libraries and info.lib not in libraries):
            continue
        names, _scalars = _parameter_names(info)
        keys = _keys_of(info)
        for name in names:
            if not _takes_items(info, name):
                continue
            for cell, key in item_probe_cases(info, name):
                ctx = _item_context(mark_safe(payload), keys, payload, key=key)
                html = _django_render(cell.src, ctx)
                if html and any(len(h) == 2 for h in holes_in(html)):
                    leaks.add(f"{info.lib}/{info.name}/{name}")
                    break
    return leaks


class TestEveryClass:
    """EVERY class component (all of them the URL sweep can drive, 200+), not only the ones whose
    module names ``url_attr``: an item value that is marked safe never creates the payload's
    handler in an attribute, and no script URL a browser follows is printed. A scalar constructor
    argument is the developer's own (see ``wide`` in :func:`sweep`)."""

    @pytest.mark.sweep("marked-safe/classes")
    @pytest.mark.parametrize("companions", [False, True], ids=["bare", "companions"])
    def test_no_class_lets_a_marked_safe_item_value_out_of_an_attribute(
        self, companions: bool
    ) -> None:
        (url_found, url_rendered), (found, rendered) = sweep(
            Sweep(CLASSES),
            Sweep(ALL_CLASSES, ("mark_safe", "html-object"), ITEM_ATTRIBUTE_PAYLOADS, wide=True),
            force_python=True,
            companions=companions,
        )
        assert url_rendered > _floor(CLASSES, 10_000), url_rendered
        assert ONLY or len(CLASSES) >= 20, sorted(CLASSES)
        assert not url_found, _report(url_found)
        assert rendered > _floor(ALL_CLASSES, 40_000), f"the sweep barely ran ({rendered})"
        assert ONLY or len(ALL_CLASSES) > 150, len(ALL_CLASSES)
        assert not found, _report(found)

    def test_the_classes_are_derived_not_listed(self) -> None:
        """Every driven class is swept (the set is the URL sweep's own), and the URL family is a
        subset of it."""
        assert set(CLASSES) <= set(ALL_CLASSES)
        assert ONLY or len(ALL_CLASSES) > len(CLASSES) * 4


#: ``Class.parameter`` of the pairs NO cell can get through, and why. Each one takes numbers, a
#: dotted module path or a queryset and raises on any text, so no markup can arrive in them; the
#: list is exact (a pair that starts to draw fails until it is removed here).
UNREACHED = {
    "components.bar_chart.BarChart.data": "numbers",
    "components.calendar_heatmap.CalendarHeatmap.year": "a year",
    "components.markdown.Markdown.extensions": "module names (ModuleNotFoundError)",
    "components.markdown_editor.MarkdownEditor.mode": "two allowed words (ValueError)",
    "components.meter.Meter.segments": "numbers",
    "data.pagination.PaginationComponent.max_visible_pages": "a number",
    "data.pagination.PaginationComponent.total_items": "a number",
    "data.pagination.PaginationComponent.total_pages": "a number",
    "forms.foreign_key.ForeignKeySelect.queryset": "a queryset",
    "forms.foreign_key.ManyToManySelect.queryset": "a queryset",
    "ui.pagination_simple.Pagination.current_page": "a number",
    "ui.pagination_simple.Pagination.total_pages": "a number",
    "ui.progress.ProgressComponent.max_value": "a number",
    "ui.progress.ProgressComponent.min_value": "a number",
    "ui.progress.ProgressComponent.value": "a number",
    "ui.progress_simple.Progress.value": "a number",
    "ui.offcanvas_simple.Offcanvas.placement": "four allowed words (ValueError)",
}

#: ``Class.parameter`` -> the item keys a cell could not take the claim for, found per key by
#: :func:`_search_overrides`. A key left out is a key the sweep does NOT test: it is stated here,
#: exactly (a number is a number key: ``int(item["level"])``; ``badge`` is left out of the shape
#: that gives it the claim itself, because the list group reads it as a mapping, which the typed
#: shape ``list_dict`` gives it and sweeps).
CARRIED_DIFFERENTLY = {
    "components.dashboard_grid.DashboardGrid.panels": {
        "col=number", "height=number", "row=number", "width=number",
    },
    "components.org_chart.OrgChart.nodes": {"parent=number"},
    "components.table_of_contents.TableOfContents.items": {"level=number"},
    "ui.list_group_simple.ListGroup.items": {"badge=left out"},
}  # fmt: skip


def _numeric_parameter(cls: type, name: str) -> bool:
    """A constructor parameter whose default is a bool / int / float (derived from the signature):
    it selects, it does not print."""
    try:
        param = inspect.signature(cls.__init__).parameters.get(name)  # type: ignore[misc]
    except (TypeError, ValueError):
        return False
    return param is not None and isinstance(param.default, (bool, int, float))


def reach(hybrid: bool = False, claim: str | None = None) -> dict[tuple[str, str], dict[str, int]]:
    """``{(class, parameter): {"rendered": n, "echoed": n}}`` over EVERY derived pair: the
    parameter is given a marked-safe probe (in every shape, under every framework, with and
    without the companions) and the output is searched for the probe. ``rendered`` counts the
    cells that drew; ``echoed`` those whose output carries the probe.

    ``hybrid`` is the same question for the second path of ``Component.render``: the classes that
    have a ``template``, drawn through it with the crate instance taken away (#3900); the
    default is ``_render_custom``, the path the rest of this file governs.

    ``claim`` wraps the probe the way that claim kind wraps a payload (``CLAIMS[claim]``), so the
    question "does a value of THIS kind reach the output" is asked per kind, not only for a
    marked-safe string."""
    out: dict[tuple[str, str], dict[str, int]] = {}
    probe = CLAIMS[claim](REACH_MARK) if claim else PROBE
    for key, cls in (TEMPLATE_CLASSES if hybrid else ALL_CLASSES).items():
        names, required = class_params(cls)
        keys = class_keys(cls)
        for name in names:
            out[(key, name)] = {"rendered": 0, "echoed": 0}
        for companions in (False, True):
            for framework in url_sweep.FRAMEWORKS[: 1 if hybrid else None]:
                for shape in SHAPES:
                    for name in names:
                        result = drive(
                            cls, key, names, required, keys, name, shape, framework,
                            companions, probe, not hybrid, hybrid,
                        )  # fmt: skip
                        if result is None:
                            continue
                        out[(key, name)]["rendered"] += 1
                        if REACH_MARK in str(result):
                            out[(key, name)]["echoed"] += 1
    return out


def carried_differently() -> dict[str, set[str]]:
    """``"Class.parameter" -> {"key=left out" / "key=number"}`` for every item key some cell of
    the probe could not take the claim for (what :func:`_search_overrides` found), so a key the
    sweep does not reach is stated, not silently skipped."""
    found: dict[str, set[str]] = {}
    for (
        key,
        name,
        _shape,
        _fw,
        _companions,
        force_python,
        hybrid,
    ), overrides in _OVERRIDES.items():
        if not force_python or hybrid or not overrides:
            continue
        for k, form in overrides.items():
            how = "left out" if form is DROP else "number"
            found.setdefault(f"{key.replace('djust.components.', '')}.{name}", set()).add(
                f"{k}={how}"
            )
    return found


@pytest.mark.pr_tier
class TestEveryClassIsReached:
    """The class sweep skips a cell its component rejects. That is only honest if the skip is
    accounted for: this test fails a (class, parameter) pair that NO cell drew (so nothing was
    swept there), and a class none of whose parameters ever printed a probe. The first version
    of the sweep skipped every item cell of ``ui.ListGroup`` and ``ui.NavBar`` (a ``badge`` that
    must be a mapping, a ``dropdown`` that must be a list) and every parameter of ``StatCard`` (a
    required ``value`` that must be text) without a word, and a leak sat behind each."""

    @pytest.fixture(scope="class")
    def outcome(self) -> dict[tuple[str, str], dict[str, int]]:
        return reach()

    def test_every_pair_is_drawn_by_some_cell_or_listed_with_its_reason(self, outcome) -> None:
        short = lambda key: key.replace("djust.components.", "")  # noqa: E731
        unreached = {pair for pair, n in outcome.items() if not n["rendered"]}
        unexplained = {
            pair for pair in unreached if not _numeric_parameter(ALL_CLASSES[pair[0]], pair[1])
        }
        listed = {f"{short(k)}.{n}" for k, n in unexplained if not ONLY}
        if ONLY:
            assert not [p for p in listed if p not in UNREACHED], sorted(listed)
            return
        assert listed == set(UNREACHED), (
            f"pairs no cell drew (fix the probe's input, or list them with a reason): "
            f"{sorted(listed - set(UNREACHED))}\n"
            f"listed but now drawn (delete from UNREACHED): {sorted(set(UNREACHED) - listed)}"
        )

    def test_the_pairs_are_partitioned_and_most_of_them_print_the_probe(self, outcome) -> None:
        echoed = {p for p, n in outcome.items() if n["echoed"]}
        silent = {p for p, n in outcome.items() if n["rendered"] and not n["echoed"]}
        unreached = {p for p, n in outcome.items() if not n["rendered"]}
        derived = {(key, name) for key, cls in ALL_CLASSES.items() for name in class_params(cls)[0]}
        assert echoed | silent | unreached == derived
        assert not (echoed & silent or echoed & unreached or silent & unreached)
        assert len(echoed) + len(silent) + len(unreached) == len(derived)
        assert ONLY or len(echoed) > 700, len(echoed)

    def test_the_keys_a_cell_could_not_carry_are_exactly_the_listed_ones(self, outcome) -> None:
        """A crash is searched per KEY, and what the search leaves out or turns into a number
        is stated here, so the sweep cannot lose a key without a test saying so."""
        assert carried_differently() == CARRIED_DIFFERENTLY

    def test_every_class_prints_a_probe_through_some_parameter(self, outcome) -> None:
        silent_classes = sorted(
            {
                key
                for key in ALL_CLASSES
                if not any(n["echoed"] for (k, _name), n in outcome.items() if k == key)
            }
        )
        assert not silent_classes, silent_classes


def raw_text_pairs() -> set[tuple[str, str]]:
    """``(class key, constructor parameter)`` of every slot a shipped hybrid template writes into
    the TEXT of a raw-text element (derived from the templates, not listed)."""
    from djust.components import _hybrid_attrs

    pairs = set()
    for key, cls in TEMPLATE_CLASSES.items():
        for slot in _hybrid_attrs.raw_slots(cls.template):
            name = re.match(r"\s*([A-Za-z_]\w*)", slot.expr)
            assert name, (key, slot)
            pairs.add((key, name.group(1)))
    return pairs


class TestRawTextCells:
    """The raw-text cells of the hybrid sweep are DRIVEN, per claim kind (#3914): a pair that no
    cell reaches, or a claim that is not applied to it, fails here instead of passing by default."""

    def test_the_pairs_are_derived_and_include_the_reported_one(self) -> None:
        pairs = raw_text_pairs()
        assert ONLY or ("djust.components.ui.textarea_simple.TextArea", "value") in pairs, pairs

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_every_pair_is_driven_with_the_breakout_and_prints_it_as_text(self, claim: str) -> None:
        label, payload = RAW_TEXT_PAYLOAD
        pairs = raw_text_pairs()
        assert pairs or ONLY
        for key, name in sorted(pairs):
            cls = TEMPLATE_CLASSES[key]
            names, required = class_params(cls)
            assert name in names, (key, name, names)
            out = drive(
                cls, key, names, required, class_keys(cls), name, "scalar",
                url_sweep.FRAMEWORKS[0], False, CLAIMS[claim](payload), False, True,
            )  # fmt: skip
            assert out is not None, (key, name, claim, "the cell was not driven")
            text = str(out)
            assert url_sweep.MARK in text and "&lt;/textarea&gt;" in text, (key, name, claim, text)
            assert holes_in(text) == [], (key, name, claim, holes_in(text))

    def test_the_claim_kinds_are_all_of_the_sweeps(self) -> None:
        """A claim kind added to ``CLAIMS`` is driven here without a line changing."""
        assert {
            "mark_safe",
            "lazy-mark_safe",
            "text-form-marked-safe",
            "callable-marked-safe",
        } <= set(CLAIMS)


@pytest.mark.pr_tier
class TestHybridCellsAreReached:
    """The hybrid cells are accounted for the way ``TestEveryClassIsReached`` accounts for the
    ``_render_custom`` ones (#3900). Until that issue was fixed they were SET ASIDE: the sweep
    saw the cell reach the template path and did not judge it, so the three paths of
    ``Component.render`` were governed by two rules. A cell that is skipped must be stated."""

    @pytest.fixture(scope="class")
    def outcome(self) -> dict[tuple[str, str], dict[str, int]]:
        return reach(hybrid=True)

    def test_the_template_classes_are_derived_and_all_driven(self, outcome) -> None:
        from djust.components.base import Component

        derived = {
            key
            for key, cls in ALL_CLASSES.items()
            if issubclass(cls, Component) and cls.template is not None
        }
        assert set(TEMPLATE_CLASSES) == derived
        assert ONLY or len(derived) >= 15, sorted(derived)
        assert {key for key, _name in outcome} == derived

    def test_every_pair_is_drawn_by_some_cell_or_listed_with_its_reason(self, outcome) -> None:
        short = lambda key: key.replace("djust.components.", "")  # noqa: E731
        unreached = {
            f"{short(key)}.{name}"
            for (key, name), n in outcome.items()
            if not n["rendered"] and not _numeric_parameter(TEMPLATE_CLASSES[key], name)
        }
        listed = {
            p for p in UNREACHED if any(p.startswith(short(k) + ".") for k in TEMPLATE_CLASSES)
        }
        if ONLY:
            assert not [p for p in unreached if p not in UNREACHED], sorted(unreached)
            return
        assert unreached == listed, (
            f"pairs no hybrid cell drew: {sorted(unreached - listed)}\n"
            f"listed but now drawn: {sorted(listed - unreached)}"
        )

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_every_claim_kind_is_driven_and_reaches_the_output(self, claim: str) -> None:
        """The reach tests above count PARAMETERS. A kind of value that no cell draws, or that
        no parameter prints, would leave the sweep clean for it by default: this asks the
        question per claim kind, so a claim in ``CLAIMS`` that is not driven fails."""
        by_claim = reach(hybrid=True, claim=claim)
        assert any(n["rendered"] for n in by_claim.values()), claim
        echoed = {pair for pair, n in by_claim.items() if n["echoed"]}
        assert len(echoed) >= (1 if ONLY else 40), (claim, len(echoed))
        # ... and a URL slot in particular: the parameter a judged URL group reads is drawn with
        # a value of this kind and printed (a kind the judge cannot handle makes the render
        # raise, which the sweep reads as "the component rejects this value": it must not hide
        # behind the many text slots that do print)
        from djust.components import _hybrid_attrs

        url_pairs = {
            (key, name)
            for key, cls in TEMPLATE_CLASSES.items()
            for group in _hybrid_attrs.plan(cls.template).groups
            for _slot_key, name in group.slots
        }
        assert url_pairs or ONLY, "no shipped template has a judged URL slot any more"
        assert url_pairs <= echoed, sorted(url_pairs - echoed)
        # ... and a slot in the text of a raw-text element (#3914): its parameter is drawn with a
        # value of this kind and printed, for the same reason
        raw_pairs = raw_text_pairs()
        assert raw_pairs or ONLY, "no shipped template has a raw-text slot any more"
        assert raw_pairs <= echoed, sorted(raw_pairs - echoed)

    def test_every_template_class_prints_a_probe_through_some_parameter(self, outcome) -> None:
        silent = sorted(
            key
            for key in TEMPLATE_CLASSES
            if not any(n["echoed"] for (k, _name), n in outcome.items() if k == key)
        )
        assert not silent, silent

    def test_a_lazy_string_takes_the_hybrid_path_on_a_class_with_a_crate_counterpart(self) -> None:
        """The premise of the issue, measured: the crate constructor refuses a lazy string, so
        ``Avatar`` is drawn by its template. If the crate ever takes it (or the class stops being
        refused), this says the cells above no longer cover that route and must be re-derived."""
        from djust.components.ui.avatar_simple import Avatar

        if Avatar._rust_impl_class is None:
            pytest.skip("the Rust extension is not built")
        assert Avatar(src="/a.png")._rust_instance is not None
        assert Avatar(src=_lazy_safe("/a.png"))._rust_instance is None


# --- the tag parameters that take ITEMS and print them in an attribute (#3904) -----------------

#: ``library/tag/parameter`` -> ``(the attributes an item value lands in, whether an item of it
#: is also printed as TEXT)``. The decision for each parameter: an item value in an attribute is
#: escaped whatever it is marked as (``attr_text``; the template tags ``force_escape``); an item
#: printed as text keeps the markup of a marked-safe value. No parameter here writes a URL: the
#: one ``href`` is the in-page fragment ``#<id>`` of ``scroll_spy``. The attribute names are
#: MEASURED (a plain probe is searched for in the output), so a rename or a new slot fails.
ITEM_PARAMETERS: dict[str, tuple[tuple[str, ...], bool]] = {
    "djust_components/audit_log/columns": (("class",), False),
    "djust_components/audit_log/entries": (("class",), True),
    "djust_components/autocomplete/suggestions": (("data-value",), True),
    "djust_components/collab_selection/users": (("data-user",), True),
    "djust_components/combobox/options": (("data-value", "value"), True),
    "djust_components/combobox/selected": (("data-value", "value"), True),
    "djust_components/comparison_table/plans": (("data-value",), True),
    "djust_components/cursors/users": (("data-user",), True),
    "djust_components/dashboard_grid/panels": (("data-panel-id",), True),
    "djust_components/data_grid/columns": (("data-col-key", "data-type"), True),
    "djust_components/data_grid/rows": (("data-row-key", "data-value"), False),
    "djust_components/dependent_select/options": (("value",), True),
    "djust_components/dj_select/options": (("value",), True),
    "djust_components/export_dialog/columns": (("value",), True),
    "djust_components/export_dialog/formats": (("value",), False),
    "djust_components/fab/actions": (("aria-label", "dj-click"), True),
    "djust_components/filter_select/options": (("value",), True),
    "djust_components/form_array/rows": (("value",), False),
    "djust_components/file_tree/nodes": (("class", "data-name", "data-type"), True),
    "djust_components/gantt_chart/tasks": (("fill",), True),
    "djust_components/import_wizard/model_fields": (("name",), True),
    "djust_components/kanban_board/columns": (
        # ``href``: a card's link (#4120), written through ``safe_url``.
        ("class", "data-card-id", "data-col-id", "data-value", "dj-key", "href"),
        True,
    ),
    "djust_components/line_chart/series": (("fill", "stroke"), True),
    "djust_components/masonry_grid/items": (("class",), True),
    "djust_components/meter/segments": (("aria-label",), True),
    "djust_components/model_selector/options": (("data-value",), True),
    "djust_components/multi_select/options": (("data-value", "dj-value-option", "value"), True),
    "djust_components/notification_center/notifications": (("data-value",), True),
    "djust_components/pie_chart/segments": (("fill",), True),
    "djust_components/reactions/options": (("aria-label", "dj-value-emoji"), True),
    "djust_components/scroll_spy/sections": (("data-section", "data-sections", "href"), True),
    "djust_components/sortable_list/items": (("data-id",), True),
    "djust_components/split_button/options": (("dj-click",), True),
    "djust_components/table_of_contents/items": (("data-value", "href"), True),
    "djust_components/tag_input/suggestions": (("value",), False),
    "djust_components/tag_input/tags": (("data-value", "value"), True),
    "djust_components/toast_container/toasts": (("class", "data-value"), True),
    "djust_components/toggle_group/options": (("data-value",), True),
    "djust_components/tour/steps": (("data-target",), True),
    "djust_components/tree_view/nodes": (("data-value",), True),
    "djust_components/wizard/steps": (("data-value",), True),
    "theme_components/theme_radio/options": (("for", "id", "value"), True),
    "theme_components/theme_select/options": (("value",), True),
}  # fmt: skip


#: The plain string first (the control every other claim is compared with), then every way a
#: value says "do not escape me" (``CLAIMS``): a marked-safe string, its subclass, a lazy one, a
#: ``str`` with ``__html__``, an object whose text form is marked safe, and a callable that
#: returns one.
ITEM_CLAIMS: dict[str, object] = {"plain": str, **CLAIMS}
#: A callable is called by a template and printed by a tag as a callable, so only the first
#: reading is equal to the plain string; for the others the cell is "no hole", not "equal".
NOT_COMPARED_WITH_PLAIN = ENGINE_CLAIMS

#: ``library/tag/parameter`` -> the claims whose value the tag REJECTS outright (an exception, no
#: page): an object that is no string where the tag calls a string method on it or serialises the
#: item as JSON. A tag that fails closed is not a hole, but the cell then proves nothing, so the
#: set is pinned: a claim that starts or stops rendering is a decision to make here.
REJECTED_CLAIMS: dict[str, frozenset[str]] = {
    "djust_components/audit_log/columns": frozenset(
        {"text-form-marked-safe", "callable-marked-safe"}
    ),
    "djust_components/scroll_spy/sections": frozenset(
        {"lazy-mark_safe", "text-form-marked-safe", "callable-marked-safe"}
    ),
}

REACH_PROBE = "ZQITEMREACH"
#: None of the parameters writes a URL, so the payloads that differ only in how a script scheme is
#: spelled add nothing: a script URL, and a breakout of a double and of a single quote.
ITEM_PAYLOADS = tuple(p for p in PAYLOADS if p[0] in {"js", "breakout-dq", "breakout-sq"})


@lru_cache(maxsize=1)
def _tag_infos() -> dict:
    return {f"{i.lib}/{i.name}": i for i in url_sweep.inventory().values()}


def _slots(markup: str, names: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """``(element, attribute, value)`` of the attributes called ``names``, as a browser reads them."""
    return sorted(
        (a.element, a.name, a.value) for a in url_sweep.attributes(markup) if a.name in names
    )


class ReachedCase(NamedTuple):
    """A cell and the item key (``None``: the item is the text itself) whose probe landed in an
    attribute."""

    cell: ItemCell
    key: str | None
    #: the attribute holds the item's own text, not the ``repr`` of a dict or a pair the tag
    #: printed for a string it expected (an object claim is formatted differently there)
    direct: bool


@lru_cache(maxsize=None)
def _reached(key: str) -> tuple[tuple[ReachedCase, ...], frozenset[str], bool]:
    """``(cases, attributes, text)`` of ``library/tag/parameter``.

    ``cases`` are the (cell, item key) pairs in which a plain probe lands in an attribute (a
    shape, branch or companion that takes another path through the tag reaches other sites, so
    none is dropped as a duplicate); ``attributes`` are the names of those attributes; ``text`` is
    whether a marked-safe ``<i>`` item is printed with its markup as text."""
    lib, tag, name = key.split("/")
    info = _tag_infos()[f"{lib}/{tag}"]
    keys = _keys_of(info)
    benign_ctx = _item_context(BENIGN, keys, REACH_PROBE)
    benign_slots: dict[str, Counter] = {}
    marked_text: dict[str, bool] = {}
    cases: list[ReachedCase] = []
    attributes: set[str] = set()
    text = False
    for cell, item_key in item_probe_cases(info, name):
        ctx = _item_context(REACH_PROBE, keys, REACH_PROBE, key=item_key)
        out = _django_render(cell.src, ctx)
        slots = [a for a in url_sweep.attributes(out or "") if REACH_PROBE in a.value]
        found = Counter((a.element, a.name) for a in slots)
        if any("p_scalar" in arg for arg in cell.mode.extra):
            # the scalar that makes an item selected is the probe too: what it prints on its own
            # (the same call with benign items) is not the item's
            if cell.src not in benign_slots:
                ref = _django_render(cell.src, benign_ctx)
                benign_slots[cell.src] = Counter(
                    (a.element, a.name)
                    for a in url_sweep.attributes(ref or "")
                    if REACH_PROBE in a.value
                )
            found -= benign_slots[cell.src]
        if not found:
            continue
        landed = [a for a in slots if (a.element, a.name) in found]
        direct = all(not set("{('") & set(a.value.replace(REACH_PROBE, "")) for a in landed)
        attributes |= {a.name for a in landed}
        cases.append(ReachedCase(cell, item_key, direct))
        # The controls replace all item keys together, so the same source has
        # the same control input for every reached key.
        if cell.src not in marked_text:
            marked = _django_render(
                cell.src,
                _item_context(mark_safe("<i>mark</i>"), keys, "<i>mark</i>", key=EVERY_KEY),
            )
            marked_text[cell.src] = bool(marked and "<i>mark</i>" in marked)
        text = text or marked_text[cell.src]
    return tuple(cases), frozenset(attributes), text


@pytest.mark.pr_tier
class TestEveryTag:
    def test_the_item_leaks_of_the_tags_are_exactly_the_listed_ones(self) -> None:
        leaks = wide_tag_leaks()
        assert leaks == set(KNOWN_TAG_ITEM_LEAKS), (
            f"new leaks (escape the item value with attr_text): {sorted(leaks - KNOWN_TAG_ITEM_LEAKS)}\n"
            f"fixed (delete from KNOWN_TAG_ITEM_LEAKS): {sorted(set(KNOWN_TAG_ITEM_LEAKS) - leaks)}"
        )


class TestEveryItemParameter:
    """Each parameter above, one cell each: it is DRIVEN (an item lands in the attributes pinned
    for it), and every way of marking a value safe leaves those attributes exactly as the plain
    string does, with no quote breakout and no script URL."""

    @pytest.mark.pr_tier
    def test_the_table_lists_real_parameters(self) -> None:
        missing = [k for k in ITEM_PARAMETERS if "/".join(k.split("/")[:2]) not in _tag_infos()]
        assert not missing, missing

    @pytest.mark.pr_tier
    @pytest.mark.parametrize("key", sorted(ITEM_PARAMETERS))
    def test_the_parameter_is_driven_and_its_slots_are_the_pinned_ones(self, key: str) -> None:
        cases, attributes, text = _reached(key)
        pinned_attributes, pinned_text = ITEM_PARAMETERS[key]
        assert cases, f"{key}: no item reaches an attribute through any cell (not driven)"
        assert any(c.direct for c in cases), f"{key}: only a dict's repr reaches an attribute"
        assert attributes == set(pinned_attributes), (key, sorted(attributes))
        assert text == pinned_text, (key, text)

    @pytest.mark.sweep("marked-safe/item-parameters")
    @pytest.mark.parametrize("key", sorted(ITEM_PARAMETERS))
    def test_a_marked_safe_item_is_neutralised_like_the_plain_string(self, key: str) -> None:
        sweep_sample.skip_unless_sampled(
            "marked-safe/item-parameters",
            key,
            sorted(ITEM_PARAMETERS),
            budget=ITEM_PARAMETER_BUDGET,
            paths=_tag_paths,
            shared=SHARED_PATHS,
        )
        cases, _attributes, _text = _reached(key)
        assert cases, f"{key}: not driven"
        slots = ITEM_PARAMETERS[key][0]
        keys = _keys_of(_tag_infos()["/".join(key.split("/")[:2])])
        failures: list[tuple] = []
        rendered: dict[str, int] = {claim: 0 for claim in ITEM_CLAIMS}
        for label, payload in ITEM_PAYLOADS:
            controls: dict[tuple, list] = {}
            for claim, make in ITEM_CLAIMS.items():
                for case in cases:
                    ctx = _item_context(make(payload), keys, payload, key=case.key)  # type: ignore[operator]
                    out = _django_render(case.cell.src, ctx)
                    if out is None:
                        continue
                    rendered[claim] += 1
                    where = (label, claim, case.cell.shape, case.key, case.cell.mode.label)
                    if holes_in(out):
                        failures.append((*where, "hole"))
                    if claim == "plain":
                        controls[(case.cell, case.key)] = _slots(out, slots)
                    elif (
                        case.direct
                        and claim not in NOT_COMPARED_WITH_PLAIN
                        and controls[(case.cell, case.key)] != _slots(out, slots)
                    ):
                        failures.append((*where, "differs from plain"))
        assert not failures, f"{key}: {failures[:4]}"
        never = frozenset(claim for claim, n in rendered.items() if n == 0)
        assert never == REJECTED_CLAIMS.get(key, frozenset()), (key, sorted(never))


# --- the derivation can fail: a planted leak of each kind it has missed must be reported -------

#: A tag library the test builds. Every ``canary_leaks_*`` tag writes an item into a quoted
#: attribute through ``conditional_escape`` and must be reported; every ``canary_clean_*`` tag
#: does the same through ``attr_text`` and must not be. The leaking ones are the shapes the
#: first derivation could not see: an item whose other key does arithmetic, an attribute
#: written only when a scalar is set (``event=``) or has one value (``step="map"``), a child tag
#: drawn only by its parent, and a block tag whose default rows are drawn only for an empty body.
CANARY_SOURCE = """
from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

from djust.components.templatetags._registry import _parse_kv_args, _resolve
from djust.components.utils import attr_text

register = template.Library()


@register.simple_tag
def canary_leaks_text(items=None):
    return mark_safe("".join(f'<i data-x="{conditional_escape(str(i))}">' for i in items or []))


@register.simple_tag
def canary_clean_text(items=None):
    return mark_safe("".join(f'<i data-x="{attr_text(i)}">' for i in items or []))


@register.simple_tag
def canary_leaks_numeric(items=None):
    out = ""
    for i in items or []:
        weight = float(i.get("weight", 0))
        out += f'<i data-w="{weight}" data-id="{conditional_escape(str(i.get("ident", "")))}">'
    return mark_safe(out)


@register.simple_tag
def canary_clean_numeric(items=None):
    out = ""
    for i in items or []:
        weight = float(i.get("weight", 0))
        out += f'<i data-w="{weight}" data-id="{attr_text(i.get("ident", ""))}">'
    return mark_safe(out)


@register.simple_tag
def canary_leaks_event(items=None, event=""):
    if not event:
        return ""
    return mark_safe("".join(f'<i data-e="{conditional_escape(str(i))}">' for i in items or []))


@register.simple_tag
def canary_clean_event(items=None, event=""):
    if not event:
        return ""
    return mark_safe("".join(f'<i data-e="{attr_text(i)}">' for i in items or []))


@register.simple_tag
def canary_leaks_step(items=None, step=""):
    if step == "map":
        return mark_safe("".join(f'<i data-s="{conditional_escape(str(i))}">' for i in items or []))
    return ""


@register.simple_tag
def canary_clean_step(items=None, step=""):
    if step == "map":
        return mark_safe("".join(f'<i data-s="{attr_text(i)}">' for i in items or []))
    return ""


class _Child(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        return ""


class _Parent(template.Node):
    def __init__(self, nodelist, clean):
        self.nodelist = nodelist
        self.clean = clean

    def render(self, context):
        out = ""
        for node in self.nodelist:
            if isinstance(node, _Child):
                options = _resolve(node.kwargs.get("options"), context) or []
                for o in options:
                    value = attr_text(o) if self.clean else conditional_escape(str(o))
                    out += f'<option value="{value}">'
        return mark_safe(out)


def _parent(clean):
    def compile_(parser, token):
        nodelist = parser.parse(("endcanary_" + ("clean" if clean else "leaks") + "_parent",))
        parser.delete_first_token()
        return _Parent(nodelist, clean)

    return compile_


register.tag("canary_leaks_parent", _parent(False))
register.tag("canary_clean_parent", _parent(True))


@register.tag("canary_child")
def canary_child(parser, token):
    return _Child(_parse_kv_args(token.split_contents()[1:], parser))


class _Block(template.Node):
    def __init__(self, nodelist, kwargs, clean):
        self.nodelist, self.kwargs, self.clean = nodelist, kwargs, clean

    def render(self, context):
        rows = _resolve(self.kwargs.get("rows"), context) or []
        if any(n.render(context).strip() for n in self.nodelist):
            return mark_safe(self.nodelist.render(context))
        out = ""
        for r in rows:
            value = attr_text(r) if self.clean else conditional_escape(str(r))
            out += f'<input value="{value}">'
        return mark_safe(out)


def _block(clean):
    def compile_(parser, token):
        kwargs = _parse_kv_args(token.split_contents()[1:], parser)
        end = "endcanary_" + ("clean" if clean else "leaks") + "_block"
        nodelist = parser.parse((end,))
        parser.delete_first_token()
        return _Block(nodelist, kwargs, clean)

    return compile_


register.tag("canary_leaks_block", _block(False))
register.tag("canary_clean_block", _block(True))
"""

CANARY_LIBRARY = "djust_item_canary_tags"
#: ``tag/parameter`` of the planted leaks: exactly these, and not the clean twins.
CANARY_LEAKS = frozenset(
    {
        f"{CANARY_LIBRARY}/canary_leaks_text/items",
        f"{CANARY_LIBRARY}/canary_leaks_numeric/items",
        f"{CANARY_LIBRARY}/canary_leaks_event/items",
        f"{CANARY_LIBRARY}/canary_leaks_step/items",
        f"{CANARY_LIBRARY}/canary_child/options",
        f"{CANARY_LIBRARY}/canary_leaks_block/rows",
    }
)


@pytest.fixture
def canary_library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Register the canary tags with the sweep and with Django's engine, and undo it afterwards.
    The module is a real file (the harness reads tag sources from files)."""
    import importlib.util

    from django.template import engines

    from scripts.lib.template_library_parity import variants

    path = tmp_path / f"{CANARY_LIBRARY}.py"
    path.write_text(CANARY_SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(CANARY_LIBRARY, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, CANARY_LIBRARY, module)
    spec.loader.exec_module(module)
    monkeypatch.setitem(url_sweep.SWEPT_LIBRARIES, CANARY_LIBRARY, CANARY_LIBRARY)
    monkeypatch.setitem(variants.PARENT, "canary_child", "canary_leaks_parent")
    monkeypatch.setitem(
        engines["django"].engine.template_libraries,  # type: ignore[attr-defined]
        CANARY_LIBRARY,
        module.register,
    )
    _tag_infos.cache_clear()
    _source_facts.cache_clear()
    yield
    _tag_infos.cache_clear()
    _source_facts.cache_clear()


@pytest.mark.pr_tier
class TestTheDerivationCanFail:
    """``TestEveryTag`` asserts that the derivation finds NOTHING in the package, which a
    derivation that finds nothing also satisfies. These plant a leak of each kind the first
    derivation missed (and a clean twin of each) in a library built here, and require the
    derivation to report exactly the planted ones."""

    def test_it_reports_exactly_the_planted_leaks(self, canary_library) -> None:
        leaks = wide_tag_leaks(frozenset({CANARY_LIBRARY}))
        assert leaks == set(CANARY_LEAKS), (
            f"missed: {sorted(CANARY_LEAKS - leaks)}; reported as leaking though clean: "
            f"{sorted(leaks - CANARY_LEAKS)}"
        )

    def test_it_reports_nothing_when_its_detection_is_switched_off(
        self, canary_library, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The instrument is what the previous test measures: with the detection removed the
        planted leaks are not reported, so the previous test cannot pass for a blind one."""
        monkeypatch.setitem(globals(), "holes_in", lambda markup: [])
        assert wide_tag_leaks(frozenset({CANARY_LIBRARY})) == set()


# --- a static hunt for the same sink, found without enumeration ---------------------------------

#: Names a tag reads its own scalar keywords from: an item is not read from these.
SCALAR_BASES = frozenset({"kw", "kwargs", "nkw", "attrs", "context", "self", "request", "extra"})
ESCAPERS = frozenset({"conditional_escape", "escape"})
#: Functions whose result is a validated value (a colour, a length, a number), not the item.
VALIDATORS = frozenset({"safe_css_color", "safe_css_length", "int", "float", "len"})


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        func = node.func
        return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return ""


_PERCENT = re.compile(r"%(?:\([^)]*\))?[#0\- +]*\d*(?:\.\d+)?[sdifrxXeEgGc%]")
_BRACE = re.compile(r"\{\{|\}\}|\{([^{}!:]*)(?:![rsa])?(?::[^{}]*)?\}")


def _pieces(node: ast.expr) -> list[str | ast.expr]:
    """A string-building expression as the text and the expressions it is made of, in order:
    an f-string, ``"a" + x + "b"``, ``"a%sb" % x``, ``"a%(k)sb" % {"k": x}``, ``"a{}b".format(x)``
    and ``"".join(["a", x, "b"])`` (#3935)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out: list[str | ast.expr] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                out.append(part.value)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _pieces(node.left) + _pieces(node.right)
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mod)
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
    ):
        right = node.right
        args = list(right.elts) if isinstance(right, ast.Tuple) else [right]
        # a mapping: ``%(name)s`` reads its key (#3935)
        mapping = (
            {
                k.value: v
                for k, v in zip(right.keys, right.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if isinstance(right, ast.Dict)
            else None
        )
        out, taken, last = [], 0, 0
        text = node.left.value
        for m in _PERCENT.finditer(text):
            out.append(text[last : m.start()])
            key = re.match(r"%\(([^)]*)\)", m.group(0))
            if m.group(0) == "%%":
                out.append("%")
            elif key is not None:
                if mapping is not None and key.group(1) in mapping:
                    out.append(mapping[key.group(1)])
                elif mapping is None:
                    out.append(right)  # a mapping built elsewhere: what it holds is not known
            elif taken < len(args):
                out.append(args[taken])
                taken += 1
            last = m.end()
        out.append(text[last:])
        return out
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    ):
        text = node.func.value.value
        keywords = {k.arg: k.value for k in node.keywords if k.arg}
        out, taken, last = [], 0, 0
        for m in _BRACE.finditer(text):
            out.append(text[last : m.start()])
            field = m.group(1)
            if field is None:
                out.append("{" if m.group(0) == "{{" else "}")
            else:
                if field == "":
                    index, taken = taken, taken + 1
                    arg = node.args[index] if index < len(node.args) else None
                elif field.isdigit():
                    arg = node.args[int(field)] if int(field) < len(node.args) else None
                else:
                    arg = keywords.get(field.split(".")[0].split("[")[0])
                if arg is not None:
                    out.append(arg)
            last = m.end()
        out.append(text[last:])
        return out
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
        and len(node.args) == 1
        and isinstance(node.args[0], (ast.List, ast.Tuple))
    ):
        # ``sep.join([piece, piece, ...])`` (#3935); a join over a generator is a loop, and the
        # string each round builds is found as its own builder
        sep = node.func.value.value
        out = []
        for i, element in enumerate(node.args[0].elts):
            if i:
                out.append(sep)
            out.extend(_pieces(element))
        return out
    return [node]


def _assembled(func: ast.AST) -> list[list[str | ast.expr]]:
    """The strings a function assembles over several statements (#3935): ``s = "a"`` then
    ``s += x`` ... ``s += "b"``, as one list of pieces per name, in source order. A new plain
    assignment starts the name over; branches and loops are read in source order (a hunt may
    over-report, never under-report, and every finding is judged)."""
    built: dict[str, list[str | ast.expr]] = {}
    done: list[list[str | ast.expr]] = []

    def visit(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                name = stmt.targets[0].id
                if name in built:
                    done.append(built.pop(name))
                built[name] = list(_pieces(stmt.value))
            elif (
                isinstance(stmt, ast.AugAssign)
                and isinstance(stmt.op, ast.Add)
                and isinstance(stmt.target, ast.Name)
                and stmt.target.id in built
            ):
                built[stmt.target.id].extend(_pieces(stmt.value))
            for field in ("body", "orelse", "finalbody", "handlers"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.AST):
                    visit([x for h in inner for x in h.body] if field == "handlers" else inner)

    visit(getattr(func, "body", []))
    return done + list(built.values())


def _quoted_attribute_values(node: ast.expr) -> list[ast.expr]:
    """The expressions of a string-building expression that land inside a quoted attribute value
    (``="{x}"``, ``="' + x``, ``="%s"``)."""
    return _quoted_in(_pieces(node))


def _quoted_in(pieces: list[str | ast.expr]) -> list[ast.expr]:
    """The expressions among ``pieces`` (text and expressions, in order) inside a quoted
    attribute value."""
    quote: str | None = None
    last = ""
    found: list[ast.expr] = []
    for part in pieces:
        if isinstance(part, str):
            for ch in part:
                if quote is None:
                    if last == "=" and ch in "\"'":
                        quote = ch
                elif ch == quote:
                    quote = None
                last = ch
        else:
            if quote is not None:
                found.append(part)
            last = "}"
    return found


def static_sink_hunt(source: str, *, lines: bool = False) -> list[str]:
    """``function: expression`` of every ``conditional_escape(...)`` / ``escape(...)`` of an ITEM
    that lands in a quoted attribute of an f-string, in one module's source.

    An item is a value read with ``.get("k")`` or ``["k"]`` from anything but the tag's own
    keyword dict, or a name bound by a ``for`` over something that is not a literal (a helper
    that is handed an item and escapes it is found the same way: the loop variable). Escaped
    through ``attr_text`` is not a finding. It follows an attribute value written through a local
    name one step (``v = conditional_escape(item.get("k"))`` ... ``f'value="{v}"'``)."""
    findings: list[str] = []
    tree = ast.parse(source)
    for func in [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        loop_names: set[str] = set()
        for node in ast.walk(func):
            targets = []
            if isinstance(node, ast.For):
                targets, iterable = [node.target], node.iter
            elif isinstance(node, ast.comprehension):
                targets, iterable = [node.target], node.iter
            else:
                continue
            if isinstance(iterable, (ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set)):
                continue
            if _call_name(iterable) == "range":
                continue
            skip_first = _call_name(iterable) == "enumerate"
            for target in targets:
                names = [n for n in ast.walk(target) if isinstance(n, ast.Name)]
                loop_names |= {
                    n.id for n in (names[1:] if skip_first else names) if n.id not in SCALAR_BASES
                }
        assigns: dict[str, list[ast.Assign]] = {}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                if isinstance(node.targets[0], ast.Name):
                    assigns.setdefault(node.targets[0].id, []).append(node)

        def bound_before(name: str, line: int) -> list[ast.expr]:
            """The value ``name`` holds at ``line``: its nearest assignment above it."""
            before = [a for a in assigns.get(name, []) if a.lineno <= line]
            nearest = max(before, key=lambda a: a.lineno, default=None)
            return [nearest.value] if nearest is not None else []

        def reads_item(expr: ast.AST) -> bool:
            if isinstance(expr, ast.JoinedStr) or _call_name(expr) in VALIDATORS:
                return False  # an f-string is an exact str; a validator returns its own value
            for n in ast.walk(expr):
                base = None
                if (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get"
                ):
                    base = n.func.value
                elif isinstance(n, ast.Subscript):
                    base = n.value
                if isinstance(base, ast.Name) and base.id not in SCALAR_BASES:
                    return True
                if isinstance(n, ast.Name) and n.id in loop_names:
                    return True
            return False

        builders = [
            n
            for n in ast.walk(func)
            if isinstance(n, (ast.JoinedStr, ast.BinOp))
            or (isinstance(n, ast.Call) and _call_name(n) in ("format", "join"))
        ]
        quoted = [expr for built in builders for expr in _quoted_attribute_values(built)]
        quoted += [expr for pieces in _assembled(func) for expr in _quoted_in(pieces)]
        for expr in quoted:
            candidates = (
                bound_before(expr.id, expr.lineno) if isinstance(expr, ast.Name) else [expr]
            )
            for candidate in candidates:
                if _call_name(candidate) in ESCAPERS and candidate.args:
                    if reads_item(candidate.args[0]):
                        at = f" (line {candidate.lineno})" if lines else ""
                        findings.append(f"{func.name}: {ast.unparse(candidate)[:90]}{at}")
    return sorted(set(findings))


#: Module paths (under ``python/djust``) the hunt reads: every tag module of both tag packages.
HUNTED_SOURCES = (
    "components/templatetags",
    "theming/templatetags",
)

CONST = "iterates a constant table written in this module, not an item handed to the tag"
PLAIN = "a plain str built in the function (a split of the value, a regex over the template text)"
CHILD_SCALAR = "the id= keyword of a child tag: a scalar argument, the developer's own"
#: ``module:function: expression`` the hunt reports and a person has judged not to be an item
#: written to an attribute, with the reason. Exactly these: a new finding fails, and so does an
#: entry the hunt no longer reports.
STATIC_SINK_ALLOWED: dict[str, str] = {
    "components/templatetags/_advanced.py:render: conditional_escape(label)": CONST,
    "components/templatetags/_advanced.py:render: conditional_escape(v)": PLAIN,
    "components/templatetags/_advanced.py:render: conditional_escape(val)": PLAIN,
    "components/templatetags/_charts.py:render: conditional_escape(str(self.COLORS[idx % len(self.COLORS)]))": CONST,
    "components/templatetags/_dev_tools.py:render: conditional_escape(prefix)": CONST,
    "components/templatetags/_dev_tools.py:render: conditional_escape(suffix)": CONST,
    "components/templatetags/djust_components.py:_render_emoji: conditional_escape(val)": CONST,
    "components/templatetags/djust_components.py:render: conditional_escape(tid)": CHILD_SCALAR,
    "components/templatetags/djust_components.py:rich_text_editor: conditional_escape(arg)": CONST,
    "components/templatetags/djust_components.py:rich_text_editor: conditional_escape(cmd)": CONST,
}


def _hunted() -> dict[str, list[str]]:
    root = ROOT / "python" / "djust"
    found: dict[str, list[str]] = {}
    for sub in HUNTED_SOURCES:
        for path in sorted((root / sub).glob("*.py")):
            for finding in static_sink_hunt(path.read_text(encoding="utf-8")):
                found.setdefault(f"{sub}/{path.name}", []).append(finding)
    return found


PLANTED_SINK = """
def leaks_through_a_helper(items):
    out = ""
    for item in items:
        value = conditional_escape(str(item.get("value", "")))
        out += f'<option value="{value}">'
    return out


def leaks_a_loop_variable(choices):
    return "".join(f'<input value="{conditional_escape(v)}">' for v, label in choices)
"""
PLANTED_BUILDERS = """
def concatenated(items):
    return "".join('<option value="' + conditional_escape(str(i.get("v", ""))) + '">' for i in items)


def percent(items):
    return "".join('<option value="%s">' % conditional_escape(str(i.get("v", ""))) for i in items)


def formatted(items):
    return "".join('<option value="{}">'.format(conditional_escape(str(i.get("v", "")))) for i in items)


def formatted_by_name(items):
    return "".join('<option value="{v}">'.format(v=conditional_escape(str(i.get("v", "")))) for i in items)


def percent_mapping(items):
    return "".join('<option value="%(v)s">' % {"v": conditional_escape(str(i.get("v", "")))} for i in items)


def joined_pieces(items):
    return "".join("".join(['<option value="', conditional_escape(str(i.get("v", ""))), '">']) for i in items)


def assembled(items):
    out = ""
    for i in items:
        out += '<option value="'
        out += conditional_escape(str(i.get("v", "")))
        out += '">'
    return out


def assembled_through_a_name(items):
    for i in items:
        v = conditional_escape(str(i.get("v", "")))
        html = '<option title="x" value="'
        html += v
        html += '">'
    return html
"""
CLEAN_BUILDERS = """
def concatenated(items, kw):
    out = "".join('<option value="' + attr_text(i.get("v", "")) + '">' for i in items)
    return out + '<p title="' + conditional_escape(kw.get("t", "")) + '">'


def percent_and_format(items):
    out = "".join('<option value="%s">' % attr_text(i.get("v", "")) for i in items)
    return out + "".join('<i title="{}">'.format(attr_text(i.get("t", ""))) for i in items)


def mapping_join_and_assembly(items, kw):
    out = "".join('<option value="%(v)s">' % {"v": attr_text(i.get("v", ""))} for i in items)
    out += "".join(['<i title="', attr_text(kw.get("t", "")), '">'])
    for i in items:
        out += '<b title="x">'
        out += conditional_escape(str(i.get("label", "")))  # text, not an attribute
        out += "</b>"
    return out
"""
CLEAN_SINK = """
def clean(items, kw):
    out = ""
    for item in items:
        value = attr_text(item.get("value", ""))
        label = conditional_escape(str(item.get("label", "")))
        out += f'<option value="{value}">{label}</option>'
    title = conditional_escape(kw.get("title", ""))
    return out + f'<p title="{title}">'
"""


class TestTheStaticHunt:
    def test_it_finds_a_planted_sink_and_not_its_clean_twin(self) -> None:
        planted = static_sink_hunt(PLANTED_SINK)
        assert len(planted) == 2, planted
        assert static_sink_hunt(CLEAN_SINK) == []

    def test_it_finds_a_string_built_by_every_builder_it_knows(self) -> None:
        """Concatenation, ``%`` (a tuple and a mapping), ``.format``, ``"".join([...])`` and a
        string assembled over several statements (#3935). Every function of the planted source is
        one builder with one leak: the expected set is read from it, not restated."""
        planted = static_sink_hunt(PLANTED_BUILDERS)
        defined = sorted(
            n.name for n in ast.walk(ast.parse(PLANTED_BUILDERS)) if isinstance(n, ast.FunctionDef)
        )
        assert sorted({f.split(":")[0] for f in planted}) == defined, planted
        assert static_sink_hunt(CLEAN_BUILDERS) == []

    def test_the_tag_modules_have_exactly_the_judged_findings(self) -> None:
        found = {f"{mod}:{f}": None for mod, fs in _hunted().items() for f in fs}
        assert set(found) == set(STATIC_SINK_ALLOWED), (
            f"new: {sorted(set(found) - set(STATIC_SINK_ALLOWED))}\n"
            f"stale: {sorted(set(STATIC_SINK_ALLOWED) - set(found))}"
        )


# --- a form field's choice value (a schema, not a tag list), through ``dj_form`` ----------------

CHOICE_WIDGETS = ("select", "radio", "checkbox")


def _choice_form(widget: str, value: object):
    """A form with one choice field whose only choice has ``value``."""
    from django import forms

    field = forms.MultipleChoiceField if widget == "checkbox" else forms.ChoiceField
    widget_class = {
        "select": forms.Select,
        "radio": forms.RadioSelect,
        "checkbox": forms.CheckboxSelectMultiple,
    }[widget]

    class ChoiceForm(forms.Form):
        c = field(choices=[(value, "label")], widget=widget_class)

    return ChoiceForm()


ENGINES = ("django", "native")


def _form_render(engine: str, widget: str, value: object) -> str | None:
    """``{% dj_form %}`` of a one-choice form through Django's engine or the native one. ``None``
    is a render that raised: a value the field does not accept fails closed."""
    src = "{% load djust_components %}{% dj_form form=f %}"
    ctx = {"f": _choice_form(widget, value)}
    if engine == "django":
        return _django_render(src, ctx)
    from djust.tests.test_components_csp_aria import _rust

    try:
        return _rust(src, ctx)
    except Exception:  # noqa: BLE001 - a call the engine rejects fails closed
        return None


class TestFormFieldChoices:
    """``dj_form`` draws a field's choices into ``value=``: the choice value is the form's schema,
    not a list handed to a tag, and is treated the same way (#3904 review). Both engines draw the
    field: the native engine hands the form to the same Python tag, so the marker reaches it."""

    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize("widget", CHOICE_WIDGETS)
    @pytest.mark.parametrize("claim", sorted(set(ITEM_CLAIMS) - {"plain"}))
    def test_a_marked_safe_choice_value_is_neutralised_like_the_plain_string(
        self, engine: str, widget: str, claim: str
    ) -> None:
        for _label, payload in ITEM_PAYLOADS:
            plain = _form_render(engine, widget, payload)
            marked = _form_render(engine, widget, ITEM_CLAIMS[claim](payload))  # type: ignore[operator]
            assert plain is not None, "the plain choice did not render"
            if marked is None:
                continue  # a value no field accepts fails closed
            assert holes_in(marked) == [], (engine, widget, claim)
            if claim not in NOT_COMPARED_WITH_PLAIN:
                slots = ("value", "id", "for")
                assert _slots(marked, slots) == _slots(plain, slots), (engine, widget, claim)

    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize("widget", CHOICE_WIDGETS)
    def test_the_choice_is_drawn_into_the_pinned_attribute(self, engine: str, widget: str) -> None:
        """The cell is driven in each engine: the probe is found in a ``value=`` (an engine that
        draws no field would fail this instead of passing for nothing)."""
        out = _form_render(engine, widget, REACH_PROBE)
        assert out is not None
        names = {a.name for a in url_sweep.attributes(out) if REACH_PROBE in a.value}
        assert "value" in names, (engine, widget, sorted(names))


#: The sweeps' full populations, for ``tests/test_sweep_tiers_4074.py`` (#4074).
SWEEP_POPULATIONS = {
    "marked-safe/classes": lambda: sorted(ALL_CLASSES),
    "marked-safe/tags": lambda: list(TAGS),
    "marked-safe/item-parameters": lambda: sorted(ITEM_PARAMETERS),
}
