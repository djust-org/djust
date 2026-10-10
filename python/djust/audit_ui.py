"""Live-template UI heuristics and summary cache for ADR-043 §D2."""

from __future__ import annotations

import dataclasses
import functools
import json
import os
import re
import tempfile
import stat
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from heapq import merge
from pathlib import PurePath
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

from djust.audit_ast import ASTAuditReport, ASTFinding, _comment_suppressed, _template_suppressed

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
_TAG_START_RE = re.compile(r"<([a-zA-Z][\w-]*+)(?=[\s/>])")
_TEMPLATE_EXPR_RE = re.compile(r"\{%[^%]*+%\}|\{\{[^{}]*+\}\}", re.S)
_CLASS_RE = re.compile(r"""\bclass\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_STYLE_RE = re.compile(r"""\bstyle\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_OVERLAY_MARKER_RE = re.compile(
    r"\brole\s*=\s*['\"]dialog['\"]|\baria-modal(?=[\s=>/])|\bdj-click(?:-away)?\s*=",
    re.I,
)
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
    "sheet",
    "dialog",
}
_IDENTIFIER_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*+")
_MESSAGE_WORDS = {
    "message",
    "messages",
    "msg",
    "flash",
    "notice",
    "notification",
    "notifications",
    "alert",
    "feedback",
    "toast",
}
_SWITCH_RE = re.compile(r"\{%\s*(?:if|elif)\s+([^%]+)%\}|\{\{\s*([^{}|]+)")
_BODY_EXPR_RE = re.compile(r"\{\{\s*([^{}|]+)")
_STATE_RE = re.compile(
    r"\b(?:error|success|danger|warning|info|ok|fail(?:ed|ure)?|invalid|valid)\b", re.I
)
_STATIC_LINK_RE = re.compile(
    r"""\bhref\s*+=\s*+["']\{%\s*+static\s++["']([^"'{}]+\.css)["']\s*+%\}["']""", re.I
)
_CSS_DECL_RE = re.compile(r"\s*+(--[\w-]++|[a-zA-Z-]++)\s*+:\s*+([^;{}]++)")
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


def _mask_delimited(source: str, opener: re.Pattern, closers: Mapping[str, str]) -> str:
    parts = []
    cursor = 0
    while match := opener.search(source, cursor):
        parts.append(source[cursor : match.start()])
        closer = closers[match.lastgroup]
        end = source.find(closer, match.end())
        end = len(source) if end < 0 else end + len(closer)
        parts.append(re.sub(r"[^\n]", " ", source[match.start() : end]))
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts)


def _mask_comments(source: str) -> str:
    # Block comments close at an endcomment directive, rather than any %}.
    opener = re.compile(r"(?P<html><!--)|(?P<short>\{#)|(?P<block>\{%\s*+comment\b[^%]*+%\})")
    endcomment = re.compile(r"\{%\s*+endcomment\s*+%\}")
    parts = []
    cursor = 0
    while match := opener.search(source, cursor):
        parts.append(source[cursor : match.start()])
        if match.lastgroup == "block":
            closing = endcomment.search(source, match.end())
            end = closing.end() if closing else len(source)
        else:
            closer = "-->" if match.lastgroup == "html" else "#}"
            end = source.find(closer, match.end())
            end = len(source) if end < 0 else end + len(closer)
        parts.append(re.sub(r"[^\n]", " ", source[match.start() : end]))
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts)


def _mask_css_comments(source: str) -> str:
    return _mask_delimited(source, re.compile(r"(?P<css>/\*)"), {"css": "*/"})


def _line_starts(source: str) -> List[int]:
    return [0] + [m.end() for m in re.finditer("\n", source)]


def _line_col(starts: Sequence[int], offset: int) -> Tuple[int, int]:
    index = bisect_right(starts, offset) - 1
    return index + 1, offset - starts[index]


def _iter_open_tags(source: str) -> Iterator[Tuple[int, str, str]]:
    """Tokenize tags in one forward pass, including quoted > characters."""
    cursor = 0
    while match := _TAG_START_RE.search(source, cursor):
        start = match.start()
        pos = match.end()
        quote = None
        while pos < len(source):
            char = source[pos]
            if quote:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "<":
                break
            elif char == ">":
                yield start, match.group(1).lower(), source[match.end() : pos]
                pos += 1
                break
            pos += 1
        cursor = pos


def _region(closings: Mapping[str, Sequence[int]], tag: str, start: int, length: int) -> int:
    offsets = closings.get(tag, ())
    index = bisect_left(offsets, start)
    return offsets[index] if index < len(offsets) else length


