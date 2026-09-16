# Simpler Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `uvx djust@latest new myproject && cd myproject && make dev` and `uvx djust@latest init` the documented install paths.

**Architecture:** `djust new` keeps its generator; its Makefile, requirements floor, setup steps and success output change. A new `djust.scaffolding.init_project` module separates pure planning (detect project, compute file edits, choose package action) from side effects (write, install, check), and `cli.py` gains a thin `init` subcommand. Both commands share the ASGI template.

**Tech Stack:** Python 3.10+ stdlib (`ast`, `difflib`, `subprocess`, `dataclasses`), pytest, Django `startproject` for end-to-end tests.

**Spec:** `docs/superpowers/specs/2026-09-16-simpler-install-design.md`

## Global Constraints

- Worktree: `/Users/tip/Dropbox/online_projects/ai/djust_project/djust-wt-init`, branch `feat/djust-init`.
- Run Python from the main checkout's venv with the worktree pinned first:
  `PYTHONPATH=$PWD/python:$PWD /Users/tip/Dropbox/online_projects/ai/djust_project/djust/.venv/bin/python -m pytest …`
  (the editable install points at the main checkout; without `PYTHONPATH` tests exercise the wrong source).
- Logging uses `%s` formatting, never f-strings. No `mark_safe`.
- Requirements: `djust>=<djust.__version__ without local segment>`, `channels>=4.0`, `uvicorn[standard]>=0.30`.
- Marker line: `# --- djust (added by djust init) ---`; end line `# --- end djust ---`.
- Environment creation: `uv venv --python ">=3.10" <dir>`.
- Commits use conventional prefixes and end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

---

### Task 1: `djust new` — Makefile, requirements floor, check step, next steps

**Files:**
- Modify: `python/djust/scaffolding/templates.py` (ASGI_PY, MAKEFILE, REQUIREMENTS_TXT)
- Modify: `python/djust/scaffolding/generator.py` (`_build_context`, `_create_project_files`, `_run_auto_setup`; add `djust_requirement`, `next_steps`)
- Modify: `python/djust/cli.py` (`cmd_new` success output)
- Modify: `python/djust/management/commands/djust_new.py` (success output)
- Test: `python/djust/tests/test_scaffold_setup_environment.py`

**Interfaces:**
- Produces: `generator.djust_requirement() -> str`; `generator.next_steps(app_name: str, setup_ran: bool) -> List[str]`; `templates.ASGI_PY` rendered with keys `project_name`, `settings_module`.

- [ ] **Step 1: Write failing tests** (append to `test_scaffold_setup_environment.py`)

```python
def test_generated_project_runs_without_activation(tmp_path):
    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    makefile = (project / "Makefile").read_text()
    assert "PYTHON ?= .venv/bin/python" in makefile
    assert "\t$(PYTHON) -m uvicorn child.asgi:application" in makefile
    assert "\tuvicorn " not in makefile
    assert "\tpython manage.py" not in makefile


def test_requirements_floor_is_generating_version(tmp_path):
    from djust import __version__

    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    requirements = (project / "requirements.txt").read_text().splitlines()
    assert "djust>=%s" % __version__.split("+")[0] in requirements


def test_generated_asgi_uses_project_settings(tmp_path):
    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    asgi = (project / "child" / "asgi.py").read_text()
    assert '"DJANGO_SETTINGS_MODULE", "child.settings"' in asgi


def test_setup_pins_python_and_runs_check_last(tmp_path):
    commands = []
    with (
        patch.object(generator.shutil, "which", return_value="uv"),
        patch.object(generator, "_run_cmd", side_effect=lambda cmd, cwd: commands.append(cmd)),
    ):
        generator._run_auto_setup(tmp_path)
    assert commands[0][:4] == ["uv", "venv", "--python", ">=3.10"]
    assert commands[-1][1:] == ["manage.py", "check"]


def test_next_steps_after_setup(monkeypatch):
    monkeypatch.setattr(generator.os, "name", "posix")
    assert generator.next_steps("child", setup_ran=True) == ["cd child", "make dev"]


def test_next_steps_without_setup_target_project_environment(monkeypatch):
    monkeypatch.setattr(generator.os, "name", "posix")
    steps = generator.next_steps("child", setup_ran=False)
    assert steps[0] == "cd child"
    assert "uv pip install --python .venv -r requirements.txt" in steps
    assert ".venv/bin/python manage.py migrate" in steps
    assert steps[-1] == "make dev"
```

