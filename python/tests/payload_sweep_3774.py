"""The payload sweep behind ``test_safe_markup_audit_3774.py`` (#3774).

Renders the component tags (and the other audited ``mark_safe`` sites it can drive) with a hostile
value in the arguments they read, PARSES the output, and records which source lines ran, so the test
can say which audited sinks the sweep did and did not execute. A sweep that never reached a sink
says nothing about it (the first version of this one reached 74 of 96 and the claim was worded as if
it reached all of them).

Two payload modes:

* ``markup``: ``ZQ"onmouseover=ZQ<b id=ZQ>'ZQ</script></textarea></title><svg onload=ZQ>`` in every
  argument. Fails when
  it became an event-handler attribute or an element.
* ``url``: the same, except an argument that looks like a URL (``url``, ``href``, ``src``, ``avatar``,
  ``image``, ...) gets ``javascript:ZQ(1)``. Fails when a navigation or load attribute carries the
  script scheme (or ``data:text/html``), which the markup payload cannot see.

The values are built from the SHAPE each argument takes: a keyword the tags ``int()`` is a number, a
function parameter whose default is a number is a number, a flag is a bool, so the code after the
coercion runs (``int(PAYLOAD)`` raising is why most sinks used to be unreachable).
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import re
import sys
from functools import lru_cache
from html.parser import HTMLParser

from django.template import Context, Node, NodeList
from django.utils.html import conditional_escape

TAG = "ZQ"
#: ``</textarea></title>`` precedes the element for a reason (#3827): the contents of ``<textarea>``
#: and ``<title>`` are RCDATA, text the parser never turns into elements, so a raw value inside one
#: only becomes an element if it first closes it (exactly what a browser does with it). Without the
#: closers a raw ``{value}</textarea>`` or SVG ``<title>{name}`` interpolation was invisible here.
RCDATA_CLOSERS = "</textarea></title>"
PAYLOAD = (
    TAG
    + '"onmouseover='
    + TAG
    + "<b id="
    + TAG
    + ">'"
    + TAG
    + "</script>"
    + RCDATA_CLOSERS
    + "<svg onload="
    + TAG
    + ">"
)
URL_PAYLOAD = "javascript:" + TAG + "(1)"
MODES = ("markup", "url")
TAG_MODULES = (
    "djust.components.templatetags.djust_components",
    "djust.components.templatetags._forms",
    "djust.components.templatetags._advanced",
    "djust.components.templatetags._charts",
    "djust.components.templatetags._dev_tools",
    "djust.templatetags.djust_pwa",
    "djust.templatetags.live_tags",
)
SHAPES = ("str", "list_str", "list_dict", "dict", "numbers", "matrix", "records")
#: Parameters that are, by contract, already-rendered (escaped) markup rather than data.
RENDERED_PARAMS = frozenset({"content", "body", "children"})
URLISH = re.compile(
    r"(^|_)(url|urls|href|src|srcset|image|img|avatar|thumbnail|link|action|uri|poster|icon_url)($|_)"
)
_URL_ATTRS = frozenset(
    {"href", "src", "action", "formaction", "xlink:href", "poster", "data", "ping"}
)


class _Injection(HTMLParser):
    """Records what the payload turned into: a handler attribute, an element, or a script URL."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("onmouseover", "onload"):
                self.hits.append(f"<{tag} {name}=...>")
            if name in _URL_ATTRS and value:
                folded = re.sub(r"[\s\x00-\x20]+", "", value).lower()
                if folded.startswith(("javascript:", "vbscript:", "data:text/html")):
                    self.hits.append(f"<{tag} {name}={folded[:24]}...>")
            if name == "style" and value and TAG in value:
                # Escaping keeps a style value inside its attribute; only a validator
                # (safe_css_*, a size regex) keeps the payload OUT of the CSS.
                self.hits.append(f"<{tag} style carries the payload>")
        if tag == "b" and dict(attrs).get("id") == TAG:
            self.hits.append("<b id=ZQ>")

    handle_startendtag = handle_starttag


#: What an UNESCAPED payload looks like as raw text. Escaped, each starts with ``&quot;`` / ``&lt;``
#: / ``&#x27;`` instead, so output without one of these cannot hold an injection and is not parsed
#: (parsing every output is the slow part of the sweep).
_RAW = (
    '"onmouseover=' + TAG.lower(),
    "<b id=" + TAG.lower(),
    "<svg onload=" + TAG.lower(),
    "'" + TAG.lower() + "<",
    "script:",
    "data:text",
)


