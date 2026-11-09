"""Django regroup compilation with typed source rows and named tuple results."""

from typing import Any

from django.template.base import Node, Parser, Token
from django.template.defaulttags import regroup

from . import register
from ._builtin import DjangoBuiltinTagHandler


class _ModelRefusingSource:
    """The ``RegroupNode`` source, refusing a model the way ``groupby()`` does.

    Django resolves the source and hands it to ``groupby()``, which iterates it; a model instance
    is not iterable, so ``{% regroup r by name as g %}`` over ONE model raises ``TypeError``. A
    model that crossed the engine as its serialized map is iterable (as its key names), so the
    resolved value is checked here, at the same seam, after any filters ran. A list, tuple or
    QuerySet of models is read element-wise and an ordinary dict is a dict; neither is touched.
    """

    __slots__ = ("_source",)

    def __init__(self, source: Any) -> None:
        self._source = source

    def resolve(self, context: Any, ignore_failures: bool = False) -> Any:
        value = self._source.resolve(context, ignore_failures=ignore_failures)
        if isinstance(value, dict):
            # Lazy: the package import set is pinned (test_lazy_package_init_2559).
            from ..serialization import is_serialized_model_map

            if is_serialized_model_map(value):
                raise TypeError("'%s' object is not iterable" % value["__model__"])
        return value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


def _compile(parser: Parser, token: Token) -> Node:
    node = regroup(parser, token)
    node.target = _ModelRefusingSource(node.target)  # type: ignore[attr-defined]
    return node


@register("regroup")
class RegroupTagHandler(DjangoBuiltinTagHandler):
    ACCEPTS_AS_VAR = True

    def __init__(self) -> None:
        super().__init__("regroup", _compile)
