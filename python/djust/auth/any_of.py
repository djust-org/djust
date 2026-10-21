"""The ``permission_required`` spec: all-of, with optional "any of" groups.

``permission_required`` (the view attribute and the ``@permission_required``
handler decorator) takes:

- a string -- that one permission;
- a list or tuple -- ALL of them (Django's ``has_perms``);
- :class:`AnyOf` -- AT LEAST ONE of them;
- a list or tuple mixing strings and :class:`AnyOf` groups -- every string,
  and at least one permission of every group::

      permission_required = AnyOf("runs.agent_run", "chat.chat_general")
      permission_required = ["runs.view_run", AnyOf("runs.review", "runs.approve")]

Every place djust evaluates or reports the spec goes through this module, so
the view check, the handler check, ``reauth_on_event``, the client route map
and ``djust_audit`` agree on what it means.

:class:`AnyOf` is a tuple, so code that predates it and reads the spec as a
tuple of permissions (``user.has_perms(tuple(spec))``) requires every member:
stricter than intended, never looser.

Kept free of Django imports: ``djust.decorators`` imports it at module load.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Sequence, Tuple, Union

__all__ = [
    "AnyOf",
    "PermissionRequired",
    "PermissionSpec",
    "normalize_permission_spec",
    "permission_spec_to_document",
    "describe_permission_spec",
    "user_has_permission_spec",
]


class AnyOf(tuple):
    """At least one of these permissions (``user.has_perm`` for any member).

    ``AnyOf("a.x", "b.y")``. Needs at least one permission; every member is a
    permission string.
    """

    __slots__ = ()

    def __new__(cls, *perms: str) -> "AnyOf":
        if len(perms) == 1 and not isinstance(perms[0], str):
            # AnyOf(["a.x", "b.y"]) reads the same as AnyOf("a.x", "b.y").
            perms = tuple(perms[0])
        if not perms:
            raise ValueError("AnyOf() needs at least one permission")
        for perm in perms:
            if not isinstance(perm, str) or not perm:
                raise TypeError("AnyOf() members must be permission strings, got %r" % (perm,))
        return super().__new__(cls, perms)

    def __repr__(self) -> str:
        return "AnyOf(%s)" % ", ".join(repr(p) for p in self)


#: What ``permission_required`` accepts (an AnyOf is itself a sequence of str).
PermissionRequired = Union[str, Sequence[Union[str, AnyOf]]]

#: ``(all_of, any_of_groups)``: every permission of ``all_of`` and at least
#: one of every group.
PermissionSpec = Tuple[Tuple[str, ...], Tuple[Tuple[str, ...], ...]]


def normalize_permission_spec(spec: Any) -> PermissionSpec:
    """Split a ``permission_required`` value into ``(all_of, any_of_groups)``.

    A falsy spec is ``((), ())``: nothing required. Raises :class:`TypeError`
    for a member that is neither a string nor an :class:`AnyOf`, so a
    malformed spec fails loudly rather than granting access.
    """
    if not spec:
        return (), ()
    if isinstance(spec, str):
        return (spec,), ()
    if isinstance(spec, AnyOf):
        return (), (tuple(spec),)
    all_of: List[str] = []
    groups: List[Tuple[str, ...]] = []
    for item in spec:
        if isinstance(item, AnyOf):
            groups.append(tuple(item))
        elif isinstance(item, str):
            all_of.append(item)
        else:
            raise TypeError(
                "permission_required members must be permission strings or "
                "AnyOf(...), got %r" % (item,)
            )
    return tuple(all_of), tuple(groups)


def user_has_permission_spec(user: Any, spec: Any) -> bool:
    """Whether ``user`` satisfies ``spec``. A missing user never does."""
    if user is None:
        return False
    all_of, groups = normalize_permission_spec(spec)
    if all_of and not user.has_perms(all_of):
        return False
    for group in groups:
        if not any(user.has_perm(perm) for perm in group):
            return False
    return True


def permission_spec_to_document(
    spec: Any,
) -> Tuple[List[str], List[List[str]]]:
    """The spec as ``(permissions, permissions_any)`` for ``permissions.yaml``.

    ``permissions`` is the all-of list; ``permissions_any`` is one list per
    any-of group. Both are sorted so the comparison ignores order.
    """
    all_of, groups = normalize_permission_spec(spec)
    return sorted(all_of), sorted(sorted(group) for group in groups)


def describe_permission_spec(spec: Union[str, Iterable[Any], None]) -> str:
    """A one-line description, e.g. ``a.x, any of (b.y | c.z)``."""
    all_of, groups = normalize_permission_spec(spec)
    parts = list(all_of)
    parts.extend("any of (%s)" % " | ".join(group) for group in groups)
    return ", ".join(parts)
