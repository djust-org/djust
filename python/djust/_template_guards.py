"""Django's template-callable guard markers (#2507).

A leaf module by design: it imports nothing from djust, so
``djust.components.base`` and ``djust._context_provider`` can both use it
without touching the ``live_view -> components.base`` cycle that
``_context_provider`` was extracted to break. ``djust.decorators``
re-exports :func:`alters_data` as the public spelling.
"""

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def alters_data(func: F) -> F:
    """Stamp Django's ``alters_data`` marker on a mutating method.

    Django's ``Variable._resolve_lookup`` refuses to auto-call any callable
    carrying ``alters_data`` — it is what stops ``{{ obj.delete }}`` in a
    template from deleting the row, and Django stamps it on ``Model.save`` /
    ``Model.delete`` / ``QuerySet.delete`` / ``QuerySet.update`` for exactly
    that reason. djust honours the same marker in ONE place,
    ``Context::maybe_call`` (``crates/djust_core/src/context.rs``), so a
    method wearing it is never invoked by a template on any render path.

    Spelled as a decorator rather than Django's ``method.alters_data = True``
    trailer because ``djust.components.base`` is an ADR-023 strict-typing
    island, where assigning an attribute to a function is an ``[attr-defined]``
    error. ``setattr`` keeps the marker out of the type checker's way while
    producing the identical runtime shape: ``getattr(bound_method,
    "alters_data", False)`` is ``True``, because attribute lookup on a bound
    method falls through to the underlying function.

    Marks the method for TEMPLATE auto-call only. Direct Python calls —
    ``component.update(**props)`` from ``ComponentMixin.update_component``,
    the framework's own ``mount`` / ``unmount`` lifecycle dispatch — are
    completely unaffected.
    """
    setattr(func, "alters_data", True)
    return func


#: Method names a template must never auto-call on a component (#2507).
#:
#: Every one mutates: ``mount`` / ``unmount`` run the component's lifecycle,
#: ``update`` rewrites its props, ``trigger_update`` re-enters the PARENT
#: view's ``_trigger_update`` mid-render, and ``clear_context_providers``
#: empties the provider registry the rest of the render reads.
#:
#: ``render`` and ``get_context_data`` are deliberately ABSENT: they are
#: read-only, ``{{ c.render }}`` is a documented spelling (#2501), and
#: stamping them would break the very cell that issue exists to fix.
ALTERS_DATA_COMPONENT_METHODS = frozenset(
    {"mount", "unmount", "update", "trigger_update", "clear_context_providers"}
)


class TemplateMutatorGuard:
    """Re-stamp ``alters_data`` on every subclass override of a mutator.

    ``alters_data`` is an attribute of the FUNCTION, so a subclass that
    overrides the method gets a fresh function object without it — and
    overriding is not an edge case here, it is the documented way to use a
    component (``unmount``'s own docstring says *"Override this method to
    perform cleanup actions"*). Stamping only the base methods therefore
    guards almost nobody: the measured case was a component whose overridden
    ``unmount`` ran its user-authored cleanup during ``{{ c.unmount }}``,
    with the base method already marked.

    So the guard has to attach to the NAME on the class rather than to one
    function object. ``__init_subclass__`` runs at class-creation time for
    every subclass at any depth, which makes the marker a property of the
    method name for the whole hierarchy — the structural cure rather than N
    correct copies (#1646).

    A subclass that deliberately wants a mutator template-callable can undo
    it explicitly with ``setattr(MyComponent.thing, "alters_data", False)``;
    nothing here is a lock, only a default that fails closed.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name in ALTERS_DATA_COMPONENT_METHODS:
            # `cls.__dict__`, not `getattr`: an INHERITED method already
            # carries the marker, and re-stamping it through `getattr` would
            # walk to the base function and set the attribute a second time
            # for every subclass ever defined.
            attr = cls.__dict__.get(name)
            if attr is None:
                continue
            # A `staticmethod` / `classmethod` wrapper is not the function the
            # guard is read from; Django's check sees the underlying one.
            func = getattr(attr, "__func__", attr)
            if callable(func):
                alters_data(func)
