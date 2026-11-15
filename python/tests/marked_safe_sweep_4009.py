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


def tag_findings(only=None, value=None, on_output=None) -> tuple[dict, dict]:
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
            names += [n for n in ("class", "id", "title", "data_x", "aria_label") if n not in names]
        if not names:
            continue
        keys = url_sweep.keys_read(info, _fn_of(info)) | set(url_sweep.COMMON_KEYS)
        parent = variants.PARENT.get(info.name)
        parent_info = infos.get("%s:%s" % (info.lib, parent)) if parent else None
        parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""
        for param in names:
            args = url_sweep.typical_args(info, param)
            src = url_sweep.wrap(info, " ".join([*args, "%s=p_" % param]), parent, parent_args)
            if param == "form":
                continue  # a bound form, not a string (``dj_form``'s fields are swept by #3898)
            for shape in ATTR_SHAPES:
                ctx = {"p_": _shaped(shape, value, keys)}
                for renderer, render in RENDERERS:
                    harness.reset_ids()
                    try:
                        out = render(src, dict(ctx))
                    except Exception:  # noqa: BLE001 - a call the tag rejects draws nothing
                        continue
                    rendered[renderer].add(key)
                    if on_output is not None:
                        on_output((renderer, key, param, shape), out)
                    hits = attr_findings(out)
                    if hits:
                        found[(renderer, key, param, shape)] = hits
    return found, rendered


def class_units() -> list[str]:
    from scripts.lib.template_library_parity import url_sweep

    return list(url_sweep.component_classes())


def class_findings(only=None, value=None, on_output=None) -> tuple[dict, set]:
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
            for shape in ATTR_SHAPES:
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
