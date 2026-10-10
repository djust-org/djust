"""Live-template UI heuristics and summary cache for ADR-043 §D2."""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from djust.audit_ast import ASTAuditReport, ASTFinding, _template_suppressed

X1XX_CODES = ("X101", "X102", "X103", "X104", "X105")
X102_OPTION_THRESHOLD = 10
_MAX_UI_FILE_BYTES = 1_048_576
UI_SUMMARY_RELPATH = os.path.join(".djust", "audit-ui.json")
UI_SUMMARY_VERSION = 1

_X101_DETAILS = (
    "use {% data_table rows=rows columns=columns %} (sorting, paging, selection, search), "
    "{% data_grid columns=columns rows=rows %} for editable cells, and wrap a growing list in "
    '{% infinite_scroll load_event="load_more" %}…{% endinfinite_scroll %}'
)
_X102_DETAILS = (
    'use {% combobox name="field" options=options event="on_change" %} (searchable) or '
    '{% rich_select name="field" options=options event="on_change" searchable=True %}'
)
_X103_DETAILS = (
    'use {% sheet open=show_panel title="Details" %}…{% endsheet %} for a side panel or '
    '{% modal open=show_modal title="Details" %}…{% endmodal %} for a dialog'
)
_X104_DETAILS = (
    'use {% server_toast_container position="top-right" %} with '
    'self.push_toast("Saved", type="success") (ServerEventToastMixin), '
    "{% toast_container toasts %} for a list the view holds, or "
    '{% page_alert type="warning" %}…{% endpage_alert %} for a persistent banner'
)
_X105_DETAILS = (
    "use theme tokens such as color: hsl(var(--foreground)); background: hsl(var(--card)), "
    'and choose a preset with LIVEVIEW_CONFIG["theme"]["preset"]'
)

_LIVE_MARKER_RE = re.compile(
    r"(?:\s|^)dj-[a-z][a-z0-9-]*(?=[\s=>/])|\{%\s*load\s[^%]*\blive_tags\b", re.M
)
_TEMPLATE_NAME_RE = re.compile(r"""\btemplate_name\s*(?::[^=\n]+)?=\s*["']([^"'\n]+\.html)["']""")
_TEMPLATE_REF_RE = re.compile(r"""\{%\s*(?:extends|include)\s+["']([^"']+)["']""")
_OPEN_TAG_RE = re.compile(r"""<([a-zA-Z][\w-]*)((?:[^<>"']|"[^"]*"|'[^']*')*)>""")
_TEMPLATE_EXPR_RE = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.S)
_CLASS_RE = re.compile(r"""\bclass\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_STYLE_RE = re.compile(r"""\bstyle\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_FOR_RE = re.compile(r"\{%\s*for\b")
_X103_OVERLAY_CLASSES = {
    "modal",
    "modal-backdrop",
    "modal-overlay",
    "modal-dialog",
    "drawer",
    "offcanvas",
    "slide-over",
    "side-panel",
    "overlay",
}
_MESSAGEISH_RE = re.compile(
    r"\b\w*(?:message|msg|flash|notice|notification|error|success|alert|feedback|toast)\w*\b", re.I
)
_SWITCH_RE = re.compile(r"\{%\s*(?:if|elif)\s+([^%]+)%\}|\{\{\s*([^}|]+)")
_BODY_EXPR_RE = re.compile(r"\{\{\s*([^}|]+)")
_STATE_RE = re.compile(
    r"\b(?:error|success|danger|warning|info|ok|fail(?:ed|ure)?|invalid|valid)\b", re.I
)
_STATIC_LINK_RE = re.compile(
    r"""<link\b[^>]*?\bhref\s*=\s*["']\{%\s*static\s+["']([^"']+\.css)["']\s*%\}["']""", re.I
)
_CSS_SUPPRESSION_RE = re.compile(
    r"/\*\s*djust\s*:\s*noqa(?:\s*[:\s]\s*([A-Za-z0-9, ]+))?\s*\*/", re.I
)
_CSS_DECL_RE = re.compile(r"(--[\w-]+|[a-zA-Z-]+)\s*:\s*([^;{}]+)")
_COLOR_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
_COLOR_FUNC_RE = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(([^()]*)\)", re.I)
_DERIVED_TEXT_TOKENS = {
    "primary-text",
    "destructive-text",
    "success-text",
    "warning-text",
    "info-text",
    "brand-text",
}
_ALIAS_TOKENS = {
    "sidebar-background",
    "sidebar-foreground",
    "sidebar-primary",
    "sidebar-primary-foreground",
    "sidebar-accent",
    "sidebar-accent-foreground",
    "sidebar-border",
    "sidebar-ring",
    "chart-1",
    "chart-2",
    "chart-3",
    "chart-4",
    "chart-5",
    "chart-6",
}


@dataclass
class UIScanResult:
    """UI findings and the files used to derive them."""

    templates: Dict[str, bool] = field(default_factory=dict)
    stylesheets: List[str] = field(default_factory=list)
    findings: List[ASTFinding] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def collect_liveview_template_names(source: str) -> Set[str]:
    """Collect literal template names in source mentioning a live class."""
    if "LiveView" not in source and "LiveComponent" not in source:
        return set()
    return set(_TEMPLATE_NAME_RE.findall(source))


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group())


