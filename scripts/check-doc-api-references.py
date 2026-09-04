#!/usr/bin/env python3
"""Cross-reference method names in docs against the classes that must provide them.

Motivation (#2652 / PR #2655): `docs/website/guides/pwa.md` documented EIGHT
methods that do not exist — `enable_offline()`, `load_offline_state()`,
`save_offline_state()`, `disable_offline()`, `is_offline_enabled()`,
`handle_online()`, `queue_sync()`, `process_sync_queue()` — in an API table AND
in two runnable examples. A reader copy-pasting either example got
`AttributeError` on the first line of `mount()`.

Two review rounds fixed those by hand and BOTH missed a second example a
hundred lines below the tables, because each swept the sites it was pointed at
rather than the whole file. This script sweeps the file.

It checks two shapes:

  * ``self.foo(...)`` inside a fenced ``python`` block  — the copy-paste path
  * ``| `foo(...)` | ...``  table rows                  — the reference path

against the union of the classes registered for that doc below. A name absent
from every class is reported. Prose is deliberately NOT scanned: a sentence
like "there is no ``enable_offline()``" is a correction, not a claim, and
flagging it would punish the fix.

Run: python3 scripts/check-doc-api-references.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# doc path -> dotted paths of the classes whose surface it documents
DOC_CLASSES: dict[str, tuple[str, ...]] = {
    "docs/guides/pwa.md": (
        "djust.pwa.mixins.PWAMixin",
        "djust.pwa.mixins.OfflineMixin",
        "djust.pwa.mixins.SyncMixin",
    ),
    "docs/website/guides/pwa.md": (
        "djust.pwa.mixins.PWAMixin",
        "djust.pwa.mixins.OfflineMixin",
        "djust.pwa.mixins.SyncMixin",
    ),
    "docs/guides/multi-tenant.md": (
        "djust.tenants.mixin.TenantMixin",
        "djust.tenants.mixin.TenantScopedMixin",
    ),
}

# Names a LiveView/Django base supplies, or that a doc legitimately writes as a
# placeholder. Not part of the documented mixin surface, so not checked here.
ALWAYS_ALLOWED = {
    "mount",
    "render",
    "get_context_data",
    "get_queryset",
    "get_object",
    "dispatch",
    "push_event",
    "super",
    "print",
    "len",
    "range",
    "str",
    "int",
    "list",
    "dict",
    "set",
    "get",
}

# `sync_create_Item` and friends are user-defined hooks; the framework looks
# them up dynamically via f"sync_{verb}_{model}".
PLACEHOLDER_RE = re.compile(r"^sync_(create|update|delete)_")

# Methods an example calls but expects the READER to supply. Each is marked
# "# your own method" at its call site in the doc. Keep this list short: a name
# belongs here only if the surrounding prose makes clear the app provides it.
APP_PROVIDED = {
    "send_to_server",
    "get_api_usage",
    "has_permission",
    "send_invitation_email",
}

PY_FENCE_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
SELF_CALL_RE = re.compile(r"\bself\.([a-z_][a-z0-9_]*)\s*\(")
TABLE_ROW_RE = re.compile(r"^\|\s*`([a-z_][a-z0-9_]*)\s*\(", re.MULTILINE)
# An example that defines its own helper is showing YOU how to write one; it is
# not claiming the framework ships it.
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([a-z_][a-z0-9_]*)", re.MULTILINE)


def load_class(dotted: str):
    module_path, _, name = dotted.rpartition(".")
    module = __import__(module_path, fromlist=[name])
    return getattr(module, name)


def check_doc(rel_path: str, dotted_classes: tuple[str, ...]) -> list[str]:
    path = REPO_ROOT / rel_path
    if not path.exists():
        return [f"{rel_path}: listed in DOC_CLASSES but missing from the repo"]

    classes = [load_class(d) for d in dotted_classes]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    found: dict[str, int] = {}

    def record(name: str, needle: str) -> None:
        if name in found:
            return
        for i, line in enumerate(lines, 1):
            if needle in line:
                found[name] = i
                return
        found[name] = 0

    self_defined: set[str] = set()
    for block in PY_FENCE_RE.findall(text):
        self_defined.update(DEF_RE.findall(block))
    for block in PY_FENCE_RE.findall(text):
        for name in SELF_CALL_RE.findall(block):
            if name not in self_defined:
                record(name, f"self.{name}(")

    for name in TABLE_ROW_RE.findall(text):
        record(name, f"`{name}(")

    problems = []
    for name, line_no in sorted(found.items(), key=lambda kv: kv[1]):
        if name in ALWAYS_ALLOWED or name in APP_PROVIDED or PLACEHOLDER_RE.match(name):
            continue
        if any(hasattr(cls, name) for cls in classes):
            continue
        where = f"{rel_path}:{line_no}" if line_no else rel_path
        owners = ", ".join(c.__name__ for c in classes)
        problems.append(f"{where}: `{name}()` is documented but exists on none of: {owners}")
    return problems


def main() -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    sys.path.insert(0, str(REPO_ROOT / "python"))
    sys.path.insert(0, str(REPO_ROOT))
    try:
        import django

        django.setup()
    except Exception:  # pragma: no cover - docs check must not hard-fail on setup
        pass

    problems: list[str] = []
    for rel_path, dotted_classes in DOC_CLASSES.items():
        problems.extend(check_doc(rel_path, dotted_classes))

    if problems:
        print("Documented methods that do not exist:\n")
        for p in problems:
            print(f"  {p}")
        print(
            "\nEither the method was renamed/removed (fix the doc against the code),\n"
            "or the doc is describing an API that was never implemented.\n"
            "A reader copy-pasting these examples gets AttributeError."
        )
        return 1

    checked = ", ".join(DOC_CLASSES)
    print(f"OK — every method documented in {checked} exists on its class")
    return 0


if __name__ == "__main__":
    sys.exit(main())
