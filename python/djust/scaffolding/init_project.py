"""Add djust to an existing Django project (``djust init``).

Planning is kept separate from side effects: the ``plan_*`` functions compute
new file contents without touching disk, so ``--dry-run`` shows exactly what
``apply_changes`` would write.
"""

import ast
import difflib
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from . import templates as T

SETTINGS_MARKER = "# --- djust (added by djust init) ---"
TEMPLATES_MARKER = T.TEMPLATES_MARKER

DONE = "done"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
ATTENTION = "attention"
PLANNED = "planned"  # --dry-run: the step would change a file

_SETTINGS_MODULE_RE = re.compile(
    r"""os\.environ\.setdefault\(\s*["']DJANGO_SETTINGS_MODULE["']\s*,\s*["']([\w.]+)["']\s*\)"""
)

_STOCK_ASGI = ast.parse(
    "import os\n"
    "from django.core.asgi import get_asgi_application\n"
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'x')\n"
    "application = get_asgi_application()\n"
).body


class InitError(RuntimeError):
    """``djust init`` cannot proceed; nothing has been written."""


@dataclass
class Project:
    root: Path
    settings_module: str
    settings_path: Path
    asgi_path: Path

    @property
    def package(self) -> str:
        return self.settings_module.rpartition(".")[0]

    @property
    def asgi_module(self) -> str:
        return "%s.asgi" % self.package if self.package else "asgi"


@dataclass
class FileChange:
    path: Path
    old: Optional[str]  # None when the file does not exist yet
    new: str

    def diff(self, root: Path) -> str:
        name = str(self.path.relative_to(root))
        return "".join(
            difflib.unified_diff(
                (self.old or "").splitlines(keepends=True),
                self.new.splitlines(keepends=True),
                fromfile="a/%s" % name,
                tofile="b/%s" % name,
            )
        )


@dataclass
class Step:
    name: str
    status: str
    detail: str
    # What a DONE step would do, worded for --dry-run, where nothing is done.
    # A DONE step without it falls back to its ``detail`` in a dry run.
    planned: str = ""


def _read(path: Path) -> str:
    # newline="" keeps CRLF files byte-identical outside the lines we add.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def render_settings_block(asgi_module: str) -> str:
    return T.SETTINGS_BLOCK % {"asgi_module": asgi_module}


def detect_project(root: Path, settings_module: Optional[str] = None) -> Project:
    """Locate the settings and ASGI files without importing project code."""
    manage = root / "manage.py"
    if not manage.is_file():
        raise InitError(
            "No manage.py in %s. Run `djust init` from the directory containing manage.py." % root
        )
    if settings_module is None:
        match = _SETTINGS_MODULE_RE.search(manage.read_text())
        if not match:
            raise InitError(
                "Could not read DJANGO_SETTINGS_MODULE from manage.py. "
                "Pass it explicitly, e.g. `djust init --settings mysite.settings`."
            )
        settings_module = match.group(1)

    module_path = root.joinpath(*settings_module.split("."))
    settings_path = module_path.with_suffix(".py")
    package = settings_module.rpartition(".")[0]
    asgi_module = "%s.asgi" % package if package else "asgi"
    if not settings_path.is_file():
        if (module_path / "__init__.py").is_file():
            raise InitError(
                "%s is a settings package; `djust init` only edits a single settings "
                "file. Add this block to the module your environment loads:\n\n%s"
                % (settings_module, render_settings_block(asgi_module))
            )
        raise InitError(
            "Settings file %s not found. Pass --settings with the right module." % settings_path
        )
    if (settings_path.parent / "__init__.py").is_file() and (
        settings_path.parent.parent / "__init__.py"
    ).is_file():
        # e.g. config.settings.local: the settings live in a package, and
        # asgi.py sits a level up, not beside this file.
        raise InitError(
            "%s lives in the settings package %s; `djust init` only edits a single "
            "settings file. Add this block to the module your environment loads:\n\n%s"
            % (
                settings_module,
                package,
                render_settings_block("%s.asgi" % package.rpartition(".")[0]),
            )
        )
    return Project(root, settings_module, settings_path, settings_path.with_name("asgi.py"))


def plan_settings(project: Project) -> Tuple[Optional[FileChange], Step]:
    name = str(project.settings_path.relative_to(project.root))
    old = _read(project.settings_path)
    if SETTINGS_MARKER in old:
        return None, Step(name, UNCHANGED, "djust block already present")
    newline = "\r\n" if "\r\n" in old else "\n"
    block = render_settings_block(project.asgi_module).replace("\n", newline)
    new = old.rstrip("\r\n") + newline * 3 + block
    return FileChange(project.settings_path, old, new), Step(
        name, DONE, "djust block appended", "append djust block"
    )


