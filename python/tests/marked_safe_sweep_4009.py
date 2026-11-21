"""The marked-safe attribute sweep behind ``test_marked_safe_attribute_sweep_4009.py`` (#4009).

``payload_sweep_3774`` sweeps every component tag with a hostile PLAIN string. A marked-safe
string is a different input: ``conditional_escape`` leaves it as it is, which is right in a text
slot (the marker means "this is markup") and wrong in an attribute, where it lets the value out.
This drives the same tags, through the same generic drivers, with one marked-safe value in every
argument, and reports each tag whose output grew an attribute from it.

The payload breaks out of a double-quoted AND a single-quoted attribute and carries no element,
so a marked-safe value correctly rendered raw in a TEXT slot is not a finding.

The generic drivers keep a tag function's ``str`` return, and an ``inclusion_tag`` returns a context
dict, so the inclusion tags are swept separately (:func:`inclusion_breakouts`): each is rendered
through its template by Django's engine, one marked-safe argument at a time.
"""

from __future__ import annotations

import contextlib
import html
import re

from django.utils.safestring import mark_safe

import payload_sweep_3774 as sweep

TAG = sweep.TAG
SAFE_PAYLOAD = mark_safe(TAG + '"onmouseover=' + TAG + " '" + "onmouseover=" + TAG + " ")


def breakouts() -> set[str]:
    """``"<module>:<name>"`` of every tag node / tag function whose output carries an
    ``onmouseover`` attribute made from the marked-safe payload."""
    original_text_for, original_modes, original_handlers = (
        sweep._text_for,
        sweep.MODES,
        sweep.handlers,
    )
    sweep._text_for = lambda key, mode: (
        SAFE_PAYLOAD if mode == "safe" else original_text_for(key, mode)
    )
    sweep.MODES = ("safe",)
    sweep.handlers = lambda: []  # the native layout handlers are swept by the layout parity tests
    try:
        result = sweep.sweep()
    finally:
        sweep._text_for, sweep.MODES, sweep.handlers = (
            original_text_for,
            original_modes,
            original_handlers,
        )
    found = {
        f"{module.rsplit('.', 1)[-1]}:{name}"
        for module, name, *_rest, hit in result["found"]
        if "onmouseover" in hit
    }
    return found | inclusion_breakouts()


#: What an inclusion tag needs to render at all (its required arguments).
_REQUIRED = {
    "data_table": {"rows": [{"id": 1, "name": "a"}], "columns": [{"key": "name", "label": "N"}]},
    "toast_container": {"toasts": [{"id": 1, "type": "info", "message": "m"}]},
}


def inclusion_tags() -> list[str]:
    """Every ``inclusion_tag`` of the components library."""
    from djust.components.templatetags.djust_components import register
    from djust.template_libraries import _classify

    return sorted(n for n, fn in register.tags.items() if _classify(fn) == "inclusion_tag")


def render_inclusion(name: str, param: str, value: object) -> str:
    """``{% name param=v %}`` (plus its required arguments) through Django's engine."""
    from django.template import engines

    required = dict(_REQUIRED.get(name, {}))
    required[param] = value
    args = " ".join(f"{k}={k}_" for k in required)
    src = "{% load djust_components %}{% " + name + " " + args + " %}"
    ctx = {f"{k}_": v for k, v in required.items()}
    return engines["django"].from_string(src).render(ctx)


def inclusion_breakouts(payload: object = SAFE_PAYLOAD) -> set[str]:
    """``"djust_components:<tag>"`` for each inclusion tag one of whose string arguments, given
    ``payload``, grows an ``onmouseover`` attribute."""
    import inspect

    from djust.components.templatetags.djust_components import register

    found = set()
    for name in inclusion_tags():
        fn = inspect.unwrap(register.tags[name])
        for param in inspect.signature(fn).parameters:
            if param in _REQUIRED.get(name, {}):
                continue
            try:
                html = render_inclusion(name, param, payload)
            except Exception:  # noqa: BLE001 - a tag that rejects the value is not rendering it
                continue
            if any("onmouseover" in hit for hit in sweep.injections(html)):
                found.add(f"djust_components:{name}")
    return found


# --------------------------------------------------------------------------- #
# The per-argument sweep over every tag, on both engines, and every component class (#4009)
#
# ``breakouts`` above drives the tag functions and nodes directly with the payload in EVERY
# argument at once, on the Python side only. This drives each argument ON ITS OWN (the others
# take a typical value, so the branch that prints it is drawn), through BOTH real engines (the
# Django engine and the Rust engine, which runs the kept native twins where it has one), for every
# tag of the five swept libraries (the parity inventory: ``djust_components`` with its ``_forms``
# / ``_advanced`` / ``_charts`` / ``_dev_tools`` tags, and the four theming libraries), and every
# component class the URL sweep can construct. A list or dict argument is given the marked-safe
# value as each string member, as a list and as a tuple.