Also change `@pytest.mark.parametrize("failed_step", range(4))` to `range(5)` (setup now has five commands).

- [ ] **Step 2: Run tests, expect failures**

Run: `PYTHONPATH=$PWD/python:$PWD $MAIN/.venv/bin/python -m pytest python/djust/tests/test_scaffold_setup_environment.py -q -p no:cacheprovider`
Expected: new tests FAIL (Makefile content, missing `next_steps`, missing `--python`, `range(5)` case).

- [ ] **Step 3: Implement**

`templates.py` — in `ASGI_PY` replace the docstring's `%(app_name)s` with `%(project_name)s` and the setdefault with `"%(settings_module)s"`. Replace `MAKEFILE` and `REQUIREMENTS_TXT`:

```python
MAKEFILE = """\
.PHONY: dev test migrate check install collectstatic

# The project's own environment; no activation needed. Override with
# `make dev PYTHON=python` to use whichever interpreter is on PATH.
PYTHON ?= .venv/bin/python

dev:
\t$(PYTHON) -m uvicorn %(app_name)s.asgi:application --host 127.0.0.1 --port 8000 --reload

test:
\t$(PYTHON) manage.py test

migrate:
\t$(PYTHON) manage.py migrate

check:
\t$(PYTHON) manage.py djust_check

install:
\tuv pip install --python .venv -r requirements.txt

collectstatic:
\t$(PYTHON) manage.py collectstatic --noinput
"""

REQUIREMENTS_TXT = """\
django>=5.1
%(djust_requirement)s
channels>=4.0
uvicorn[standard]>=0.30
"""
```

`generator.py` — add near the top (after `ScaffoldSetupError`):

```python
def djust_requirement() -> str:
    """Requirement line pinning djust to at least the running version."""
    from djust import __version__

    return "djust>=%s" % __version__.split("+", 1)[0]


def next_steps(app_name: str, setup_ran: bool) -> List[str]:
    """Commands a user runs after ``djust new`` to start the dev server."""
    windows = os.name == "nt"
    python = r".venv\Scripts\python" if windows else ".venv/bin/python"
    steps = ["cd %s" % app_name]
    if not setup_ran:
        steps += [
            'uv venv --python ">=3.10" .venv',
            "uv pip install --python .venv -r requirements.txt",
            "%s manage.py makemigrations" % python,
            "%s manage.py migrate" % python,
        ]
    if windows:
        steps.append("%s -m uvicorn %s.asgi:application --reload" % (python, app_name))
    else:
        steps.append("make dev")
    return steps
```

In `_build_context`'s returned dict add:

```python
        "project_name": app_name,
        "settings_module": "%s.settings" % app_name,
        "djust_requirement": djust_requirement(),
```

In `_create_project_files`: `_write(project_dir / "requirements.txt", T.REQUIREMENTS_TXT % ctx)`.

In `_run_auto_setup`: uv branch becomes `_run_cmd(["uv", "venv", "--python", ">=3.10", str(venv_dir)], cwd=project_dir)`; after the migrate call add:

```python
    print("Checking project...")
    _run_cmd([venv_python, "manage.py", "check"], cwd=project_dir)
```

`cli.py` `cmd_new` — replace the "Next steps" prints:

```python
    from djust.scaffolding.generator import next_steps

    print("\nNext steps:")
    for step in next_steps(args.name, setup_ran=not getattr(args, "no_setup", False)):
        print("  %s" % step)
    print("\nThen open http://127.0.0.1:8000/\n")
```

`djust_new.py` — same loop via `self.stdout.write("    %s" % step)` with `setup_ran=not options["no_setup"]`, then `self.stdout.write("  Then open http://127.0.0.1:8000/")`.

