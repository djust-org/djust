"""A model row a view reads by its id, on every turn.

A view keeps the row's id, never the row: between events the HTTP fallback
restores a view's saved attributes without running ``mount()`` again, and a
saved model instance comes back as its field dict, not a row. An id comes back
as the id, and this descriptor reads the row from it (once per turn: the row
it read is reused while the id is unchanged and the cached copy is a row).
"""

from typing import Any, Callable, Optional


class RowById:
    """``_lease = RowById("_lease_id", lambda pk: Lease.objects.filter(pk=pk).first())``."""

    def __init__(self, id_attr: str, load: Callable[[Any], Optional[Any]]):
        self.id_attr = id_attr
        self.load = load
        self.cache = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.cache = "_%s_row" % name.lstrip("_")

    def __get__(self, view: Any, owner: Optional[type] = None) -> Any:
        if view is None:
            return self
        from django.db.models import Model

        pk = getattr(view, self.id_attr, None)
        cached = view.__dict__.get(self.cache)
        # (id, row): a restored copy carries a field dict, never a row, so it
        # never passes for one.
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] == pk
            and (cached[1] is None or isinstance(cached[1], Model))
        ):
            return cached[1]
        row = self.load(pk) if pk is not None else None
        view.__dict__[self.cache] = (pk, row)
        return row
