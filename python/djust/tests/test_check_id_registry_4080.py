"""Every system-check ID the code emits is registered in ``docs/checks/`` (#4080).

``docs/checks/<ID>.md`` is the registry of every ID djust has assigned, live,
retired and reserved. It is also the source of the generated tables and
sections in ``docs/system-checks.md`` and the error-codes guide, so a check
added without its file would ship undocumented, and a retired ID put back into
use would silently change what an old ``SILENCED_SYSTEM_CHECKS`` entry
suppresses. Two branches that take the same new ID both add the same file and
conflict on it.

The IDs are read from string literals in ``python/djust`` (every
``id="djust.V027"`` and ``_is_check_suppressed("djust.V027")``), so a branch of
a check that only fires on some project states is still found.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "system-check-docs.py"


def _load():
    spec = importlib.util.spec_from_file_location("system_check_docs", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_code_and_the_check_id_registry_agree() -> None:
    mod = _load()
    entries = mod.load()
    emitted = mod.emitted_ids()
    # A canary that the scan sees the checks at all.
    assert {"C001", "V027", "T019", "audio.E001", "djust_theming.W001"} <= set(emitted)
    problems = mod.registry_problems(entries, emitted)
    assert not problems, "\n".join(problems)


def test_registry_files_are_well_formed() -> None:
    mod = _load()
    problems = mod.validate(mod.load())
    assert not problems, "\n".join(problems)


def test_the_retired_ids_the_docs_promise_never_to_reuse_are_registered() -> None:
    mod = _load()
    status = {e.stem: e.meta.get("status", "live") for e in mod.load()}
    for stem in ("V007", "V011", "V017", "V020", "C018", "T015"):
        assert status.get(stem) == "retired", stem
    assert status.get("S010") == "reserved"
