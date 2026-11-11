"""A template library for ``test_custom_callable_model_argument_3949``: every callable kind reports what it RECEIVED.

Each callable is the shape an application writes (a filter, a ``simple_tag``, the same tag used with
``as``, an ``inclusion_tag``, a ``takes_context`` tag) and each returns ONE JSON document describing its
argument, so the test reads the answer instead of parsing prose. The description asks the questions a
filter author asks of a model: what type is it, is it a ``Model``, what does ``str()`` give, can I read
``password``, can I read ``_meta``, can I subscript it, can I read a field the model excludes.
"""

from __future__ import annotations

import json
from typing import Any

from django import template
from django.db import models
from django.template import Template

register = template.Library()

#: the model's own per-model exclusion (``djust_exclude_fields``) names this field
EXCLUDED_FIELD = "email"


def _try(fn: Any) -> Any:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - the library reports, the test asserts
        return "ERR:" + type(exc).__name__


def describe(value: Any, depth: int = 0) -> dict:
    """What ``value`` is, as plain JSON-able data."""
    out: dict = {"type": type(value).__name__, "is_model": isinstance(value, models.Model)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return out
    out["str"] = _try(lambda: str(value))
    if isinstance(value, dict):
        out["keys"] = sorted(str(k) for k in value)
        # the wrapper keys the test uses for a dict that HOLDS models
        out["children"] = {
            str(k): describe(v, depth + 1) for k, v in value.items() if k in ("owner", "items")
        }
        out["get_first_name"] = value.get("first_name", "ABSENT")
        out["get_owner_code"] = value.get("owner_code", "ABSENT")
        out["get_note_text"] = value.get("get_note_text", "ABSENT")
        out["get_excluded"] = value.get(EXCLUDED_FIELD, "ABSENT")
        out["get_pw"] = value.get("password", "ABSENT")
        out["get_full_name"] = value.get("get_full_name", "ABSENT")
        return out
    if isinstance(value, (list, tuple)):
        out["items"] = [describe(item, depth + 1) for item in value]
        return out
    # an object: ask the questions a filter author would
    out["pw_read"] = _try(lambda: value.password)
    out["meta_read"] = _try(lambda: type(value._meta).__name__)
    out["excluded_read"] = _try(lambda: getattr(value, EXCLUDED_FIELD))
    out["username"] = _try(lambda: value.username)
    out["first_name"] = _try(lambda: value.first_name)
    out["owner_code"] = _try(lambda: value.owner_code)
    out["note_text"] = _try(lambda: value.get_note_text())
    out["subscript"] = _try(lambda: value["username"])
    out["pk_read"] = _try(lambda: value.pk)
    out["full_name"] = _try(lambda: value.get_full_name())
    out["last_name_read"] = _try(lambda: value.last_name)
    if depth < 2 and not isinstance(value, models.Model):
        # a proxy for a QuerySet / Manager: enumerate what iterating it yields
        items = _try(lambda: [describe(item, depth + 1) for item in iter(value)])
        out["iter"] = items
    return out


def _json(value: Any) -> str:
    return json.dumps(describe(value), sort_keys=True, default=str)


@register.filter
def cc3949_filter(value, arg=None):
    return _json(value)


@register.simple_tag
def cc3949_tag(value):
    return _json(value)


@register.inclusion_tag(Template("{{ d }}"))
def cc3949_incl(value):
    return {"d": _json(value)}


@register.simple_tag(takes_context=True)
def cc3949_ctx(context, name):
    return _json(context[name])


#: the two callables whose argument is the template author's expression, by group
FILTER_NAMES = ("cc3949_filter",)
TAG_NAMES = ("cc3949_tag", "cc3949_incl", "cc3949_ctx")
