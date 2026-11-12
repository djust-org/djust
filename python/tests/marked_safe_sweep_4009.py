"""The marked-safe attribute sweep behind ``test_marked_safe_attribute_sweep_4009.py`` (#4009).

``payload_sweep_3774`` sweeps every component tag with a hostile PLAIN string. A marked-safe
string is a different input: ``conditional_escape`` leaves it as it is, which is right in a text
slot (the marker means "this is markup") and wrong in an attribute, where it lets the value out.
This drives the same tags, through the same generic drivers, with one marked-safe value in every
argument, and reports each tag whose output grew an attribute from it.

The payload breaks out of a double-quoted AND a single-quoted attribute and carries no element,
so a marked-safe value correctly rendered raw in a TEXT slot is not a finding.
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
    return {
        f"{module.rsplit('.', 1)[-1]}:{name}"
        for module, name, *_rest, hit in result["found"]
        if "onmouseover" in hit
    }