- [ ] **Step 4: Run scaffold tests, expect pass**

Run: `… -m pytest python/djust/tests/test_scaffold_setup_environment.py python/djust/tests/test_scaffold_migrations_1637.py python/djust/tests/test_scaffold_db_path.py python/djust/tests/test_native_renderer_scaffold.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit** — `feat: run scaffolded projects without activating the environment`

---

### Task 2: `djust init` planning — detection, settings block, ASGI

**Files:**
- Create: `python/djust/scaffolding/init_project.py`
- Modify: `python/djust/scaffolding/templates.py` (add `SETTINGS_BLOCK`)
- Test: `python/djust/tests/test_djust_init.py`

**Interfaces:**
- Consumes: `templates.ASGI_PY` with `project_name`, `settings_module`.
- Produces:
  - `class InitError(RuntimeError)`
  - `@dataclass Project(root: Path, settings_module: str, settings_path: Path, asgi_path: Path)` with properties `package: str`, `asgi_module: str`
  - `@dataclass FileChange(path: Path, old: Optional[str], new: str)` with `diff(root: Path) -> str`
  - `@dataclass Step(name: str, status: str, detail: str)`; status constants `DONE`, `UNCHANGED`, `SKIPPED`, `ATTENTION`
  - `detect_project(root: Path, settings_module: Optional[str] = None) -> Project`
  - `render_settings_block(asgi_module: str) -> str`
  - `plan_settings(project) -> Tuple[Optional[FileChange], Step]`
  - `plan_asgi(project) -> Tuple[Optional[FileChange], Step, Optional[str]]` (third item: snippet to show when customized)

- [ ] **Step 1: Write failing tests** (`python/djust/tests/test_djust_init.py`)

```python
"""`djust init` adds djust to an existing Django project."""

import subprocess
import sys
from pathlib import Path

import pytest

from djust.scaffolding import init_project as init

STOCK_ASGI = '''"""
ASGI config for mysite project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

application = get_asgi_application()
'''


def make_project(root: Path, name: str = "mysite", asgi: str = STOCK_ASGI) -> Path:
    (root / name).mkdir(parents=True)
    (root / "manage.py").write_text(
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', '%s.settings')\n" % name
    )
    (root / name / "__init__.py").write_text("")
    (root / name / "settings.py").write_text(
        'INSTALLED_APPS = ["django.contrib.auth"]\nTEMPLATES = []\n'
    )
    (root / name / "asgi.py").write_text(asgi)
    return root


