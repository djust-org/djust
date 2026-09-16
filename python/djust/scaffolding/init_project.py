"""Add djust to an existing Django project (``djust init``).

Planning is kept separate from side effects: the ``plan_*`` functions compute
new file contents without touching disk, so ``--dry-run`` shows exactly what
``apply_changes`` would write.
"""

import ast
import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

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