def injections(markup) -> list[str]:
    text = str(markup)
    lowered = text.lower()
    if not any(raw in lowered for raw in _RAW):
        return []
    parser = _Injection()
    parser.feed(text)
    parser.close()
    return parser.hits


@lru_cache(maxsize=None)
def keys() -> tuple[set[str], set[str]]:
    """Every kwarg name the tags read, and the ones they ``int()`` straight away."""
    found: set[str] = set()
    numeric: set[str] = set()
    for name in TAG_MODULES:
        src = inspect.getsource(importlib.import_module(name))
        found |= set(re.findall(r'kw\.get\(\s*"(\w+)"', src))
        found |= set(re.findall(r'nkw\.get\(\s*"(\w+)"', src))
        numeric |= set(re.findall(r'int\(\s*kw\.get\(\s*"(\w+)"', src))
    return found, numeric


def _text_for(key: str, mode: str) -> str:
    return URL_PAYLOAD if mode == "url" and URLISH.search(key) else PAYLOAD


def kwargs_for(shape: str, mode: str, all_keys: set[str], numeric: set[str]) -> dict:
    """The resolved kwargs of a tag: the payload in every key, as ``shape`` says."""
    if shape == "str":
        make = lambda k: _text_for(k, mode)  # noqa: E731
    elif shape == "list_str":
        make = lambda k: [_text_for(k, mode)] * 2  # noqa: E731
    else:
        row = {k: (3 if k in numeric else _text_for(k, mode)) for k in all_keys}
        make = {  # noqa: E731
            "list_dict": lambda k: [dict(row), dict(row, children=[dict(row)]), *_quiet(row)],
            "dict": lambda k: dict(row),
            "numbers": lambda k: _text_for(k, mode),
            "matrix": lambda k: _text_for(k, mode),
            "records": lambda k: _text_for(k, mode),
        }[shape]
    kw = {k: (3 if k in numeric else make(k)) for k in all_keys}
    text = _text_for("x", mode)
    if shape == "numbers":
        kw.update(
            data=[1, 2, 3],
            labels=[text, text, text],
            series=[{"name": text, "data": [1, 2, 3], "color": text}],
            segments=[{"label": text, "value": 3, "color": text, "name": text}],
            items=[{"label": text, "value": 3, "id": text, "name": text, "size": 3}],
            events=[{"user": text, "action": text, "date": "2026-01-02", "title": text}],
            tasks=[{"name": text, "start": 1, "duration": 2, "color": text, "progress": 0.5}],
            values=[3, 4],
            max=3,
            year=2026,
            month=1,
        )
    elif shape == "matrix":
        kw.update(
            data=[[1, 2], [3, 4]],
            x_labels=[text, text],
            y_labels=[text, text],
            labels=[text, text],
            color_min="#f0f9ff",  # interpolate_color parses a hex pair: text would raise before the sink
            color_max="#1e40af",
        )
    elif shape == "records":
        kw.update(
            data=[{text: text}],
            rows=text,
            cols=text,
            values=text,
            columns=[{"key": text, "label": text}],
        )
    return kw


def render_node(cls, shape: str, mode: str, all_keys: set[str], numeric: set[str]):
    """Instantiate a tag ``Node`` with the payload, render it; ``None`` if it cannot."""
    kw = kwargs_for(shape, mode, all_keys, numeric)
    args = []
    for name in (p for p in inspect.signature(cls.__init__).parameters if p != "self"):
        args.append(kw if name == "kwargs" else NodeList() if name == "nodelist" else PAYLOAD)
    try:
        return cls(*args).render(Context({}))
    except Exception:  # noqa: BLE001 - a tag that rejects the payload is not rendering it
        return None


#: A parameter that defaults to ``None`` but is coerced with ``int()`` (slider's ``value``).
_NUMERIC_NAME = re.compile(
    r"^(value|value_end|min|max|min_val|max_val|step|total|page|page_size|count|rows|cols|digits|"
    r"limit|level|active|index|duration|decimals|width|height|size|zoom|progress|unread_count)$"
)


def _records(mode: str) -> list:
    """What a list-taking parameter (``items=``, ``options=``, ``nodes=`` ...) is given: dicts
    carrying the payload under every key the tags read, one of them nested."""
    all_keys, numeric = keys()
    row = {k: (3 if k in numeric else _text_for(k, mode)) for k in all_keys}
    return [dict(row, children=[dict(row)]), dict(row), *_quiet(row)]