_DJUST_BACKEND = "djust.template_backend.DjustTemplateBackend"
_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
# Methods that change a list in place: a TEMPLATES edited like this is not the
# literal the file shows.
_LIST_MUTATORS = frozenset(
    {"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "__setitem__"}
)


@dataclass
class TemplatesPlan:
    """What ``djust init`` does about ``TEMPLATES``: ``kind`` is one of
    ``add`` (append the block), ``present`` (djust is already configured),
    ``misordered`` or ``unrecognised`` (reported, not edited)."""

    kind: str
    detail: str


def _templates_literal(tree: ast.Module) -> Tuple[Optional[List[str]], str]:
    """The BACKEND of each entry of the file's TEMPLATES literal, in order.

    Returns ``(None, reason)`` unless TEMPLATES is assigned exactly once, at
    the top level, to a list or tuple of dict literals that each name a string
    BACKEND, and is never reassigned, augmented or mutated in place.
    """
    assigns = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "TEMPLATES" for t in node.targets):
                assigns.append(node)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "TEMPLATES" and node.value:
                assigns.append(node)
    if not assigns:
        return None, "TEMPLATES is not assigned in this file"
    if len(assigns) > 1:
        return None, "TEMPLATES is assigned more than once"
    assign = assigns[0]
    simple_targets = {
        id(t)
        for t in (assign.targets if isinstance(assign, ast.Assign) else [assign.target])
        if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "TEMPLATES":
            if isinstance(node.ctx, (ast.Store, ast.Del)) and id(node) not in simple_targets:
                return None, "TEMPLATES is changed after its assignment"
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _LIST_MUTATORS
            and isinstance(node.value, ast.Name)
            and node.value.id == "TEMPLATES"
        ):
            return None, "TEMPLATES is changed after its assignment"
    value = assign.value
    if not isinstance(value, (ast.List, ast.Tuple)):
        return None, "TEMPLATES is not a list literal"
    backends = []
    for entry in value.elts:
        if not isinstance(entry, ast.Dict) or None in entry.keys:
            return None, "a TEMPLATES entry is not a plain dict literal"
        backend = None
        for key, val in zip(entry.keys, entry.values):
            if isinstance(key, ast.Constant) and key.value == "BACKEND":
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    backend = val.value
        if backend is None:
            return None, "a TEMPLATES entry has no literal BACKEND"
        backends.append(backend)
    return backends, ""


def _names_djust_backend(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "DjustTemplateBackend" in node.value
        for node in ast.walk(tree)
    )


def classify_templates(source: str) -> TemplatesPlan:
    """Decide what to do about the TEMPLATES in ``source`` without running it."""
    try:
        # A UTF-8 byte order mark is valid in a Python file, not in ast.parse's text.
        tree = ast.parse(source.lstrip("\ufeff"))
    except SyntaxError:
        return TemplatesPlan("unrecognised", "the settings file does not parse")
    backends, reason = _templates_literal(tree)
    if _names_djust_backend(tree):
        if backends is not None:
            djust_at = next(
                (i for i, b in enumerate(backends) if "DjustTemplateBackend" in b), None
            )
            if djust_at is not None and _DJANGO_BACKEND in backends[:djust_at]:
                return TemplatesPlan(
                    "misordered",
                    "DjangoTemplates comes before DjustTemplateBackend",
                )
        return TemplatesPlan("present", "DjustTemplateBackend already configured")
    if backends is None:
        return TemplatesPlan("unrecognised", reason)
    if _DJANGO_BACKEND not in backends:
        return TemplatesPlan("unrecognised", "no DjangoTemplates entry to build on")
    return TemplatesPlan("add", "")


