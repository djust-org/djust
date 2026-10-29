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
from secrets import token_hex
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
    direct_results: dict[int, tuple[str, Any]] = field(default_factory=dict)
    issued_children: dict[str, Any] = field(default_factory=dict)
    owned_preparing: bool = False
    prepared_owned_html: Any = None
    pending_owned_version: int | None = None
    vdom_bindings: dict[str, Any] = field(default_factory=dict)

    def finish(self, html: str) -> None:
        if self.owned_preparing:
            self.finish_owned(html)
            return
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

    def finish_owned(self, html: str) -> None:
        from ._render_provenance import RenderedDiffHTML

        if not isinstance(html, RenderedDiffHTML) or html is not self.prepared_owned_html:
            raise ExposureError("Pending child ownership proof was lost")
        if html.diff_version != self.pending_owned_version:
            raise ExposureError("Pending child ownership version changed")
        issued = html.issued_children
        if len(issued) != len(set(issued)) or set(issued) != set(self.issued_children):
            raise ExposureError("Child issuance ledger is incomplete")
        registry = getattr(self.owner, "_child_views", {})
        previous = getattr(self.owner, "_explicit_child_vdom_bindings", {})
        if type(registry) is not dict or type(previous) is not dict:
            raise ExposureError("Invalid child binding registry")
        self.regions = {}
        self.vdom_bindings = {}
        for key, node_id in html.fresh_child_ids:
            child = self.issued_children.get(key)
            slot = getattr(child, "_view_id", None)
            if (
                child is None
                or type(slot) is not str
                or registry.get(slot) is not child
                or slot in self.regions
                or node_id in self.vdom_bindings
            ):
                raise ExposureError("Fresh child binding changed")
            self.regions[slot] = (child, "root")
            self.vdom_bindings[node_id] = child
        for old_id, node_id in html.retained_child_ids:
            child = previous.get(old_id)
            slot = getattr(child, "_view_id", None)
            if (
                child is None
                or type(slot) is not str
                or registry.get(slot) is not child
                or slot in self.regions
                or node_id in self.vdom_bindings
            ):
                raise ExposureError("Retained child binding changed")
            self.regions[slot] = (child, "root")
            self.vdom_bindings[node_id] = child

    def release(self) -> None:
        """Drop the view and its children once the render is over (#3116).

        The plan is the value of a ContextVar while the render runs, so every
        copy of the context taken then (a task, or a thread on 3.14+) keeps it;
        emptying it here stops such a copy from keeping the session alive.
        """
        if self.owned_preparing and self.owner is not None:
            self.owner._rust_view.discard_authenticated_page()
        self.owner = None
        self.prepared_owned_html = None
        self.vdom_bindings.clear()
        self.candidates.clear()
        self.plans.clear()
        self.regions.clear()
        self.direct_results.clear()
        self.issued_children.clear()

    def commit(self) -> None:
        from ._child_lifecycle import dispose_child_subtree

        if getattr(self.owner, "_djust_child_disposed", False):
            if self.owned_preparing:
                raise ExposureError("Pending child owner was disposed")
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
        if self.owned_preparing:
            if self.pending_owned_version is None:
                raise ExposureError("Pending child version is unavailable")
            self.owner._rust_view.commit_owned_diff(self.pending_owned_version)
            self.owned_preparing = False
            self.owner._explicit_child_vdom_bindings = dict(self.vdom_bindings)
            self.owner._explicit_child_vdom_tracking = True
            self.owner._pending_authenticated_page = False
            self.owner._refresh_cached_rust_view()
            self.owner._record_dj_model_fields_from_rust(self.owner._rust_view)
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


def owned_diff_active(view: Any) -> bool:
    active = _active.get()
    return active is not None and active.owner is view and not active.whole_page


def prepare_owned_child_diff(view: Any) -> tuple[str, str | None, int]:
    from ._render_provenance import RenderedDiffHTML

    active = _active.get()
    if active is None or active.owner is not view or active.whole_page:
        raise ExposureError("Child diff scope is unavailable")
    previous = getattr(view, "_explicit_child_vdom_bindings", {})
    if type(previous) is not dict:
        raise ExposureError("Invalid previous child bindings")
    active.owned_preparing = True
    html, patches, version, (fresh, retained, issued) = view._rust_view.prepare_owned_diff(
        list(previous)
    )
    result = RenderedDiffHTML(html, tuple(fresh), tuple(retained), tuple(issued), version)
    active.prepared_owned_html = result
    active.pending_owned_version = version
    return result, patches, version


def record_direct_child_result(child: Any, html: Any) -> None:
    """Retain the exact direct result, never a later string copy or slot lookup."""
    active = _active.get()
    if (
        active is None
        or getattr(child, "_parent_view", None) is not active.owner
        or uses_legacy_exposure(child)
    ):
        return
    if not isinstance(html, str):
        raise ExposureError("Invalid direct child render result")
    active.direct_results[id(html)] = (html, child)


def consume_direct_child_result(parent: Any, html: str) -> tuple[str, int] | None:
    """Issue a location capability only for the exact registered callback result."""
    active = _active.get()
    if active is None or active.owner is not parent:
        return None
    entry = active.direct_results.pop(id(html), None)
    if entry is None:
        return None  # A legacy child does not acquire an explicit binding.
    original, child = entry
    registry = getattr(parent, "_child_views", None)
    slot = getattr(child, "_view_id", None)
    if (
        original is not html
        or type(registry) is not dict
        or type(slot) is not str
        or registry.get(slot) is not child
    ):
        raise ExposureError("Direct child render ownership changed")
    from ._rust import child_wrapper_opening_end

    try:
        end = child_wrapper_opening_end(html, slot)
    except ValueError:
        raise ExposureError("Invalid direct child wrapper") from None
    key = token_hex(16)
    while key in active.issued_children:
        key = token_hex(16)
    active.issued_children[key] = child
    return key, end


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
                record_direct_child_result(view, result)
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
