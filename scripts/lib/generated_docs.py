"""Generated regions in hand-written docs, and the one-file-per-entry sources that fill them (#4080).

Shared tables that every change appended to (system-check docs, removed-name
tables) conflicted on nearly every merge. The cure is the one ``changelog.d/``
uses: each entry lives in its own small file, and the shared doc is assembled
from those files. Two branches that add different entries add different files,
so their sources never conflict.

A doc keeps its hand-written prose. Each generated part sits between markers::

    <!-- BEGIN GENERATED <name>: do not edit by hand; <how to regenerate> -->
    ...
    <!-- END GENERATED <name> -->

and the last line of the doc is a fingerprint of everything OUTSIDE those
regions::

    <!-- generated-doc prose-sha256: 0123456789ab -->

The docs are ``merge=union`` in ``.gitattributes``, so two branches that both
regenerated a region merge without a conflict (their rows end up side by side,
and ``check`` then fails until ``write`` re-sorts them). Union would also keep
both versions of a hand-written paragraph that two branches edited; the
fingerprint catches that: each branch's edit changed the fingerprint line, union
keeps both lines, and ``check`` refuses the doc until someone compares the
merged prose by hand.

Entry files are Markdown with a small front matter block::

    ---
    id: V027
    severity: Warning
    summary: One line for a table cell
    ---

    <!-- doc: reference -->
    ### V027 — ...
    body

    <!-- doc: guide -->
    ### V027: ...
    body

Front matter is ``key: value``, one per line, values verbatim. A body is split
into named parts by ``<!-- doc: <name> -->`` lines; text before the first such
line is the part named ``""``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

BEGIN_RE = re.compile(r"^<!-- BEGIN GENERATED (?P<name>\S+?):? .*-->$")
END_RE = re.compile(r"^<!-- END GENERATED (?P<name>\S+) -->$")
FINGERPRINT_RE = re.compile(r"^<!-- generated-doc prose-sha256: (?P<sha>[0-9a-f]+) -->$")
PART_RE = re.compile(r"^<!-- doc: (?P<name>[a-z0-9_-]+) -->$")


class GeneratedDocError(Exception):
    """A source file or a generated doc is malformed."""


@dataclass
class Entry:
    """One source file: its front matter and its named body parts."""

    path: Path
    meta: dict[str, str]
    parts: dict[str, str] = field(default_factory=dict)

    @property
    def stem(self) -> str:
        return self.path.name[: -len(".md")]


def read_entry(path: Path) -> Entry:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    meta: dict[str, str] = {}
    body_start = 0
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError:
            raise GeneratedDocError("%s: front matter has no closing '---'" % path) from None
        for raw in lines[1:end]:
            if not raw.strip():
                continue
            key, sep, value = raw.partition(":")
            if not sep or not key.strip() or " " in key.strip():
                raise GeneratedDocError("%s: front matter line is not 'key: value': %r" % (path, raw))
            key = key.strip()
            if key in meta:
                raise GeneratedDocError("%s: front matter key %r appears twice" % (path, key))
            meta[key] = value.strip()
        body_start = end + 1
    parts: dict[str, list[str]] = {}
    current = ""
    for raw in lines[body_start:]:
        m = PART_RE.match(raw)
        if m:
            current = m.group("name")
            if current in parts:
                raise GeneratedDocError("%s: part %r appears twice" % (path, current))
            parts[current] = []
            continue
        parts.setdefault(current, []).append(raw)
    cleaned = {name: "\n".join(body).strip("\n") for name, body in parts.items()}
    return Entry(path=path, meta=meta, parts={k: v for k, v in cleaned.items() if v.strip()})


def read_entries(directory: Path) -> list[Entry]:
    """Every ``*.md`` entry in ``directory`` except ``README.md``, sorted by file name."""
    return [
        read_entry(p) for p in sorted(directory.glob("*.md")) if p.name != "README.md"
    ]


def write_entry(path: Path, meta: dict[str, str], parts: dict[str, str]) -> None:
    """Write an entry file (used by one-off conversions and tests)."""
    out: list[str] = []
    if meta:
        out.append("---")
        out.extend("%s: %s" % (k, v) for k, v in meta.items())
        out.append("---")
    for name, body in parts.items():
        if not body.strip():
            continue
        if out:
            out.append("")
        if name:
            out.append("<!-- doc: %s -->" % name)
        out.append(body.strip("\n"))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _split(text: str) -> tuple[list[str], list[tuple[str, int, int]]]:
    """Lines of ``text`` and its regions as ``(name, begin_line, end_line)``."""
    lines = text.split("\n")
    regions: list[tuple[str, int, int]] = []
    open_name: str | None = None
    open_at = -1
    seen: set[str] = set()
    for i, line in enumerate(lines):
        b = BEGIN_RE.match(line)
        if b:
            if open_name is not None:
                raise GeneratedDocError(
                    "line %d: region %r begins inside region %r" % (i + 1, b.group("name"), open_name)
                )
            open_name, open_at = b.group("name"), i
            continue
        e = END_RE.match(line)
        if e:
            if open_name != e.group("name"):
                raise GeneratedDocError(
                    "line %d: END GENERATED %s does not close %r" % (i + 1, e.group("name"), open_name)
                )
            if open_name in seen:
                raise GeneratedDocError("region %r appears twice" % open_name)
            seen.add(open_name)
            regions.append((open_name, open_at, i))
            open_name = None
    if open_name is not None:
        raise GeneratedDocError("region %r is never closed" % open_name)
    return lines, regions


def region_names(text: str) -> list[str]:
    return [name for name, _, _ in _split(text)[1]]


def prose_fingerprint(text: str) -> str:
    """SHA-256 (12 hex) of the doc with region contents and fingerprint lines removed."""
    lines, regions = _split(text)
    inside: set[int] = set()
    for _, begin, end in regions:
        inside.update(range(begin + 1, end))
    kept = [
        line
        for i, line in enumerate(lines)
        if i not in inside and not FINGERPRINT_RE.match(line)
    ]
    while kept and kept[-1] == "":
        kept.pop()
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()[:12]


def render(text: str, contents: dict[str, str]) -> str:
    """``text`` with every region replaced by ``contents[name]`` and the fingerprint refreshed.

    A region ``text`` has but ``contents`` lacks, or the reverse, is an error:
    a generator and its doc must agree on the set of regions.
    """
    lines, regions = _split(text)
    names = {name for name, _, _ in regions}
    missing = sorted(set(contents) - names)
    if missing:
        raise GeneratedDocError("no region marked for: %s" % ", ".join(missing))
    unknown = sorted(names - set(contents))
    if unknown:
        raise GeneratedDocError("no generator for region(s): %s" % ", ".join(unknown))
    out: list[str] = []
    pos = 0
    for name, begin, end in regions:
        out.extend(lines[pos : begin + 1])
        body = contents[name].strip("\n")
        out.append("")
        if body:
            out.extend(body.split("\n"))
            out.append("")
        pos = end
    out.extend(lines[pos:])
    # Drop any fingerprint lines (a union merge can leave two) and append one.
    while out and out[-1] == "":
        out.pop()
    out = [line for line in out if not FINGERPRINT_RE.match(line)]
    while out and out[-1] == "":
        out.pop()
    new = "\n".join(out) + "\n"
    return new.rstrip("\n") + "\n\n<!-- generated-doc prose-sha256: %s -->\n" % prose_fingerprint(new)


def check(path: Path, contents: dict[str, str], how: str) -> list[str]:
    """Problems with the committed ``path`` against what ``contents`` produce."""
    text = path.read_text(encoding="utf-8")
    try:
        fingerprints = [m.group("sha") for m in map(FINGERPRINT_RE.match, text.split("\n")) if m]
        expected = render(text, contents)
    except GeneratedDocError as exc:
        return ["%s: %s" % (path, exc)]
    problems: list[str] = []
    if len(fingerprints) > 1:
        problems.append(
            "%s has %d prose fingerprint lines. Two branches both edited its hand-written "
            "text and a union merge kept both versions of what they changed. Compare the "
            "merged text with each branch by hand (look for doubled paragraphs or table "
            "rows outside the generated regions), fix it, then run: %s"
            % (path, len(fingerprints), how)
        )
    elif not fingerprints or fingerprints[0] != prose_fingerprint(text):
        problems.append(
            "%s: its hand-written text changed but the fingerprint on its last line was "
            "not refreshed. Run: %s" % (path, how)
        )
    if not problems and text != expected:
        problems.append(
            "%s: a generated region differs from its sources (a hand edit inside a "
            "BEGIN/END GENERATED region, or a source added, changed or removed without "
            "regenerating). Edit the source files, then run: %s" % (path, how)
        )
    return problems


def write(path: Path, contents: dict[str, str]) -> bool:
    """Regenerate ``path`` in place. Returns whether it changed."""
    text = path.read_text(encoding="utf-8")
    new = render(text, contents)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False