def plan_templates(
    project: Project, source: str, opt_in: bool = False
) -> Tuple[Optional[str], Step, Optional[str]]:
    """The TEMPLATES block to append to ``source`` (or None), its step, and a
    note with the snippet to add by hand.

    TEMPLATES is only edited when ``opt_in`` (``djust init --templates``) and
    the setting is one init can read with certainty. Otherwise the step is
    informational (``skipped``, exit code 0); it is ATTENTION only when the
    user asked for the edit and it could not be done.
    """
    name = "TEMPLATES"
    if TEMPLATES_MARKER in source:
        return None, Step(name, UNCHANGED, "djust block already present"), None
    plan = classify_templates(source)
    if plan.kind == "present":
        return None, Step(name, UNCHANGED, plan.detail), None
    if opt_in and plan.kind == "add":
        return (
            T.TEMPLATES_BLOCK,
            Step(name, DONE, "DjustTemplateBackend added first", "add DjustTemplateBackend first"),
            None,
        )
    settings_name = project.settings_path.relative_to(project.root)
    if plan.kind == "misordered":
        detail = "%s; not reordered" % plan.detail
        note = (
            "%s lists DjangoTemplates before DjustTemplateBackend, so Django renders every "
            "template it finds (djust.C016). djust init never reorders a TEMPLATES; move the "
            "DjustTemplateBackend entry first by hand if you want djust's engine to render "
            "your templates." % settings_name
        )
    else:
        if plan.kind == "add":
            detail = "not edited; --templates adds DjustTemplateBackend first"
            why = "djust init does not edit TEMPLATES unless you pass --templates"
        else:
            detail = "%s; not edited" % plan.detail
            why = "djust init cannot edit it (%s)" % plan.detail
        note = (
            "%s TEMPLATES was not changed: %s. LiveViews render with djust's engine whatever "
            "TEMPLATES says. To render your other templates with djust's engine too, put the "
            "djust entry first (read the notes in the installation guide on what djust's "
            "engine does not render like Django's):\n\n%s"
            % (settings_name, why, T.TEMPLATES_ENTRY_SNIPPET)
        )
    return None, Step(name, ATTENTION if opt_in else SKIPPED, detail), note


def render_asgi(project: Project) -> str:
    return T.ASGI_PY % {
        "project_name": project.package or project.settings_module,
        "settings_module": project.settings_module,
    }


def _stock_asgi_settings(source: str) -> Optional[str]:
    """The settings module named by Django's ``startproject`` asgi.py, in any
    formatting; None when the file is anything else."""
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return None
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]  # module docstring
    if len(body) != len(_STOCK_ASGI):
        return None
    settings_module = None
    for got, want in zip(body, _STOCK_ASGI):
        if isinstance(want, ast.Expr):
            # The settings module string differs per project; compare the call shape.
            call = got.value if isinstance(got, ast.Expr) else None
            if not isinstance(call, ast.Call) or ast.dump(call.func) != ast.dump(want.value.func):
                return None
            args = call.args
            if len(args) != 2 or call.keywords:
                return None
            if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
                return None
            if args[0].value != "DJANGO_SETTINGS_MODULE":
                return None
            settings_module = args[1].value
        elif ast.dump(got) != ast.dump(want):
            return None
    return settings_module


def plan_asgi(project: Project) -> Tuple[Optional[FileChange], Step, Optional[str]]:
    name = str(project.asgi_path.relative_to(project.root))
    new = render_asgi(project)
    if not project.asgi_path.exists():
        return FileChange(project.asgi_path, None, new), Step(name, DONE, "created", "create"), None
    old = _read(project.asgi_path)
    if "LiveViewConsumer" in old:
        return None, Step(name, UNCHANGED, "already routes LiveView WebSockets"), None
    stock_settings = _stock_asgi_settings(old)
    if stock_settings == project.settings_module:
        change = FileChange(project.asgi_path, old, new)
        return (
            change,
            Step(name, DONE, "replaced Django's default", "replace Django's default"),
            None,
        )
    if stock_settings is not None:
        detail = "uses %s, not %s; merge the djust ASGI app by hand" % (
            stock_settings,
            project.settings_module,
        )
        return None, Step(name, ATTENTION, detail), new
    return None, Step(name, ATTENTION, "customized; merge the djust ASGI app by hand"), new


_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_REQUIREMENT_EXTRAS_RE = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*\[([^\]]*)\]")
_INCLUDE_RE = re.compile(r"^\s*(?:-r|--requirement)[\s=]+(\S+)")
_ALREADY_DECLARED = "djust, channels and uvicorn already declared in pyproject.toml"
_NOTHING_FOR_UV = "nothing for uv to add (see the pyproject.toml step)"
FIRST_LIVEVIEW_URL = "https://docs.djust.org/getting-started/first-liveview/"


def requirements() -> List[str]:
    from .generator import djust_requirement

    return [djust_requirement(), "channels>=4.0", "uvicorn[standard]>=0.30"]


