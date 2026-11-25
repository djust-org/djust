"""``djust_exposure_inventory --fix``: the mechanical half of the 2.0 upgrade (#4209).

Three rewrites, planned from the imported, unedited classes and then written:

1. **Template names.** For each view that states ``exposure_policy = "legacy"`` (on
   itself or on a base or mixin), the names its template reads from the view's
   attributes are supplied from ``get_context_data()`` (the computation is T027's,
   :func:`djust.checks.templates.held_template_reads`), and the policy line is
   deleted once every view that inherits it was fully supplied.
2. **Handler parameters.** Unannotated event-handler parameters are annotated
   ``Any``, which the strict parameter policy accepts unchecked.
3. **Event arguments.** ``data-X`` on an element bound to a handler that declares
   ``X`` becomes ``dj-value-X`` in the template (an inline ``template`` string is a
   template; HTML built elsewhere in Python is reported, never rewritten).

What it does not decide is reported as needing a person: whether a supplied name is
the view's state (``state()``), the real type of a parameter, ``_target``, ``data-*``
built in Python. It never instantiates a view, evaluates a property or a ``state()``
factory, and never writes a grant (``state()``, ``persist=`` or ``client=``). It only
edits files under ``--root`` and outside djust and the installed packages, using
``ast`` positions and text splices (no new dependency); a ``.py`` whose result does
not parse, or whose edits overlap, is left as it was.

Note for #4199 (making ``exposure_policy = "legacy"`` an error at class creation):
``--fix`` imports the views it rewrites, so #4199 must keep it able to import a
module that still states the legacy policy, for example with a process-local permit
(a ``ContextVar`` such as ``djust._exposure.permit_legacy_for_migration()``) that
only ``djust_exposure_inventory`` enters around discovery and that turns the refusal
back into the DeprecationWarning. Without it the command cannot see the views it is
meant to migrate.
"""

from __future__ import annotations

import ast
import difflib
import inspect
import re
import site
import sysconfig
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

#: ``exposure_policy`` values; built so the literal never appears as a statement here.
_LEGACY = "legacy"

_NEWLINE_RE = re.compile(r"\r\n|\r|\n")
_LINES_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class Change:
    """One applied (or, in a dry run, planned) edit."""

    file: str  # path relative to root
    line: int  # 1-based, in the file as it was before the fix
    kind: str  # policy_deleted | context_added | parameter_annotated | data_argument_renamed
    detail: str  # the text line printed


@dataclass
class Note:
    """Something the command did not decide: it needs a person."""

    kind: str
    where: str  # dotted label or relpath:line
    detail: str


@dataclass
class FixReport:
    changes: list[Change] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    #: relpath -> unified diff (filled in a dry run).
    diffs: dict[str, str] = field(default_factory=dict)
    written: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "changes": [asdict(c) for c in self.changes],
            "notes": [asdict(n) for n in self.notes],
            "diffs": dict(self.diffs),
            "written": self.written,
        }

    def changed_files(self) -> list[str]:
        return sorted({c.file for c in self.changes})


def _and(names: Iterable[str]) -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    items = list(names)
    if len(items) <= 1:
        return "".join(items)
    return "%s and %s" % (", ".join(items[:-1]), items[-1])


def format_fix_report(report: FixReport, dry_run: bool) -> str:
    """The text report (A.7 of the plan): changes by file, then what needs a person."""
    files = report.changed_files()
    if not files:
        headline = "nothing to change"
    elif dry_run:
        headline = "%d file%s would change" % (len(files), "" if len(files) == 1 else "s")
    else:
        headline = "%d file%s changed" % (len(files), "" if len(files) == 1 else "s")
    lines = ["djust_exposure_inventory --fix: " + headline]
    if files:
        lines += ["", "Would change:" if dry_run else "Changed:"]
        for relpath in files:
            lines.append("  " + relpath)
            for change in sorted(
                (c for c in report.changes if c.file == relpath), key=lambda c: c.line
            ):
                lines.append("    line %d: %s" % (change.line, change.detail))
    if report.notes:
        lines += ["", "Needs a person:"]
        lines.extend("  %s: %s" % (note.where, note.detail) for note in report.notes)
    if files:
        lines += [
            "",
            "Next: run python manage.py check, then load each page and compare it with how "
            "it rendered before.",
        ]
    if dry_run:
        for relpath in sorted(report.diffs):
            lines += ["", report.diffs[relpath].rstrip("\n")]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Source editing
# ---------------------------------------------------------------------------


class _Overlap(Exception):
    """Two edits to one file touch the same text."""


@dataclass
class _Edit:
    start: int
    end: int
    text: str
    change: int  # index into the planner's changes, or -1
    seq: int


