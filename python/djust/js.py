"""
Server-side helper for building JS Command chains.

``djust.js.JS`` mirrors Phoenix LiveView's ``Phoenix.LiveView.JS`` module:
build up a chain of DOM operations server-side, stringify it, and bind
it to an attribute like ``dj-click``. When the user triggers the event
the client runs the chain locally without a server round-trip.

Example::

    from djust.js import JS

    class ModalView(LiveView):
        def mount(self, request, **kwargs):
            self.open_modal = JS.show("#modal").add_class("active", to="#overlay")
            self.close_modal = JS.hide("#modal").remove_class("active", to="#overlay")

    # In the template:
    #   <button dj-click="{{ open_modal }}">Open modal</button>
    #   <button dj-click="{{ close_modal }}">Close modal</button>

Every chain method returns a **new** ``JSChain`` so chains are safe to
share across template calls. ``str(chain)`` emits a JSON array that the
client's ``window.djust.js`` interpreter understands, and the value is
wrapped in Django's ``SafeString`` so Django's auto-escape machinery
doesn't double-encode the quotes (JSON strings already escape).

Eleven commands are supported, matching Phoenix LiveView 1.0:

* ``show`` / ``hide`` / ``toggle`` — visibility toggles
* ``add_class`` / ``remove_class`` — class list mutations
* ``transition`` — apply a CSS class for a duration (used for animations)
* ``dispatch`` — fire a ``CustomEvent`` on the target
* ``focus`` — move keyboard focus
* ``set_attr`` / ``remove_attr`` — attribute mutations
* ``push`` — send a server event (like ``dj-click="search"`` but in a chain)

Every command accepts a ``to``, ``inner``, or ``closest`` keyword to
target a different element than the event origin:

* ``to="#id"``   — absolute CSS selector (document.querySelectorAll)
* ``inner=".x"`` — scoped to the event origin's children
* ``closest=".m"`` — walk up the DOM from the origin to find a match

See ``docs/website/guides/js-commands.md`` for the full reference.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.utils.safestring import mark_safe


def _normalise_target(
    to: Optional[str] = None,
    inner: Optional[str] = None,
    closest: Optional[str] = None,
) -> Dict[str, str]:
    """Build the target kwargs dict.

    At most one of ``to`` / ``inner`` / ``closest`` may be supplied; if
    none is set the command targets the event origin element itself.
    """
    supplied = [k for k in ("to", "inner", "closest") if locals()[k]]
    if len(supplied) > 1:
        raise ValueError(
            f"JS commands accept at most one of to=, inner=, closest= — got {', '.join(supplied)}"
        )
    out: Dict[str, str] = {}
    if to:
        out["to"] = to
    if inner:
        out["inner"] = inner
    if closest:
        out["closest"] = closest
    return out


@dataclass(frozen=True)
class JSChain:
    """An immutable chain of JS Command operations.

    ``str(chain)`` emits a JSON-serialised list of ``[op, args]`` pairs,
    wrapped in ``SafeString`` so Django templates don't HTML-escape the
    quotes (the client parser needs them intact).
    """

    ops: List[List[Any]] = field(default_factory=list)

    def _append(self, op: str, args: Dict[str, Any]) -> "JSChain":
        return JSChain(ops=self.ops + [[op, args]])

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def show(
        self,
        to: Optional[str] = None,
        *,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
        display: Optional[str] = None,
        transition: Optional[str] = None,
        time: Optional[int] = None,
    ) -> "JSChain":
        """Set the target element's display to ``display`` (default ``block``)."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        if display is not None:
            args["display"] = display
        if transition is not None:
            args["transition"] = transition
        if time is not None:
            args["time"] = int(time)
        return self._append("show", args)

    def hide(
        self,
        to: Optional[str] = None,
        *,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
        transition: Optional[str] = None,
        time: Optional[int] = None,
    ) -> "JSChain":
        """Set the target element's display to ``none``."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        if transition is not None:
            args["transition"] = transition
        if time is not None:
            args["time"] = int(time)
        return self._append("hide", args)

    def toggle(
        self,
        to: Optional[str] = None,
        *,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
        display: Optional[str] = None,
    ) -> "JSChain":
        """Toggle the target element between hidden and shown."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        if display is not None:
            args["display"] = display
        return self._append("toggle", args)

    # ------------------------------------------------------------------
    # Class mutations
    # ------------------------------------------------------------------

    def add_class(
        self,
        names: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
    ) -> "JSChain":
        """Add one or more space-separated CSS class names to the target."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["names"] = names
        return self._append("add_class", args)

    def remove_class(
        self,
        names: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
    ) -> "JSChain":
        """Remove one or more space-separated CSS class names from the target."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["names"] = names
        return self._append("remove_class", args)

    def transition(
        self,
        names: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
        time: int = 200,
    ) -> "JSChain":
        """Apply ``names`` as CSS classes for ``time`` ms, then remove them.

        Used to trigger CSS transitions — e.g.
        ``JS.transition("fade-in", time=300)`` adds the ``fade-in`` class,
        waits 300 ms for the animation, then removes it again.
        """
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["names"] = names
        args["time"] = int(time)
        return self._append("transition", args)

    # ------------------------------------------------------------------
    # Attribute mutations
    # ------------------------------------------------------------------

    def set_attr(
        self,
        name: str,
        value: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
    ) -> "JSChain":
        """Set an HTML attribute on the target element."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["attr"] = [name, value]
        return self._append("set_attr", args)

    def remove_attr(
        self,
        name: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
    ) -> "JSChain":
        """Remove an HTML attribute from the target element."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["attr"] = name
        return self._append("remove_attr", args)

    # ------------------------------------------------------------------
    # Misc DOM ops
    # ------------------------------------------------------------------

    def focus(
        self,
        to: Optional[str] = None,
        *,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
    ) -> "JSChain":
        """Move keyboard focus to the target element."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        return self._append("focus", args)

    def dispatch(
        self,
        event: str,
        *,
        to: Optional[str] = None,
        inner: Optional[str] = None,
        closest: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        bubbles: bool = True,
    ) -> "JSChain":
        """Fire a ``CustomEvent`` with the given name on the target."""
        args = _normalise_target(to=to, inner=inner, closest=closest)
        args["event"] = event
        if detail is not None:
            args["detail"] = detail
        args["bubbles"] = bool(bubbles)
        return self._append("dispatch", args)

    # ------------------------------------------------------------------
    # Server push
    # ------------------------------------------------------------------

    def push(
        self,
        event: str,
        *,
        value: Optional[Dict[str, Any]] = None,
        target: Optional[str] = None,
        page_loading: bool = False,
    ) -> "JSChain":
        """Send a server event as part of a JS command chain.

        This is the bridge between client-only JS Commands (fast, local)
        and server round-trips — use it when you need to mix
        "optimistically close the modal, then tell the server the user
        saved the form" in a single click handler.

        ``page_loading=True`` shows the navigation-level loading bar
        (``dj-page-loading`` elements) while the event is in flight.
        """
        args: Dict[str, Any] = {"event": event}
        if value is not None:
            args["value"] = value
        if target is not None:
            args["target"] = target
        if page_loading:
            args["page_loading"] = True
        return self._append("push", args)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(self.ops, separators=(",", ":"))

    def __str__(self) -> str:
        return self.to_json()

    def __html__(self) -> str:
        """Return the JSON payload as a Django SafeString.

        ``{{ cmd }}`` in a template invokes ``__html__`` under Django's
        ``conditional_escape``, so the JSON comes through with its
        quotes intact and can be dropped directly into an attribute
        value. JSON already escapes ``<``, ``>``, ``"``, etc. so the
        output is safe for HTML attribute context.
        """
        return mark_safe(self.to_json())


class _JSFactory:
    """Chain-starting factory — every public attribute is a class method
    that returns a fresh ``JSChain`` with the operation pre-appended.

    Usage::

        JS.show("#modal").add_class("active", to="#overlay")

    The factory is stateless; it's really just a typing convenience so
    call sites don't have to type ``JSChain().show(...)``.
    """

    # The factory's methods mirror JSChain's in both signature and
    # semantics — they just start from an empty chain.
    def show(self, *args, **kwargs) -> JSChain:
        return JSChain().show(*args, **kwargs)

    def hide(self, *args, **kwargs) -> JSChain:
        return JSChain().hide(*args, **kwargs)

    def toggle(self, *args, **kwargs) -> JSChain:
        return JSChain().toggle(*args, **kwargs)

    def add_class(self, *args, **kwargs) -> JSChain:
        return JSChain().add_class(*args, **kwargs)

    def remove_class(self, *args, **kwargs) -> JSChain:
        return JSChain().remove_class(*args, **kwargs)

    def transition(self, *args, **kwargs) -> JSChain:
        return JSChain().transition(*args, **kwargs)

    def set_attr(self, *args, **kwargs) -> JSChain:
        return JSChain().set_attr(*args, **kwargs)

    def remove_attr(self, *args, **kwargs) -> JSChain:
        return JSChain().remove_attr(*args, **kwargs)

    def focus(self, *args, **kwargs) -> JSChain:
        return JSChain().focus(*args, **kwargs)

    def dispatch(self, *args, **kwargs) -> JSChain:
        return JSChain().dispatch(*args, **kwargs)

    def push(self, *args, **kwargs) -> JSChain:
        return JSChain().push(*args, **kwargs)


JS = _JSFactory()

__all__ = ["JS", "JSChain"]