def _interpreter(env_dir: Path) -> Path:
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def find_project_python(root: Path, environ: Mapping[str, str]) -> Optional[Path]:
    """The project's own interpreter; never an environment from outside the project."""
    local = _interpreter(root / ".venv")
    if local.exists():
        return local
    active = environ.get("VIRTUAL_ENV")
    if active:
        env_dir = Path(active).resolve()
        if env_dir.is_relative_to(root.resolve()) and _interpreter(env_dir).exists():
            return _interpreter(env_dir)
    return None


@dataclass
class PackageAction:
    kind: str  # "uv", "poetry", "requirements", or "none"
    command: List[str]
    runnable: bool
    # Requirements the command cannot add safely (a marker-declared package that
    # lacks an extra): the user adds them by hand, ``init_project`` reports them.
    manual: List[str] = field(default_factory=list)


def _extras(requirement: str) -> set:
    """The extras a PEP 508 requirement string asks for, canonicalised."""
    found = _REQUIREMENT_EXTRAS_RE.match(requirement)
    if not found:
        return set()
    return {_canonical_name(extra) for extra in found.group(1).split(",") if extra.strip()}


def _has_marker(requirement: str) -> bool:
    """Whether a PEP 508 requirement carries an environment marker (``; python_version...``).

    After a URL reference the marker must be separated by whitespace, because a
    URL may itself contain ``;``.
    """
    body = requirement.split(" #", 1)[0]
    if "@" in body:
        return re.search(r"\s;", body) is not None
    return ";" in body


def _declared_dependencies(pyproject: Path) -> Optional[dict]:
    """``[project].dependencies`` as {canonical name: [(extras, marked)]}, or None when unreadable.

    One tuple per declaration, so duplicate declarations (a marked pair) stay
    separate instead of their extras being unioned. Names and extras are read,
    the specifier is deliberately left alone. Every PEP 508 form counts
    (markers, a ``name @ url`` reference).
    """
    try:
        data = tomllib.loads(_read(pyproject))
    except (OSError, ValueError):
        return None
    declared: dict = {}
    for entry in data.get("project", {}).get("dependencies", []):
        match = _REQUIREMENT_NAME_RE.match(entry) if isinstance(entry, str) else None
        if match:
            declared.setdefault(_canonical_name(match.group(1)), []).append(
                (_extras(entry), _has_marker(entry))
            )
    return declared


def _plan_uv_requirements(pyproject: Path) -> Tuple[List[str], List[str]]:
    """``(needed, manual)``: what ``uv add`` is given, and what the user adds by hand.

    ``uv add`` rewrites the specifier of a package it is given, so one the
    project already declares is left out (#3296), as ``plan_requirements`` does
    for requirements.txt. A declared package that lacks an extra the scaffold
    needs (``uvicorn`` without ``[standard]``, which carries the WebSocket
    library) is passed as ``name[extras]`` with NO specifier: uv keeps the
    existing specifier and adds the extra, so the user's bounds survive.

    The extra has to sit on an unmarked declaration to count: a marked one
    (``uvicorn[standard]>=0.30; python_version>'3.9'``) installs it only where
    the marker holds, and a marked pair must not add up to "satisfied". When it
    is missing and any declaration of the package carries a marker, ``uv add``
    would append a second, unconditional line (#3312), so the requirement goes
    to ``manual`` instead.
    """
    declared = _declared_dependencies(pyproject) if pyproject.exists() else None
    if declared is None:
        return requirements(), []
    needed, manual = [], []
    for req in requirements():
        name = _REQUIREMENT_NAME_RE.match(req).group(1)
        entries = declared.get(_canonical_name(name))
        if entries is None:
            needed.append(req)
            continue
        wanted = _extras(req)
        if not wanted:
            continue  # declared, and no extra to check: a marker on it is the user's business
        if any(wanted <= extras for extras, marked in entries if not marked):
            continue
        if any(marked for _, marked in entries):
            manual.append("%s[%s]" % (name, ",".join(sorted(wanted))))
            continue
        have = set().union(*(extras for extras, _ in entries))
        needed.append("%s[%s]" % (name, ",".join(sorted(wanted | have))))
    return needed, manual


def _undeclared_requirements(pyproject: Path) -> List[str]:
    """The requirements ``uv add`` must be given (see ``_plan_uv_requirements``)."""
    return _plan_uv_requirements(pyproject)[0]


