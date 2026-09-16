# Simpler installation: `djust init` and a three-command `djust new`

Date: 2026-09-16
Status: approved design

## Problem

`docs/website/getting-started/installation.md` asks new users to run nine
commands on the recommended path, then carries two recovery sections. Adding
djust to an existing project means hand-editing `settings.py`, replacing a
25-line `asgi.py`, and installing three packages. The length is mostly
compensation for CLI defects:

1. Released generators (PyPI 1.1.3) install into an inherited active
   environment. The fix (`b6d01e94`) is unreleased, so the docs force
   `--no-setup` plus a manual setup sequence.
2. The generated `Makefile` calls bare `uvicorn`/`python`, so `make dev` only
   works after `source .venv/bin/activate`.
3. The generated `requirements.txt` pins `djust>=0.3.0`.
4. Setup has historically printed success after failed steps, which is why
   the recovery sections exist.

## Goals

- New project: `uvx djust@latest new myproject && cd myproject && make dev`.
- Existing project: `uvx djust@latest init` (or `djust init`).
- Manual configuration remains documented, but is no longer the main path.

## Non-goals

- No new public runtime API (no `djust.asgi.get_application()` helper).
- No rewriting of user code by syntax tree (no libcst). Split settings
  packages are refused with the snippet printed, not edited.
- No starter view or app from `djust init`.
- `urls.py` is not touched: LiveViews use ordinary `path()` routes and the
  WebSocket route lives in `asgi.py`.

## Part 1 — `djust init`

### Interface

```
djust init [--dry-run] [--no-install] [--force] [--settings MODULE]
```

Exit codes: `0` everything applied and check passed (or was skipped because
installation was skipped); `1` refused or failed before writing; `2` files were
written but a step needs attention (customized `asgi.py`, failed install,
failed check).

### Project detection

- `manage.py` must exist in the current directory; otherwise exit 1 with
  "run this from the directory containing manage.py".
- The settings module is read from `manage.py` with a regular expression
  matching `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "<module>")`
  (either quote style). `--settings` overrides it. `manage.py` is never
  imported or executed for detection.
- The module maps to `<module path>.py`. If that file does not exist but
  `<module path>/__init__.py` does (settings package), or the file cannot be
  found, exit 1 and print the settings block to paste.
- The ASGI file is `asgi.py` beside the settings file. The ASGI module path is
  the settings module's package plus `.asgi`.

### Safety

- Before writing, if the directory is inside a git work tree, run
  `git status --porcelain --` on every file init will modify. Any output means
  uncommitted changes: exit 1 unless `--force`. Outside git, proceed.
- `--dry-run` prints a unified diff of every planned file change plus the
  package action it would take, then exits 0 without writing or installing.
- Planning (computing new file contents) is a pure function separate from
  applying (writing).

### `settings.py`

If the file already contains `# --- djust (added by djust init) ---`, leave it
unchanged and report "already configured". Otherwise append:

```python
# --- djust (added by djust init) ---
# Reruns of `djust init` detect this block and leave settings unchanged.
INSTALLED_APPS = [
    *INSTALLED_APPS,
    *[app for app in ("channels", "djust") if app not in INSTALLED_APPS],
]

ASGI_APPLICATION = "<package>.asgi.application"

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
```

The conditions run inside settings, so the block is correct whether
`INSTALLED_APPS`/`TEMPLATES` are lists or tuples, whether apps are already
listed, and whether the project configures its own channel layer. Django's
own template backend is kept so the admin keeps working.
`LIVEVIEW_ALLOWED_MODULES` is not set; the installed-apps fallback applies.

### `asgi.py`

Parse the file with `ast`. It is "stock" when, ignoring a module docstring,
its body is exactly: `import os`; `from django.core.asgi import
get_asgi_application`; an `os.environ.setdefault("DJANGO_SETTINGS_MODULE",
<str>)` call; `application = get_asgi_application()`.

- Stock (or missing): write the shared djust ASGI template
  (`templates.ASGI_PY`, parametrized by `settings_module` and `project_name`).
- Already references `LiveViewConsumer`: leave it and report "already configured".
- Anything else: leave it, print the template, report "customized — merge by
  hand", exit status 2 at the end.

### Packages (skipped with `--no-install`)

Requirements: `djust>=<running djust version>`, `channels>=4.0`,
`uvicorn[standard]>=0.30`. The first matching rule wins:

| Project has | Action |
|---|---|
| `uv.lock`, or `[tool.uv]` in `pyproject.toml` | `uv add <requirements>` |
| `poetry.lock` | print `poetry add <requirements>`; do not run it |
| `requirements.txt` | append missing requirement lines, then install `-r requirements.txt` into the project environment |
| none | print `pip install <requirements>` |