class SourceFile:
    """A file read once with ``newline=""`` (keeps ``\\r\\n``), edited by non-overlapping splices."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with open(path, encoding="utf-8", newline="") as fh:
            self.text = fh.read()
        self.line_starts = [0] + [m.end() for m in _NEWLINE_RE.finditer(self.text)]
        self.edits: list[_Edit] = []
        self.broken = False
        self._tree: Optional[ast.Module] = None
        self._tree_failed = False

    def tree(self) -> Optional[ast.Module]:
        """A fresh ``ast`` of the file as read from disk (never linecache), or None."""
        if self._tree is None and not self._tree_failed:
            try:
                self._tree = ast.parse(self.text)
            except SyntaxError:
                self._tree_failed = True
        return self._tree

    def line_start(self, lineno: int) -> int:
        """Offset of the first character of 1-based ``lineno`` (len(text) past the end)."""
        if lineno - 1 < len(self.line_starts):
            return self.line_starts[lineno - 1]
        return len(self.text)

    def line_text(self, lineno: int) -> str:
        """Line ``lineno`` without its line ending."""
        start = self.line_start(lineno)
        end = self.line_start(lineno + 1) if lineno < len(self.line_starts) else len(self.text)
        return self.text[start:end].rstrip("\r\n")

    def offset(self, lineno: int, col_bytes: int) -> int:
        """``ast`` positions are UTF-8 byte columns; this is the text offset."""
        line = self.line_text(lineno)
        return self.line_start(lineno) + len(
            line.encode("utf-8")[:col_bytes].decode("utf-8", errors="ignore")
        )

    def newline(self) -> str:
        match = _NEWLINE_RE.search(self.text)
        return match.group(0) if match else "\n"

    def add(self, start: int, end: int, text: str, change: int = -1) -> None:
        for edit in self.edits:
            if start < edit.end and edit.start < end:
                raise _Overlap()
            if start == end == edit.start == edit.end:
                continue  # two insertions at one point: kept in the order added
            if start == end and edit.start < start < edit.end:
                raise _Overlap()
            if edit.start == edit.end and start < edit.start < end:
                raise _Overlap()
        self.edits.append(_Edit(start, end, text, change, len(self.edits)))

    def drop_change(self, change: int) -> None:
        self.edits = [e for e in self.edits if e.change != change]

    def result(self) -> str:
        text = self.text
        for edit in sorted(self.edits, key=lambda e: (e.start, e.seq), reverse=True):
            text = text[: edit.start] + edit.text + text[edit.end :]
        return text


def _lines(text: str) -> list[str]:
    return _LINES_RE.findall(text)


# ---------------------------------------------------------------------------
# Files the command may edit
# ---------------------------------------------------------------------------


def _installed_roots() -> list[Path]:
    import djust

    roots = [Path(djust.__file__).resolve().parent]
    candidates: list[str] = []
    try:
        candidates.extend(site.getsitepackages())
    except AttributeError:  # a virtualenv's old site module
        pass
    try:
        candidates.append(site.getusersitepackages())
    except AttributeError:
        pass
    paths = sysconfig.get_paths()
    candidates.extend(p for p in (paths.get("purelib"), paths.get("platlib")) if p)
    for candidate in candidates:
        try:
            roots.append(Path(candidate).resolve())
        except OSError:
            continue
    return roots


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _find_qualname(tree: ast.Module, qualname: str) -> Optional[ast.AST]:
    """The ``ClassDef``/``FunctionDef`` a dotted ``__qualname__`` names, or None."""
    if "<locals>" in qualname:
        return None
    body: list[ast.stmt] = tree.body
    node: Optional[ast.AST] = None
    for part in qualname.split("."):
        node = next(
            (
                n
                for n in body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == part
            ),
            None,
        )
        if node is None:
            return None
        body = node.body  # type: ignore[attr-defined]
    return node


def _source_file(obj: Any) -> Optional[Path]:
    try:
        name = inspect.getsourcefile(obj)
    except (OSError, TypeError):
        return None
    return Path(name).resolve() if name else None


def _label(cls: type) -> str:
    return "%s.%s" % (cls.__module__, cls.__qualname__)


def _self_attr_targets(target: ast.AST, self_name: str) -> list[str]:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == self_name
    ):
        return [target.attr]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for element in target.elts for n in _self_attr_targets(element, self_name)]
    return []


def _is_super_mount(node: ast.stmt) -> bool:
    call = node.value if isinstance(node, ast.Expr) else None
    if isinstance(call, ast.Await):
        call = call.value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "mount"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
    )


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------


_UNREADABLE_WORDS = {
    "override": "its get_context_data() is more than this command can follow",
    "template": "its template is chosen at runtime or could not be loaded",
    "unreadable": "its source could not be read",
    "no-template": "its template is chosen at runtime or could not be loaded",
}


class _Planner:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.installed = _installed_roots()
        self.files: dict[Path, SourceFile] = {}
        self.changes: list[Change] = []
        self.change_files: list[Path] = []
        self.notes: list[Note] = []
        #: change index of a policy deletion -> files whose edits it depends on
        self.depends: dict[int, set[Path]] = {}
        self.policy_notes: dict[int, Note] = {}

    # -- files ---------------------------------------------------------------

    def rel(self, path: Path) -> str:
        if _under(path, self.root):
            return path.relative_to(self.root).as_posix()
        return str(path)

    def installed_file(self, path: Path) -> bool:
        return any(_under(path, root) for root in self.installed)

    def editable(self, path: Path) -> bool:
        return _under(path, self.root) and not self.installed_file(path)

    def source(self, path: Path) -> Optional[SourceFile]:
        if path not in self.files:
            try:
                self.files[path] = SourceFile(path)
            except (OSError, UnicodeDecodeError):
                return None
        return self.files[path]

    def note(self, kind: str, where: str, detail: str) -> Note:
        note = Note(kind, where, detail)
        if note not in self.notes:  # several owners can reach one partial's line
            self.notes.append(note)
        return note

    def outside(self, path: Path, what: str) -> None:
        self.note(
            "outside_root",
            self.rel(path),
            "not under --root; change it by hand: %s" % what,
        )

    def edit(
        self, source: SourceFile, edits: list[tuple[int, int, str]], change: Change
    ) -> Optional[int]:
        """Record ``change`` with its splices; on an overlap the file is left as it was."""
        if source.broken:
            return None
        index = len(self.changes)
        try:
            for start, end, text in edits:
                source.add(start, end, text, index)
        except _Overlap:
            source.drop_change(index)
            source.broken = True
            return None
        self.changes.append(change)
        self.change_files.append(source.path)
        return index

    # -- finishing -------------------------------------------------------------

    def finish(self, write: bool) -> FixReport:
        dropped: set[Path] = set()
        results: dict[Path, str] = {}
        while True:
            for path, source in self.files.items():
                if path in dropped or not source.edits:
                    continue
                if source.broken:
                    dropped.add(path)
                    continue
                new = source.result()
                if path.suffix == ".py":
                    try:
                        ast.parse(new)
                    except SyntaxError:
                        dropped.add(path)
                        continue
                results[path] = new
            # A policy line deleted on a base whose subclasses' supplies were dropped stays.
            undone = [
                index
                for index, files in self.depends.items()
                if files & dropped
                and self.change_files[index] not in dropped
                and any(e.change == index for e in self.files[self.change_files[index]].edits)
            ]
            if not undone:
                break
            for index in undone:
                path = self.change_files[index]
                self.files[path].drop_change(index)
                results.pop(path, None)
                change = self.changes[index]
                self.note(
                    "policy_kept",
                    change.detail.split(":", 1)[0],
                    'kept exposure_policy = "%s" because a view that inherits it could not be '
                    "changed (see above); run --fix again after." % _LEGACY,
                )
        for path in sorted(dropped):
            self.note(
                "unchanged_file",
                self.rel(path),
                "the rewrite did not parse or its edits overlapped; nothing in this file "
                "was changed.",
            )
        live = {
            (path, e.change)
            for path, source in self.files.items()
            if path not in dropped
            for e in source.edits
        }
        report = FixReport(written=False)
        for index, change in enumerate(self.changes):
            if (self.change_files[index], index) in live:
                report.changes.append(change)
        report.changes.sort(key=lambda c: (c.file, c.line))
        report.notes = self.notes
        for path, new in sorted(results.items()):
            if path in dropped or new == self.files[path].text:
                continue
            relpath = self.rel(path)
            if write:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
            else:
                report.diffs[relpath] = "".join(
                    difflib.unified_diff(
                        _lines(self.files[path].text),
                        _lines(new),
                        "a/" + relpath,
                        "b/" + relpath,
                    )
                )
        report.written = write and bool(report.changes)
        return report


# ---------------------------------------------------------------------------
# Rewrite 1: supply the template's names, delete the legacy policy line
# ---------------------------------------------------------------------------


def _policy_owner(cls: type) -> Optional[type]:
    for klass in cls.__mro__:
        if "exposure_policy" in vars(klass):
            return klass
    return None


def _is_legacy_owner(klass: Optional[type]) -> bool:
    if klass is None:
        return False
    value = vars(klass).get("exposure_policy")
    return type(value) is str and value == _LEGACY


def _template_less_base(cls: type) -> bool:
    """A base view with no template of its own: nothing to supply."""
    from djust.checks.bindings import _overrides
    from djust.components.base import LiveComponent
    from djust.live_view import LiveView

    if inspect.getattr_static(cls, "template", None) or inspect.getattr_static(
        cls, "template_name", None
    ):
        return False
    framework = (LiveView, LiveComponent)
    return not (
        _overrides(cls, "get_template", framework)
        or _overrides(cls, "get_template_names", framework)
    )


def _user_mount(cls: type, after: Optional[type] = None) -> Optional[tuple[type, Any]]:
    from djust.management._introspect import is_user_class

    mro = cls.__mro__
    start = mro.index(after) + 1 if after is not None and after in mro else 0
    for klass in mro[start:]:
        if "mount" in vars(klass):
            if not is_user_class(klass) or klass.__module__.startswith("django."):
                return None
            return klass, vars(klass)["mount"]
    return None


def _mount_always_sets(planner: _Planner, cls: type) -> set[str]:
    """``self.x`` names the resolved ``mount()`` always assigns (top-level statements),
    following a top-level ``super().mount(...)`` into the next user class's ``mount``."""
    names: set[str] = set()
    found = _user_mount(cls)
    seen: set[type] = set()
    while found is not None and found[0] not in seen:
        klass, member = found
        seen.add(klass)
        function = inspect.unwrap(getattr(member, "__func__", member))
        path = _source_file(function)
        source = planner.source(path) if path else None
        tree = source.tree() if source else None
        node = _find_qualname(tree, function.__qualname__) if tree else None
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.args.args:
            return names
        self_name = node.args.args[0].arg
        follow = False
        for statement in node.body:
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    names.update(_self_attr_targets(target, self_name))
            elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
                names.update(_self_attr_targets(statement.target, self_name))
            elif _is_super_mount(statement):
                follow = True
        if not follow:
            break
        found = _user_mount(cls, after=klass)
    return names


