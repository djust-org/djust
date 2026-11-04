"""A parametrized id is short, however big the value it names is (#3800).

Failure shape: ``test_resource_limits["x" * 65537]`` is collected with the 65,537
characters of its value as its id, so pytest ``-v`` prints a 65 KB line (the
largest in this suite is 800 KB). ``gh run view --log`` reads a job log through a
64 KiB line scanner, and stops, silently, at the first line longer than that. The
log it showed for a CI shard ended in the middle of ``test_parameter_contract.py``
at about 100 s, with no summary, no failure and no exit line: a run that had gone
quiet. It had not. It had finished, with one flaky failure, 11 minutes later; the
"silent hang" of #3800 was this reader, and cost two investigations of a hang that
did not exist.

The cure is at the id, not at the reader: nothing in a CI log needs 65,537
characters of ``x`` to say which case ran.

* A ``str`` / ``bytes`` argument longer than :data:`MAX_VALUE_CHARS` (or an ``int`` of more
  than :data:`MAX_INT_BITS` bits) is named by its
  kind, its length and a digest of its content: ``value-str65537-3f2a9c1d``. The digest
  is a SHA-1 of the bytes, not ``hash()``, because every xdist worker collects the
  suite on its own and must arrive at the same ids (a salted ``hash()`` would give each
  worker different ones and the controller would report the run as losing items).
* Anything shorter keeps the id pytest would have given it, so no existing id changes.
* :func:`pytest_collection_modifyitems` refuses a collected id over
  :data:`MAX_ID_CHARS`, so one added with ``ids=[...]`` or ``pytest.param(id=...)``
  (which this hook is never asked about) fails at collection and names the test.

Registered in the root ``conftest.py``; ``tests/test_short_param_ids_3800.py`` pins it.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import pytest

#: A string / bytes value longer than this is not spelled out in its id. Long enough that
#: every id a reader can usefully read is unchanged; short enough that the id of a
#: "too big" case never is the size of the case.
MAX_VALUE_CHARS = 512

#: An ``int`` of more bits than this (about 512 digits) is named by its size, like a string.
MAX_INT_BITS = 1700

#: A collected id over this is refused. ``gh run view --log`` stops at 65,536 bytes a line;
#: the rest of the line is the file path, the worker tag and the result.
MAX_ID_CHARS = 8192


def short_id(argname: str, val: Any) -> Optional[str]:
    """The id for a big ``str`` / ``bytes`` value, or None to leave it to pytest."""
    if isinstance(val, str):
        if len(val) <= MAX_VALUE_CHARS:
            return None
        kind, raw = "str", val.encode("utf-8", "surrogatepass")
    elif isinstance(val, (bytes, bytearray)):
        if len(val) <= MAX_VALUE_CHARS:
            return None
        kind, raw = "bytes", bytes(val)
    elif isinstance(val, int) and not isinstance(val, bool):
        # ``1 << 4096`` is a 1,234-digit id; ``1 << 100000`` would not even format
        # (``int`` refuses to print more than 4,300 digits).
        if val.bit_length() <= MAX_INT_BITS:
            return None
        size = val.bit_length()
        raw = val.to_bytes(size // 8 + 1, "big", signed=True)
        return f"{argname}-int{size}bit-{hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:8]}"
    else:
        return None
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:8]
    return f"{argname}-{kind}{len(val)}-{digest}"


def pytest_make_parametrize_id(config: pytest.Config, val: Any, argname: str) -> Optional[str]:
    return short_id(argname, val)


def pytest_collection_modifyitems(items: list) -> None:
    too_long = [item.nodeid[:120] + "..." for item in items if len(item.nodeid) > MAX_ID_CHARS]
    if too_long:
        raise pytest.UsageError(
            f"{len(too_long)} collected test id(s) are over {MAX_ID_CHARS} characters "
            "(`gh run view --log` silently stops reading a log at its first line over 64 KiB, "
            "#3800). Give the case a short id with `pytest.param(..., id=...)` / `ids=`. "
            "First: " + too_long[0]
        )
