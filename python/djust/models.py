"""djust's own tables.

:class:`ViewState` holds view state for
:class:`djust.state_store.DatabaseViewStateStore`: the payload bytes djust
built, under the key it built, and when they expire. Nothing in a row is read
without the index entry the session keeps for it, so a row on its own restores
nothing (see :mod:`djust.state_store`).
"""

from __future__ import annotations

from django.db import models


class ViewState(models.Model):
    """One saved view state (``DatabaseViewStateStore``)."""

    #: ``StateKey.id``: 64 hex characters, unique per session, page and tab.
    key = models.CharField(max_length=64, primary_key=True)
    payload = models.BinaryField()
    #: Unix time after which djust does not read the row back; the sweep
    #: (``djust clear``) deletes rows past it.
    expires = models.BigIntegerField(db_index=True)

    class Meta:
        app_label = "djust"
        db_table = "djust_view_state"
        verbose_name = "view state"

    def __str__(self) -> str:
        return self.key