#: A marked-safe value that closes a double- OR a single-quoted attribute and adds a ``data-pwn``
#: attribute; it also carries ``&amp;``, which an escaping writer turns into ``&amp;amp;`` (so a
#: value written raw into ANY attribute, quoted or not, decodes to :data:`RAW_MARK`). No element:
#: written raw in a TEXT slot, which is what ``mark_safe`` is for, it is not a finding.
ATTR_PAYLOAD_TEXT = "ZQ&amp;ZQ\" data-pwn=\"1' data-pwn='1"
#: What a raw :data:`ATTR_PAYLOAD_TEXT` decodes to inside an attribute value (escaped, it decodes
#: to ``ZQ&amp;ZQ``).
RAW_MARK = "ZQ&ZQ"

#: A list or dict argument: the shapes each argument is driven in.
ATTR_SHAPES = ("scalar", "list_str", "tuple_str", "list_dict", "tuple_dict", "dict")


def attr_payload() -> object:
    return mark_safe(ATTR_PAYLOAD_TEXT)


class _AttrScan(sweep.HTMLParser):
    """Every element that grew a ``data-pwn`` attribute, and every attribute whose decoded value
    holds :data:`RAW_MARK` (the marked-safe value was written into it unescaped)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "data-pwn":
                self.hits.append(f"<{tag} data-pwn> (breakout)")
            elif value and RAW_MARK in value:
                self.hits.append(f"<{tag} {name}> (raw)")

    handle_startendtag = handle_starttag


def reaches_an_attribute(markup: object) -> bool:
    """Whether :data:`ATTR_PAYLOAD_TEXT` arrived, escaped, in some attribute value of ``markup``:
    the instrument saw the value where the defect would be (a sweep whose values never reach an
    attribute would pass for nothing)."""
    text = str(markup)
    if "ZQ&amp;amp;ZQ" not in text:
        return False

    class _Reached(sweep.HTMLParser):
        hit = False

        def handle_starttag(self, tag, attrs):
            if any(v and "ZQ&amp;ZQ" in v for _n, v in attrs):
                self.hit = True

        handle_startendtag = handle_starttag

    parser = _Reached(convert_charrefs=True)
    parser.feed(text)
    parser.close()
    return parser.hit


def attr_findings(markup: object) -> list[str]:
    """What :data:`ATTR_PAYLOAD_TEXT` did to ``markup``'s attributes (empty: nothing)."""
    text = str(markup)
    if "data-pwn" not in text and "ZQ&amp;ZQ" not in text:
        return []  # an escaped payload leaves neither (``data-pwn=&quot;`` / ``ZQ&amp;amp;ZQ``)
    parser = _AttrScan()
    parser.feed(text)
    parser.close()
    return sorted(set(parser.hits))


def _shaped(shape: str, value: object, keys) -> object:
    from scripts.lib.template_library_parity import url_sweep, variants

    if shape == "scalar":
        return value
    if shape == "list_str":
        return [value, value]
    if shape == "tuple_str":
        return (value, value)
    row = url_sweep.dict_of(value, sorted(keys))
    row = {
        k: row[k] for k in sorted(row)
    }  # a fixed order: a dict printed whole is the same each run
    for k in list(row):
        if variants.kind_of(k) in ("num", "bool"):
            row[k] = 3 if variants.kind_of(k) == "num" else False
    if shape == "dict":
        return row
    rows = [row, dict(row)]
    return rows if shape == "list_dict" else tuple(rows)


def _plain_items(keys) -> list:
    """One item of plain text under every key a tag reads (numbers where it counts)."""
    from scripts.lib.template_library_parity import variants

    return [{k: (3 if variants.kind_of(k) == "num" else "x") for k in sorted(keys)}]


def _tag_infos() -> dict:
    """The parity inventory over the URL sweep's five libraries plus ``live_tags`` and
    ``djust_pwa`` (the CSP sweep's set), less the tags that draw another view's markup."""
    from scripts.lib.template_library_parity import csp_sweep

    return {k: v for k, v in csp_sweep.inventory().items() if k not in csp_sweep.NOT_RENDERED}


def _fn_of(info) -> object:
    from django.template.library import import_library
    from scripts.lib.template_library_parity import csp_sweep

    fn = import_library(csp_sweep.SWEPT_LIBRARIES[info.lib]).tags[info.name]
    return getattr(fn, "__wrapped__", fn)


def tag_units() -> list[str]:
    """``"<lib>:<tag>"`` of every tag the per-argument sweep drives (the sampling unit)."""
    infos = _tag_infos()
    out = []
    for key, info in infos.items():
        if _closer(info, infos):
            continue
        out.append(key)
    return out


def _closer(info, infos) -> bool:
    return info.name.startswith("end") and info.name[3:].lstrip("_") in {
        i.name.removesuffix("_block") for i in infos.values() if i.lib == info.lib
    }


def _rust_render(src: str, ctx: dict) -> str:
    from djust import _rust
    from djust.components.rust_handlers import register_with_rust_engine
    from djust.theming.rust_handlers import register_with_rust_engine as register_theming

    register_with_rust_engine()
    register_theming()
    return str(_rust.render_template(src, ctx))