A requirement counts as present in `requirements.txt` when a line's normalized
name (before any extra or specifier) matches. Appending `requirements.txt` is a
file change and appears in `--dry-run` and the git safety check.

**Project environment:** `./.venv` if it has a Python interpreter; otherwise
`$VIRTUAL_ENV` only when it resolves inside the project directory; otherwise
none. With no environment, print the install command and skip install and check.
Installing uses `uv pip install --python <interpreter> -r requirements.txt`, or
`<interpreter> -m pip install -r requirements.txt` when `uv` is absent.
Subprocesses run with `VIRTUAL_ENV` removed from their environment so an
inherited environment cannot be targeted. `uv add` targets the uv project's
own environment.

### Check

After a successful install, run `manage.py check` with the project interpreter
(`uv run python manage.py check` for uv projects). A non-zero exit is reported
with its output and yields exit status 2. Files already written stay written.

### Output

One line per step (`settings.py`, `asgi.py`, `packages`, `check`), each marked
done/unchanged/skipped/needs attention, then the run command and the link to
Your First LiveView:

- uv projects: `uv run uvicorn <package>.asgi:application --reload`
- otherwise: `<interpreter> -m uvicorn <package>.asgi:application --reload`

### Code layout

- `python/djust/scaffolding/init_project.py`: `detect_project()`,
  `plan_changes()` (returns `FileChange` records with old/new text),
  `apply_changes()`, `choose_package_action()`, `find_project_python()`,
  `run_check()`, `init_project()` orchestrator returning a result summary.
- `python/djust/scaffolding/templates.py`: `SETTINGS_BLOCK`, and `ASGI_PY`
  parametrized by `settings_module` so `djust new` and `djust init` share it.
- `python/djust/cli.py`: `init` subparser and `cmd_init`, which only parses
  arguments, calls `init_project()`, prints, and exits.

## Part 2 — `djust new` changes

- `Makefile`: targets use `.venv/bin/python -m uvicorn` and
  `.venv/bin/python manage.py`; no activation needed.
- Setup creates the environment with `uv venv --python ">=3.10"` when uv is
  available, then install, `makemigrations`, `migrate`, and `manage.py check`.
- `requirements.txt` floor is `djust>=<generating djust version>` (local
  version segment stripped).
- After success, `cmd_new` prints `cd <name>`, `make dev`, and
  `http://127.0.0.1:8000/`; on Windows it prints
  `.venv\Scripts\python -m uvicorn <name>.asgi:application --reload`
  instead of `make dev`. With `--no-setup` it prints the setup commands.

## Part 3 — Documentation

- `docs/website/getting-started/installation.md`: requirements, then three
  paths — new project (three commands plus the feature-flag table), existing
  project (`djust init`, `--dry-run`, what it changes), and "Configure by hand"
  (the current manual settings/ASGI/URL steps). Building from source stays.
- `docs/website/getting-started/troubleshooting.md`: the two recovery
  sections moved from the install page, linked from it.
- `docs/website/guides/scaffolding.md`: short `djust init` reference.
- `changelog.d/`: `djust-init.added.md` and a `scaffold-make-dev.changed.md`.

## Testing

`python/djust/tests/test_djust_init.py` (TDD):

- detection: default layout, `--settings`, settings package refused, missing
  `manage.py`;
- settings planning: appended once; rerun unchanged; the rendered block,
  executed against list and tuple `INSTALLED_APPS`/`TEMPLATES`, pre-listed
  apps, and an existing `CHANNEL_LAYERS`, yields the expected values;
- asgi: stock (both quote styles, with/without docstring) replaced;
  customized left with status 2; already configured unchanged;
- package action for each project type, with subprocess mocked;
  requirements appended only when missing;
- project environment never resolves to a `VIRTUAL_ENV` outside the project;
- git: dirty target refused, `--force` proceeds, `--dry-run` writes nothing;
- end to end: `django-admin startproject` in a temp dir, `djust init
  --no-install`, then `manage.py check` under the test interpreter passes.

Scaffold tests: Makefile uses `.venv` interpreter; requirements floor matches
`djust.__version__`; setup runs `check` last; CLI success output.

Manual verification before merge, from outside any active environment:
`uvx --from . djust new demo` then `make dev`; and `uvx --from . djust init`
on a fresh `django-admin startproject`.

## Release

Branch `feat/djust-init` is based on `codex/markdown-editor-guide-readability`,
which carries the unreleased `b6d01e94` and the install-doc rewrite. The
simplified docs describe unreleased behavior, so they ship together with the
release that contains this work.