@lru_cache(maxsize=None)
def flag_keys() -> frozenset[str]:
    """Keys the tags read as a flag: ``.get("x", False)`` plus the usual state names."""
    found: set[str] = {"active", "disabled", "danger", "divider", "selected", "open", "checked"}
    for name in TAG_MODULES:
        source = inspect.getsource(importlib.import_module(name))
        found |= set(re.findall(r'\.get\(\s*"(\w+)"\s*,\s*False\s*\)', source))
    return frozenset(found)


def _quiet(row: dict) -> list[dict]:
    """``row`` with every flag false. A flag holding the payload is truthy and switches the branch
    that matters off (breadcrumb's ``active`` skips the link, ``disabled`` skips the href), so the
    URL code behind it only runs for a row like this one."""
    quiet = {k: (False if k in flag_keys() else v) for k, v in row.items()}
    return [dict(quiet, children=[dict(quiet)])]


def _param_value(name: str, p: inspect.Parameter, variant: str, mode: str):
    default = p.default
    if name in RENDERED_PARAMS:
        return conditional_escape(PAYLOAD)
    if variant == "records" and default is None and not _NUMERIC_NAME.search(name):
        return _records(mode)
    if variant == "defaults" and default is not inspect.Parameter.empty and default is not None:
        return default
    if isinstance(default, bool):
        return variant != "flags_off"
    if isinstance(default, (int, float)):
        return 3
    if default is None and variant == "none":
        return None
    if default is None and variant == "numeric_none" and _NUMERIC_NAME.search(name):
        return 3
    return _text_for(name, mode)


def render_function(func, variant: str, mode: str):
    """Call a simple tag with the payload; numbers where the default is a number, flags per variant."""
    params = {}
    for name, p in inspect.signature(func).parameters.items():
        if p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        params[name] = Context({}) if name == "context" else _param_value(name, p, variant, mode)
    try:
        return func(**params)
    except Exception:  # noqa: BLE001
        return None


FUNCTION_VARIANTS = ("text", "numeric_none", "records", "flags_off", "none", "defaults")


@contextlib.contextmanager
def line_tracer(files: set[str], executed: dict[str, set[int]]):
    """Record which lines of ``files`` run. Chains to (and restores) any tracer already installed."""
    previous = sys.gettrace()

    def local(frame, event, arg):
        if event == "line":
            executed.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
        return local

    def global_trace(frame, event, arg):
        if frame.f_code.co_filename in files:
            executed.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
            return local
        return None

    sys.settrace(global_trace)
    try:
        yield
    finally:
        sys.settrace(previous)


#: Tag classes the generic loop does not drive with a payload NAME, and why. Each has a driver in
#: ``payload_drivers_3774`` that gives it a realistic input.
NOT_DRIVEN_GENERICALLY = {
    "ColocatedHookNode": (
        'its name is the tag\'s parse-time literal ({% colocated_hook "Name" %}), written raw into a '
        "JS comment banner; the driver renders it with a plain name and the payload in the body and nonce"
    ),
}


def sweep(extra_drivers=()) -> dict:
    """Run every driver; return the injections found and what was discovered/rendered."""
    all_keys, numeric = keys()
    found: list[tuple] = []
    rendered = discovered = 0
    for module_name in TAG_MODULES:
        module = importlib.import_module(module_name)
        filters = {f for f in getattr(getattr(module, "register", None), "filters", {}).values()}
        for name, obj in inspect.getmembers(module):
            if getattr(obj, "__module__", None) != module_name or name in NOT_DRIVEN_GENERICALLY:
                continue
            if obj in filters:  # a filter returns its input: Django escapes that at the call site
                continue
            if inspect.isclass(obj) and issubclass(obj, Node):
                discovered += 1
                ok = False
                for mode in MODES:
                    for shape in SHAPES:
                        out = render_node(obj, shape, mode, all_keys, numeric)
                        if out is None:
                            continue
                        ok = True
                        found += [(module_name, name, shape, mode, h) for h in injections(out)]
                rendered += ok
            elif inspect.isfunction(obj) and not name.startswith(("_", "do_")):
                for mode in MODES:
                    for variant in FUNCTION_VARIANTS:
                        out = render_function(obj, variant, mode)
                        if isinstance(out, str):
                            found += [
                                (module_name, name, variant, mode, h) for h in injections(out)
                            ]
    for label, produce in extra_drivers:
        for mode in MODES:
            try:
                outputs = produce(mode)
            except Exception as exc:  # noqa: BLE001
                found.append(("driver", label, mode, f"driver raised {exc!r}"))
                continue
            for out in outputs:
                found += [("driver", label, mode, h) for h in injections(out)]
    return {"found": found, "rendered": rendered, "discovered": discovered}