def _django_render(src: str, ctx: dict) -> str:
    from django.template import engines

    return str(engines["django"].from_string(src).render(ctx))


RENDERERS = (("django", _django_render), ("rust", _rust_render))


def tag_findings(
    only=None, value=None, on_output=None, shapes=None, companions=()
) -> tuple[dict, dict]:
    """``({(renderer, "<lib>:<tag>", param, shape): [finding, ...]}, {renderer: {tag rendered}})``
    for the tags in ``only`` (all of them when ``None``)."""
    import dataclasses

    from scripts.lib.template_library_parity import harness, url_sweep, variants

    infos = _tag_infos()
    found: dict = {}
    rendered: dict = {"django": set(), "rust": set()}
    value = attr_payload() if value is None else value
    for key in tag_units():
        if only is not None and key not in only:
            continue
        info = infos[key]
        base = infos.get("%s:%s" % (info.lib, info.name.removesuffix("_block")))
        if info.name.endswith("_block") and base is not None and base is not info:
            info = dataclasses.replace(
                info, py_kind=base.py_kind, sig=base.sig, py_keys=base.py_keys,
                rust_keys=base.rust_keys,
            )  # fmt: skip
        names = list(variants.passed_names(info))
        if url_sweep.takes_varkw(info):
            names += [n for n in PASSTHROUGH if n not in names]
        if not names:
            continue
        keys = url_sweep.keys_read(info, _fn_of(info)) | set(url_sweep.COMMON_KEYS)
        parent = variants.PARENT.get(info.name)
        parent_info = infos.get("%s:%s" % (info.lib, parent)) if parent else None
        parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""
        for param in names:
            args = url_sweep.typical_args(info, param)
            # parameters that make the tag draw more (a description, a helper text): given a
            # plain value, so the element an id names is drawn too
            args += ['%s="x"' % c for c in companions if c in names and c != param]
            # a list parameter not under test gets one plain item, so the loop that draws it runs
            # (``{% theme_tabs id=... %}`` writes its ids inside ``{% for tab in tabs %}``)
            lists = [n for n in names if n != param and variants.kind_of(n) == "list"]
            lists = [n for n in lists if not any(a.startswith(n + "=") for a in args)]
            bare = url_sweep.wrap(info, " ".join([*args, "%s=p_" % param]), parent, parent_args)
            args += ["%s=p_items_" % n for n in lists]
            src = url_sweep.wrap(info, " ".join([*args, "%s=p_" % param]), parent, parent_args)
            if param == "form":
                continue  # a bound form, not a string (``dj_form``'s fields are swept by #3898)
            for shape in shapes or ATTR_SHAPES:
                ctx = {"p_": _shaped(shape, value, keys), "p_items_": _plain_items(keys)}
                for pack in packs_for(info.lib):
                    for renderer, render in RENDERERS:
                        name = renderer if pack is None else f"{renderer}@{pack}"
                        harness.reset_ids()
                        try:
                            with theme_pack(pack):
                                try:
                                    out = render(src, dict(ctx))
                                except Exception:  # noqa: BLE001 - the items may be what it rejects
                                    harness.reset_ids()
                                    out = render(bare, dict(ctx))
                        except Exception:  # noqa: BLE001 - a call the tag rejects draws nothing
                            continue
                        rendered.setdefault(name, set()).add(key)
                        if on_output is not None:
                            on_output((name, key, param, shape), out)
                        hits = attr_findings(out)
                        if hits:
                            found[(name, key, param, shape)] = hits
    return found, rendered


#: What a ``**attrs`` tag is given besides its named parameters: the attributes a caller passes
#: through (an event handler included: ``{% theme_button "T" onclick=... %}``).
PASSTHROUGH = (
    "class", "id", "title", "onclick", "type", "name", "value", "placeholder", "role",
    "data_x", "aria_label", "dj_click", "style",
)  # fmt: skip


def theme_packs() -> tuple[str, ...]:
    """Every theme pack that ships its own component templates, plus ``"base"`` (the shared
    ``djust_theming/components/*.html`` every other component falls back to)."""
    from pathlib import Path

    import djust.theming

    root = Path(djust.theming.__file__).parent / "templates" / "djust_theming" / "themes"
    return ("base", *sorted(p.name for p in root.iterdir() if (p / "components").is_dir()))


def packs_for(lib: str) -> tuple:
    """A theming library is swept under every pack; any other library once, as configured."""
    return theme_packs() if lib.startswith("theme_") else (None,)


@contextlib.contextmanager
def theme_pack(pack):
    """Resolve theming templates as if ``pack`` were the active theme (``"base"``: no pack, so
    every template is the shared one)."""
    if pack is None:
        yield
        return
    from djust.theming import template_resolver as tr

    names = [n for n in vars(tr) if n.startswith("_get_") and n.endswith("_candidates")]
    saved = {n: getattr(tr, n) for n in names}
    forced = "__no_pack__" if pack == "base" else pack
    try:
        for n, fn in saved.items():
            setattr(tr, n, (lambda f: lambda _theme, name: f(forced, name))(fn))
        yield
    finally:
        for n, fn in saved.items():
            setattr(tr, n, fn)


