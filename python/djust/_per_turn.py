"""Switches for the per-turn shortcuts (#3996, #3997, #3999) and per-view memory (#3998).

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
``unchanged_save_skip``     The automatic state (#4107) is not written to the
                            session when its encoding equals what the store
                            already holds (or, with nothing stored, what
                            ``mount()`` produced).
``deferred_save``           With ``DJUST_STATE_SAVE_INTERVAL`` set, a socket
                            turn ``_save_policy`` finds deferrable sends its
                            frame without waiting for storage, and a flush
                            writes the view later (design: save frequency).
                            Off: every turn saves before its frame, as with
                            the interval at 0 (the default).
``compact_fingerprints``    A structural fingerprint keeps a ``str`` / ``int``
                            / ``bytes`` / ``None`` leaf as the value itself and
                            a dict's items as one flat tuple, in place of a
                            tuple per leaf and per item (#3998). Off: the wide
                            encoding ``main`` had.
``digest_fingerprints``     What a view keeps between turns for change
                            detection (the dirty baseline, the last render's
                            fingerprints, the request-scoped ones) is a 16-byte
                            digest of each fingerprint, not the fingerprint
                            tree (#3998). Off: the trees themselves.
==========================  ==================================================

The last two change what a socket holds between turns, not what a turn does;
they are here because the same oracle proves them (no frame may differ).
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
        "unchanged_save_skip",
        "compact_fingerprints",
        "digest_fingerprints",
        "deferred_save",
    }
)

#: The shortcuts that are turned OFF. Empty outside the tests.
disabled: FrozenSet[str] = frozenset()


def on(name: str) -> bool:
    """Whether the shortcut *name* is in force."""
    return name not in disabled
