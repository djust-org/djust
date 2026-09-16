"""Add djust to an existing Django project (``djust init``).

Planning is kept separate from side effects: the ``plan_*`` functions compute
new file contents without touching disk, so ``--dry-run`` shows exactly what
``apply_changes`` would write.
"""

import ast
import difflib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from . import templates as T

SETTINGS_MARKER = "# --- djust (added by djust init) ---"

DONE = "done"
UNCHANGED = "unchanged"
SKIPPED = "skipped"
ATTENTION = "attention"

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
    return Project(root, settings_module, settings_path, settings_path.with_name("asgi.py"))


def plan_settings(project: Project) -> Tuple[Optional[FileChange], Step]:
    name = str(project.settings_path.relative_to(project.root))
    old = project.settings_path.read_text()
    if SETTINGS_MARKER in old:
        return None, Step(name, UNCHANGED, "djust block already present")
    new = old.rstrip("\n") + "\n\n\n" + render_settings_block(project.asgi_module)
    return FileChange(project.settings_path, old, new), Step(name, DONE, "djust block appended")


def render_asgi(project: Project) -> str:
    return T.ASGI_PY % {
        "project_name": project.package or project.settings_module,
        "settings_module": project.settings_module,
    }


def _is_stock_asgi(source: str) -> bool:
    """True when the file is Django's ``startproject`` asgi.py, in any formatting."""
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return False
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # module docstring
    if len(body) != len(_STOCK_ASGI):
        return False
    for got, want in zip(body, _STOCK_ASGI):
        if isinstance(want, ast.Expr):
            # The settings module string differs per project; compare the call shape.
            call = got.value if isinstance(got, ast.Expr) else None
            if not isinstance(call, ast.Call) or ast.dump(call.func) != ast.dump(want.value.func):
                return False
            args = call.args
            if len(args) != 2 or call.keywords:
                return False
            if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
                return False
            if args[0].value != "DJANGO_SETTINGS_MODULE":
                return False
        elif ast.dump(got) != ast.dump(want):
            return False
    return True


def plan_asgi(project: Project) -> Tuple[Optional[FileChange], Step, Optional[str]]:
    name = str(project.asgi_path.relative_to(project.root))
    new = render_asgi(project)
    if not project.asgi_path.exists():
        return FileChange(project.asgi_path, None, new), Step(name, DONE, "created"), None
    old = project.asgi_path.read_text()
    if "LiveViewConsumer" in old:
        return None, Step(name, UNCHANGED, "already routes LiveView WebSockets"), None
    if _is_stock_asgi(old):
        change = FileChange(project.asgi_path, old, new)
        return change, Step(name, DONE, "replaced Django's default"), None
    return None, Step(name, ATTENTION, "customized; merge the djust ASGI app by hand"), new


_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
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


def choose_package_action(root: Path, python: Optional[Path], uv_available: bool) -> PackageAction:
    reqs = requirements()
    pyproject = root / "pyproject.toml"
    if (root / "uv.lock").exists() or (pyproject.exists() and "[tool.uv]" in pyproject.read_text()):
        return PackageAction("uv", ["uv", "add", *reqs], runnable=uv_available)
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


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def plan_requirements(root: Path) -> Optional[FileChange]:
    path = root / "requirements.txt"
    old = path.read_text()
    present = set()
    for line in old.splitlines():
        match = _REQUIREMENT_NAME_RE.match(line)
        if match:
            present.add(_normalize(match.group(1)))
    missing = [
        req
        for req in requirements()
        if _normalize(_REQUIREMENT_NAME_RE.match(req).group(1)) not in present
    ]
    if not missing:
        return None
    prefix = old if not old or old.endswith("\n") else old + "\n"
    return FileChange(path, old, prefix + "".join("%s\n" % req for req in missing))


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
        ["git", "status", "--porcelain", "--", *existing],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    return [line[3:] for line in status.stdout.splitlines() if line.strip()]


def apply_changes(changes: List[FileChange]) -> None:
    for change in changes:
        change.path.write_text(change.new)


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
        return 2 if any(step.status == ATTENTION for step in self.steps) else 0


def init_project(
    root: Path,
    settings_module: Optional[str] = None,
    dry_run: bool = False,
    install: bool = True,
    force: bool = False,
) -> InitResult:
    project = detect_project(root, settings_module)
    python = find_project_python(root, os.environ)
    action = choose_package_action(root, python, uv_available=shutil.which("uv") is not None)
    result = InitResult(dry_run=dry_run)

    settings_change, step = plan_settings(project)
    result.steps.append(step)
    asgi_change, step, snippet = plan_asgi(project)
    result.steps.append(step)
    if snippet:
        result.notes.append("%s is customized. Merge in:\n\n%s" % (step.name, snippet))
    requirements_change = (
        plan_requirements(root) if install and action.kind == "requirements" else None
    )
    result.changes = [c for c in (settings_change, asgi_change, requirements_change) if c]

    if action.kind == "uv":
        result.run_command = "uv run uvicorn %s:application --reload" % project.asgi_module
    else:
        runner = str(python.relative_to(root)) if python else "python"
        result.run_command = "%s -m uvicorn %s:application --reload" % (runner, project.asgi_module)

    command = " ".join(action.command)
    if dry_run:
        result.steps.append(
            Step("packages", SKIPPED, "would run: %s" % command if install else "--no-install")
        )
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
        result.steps.append(Step("packages", SKIPPED, "run: %s" % command))
        result.steps.append(Step("check", SKIPPED, "run manage.py check after installing"))
        return result

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
    if checked.returncode != 0:
        result.steps.append(Step("check", ATTENTION, "manage.py check failed"))
        result.notes.append("manage.py check\n%s" % (checked.stdout + checked.stderr).strip())
    else:
        result.steps.append(Step("check", DONE, "no issues"))
    return result


_STATUS_LABELS = {DONE: "done", UNCHANGED: "unchanged", SKIPPED: "skipped", ATTENTION: "ATTENTION"}


def format_result(result: InitResult, root: Path) -> str:
    lines = []
    if result.dry_run:
        lines += [change.diff(root) for change in result.changes]
        lines.append("Dry run: nothing was written or installed.\n")
    width = max(len(step.name) for step in result.steps)
    for step in result.steps:
        lines.append(
            "  %s  %-9s  %s" % (step.name.ljust(width), _STATUS_LABELS[step.status], step.detail)
        )
    for note in result.notes:
        lines.append("\n%s" % note)
    lines.append("\nRun:  %s" % result.run_command)
    lines.append("Next: %s" % FIRST_LIVEVIEW_URL)
    return "\n".join(lines)