def _in_region(offsets: Sequence[int], start: int, end: int) -> int:
    return bisect_left(offsets, end) - bisect_left(offsets, start)


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


def _check_x101(tag: str, has_rows: bool) -> Optional[str]:
    return _X101_DETAILS if tag == "table" and has_rows else None


def _check_x102(tag: str, attrs: str, generated: bool, count: int) -> Optional[str]:
    if tag != "select" or not re.search(r"\bdj-change\b", attrs):
        return None
    if generated:
        return "options generated by {% for %}; " + _X102_DETAILS
    if count > X102_OPTION_THRESHOLD:
        return f"{count} options; " + _X102_DETAILS
    return None


def _attr_value(match: re.Match) -> str:
    return match.group(2) if match.group(2) is not None else match.group(3)


def _check_x103(tag: str, attrs: str, overlay_context: bool = False) -> Optional[str]:
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

        full_edges = all(zero(prop) for prop in ("top", "bottom", "left", "right"))
        full_size = all(
            re.search(r"(?:^|;)" + prop + ":100" + unit + r"(?:;|$|!)", value)
            for prop, unit in (("width", "vw"), ("height", "vh"))
        )
        if re.search(r"(?:^|;)position:fixed(?:;|$|!)", value):
            if zero("inset") or full_edges or full_size:
                return "position: fixed full-screen style; " + _X103_DETAILS
            full_height = (zero("top") and zero("bottom")) or re.search(
                r"(?:^|;)height:100(?:vh|%)(?:;|$|!)", value
            )
            if full_height and overlay_context:
                return "position: fixed full-height overlay style; " + _X103_DETAILS
    return None


def _overlay_contexts(source: str, tags: Sequence[Tuple[int, str, str]]) -> Set[int]:
    """Index overlay context in linear passes, doing marker work only as needed."""
    candidates = {
        offset
        for offset, tag, attrs in tags
        if _STYLE_RE.search(attrs) and _check_x103(tag, _CLASS_RE.sub("", attrs), True)
    }
    if not candidates:
        return set()

    # Record disjoint outermost conditional spans once. Directives inside an
    # opening tag's attributes do not conditionally render that tag.
    regions: List[Tuple[int, int]] = []
    conditionals: List[int] = []
    tag_index = 0
    for match in re.finditer(r"\{%\s*(if|endif)\b[^%]*%\}", source):
        while tag_index < len(tags):
            offset, tag, attrs = tags[tag_index]
            if offset + len(tag) + len(attrs) + 2 > match.start():
                break
            tag_index += 1
        if tag_index < len(tags) and tags[tag_index][0] <= match.start():
            continue
        if match.group(1) == "if":
            conditionals.append(match.end())
        elif conditionals:
            start = conditionals.pop()
            if not conditionals:
                regions.append((start, match.start()))
    if conditionals:
        regions.append((conditionals[0], len(source)))

    contexts: Set[int] = set()
    backdrop_offsets: Set[int] = set()
    region_index = 0
    for offset, tag, attrs in tags:
        if offset in candidates:
            while region_index < len(regions) and regions[region_index][1] <= offset:
                region_index += 1
            conditional = region_index < len(regions) and regions[region_index][0] <= offset
            if conditional or _OVERLAY_MARKER_RE.search(attrs):
                contexts.add(offset)
        # Backdrops can be non-fixed siblings. Inspect their markers only after
        # a cheap class-name screen; ordinary tags need no marker regex.
        backdrop = False
        if "backdrop" in attrs or "overlay" in attrs:
            classes = _CLASS_RE.search(attrs)
            backdrop = bool(
                classes
                and {"backdrop", "overlay", "modal-backdrop", "modal-overlay"}.intersection(
                    _attr_value(classes).split()
                )
            )
        if (backdrop or offset in candidates) and _OVERLAY_MARKER_RE.search(attrs):
            if backdrop or _check_x103(tag, _CLASS_RE.sub("", attrs)):
                backdrop_offsets.add(offset)
    if not backdrop_offsets:
        return contexts

    # Only backdrop sibling matching needs HTML ancestry. Merge the already
    # ordered streams rather than sorting events. Each stack entry is removed
    # once, including when malformed markup closes an ancestor.
    masked = _TEMPLATE_EXPR_RE.sub(lambda m: " " * len(m.group()), source)
    openings = ((offset, False, tag, attrs) for offset, tag, attrs in tags)
    closings = (
        (m.start(), True, m.group(1).lower(), "")
        for m in re.finditer(r"</([a-zA-Z][\w-]*+)\b", masked)
    )
    parents: Dict[int, Optional[int]] = {}
    backdrops: Set[Optional[int]] = set()
    stack: List[Tuple[str, int]] = []
    positions: Dict[str, List[int]] = {}
    void_tags = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    for offset, closing, tag, attrs in merge(openings, closings):
        if closing:
            matches = positions.get(tag)
            if matches:
                index = matches[-1]
                while len(stack) > index:
                    popped_tag, _ = stack.pop()
                    positions[popped_tag].pop()
        else:
            parent = stack[-1][1] if stack else None
            if offset in candidates:
                parents[offset] = parent
            if offset in backdrop_offsets:
                backdrops.add(parent)
            if tag not in void_tags and not attrs.rstrip().endswith("/"):
                positions.setdefault(tag, []).append(len(stack))
                stack.append((tag, offset))
    contexts.update(offset for offset, parent in parents.items() if parent in backdrops)
    return contexts