def choose_package_action(root: Path, python: Optional[Path], uv_available: bool) -> PackageAction:
    reqs = requirements()
    pyproject = root / "pyproject.toml"
    if (root / "uv.lock").exists() or (pyproject.exists() and "[tool.uv]" in pyproject.read_text()):
        missing, manual = _plan_uv_requirements(pyproject)
        # An empty command means there is nothing for uv to add.
        return PackageAction(
            "uv", ["uv", "add", *missing] if missing else [], runnable=uv_available, manual=manual
        )
    if (root / "poetry.lock").exists():
        return PackageAction("poetry", ["poetry", "add", *reqs], runnable=False)
    if (root / "requirements.txt").exists():
        if python is None:
            command = ["pip", "install", "-r", "requirements.txt"]
        elif uv_available:
            command = ["uv", "pip", "install", "--python", str(python), "-r", "requirements.txt"]
        else:
            command = [str(python), "-m", "pip", "install", "-r", "requirements.txt"]
        return PackageAction("requirements", command, runnable=python is not None)
    return PackageAction("none", ["pip", "install", *reqs], runnable=False)


def _canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_entries(path: Path, seen: Optional[set] = None) -> List[Tuple[str, Path]]:
    """Non-comment lines of a requirements file and the files it includes, each
    with the file it was read from."""
    seen = set() if seen is None else seen
    key = os.path.realpath(path)  # `a/../b.txt` and `b.txt` are one file
    if key in seen or not path.is_file():
        return []
    seen.add(key)
    entries: List[Tuple[str, Path]] = []
    for line in _read(path).splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        include = _INCLUDE_RE.match(line)
        if include:
            entries += _requirement_entries(path.parent / include.group(1), seen)
        else:
            entries.append((line, path))
    return entries


def _mentions(line: str, name: str) -> bool:
    """Whether a requirement line installs ``name``: a plain specifier, an
    editable path, a VCS or archive URL, or a ``name @ url`` reference."""
    wanted = _canonical_name(name)
    tokens = re.split(r"[^A-Za-z0-9._-]+", line)
    for token in tokens:
        # Archive and wheel names carry a version: djust-1.0.tar.gz
        base = re.split(r"-\d", token, maxsplit=1)[0]
        if _canonical_name(token) == wanted or _canonical_name(base) == wanted:
            return True
    return False


def _add_extras_to_line(line: str, name: str, extras: set) -> Optional[str]:
    """``line`` with ``extras`` added after the package name, or None when the
    line is not a plain ``name[extras] spec`` requirement for ``name``."""
    found = _REQUIREMENT_NAME_RE.match(line)
    if not found or _canonical_name(found.group(1)) != _canonical_name(name):
        return None
    if line[found.end() :].lstrip().startswith("@"):
        return None  # a direct URL reference: leave it alone
    have = _extras(line)
    if extras <= have:
        return line
    rest = line[found.end() :]
    existing = _REQUIREMENT_EXTRAS_RE.match(line)
    if existing:
        rest = line[existing.end() :]
    return "%s[%s]%s" % (line[: found.end()], ",".join(sorted(extras | have)), rest)


_HASH_OPTION_RE = re.compile(r"(?:^|\s)--(?:hash[=\s]|require-hashes(?:\s|$))")


def _uses_hashes(path: Path, seen: Optional[set] = None) -> bool:
    """Whether ``path`` or a file it includes carries a ``--hash=`` option.

    pip and uv switch to ``--require-hashes`` as soon as ONE requirement has a
    hash, so every requirement in the tree then needs one.
    """
    seen = set() if seen is None else seen
    key = os.path.realpath(path)
    if key in seen or not path.is_file():
        return False
    seen.add(key)
    for line in _read(path).splitlines():
        line = line.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if _HASH_OPTION_RE.search(line):
            return True
        include = _INCLUDE_RE.match(line)
        if include and _uses_hashes(path.parent / include.group(1), seen):
            return True
    return False


@dataclass
class _RequirementEdits:
    missing: List[str] = field(default_factory=list)  # requirements to append
    rewrites: dict = field(default_factory=dict)  # name -> extras to add on an existing line
    # Requirements djust init cannot fix in the file it edits: the extra exists
    # only on marked lines (a marker limits where the extra applies, so adding
    # it there, or counting it as present, would both be guesses).
    by_hand: List[str] = field(default_factory=list)
    # name -> included files that hold the only line lacking the extra; those
    # files are the user's, so they are reported, never edited.
    included: dict = field(default_factory=dict)


