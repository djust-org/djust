"""Call a native component handler the way the Rust engine calls it (#3882, #3883).

A test that calls a handler's ``render`` directly used to pass a raw token (``["rows=rows"]``) and
rely on the handler looking the NAME up in the context. The engine never hands a handler a name:
it resolves every operand first, and a handler that looked a resolved value up again read another
context variable (#3882). The handler no longer does, so a direct call has to hand it what the
engine hands it:

* a list or dict as the JSON text of its ``key=`` argument (the engine encodes a container as
  JSON and does not hand it over a second time);
* any other value as its text, and as the value itself on the typed channel
  (``TYPED_ARGS_KEY``), which is what the handler reads.

``engine_args(args, context)`` rewrites a raw-token call into that shape; a quoted literal, a
number and an argument whose token is not in ``context`` are left as written.
"""

from __future__ import annotations

import json
from typing import Any

from djust.template_tags import TYPED_ARGS_KEY


def engine_args(args: list[str], context: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    out: list[str] = []
    typed: dict[str, Any] = dict(context.get(TYPED_ARGS_KEY) or {})
    for arg in args:
        key, sep, token = arg.partition("=")
        if not sep or token[:1] in ("'", '"') or token not in context:
            out.append(arg)
            continue
        value = context[token]
        if isinstance(value, (list, dict)):
            out.append("%s=%s" % (key, json.dumps(value, default=str)))
        else:
            out.append("%s=%s" % (key, value))
            typed[key.strip()] = value
    return out, {**context, TYPED_ARGS_KEY: typed}


def native_render(
    handler: Any, args: list[str], context: dict[str, Any], content: Any = None
) -> str:
    """``handler.render`` with ``args`` / ``context`` as the engine would hand them; a block handler
    when ``content`` is given."""
    args, context = engine_args(args, context)
    if content is None:
        return str(handler.render(args, context))
    return str(handler.render(args, content, context))