def _messageish(expression: str) -> bool:
    for match in _IDENTIFIER_RE.finditer(expression):
        name = match.group().lower()
        if _MESSAGE_WORDS.intersection(name.split("_")) or "_status_text_" in "_" + name + "_":
            return True
    return False


def _check_x104(tag: str, attrs: str, body_message: bool) -> Optional[str]:
    if tag not in {"p", "div", "span"}:
        return None
    classes = _CLASS_RE.search(attrs)
    if not classes:
        return None
    value = _attr_value(classes)
    switches = [m.group(1) or m.group(2) for m in _SWITCH_RE.finditer(value)]
    if not switches:
        return None
    if not any(_messageish(expr) for expr in switches) and not body_message:
        return None
    if _STATE_RE.search(_TEMPLATE_EXPR_RE.sub(" ", value)) or body_message:
        return _X104_DETAILS
    return None


def _resolve_static(
    rel: str, static_dirs: Sequence[Union[str, Tuple[str, str]]], static_roots: Sequence[str]
) -> Set[str]:
    if "\\" in rel or os.path.isabs(rel) or ".." in rel.split("/") or rel.endswith(".min.css"):
        return set()
    paths: Set[str] = set()
    for entry in [*static_roots, *static_dirs]:
        prefix, base = entry if isinstance(entry, (tuple, list)) else ("", entry)
        name = rel
        if prefix:
            if not name.startswith(str(prefix) + "/"):
                continue
            name = name[len(str(prefix)) + 1 :]
        base = os.path.realpath(base)
        path = os.path.realpath(os.path.join(base, *name.split("/")))
        if os.path.commonpath([path, base]) != base:
            continue
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
        for _, tag, attrs in _iter_open_tags(sources[path]):
            if tag == "link":
                for rel in _STATIC_LINK_RE.findall(attrs):
                    paths.update(_resolve_static(rel, static_dirs, static_roots))
    return paths


@functools.lru_cache(maxsize=1)
def _theme_token_names() -> Set[str]:
    from djust.theming._types import ThemeTokens

    return (
        {f.name.replace("_", "-") for f in dataclasses.fields(ThemeTokens)}
        | _DERIVED_TEXT_TOKENS
        | _ALIAS_TOKENS
    )


def _css_declarations(source: str) -> Iterator[Tuple[int, str, str, str]]:
    """Match declarations only in blocks, never selector preludes."""
    start = 0
    depth = 0
    for delimiter in re.finditer(r"[{};]", source):
        char = delimiter.group()
        if char != "{" and depth:
            segment = source[start : delimiter.start()]
            match = _CSS_DECL_RE.fullmatch(segment)
            if match:
                offset = start + len(segment) - len(segment.lstrip())
                yield offset, *match.groups(), segment.strip()
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        start = delimiter.end()


def _normalise_css_value(value: str) -> str:
    # Drop entire var/url subtrees, including arbitrarily nested fallbacks,
    # without repeatedly substituting innermost parentheses.
    parts = []
    cursor = 0
    depth = 0
    for match in re.finditer(r"\b(?:var|url)\(|[()]", value, re.I):
        token = match.group().lower()
        if depth:
            depth += -1 if token == ")" else 1
            if depth == 0:
                cursor = match.end()
        elif token in {"var(", "url("}:
            parts.append(value[cursor : match.start()])
            parts.append("__TOKEN__" if token == "var(" else "")
            depth = 1
    if not depth:
        parts.append(value[cursor:])
    return "".join(parts)


