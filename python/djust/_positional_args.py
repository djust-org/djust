"""Positional arguments for native Rust tag handlers (#3664).

A Python component tag is a Django ``simple_tag`` / ``inclusion_tag``, so its
parameters can be given by position: ``{% dj_tag "Python" "primary" %}``. A
native Rust handler reads ``key=value`` strings, and used to skip every argument
without an ``=``, so the same template rendered ``<span class="tag
tag-default"></span>`` (empty) under the Rust engine.

The handler cannot repair that after the engine has resolved the operands: a
resolved ``{% dj_tag name %}`` with ``name = "variant=primary"`` arrives as the
text ``variant=primary``, identical to a keyword argument. (Before this module
that was also a hole: a positional VALUE containing ``=`` was parsed as a
keyword.) So the decision is made at template compilation, from the tokens the
template wrote, through the engine's ``rewrite_args_at_parse`` hook: every
positional token ``T`` becomes ``<param>=T`` where ``<param>`` is the name the
Python tag declares at that position. From there the engine resolves it exactly
like the keyword it stands for, so ``{% dj_tag "Python" %}`` and ``{% dj_tag
label="Python" %}`` are the same call by construction, with no per-tag list to
maintain: the names come from the Django compile function's own signature.

The checks are Django's ``parse_bits`` ones that concern positional arguments,
with its messages:

* ``'<tag>' received too many positional arguments``
* ``'<tag>' received some positional argument(s) after some keyword argument(s)``
* ``'<tag>' received multiple values for keyword argument '<param>'``: a
  parameter given twice, including once by position and once by keyword. (Django
  reports the positional-and-keyword case as a ``TypeError`` when the node
  renders, because it only notices at the Python call; this raises the same
  refusal at compilation, as a ``TemplateSyntaxError``.)

``parse_bits`` also rejects unknown keywords and missing required arguments.
Those are about keywords, not about positional use, and the native handlers have
always been lenient there (``rust_only_keys`` in the parity baseline records
it), so they are left as they were.

Raw ``@register.tag`` tags have no positional parameters: their compile
functions parse ``key=value`` only, and Django refuses a bare token. A native
handler for one is left alone: a bare literal token is still ignored there, and a
bare variable is still read as ``key=value`` pairs (tracked separately).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from django.template import TemplateSyntaxError
from django.template.base import kwarg_re

#: ``Library.simple_tag`` / ``inclusion_tag`` compile functions, as classified by
#: the ``{% load %}`` bridge (the one place that knows how to tell them apart).
_POSITIONAL_KINDS = ("simple_tag", "inclusion_tag")


@dataclass(frozen=True)
class PositionalSpec:
    """What ``parse_bits`` knows about one tag's positional parameters."""

    #: The tag's name as Django reports it in errors (``function_name``).
    name: str
    #: Positional-or-keyword parameter names in declaration order, ``context``
    #: already removed for a ``takes_context`` tag.
    params: Tuple[str, ...]
    #: ``*args``: positional arguments past ``params`` are accepted.
    has_varargs: bool
    #: ``simple_tag`` takes a trailing ``as <var>``; ``inclusion_tag`` does not.
    takes_as_var: bool


def _closure(func: Callable[..., Any]) -> Dict[str, Any]:
    code = getattr(func, "__code__", None)
    cells = getattr(func, "__closure__", None) or ()
    if code is None:
        return {}
    return dict(zip(code.co_freevars, (cell.cell_contents for cell in cells)))


def positional_spec(tag_name: str, compile_func: Callable[..., Any]) -> Optional[PositionalSpec]:
    """The positional contract of a Django library tag, or ``None``.

    ``None`` for anything that is not a ``simple_tag`` / ``inclusion_tag``: a
    raw ``@register.tag`` has no parameter list to map onto.
    """
    from djust.template_libraries import _classify

    kind = _classify(compile_func)
    if kind not in _POSITIONAL_KINDS:
        return None
    wrapped = inspect.unwrap(getattr(compile_func, "__wrapped__", compile_func))
    spec = inspect.getfullargspec(wrapped)
    params = list(spec.args)
    closure = _closure(compile_func)
    takes_context = closure.get("takes_context")
    if takes_context is None:
        takes_context = bool(params) and params[0] == "context"
    if takes_context and params and params[0] == "context":
        params = params[1:]
    return PositionalSpec(
        name=str(closure.get("function_name") or tag_name),
        params=tuple(params),
        has_varargs=spec.varargs is not None,
        takes_as_var=kind == "simple_tag",
    )


def rewrite_positional(spec: PositionalSpec, args: List[str]) -> List[str]:
    """``args`` with every positional token named after its parameter.

    Mirrors the positional half of Django's ``parse_bits``. A token is a keyword
    exactly when Django's ``token_kwargs`` says so (``kwarg_re``): ``name=...``
    with at least one character after the ``=``. Everything else is positional,
    including a quoted literal that contains ``=`` and a filter argument.
    """
    bits = list(args)
    tail: List[str] = []
    if spec.takes_as_var and len(bits) >= 2 and bits[-2] == "as":
        # ``{% tag ... as var %}``: Django strips it before looking at the rest.
        tail, bits = bits[-2:], bits[:-2]
    rewritten: List[str] = []
    named: Set[str] = set()
    keyword_seen = False
    position = 0
    for bit in bits:
        match = kwarg_re.match(bit)
        if match and match[1]:
            keyword_seen = True
            if match[1] in named:
                raise TemplateSyntaxError(
                    "'%s' received multiple values for keyword argument '%s'"
                    % (spec.name, match[1])
                )
            named.add(match[1])
            rewritten.append(bit)
            continue
        if keyword_seen:
            raise TemplateSyntaxError(
                "'%s' received some positional argument(s) after some keyword argument(s)"
                % spec.name
            )
        if position < len(spec.params):
            param = spec.params[position]
            named.add(param)
            rewritten.append("%s=%s" % (param, bit))
        elif spec.has_varargs:
            # ``*args`` of the Python tag. A native handler that reads them
            # (``{% kbd %}``) reads the raw tokens; there is no name to give.
            rewritten.append(bit)
        else:
            raise TemplateSyntaxError("'%s' received too many positional arguments" % spec.name)
        position += 1
    return rewritten + tail


class PositionalTagHandler:
    """An inline handler plus the engine's ``rewrite_args_at_parse`` hook.

    Wraps the handler rather than patching it: a handler class may be shared and
    its ``render`` is read by tests directly. Everything else the engine reads
    off a handler (``RESOLVE_ARG_POSITIONS``, ``validate_at_parse``, ...) is
    forwarded.
    """

    def __init__(self, handler: Any, spec: PositionalSpec) -> None:
        self._handler = handler
        self.spec = spec

    def rewrite_args_at_parse(self, args: List[str]) -> List[str]:
        return rewrite_positional(self.spec, args)

    def render(self, args: List[str], context: Dict[str, Any]) -> Any:
        return self._handler.render(args, context)

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not found on the wrapper itself.
        if name == "_handler":
            raise AttributeError(name)
        return getattr(self._handler, name)


def with_positional_args(tag_name: str, handler: Any, library: Any) -> Any:
    """``handler``, wrapped so the tag accepts its Python tag's positional parameters.

    ``library`` is the Django ``Library`` the Python tag lives in. A tag the
    library does not have, or a raw ``@register.tag``, comes back unchanged.
    """
    compile_func = library.tags.get(tag_name)
    if compile_func is None:
        return handler
    spec = positional_spec(tag_name, compile_func)
    if spec is None:
        return handler
    return PositionalTagHandler(handler, spec)
