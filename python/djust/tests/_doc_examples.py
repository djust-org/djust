"""Documented examples executed as fixtures (ADR-037 D2).

A Python block in a covered documentation section carries a marker on the
comment lines directly above its fence::

    <!-- djust-example: project-menu scenario=project-menu -->
    <!-- djust-example: skip -- the URL route for article-edit -->

The harness finds every block, pairs each Python block with the first ``html``
block after it in the same section, and hands it to the named scenario in
``djust.tests.doc_scenarios``. ``test_doc_example_drift`` fails when a covered
block has no marker, so a new example cannot silently go unexecuted.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import sys
import types
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[3]

_COMMENT = re.compile(r"^<!--.*-->$")
_MARKER = re.compile(r"^<!--\s*djust-example:\s*(?P<body>.*?)\s*-->$")
_TAG = re.compile(r"^(?P<id>[a-z0-9][a-z0-9-]*)\s+scenario=(?P<scenario>[a-z0-9-]+)$")
_SKIP = re.compile(r"^skip\s+--\s*(?P<reason>.*)$")


@dataclasses.dataclass(frozen=True)
class Block:
    line: int  # 1-based line of the opening fence
    language: str
    code: str
    comments: Tuple[str, ...]  # the <!-- ... --> lines directly above the fence


@dataclasses.dataclass(frozen=True)
class Section:
    path: str  # repository-relative, or absolute (tests)
    start: Optional[str] = None  # exact heading line; None: start of file
    stop: Optional[str] = None  # exact heading line that ends it; None: end of file


@dataclasses.dataclass(frozen=True)
class Example:
    id: str
    scenario: Optional[str]
    skip_reason: Optional[str]
    path: str
    line: int
    code: str
    template: Optional[str]
    section_code: Tuple[str, ...]
    marked: bool = True


class DocExampleError(ValueError):
    """A covered section or marker the harness cannot use."""


#: The covered sections (filled in by Task 5).
COVERED: Tuple[Section, ...] = (
    Section("docs/website/guides/interactive-components.md"),
    Section(
        "docs/ai/components.md",
        "## Interactive components (djust 1.3+)",
        "## Common Patterns",
    ),
    Section(
        "docs/website/guides/forms.md",
        "## Editing one record with `ModelFormMixin`",
        "## Form Reset",
    ),
    Section(
        "docs/ai/forms.md",
        "## ModelFormMixin: edit one record (djust 1.3+)",
        "## FormMixin Pattern",
    ),
    Section(
        "docs/website/core-concepts/events.md",
        "## Typed event parameters (strict policy)",
        "## Next Steps",
    ),
    Section("docs/ai/events.md"),
    Section(
        "docs/website/guides/error-codes.md",
        "### T019: Event binding names no handler on its owner",
        "## Code Quality (Q0xx)",
    ),
)


def _path(section_path: str) -> pathlib.Path:
    path = pathlib.Path(section_path)
    return path if path.is_absolute() else ROOT / path


def blocks(path: pathlib.Path) -> List[Block]:
    """Every fenced block, with the run of HTML comments directly above it."""
    lines = path.read_text().splitlines()
    found: List[Block] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped.startswith("```"):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() != "```":
            j += 1
        comments: List[str] = []
        k = i - 1
        while k >= 0 and _COMMENT.match(lines[k].strip()):
            comments.insert(0, lines[k].strip())
            k -= 1
        code = "\n".join(lines[i + 1 : j])
        found.append(Block(i + 1, stripped[3:].strip(), code, tuple(comments)))
        i = j + 1
    return found


def _headings(lines: List[str]) -> Dict[str, int]:
    """Heading line text -> 1-based line, ignoring lines inside fences."""
    fenced = False
    found: Dict[str, int] = {}
    for number, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced and line.startswith("#"):
            found.setdefault(line.rstrip(), number)
    return found


def bounds(section: Section) -> Tuple[int, int]:
    """The section's first and last line (1-based, inclusive)."""
    lines = _path(section.path).read_text().splitlines()
    headings = _headings(lines)
    first, last = 1, len(lines)
    for heading in (section.start, section.stop):
        if heading is not None and heading not in headings:
            raise DocExampleError("%s: heading %r not found" % (section.path, heading))
    if section.start is not None:
        first = headings[section.start]
    if section.stop is not None:
        last = headings[section.stop] - 1
    if last < first:
        raise DocExampleError("%s: %r ends before it starts" % (section.path, section.stop))
    return first, last


def marker(block: Block) -> Optional[Tuple[Optional[str], Optional[str], Optional[str]]]:
    """``(id, scenario, skip_reason)`` from the block's djust-example comment."""
    bodies = [m.group("body") for m in (_MARKER.match(c) for c in block.comments) if m]
    if not bodies:
        return None
    if len(bodies) > 1:
        raise DocExampleError("line %d: more than one djust-example marker" % block.line)
    body = bodies[0]
    skip = _SKIP.match(body)
    if skip:
        reason = skip.group("reason").strip()
        if not reason:
            raise DocExampleError("line %d: skip marker without a reason" % block.line)
        return None, None, reason
    tag = _TAG.match(body)
    if not tag:
        raise DocExampleError("line %d: malformed djust-example marker %r" % (block.line, body))
    return tag.group("id"), tag.group("scenario"), None