def _requirement_edits(path: Path) -> _RequirementEdits:
    """What ``path`` needs: requirements to append, extras still needed on a line
    that already names the package, and what has to be done by hand."""
    entries = _requirement_entries(path)
    edits = _RequirementEdits()
    for req in requirements():
        name = _REQUIREMENT_NAME_RE.match(req).group(1)
        mentioning = [(line, origin) for line, origin in entries if _mentions(line, name)]
        if not mentioning:
            edits.missing.append(req)
            continue
        wanted = _extras(req)
        if not wanted:
            continue
        # Only an unmarked line counts: ``uvicorn[standard]; python_version>'3.9'``
        # next to ``uvicorn; python_version<='3.9'`` is not "satisfied" (#3312).
        unmarked = [(line, origin) for line, origin in mentioning if not _has_marker(line)]
        if any(
            wanted <= _extras(line) or _add_extras_to_line(line, name, set()) is None
            for line, _ in unmarked
        ):
            # Has the extra, or is a form with no specifier to extend (an
            # editable path, a URL reference): leave it alone.
            continue
        if not unmarked:
            edits.by_hand.append(req)
        elif any(os.path.realpath(origin) == os.path.realpath(path) for _, origin in unmarked):
            edits.rewrites[name] = wanted
        else:
            edits.included[name] = sorted({str(origin) for _, origin in unmarked})
    return edits


def hash_locked_requirements(root: Path) -> Optional[List[str]]:
    """What a hash-locked ``requirements.txt`` needs, or None.

    A hash-locked file is never edited (a changed line no longer matches its
    ``--hash``, an appended one has none, and ``--require-hashes`` refuses the
    install either way), so ``djust init`` reports these instead. None when the
    file is not hash-locked or already has everything.
    """
    path = root / "requirements.txt"
    if not _uses_hashes(path):
        return None
    edits = _requirement_edits(path)
    touched = {*edits.rewrites, *edits.included, *(_requirement_name(r) for r in edits.by_hand)}
    needed = [r for r in requirements() if r in edits.missing or _requirement_name(r) in touched]
    return needed or None


def requirements_by_hand(root: Path) -> List[str]:
    """Requirements ``plan_requirements`` cannot fix, as the lines to add by hand.

    One entry per requirement whose extra is missing on every unmarked line and
    which is either only declared on marked lines, or declared in an included
    file (which djust init does not edit). Empty for a hash-locked file, which
    ``hash_locked_requirements`` reports instead.
    """
    path = root / "requirements.txt"
    if _uses_hashes(path):
        return []
    edits = _requirement_edits(path)
    lines = []
    for req in requirements():
        name = _requirement_name(req)
        if req in edits.by_hand:
            lines.append(
                "%s: its extra is only on lines with an environment marker. "
                "Add `%s` on an unmarked line." % (name, _extras_requirement(req))
            )
        elif name in edits.included:
            lines.append(
                "%s: its `[%s]` extra is missing in %s, which djust init does not edit. "
                "Add `%s` there."
                % (
                    name,
                    ",".join(sorted(_extras(req))),
                    ", ".join(edits.included[name]),
                    _extras_requirement(req),
                )
            )
    return lines


def _extras_requirement(req: str) -> str:
    """``name[extras]`` for a scaffold requirement, with no specifier."""
    return "%s[%s]" % (_requirement_name(req), ",".join(sorted(_extras(req))))


def _requirement_name(req: str) -> str:
    return _REQUIREMENT_NAME_RE.match(req).group(1)


def plan_requirements(root: Path) -> Optional[FileChange]:
    path = root / "requirements.txt"
    if _uses_hashes(path):
        return None  # see hash_locked_requirements: reported, never edited
    old = _read(path)
    edits = _requirement_edits(path)
    missing, rewrites = edits.missing, dict(edits.rewrites)
    if not missing and not rewrites:
        return None
    newline = "\r\n" if "\r\n" in old else "\n"
    out = []
    for raw in old.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        tail = raw[len(body) :]
        for name, extras in rewrites.items():
            if _has_marker(body):
                break  # a marked line is never the one that gets the extra
            changed = _add_extras_to_line(body, name, extras)
            if changed is not None and changed != body:
                body = changed
                rewrites = {k: v for k, v in rewrites.items() if k != name}
                break
        out.append(body + tail)
    new = "".join(out)
    if missing:
        new = (new if not new or new.endswith("\n") else new + newline) + "".join(
            req + newline for req in missing
        )
    if new == old:
        return None
    return FileChange(path, old, new)


