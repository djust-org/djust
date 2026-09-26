"""Generate the interactive-components reference in api-reference/components.md.

ADR-034 D2/D7, owner decision C4-Q2. The tables come from the running
contracts of ``djust.components.interactive``, not from a hand-kept list:

- the constructor's configuration and the item TypedDicts;
- the state properties;
- the outputs and their payloads;
- the local actions, with their parameters;
- the keyed-collection API.

Every description is the first line of the code object's own docstring.

The action names describe what the component does. They are not an API for
writing components; the output-authoring pieces stay private (ADR-034 Q4).

Usage::

    python scripts/generate-interactive-reference.py          # fail if the block is stale
    python scripts/generate-interactive-reference.py --write  # rewrite the block in place
    python scripts/generate-interactive-reference.py --print  # print the block
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path
from typing import Any, get_type_hints

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DOC = REPO / "docs" / "website" / "api-reference" / "components.md"
BEGIN = (
    "<!-- BEGIN GENERATED: interactive components (scripts/generate-interactive-reference.py) -->"
)
END = "<!-- END GENERATED: interactive components -->"


def _setup_django() -> None:
    from django.conf import settings

    if not settings.configured:
        settings.configure(SECRET_KEY="interactive-reference", INSTALLED_APPS=[])
        import django

        django.setup()


def _summary(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    first = doc.strip().splitlines()[0] if doc.strip() else ""
    return first.replace("``", "`")


def _type_name(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).replace("typing.", "").replace("djust.components._interactive.", "")


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def render_block() -> str:
    _setup_django()
    from djust.components import _interactive
    from djust.components.interactive import ActionItem, DropdownMenu, SeparatorItem

    lines = [BEGIN, "", "### `DropdownMenu` configuration", ""]
    lines += ["| Argument | Type | Default |", "| --- | --- | --- |"]
    for name, param in inspect.signature(DropdownMenu.__init__).parameters.items():
        if name == "self":
            continue
        default = "required" if param.default is inspect.Parameter.empty else repr(param.default)
        lines.append(
            "| `%s` | `%s` | %s |" % (name, _escape(_type_name(param.annotation)), default)
        )

    lines += ["", "| Item type | Keys |", "| --- | --- |"]
    for item in (ActionItem, SeparatorItem):
        hints = get_type_hints(item)
        keys = [
            "`%s: %s`%s"
            % (
                key,
                _escape(_type_name(hint)),
                "" if key in item.__required_keys__ else " (optional)",
            )
            for key, hint in hints.items()
        ]
        lines.append("| `%s` | %s |" % (item.__name__, ", ".join(keys)))

    lines += ["", "### State", "", "| Property | Type | Meaning |", "| --- | --- | --- |"]
    for name in ("key", "open", "selected", "label", "visibility"):
        prop = inspect.getattr_static(DropdownMenu, name)
        returns = get_type_hints(prop.fget).get("return", inspect.Parameter.empty)
        lines.append(
            "| `%s` | `%s` | %s |"
            % (name, _escape(_type_name(returns)), _escape(_summary(prop) or _summary(prop.fget)))
        )

    lines += [
        "",
        "### Outputs",
        "",
        "| Subscription | Callback payload | When |",
        "| --- | --- | --- |",
    ]
    probe = DropdownMenu(label="reference", items=[])
    for output in probe.outputs:
        payload = ", ".join("%s: %s" % (name, kind.__name__) for name, kind in output.payload)
        description = _summary(getattr(_interactive.Outputs, output.name))
        lines.append(
            "| `@menu.on.%s` | `component: DropdownMenu, %s` | %s |"
            % (output.name, payload, _escape(description))
        )

    lines += [
        "",
        "### Local actions",
        "",
        "| Action | Parameters | What it does |",
        "| --- | --- | --- |",
    ]
    from djust._parameter_metadata import component_stop, declared_handlers

    # The component's actions, as dispatch discovers them (ADR-037 D1).
    for handler in sorted(declared_handlers(DropdownMenu, component_stop), key=lambda h: h.name):
        name, member = handler.name, handler.function
        params = ", ".join(
            "%s: %s" % (p.name, _type_name(p.annotation))
            for p in inspect.signature(member).parameters.values()
            if p.name != "self"
        )
        lines.append(
            "| `%s` | %s | %s |"
            % (name, "`%s`" % params if params else "none", _escape(_summary(member)))
        )

    lines += [
        "",
        "### Keyed collection (`DropdownMenu.collection()`)",
        "",
        "| Member | Meaning |",
        "| --- | --- |",
    ]
    collection = _interactive.DropdownMenuCollection
    for name in ("sync", "get", "values", "__len__", "__iter__"):
        member = inspect.getattr_static(collection, name)
        label = {"__len__": "len(rows)", "__iter__": "iter(rows)", "values": "rows.values"}.get(
            name, "rows.%s()" % name
        )
        lines.append("| `%s` | %s |" % (label, _escape(_summary(member))))
    lines += ["", END]
    return "\n".join(lines) + "\n"


def current_block(text: str) -> str:
    start, end = text.find(BEGIN), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError("generated block markers not found")
    return text[start : end + len(END)] + "\n"


def splice(text: str, block: str) -> str:
    start, end = text.find(BEGIN), text.find(END)
    return text[:start] + block.rstrip("\n") + text[end + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args(argv)
    block = render_block()
    if args.print:
        sys.stdout.write(block)
        return 0
    text = args.doc.read_text(encoding="utf-8")
    try:
        old = current_block(text)
    except ValueError as exc:
        print("ERROR: %s: %s" % (args.doc, exc), file=sys.stderr)
        return 2
    if old == block:
        print("%s: unchanged" % os.path.relpath(args.doc, REPO))
        return 0
    if args.write:
        args.doc.write_text(splice(text, block), encoding="utf-8")
        print("%s: generated block rewritten" % os.path.relpath(args.doc, REPO))
        return 0
    print(
        "ERROR: %s: the interactive reference is stale; run `make interactive-reference`"
        % os.path.relpath(args.doc, REPO),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