def _safe_declaration(decl: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", decl[:120])


def _scan_stylesheet(path: str, source: str) -> List[ASTFinding]:
    masked = _mask_css_comments(source)
    lines = source.splitlines()
    starts = _line_starts(masked)
    suppressed = {
        i + 1 for i, line in enumerate(lines) if _comment_suppressed(line, "X105", "/*", "*/")
    }
    offenders: List[Tuple[int, str]] = []
    for offset, prop, value, declaration in _css_declarations(masked):
        if prop.startswith("--") and prop[2:] in _theme_token_names():
            continue
        value = _normalise_css_value(value)
        if not (
            _COLOR_HEX_RE.search(value)
            or any("__TOKEN__" not in m.group(1) for m in _COLOR_FUNC_RE.finditer(value))
        ):
            continue
        lineno, _ = _line_col(starts, offset)
        if lineno not in suppressed:
            offenders.append((lineno, _safe_declaration(declaration)))
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
        lower = source.lower()
        starts = _line_starts(source)
        closings: Dict[str, List[int]] = {}
        for match in re.finditer(r"</([a-zA-Z][\w-]*+)\b", lower):
            closings.setdefault(match.group(1), []).append(match.start())
        loops = [m.start() for m in _FOR_RE.finditer(source)]
        rows = [
            m.start()
            for m in re.finditer(
                r"\{%\s*+for\b|\bdj-stream\b|dj-update\s*+=\s*+[\"'](?:append|prepend)[\"']", source
            )
        ]
        options = [m.start() for m in re.finditer(r"<option\b", lower)]
        messages = [m.start() for m in _BODY_EXPR_RE.finditer(source) if _messageish(m.group(1))]
        original_lines = html_sources[path].splitlines()
        suppressed = {
            code: {
                i + 1 for i, line in enumerate(original_lines) if _template_suppressed(line, code)
            }
            for code in X1XX_CODES[:4]
        }
        tags = list(_iter_open_tags(source))
        overlay_contexts = _overlay_contexts(source, tags)
        for offset, tag, attrs in tags:
            end = _region(closings, tag, offset, len(source))
            lineno, col = _line_col(starts, offset)
            checks = (
                ("X101", _check_x101(tag, bool(_in_region(rows, offset, end)))),
                (
                    "X102",
                    _check_x102(
                        tag,
                        attrs,
                        bool(_in_region(loops, offset, end)),
                        _in_region(options, offset, end),
                    ),
                ),
                ("X103", _check_x103(tag, attrs, offset in overlay_contexts)),
                (
                    "X104",
                    _check_x104(
                        tag,
                        attrs,
                        bool(_in_region(messages, offset + len(tag) + len(attrs) + 2, end)),
                    ),
                ),
            )
            for code, details in checks:
                if details and lineno not in suppressed[code]:
                    result.findings.append(ASTFinding.make(code, path, lineno, col, details))
    for path in sorted(_find_linked_stylesheets(sources, live, static_dirs, static_roots)):
        rel = os.path.relpath(path, os.path.realpath(root))
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

    root = os.path.realpath(root)
    counts: Counter = Counter()
    per_path: Dict[str, Counter] = {}
    for finding in report.findings:
        if finding.code in X1XX_CODES:
            counts[finding.code] += 1
            per_path.setdefault(finding.path, Counter())[finding.code] += 1

    def records(paths: Sequence[str], templates: bool = False) -> Dict[str, Any]:
        result = {}
        for path in sorted(paths):
            findings = per_path.get(path, Counter())
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
    path = os.path.join(os.path.realpath(root), UI_SUMMARY_RELPATH)
    dirpath = os.path.dirname(path)
    temp_path = None
    result: Tuple[Optional[str], Optional[str]] = (None, None)
    try:
        payload = build_ui_summary(report, root)
        created = False
        try:
            os.mkdir(dirpath)
            created = True
        except FileExistsError:
            # Existing cache directories are validated with lstat below.
            created = False
        mode = os.lstat(dirpath).st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            return None, "cache directory is not a real directory"
        if created:
            ignore = os.path.join(dirpath, ".gitignore")
            fd = os.open(ignore, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("*\n")
        fd, temp_path = tempfile.mkstemp(dir=dirpath, prefix=".audit-ui.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, indent=2)
            fh.write("\n")
        os.replace(temp_path, path)
        temp_path = None
        result = path, None
    except (OSError, ValueError, TypeError) as exc:
        result = None, getattr(exc, "strerror", None) or type(exc).__name__
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                # A missing temporary file is already cleaned up.
                temp_path = None
            except OSError as exc:
                result = None, exc.strerror or type(exc).__name__
    return result