def _mask_comments(source: str) -> str:
    return re.sub(
        r"\{#.*?#\}|\{%\s*comment\b[^%]*%\}[\s\S]*?\{%\s*endcomment\s*%\}|<!--[\s\S]*?-->",
        _blank,
        source,
    )


def _mask_css_comments(source: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", _blank, source)


def _line_col(source: str, offset: int) -> Tuple[int, int]:
    return source.count("\n", 0, offset) + 1, offset - (source.rfind("\n", 0, offset) + 1)


def _iter_open_tags(source: str):
    return _OPEN_TAG_RE.finditer(source)


def _region(source: str, tag: str, start: int) -> str:
    end = source.lower().find("</" + tag.lower(), start)
    return source[start:] if end < 0 else source[start:end]


def _template_index(paths: Sequence[str]) -> Dict[str, Set[str]]:
    index: Dict[str, Set[str]] = {}
    for path in paths:
        parts = PurePath(path).parts
        for i, part in enumerate(parts):
            if part == "templates":
                name = "/".join(parts[i + 1 :])
                index.setdefault(name, set()).add(path)
    return index


def _live_closure(sources: Mapping[str, str], names: Set[str]) -> Set[str]:
    index = _template_index(list(sources))
    live = {path for path, src in sources.items() if _LIVE_MARKER_RE.search(src)}
    for name in names:
        live.update(index.get(name, ()))
    worklist = list(live)
    while worklist:
        path = worklist.pop()
        for name in _TEMPLATE_REF_RE.findall(sources[path]):
            for linked in index.get(name, ()):
                if linked not in live:
                    live.add(linked)
                    worklist.append(linked)
    return live


def _check_x101(tag: str, attrs: str, region: str) -> Optional[str]:
    if tag == "table" and (
        _FOR_RE.search(region)
        or re.search(r"\bdj-stream\b", region)
        or re.search(r"dj-update\s*=\s*[\"'](?:append|prepend)[\"']", region)
    ):
        return _X101_DETAILS
    return None


def _check_x102(tag: str, attrs: str, region: str) -> Optional[str]:
    if tag != "select" or not re.search(r"\bdj-change\b", attrs):
        return None
    if _FOR_RE.search(region):
        return "options generated by {% for %}; " + _X102_DETAILS
    count = len(re.findall(r"<option\b", region, re.I))
    if count > X102_OPTION_THRESHOLD:
        return f"{count} options; " + _X102_DETAILS
    return None


def _attr_value(match: re.Match) -> str:
    return match.group(2) if match.group(2) is not None else match.group(3)


def _check_x103(tag: str, attrs: str, region: str) -> Optional[str]:
    classes = _CLASS_RE.search(attrs)
    if classes:
        for token in _TEMPLATE_EXPR_RE.sub(" ", _attr_value(classes)).split():
            if token in _X103_OVERLAY_CLASSES:
                return f'matched class "{token}"; ' + _X103_DETAILS
    style = _STYLE_RE.search(attrs)
    if style:
        value = re.sub(r"\s+", "", _attr_value(style).lower())

        def zero(prop: str) -> bool:
            return bool(re.search(r"(?:^|;)" + prop + r":0(?:px|rem|em|%)?(?:;|$|!)", value))

        if "position:fixed" in value and (zero("inset") or (zero("top") and zero("bottom"))):
            return "position: fixed full-height style; " + _X103_DETAILS
    return None


def _check_x104(tag: str, attrs: str, region: str) -> Optional[str]:
    if tag not in {"p", "div", "span"}:
        return None
    classes = _CLASS_RE.search(attrs)
    if not classes:
        return None
    value = _attr_value(classes)
    if not any(_MESSAGEISH_RE.search(m.group(1) or m.group(2)) for m in _SWITCH_RE.finditer(value)):
        return None
    body = region[region.find(">") + 1 :]
    if _STATE_RE.search(_TEMPLATE_EXPR_RE.sub(" ", value)) or any(
        _MESSAGEISH_RE.search(m.group(1)) for m in _BODY_EXPR_RE.finditer(body)
    ):
        return _X104_DETAILS
    return None


def _resolve_static(
    rel: str, static_dirs: Sequence[Union[str, Tuple[str, str]]], static_roots: Sequence[str]
) -> Set[str]:
    if os.path.isabs(rel) or ".." in rel.split("/") or rel.endswith(".min.css"):
        return set()
    paths: Set[str] = set()
    for entry in [*static_roots, *static_dirs]:
        prefix, base = entry if isinstance(entry, (tuple, list)) else ("", entry)
        name = rel
        if prefix:
            if not name.startswith(str(prefix) + "/"):
                continue
            name = name[len(str(prefix)) + 1 :]
        path = os.path.realpath(os.path.join(base, *name.split("/")))
        if any(
            part in {"site-packages", "dist-packages", "node_modules"}
            for part in PurePath(path).parts
        ):
            continue
        if os.path.isfile(path):
            paths.add(path)
    return paths


def _find_linked_stylesheets(
    sources: Mapping[str, str],
    live: Set[str],
    static_dirs: Sequence[Union[str, Tuple[str, str]]],
    static_roots: Sequence[str],
) -> Set[str]:
    paths: Set[str] = set()
    for path in live:
        for rel in _STATIC_LINK_RE.findall(sources[path]):
            paths.update(_resolve_static(rel, static_dirs, static_roots))
    return paths


def _css_suppressed(line: str, code: str) -> bool:
    match = _CSS_SUPPRESSION_RE.search(line)
    if not match:
        return False
    codes = match.group(1)
    return not codes or code.upper() in {c.strip().upper() for c in codes.split(",") if c.strip()}


@functools.lru_cache(maxsize=1)
def _theme_token_names() -> Set[str]:
    from djust.theming._types import ThemeTokens

    return (
        {f.name.replace("_", "-") for f in dataclasses.fields(ThemeTokens)}
        | _DERIVED_TEXT_TOKENS
        | _ALIAS_TOKENS
    )


def _scan_stylesheet(path: str, source: str) -> List[ASTFinding]:
    masked = _mask_css_comments(source)
    lines = source.splitlines()
    offenders: List[Tuple[int, str]] = []
    for match in _CSS_DECL_RE.finditer(masked):
        prop, value = match.groups()
        if prop.startswith("--") and prop[2:] in _theme_token_names():
            continue
        while True:
            normalised = re.sub(r"var\([^()]*\)", "__TOKEN__", value)
            if normalised == value:
                break
            value = normalised
        value = re.sub(r"url\([^)]*\)", "", value, flags=re.I)
        if not (
            _COLOR_HEX_RE.search(value)
            or any("__TOKEN__" not in m.group(1) for m in _COLOR_FUNC_RE.finditer(value))
        ):
            continue
        lineno, _ = _line_col(masked, match.start())
        if not _css_suppressed(lines[lineno - 1], "X105"):
            offenders.append((lineno, match.group().strip()))
    if not offenders:
        return []
    lineno, decl = offenders[0]
    line_list = ", ".join(str(n) for n, _ in offenders[:10])
    if len(offenders) > 10:
        line_list += ", …"
    details = (
        f"{len(offenders)} color literal(s), first `{decl}` at line {lineno} (lines {line_list}); "
        + _X105_DETAILS
    )
    return [ASTFinding.make("X105", path, lineno, details=details)]


def scan_ui(
    root: str,
    html_sources: Mapping[str, str],
    template_names: Set[str],
    static_dirs: Sequence[Union[str, Tuple[str, str]]] = (),
    static_roots: Sequence[str] = (),
    *,
    exclude: Sequence[str] = (),
) -> UIScanResult:
    """Scan the live template closure and its linked application stylesheets."""
    result = UIScanResult()
    sources: Dict[str, str] = {}
    for path, source in html_sources.items():
        result.templates[path] = False
        if len(source.encode("utf-8")) > _MAX_UI_FILE_BYTES:
            result.skipped.append((path, "too large for UI rules"))
        else:
            sources[path] = _mask_comments(source)
    live = _live_closure(sources, template_names)
    for path in live:
        result.templates[path] = True
        source = sources[path]
        original_lines = html_sources[path].splitlines()
        for match in _iter_open_tags(source):
            tag, attrs = match.groups()
            region = _region(source, tag, match.start())
            lineno, col = _line_col(source, match.start())
            for code, check in (
                ("X101", _check_x101),
                ("X102", _check_x102),
                ("X103", _check_x103),
                ("X104", _check_x104),
            ):
                details = check(tag.lower(), attrs, region)
                if details and not _template_suppressed(original_lines[lineno - 1], code):
                    result.findings.append(ASTFinding.make(code, path, lineno, col, details))
    for path in sorted(_find_linked_stylesheets(sources, live, static_dirs, static_roots)):
        rel = os.path.relpath(path, os.path.abspath(root))
        if any(rel == e or rel.startswith(e + os.sep) for e in exclude):
            continue
        try:
            if os.path.getsize(path) > _MAX_UI_FILE_BYTES:
                result.skipped.append((path, "too large for UI rules"))
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                source = fh.read()
            result.stylesheets.append(path)
            result.findings.extend(_scan_stylesheet(path, source))
        except OSError as exc:
            result.skipped.append((path, exc.strerror or type(exc).__name__))
    return result


def build_ui_summary(report: ASTAuditReport, root: str) -> Dict[str, Any]:
    """Build the versioned, mtime-based summary consumed by discovery checks."""
    from djust import __version__

    root = os.path.abspath(root)
    counts = Counter(f.code for f in report.findings if f.code in X1XX_CODES)

    def records(paths: Sequence[str], templates: bool = False) -> Dict[str, Any]:
        result = {}
        for path in sorted(paths):
            findings = Counter(
                f.code for f in report.findings if f.path == path and f.code in X1XX_CODES
            )
            item = {"mtime": os.stat(path).st_mtime, "findings": dict(findings)}
            if templates:
                item["live"] = report.ui_templates[path]
            result[PurePath(os.path.relpath(path, root)).as_posix()] = item
        return result

    return {
        "version": UI_SUMMARY_VERSION,
        "generator": "djust_audit --ast",
        "djust_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": root,
        "rules": list(X1XX_CODES),
        "counts": {code: counts[code] for code in X1XX_CODES},
        "total": sum(counts.values()),
        "templates": records(list(report.ui_templates), templates=True),
        "stylesheets": records(report.ui_stylesheets),
    }


def write_ui_summary(report: ASTAuditReport, root: str) -> Tuple[Optional[str], Optional[str]]:
    """Atomically write the cache, returning write errors as data."""
    path = os.path.join(os.path.abspath(root), UI_SUMMARY_RELPATH)
    dirpath = os.path.dirname(path)
    temp_path = None
    try:
        payload = build_ui_summary(report, root)
        os.makedirs(dirpath, exist_ok=True)
        ignore = os.path.join(dirpath, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w", encoding="utf-8") as fh:
                fh.write("*\n")
        fd, temp_path = tempfile.mkstemp(dir=dirpath, prefix=".audit-ui.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(temp_path, path)
        return path, None
    except (OSError, ValueError, TypeError) as exc:
        return None, getattr(exc, "strerror", None) or type(exc).__name__
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
