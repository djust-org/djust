#!/usr/bin/env python3
"""Find documented LiveView examples that rely on the removed attribute walk (#4048).

Under explicit exposure, the default since 2.0, a template sees what
``get_context_data()`` (or a context provider) supplies. A public attribute a
view sets on ``self`` does not reach the template by itself: the name renders
empty, with no error. An example written for the old default therefore still
parses, imports and mounts, and shows a blank page.

This script reads every documented view example and reports each one that

* sets a public name on ``self`` (or as a plain class attribute),
* shows a template, in the class or elsewhere in the same document, that reads
  that name, and
* does not supply the name from ``get_context_data()``.

Sources: fenced ``python`` blocks in the Markdown files listed in ``_doc_files``
and string constants (docstrings, generated text) in ``python/djust``.

A class that states ``exposure_policy = "legacy"`` is the "before" half of
migration material and is counted, not reported. ``<!-- doc-exposure-check:
legacy -->`` on the line before a fence does the same for a block that shows
the old behaviour without stating the policy; ``<!-- doc-snippet-check: skip
-->`` and ``anti-pattern`` are honoured too.

Usage:
    python3 scripts/check-doc-exposure.py            # findings, exit 1 if any
    python3 scripts/check-doc-exposure.py --all      # also "no template shown"
    python3 scripts/check-doc-exposure.py --counts   # counts only
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_EXCLUDED_DOC_DIRS = ("docs/archive", "docs/adr", "docs/plans", "docs/reviews", "docs/retros")
# Dated records of how a feature was built or reviewed. They describe the
# framework as it was on their date and are not teaching material.
_DATED_RECORD = re.compile(
    r"(IMPLEMENTATION_PHASE\d|_SUMMARY|_DESIGN|_REPORT|_IMPLEMENTATION|_VISION|_PLAN)\.md$"
    r"|^docs/(superpowers|features|strategy-sessions|audits|internal|example-site)/"
)
# Documents whose component examples may still assign a component in
# ``mount()``: listed and counted, not failed. Empty since #4048 rewrote the
# last of them per #4063 (a LiveComponent in the class body, a plain component
# as a ``state(default_factory=...)`` field); a new entry needs a reason.
_COMPONENT_BACKLOG: tuple[str, ...] = ()
_TOP_LEVEL_DOCS = ("README.md", "QUICKSTART.md", "CONTRIBUTING.md", "CLAUDE.md", "AGENTS.md")
_SKIP_MARKERS = {
    "<!-- doc-snippet-check: skip -->",
    "<!-- doc-snippet-check: anti-pattern -->",
    "<!-- doc-exposure-check: legacy -->",
}

# Names a template can read without the view supplying them, and template
# syntax words that are not names.
_TEMPLATE_WORDS = frozenset(
    """
    if elif else endif for endfor in not and or as with endwith empty block
    endblock extends include load url static csrf_token true false none True
    False None request user forloop perms messages by from only is
    """.split()
)

# Names a framework mixin supplies to the template itself (a context provider):
# ``FormMixin``, ``ModelFormMixin``, streams, uploads, presence. The executed
# check in tests/test_doc_snippets_execute.py reads the real context instead.
_PROVIDED_NAMES = frozenset(
    """
    form_data form_choices form_errors field_errors is_valid success_message
    error_message object streams uploads online_count
    """.split()
)

# Class attributes that configure the view; assigning one is not state.
_CONFIG_NAMES = frozenset(
    """
    template template_name exposure_policy exposure_schema_version login_required
    permission_required login_url tick_interval push_scope params_form
    params_form_fallback http_status_of_state state_attribute temporary_assigns
    form_class model fields queryset paginate_by context_object_name success_url
    use_actors sticky sticky_id layout page_title page_meta api_name
    enable_state_snapshot static_assigns on_mount wizard_steps success_message
    """.split()
)


def _framework_class_attrs() -> frozenset[str]:
    """Class-level names djust's own view classes and mixins define.

    A documented view that assigns one of these is configuring the framework
    (``tick_interval = 5``, ``draft_key = "post"``), not publishing state.
    """
    names: set[str] = set()
    for path in (REPO_ROOT / "python" / "djust").rglob("*.py"):
        if "/tests/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not (node.name.endswith(("Mixin", "View")) or node.name == "LiveViewSmokeTest"):
                continue
            for item in node.body:
                targets = []
                if isinstance(item, ast.Assign):
                    targets = item.targets
                elif isinstance(item, ast.AnnAssign):
                    targets = [item.target]
                names.update(t.id for t in targets if isinstance(t, ast.Name))
    return frozenset(names)


_FRAMEWORK_ATTRS: frozenset[str] = frozenset()


@dataclass
class Example:
    path: str
    line: int
    cls: str
    set_names: set[str] = field(default_factory=set)
    supplied: set[str] = field(default_factory=set)
    supplies_all: bool = False
    legacy: bool = False
    inline_template: str = ""
    has_context_method: bool = False
    components: set[str] = field(default_factory=set)
    has_mount: bool = False
    has_methods: bool = False
    has_template: bool = False
    class_names: set[str] = field(default_factory=set)
    context_assigned: set[str] = field(default_factory=set)

    @property
    def unsupplied(self) -> set[str]:
        if self.supplies_all:
            return set()
        return self.set_names - self.supplied - _PROVIDED_NAMES

    @property
    def is_fragment(self) -> bool:
        """Handlers only (no ``mount()``, no template, no class-level state), or
        declarations only (no method at all, no inline template).

        Such a block shows one part of a view the reader already has, or the
        first step of one a tutorial builds up; it does not claim to be a view
        that renders.
        """
        if not self.has_methods and not self.inline_template:
            return True
        return not (self.has_mount or self.has_template or self.class_names)


def _doc_files() -> list[Path]:
    files = [REPO_ROOT / name for name in _TOP_LEVEL_DOCS]
    files += sorted((REPO_ROOT / "docs").rglob("*.md"))
    files += sorted((REPO_ROOT / "examples").rglob("README*.md"))
    kept = []
    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(prefix + "/") for prefix in _EXCLUDED_DOC_DIRS):
            continue
        if "node_modules" in rel or "/.venv/" in rel or _DATED_RECORD.search(rel):
            continue
        kept.append(path)
    return kept


def _fences(text: str):
    """Yield ``(line, language, body, marker)`` for each fenced block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)(```+|~~~+)\s*([\w+-]*)", lines[i])
        if not match:
            i += 1
            continue
        fence, language = match.group(2), match.group(3).lower()
        marker = lines[i - 1].strip() if i else ""
        body = []
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith(fence):
            body.append(lines[j])
            j += 1
        yield i + 1, language, "\n".join(body), marker
        i = j + 1


def _template_reads(source: str) -> set[str]:
    """Root names read inside ``{{ }}`` and ``{% %}``."""
    names: set[str] = set()
    for chunk in re.findall(r"\{\{(.*?)\}\}|\{%(.*?)%\}", source, flags=re.DOTALL):
        body = chunk[0] or chunk[1]
        body = re.sub(r"\"[^\"]*\"|'[^']*'", " ", body)
        loop = re.match(r"\s*for\s+([\w,\s]+?)\s+in\s", body)
        for name in re.findall(r"(?<![.\w|:])([A-Za-z_]\w*)", body):
            if name not in _TEMPLATE_WORDS:
                names.add(name)
        if loop:
            names -= set(re.findall(r"\w+", loop.group(1)))
    return names


def _is_view(node: ast.ClassDef, known: set[str]) -> bool:
    bases = [ast.unparse(base) for base in node.bases]
    if any("Component" in base or "Test" in base for base in bases):
        return False
    return any("LiveView" in base or base.split(".")[-1] in known for base in bases)


def _self_attr(target: ast.AST) -> str | None:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return target.attr
    return None


def _string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _context_keys(func: ast.AST, example: Example) -> None:
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None and _string(key):
                    example.supplied.add(_string(key))
        elif isinstance(node, ast.Subscript) and _string(node.slice):
            example.supplied.add(_string(node.slice))
        elif isinstance(node, ast.Call):
            callee = ast.unparse(node.func)
            if callee.endswith(".update") or callee in {"dict", "super().get_context_data"}:
                example.supplied.update(kw.arg for kw in node.keywords if kw.arg)
            if callee == "vars" or callee.endswith("setdefault"):
                if callee == "vars":
                    example.supplies_all = True
                elif node.args and _string(node.args[0]):
                    example.supplied.add(_string(node.args[0]))
        elif isinstance(node, ast.Attribute) and node.attr == "__dict__":
            example.supplies_all = True


def _component_classes(tree: ast.Module) -> set[str]:
    """Names in a block that are component classes: imported or defined there."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "component" in (node.module or ""):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.ClassDef) and any(
            "Component" in ast.unparse(base) for base in node.bases
        ):
            names.add(node.name)
    return names


def _is_component_call(value: ast.AST | None, component_classes: set[str]) -> bool:
    if not isinstance(value, ast.Call):
        return False
    callee = ast.unparse(value.func).split(".")[-1]
    return callee in component_classes or callee.endswith("Component")


def _scan_class(
    node: ast.ClassDef, path: str, line: int, component_classes: set[str] = frozenset()
) -> Example:
    example = Example(path=path, line=line + node.lineno - 1, cls=node.name)
    declared: set[str] = set()
    builds_on_base = True
    for item in node.body:
        if isinstance(item, (ast.Assign, ast.AnnAssign)):
            targets = item.targets if isinstance(item, ast.Assign) else [item.target]
            value = item.value
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if name == "formset_classes" and isinstance(value, ast.Dict):
                    # FormSetHelpersMixin supplies each declared formset.
                    example.supplied.update(_string(key) or "" for key in value.keys if key)
                elif name == "exposure_policy" and value is not None and _string(value) == "legacy":
                    example.legacy = True
                elif name in {"template", "template_name"} and value is not None:
                    example.has_template = True
                    if name == "template":
                        example.inline_template = _string(value) or ""
                elif name.startswith("_") or value is None:
                    continue
                elif isinstance(value, ast.Call):
                    # A declared state() field renders by default (#4050),
                    # unless render=False. Any other call is a form or a
                    # component descriptor, not template state.
                    if ast.unparse(value.func).split(".")[-1] == "state":
                        example.class_names.add(name)
                        example.set_names.add(name)
                        hidden = any(
                            kw.arg == "render"
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False
                            for kw in value.keywords
                        )
                        if not hidden:
                            declared.add(name)
                    continue
                elif name in _CONFIG_NAMES or name in _FRAMEWORK_ATTRS:
                    continue
                elif isinstance(value, ast.Constant) or isinstance(
                    value, (ast.List, ast.Dict, ast.Tuple)
                ):
                    if name.islower():
                        example.set_names.add(name)
                        example.class_names.add(name)
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            example.has_methods = True
            if item.name in {"mount", "__init__"}:
                example.has_mount = True
            if any(
                ast.unparse(dec).split("(")[0].split(".")[-1] == "computed"
                for dec in item.decorator_list
            ):
                # A @computed value is read as ``self.total``; like any other
                # attribute it reaches the template only when supplied.
                example.set_names.add(item.name)
                example.class_names.add(item.name)
            if item.name == "get_context_data":
                # Declared fields arrive through the base context, so a
                # get_context_data() that never calls one supplies none.
                builds_on_base = any(
                    isinstance(sub, ast.Attribute) and sub.attr == "get_context_data"
                    for sub in ast.walk(item)
                )
            if item.name == "get_context_data" or any(
                "context" in ast.unparse(dec) for dec in item.decorator_list
            ):
                example.has_context_method = True
                _context_keys(item, example)
            for sub in ast.walk(item):
                # self.assign_async("metrics", loader) sets self.metrics.
                if (
                    isinstance(sub, ast.Call)
                    and ast.unparse(sub.func) == "self.assign_async"
                    and sub.args
                    and _string(sub.args[0])
                ):
                    example.set_names.add(_string(sub.args[0]))
                targets = []
                if isinstance(sub, ast.Assign):
                    targets = sub.targets
                elif isinstance(sub, (ast.AugAssign, ast.AnnAssign)):
                    targets = [sub.target]
                for target in targets:
                    for leaf in ast.walk(target) if isinstance(target, ast.Tuple) else [target]:
                        name = _self_attr(leaf)
                        if name and not name.startswith("_") and name not in _CONFIG_NAMES:
                            example.set_names.add(name)
                            if item.name == "get_context_data":
                                example.context_assigned.add(name)
                            if _is_component_call(getattr(sub, "value", None), component_classes):
                                example.components.add(name)
    if builds_on_base:
        example.supplied |= declared
    return example


def _parse(code: str) -> ast.Module | None:
    for candidate in (code, textwrap.dedent(code)):
        try:
            return ast.parse(candidate)
        except SyntaxError:
            continue
    return None


def _examples_in_code(
    code: str, path: str, line: int, document_components: set[str] = frozenset()
) -> list[Example]:
    tree = _parse(code)
    if tree is None:
        return []
    known: set[str] = set()
    found = []
    component_classes = _component_classes(tree) | document_components
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_view(node, known):
            known.add(node.name)
            found.append(_scan_class(node, path, line, component_classes))
    return found


def scan_markdown(path: Path) -> list[tuple[Example, set[str]]]:
    """Each view example in a Markdown file with the template names that apply to it.

    The template for an example is the nearest one: the template fences that
    follow the block, up to the next block that defines a view; when none
    follows, the ones between the previous view block and this one; when the
    document shows a template nowhere near, every ``{{ }}`` and ``{% %}`` in
    the file, prose included.
    """
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO_ROOT).as_posix()
    # One entry per fence: the view examples of a python block, or the names
    # a template block reads.
    entries: list[tuple[list[Example] | None, set[str]]] = []
    everything: set[str] = set()
    fences = list(_fences(text))
    # A component class defined or imported in one block is often used by a
    # view in the next.
    components: set[str] = set()
    for _, language, body, _ in fences:
        tree = _parse(body) if language in {"python", "py"} else None
        if tree is not None:
            components |= _component_classes(tree)
    for line, language, body, marker in fences:
        if language in {"python", "py"}:
            examples = (
                [] if marker in _SKIP_MARKERS else _examples_in_code(body, rel, line, components)
            )
            if examples:
                entries.append((examples, set()))
        else:
            reads = _template_reads(body)
            everything |= reads
            if reads:
                entries.append((None, reads))
    prose = re.sub(r"(```+|~~~+).*?\1", " ", text, flags=re.DOTALL)
    everything |= _template_reads(prose)

    def span(start: int, step: int) -> set[str] | None:
        found: set[str] | None = None
        index = start + step
        while 0 <= index < len(entries) and entries[index][0] is None:
            found = (found or set()) | entries[index][1]
            index += step
        return found

    results: list[tuple[Example, set[str]]] = []
    for index, (examples, _) in enumerate(entries):
        if examples is None:
            continue
        reads = span(index, 1)
        if reads is None:
            reads = span(index, -1)
        if reads is None:
            reads = everything
        results += [(example, reads) for example in examples]
    return results


