"""A state store outside the session, for the multi-process harness (#4132).

One file per state in a directory every node opens (``cache_dir``), written
through a rename so a reader on another process never sees half a write. It
stands for an application's own table: a store only moves bytes.
"""

from __future__ import annotations

import os
import tempfile
from typing import Iterable, Optional

from djust.state_store import StateKey, ViewStateStore
from tests.integration.multinode.app.settings import CONFIG


class FileStateStore(ViewStateStore):
    def __init__(self) -> None:
        self.directory = os.path.join(CONFIG["cache_dir"], "view-state")
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, key: StateKey) -> str:
        return os.path.join(self.directory, key.id)

    def load(self, key: StateKey) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        fd, temporary = tempfile.mkstemp(dir=self.directory)
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(temporary, self._path(key))

    def delete(self, keys: Iterable[StateKey]) -> None:
        for key in keys:
            try:
                os.remove(self._path(key))
            except FileNotFoundError:
                continue