def class_units() -> list[str]:
    from scripts.lib.template_library_parity import url_sweep

    return list(url_sweep.component_classes())


def class_findings(only=None, value=None, on_output=None, shapes=None) -> tuple[dict, set]:
    """``({("class:<framework>", "<module>.<Class>", param, shape): [finding]}, {rendered})``."""
    from scripts.lib.template_library_parity import harness, url_sweep

    from djust.config import config

    found: dict = {}
    rendered: set = set()
    value = attr_payload() if value is None else value
    keys = set(url_sweep.COMMON_KEYS)
    for key, cls in url_sweep.component_classes().items():
        if only is not None and key not in only:
            continue
        names, required = url_sweep.component_params(cls)
        required = {**required, **_CLASS_REQUIRED.get(key, {})}
        frameworks = url_sweep.FRAMEWORKS
        for param in names:
            for shape in shapes or ATTR_SHAPES:
                kwargs = {**required, param: _shaped(shape, value, keys)}
                for fw in frameworks:
                    before = config.get("css_framework", "bootstrap5")
                    config.set("css_framework", fw)
                    harness.reset_ids()
                    try:
                        given = dict(kwargs)
                        if "css_framework" in names and param != "css_framework":
                            given["css_framework"] = fw
                        out = str(cls(**given).render())
                    except Exception:  # noqa: BLE001 - a call the class rejects draws nothing
                        continue
                    finally:
                        config.set("css_framework", before)
                    rendered.add(key)
                    if on_output is not None:
                        on_output(("class:" + fw, key, param, shape), out)
                    hits = attr_findings(out)
                    if hits:
                        found[("class:" + fw, key, param, shape)] = hits
    return found, rendered


#: Component classes that need more than their required arguments to construct.
_CLASS_REQUIRED: dict = {
    "djust.components.components.stat_card.StatCard": {"label": "x", "value": "1"},
}


def rust_component_units() -> list[str]:
    """The Rust components ``djust._rust`` exposes to Python (``RustButton`` ...)."""
    from djust import _rust

    return sorted(
        n
        for n in dir(_rust)
        if n.startswith("Rust") and n != "RustLiveView" and getattr(_rust, n).__text_signature__
    )


def _rust_params(cls) -> list[tuple[str, object]]:
    """``(name, default)`` of each parameter of a Rust component's constructor."""
    text = cls.__text_signature__.strip("()")
    out = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        name, _, default = part.partition("=")
        out.append((name.strip(), default.strip() or None))
    return out


def rust_component_findings(only=None, value=None, on_output=None) -> tuple[dict, set]:
    """``({("rust-component", "Rust<X>", param, "scalar"): [finding]}, {rendered})``: each string
    parameter of each Rust component given the marked-safe value, the others their defaults (a
    required one a plain ``"x"``, a number for ``value``)."""
    from djust import _rust

    found: dict = {}
    rendered: set = set()
    value = attr_payload() if value is None else value
    for name in rust_component_units():
        if only is not None and name not in only:
            continue
        cls = getattr(_rust, name)
        params = _rust_params(cls)
        required = {p: (50.0 if p == "value" else "x") for p, default in params if default is None}
        for param, default in params:
            if default not in (None, "None") and not default.startswith(('"', "'")):
                continue  # a bool or a number
            if param == "value" and default is None:
                continue
            try:
                out = cls(**{**required, param: value}).render()
            except Exception:  # noqa: BLE001 - a value the component rejects draws nothing
                continue
            rendered.add(name)
            if on_output is not None:
                on_output(("rust-component", name, param, "scalar"), out)
            hits = attr_findings(out)
            if hits:
                found[("rust-component", name, param, "scalar")] = hits
    return found, rendered


# --------------------------------------------------------------------------- #
# An id and the references to it are escaped exactly once, and so match (#4009)

#: An id that needs every escape an attribute has.
PAIR_ID = 'a&b"c<d'
#: Attributes that name an element by id (``data-bs-target`` / ``href`` may carry ``#``).
REFERENCE_ATTRS = frozenset(
    {"for", "aria-controls", "aria-labelledby", "aria-describedby", "aria-owns",
     "aria-activedescendant", "data-bs-target", "list", "form"}
)  # fmt: skip
#: Parameters that make a tag draw the element an id names (``description=`` draws the
#: ``<p id="<id>-desc">`` that ``aria-describedby`` points at).
COMPANIONS = ("title", "label", "description", "helper", "help_text", "error", "hint", "content")
#: Parameters that are themselves a reference to an id the caller owns (not one the tag draws).
_REFERENCE_PARAMS = ("describedby", "labelledby", "controls", "target", "for", "for_", "form")