def reads_for(path: Path, class_name: str, line: int) -> set[str]:
    """The template names that apply to the view ``class_name`` in the block at ``line``."""
    candidates = [
        (abs(example.line - line), reads)
        for example, reads in scan_markdown(path)
        if example.cls == class_name and example.line >= line
    ]
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else set()


def _class_snippets(text: str):
    """Yield ``(offset_line, code)`` for each ``class X(...):`` block in a string."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)class \w+\(.*\):\s*$", lines[i])
        if not match:
            i += 1
            continue
        indent = len(match.group(1))
        j = i + 1
        while j < len(lines) and (
            not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent
        ):
            j += 1
        yield i, "\n".join(lines[i:j])
        i = j


def scan_python(path: Path) -> tuple[list[Example], set[str]]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return [], set()
    examples: list[Example] = []
    reads: set[str] = set()
    for node in ast.walk(tree):
        value = _string(node)
        if not value or "class " not in value or "self." not in value:
            continue
        reads |= _template_reads(value)
        for offset, code in _class_snippets(value):
            examples.extend(_examples_in_code(code, rel, node.lineno + offset))
    return examples, reads


def collect() -> list[tuple[Example, set[str]]]:
    """Every example with the names its document's templates read."""
    global _FRAMEWORK_ATTRS
    _FRAMEWORK_ATTRS = _framework_class_attrs()
    results: list[tuple[Example, set[str]]] = []
    for path in _doc_files():
        results += scan_markdown(path)
    for path in sorted((REPO_ROOT / "python" / "djust").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "/tests/" in rel or "/migrations/" in rel:
            continue
        examples, reads = scan_python(path)
        results += [(example, reads) for example in examples]
    return results


def classify(example: Example, reads: set[str]) -> tuple[str, set[str]]:
    """``(kind, names)``: ``ok``, ``legacy``, ``fragment``, ``component``, ``blank``, ``unshown``.

    ``component``: the example assigns a component to a public attribute in a
    method. An explicit view refuses that at its first render
    (``ExposureConfigurationError``); components are declared at class level,
    or built in ``get_context_data()`` when they are stateless.

    ``blank``: a template in the class or the document reads a name the
    example sets and does not supply, or ``get_context_data()`` assigns a
    public attribute and does not supply it. ``unshown``: the example sets public
    names, supplies none of them and no template is shown to decide by.
    """
    if example.legacy:
        return "legacy", set()
    if example.components:
        return "component", set(example.components)
    if example.is_fragment:
        return "fragment", set()
    unsupplied = example.unsupplied
    if not unsupplied:
        return "ok", set()
    # A view that carries its own template is judged by that template alone.
    if example.inline_template:
        reads = _template_reads(example.inline_template)
    read = unsupplied & reads
    # ``self.items = self._items`` inside get_context_data() is the 1.x way of
    # publishing a value; it publishes nothing now, template shown or not.
    read |= unsupplied & example.context_assigned
    if read:
        return "blank", read
    if not example.has_context_method:
        return "unshown", unsupplied
    return "ok", set()


_KINDS = ("ok", "legacy", "fragment", "component", "blank", "unshown")


def failures(kinds: dict[str, list[tuple[Example, set[str]]]]) -> list[tuple[Example, set[str]]]:
    """The findings that fail the check: every ``blank``, and each ``component``
    outside the recorded backlog."""
    return list(kinds.get("blank", [])) + [
        (example, names)
        for example, names in kinds.get("component", [])
        if not example.path.startswith(_COMPONENT_BACKLOG)
    ]


def by_kind(
    results: list[tuple[Example, set[str]]],
) -> dict[str, list[tuple[Example, set[str]]]]:
    kinds: dict[str, list[tuple[Example, set[str]]]] = {}
    for example, reads in results:
        kind, names = classify(example, reads)
        kinds.setdefault(kind, []).append((example, names))
    return kinds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--all", action="store_true", help="also list examples with no template")
    parser.add_argument("--counts", action="store_true", help="print the counts only")
    args = parser.parse_args(argv)

    results = collect()
    kinds = by_kind(results)

    shown = ["component", "blank", "unshown"] if args.all else ["component", "blank"]
    if not args.counts:
        for kind in shown:
            for example, names in sorted(
                kinds.get(kind, []), key=lambda pair: (pair[0].path, pair[0].line)
            ):
                backlog = kind == "component" and example.path.startswith(_COMPONENT_BACKLOG)
                print(
                    f"{'component-backlog' if backlog else kind}\t{example.path}:{example.line}"
                    f"\t{example.cls}\t{', '.join(sorted(names))}"
                )
    files = {example.path for example, _ in results}
    print(
        f"{len(results)} view examples in {len(files)} files: "
        + ", ".join(f"{len(kinds.get(k, []))} {k}" for k in _KINDS),
        file=sys.stderr,
    )
    return 1 if failures(kinds) else 0


if __name__ == "__main__":
    sys.exit(main())