def test_detects_settings_from_manage_py(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    assert project.settings_module == "mysite.settings"
    assert project.settings_path == tmp_path / "mysite" / "settings.py"
    assert project.asgi_path == tmp_path / "mysite" / "asgi.py"
    assert project.asgi_module == "mysite.asgi"


def test_settings_flag_overrides_manage_py(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "local.py").write_text("")
    project = init.detect_project(tmp_path, settings_module="mysite.local")
    assert project.settings_path.name == "local.py"


def test_missing_manage_py_is_refused(tmp_path):
    with pytest.raises(init.InitError, match="manage.py"):
        init.detect_project(tmp_path)


def test_settings_package_is_refused_with_snippet(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "settings.py").unlink()
    (tmp_path / "mysite" / "settings").mkdir()
    (tmp_path / "mysite" / "settings" / "__init__.py").write_text("")
    with pytest.raises(init.InitError) as exc:
        init.detect_project(tmp_path)
    assert "package" in str(exc.value)
    assert init.SETTINGS_MARKER in str(exc.value)


def test_settings_block_appended_once(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    change, step = init.plan_settings(project)
    assert step.status == init.DONE
    assert change.new.startswith(change.old)
    assert change.new.count(init.SETTINGS_MARKER) == 1
    project.settings_path.write_text(change.new)
    change, step = init.plan_settings(project)
    assert change is None
    assert step.status == init.UNCHANGED


def run_block(**settings):
    namespace = dict(settings)
    exec(init.render_settings_block("mysite.asgi"), namespace)  # noqa: S102 — trusted template
    return namespace


@pytest.mark.parametrize("container", [list, tuple])
def test_settings_block_adds_apps_and_backend(container):
    django_backend = {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["t"]}
    ns = run_block(
        INSTALLED_APPS=container(["django.contrib.auth", "channels"]),
        TEMPLATES=container([django_backend]),
    )
    assert ns["INSTALLED_APPS"] == ["django.contrib.auth", "channels", "djust"]
    assert ns["TEMPLATES"][0]["BACKEND"] == "djust.template_backend.DjustTemplateBackend"
    assert ns["TEMPLATES"][0]["DIRS"] == ["t"]
    assert ns["TEMPLATES"][1] == django_backend
    assert ns["ASGI_APPLICATION"] == "mysite.asgi.application"
    assert ns["CHANNEL_LAYERS"]["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"


def test_settings_block_respects_existing_configuration():
    djust_backend = {"BACKEND": "djust.template_backend.DjustTemplateBackend"}
    redis = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
    ns = run_block(INSTALLED_APPS=["djust"], TEMPLATES=[djust_backend], CHANNEL_LAYERS=redis)
    assert ns["INSTALLED_APPS"] == ["djust", "channels"]
    assert ns["TEMPLATES"] == [djust_backend]
    assert ns["CHANNEL_LAYERS"] is redis


@pytest.mark.parametrize(
    "source",
    [STOCK_ASGI, STOCK_ASGI.replace("'", '"'), STOCK_ASGI.split('"""\n', 2)[2]],
)
def test_stock_asgi_is_replaced(tmp_path, source):
    project = init.detect_project(make_project(tmp_path, asgi=source))
    change, step, snippet = init.plan_asgi(project)
    assert step.status == init.DONE
    assert "LiveViewConsumer" in change.new
    assert '"DJANGO_SETTINGS_MODULE", "mysite.settings"' in change.new
    assert snippet is None


def test_customized_asgi_is_left_alone(tmp_path):
    custom = STOCK_ASGI + "\napplication = Wrapper(application)\n"
    project = init.detect_project(make_project(tmp_path, asgi=custom))
    change, step, snippet = init.plan_asgi(project)
    assert change is None
    assert step.status == init.ATTENTION
    assert "LiveViewConsumer" in snippet


def test_configured_asgi_is_unchanged(tmp_path):
    project = init.detect_project(make_project(tmp_path, asgi="from djust.websocket import LiveViewConsumer\n"))
    change, step, snippet = init.plan_asgi(project)
    assert (change, step.status, snippet) == (None, init.UNCHANGED, None)
```

- [ ] **Step 2: Run, expect ImportError failures**

Run: `… -m pytest python/djust/tests/test_djust_init.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement**

`templates.py` — add after `ASGI_PY`:

```python
# ---------------------------------------------------------------------------
# settings block appended by ``djust init``
# ---------------------------------------------------------------------------
#
# The conditions run inside the user's settings module, so the block is
# correct for list or tuple settings, already-listed apps, and projects that
# configure their own channel layer — without parsing the user's code.

SETTINGS_BLOCK = """\
# --- djust (added by djust init) ---
# Reruns of `djust init` detect this block and leave settings unchanged.
INSTALLED_APPS = [
    *INSTALLED_APPS,
    *[app for app in ("channels", "djust") if app not in INSTALLED_APPS],
]

ASGI_APPLICATION = "%(asgi_module)s.application"

if not any(
    backend.get("BACKEND") == "djust.template_backend.DjustTemplateBackend"
    for backend in TEMPLATES
):
    TEMPLATES = [
        {
            "NAME": "djust",
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "DIRS": list(TEMPLATES[0].get("DIRS", [])) if TEMPLATES else [],
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                ],
            },
        },
        *TEMPLATES,
    ]

# In-memory layer: suitable for one local development process.
if "CHANNEL_LAYERS" not in globals():
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }
# --- end djust ---
"""
```

Note: in `test_settings_block_respects_existing_configuration` `INSTALLED_APPS=["djust"]` → `["djust", "channels"]`.

`init_project.py`:

```python
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
            "No manage.py in %s. Run `djust init` from the directory containing manage.py."
            % root
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
```

- [ ] **Step 4: Run tests, expect pass**
- [ ] **Step 5: Commit** — `feat: plan djust init settings and ASGI edits`

---

### Task 3: `djust init` packages, environment, check, orchestration, CLI

**Files:**
- Modify: `python/djust/scaffolding/init_project.py`
- Modify: `python/djust/cli.py` (add `init` subparser, `cmd_init`, register in `commands`)
- Test: `python/djust/tests/test_djust_init.py`

**Interfaces:**
- Consumes: Task 2 names; `generator.djust_requirement()`.
- Produces:
  - `requirements() -> List[str]`
  - `find_project_python(root: Path, environ: Mapping[str, str]) -> Optional[Path]`
  - `@dataclass PackageAction(kind: str, command: List[str], runnable: bool)`; kinds `"uv"`, `"poetry"`, `"requirements"`, `"none"`
  - `choose_package_action(root: Path, python: Optional[Path], uv_available: bool) -> PackageAction`
  - `plan_requirements(root: Path) -> Optional[FileChange]`
  - `dirty_files(root: Path, paths: List[Path]) -> List[str]`
  - `apply_changes(changes: List[FileChange]) -> None`
  - `@dataclass InitResult(steps, changes, notes, run_command, dry_run)` with `exit_code` property
  - `init_project(root, settings_module=None, dry_run=False, install=True, force=False) -> InitResult`
  - `format_result(result: InitResult, root: Path) -> str`
  - `cli.cmd_init(args) -> int`

- [ ] **Step 1: Write failing tests** (append)

```python
from unittest.mock import patch


def test_requirements_include_running_djust():
    from djust.scaffolding.generator import djust_requirement

    assert init.requirements() == [djust_requirement(), "channels>=4.0", "uvicorn[standard]>=0.30"]


def test_project_python_prefers_local_venv(tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert init.find_project_python(tmp_path, {"VIRTUAL_ENV": "/elsewhere"}) == python


def test_project_python_ignores_environment_outside_project(tmp_path):
    outside = tmp_path / "other-env"
    (outside / "bin").mkdir(parents=True)
    (outside / "bin" / "python").write_text("")
    project = tmp_path / "project"
    project.mkdir()
    assert init.find_project_python(project, {"VIRTUAL_ENV": str(outside)}) is None


def test_project_python_accepts_active_environment_inside_project(tmp_path):
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "python").write_text("")
    assert init.find_project_python(tmp_path, {"VIRTUAL_ENV": str(env)}) == env / "bin" / "python"


@pytest.mark.parametrize(
    "files, kind, runnable",
    [
        ({"uv.lock": ""}, "uv", True),
        ({"pyproject.toml": "[tool.uv]\n"}, "uv", True),
        ({"poetry.lock": ""}, "poetry", False),
        ({"requirements.txt": "django\n"}, "requirements", True),
        ({}, "none", False),
    ],
)
def test_package_action_matches_project_tooling(tmp_path, files, kind, runnable):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    action = init.choose_package_action(tmp_path, tmp_path / ".venv/bin/python", uv_available=True)
    assert (action.kind, action.runnable) == (kind, runnable)


def test_requirements_install_targets_project_python(tmp_path):
    (tmp_path / "requirements.txt").write_text("django\n")
    python = tmp_path / ".venv/bin/python"
    action = init.choose_package_action(tmp_path, python, uv_available=True)
    assert action.command == ["uv", "pip", "install", "--python", str(python), "-r", "requirements.txt"]
    action = init.choose_package_action(tmp_path, python, uv_available=False)
    assert action.command == [str(python), "-m", "pip", "install", "-r", "requirements.txt"]


def test_requirements_without_environment_is_not_runnable(tmp_path):
    (tmp_path / "requirements.txt").write_text("django\n")
    assert init.choose_package_action(tmp_path, None, uv_available=True).runnable is False


def test_requirements_appended_only_when_missing(tmp_path):
    (tmp_path / "requirements.txt").write_text("Django>=5.2\n# comment\nUvicorn[standard]==0.35\n")
    change = init.plan_requirements(tmp_path)
    added = change.new[len(change.old):].splitlines()
    assert [line.split(">=")[0] for line in added] == ["djust", "channels"]
    (tmp_path / "requirements.txt").write_text(change.new)
    assert init.plan_requirements(tmp_path) is None


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_uncommitted_target_files_are_refused(tmp_path):
    make_project(tmp_path)
    git(tmp_path, "init", "-q")
    with pytest.raises(init.InitError, match="--force"):
        init.init_project(tmp_path, install=False)
    assert init.SETTINGS_MARKER not in (tmp_path / "mysite/settings.py").read_text()
    result = init.init_project(tmp_path, install=False, force=True)
    assert result.exit_code == 0
    assert init.SETTINGS_MARKER in (tmp_path / "mysite/settings.py").read_text()


def test_committed_project_is_edited(tmp_path):
    make_project(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    assert init.init_project(tmp_path, install=False).exit_code == 0


def test_dry_run_writes_nothing(tmp_path):
    make_project(tmp_path)
    before = (tmp_path / "mysite/settings.py").read_text()
    result = init.init_project(tmp_path, dry_run=True)
    assert (tmp_path / "mysite/settings.py").read_text() == before
    output = init.format_result(result, tmp_path)
    assert "+++ b/mysite/settings.py" in output
    assert "Dry run" in output


def test_install_and_check_run_with_project_python(tmp_path, monkeypatch):
    make_project(tmp_path)
    (tmp_path / "requirements.txt").write_text("django\n")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch.object(init.shutil, "which", return_value=None), patch.object(init.subprocess, "run", side_effect=run):
        result = init.init_project(tmp_path, force=True)
    commands = [cmd for cmd, _ in calls if cmd[0] != "git"]
    assert commands == [
        [str(python), "-m", "pip", "install", "-r", "requirements.txt"],
        [str(python), "manage.py", "check"],
    ]
    assert all("VIRTUAL_ENV" not in env for cmd, env in calls if cmd[0] != "git")
    assert result.exit_code == 0
    assert result.run_command == ".venv/bin/python -m uvicorn mysite.asgi:application --reload"


def test_failed_check_needs_attention(tmp_path):
    make_project(tmp_path)
    (tmp_path / "uv.lock").write_text("")

    def run(cmd, **kwargs):
        code = 1 if "check" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "", "SystemCheckError: boom")

    with patch.object(init.shutil, "which", return_value="/usr/bin/uv"), patch.object(init.subprocess, "run", side_effect=run):
        result = init.init_project(tmp_path, force=True)
    assert result.exit_code == 2
    assert any("boom" in note for note in result.notes)
    assert result.run_command == "uv run uvicorn mysite.asgi:application --reload"


def test_cli_init_reports_refusal(tmp_path, monkeypatch, capsys):
    import argparse

    from djust import cli

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(settings=None, dry_run=False, no_install=True, force=False)
    assert cli.cmd_init(args) == 1
    assert "manage.py" in capsys.readouterr().out


def test_init_on_startproject_passes_django_check(tmp_path):
    import os

    subprocess.run(
        [sys.executable, "-m", "django", "startproject", "mysite", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    result = init.init_project(tmp_path, install=False)
    assert result.exit_code == 0
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), *sys.path])
    check = subprocess.run(
        [sys.executable, "manage.py", "check"], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stdout + check.stderr
    asgi = subprocess.run(
        [sys.executable, "-c", "import mysite.asgi as a; print(type(a.application).__name__)"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert asgi.stdout.strip() == "ProtocolTypeRouter", asgi.stderr
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Implement** (append to `init_project.py`; add imports `os`, `shutil`, `subprocess`, `dataclass field`, `List`, `Mapping`)

```python
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
        req for req in requirements()
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
            cwd=str(root), capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return []
    existing = [str(p) for p in paths if p.exists()]
    if inside.returncode != 0 or inside.stdout.strip() != "true" or not existing:
        return []
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *existing],
        cwd=str(root), capture_output=True, text=True, check=False,
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
        return subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, text=True, check=False)
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
        result.steps.append(Step("packages", SKIPPED, "would run: %s" % command if install else "--no-install"))
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
```

`cli.py` — add after `cmd_startproject`:

```python
def cmd_init(args: argparse.Namespace) -> int:
    """Add djust to the Django project in the current directory."""
    from djust.scaffolding.init_project import InitError, format_result, init_project

    root = Path.cwd()
    try:
        result = init_project(
            root,
            settings_module=args.settings,
            dry_run=args.dry_run,
            install=not args.no_install,
            force=args.force,
        )
    except InitError as e:
        print("Error: %s" % e)
        return 1
    print(format_result(result, root))
    return result.exit_code
```

(ensure `from pathlib import Path` is imported in `cli.py`). Subparser after `new`:

```python
    init_parser = subparsers.add_parser(
        "init", help="Add djust to the Django project in the current directory"
    )
    init_parser.add_argument("--settings", help="Settings module (default: read from manage.py)")
    init_parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Show the changes without writing"
    )
    init_parser.add_argument(
        "--no-install", action="store_true", dest="no_install", help="Skip installing packages"
    )
    init_parser.add_argument(
        "--force", action="store_true", help="Edit files even if they have uncommitted changes"
    )