class _Ids(sweep.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.refs: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value is None:
                continue
            if name == "id":
                self.ids.add(value)
            elif name in REFERENCE_ATTRS:
                self.refs += [(name, v.lstrip("#")) for v in value.split()]

    handle_startendtag = handle_starttag


def pairing_problems(markup: object, value: str = PAIR_ID) -> list[str]:
    """Each id or reference built from ``value`` that is not ``value`` decoded exactly once, and
    each such reference that names no id of ``markup``."""
    text = str(markup)
    if "a&amp;b" not in text and "a&amp;amp;b" not in text:
        return []
    parser = _Ids()
    parser.feed(text)
    parser.close()
    out = []
    for i in parser.ids:
        if "a&amp;b" in i or ("a&b" in i and value not in i):
            out.append(f"id {i!r} is not the value escaped once")
    for name, ref in parser.refs:
        if "a&amp;b" in ref or ("a&b" in ref and value not in ref):
            out.append(f"{name} {ref!r} is not the value escaped once")
        elif ref not in parser.ids and any(
            html.unescape(i) == ref or i == html.unescape(ref) for i in parser.ids
        ):
            # the reference and the id it names were escaped a different number of times (a
            # reference to an element the call does not draw at all is not an escaping question)
            out.append(f"{name} {ref!r} does not match the id it names")
    return sorted(set(out))


def pairing_findings(tags=None, classes=None) -> dict:
    """``{cell: problems}`` with :data:`PAIR_ID` in each scalar argument of each tag (both engines,
    every theme pack) and each component class."""
    found: dict = {}

    def check(cell, out):
        if cell[3] != "scalar" or cell[2].endswith(_REFERENCE_PARAMS):
            return
        problems = pairing_problems(out)
        if problems:
            found[cell] = problems

    tag_findings(only=tags, value=PAIR_ID, on_output=check, shapes=("scalar",))
    tag_findings(
        only=tags, value=PAIR_ID, on_output=check, shapes=("scalar",), companions=COMPANIONS
    )
    class_findings(only=classes, value=PAIR_ID, on_output=check, shapes=("scalar",))
    return found


# --------------------------------------------------------------------------- #
# The kept native handlers, called directly (#4009)


def native_handler_units() -> list[str]:
    """``"<tag>"`` of each native handler ``djust.components.rust_handlers`` registers."""
    from djust.components import rust_handlers as rh

    return [t for t, _h in rh.NATIVE_INLINE_HANDLERS] + [
        t for t, _e, _h in rh.NATIVE_BLOCK_HANDLERS
    ]


def native_handler_findings(only=None, value=None) -> tuple[dict, set]:
    """``({("native", tag, key, shape): [finding]}, {rendered})``: each handler called the way the
    engine calls it, with its operands handed over AS GIVEN (``_parse_args`` replaced for the call).

    Through the engine an operand never reaches a handler marked safe: ``_parse_args`` makes each
    one plain text (``plain_operand``). This drives the handler's own writing with a marked-safe
    operand, the only way one can arrive, so a handler that relies on ``conditional_escape`` for an
    attribute is seen, not only the guard's count of its sinks."""
    from unittest.mock import patch

    from scripts.lib.template_library_parity import harness, url_sweep

    from djust.components import rust_handlers as rh

    value = attr_payload() if value is None else value
    handlers = [(t, h, False) for t, h in rh.NATIVE_INLINE_HANDLERS]
    handlers += [(t, h, True) for t, _e, h in rh.NATIVE_BLOCK_HANDLERS]
    found: dict = {}
    rendered: set = set()
    for tag, handler, block in handlers:
        if only is not None and tag not in only:
            continue
        inner = getattr(handler, "_handler", handler)
        keys_, params = harness.discover(type(inner))
        keys = sorted((keys_ | params | set(url_sweep.COMMON_KEYS)) - {"self", "args", "context"})
        for key in keys:
            for shape in ("scalar", "list_dict", "tuple_dict"):
                kw = {key: _shaped(shape, value, keys)}
                harness.reset_ids()
                with patch.object(rh, "_parse_args", lambda _args, _ctx, kw=kw: dict(kw)):
                    try:
                        out = (
                            handler.render([], mark_safe("BODY"), {})
                            if block
                            else handler.render([], {})
                        )
                    except Exception:  # noqa: BLE001 - a value the handler rejects draws nothing
                        continue
                rendered.add(tag)
                hits = attr_findings(out)
                if hits:
                    found[("native", tag, key, shape)] = hits
    return found, rendered


# --------------------------------------------------------------------------------------------
# Page templates (#4157): the admin, allauth / djust_auth, bug-capture replay and gallery pages.
# --------------------------------------------------------------------------------------------

#: Template directories swept, relative to the package (``python/djust``), and the template-name
#: prefix each holds. Globbed, so a new template in them is swept without being listed.
PAGE_TEMPLATE_DIRS = (
    ("admin_ext/templates", "djust_admin/"),
    ("auth/templates", "allauth/elements/"),
    ("auth/templates", "djust_auth/"),
    ("templates", "djust/bug_capture/"),
    ("components/templates", "djust_components/gallery/"),
    ("theming/templates", "djust_theming/gallery/"),
)

#: Attributes whose value is navigated to: they go through ``djust_url_attr``.
URL_ATTRS = frozenset({"href", "action", "src", "formaction"})
#: The filters that escape an attribute value whatever it is marked as.
ATTR_ESCAPES = frozenset({"force_escape", "djust_url_attr"})


def _package_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "djust"


def page_templates() -> tuple[str, ...]:
    """Every swept page template, by template name."""
    root = _package_root()
    names = []
    for base, prefix in PAGE_TEMPLATE_DIRS:
        top = root / base
        names += [p.relative_to(top).as_posix() for p in sorted((top / prefix).rglob("*.html"))]
    return tuple(sorted(names))


def page_template_path(name: str):
    root = _package_root()
    for base, prefix in PAGE_TEMPLATE_DIRS:
        if name.startswith(prefix):
            return root / base / name
    raise KeyError(name)


def page_attr_tokens(source: str) -> list[dict]:
    """Each ``{{ }}`` written inside a start tag: ``line``, ``attr``, ``expression``,
    ``position`` (``(start, end)`` in ``source``) and ``leading`` (nothing of the value precedes
    it: no text, no ``{% url %}`` / ``{% static %}``).

    The Django lexer splits the source; TEXT tokens drive a small HTML state machine (in a tag,
    in a quoted or unquoted attribute value, in a comment, in a ``<script>`` / ``<style>`` body).
    A ``{{ }}`` in a tag but outside any value (``<div {{ attrs }}>``) has ``attr`` ``<bare>``.
    """
    from django.template.base import DebugLexer, TokenType

    slots: list[dict] = []
    state = "text"  # text | tag | value_dq | value_sq | value_bare | comment | raw
    raw_end = ""
    attr = ""
    name_buf = ""
    after_eq = False
    has_content = False
    pending_lt = False
    in_value = ("value_dq", "value_sq", "value_bare")
    for token in DebugLexer(source).tokenize():
        if token.token_type == TokenType.VAR:
            if state == "tag" and after_eq:
                state, after_eq, has_content = "value_bare", False, False
            if state == "tag" or state in in_value:
                slots.append(
                    {
                        "line": token.lineno,
                        "attr": (attr if state in in_value else "<bare>").lower(),
                        "expression": token.contents.strip(),
                        "position": token.position,
                        "leading": state in in_value and not has_content,
                    }
                )
                if state in in_value:
                    has_content = True
            continue
        if token.token_type == TokenType.BLOCK:
            if state in in_value and token.contents.split()[:1] in (["url"], ["static"]):
                has_content = True
            elif state == "text" and pending_lt:
                # ``<{% if x %}a href=...{% else %}button{% endif %}``: a tag whose name a tag
                # chooses is still a tag.
                state, attr, name_buf, after_eq, raw_end = "tag", "", "", False, ""
            pending_lt = False
            continue
        pending_lt = False
        if token.token_type != TokenType.TEXT:
            continue
        text = token.contents
        pending_lt = state == "text" and text.endswith("<")
        i = 0
        while i < len(text):
            ch = text[i]
            if state == "text":
                if text.startswith("<!--", i):
                    state, i = "comment", i + 4
                    continue
                if (
                    ch == "<"
                    and i + 1 < len(text)
                    and (text[i + 1].isalpha() or text[i + 1] == "/")
                ):
                    j = i + 1
                    while j < len(text) and (text[j].isalnum() or text[j] in "/-"):
                        j += 1
                    tagname = text[i + 1 : j].lower()
                    raw_end = f"</{tagname}" if tagname in ("script", "style") else ""
                    state, attr, name_buf, after_eq, i = "tag", "", "", False, j
                    continue
            elif state == "comment":
                if text.startswith("-->", i):
                    state, i = "text", i + 3
                    continue
            elif state == "raw":
                if text.lower().startswith(raw_end, i):
                    state, raw_end = "text", ""
                    continue
            elif state == "tag":
                if ch == ">":
                    state = "raw" if raw_end else "text"
                elif ch == "=":
                    attr, name_buf, after_eq = name_buf or attr, "", True
                elif ch in "\"'" and after_eq:
                    state, after_eq, has_content = (
                        ("value_dq" if ch == '"' else "value_sq"),
                        False,
                        False,
                    )
                elif ch.isspace():
                    if name_buf:
                        attr, name_buf = name_buf, ""
                elif after_eq:
                    state, after_eq, has_content = "value_bare", False, True
                else:
                    name_buf += ch
            elif state == "value_dq":
                if ch == '"':
                    state = "tag"
                else:
                    has_content = True
            elif state == "value_sq":
                if ch == "'":
                    state = "tag"
                else:
                    has_content = True
            elif state == "value_bare":
                if ch.isspace():
                    state = "tag"
                elif ch == ">":
                    state = "raw" if raw_end else "text"
                else:
                    has_content = True
            i += 1
    return slots


def page_attr_slots(source: str) -> list[tuple[int, str, str]]:
    """``[(line, attribute, expression)]`` of :func:`page_attr_tokens`."""
    return [(t["line"], t["attr"], t["expression"]) for t in page_attr_tokens(source)]


def _last_filter(expression: str) -> str:
    parts = expression.split("|")
    return parts[-1].split(":")[0].strip() if len(parts) > 1 else ""


def page_slot_findings(source: str) -> list[str]:
    """Each attribute slot of ``source`` that does not escape its value whatever it is marked
    as: the last filter is not ``force_escape`` / ``djust_url_attr``, or the value of a URL
    attribute starts with a slot that does not end in ``djust_url_attr`` (a slot after a literal
    ``?next=`` or a ``{% url %}`` cannot set the scheme, so ``force_escape`` is enough there)."""
    out = []
    for slot in page_attr_tokens(source):
        line, attr, expression = slot["line"], slot["attr"], slot["expression"]
        last = _last_filter(expression)
        if attr in URL_ATTRS and slot["leading"]:
            if last != "djust_url_attr":
                out.append(f"line {line}: {attr}={{{{ {expression} }}}} (URL)")
        elif last not in ATTR_ESCAPES:
            out.append(f"line {line}: {attr}={{{{ {expression} }}}}")
    return out


#: Attributes a probe does not have: its own, and the markers Django's variable resolution
#: reads off a value (a probe answering them would be recorded as data the template reads).
_PROBE_OPAQUE = frozenset({"path", "trace", "do_not_call_in_templates", "alters_data"})


class PageProbe(str):
    """A context value for a page template that IS :data:`ATTR_PAYLOAD_TEXT`, marked safe,
    wherever the template writes it, and a container wherever the template looks inside it:
    ``probe.x`` / ``probe["x"]`` / ``probe.0`` is another probe, ``{% for %}`` sees two of them.

    No per-template context is written by hand: every variable a template reads is a probe, so
    every slot is driven (each branch an ``{% if %}`` on a probe takes; comparisons with a literal
    are false). Each probe records the path the template read it by, which
    :func:`materialise` turns into plain dicts and lists for the Rust engine.
    """

    def __new__(cls, path: tuple = (), trace: set | None = None):
        probe = super().__new__(cls, ATTR_PAYLOAD_TEXT)
        probe.path = path
        probe.trace = set() if trace is None else trace
        probe.trace.add(path)
        return probe

    def __html__(self):  # marked safe, like ``mark_safe(ATTR_PAYLOAD_TEXT)``
        return str.__str__(self)

    def __str__(self):
        return str.__str__(self)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return [PageProbe(self.path + ("*",), self.trace) for _ in range(2)]
        return PageProbe(self.path + (str(key),), self.trace)

    def __getattr__(self, name):
        if name.startswith("__") or name in _PROBE_OPAQUE:
            raise AttributeError(name)
        return PageProbe(self.path + (name,), self.trace)

    def __iter__(self):
        return iter([PageProbe(self.path + ("*",), self.trace) for _ in range(2)])

    def __len__(self):
        return 2

    def __call__(self, *args, **kwargs):
        # A method the template or a tag calls (``form.non_field_errors``) answers the probe.
        return self

    def __bool__(self):
        return True


_NAME_RE = re.compile(r"\{[{%]\s*(.*?)\s*[}%]\}", re.S)
_LITERAL_RE = re.compile(r"(['\"]).*?\1")
_IDENT_RE = re.compile(r"(?<![\w.|:])([A-Za-z_]\w*)")
_TEMPLATE_REF_RE = re.compile(r"\{%\s*(?:extends|include)\s+[\"']([^\"']+)[\"']")


def _template_variable_names(name: str, seen: set | None = None) -> set:
    """Every identifier a template (and the templates it extends or includes by a literal name)
    names inside ``{{ }}`` / ``{% %}``: the context keys that get a probe. A key only Python code
    reads (a tag's ``context.get("theme_manager")``) is not one, so the tag keeps its default."""
    seen = set() if seen is None else seen
    if name in seen:
        return set()
    seen.add(name)
    try:
        source = _page_engine().get_template(name).source
    except Exception:  # noqa: BLE001 - a template the engine cannot load names nothing
        return set()
    names = set()
    for body in _NAME_RE.findall(source):
        names |= set(_IDENT_RE.findall(_LITERAL_RE.sub("", body)))
    for ref in _TEMPLATE_REF_RE.findall(source):
        names |= _template_variable_names(ref, seen)
    return names


class _ProbeContext(dict):
    """A context dict that answers each of ``names`` with a probe (Django asks ``key in ctx``
    before ``ctx[key]``, so both say yes); ``fixed`` values are given as they are."""

    def __init__(self, trace: set, names=None, fixed=None):
        super().__init__(fixed or {})
        self.trace = trace
        self.names = names

    def __contains__(self, key):
        return dict.__contains__(self, key) or self.names is None or key in self.names

    def __missing__(self, key):
        value = PageProbe((key,), self.trace)
        self[key] = value
        return value


def materialise(trace: set) -> dict:
    """The paths a probe context was read by, as nested dicts and lists whose leaves are the
    marked-safe payload: what the Rust engine (which takes real containers) is given."""
    tree: dict = {}
    for path in sorted(trace, key=len):
        node = tree
        for part in path:
            node = node.setdefault(part, {})

    def build(node):
        if not node:
            return attr_payload()
        if "*" in node:
            item = build(node["*"])
            return [item, item]
        return {k: build(v) for k, v in node.items()}

    return {k: build(v) for k, v in tree.items()}


#: The stand-in for django-allauth's ``{% load allauth %}`` (``slot`` renders its default
#: body), so ``allauth/elements/*`` render without allauth installed; also the URLconf the
#: pages render under (:data:`PAGE_URLCONF`).
ALLAUTH_STUB = "page_sweep_support_4157"
PAGE_URLCONF = "page_sweep_support_4157"

#: Values framework code (a tag, ``{% live_render %}``, ``{% url %}``) consumes as objects:
#: given as they are, never a probe.
PAGE_FIXED = {
    "form": None,
    "opts": None,  # set to ``Group._meta`` in :func:`page_render` (the support site has it)
    "messages": (),
    "change_form_widgets": (),
    "change_list_widgets": (),
}


def _page_engine():
    """A Django engine over the project's template settings plus :data:`ALLAUTH_STUB`."""
    from django.template import Engine, engines

    base = engines["django"].engine
    return Engine(
        dirs=list(base.dirs),
        app_dirs=base.app_dirs,
        context_processors=[],
        debug=False,
        loaders=None if base.app_dirs else base.loaders,
        string_if_invalid="",
        file_charset=base.file_charset,
        libraries={**base.libraries, "allauth": ALLAUTH_STUB},
        builtins=[b for b in base.builtins if b not in Engine.default_builtins],
        autoescape=True,
    )


def _page_request():
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = {}
    return request


def page_render(name: str, fixed: dict | None = None) -> tuple[dict, dict]:
    """``({engine: markup}, {engine: error})`` for one page template.

    Two passes. The first renders with every context key the template names as a
    :class:`PageProbe` (``fixed`` keys, ``request`` and ``csrf_token`` excepted: framework code
    reads them), and ``{% auth_context %}`` answering a probe too, which records what the
    template reads. That trace is then :func:`materialise`-d into plain dicts and lists whose
    leaves are the marked-safe payload, and THAT context is what is judged: Django renders it
    through the loader (``{% extends %}`` / ``{% include %}`` resolve as in a project), and the
    ``djust_admin`` pages, LiveView templates the Rust engine renders in production, also go
    through ``render_template_with_dirs``. (A probe is a ``str``, so a tag that treats a string
    argument as "not a list" would be judged on a value no caller passes.)"""
    from unittest.mock import patch

    from django.contrib.auth.models import Group
    from django.template import Context
    from page_sweep_support_4157 import ADMIN_SITE_NAME

    fixed = {**PAGE_FIXED, "opts": Group._meta, "admin_site_name": ADMIN_SITE_NAME, **(fixed or {})}
    out: dict = {}
    errors: dict = {}
    trace: set = set()
    values = {"request": _page_request(), "csrf_token": "csrf-token-4157", **fixed}
    probes = _ProbeContext(trace, _template_variable_names(name) - set(values), values)
    engine = _page_engine()
    build_auth = "djust.auth.templatetags.djust_auth.build_auth"
    try:
        with patch(build_auth, lambda *_a, **_k: PageProbe(("auth",), trace)):
            engine.get_template(name).render(Context(probes))
    except Exception as exc:  # noqa: BLE001 - reported by the caller's non-vacuity check
        errors["trace"] = f"{type(exc).__name__}: {exc}"
        return out, errors
    data = materialise(trace - {("request",), ("csrf_token",)})
    auth = data.pop("auth", {})
    try:
        with patch(build_auth, lambda *_a, **_k: auth):
            out["django"] = str(engine.get_template(name).render(Context({**data, **values})))
    except Exception as exc:  # noqa: BLE001
        errors["django"] = f"{type(exc).__name__}: {exc}"
    if name.startswith("djust_admin/"):
        from djust import _rust
        from djust.template_libraries import install_loader

        install_loader()
        source = page_template_path(name).read_text()
        dirs = [str(page_template_path(name).parents[1])]
        try:
            out["rust"] = str(_rust.render_template_with_dirs(source, {**data, **fixed}, dirs))
        except Exception as exc:  # noqa: BLE001
            errors["rust"] = f"{type(exc).__name__}: {exc}"
    return out, errors


def page_findings(name: str, fixed: dict | None = None) -> tuple[dict, dict, dict]:
    """``({engine: findings}, {engine: reached}, {engine: error})`` for one page template."""
    out, errors = page_render(name, fixed)
    found = {engine: attr_findings(markup) for engine, markup in out.items()}
    reached = {engine: reaches_an_attribute(markup) for engine, markup in out.items()}
    return {k: v for k, v in found.items() if v}, reached, errors
