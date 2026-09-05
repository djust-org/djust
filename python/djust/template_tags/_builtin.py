"""Adapt a built-in Django tag to the shared library compilation/render bridge."""

from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple

from . import TagHandler


class DjangoBuiltinTagHandler(TagHandler):
    RESOLVE_ARG_POSITIONS: ClassVar[Optional[Set[int]]] = set()
    RETURNS_BINDINGS: ClassVar[bool] = True
    WANTS_AUTOESCAPE: ClassVar[bool] = True

    def __init__(self, name: str, compile_func: Callable[..., Any]) -> None:
        from ..template_libraries import LibraryTagHandler

        self._bridge = LibraryTagHandler("django builtins", name, compile_func)

    def validate_at_parse(self, args: List[str]) -> None:
        self._bridge.validate_at_parse(args)

    def render(
        self, args: List[str], context: Dict[str, Any], autoescape: bool = True
    ) -> Tuple[str, Dict[str, Any]]:
        return self._bridge.render(args, context, autoescape)