def _value_expression(name: str, self_values: set[str]) -> str:
    if name in self_values:
        return "self.%s" % name
    return 'getattr(self, "%s", "")' % name


def _indent_of(source: SourceFile, lineno: int) -> str:
    line = source.line_text(lineno)
    return line[: len(line) - len(line.lstrip(" \t"))]


def _supply(
    planner: _Planner, view: type, missing: list[str], self_values: set[str]
) -> Optional[str]:
    """Write the supplies on ``view``; None when written, else why it needs a person."""
    path = _source_file(view)
    if path is None:
        return "unreadable_source"
    if not planner.editable(path):
        planner.outside(
            path,
            "supply %s from %s.get_context_data()" % (", ".join(missing), view.__qualname__),
        )
        return "outside_root"
    source = planner.source(path)
    tree = source.tree() if source else None
    node = _find_qualname(tree, view.__qualname__) if tree else None
    if source is None or not isinstance(node, ast.ClassDef):
        planner.note(
            "unreadable_source",
            _label(view),
            "its class could not be found in %s; supply %s from get_context_data() by hand."
            % (planner.rel(path), _and(missing)),
        )
        return "unreadable_source"
    nl = source.newline()
    own = next(
        (n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "get_context_data"),
        None,
    )
    has_own = "get_context_data" in vars(view)
    if not has_own:
        first = node.body[0]
        if first.lineno == node.lineno:
            return "context_by_hand"
        indent = _indent_of(source, first.lineno)
        class_indent = _indent_of(source, node.lineno)
        unit = indent[len(class_indent) :] if indent.startswith(class_indent) else "    "
        unit = unit or "    "
        body = [
            "",
            "%sdef get_context_data(self, **kwargs):" % indent,
            "%s%s# Added by djust_exposure_inventory --fix (#4209): the names the template reads."
            % (indent, unit),
            "%s%s# Declare one with state() instead when it is the view's state." % (indent, unit),
            "%s%scontext = super().get_context_data(**kwargs)" % (indent, unit),
        ]
        body += [
            '%s%scontext["%s"] = %s' % (indent, unit, name, _value_expression(name, self_values))
            for name in missing
        ]
        body.append("%s%sreturn context" % (indent, unit))
        end_line = node.end_lineno or node.lineno
        at = source.line_start(end_line + 1)
        text = nl.join(body) + nl
        if at == len(source.text) and source.text and not source.text.endswith(("\n", "\r")):
            text = nl + text
        index = planner.edit(
            source,
            [(at, at, text)],
            Change(
                planner.rel(path),
                end_line + 1,
                "context_added",
                "%s: added get_context_data() supplying %s"
                % (view.__qualname__, ", ".join(missing)),
            ),
        )
        return None if index is not None else "unchanged_file"
    if own is None:
        return "context_by_hand"
    from djust.checks.templates import _override_context_analysis, _own_scope_nodes

    returns = [n for n in _own_scope_nodes(own) if isinstance(n, ast.Return)]
    last = own.body[-1]
    if (
        len(returns) != 1
        or returns[0] is not last
        or not isinstance(last, ast.Return)
        or not isinstance(last.value, ast.Name)
        or last.value.id not in _override_context_analysis(own)[2]
    ):
        return "context_by_hand"
    indent = _indent_of(source, last.lineno)
    if source.offset(last.lineno, last.col_offset) != source.line_start(last.lineno) + len(indent):
        return "context_by_hand"
    context = last.value.id
    text = "".join(
        '%s%s["%s"] = %s%s' % (indent, context, name, _value_expression(name, self_values), nl)
        for name in missing
    )
    at = source.line_start(last.lineno)
    index = planner.edit(
        source,
        [(at, at, text)],
        Change(
            planner.rel(path),
            last.lineno,
            "context_added",
            "%s: get_context_data() now also supplies %s" % (view.__qualname__, ", ".join(missing)),
        ),
    )
    return None if index is not None else "unchanged_file"


