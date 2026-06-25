"""djust system checks — shared utilities (file discovery, suppression, comment/verbatim stripping).

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import ast
import os
import re
from collections.abc import Iterable, Iterator
from typing import Any, Optional

from django.core.checks import Error, Info, Warning

import djust.checks as _root

# Shared surface this module provides to the sibling check submodules
# (configuration, components, templates, …). Declared explicitly because
# some of these — notably the ``_LIVE_RENDER_*`` regexes — are consumed only
# by sibling modules (templates.py + components.py) and never read within
# utils.py itself, so they read as module-unused to static analysis even
# though they are live shared constants. Listing them here documents the
# contract and marks them as exported.
__all__ = [
    # result classes
    "DjustError",
    "DjustWarning",
    "DjustInfo",
    # discovery / parsing helpers
    "_is_check_suppressed",
    "_get_project_app_dirs",
    "_get_template_dirs",
    "_iter_python_files",
    "_iter_template_files",
    "_iter_js_files",
    "_parse_python_file",
    "_has_noqa",
    "_walk_subclasses",
    "_strip_verbatim_blocks",
    # shared scanner regexes
    "_LIVE_RENDER_TAG_RE",
    "_LIVE_RENDER_STICKY_TRUTHY_RE",
    "_LIVE_RENDER_STICKY_FALSY_RE",
    "_VERBATIM_BLOCK_RE",
]


class _DjustCheckMixin:
    """Mixin that adds fix_hint, file_path, and line_number to check results."""

    def __init__(
        self,
        *args: Any,
        fix_hint: str = "",
        file_path: str = "",
        line_number: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fix_hint = fix_hint
        self.file_path = file_path
        self.line_number = line_number


class DjustError(_DjustCheckMixin, Error):
    """Error with fix_hint metadata."""

    pass


class DjustWarning(_DjustCheckMixin, Warning):
    """Warning with fix_hint metadata."""

    pass


class DjustInfo(_DjustCheckMixin, Info):
    """Info with fix_hint metadata."""

    pass


def _is_check_suppressed(check_id: str) -> bool:
    """Return True if the user has suppressed *check_id* via settings.

    Users can add ``suppress_checks`` to ``DJUST_CONFIG`` (or
    ``LIVEVIEW_CONFIG``) to silence informational checks that are noisy for
    projects that deliberately don't use the checked feature::

        # settings.py
        DJUST_CONFIG = {
            "suppress_checks": ["T002", "V008", "C003"],
        }

    Both short IDs (``"T002"``) and fully-qualified IDs (``"djust.T002"``)
    are accepted; comparison is case-insensitive.
    """
    try:
        from django.conf import settings

        suppressed = (
            getattr(settings, "DJUST_CONFIG", {}).get("suppress_checks")
            or getattr(settings, "LIVEVIEW_CONFIG", {}).get("suppress_checks")
            or []
        )
    except Exception:
        return False

    if not suppressed:
        return False

    # Normalise: accept "T002" or "djust.T002", case-insensitive
    normalised = set()
    for item in suppressed:
        item_lower = str(item).lower()
        normalised.add(item_lower)
        # Also add/remove the "djust." prefix so either form matches
        if item_lower.startswith("djust."):
            normalised.add(item_lower[len("djust.") :])
        else:
            normalised.add("djust." + item_lower)

    return check_id.lower() in normalised


def _is_within_djust_package(path: str) -> bool:
    """True if ``path`` is djust's own package dir or a directory inside it.

    Compares against the ACTUAL djust package location
    (``os.path.dirname(djust.__file__)``) rather than matching any path that
    happens to contain the substring ``/djust/``. The latter is too broad: a
    downstream project (or the repo itself) living under a ``…/djust/…`` path —
    e.g. ``examples/demo_project`` checked from inside the repo checkout — would
    be excluded, blinding S009/S011 and every other dir-walking check (#1865).
    """
    import djust as _djust_pkg

    try:
        djust_dir = os.path.realpath(os.path.dirname(_djust_pkg.__file__))
    except (AttributeError, TypeError):
        return False

    real_path = os.path.realpath(path)
    if real_path == djust_dir:
        return True
    # Inside the package: real_path is djust_dir + os.sep + <something>.
    return real_path.startswith(djust_dir + os.sep)


def _get_project_app_dirs() -> list[str]:
    """Return directories for project apps (excluding third-party and djust itself)."""
    from django.apps import apps

    dirs = []
    for config in apps.get_app_configs():
        path = config.path
        # Skip site-packages / third-party
        if "site-packages" in path:
            continue
        # Skip djust's own package (its actual location, NOT any path that
        # merely contains "/djust/" — #1865).
        if _is_within_djust_package(path):
            continue
        if os.path.isdir(path):
            dirs.append(path)
    return dirs


def _get_template_dirs() -> list[str]:
    """Return all configured template directories."""
    from django.conf import settings

    dirs = []
    for backend in getattr(settings, "TEMPLATES", []):
        for d in backend.get("DIRS", []):
            if os.path.isdir(d):
                dirs.append(d)
        # Also check APP_DIRS templates
        if backend.get("APP_DIRS"):
            # ``_root`` is ``djust.checks``, whose symbols are re-exported
            # dynamically via ``setattr`` in ``__init__`` (so tests can
            # monkeypatch by the ``djust.checks._get_project_app_dirs`` path).
            # mypy can't see that dynamic attribute — narrow ignore preserves
            # the patch-by-path contract.
            for app_dir in _root._get_project_app_dirs():  # type: ignore[attr-defined]
                tpl_dir = os.path.join(app_dir, "templates")
                if os.path.isdir(tpl_dir):
                    dirs.append(tpl_dir)
    return dirs


def _iter_python_files(directories: Iterable[str]) -> Iterator[str]:
    """Yield .py file paths from directories, skipping migrations/tests."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            # Skip common non-project directories
            basename = os.path.basename(root)
            if basename in ("migrations", "tests", "__pycache__", ".venv", "node_modules"):
                continue
            for fname in files:
                if fname.endswith(".py"):
                    yield os.path.join(root, fname)


def _iter_template_files(directories: Iterable[str]) -> Iterator[str]:
    """Yield .html template file paths from directories."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            for fname in files:
                if fname.endswith(".html"):
                    yield os.path.join(root, fname)


def _iter_js_files(directories: Iterable[str]) -> Iterator[str]:
    """Yield .js file paths from directories."""
    for directory in directories:
        for root, _dirs, files in os.walk(directory):
            basename = os.path.basename(root)
            if basename in ("node_modules", "__pycache__", ".venv"):
                continue
            for fname in files:
                if fname.endswith(".js"):
                    yield os.path.join(root, fname)


def _parse_python_file(
    filepath: str,
) -> tuple[Optional[ast.Module], list[str]]:
    """Return (AST tree, source_lines) for a Python file, or (None, []) on failure.

    source_lines is 1-indexed: source_lines[0] is unused, source_lines[1] is line 1.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=filepath)
        # Prepend empty string so source_lines[1] == first line of file
        lines = [""] + source.splitlines()
        return tree, lines
    except SyntaxError:
        return None, []


