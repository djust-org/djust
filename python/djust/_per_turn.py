"""Switches for the per-turn shortcuts (#3996, #3997, #3999).

Each name below is one place where a turn reuses a value it already has, or
skips work whose result is already known. Every one is on. The switch exists
for the tests: ``test_per_turn_oracle.py`` runs the same views and event
sequences with a shortcut off and on, in one process, and compares the frames
byte for byte; the gate-off tests turn one off and check that the work it
saves comes back. Application code must not turn them off: nothing reads a
setting, and there is no supported way to do it.

==========================  ==================================================
``contract_cache``          ``ExposureContract.from_view_class`` and the
                            provider manifest are compiled once per class and
                            re-checked against the class dictionaries on every
                            use (``_exposure._ClassFacts``).
``snapshot_single_hop``     The event snapshot takes no thread hop for a view
                            with no client-persisted field whose binding the
                            commit just verified, and one hop otherwise.
``request_scoped_changes``  A context processor's plain-data value is sent to
                            the renderer when it changed, not on every turn,
                            and a changed one is reported as a changed key.
``serializable_by_type``    ``_is_json_serializable`` answers False for a value
                            ``json.dumps`` cannot accept by its type, without
                            calling it.
``plain_sub_ids``           ``_collect_sub_ids`` is not run over a changed
                            attribute that is plain data and is itself a
                            context value.
==========================  ==================================================
"""

from __future__ import annotations

from typing import FrozenSet

ALL: FrozenSet[str] = frozenset(
    {
        "contract_cache",
        "snapshot_single_hop",
        "request_scoped_changes",
        "serializable_by_type",
        "plain_sub_ids",
    }
)

#: The shortcuts that are turned OFF. Empty outside the tests.
disabled: FrozenSet[str] = frozenset()


def on(name: str) -> bool:
    """Whether the shortcut *name* is in force."""
    return name not in disabled