def _delete_policy(
    planner: _Planner, owner: type, dependent_files: set[Path], gets_method: bool
) -> None:
    path = _source_file(owner)
    where = _label(owner)
    if path is None:
        planner.note(
            "unreadable_source",
            where,
            'its source could not be read; delete exposure_policy = "%s" by hand.' % _LEGACY,
        )
        return
    if not planner.editable(path):
        planner.outside(
            path, 'delete exposure_policy = "%s" from %s' % (_LEGACY, owner.__qualname__)
        )
        return
    source = planner.source(path)
    tree = source.tree() if source else None
    node = _find_qualname(tree, owner.__qualname__) if tree else None
    if source is None or not isinstance(node, ast.ClassDef):
        planner.note(
            "unreadable_source",
            where,
            'its class could not be found in %s; delete exposure_policy = "%s" by hand.'
            % (planner.rel(path), _LEGACY),
        )
        return
    statement: Optional[ast.stmt] = None
    for item in node.body:
        target: Optional[ast.expr] = None
        value: Optional[ast.expr] = None
        if isinstance(item, ast.Assign) and len(item.targets) == 1:
            target, value = item.targets[0], item.value
        elif isinstance(item, ast.AnnAssign):
            target, value = item.target, item.value
        if (
            isinstance(target, ast.Name)
            and target.id == "exposure_policy"
            and isinstance(value, ast.Constant)
            and value.value == _LEGACY
        ):
            statement = item
    if statement is None:
        planner.note(
            "policy_kept",
            where,
            'exposure_policy is not stated as a plain "%s" assignment; change it by hand.'
            % _LEGACY,
        )
        return
    first, last = statement.lineno, statement.end_lineno or statement.lineno
    shares = any(
        other is not statement
        and (other.lineno <= last and (other.end_lineno or other.lineno) >= first)
        for other in ast.walk(node)
        if isinstance(other, ast.stmt) and other is not node and not _contains(statement, other)
    )
    if shares or first == node.lineno:
        planner.note(
            "policy_kept",
            where,
            'kept exposure_policy = "%s": another statement shares its line; delete it by hand.'
            % _LEGACY,
        )
        return
    start, end = source.line_start(first), source.line_start(last + 1)
    replacement = ""
    if len(node.body) == 1 and not gets_method:
        replacement = "%spass%s" % (_indent_of(source, first), source.newline())
    index = planner.edit(
        source,
        [(start, end, replacement)],
        Change(
            planner.rel(path),
            first,
            "policy_deleted",
            '%s: deleted exposure_policy = "%s"' % (owner.__qualname__, _LEGACY),
        ),
    )
    if index is not None:
        planner.depends[index] = dependent_files - {path}


