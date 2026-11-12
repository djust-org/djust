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
