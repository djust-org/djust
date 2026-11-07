"""Filters for the #3902 tests: what a custom filter that copies or pickles its argument gets."""

import copy
import pickle

from django import template

register = template.Library()


@register.filter
def deepcopied(value):
    """``ERR:<type>`` when ``copy.deepcopy`` refuses the value, ``ok`` when it copies."""
    try:
        copy.deepcopy(value)
    except Exception as exc:  # noqa: BLE001 - the helper reports, the test asserts
        return "ERR:" + type(exc).__module__ + "." + type(exc).__name__
    return "ok"


@register.filter
def pickled(value):
    try:
        pickle.dumps(value)
    except Exception as exc:  # noqa: BLE001
        return "ERR:" + type(exc).__module__ + "." + type(exc).__name__
    return "ok"