def _contains(outer: ast.AST, inner: ast.AST) -> bool:
    return any(node is inner for node in ast.walk(outer))


def _rewrite_policies(planner: _Planner, views: list[type]) -> None:
    from djust._exposure_unsupplied import context_processor_names
    from djust.checks.templates import _get_context_data_reaches_base, held_template_reads
    from djust.management._exposure_inventory import inventory_view
    from djust.management._introspect import is_user_class, walk_subclasses

    legacy = [v for v in views if _is_legacy_owner(_policy_owner(v))]
    if not legacy:
        return
    processor_names = context_processor_names()
    added: dict[type, set[str]] = {}
    blocked: dict[type, list[str]] = {}  # owner -> labels of views that need a person
    files_for: dict[type, set[Path]] = {}
    gets_method: set[type] = set()
    for view in sorted(legacy, key=lambda c: len(c.__mro__)):
        owner = _policy_owner(view)
        assert owner is not None
        blocked.setdefault(owner, [])
        files_for.setdefault(owner, set())
        label = _label(view)
        reads = held_template_reads(view, processor_names)
        if isinstance(reads, str):
            if reads == "no-template" and _template_less_base(view):
                continue  # a base: its subclasses render, and are supplied themselves
            planner.note(
                "unreadable_view",
                label,
                "%s; supply what its template reads by hand." % _UNREADABLE_WORDS[reads],
            )
            blocked[owner].append(label)
            continue
        unsupplied = [name for name, _line in reads.unsupplied()]
        if not _get_context_data_reaches_base(view):
            if unsupplied:
                planner.note(
                    "context_not_built_on_super",
                    label,
                    "get_context_data() does not build on super().get_context_data(), so "
                    "under 1.3 too these rendered empty: %s" % ", ".join(unsupplied),
                )
            continue
        inherited: set[str] = set()
        for base in view.__mro__[1:]:
            inherited |= added.get(base, set())
        missing = [name for name in unsupplied if name not in inherited]
        will_supply = set(reads.supplied) | set(missing) | inherited
        seen_counts: set[str] = set()
        for name, _line in reads.refs:
            if not name.endswith("_count") or name in seen_counts:
                continue
            stem = name[: -len("_count")]
            if (
                stem
                and stem in will_supply
                and name not in reads.supplied
                and name not in reads.held
                and not ("*" in reads.silenced or name in reads.silenced)
            ):
                seen_counts.add(name)
                planner.note(
                    "count_name",
                    label,
                    "{{ %s }}: write {{ %s|length }} (1.3 generated %s from %s; 2.0 does not)."
                    % (name, stem, name, stem),
                )
        if not missing:
            continue
        kinds = {entry["name"]: entry["kind"] for entry in inventory_view(view)["names"]}
        self_values = {n for n, k in kinds.items() if k in ("class_value", "property")}
        self_values |= _mount_always_sets(planner, view)
        before = len(planner.changes)
        outcome = _supply(planner, view, missing, self_values)
        if outcome is None:
            added[view] = set(missing)
            if "get_context_data" not in vars(view):
                gets_method.add(view)
            path = planner.change_files[before] if len(planner.changes) > before else None
            if path is not None:
                files_for[owner].add(path)
            planner.note(
                "choose_state_or_context",
                label,
                "%s %s supplied from get_context_data(). Declare a name with state() instead "
                'when it is the view\'s state; add persist="server" only when it must survive '
                "a reconnect." % (_and(missing), "is" if len(missing) == 1 else "are"),
            )
            continue
        if outcome == "context_by_hand":
            planner.note(
                "context_by_hand",
                label,
                "add these to get_context_data(): %s" % ", ".join(missing),
            )
        elif outcome == "unchanged_file":
            planner.note(
                "unchanged_file",
                label,
                "its file could not be edited safely; supply %s from get_context_data() by "
                "hand." % ", ".join(missing),
            )
        blocked[owner].append(label)

    processed = set(legacy)
    for owner, labels in blocked.items():
        # Views that inherit this line but were not part of this run flip too.
        others = [
            _label(cls)
            for cls in [owner, *walk_subclasses(owner)]
            if cls not in processed
            and is_user_class(cls)
            and _policy_owner(cls) is owner
            and not _template_less_base(cls)
            and cls.__dict__.get("abstract") is not True
        ]
        if labels or others:
            reason = (
                "%s needs a person (see above)" % _and(labels)
                if labels
                else "%s also inherit%s it and %s not part of this run"
                % (
                    _and(others),
                    "s" if len(others) == 1 else "",
                    "is" if len(others) == 1 else "are",
                )
            )
            planner.note(
                "policy_kept",
                _label(owner),
                'kept exposure_policy = "%s" because %s; run --fix again after.'
                % (_LEGACY, reason),
            )
            continue
        _delete_policy(planner, owner, files_for[owner], owner in gets_method)


# ---------------------------------------------------------------------------
# Rewrite 2: annotate unannotated handler parameters as Any
# ---------------------------------------------------------------------------


