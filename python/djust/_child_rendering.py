"""Render-scoped ownership reconciliation for gated explicit children.

Only server-invoked child renderers create candidates. Markup is used to decide
which candidates survived the completed render, never to construct a component
or accept an identifier from a client event. Nested plans commit only when the
outer render succeeds; a root-only update preserves known page-shell children.
"""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature
from typing import Any, ParamSpec, Protocol, TypeVar

from ._exposure import ExposureError, uses_legacy_exposure

_P = ParamSpec("_P")
_R = TypeVar("_R")


class _ViewRenderer(Protocol):
    def render_with_diff(self, *args: Any, **kwargs: Any) -> tuple[str, str | None, int]: ...

    def render_full_template(self, *args: Any, **kwargs: Any) -> str: ...


@dataclass
class _Render:
    owner: Any
    whole_page: bool
    owner_wrapper: bool
    candidates: dict[str, Any] = field(default_factory=dict)
    plans: list[Any] = field(default_factory=list)
    regions: dict[str, tuple[Any, str]] = field(default_factory=dict)

    def finish(self, html: str) -> None:
        if not self.candidates:
            return
        from ._rust import child_render_regions

        try:
            regions = child_render_regions(
                html, list(self.candidates), self.whole_page, self.owner_wrapper
            )
        except ValueError:
            raise ExposureError("Invalid rendered child ownership") from None
        self.regions = {slot: (self.candidates[slot], region) for slot, region in regions.items()}

    def release(self) -> None:
        """Drop the view and its children once the render is over (#3116).

        The plan is the value of a ContextVar while the render runs, so every
        copy of the context taken then (a task, or a thread on 3.14+) keeps it;
        emptying it here stops such a copy from keeping the session alive.
        """
        self.owner = None
        self.candidates.clear()
        self.plans.clear()
        self.regions.clear()

    def commit(self) -> None:
        from ._child_lifecycle import dispose_child_subtree

        if getattr(self.owner, "_djust_child_disposed", False):
            return
        registry = getattr(self.owner, "_child_views", {})
        if type(registry) is not dict:
            raise ExposureError("Invalid rendered child registry")
        keep = dict(self.regions)
        if not self.whole_page:
            previous = getattr(self.owner, "_explicit_child_render_regions", {})
            for slot, (child, region) in previous.items():
                if region == "shell" and registry.get(slot) is child and slot not in keep:
                    keep[slot] = (child, region)
        if any(registry.get(slot) is not child for slot, (child, _) in keep.items()):
            raise ExposureError("Rendered child ownership changed")
        self.owner._explicit_child_render_regions = keep
        self.owner._explicit_child_render_scope = "full" if self.whole_page else "root"
        if self.whole_page:
            self.owner._explicit_child_rendered_full = True
        for slot, child in list(registry.items()):
            if (
                slot not in keep
                and not uses_legacy_exposure(child)
                and type(getattr(child, "_explicit_child_schema", None)) is str
            ):
                dispose_child_subtree(child)


_active: ContextVar[_Render | None] = ContextVar("djust_explicit_child_render", default=None)


def record_rendered_child(child: Any) -> None:
    """Record a currently registered server-rendered child, not a markup-only ID."""
    active = _active.get()
    if active is None or getattr(child, "_parent_view", None) is not active.owner:
        return
    if uses_legacy_exposure(child):
        return
    slot = getattr(child, "_view_id", None)
    registry = getattr(active.owner, "_child_views", None)
    if type(slot) is not str or type(registry) is not dict or registry.get(slot) is not child:
        raise ExposureError("Rendered child is not registered")
    active.candidates[slot] = child


def reconcile_child_render(
    *, whole_page: bool = False, owner_wrapper: bool = False
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Wrap a server renderer while preserving its public signature for tooling."""

    def decorate(render: Callable[_P, _R]) -> Callable[_P, _R]:
        owner_parameter = next(iter(signature(render).parameters))

        @wraps(render)
        def rendered(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            if not args and owner_parameter not in kwargs:
                return render(*args, **kwargs)  # retain the callable's normal argument error
            view = args[0] if args else kwargs[owner_parameter]
            previous = _active.get()
            if uses_legacy_exposure(view) or (previous is not None and previous.owner is view):
                return render(*args, **kwargs)
            current = _Render(view, whole_page, owner_wrapper)
            token = _active.set(current)
            done = False
            try:
                result = render(*args, **kwargs)
                html = result[0] if isinstance(result, tuple) else result
                if not isinstance(html, str):
                    raise ExposureError("Explicit child renderer did not return HTML")
                if (
                    current.whole_page
                    and getattr(view, "template_name", None)
                    and not getattr(view, "template", None)
                    and getattr(view, "_full_template", None) is None
                ):
                    # Inheritance resolution can deliberately fall back to a
                    # live-root fragment. That is not a completed page shell.
                    current.whole_page = False
                current.finish(html)
                done = True
            finally:
                _active.reset(token)
                if not done:
                    for plan in [*current.plans, current]:
                        plan.release()
            if previous is not None:
                previous.plans.extend([*current.plans, current])
            else:
                plans = [*current.plans, current]
                try:
                    committed: set[int] = set()
                    for plan in reversed(plans):
                        if id(plan.owner) not in committed:
                            plan.commit()
                            committed.add(id(plan.owner))
                finally:
                    for plan in plans:
                        plan.release()
            if owner_wrapper:
                record_rendered_child(view)
            return result

        return rendered

    return decorate


@reconcile_child_render()
def render_view_with_diff(
    view: _ViewRenderer, *args: Any, **kwargs: Any
) -> tuple[str, str | None, int]:
    """Keep subclass overrides inside the transport's successful-render boundary."""
    return view.render_with_diff(*args, **kwargs)


@reconcile_child_render(whole_page=True)
def render_view_full_template(view: _ViewRenderer, *args: Any, **kwargs: Any) -> str:
    """Keep full-page overrides inside the successful-render boundary too."""
    return view.render_full_template(*args, **kwargs)