def dirty_files(root: Path, paths: List[Path]) -> List[str]:
    """Target files with uncommitted changes; empty outside a git work tree."""
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    existing = [str(p) for p in paths if p.exists()]
    if inside.returncode != 0 or inside.stdout.strip() != "true" or not existing:
        return []
    status = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--", *existing],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    # -z prints raw paths (no quoting); a rename adds its source as an extra entry.
    entries = status.stdout.split("\0")
    dirty = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) > 3:
            dirty.append(entry[3:])
            if entry[0] in "RC":
                index += 1
    return dirty


def apply_changes(changes: List[FileChange]) -> None:
    for change in changes:
        _write(change.path, change.new)


def _run(cmd: List[str], root: Path) -> subprocess.CompletedProcess:
    # Drop an inherited VIRTUAL_ENV so no tool can fall back to an environment
    # from outside this project.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        return subprocess.run(
            cmd, cwd=str(root), env=env, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", "%s: command not found" % cmd[0])


@dataclass
class InitResult:
    steps: List[Step] = field(default_factory=list)
    changes: List[FileChange] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    run_command: str = ""
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        if self.dry_run:
            return 0
        return 2 if any(step.status == ATTENTION for step in self.steps) else 0


def init_project(
    root: Path,
    settings_module: Optional[str] = None,
    dry_run: bool = False,
    install: bool = True,
    force: bool = False,
    templates: bool = False,
) -> InitResult:
    project = detect_project(root, settings_module)
    python = find_project_python(root, os.environ)
    action = choose_package_action(root, python, uv_available=shutil.which("uv") is not None)
    result = InitResult(dry_run=dry_run)

    settings_change, step = plan_settings(project)
    result.steps.append(step)
    # Plan TEMPLATES against the file as it will be after the block above, and
    # fold both blocks into the one FileChange for settings.py.
    settings_text = settings_change.new if settings_change else _read(project.settings_path)
    templates_block, step, templates_note = plan_templates(project, settings_text, opt_in=templates)
    result.steps.append(step)
    if templates_note:
        result.notes.append(templates_note)
    if templates_block:
        newline = "\r\n" if "\r\n" in settings_text else "\n"
        base = settings_change.old if settings_change else settings_text
        joined = settings_text.rstrip("\r\n") + newline * 3
        settings_change = FileChange(
            project.settings_path, base, joined + templates_block.replace("\n", newline)
        )
    asgi_change, step, snippet = plan_asgi(project)
    result.steps.append(step)
    if snippet:
        result.notes.append("%s is customized. Merge in:\n\n%s" % (step.name, snippet))
    requirements_change = None
    if install and action.kind == "requirements":
        needed = hash_locked_requirements(root)
        if needed:
            result.steps.append(
                Step(
                    "requirements.txt",
                    ATTENTION,
                    "hash-locked (--hash): not edited, add %d requirement(s) yourself"
                    % len(needed),
                )
            )
            result.notes.append(
                "requirements.txt pins hashes, so djust init left it alone (a changed or "
                "appended line breaks --require-hashes). Add these to your source "
                "requirements and recompile with hashes (e.g. pip-compile --generate-hashes "
                "or uv pip compile --generate-hashes):\n\n%s" % "\n".join(needed)
            )
        else:
            requirements_change = plan_requirements(root)
            by_hand = requirements_by_hand(root)
            if by_hand:
                result.steps.append(
                    Step(
                        "requirements.txt",
                        ATTENTION,
                        "add %d extra(s) yourself: not safe to edit" % len(by_hand),
                    )
                )
                result.notes.append(
                    "djust init did not touch these requirements (an extra that only a "
                    "marked line or an included file carries is not edited or counted):\n\n%s"
                    % "\n".join("- %s" % line for line in by_hand)
                )
    if install and action.manual:
        result.steps.append(
            Step(
                "pyproject.toml",
                ATTENTION,
                "add %d extra(s) yourself: declared with an environment marker"
                % len(action.manual),
            )
        )
        result.notes.append(
            "These packages are declared in pyproject.toml with an environment marker, and "
            "`uv add` would append a second, unconditional line instead of keeping your "
            "specifier. Add the extra by hand, on an unmarked dependency line:\n\n%s"
            % "\n".join("- %s" % req for req in action.manual)
        )
    result.changes = [c for c in (settings_change, asgi_change, requirements_change) if c]

    if action.kind == "uv":
        result.run_command = "uv run uvicorn %s:application --reload" % project.asgi_module
    else:
        runner = str(python.relative_to(root)) if python else "python"
        result.run_command = "%s -m uvicorn %s:application --reload" % (runner, project.asgi_module)

    command = shlex.join(action.command)
    nothing_to_add = action.kind == "uv" and not action.command
    # Not "already declared" when an extra is still for the user to add by hand.
    declared_detail = _NOTHING_FOR_UV if action.manual else _ALREADY_DECLARED
    if dry_run:
        result.steps = [
            Step(step.name, PLANNED, step.planned or step.detail) if step.status == DONE else step
            for step in result.steps
        ]
        if not install:
            result.steps.append(Step("packages", SKIPPED, "--no-install"))
        elif nothing_to_add:
            result.steps.append(Step("packages", UNCHANGED, declared_detail))
        else:
            result.steps.append(Step("packages", SKIPPED, "would run: %s" % command))
        return result

    if result.changes and not force:
        dirty = dirty_files(root, [c.path for c in result.changes])
        if dirty:
            raise InitError(
                "Uncommitted changes in %s. Commit them first so the edit is easy to "
                "review, or rerun with --force." % ", ".join(dirty)
            )
    apply_changes(result.changes)

    if not install:
        result.steps.append(Step("packages", SKIPPED, "--no-install"))
        result.steps.append(Step("check", SKIPPED, "install packages, then run manage.py check"))
        return result
    if not action.runnable:
        if nothing_to_add:
            result.steps.append(Step("packages", UNCHANGED, declared_detail))
        else:
            result.steps.append(Step("packages", SKIPPED, "run: %s" % command))
        result.steps.append(Step("check", SKIPPED, "run manage.py check after installing"))
        return result

    if nothing_to_add:
        result.steps.append(Step("packages", UNCHANGED, declared_detail))
    else:
        installed = _run(action.command, root)
        if installed.returncode != 0:
            result.steps.append(Step("packages", ATTENTION, "failed: %s" % command))
            result.notes.append("%s\n%s" % (command, (installed.stdout + installed.stderr).strip()))
            result.steps.append(Step("check", SKIPPED, "packages did not install"))
            return result
        result.steps.append(Step("packages", DONE, command))

    if action.kind == "uv":
        check_cmd = ["uv", "run", "python", "manage.py", "check"]
    else:
        check_cmd = [str(python), "manage.py", "check"]
    checked = _run(check_cmd, root)
    output = (checked.stdout + checked.stderr).strip()
    if checked.returncode != 0:
        result.steps.append(Step("check", ATTENTION, "manage.py check failed"))
        result.notes.append("manage.py check\n%s" % output)
        return result
    # Warnings don't fail the check (exit 0), but they are still findings.
    found = _CHECK_ISSUES_RE.search(output)
    count = int(found.group(1)) if found else 0
    if count:
        noun = "issue" if count == 1 else "issues"
        result.steps.append(Step("check", DONE, "%d %s reported (see below)" % (count, noun)))
        result.notes.append("manage.py check\n%s" % output)
    else:
        result.steps.append(Step("check", DONE, "no issues"))
    return result


# Django's summary line, e.g. "System check identified 2 issues (0 silenced)."
# Anchored to a line start: the summary is its own line, and a check message
# quoting that sentence must not be counted instead.
_CHECK_ISSUES_RE = re.compile(r"^System check identified (\d+) issues? \(", re.M)


_STATUS_LABELS = {
    DONE: "done",
    UNCHANGED: "unchanged",
    SKIPPED: "skipped",
    ATTENTION: "ATTENTION",
    PLANNED: "would change",
}


def format_result(result: InitResult, root: Path) -> str:
    lines = []
    if result.dry_run:
        lines += [change.diff(root) for change in result.changes]
        lines.append("Dry run: nothing was written or installed.\n")
    width = max(len(step.name) for step in result.steps)
    status_width = max(len(_STATUS_LABELS[step.status]) for step in result.steps)
    for step in result.steps:
        lines.append(
            "  %s  %s  %s"
            % (
                step.name.ljust(width),
                _STATUS_LABELS[step.status].ljust(status_width),
                step.detail,
            )
        )
    for note in result.notes:
        lines.append("\n%s" % note)
    lines.append("\nRun:  %s" % result.run_command)
    lines.append("Next: %s" % FIRST_LIVEVIEW_URL)
    return "\n".join(lines)