def _any_import(source: SourceFile) -> "tuple[bool, Optional[int]]":
    """``(usable, offset)``: whether ``Any`` can be used, and where to insert the import
    (None when it is already bound)."""
    tree = source.tree()
    if tree is None:
        return False, None
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module in (
            "typing",
            "typing_extensions",
        ):
            for alias in statement.names:
                if alias.name == "Any" and alias.asname in (None, "Any"):
                    return True, None
    for statement in tree.body:
        bound: list[str] = []
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            bound = [(a.asname or a.name).split(".")[0] for a in statement.names]
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound = [statement.name]
        elif isinstance(statement, ast.Assign):
            bound = [t.id for t in statement.targets if isinstance(t, ast.Name)]
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)) and isinstance(
            statement.target, ast.Name
        ):
            bound = [statement.target.id]
        if "Any" in bound or "*" in bound:
            return False, None
    last: Optional[ast.stmt] = None
    for index, statement in enumerate(tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            last = statement
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            last = statement
            continue
        break
    if last is None:
        return True, 0
    return True, source.line_start((last.end_lineno or last.lineno) + 1)


def _rewrite_parameters(planner: _Planner, owners: list[tuple[type, str]]) -> None:
    from djust._framework_code import is_djust_module
    from djust._parameter_contract import FRAMEWORK_ARGUMENT_NAMES
    from djust._parameter_metadata import component_stop, declared_handlers, view_stop

    seen: set[Any] = set()
    import_added: dict[Path, bool] = {}
    for owner, kind in owners:
        stop = view_stop if kind == "view" else component_stop
        for handler in declared_handlers(owner, stop, server_functions=True):
            function = inspect.unwrap(handler.function)
            if function in seen:
                continue
            seen.add(function)
            if is_djust_module(getattr(function, "__module__", "") or ""):
                continue
            label = "%s.%s" % (function.__module__, function.__qualname__)
            code = getattr(function, "__code__", None)
            if code is None:
                continue
            path = Path(code.co_filename).resolve()
            if planner.installed_file(path):
                continue
            decorators = getattr(handler.function, "_djust_decorators", {}) or {}
            metadata = decorators.get("event_handler") or decorators.get("server_function") or {}
            if metadata.get("parameter_policy") == _LEGACY:
                planner.note(
                    "legacy_handler_pin",
                    label,
                    'delete parameter_policy="%s" once its templates send dj-value-*.' % _LEGACY,
                )
            source = planner.source(path) if path.exists() else None
            tree = source.tree() if source else None
            node = _find_qualname(tree, function.__qualname__) if tree else None
            if source is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                planner.note(
                    "unreadable_source",
                    label,
                    "its source could not be located; annotate its parameters by hand.",
                )
                continue
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaults: dict[int, ast.expr] = {}
            for arg, default in zip(
                positional[len(positional) - len(args.defaults) :], args.defaults
            ):
                defaults[id(arg)] = default
            for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
                if kw_default is not None:
                    defaults[id(arg)] = kw_default
            skip_first = not isinstance(handler.member, staticmethod)
            params = (positional[1:] if skip_first else positional) + list(args.kwonlyargs)
            todo: list[ast.arg] = []
            for arg in params:
                name = arg.arg
                if name.startswith("_") or name in FRAMEWORK_ARGUMENT_NAMES:
                    advice = "Declare field: str instead." if name == "_target" else "rename it."
                    planner.note(
                        "reserved_parameter",
                        label,
                        "parameter %s is reserved; strict never sends it. %s" % (name, advice),
                    )
                    continue
                if arg.annotation is None:
                    todo.append(arg)
            if not todo:
                continue
            names = [arg.arg for arg in todo]
            if not planner.editable(path):
                planner.outside(path, "annotate %s of %s as Any" % (", ".join(names), label))
                continue
            usable, insert_at = _any_import(source)
            if not usable:
                planner.note(
                    "unchanged_file",
                    planner.rel(path),
                    "the module binds the name Any to something else; annotate %s of %s by hand."
                    % (", ".join(names), label),
                )
                continue
            edits: list[tuple[int, int, str]] = []
            for arg in todo:
                end = source.offset(arg.end_lineno or arg.lineno, arg.end_col_offset or 0)
                default = defaults.get(id(arg))
                if default is None:
                    edits.append((end, end, ": Any"))
                else:
                    default_at = source.offset(default.lineno, default.col_offset)
                    edits.append((end, default_at, ": Any = "))
            if insert_at is not None and not import_added.get(path):
                text = "from typing import Any" + source.newline()
                if insert_at == len(source.text) and not source.text.endswith(("\n", "\r")):
                    text = source.newline() + text
                edits.append((insert_at, insert_at, text))
            index = planner.edit(
                source,
                edits,
                Change(
                    planner.rel(path),
                    node.lineno,
                    "parameter_annotated",
                    "%s: annotated %s as Any" % (function.__qualname__, ", ".join(names)),
                ),
            )
            if index is None:
                continue
            if insert_at is not None:
                import_added[path] = True
            planner.note(
                "parameter_types",
                label,
                "%s %s annotated Any. Give %s its real type (str, int, Optional[int], ...): "
                "strict dispatch then converts and checks it."
                % (
                    _and(names),
                    "is" if len(names) == 1 else "are",
                    "it" if len(names) == 1 else "each",
                ),
            )


# ---------------------------------------------------------------------------
# Rewrite 3: data-X -> dj-value-X in templates
# ---------------------------------------------------------------------------


def _value_attribute(attribute: str) -> str:
    rest = attribute[len("data-") :]
    if rest.lower().startswith("dj-"):
        rest = rest[3:]
    return "dj-value-" + rest


def _tag_end(text: str, start: int) -> Optional[int]:
    """Offset just past the ``>`` closing the start tag at ``start``, outside quotes
    and outside ``{{ }}`` / ``{% %}``."""
    i = start + 1
    length = len(text)
    while i < length:
        if text.startswith(("{{", "{%"), i):
            close = "}}" if text[i + 1] == "{" else "%}"
            j = text.find(close, i + 2)
            if j < 0:
                return None
            i = j + 2
            continue
        char = text[i]
        if char in "\"'":
            j = i + 1
            while j < length and text[j] != char:
                if text.startswith(("{{", "{%"), j):
                    close = "}}" if text[j + 1] == "{" else "%}"
                    k = text.find(close, j + 2)
                    if k < 0:
                        return None
                    j = k + 2
                    continue
                j += 1
            if j >= length:
                return None
            i = j + 1
            continue
        if char == ">":
            return i + 1
        if char == "<" and i > start:
            return None
        i += 1
    return None


def _attr_re(attribute: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w:-])%s(?=\s*=|\s|/?>)" % re.escape(attribute), re.IGNORECASE)