def _has_noqa(source_lines: list[str], lineno: int, check_id: str) -> bool:
    """Return True if source line has a # noqa comment suppressing check_id.

    Supports:
        # noqa           — suppress all checks on this line
        # noqa: Q001     — suppress specific check
        # noqa: Q001,S002 — suppress multiple checks
    """
    if lineno < 1 or lineno >= len(source_lines):
        return False
    line = source_lines[lineno]
    # Find # noqa in the line
    idx = line.find("# noqa")
    if idx == -1:
        return False
    rest = line[idx + 6 :].strip()
    if not rest:
        return True  # bare # noqa — suppress everything
    if rest.startswith(":"):
        # Split on comma, take first whitespace-delimited token from each
        # to handle trailing comments like "# noqa: Q001 — reason here"
        codes = []
        for part in rest[1:].split(","):
            token = part.strip().split()[0] if part.strip() else ""
            codes.append(token)
        return check_id in codes
    return True


# ---------------------------------------------------------------------------
# LiveView checks (V0xx)
# ---------------------------------------------------------------------------


def _walk_subclasses(cls: type) -> Iterator[type]:
    """Recursively yield all subclasses of cls."""
    for sub in cls.__subclasses__():
        yield sub
        yield from _walk_subclasses(sub)


# A075 — `{% live_render ... %}` sticky+lazy collision scanner (v0.9.1, #1146).
# Captures the raw kwarg-list body so we can inspect it for both
# ``sticky=`` and ``lazy=`` truthy assignments. The two are mutually
# exclusive at tag-eval time (``TemplateSyntaxError`` in
# ``live_tags.live_render``); A075 promotes that runtime error to a
# startup-time warning so the misuse never reaches a request.
_LIVE_RENDER_TAG_RE = re.compile(r"\{%\s*live_render\b([^%]*?)%\}", re.DOTALL)
# Truthy assignments for sticky=/lazy=. Matches:
#   sticky=True / lazy=True  (literal)
#   sticky="..." / lazy="..." with non-empty quoted value (any non-empty string)
#   sticky=<bare-identifier> / lazy=<bare-identifier> (variable, conservatively
#       treated as truthy at scan time — false positives are silenceable
#       via the suppress_checks knob)
# False / "" / 0 are explicitly excluded.
_LIVE_RENDER_STICKY_TRUTHY_RE = re.compile(
    r"""\bsticky\s*=\s*(?:True|"[^"]+"|'[^']+'|[A-Za-z_]\w*)"""
)
# Explicit "this kwarg is FALSY" — overrides the truthy match above. We
# exclude these to avoid flagging ``sticky=False lazy=True`` (a legitimate
# pattern when toggling at template-evaluation time).
_LIVE_RENDER_STICKY_FALSY_RE = re.compile(r"""\bsticky\s*=\s*(?:False|"\s*"|'\s*'|0)\b""")