def examples(section: Section) -> List[Example]:
    """Every Python block in the section, marked or not."""
    first, last = bounds(section)
    inside = [b for b in blocks(_path(section.path)) if first <= b.line <= last]
    python = [b for b in inside if b.language == "python"]
    section_code = tuple(b.code for b in python)
    found: List[Example] = []
    for block in python:
        parsed = marker(block)
        template = next(
            (b.code for b in inside if b.language == "html" and b.line > block.line), None
        )
        fallback = "%s:%d" % (section.path, block.line)
        if parsed is None:
            found.append(
                Example(
                    fallback,
                    None,
                    None,
                    section.path,
                    block.line,
                    block.code,
                    template,
                    section_code,
                    marked=False,
                )
            )
            continue
        ident, scenario, reason = parsed
        found.append(
            Example(
                ident or fallback,
                scenario,
                reason,
                section.path,
                block.line,
                block.code,
                template,
                section_code,
            )
        )
    return found


def marker_lines(section: Section) -> List[int]:
    """Lines of the section that carry a djust-example marker, found by text."""
    first, last = bounds(section)
    lines = _path(section.path).read_text().splitlines()
    return [n for n in range(first, last + 1) if _MARKER.match(lines[n - 1].strip())]


def load(
    example: Example,
    *,
    template: Optional[str] = None,
    package: Optional[str] = None,
    modules: Optional[Dict[str, Dict[str, object]]] = None,
) -> type:
    """Execute the example as a uniquely named module; return its one LiveView.

    ``package`` makes the module part of a stand-in package, so relative
    imports such as ``from .models import Article`` resolve against
    ``modules`` (``{"models": {"Article": Article}}``).
    """
    from djust import LiveView

    stem = re.sub(r"[^a-z0-9_]", "_", example.id.lower())
    if package:
        parent = types.ModuleType(package)
        parent.__path__ = []  # type: ignore[attr-defined]
        sys.modules[package] = parent
        for child, names in (modules or {}).items():
            module = types.ModuleType("%s.%s" % (package, child))
            module.__dict__.update(names)
            sys.modules[module.__name__] = module
        name = "%s.doc_%s" % (package, stem)
    else:
        name = "djust_doc_example_%s" % stem
    module = types.ModuleType(name)
    if package:
        module.__package__ = package
    sys.modules[name] = module
    exec(compile(example.code, "<%s>" % name, "exec"), module.__dict__)
    views = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, LiveView) and value.__module__ == name
    ]
    if len(views) != 1:
        raise DocExampleError("%s: expected one LiveView, found %r" % (example.id, views))
    view = views[0]
    view.template_name = None
    view.template = example.template if template is None else template
    return view


class Page:
    """A GET, then HTTP-fallback events on the same session."""

    def __init__(self, view_class: type) -> None:
        from djust.tests.test_exposure_runtime import make_request

        self.view_class = view_class
        self.initial = make_request()
        response = view_class().get(self.initial)
        assert response.status_code == 200, response.content
        self.html = response.content.decode()
        self.view = None

    def ids(self) -> List[str]:
        return re.findall(r'data-component-id="([^"]+)"', self.html)

    def event(self, name: str, **params: object) -> int:
        from django.test import RequestFactory

        request = RequestFactory().post(
            self.initial.path,
            data=json.dumps(params),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=name,
        )
        request.user, request.session, request.tenant = (
            self.initial.user,
            self.initial.session,
            None,
        )
        self.view = self.view_class()
        response = self.view.post(request)
        body = json.loads(response.content) if response.content else {}
        if "html" in body:
            self.html = body["html"]
        return response.status_code


def problems(sections, scenarios) -> List[str]:
    """Everything that makes covered documentation drift from its fixtures."""
    found: List[str] = []
    seen: Dict[str, str] = {}
    for section in sections:
        try:
            collected = examples(section)
            markers = marker_lines(section)
        except DocExampleError as exc:
            found.append(str(exc))
            continue
        attached = set()
        for example in collected:
            where = "%s:%d" % (section.path, example.line)
            if not example.marked:
                found.append("%s: Python block without a djust-example marker" % where)
                continue
            if example.scenario and example.scenario not in scenarios:
                found.append("%s: unknown scenario %r" % (where, example.scenario))
            if example.scenario:
                if example.id in seen:
                    found.append(
                        "%s: duplicate id %r (also %s)" % (where, example.id, seen[example.id])
                    )
                seen[example.id] = where
        first, last = bounds(section)
        for block in blocks(_path(section.path)):
            if block.language == "python" and first <= block.line <= last:
                # The block's comment run: the lines directly above its fence.
                attached.update(range(block.line - len(block.comments), block.line))
        for line in markers:
            if line not in attached:
                found.append(
                    "%s:%d: marker not attached to an extracted Python block" % (section.path, line)
                )
    return found


def report(sections) -> Dict[str, object]:
    """Executed, skipped and unmarked counts per covered file."""
    covered: Dict[str, Dict[str, int]] = {}
    for section in sections:
        counts = covered.setdefault(section.path, {"executed": 0, "skipped": 0, "unmarked": 0})
        for example in examples(section):
            key = (
                "unmarked"
                if not example.marked
                else "skipped"
                if example.skip_reason
                else "executed"
            )
            counts[key] += 1
    website = sorted((ROOT / "docs/website").rglob("*.md"))
    total = sum(1 for p in website for b in blocks(p) if b.language == "python")
    executed = sum(c["executed"] for p, c in covered.items() if p.startswith("docs/website/"))
    return {
        "covered": covered,
        "docs_website_python_blocks": total,
        "docs_website_unexecuted": total - executed,
    }
