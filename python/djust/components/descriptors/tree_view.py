"""
TreeView — descriptor for which nodes of ``{% tree_view %}`` are expanded (#4134).

Usage::

    class MyPage(LiveView):
        tree = TreeView(expanded=["docs"])

    # template: {% tree_view nodes=nodes expanded=tree.expanded %}
    # ``tree_expand`` answered here, no app handler needed.
"""

from djust.decorators import event_handler

from .base import LiveComponent, TypedState, attribute_name, bound_state, warn_once
from typing import Any, Optional, Sequence


__all__ = ["TreeView"]


#: The longest node id a click may name.
MAX_ID_LENGTH = 256
#: How many nodes may be expanded at once; a click past this is refused.
MAX_EXPANDED = 10_000


def _node_id(value: Any) -> Optional[str]:
    """The node id a click names, or None when it cannot be one.

    A node id is a non-empty string (or an integer, compared as its text) of at
    most ``MAX_ID_LENGTH`` characters; a boolean, a float, a container or an
    empty value is refused.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH:
        return None
    return value


class TreeView(LiveComponent):
    """The ids of the expanded nodes of ``{% tree_view %}``.

    Pass ``expanded=tree.expanded`` to the tag: it then decides which nodes are
    open, and each node's own ``"expanded"`` key is not read. Seed it with the
    nodes open at first, ``TreeView(expanded=["docs"])``. Selecting a node
    (``tree_select``) stays the application's: what a selection does is its
    business. Assign a new list to change it in code
    (``self.tree.expanded = [*self.tree.expanded, "api"]``)."""

    class State(TypedState):
        expanded: Sequence[str] = ()

    class Meta:
        # The ``{% tree_view %}`` tag's default ``expand_event`` (#4134).
        events = ("tree_expand",)

    @event_handler
    def tree_expand(self, value: Any = "", **kwargs: Any) -> None:
        """Expand node ``value`` if it is collapsed, collapse it if it is expanded.

        A value that cannot be a node id is refused, and the first refusal logs a
        warning naming the component; so is expanding past ``MAX_EXPANDED`` nodes.
        """
        state = bound_state(self)
        node = _node_id(value)
        name = attribute_name(self)
        if node is None:
            warn_once(
                self,
                "id",
                "TreeView %r refused a tree_expand value of type %s: a node id is a "
                "non-empty string of at most %s characters.",
                name,
                type(value).__name__,
                MAX_ID_LENGTH,
            )
            return
        current = [str(i) for i in (state.expanded or ())]
        if node in current:
            state.expanded = [i for i in current if i != node]
            return
        if len(current) >= MAX_EXPANDED:
            warn_once(
                self,
                "count",
                "TreeView %r refused tree_expand: %s nodes are already expanded.",
                name,
                MAX_EXPANDED,
            )
            return
        state.expanded = [*current, node]