def _locate_tag(
    source: SourceFile, line: int, binding_attribute: str, attributes: list[str]
) -> Optional[tuple[int, int]]:
    text = source.text
    start = source.line_start(line)
    end = source.line_start(line + 1) if line < len(source.line_starts) else len(text)
    pattern = re.compile(r"(?<![\w:-])%s\s*=" % re.escape(binding_attribute), re.IGNORECASE)
    tags: set[tuple[int, int]] = set()
    for match in pattern.finditer(text, start, end):
        open_at = text.rfind("<", 0, match.start())
        if open_at < 0:
            continue
        close_at = _tag_end(text, open_at)
        if close_at is None or close_at <= match.start():
            continue
        tag = text[open_at:close_at]
        if all(_attr_re(attribute).search(tag) for attribute in attributes):
            tags.add((open_at, close_at))
    return next(iter(tags)) if len(tags) == 1 else None


def _rename_in_tag(tag: str, renames: list[tuple[str, str]]) -> Optional[str]:
    from djust._template_bindings import _Markup

    new = tag
    for old, replacement in renames:
        matches = list(_attr_re(old).finditer(new))
        if len(matches) != 1:
            return None
        match = matches[0]
        new = new[: match.start()] + replacement + new[match.end() :]
    try:
        parser = _Markup(new)
        parser.feed(new)
        parser.close()
    except (AssertionError, ValueError):
        return None
    if len(parser.elements) != 1:
        return None
    present = {name for name, _value in parser.elements[0].attrs}
    if any(old.lower() in present for old, _new in renames):
        return None
    if not all(replacement.lower() in present for _old, replacement in renames):
        return None
    return new


def _rewrite_markup(planner: _Planner, owners: list[tuple[type, str]]) -> None:
    from djust._parameter_metadata import component_stop, declared_handlers, view_stop
    from djust._template_bindings import django_engine
    from djust.checks.bindings import scan_owner

    engine = django_engine()
    if engine is None:
        return
    cache: dict[Any, Any] = {}
    # (file, line, binding attribute, data attribute) -> decisions, and the facts to print
    decisions: dict[tuple[str, int, str, str], list[bool]] = {}
    details: dict[tuple[str, int, str, str], str] = {}
    for owner, kind in owners:
        try:
            report = scan_owner(owner, kind, engine, cache)
        except Exception:  # noqa: BLE001 -- a template the scan cannot read is not rewritten
            continue
        stop = view_stop if kind == "view" else component_stop
        handlers = {h.name: h for h in declared_handlers(owner, stop, server_functions=True)}
        for result in report.results:
            binding = result.binding
            if (
                binding.region != "live"
                or binding.tag is not None
                or binding.unresolved is not None
                or binding.malformed is not None
                or binding.name not in handlers
                or not binding.payload.data_keys
                or not binding.file
            ):
                continue
            function = inspect.unwrap(handlers[binding.name].function)
            try:
                params = inspect.signature(function).parameters
            except (TypeError, ValueError):
                continue
            catch_all = any(p.kind is p.VAR_KEYWORD for p in params.values())
            for key, attribute in binding.payload.data_keys.items():
                where = (binding.file, binding.line, binding.attribute, attribute)
                if key in params and key not in binding.payload.values:
                    decisions.setdefault(where, []).append(True)
                    details[where] = "%s -> %s (%s: %s() declares %s)" % (
                        attribute,
                        _value_attribute(attribute),
                        binding.label,
                        binding.name,
                        key,
                    )
                    continue
                if key in binding.payload.values:
                    continue
                decisions.setdefault(where, []).append(False)
                if catch_all:
                    planner.note(
                        "catch_all_data",
                        "%s:%d" % (planner.rel(Path(binding.file).resolve()), binding.line),
                        "%s feeds **%s; if the handler reads it, rename it to %s"
                        % (
                            attribute,
                            next(n for n, p in params.items() if p.kind is p.VAR_KEYWORD),
                            _value_attribute(attribute),
                        ),
                    )

    # Group the renames every owner agrees on by (file, line, binding attribute).
    groups: dict[tuple[str, int, str], list[str]] = {}
    for where, votes in sorted(decisions.items()):
        file, line, binding_attribute, attribute = where
        if not any(votes):
            continue
        if not all(votes):
            planner.note(
                "ambiguous_markup",
                "%s:%d" % (planner.rel(Path(file).resolve()), line),
                "%s reaches handlers that disagree on it; rename it to %s by hand where "
                "the handler declares it." % (attribute, _value_attribute(attribute)),
            )
            continue
        groups.setdefault((file, line, binding_attribute), []).append(attribute)

    # Locate each group's tag; merge groups that land on the same tag.
    tags: dict[tuple[Path, int, int], dict[str, tuple[int, str]]] = {}
    for (file, line, binding_attribute), attributes in sorted(groups.items()):
        path = Path(file).resolve()
        where = "%s:%d" % (planner.rel(path), line)
        if not path.is_file():
            continue
        if not planner.editable(path):
            if not planner.installed_file(path):
                planner.outside(
                    path,
                    "line %d: rename %s"
                    % (
                        line,
                        ", ".join("%s to %s" % (a, _value_attribute(a)) for a in attributes),
                    ),
                )
            continue
        source = planner.source(path)
        if source is None:
            continue
        located = _locate_tag(source, line, binding_attribute, attributes)
        if located is None:
            planner.note(
                "ambiguous_markup",
                where,
                "could not find one tag with %s; rename %s by hand."
                % (binding_attribute, ", ".join(attributes)),
            )
            continue
        entry = tags.setdefault((path, *located), {})
        for attribute in attributes:
            entry.setdefault(attribute, (line, details[(file, line, binding_attribute, attribute)]))

    for (path, start, end), renames in sorted(tags.items()):
        source = planner.source(path)
        assert source is not None
        tag = source.text[start:end]
        new = _rename_in_tag(tag, [(old, _value_attribute(old)) for old in renames])
        if new is None:
            first_line = min(line for line, _detail in renames.values())
            planner.note(
                "ambiguous_markup",
                "%s:%d" % (planner.rel(path), first_line),
                "the tag could not be rewritten safely; rename %s by hand." % ", ".join(renames),
            )
            continue
        ordered = sorted(renames.items(), key=lambda item: item[1][0])
        index = planner.edit(
            source,
            [(start, end, new)],
            Change(planner.rel(path), ordered[0][1][0], "data_argument_renamed", ordered[0][1][1]),
        )
        if index is None:
            continue
        # One splice per tag; each renamed attribute is its own printed change.
        for _attribute, (line, detail) in ordered[1:]:
            planner.changes.append(Change(planner.rel(path), line, "data_argument_renamed", detail))
            planner.change_files.append(path)
            source.edits.append(_Edit(start, start, "", len(planner.changes) - 1, -1))