# #1004 — `{% verbatim %}...{% endverbatim %}` regions hold raw template
# source that Django renders as-is without parsing. Docs and marketing
# templates routinely wrap literal `{% dj_activity %}` examples in
# verbatim blocks; the A070 / A071 scanner used to treat those as real
# uninstrumented activity calls and false-positive. The pre-processor
# below replaces each verbatim region with whitespace (preserving line
# breaks) so downstream regex scanners see no `{% dj_activity %}` text
# inside the region, while line numbers from the original source remain
# accurate for any matches OUTSIDE the region.
#
# Both unnamed (`{% verbatim %}`) and named (`{% verbatim foo %}`)
# variants are matched. The endverbatim form must match the opening
# variant, but Django allows either to close — we accept either since
# the goal is to redact the BODY of the region, not validate Django
# syntax (the template engine itself will reject malformed verbatim
# blocks at render time).
_VERBATIM_BLOCK_RE = re.compile(
    r"\{%\s*verbatim\b[^%]*%\}.*?\{%\s*endverbatim\b[^%]*%\}",
    re.DOTALL,
)


def _strip_verbatim_blocks(content: str) -> str:
    """Replace the BODY of every ``{% verbatim %}...{% endverbatim %}`` region
    with whitespace, preserving newlines so line numbers stay accurate for
    matches outside the region.

    Used before regex-scanning for A070 / A071 (and any future scanner
    that walks template source as raw text) — without this, literal
    `{% dj_activity %}` examples wrapped in verbatim blocks (a common
    pattern on docs / marketing pages that document the tag) get
    flagged as real uninstrumented activity calls.

    The verbatim tags themselves are kept (so the scanner doesn't see
    a totally absent region), but their content is blanked. We replace
    every non-newline character with a space and keep newlines verbatim
    — line numbers stay aligned with the original source.

    Returns ``content`` unchanged if no verbatim region is present (the
    common case). Cost: one regex pass plus one rewrite when at least
    one verbatim region exists.
    """
    if "verbatim" not in content:
        return content

    def _redact(match: re.Match) -> str:
        body = match.group(0)
        # Preserve newlines, blank everything else.
        return "".join("\n" if ch == "\n" else " " for ch in body)

    return _VERBATIM_BLOCK_RE.sub(_redact, content)