```

Register `"init": cmd_init,` in `commands`.

- [ ] **Step 4: Run `test_djust_init.py` and scaffold tests, expect pass**
- [ ] **Step 5: Commit** — `feat: add djust init for existing Django projects`

---

### Task 4: Documentation and changelog

**Files:**
- Modify: `docs/website/getting-started/installation.md`
- Modify: `docs/website/guides/scaffolding.md` (Project and app scaffolding section)
- Create: `changelog.d/djust-init.added.md`, `changelog.d/scaffold-make-dev.changed.md`

- [ ] **Step 1: Rewrite `installation.md`** with sections: intro table (new / existing / by hand); Requirements (unchanged); "Create a new project" (`uvx djust@latest new myproject`, `cd myproject`, `make dev`, open URL, `@latest` note, what gets generated tree, feature flag table, `--no-setup` pointer that the CLI prints the manual commands); "Add djust to an existing project" (`uvx djust@latest init`, `--dry-run`, what it changes: settings block, asgi.py replaced only if stock, packages via uv/requirements/poetry, check; uncommitted-change refusal and `--force`; customized asgi merge); "Configure by hand" (current steps 1–6 kept verbatim); "Troubleshooting" (older scaffold recovery; empty `.venv`/missing session tables; C401 watchdog); "Building from source" (unchanged).
- [ ] **Step 2: Add `djust init` subsection to `guides/scaffolding.md`** listing flags and exit codes.
- [ ] **Step 3: Changelog fragments** (one sentence each).
- [ ] **Step 4: Run the docs checks** — `pre-commit run --files <changed docs>`; fix failures.
- [ ] **Step 5: Commit** — `docs: three-command install and djust init`

---

### Task 5: Full verification

- [ ] **Step 1:** Full Python suite: `PYTHONPATH=$PWD/python:$PWD $MAIN/.venv/bin/python -m pytest tests/ python/tests/ python/djust/tests/ -n auto -q -p no:cacheprovider` — compare failures against the base branch before attributing any to this work.
- [ ] **Step 2:** Manual `djust new` from outside any environment (`env -u VIRTUAL_ENV`), using this worktree's source: `uv run --no-project --with-editable <wt> djust new demo` in a scratch dir, then `make dev` in the background, `curl -sf http://127.0.0.1:8000/`, stop the server.
- [ ] **Step 3:** Manual `djust init`: `django-admin startproject mysite .` in a scratch dir with a `.venv` holding Django, `requirements.txt` listing Django, run `djust init` from the worktree CLI; confirm check passes and the printed run command serves `/` (404 expected) and `/static/djust/client.js` (200).
- [ ] **Step 4:** Render `http://127.0.0.1:8004/getting-started/installation/` if the local docs server reads this tree; otherwise confirm the markdown renders via the repo's docs checks.