# ---------------------------------------------------------------------------
# Python-built markup and _target reads: reported, never rewritten
# ---------------------------------------------------------------------------


def _report_python(planner: _Planner, owners: list[tuple[type, str]]) -> None:
    from djust.checks.upgrade import check_python_markup_arguments, check_target_parameter

    # Only messages about the owners in scope: T029 by the owner's lines, V034 by its label.
    spans: dict[str, list[tuple[int, int]]] = {}
    labels: list[str] = []
    for owner, _kind in owners:
        labels.append(_label(owner) + ".")
        path = _source_file(owner)
        source = planner.source(path) if path is not None and path.is_file() else None
        tree = source.tree() if source else None
        node = _find_qualname(tree, owner.__qualname__) if tree else None
        if path is not None and isinstance(node, ast.ClassDef):
            spans.setdefault(str(path), []).append((node.lineno, node.end_lineno or node.lineno))
    for message in check_python_markup_arguments(None):
        file_path = getattr(message, "file_path", "") or ""
        line = getattr(message, "line_number", None) or 0
        resolved = Path(file_path).resolve() if file_path else None
        if resolved is None or not any(
            first <= line <= last for first, last in spans.get(str(resolved), [])
        ):
            continue
        text = str(message.msg)
        prefix = "%s:%d: " % (file_path, line)
        if text.startswith(prefix):
            text = text[len(prefix) :]
        planner.note(
            "python_markup",
            "%s:%d" % (planner.rel(resolved), line),
            "%s %s" % (text, message.hint or ""),
        )
    for message in check_target_parameter(None):
        text = str(message.msg)
        label, _sep, rest = text.partition(": ")
        if not rest.startswith("reads _target") or not any(
            label.startswith(prefix) for prefix in labels
        ):
            continue  # a declared _target is a reserved_parameter note
        planner.note("target_read", label, "%s %s" % (rest, message.hint or ""))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _owners_in_scope(views: list[type], scoped: bool) -> list[tuple[type, str]]:
    from djust.management._introspect import is_user_class

    owners = [(cls, "view") for cls in views]
    if not scoped:
        from djust.checks.bindings import _owners

        known = set(views)
        owners += [
            (cls, kind)
            for cls, kind in _owners()
            if kind == "component" and cls not in known and is_user_class(cls)
        ]
    return owners


def fix_views(classes: list[type], *, root: Path, write: bool, scoped: bool) -> FixReport:
    """Plan every rewrite for ``classes`` from the unedited classes, then write (or diff).

    ``scoped`` (``--view`` or ``--app``): only these views' handlers and templates are
    rewritten; otherwise every user LiveComponent's too.
    """
    from djust.management._introspect import is_user_class

    planner = _Planner(Path(root).resolve())
    views = [cls for cls in classes if is_user_class(cls)]
    owners = _owners_in_scope(views, scoped)
    _rewrite_policies(planner, views)
    _rewrite_parameters(planner, owners)
    _rewrite_markup(planner, owners)
    _report_python(planner, owners)
    return planner.finish(write)


__all__ = ["Change", "FixReport", "Note", "SourceFile", "fix_views", "format_fix_report"]
