"""djust system checks — template checks (T0xx) — template file scanning.

Split from the former monolithic ``checks.py`` (#1822). No behavior change.
"""

import logging
import os
import re
from typing import Any

from django.core.checks import CheckMessage, register

from djust.checks.utils import (
    DjustError,
    DjustInfo,
    DjustWarning,
    _is_check_suppressed,
    _iter_template_files,
    _get_template_dirs,
    _strip_verbatim_blocks,
    _LIVE_RENDER_TAG_RE,
    _LIVE_RENDER_STICKY_TRUTHY_RE,
    _LIVE_RENDER_STICKY_FALSY_RE,
)

logger = logging.getLogger(__name__)


_DJ_VIEW_RE = re.compile(r"dj-view")


# ---------------------------------------------------------------------------
# Template checks (T0xx)
# ---------------------------------------------------------------------------

_DEPRECATED_ATTR_RE = re.compile(
    r"@(click|input|change|submit|blur|focus|keydown|keyup|mouseenter|mouseleave)="
)
# A070 / A071 — ``{% dj_activity %}`` block tag scanner (v0.7.0).
# Captures the raw argument list after the tag name so we can inspect it
# for a ``name=`` / first-positional string and detect missing / duplicate
# names without invoking the full Django template parser. Multi-line tag
# bodies are handled via ``re.DOTALL``.
_DJ_ACTIVITY_TAG_RE = re.compile(r"\{%\s*dj_activity\b([^%]*?)%\}", re.DOTALL)
# Activity name extractor. We accept three forms of the first argument:
#   group 1: double-quoted string literal -> "panel-name"
#   group 2: single-quoted string literal -> 'panel-name'
#   group 3: bare identifier or dotted path -> panel_name, view.panel_name
# A bare identifier is treated as "name present" but resolves at render
# time, so the A071 duplicate check cannot compare it to another tag's
# identifier — we skip A071 for identifier-form names. Only emit A070
# when NONE of the three groups match (truly missing name).
_DJ_ACTIVITY_NAME_RE = re.compile(
    r"""^\s*(?:name\s*=\s*)?(?:"([^"]+)"|'([^']+)'|([A-Za-z_][\w.]*))\s*(?:$|\s)"""
)
_DJ_ROOT_RE = re.compile(r"dj-root")
_INCLUDE_RE = re.compile(r"\{%\s*include\s+")
_LIVEVIEW_CONTENT_RE = re.compile(r"\{\{\s*liveview_content\s*\|\s*safe\s*\}\}")
# S007 (#1821) — `{{ <expr>.client_name|safe }}` stored-XSS scanner. Upload
# entries (djust.uploads.UploadEntry, field defined at uploads/__init__.py:483)
# store the client-supplied filename UNSANITISED — auto-escaping is the only
# thing protecting templates that render `client_name`. Marking it `|safe`
# bypasses that protection, so an attacker-controlled filename containing
# `<script>...</script>` becomes a stored-XSS vector. We anchor on the
# `{{ ... }}` variable form (NOT a bare `client_name|safe` substring) and
# require `client_name` to be the rendered variable or its trailing attribute:
#   - `[\w.]*\bclient_name` matches `entry.client_name`,
#     `upload_entry.client_name`, `obj.uploads.0.client_name`, and bare
#     `client_name`.
#   - The `\b` word boundary rejects `notclient_name`; the trailing `\s*\|`
#     rejects `client_name_foo` (a different attribute sharing the prefix).
# Whitespace around `|` is tolerated (mirrors _LIVEVIEW_CONTENT_RE), so both
# `client_name|safe` and `client_name | safe` are flagged.
_CLIENT_NAME_SAFE_RE = re.compile(r"\{\{\s*[\w.]*\bclient_name\s*\|\s*safe\s*\}\}")
# T004 (#1809) — `document.addEventListener('djust:...')`. The matcher captures
# the event name (group 1) so the emission site can exempt the djust: events
# that djust itself dispatches on `document` (see _DOC_DISPATCHED_DJUST_EVENTS).
# Those listeners are CORRECT on `document`; flagging them and telling the user
# to switch to `window` would BREAK the listener (it would never fire).
_DOC_DJUST_EVENT_RE = re.compile(
    r"""document\s*\.\s*addEventListener\s*\(\s*['"]djust:([a-zA-Z0-9_-]*)"""
)
# Authoritative set of djust: events that the client bundle dispatches on
# `document` (NOT `window`). Sourced from client.js
# `document.dispatchEvent(new CustomEvent('djust:...'))` sites:
#   - 'djust:ws-reconnected'    python/djust/static/djust/client.js:670
#   - 'djust:hvr-applied'       python/djust/static/djust/client.js:1463
#   - 'djust:time-travel-state' python/djust/static/djust/client.js:1483
#   - 'djust:time-travel-event' python/djust/static/djust/client.js:1501
#   - 'djust:navigate-start'    python/djust/static/djust/client.js:10865
#   - 'djust:navigate-end'      python/djust/static/djust/client.js:10877
#   - 'djust:layout-changed'    python/djust/static/djust/client.js:13855
# (mirrored in src modules 03-websocket.js / 18-navigation.js / 40-dj-layout.js).
# A listener for any of these on `document` is correct and must NOT trigger T004.
_DOC_DISPATCHED_DJUST_EVENTS = frozenset(
    {
        "ws-reconnected",
        "hvr-applied",
        "time-travel-state",
        "time-travel-event",
        "navigate-start",
        "navigate-end",
        "layout-changed",
    }
)
_NAV_DATA_ATTRS = re.compile(r"data-(view|tab|page|section)")  # Navigation-style data attributes
_DJ_EVENT_DIRECTIVES_RE = re.compile(
    r"dj-(click|input|change|submit|blur|focus|keydown|keyup|mouseenter|mouseleave|window-\w+|document-\w+|click-away|shortcut)="
)
_DJ_COMPONENT_RE = re.compile(r"dj-component")
# T016 (#1733) — dj-navigate directive. Used to warn when SPA navigation is
# requested but the URLconf-derived route map is empty (so dj-navigate would
# silently full-reload instead of navigating over the WebSocket).
_DJ_NAVIGATE_RE = re.compile(r"dj-navigate\s*=")
# #1096 — opt-out marker for fragment templates that are intentionally
# {% include %}d from a parent LiveView root. Fragment authors annotate
# the file with `{# djust:partial #}` (case-insensitive, optional surrounding
# whitespace) to silence T012 without introducing a global suppression.
_DJ_PARTIAL_MARKER_RE = re.compile(r"\{#\s*djust\s*:\s*partial\s*#\}", re.IGNORECASE)
_DEPRECATED_DATA_DJ_ID_RE = re.compile(r"""data-dj-id\s*=\s*["'][^"']*["']""")
# T015 (#1602) — pre-1.0 legacy root attributes. djust 1.0 renamed the root
# markers from `data-djust-root` / `data-djust-view` to `dj-root` / `dj-view`
# (the `data-` prefix is no longer required). The negative-lookahead
# `(?![\w-])` scopes the match to EXACTLY `data-djust-root` / `data-djust-view`
# so legitimate sibling attributes (`data-djust-embedded`, `data-djust-activity`,
# `data-djust-view-model`, `data-djust-rooted`, ...) never false-match.
_LEGACY_ROOT_ATTR_RE = re.compile(r"data-djust-(root|view)(?![\w-])")
# A090 — scanner for {% djust_markdown %} (v0.7.0). Fires info-level once
# per project when the tag is detected, confirming the Rust-side safe
# renderer is in use (raw HTML escaped, provisional-line splitter active).
_DJ_MARKDOWN_TAG_RE = re.compile(r"\{%\s*djust_markdown\b")
_LIVE_RENDER_LAZY_TRUTHY_RE = re.compile(
    r"""\blazy\s*=\s*(?:True|"[^"]+"|'[^']+'|[A-Za-z_]\w*|\{[^}]*\})"""
)
_LIVE_RENDER_LAZY_FALSY_RE = re.compile(r"""\blazy\s*=\s*(?:False|"\s*"|'\s*'|0)\b""")

# T017 (#1837) — `dj-view` / `dj-root` placed on an HTML table-section element.
# Such a view renders to SILENT GARBAGE: html5ever foster-parents the table
# elements out of the tree at render time, so
# `<tbody dj-view="…">{% for %}<tr>…{% endfor %}</tbody>` renders as
# `<html><head></head><body>text</body></html>` (all rows dropped) with NO
# error. The fix is to put the root attribute on a wrapping element (the
# `<table>` or a surrounding `<div>`).
#
# Detection: match an OPENING table-section tag that carries `dj-view=` or
# `dj-root=` ON THE SAME TAG. The `[^>]*` attribute span stops at the tag's
# own `>` (opening tags can't contain a literal `>` in attributes), so the
# attribute must be on the table-section tag itself — `<div dj-view><table>
# <tbody>…` does NOT match because the `dj-view` is on the `<div>`, not on
# any table-section opening tag. The `\b` word-boundary after the tag name
# rejects `<tablefoo` / `<trx`. Attribute order/whitespace is tolerated:
# both `<tbody dj-view=…>` and `<tbody class="x" dj-root>` match.
#
# `col` / `colgroup` are matched too — they're self-closing-ish but the same
# `<col …>` opening-tag shape applies. We list the alternatives longest-first
# (`colgroup` before `col`, `tfoot`/`thead` before `td`/`th`/`tr`) so the
# alternation prefers the longer name; the trailing `(?![\w-])` boundary
# makes ordering immaterial but the explicit order keeps the intent clear.
_DJ_TABLE_SECTION_ROOT_RE = re.compile(
    r"<(tbody|thead|tfoot|colgroup|caption|col|tr|td|th)(?![\w-])"
    r"[^>]*?\b(dj-view|dj-root)\b",
    re.IGNORECASE,
)


@register("djust")
def check_templates(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    """Regex-scan template files for common issues."""
    errors: list[CheckMessage] = []
    tpl_dirs = _get_template_dirs()
    if not tpl_dirs:
        return errors

    # A090 — project-wide counter for {% djust_markdown %} usage (v0.7.0).
    # We emit a single info-level check after the per-file loop when at
    # least one template uses the tag, so developers get explicit
    # confirmation the Rust-side safe renderer is active.
    djust_markdown_hits: list[tuple[str, int]] = []

    # T016 (#1733) — tally dj-navigate occurrences across templates. The
    # actual Warning is emitted once per project after the loop, gated on the
    # URLconf-derived route map being empty (no LiveView routes → dj-navigate
    # silently full-reloads).
    dj_navigate_hits: list[tuple[str, int]] = []

    for filepath in _iter_template_files(tpl_dirs):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        relpath = os.path.relpath(filepath)

        # T001 -- deprecated @click/@input syntax
        for match in _DEPRECATED_ATTR_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            old_attr = match.group(0).rstrip("=")
            new_attr = old_attr.replace("@", "dj-")
            errors.append(
                DjustWarning(
                    "%s:%d -- deprecated '%s' syntax." % (relpath, lineno, old_attr),
                    hint="Use '%s' instead of '%s'." % (new_attr, old_attr),
                    id="djust.T001",
                    fix_hint=(
                        "Replace `%s=` with `%s=` at line %d in `%s`."
                        % (old_attr, new_attr, lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )

        # S007 (#1821) -- `{{ <expr>.client_name|safe }}` stored-XSS.
        # An upload entry's `client_name` is the attacker-controlled original
        # filename, stored without sanitisation; `|safe` disables Django's
        # auto-escaping, so a `<script>`-bearing filename renders as live HTML.
        # WARNING (not Error): a pre-sanitised value is a legitimate, if rare,
        # use case. Honours DJUST_CONFIG['suppress_checks'] (mirrors T004/S004).
        if not _is_check_suppressed("djust.S007"):
            for match in _CLIENT_NAME_SAFE_RE.finditer(content):
                lineno = content[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- potentially unsafe rendering of client-supplied "
                        "filename. `client_name` is user-controlled — using `|safe` "
                        "bypasses XSS protection." % (relpath, lineno),
                        hint=(
                            "Use auto-escaping (remove `|safe`) or explicitly "
                            "sanitize with django.utils.html.escape() first. "
                            "Suppress with DJUST_CONFIG = {'suppress_checks': "
                            "['S007']} if the value is pre-sanitised."
                        ),
                        id="djust.S007",
                        fix_hint=(
                            "Remove the `|safe` filter from `client_name` at "
                            "line %d in `%s` (auto-escaping is the safe default)."
                            % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # T002 -- LiveView template missing dj-root (informational)
        # Since PR #297, dj-root is auto-inferred from dj-view on both
        # client (autoStampRootAttributes) and server (template.py fallback).
        # This is now an INFO-level hint rather than a warning.
        has_dj_attrs = re.search(r"dj-(click|input|change|submit|model)", content)
        has_djust_view = _DJ_VIEW_RE.search(content)
        has_djust_root = _DJ_ROOT_RE.search(content)
        if (has_dj_attrs or has_djust_view) and not has_djust_root:
            # Check if it extends a base template (in which case root is likely in the base)
            if not re.search(r"\{%\s*extends\s+", content) and not _is_check_suppressed(
                "djust.T002"
            ):
                errors.append(
                    DjustInfo(
                        "%s -- LiveView template does not have explicit 'dj-root' attribute. "
                        "This is OK — dj-root is auto-inferred from dj-view." % relpath,
                        hint=(
                            "You can optionally add dj-root for clarity: "
                            '<div dj-root dj-view="myapp.views.MyView">. '
                            "Suppress this check with DJUST_CONFIG = {'suppress_checks': ['T002']}."
                        ),
                        id="djust.T002",
                        file_path=filepath,
                    )
                )

        # T003 -- wrapper_template uses {% include %} instead of {{ liveview_content|safe }}
        # Only check files that look like wrapper templates
        if _INCLUDE_RE.search(content) and not _LIVEVIEW_CONTENT_RE.search(content):
            # Only flag if file appears to be a wrapper (has a block named "content" or similar)
            if re.search(r"\{%\s*block\s+(content|body|main)\s*%\}", content):
                # Check if any {% include %} path mentions liveview/live_view
                include_paths = re.findall(r'\{%\s*include\s+["\']([^"\']+)["\']', content)
                has_liveview_include = any(
                    re.search(r"liveview|live_view", path, re.IGNORECASE) for path in include_paths
                )
                has_noqa = "{# noqa: T003 #}" in content or "{# noqa #}" in content
                if has_liveview_include and not has_noqa:
                    errors.append(
                        DjustInfo(
                            "%s -- wrapper template may be using {%% include %%} instead of {{ liveview_content|safe }}."
                            % relpath,
                            hint="In wrapper templates, use {{ liveview_content|safe }} to render the LiveView.",
                            id="djust.T003",
                            fix_hint=(
                                "Replace `{%% include ... %%}` with "
                                "`{{ liveview_content|safe }}` in `%s`." % relpath
                            ),
                            file_path=filepath,
                        )
                    )

        # T004 -- document.addEventListener('djust:...') should be window
        # (#1809) — except for the djust: events djust itself dispatches on
        # `document` (navigate-*, hvr-*, layout-changed, ws-reconnected,
        # time-travel-*), where `document` is CORRECT. Also honor
        # suppress_checks (mirrors T002/C013).
        if not _is_check_suppressed("djust.T004"):
            for match in _DOC_DJUST_EVENT_RE.finditer(content):
                event_name = match.group(1)
                if event_name in _DOC_DISPATCHED_DJUST_EVENTS:
                    continue
                lineno = content[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- document.addEventListener for djust: event." % (relpath, lineno),
                        hint=(
                            "djust custom events (djust:push_event, djust:navigate, etc.) "
                            "are dispatched on window, not document. "
                            "Change to: window.addEventListener('djust:...')"
                        ),
                        id="djust.T004",
                        fix_hint=(
                            "Replace `document.addEventListener` with "
                            "`window.addEventListener` at line %d in `%s`." % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # T005 -- dj-view and dj-root on different elements
        if has_djust_view and has_djust_root:
            _check_view_root_same_element(content, relpath, filepath, errors)

        # T010 -- dj-click used for navigation instead of dj-patch
        _check_click_for_navigation(content, relpath, filepath, errors)

        # T011 -- unsupported Django template tags (not implemented in Rust renderer)
        _check_unsupported_tags(content, relpath, filepath, errors)

        # T012 -- template uses dj-* event directives but missing dj-view
        if (
            _DJ_EVENT_DIRECTIVES_RE.search(content)
            and not _DJ_VIEW_RE.search(content)
            # Component templates (dj-component) don't need dj-view
            and not _DJ_COMPONENT_RE.search(content)
            # #1096: partial-template opt-out marker
            and not _DJ_PARTIAL_MARKER_RE.search(content)
            # Global suppression via DJUST_CONFIG['suppress_checks']
            and not _is_check_suppressed("djust.T012")
        ):
            errors.append(
                DjustWarning(
                    "%s -- template uses dj-* event directives but has no dj-view attribute."
                    % relpath,
                    hint=(
                        'Add dj-view="yourapp.views.YourView" to the root element, '
                        "or this template won't be connected to a LiveView. "
                        "If this template is an intentional fragment included from "
                        "a parent LiveView root, add a `{# djust:partial #}` "
                        "comment to silence this check, or suppress globally "
                        "with DJUST_CONFIG = {'suppress_checks': ['T012']}."
                    ),
                    id="djust.T012",
                    file_path=filepath,
                )
            )

        # T013 -- dj-view with empty or invalid value
        for match in re.finditer(r'dj-view="([^"]*)"', content):
            value = match.group(1)
            # {{ ... }} is a valid dynamic injection pattern (base-template use case)
            if re.match(r"^\s*\{\{.*\}\}\s*$", value):
                continue
            if not value or "." not in value:
                lineno = content[: match.start()].count("\n") + 1
                errors.append(
                    DjustWarning(
                        "%s:%d -- dj-view has empty or invalid value '%s'."
                        % (relpath, lineno, value),
                        hint="dj-view should be a dotted Python path like 'myapp.views.MyView'.",
                        id="djust.T013",
                        file_path=filepath,
                        line_number=lineno,
                    )
                )

        # T014 -- deprecated data-dj-id attribute (renamed to dj-id in v1.0)
        _check_deprecated_data_dj_id(content, relpath, filepath, errors)

        # T015 -- legacy data-djust-root / data-djust-view root attributes
        _check_legacy_root_attrs(content, relpath, filepath, errors)

        # T017 -- dj-view / dj-root on a table-section element (#1837)
        _check_table_section_root(content, relpath, filepath, errors)

        # A070 / A071 -- {% dj_activity %} name validation (v0.7.0).
        # A070 (Warning): tag with no name arg — renders a no-op wrapper
        # that never ties back to the server-side activity registry.
        # A071 (Error): two tags in one template share the same name — the
        # later registration silently overwrites the earlier one at render
        # time and all events route to the last-declared state.
        #
        # #1004 — strip {% verbatim %}...{% endverbatim %} regions before
        # the regex scan so literal `{% dj_activity %}` examples on docs /
        # marketing pages (which Django renders as-is, without parsing the
        # tag) don't false-positive. `_strip_verbatim_blocks` preserves
        # line numbers by replacing the body with whitespace.
        _activity_scan_source = _strip_verbatim_blocks(content)
        _seen_activity_names = {}  # type: ignore[var-annotated]
        for match in _DJ_ACTIVITY_TAG_RE.finditer(_activity_scan_source):
            args = match.group(1)
            lineno = content[: match.start()].count("\n") + 1
            name_match = _DJ_ACTIVITY_NAME_RE.match(args)
            # A name is "present" iff ANY of the three groups (double-quoted,
            # single-quoted, bare identifier / dotted path) matched.
            name_literal = None  # str when a string-literal name was given
            if name_match is not None:
                name_literal = name_match.group(1) or name_match.group(2)
                identifier_name = name_match.group(3)
            else:
                identifier_name = None
            if name_match is None or (not name_literal and not identifier_name):
                errors.append(
                    DjustWarning(
                        "%s:%d -- {%% dj_activity %%} is missing a 'name' argument."
                        % (relpath, lineno),
                        hint=(
                            "Every {% dj_activity %} block must have a non-empty name: "
                            '{% dj_activity "my-panel" visible=expr %}. Without a name, '
                            "the server-side ActivityMixin cannot route events or track "
                            "visibility for this region."
                        ),
                        id="djust.A070",
                        fix_hint=(
                            "Add a name argument to the {%% dj_activity %%} tag at line %d in `%s`, "
                            'e.g. `{%% dj_activity "panel-name" %%}`.' % (lineno, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )
                continue
            # Only string-literal names can be statically compared for
            # duplicate detection. Variable-name tags (bare identifiers)
            # resolve at render time — we can't know if two such tags
            # will produce the same name, so we skip A071 for them to
            # avoid false positives.
            if not name_literal:
                continue
            if name_literal in _seen_activity_names:
                first_line = _seen_activity_names[name_literal]
                errors.append(
                    DjustError(
                        "%s:%d -- duplicate {%% dj_activity %%} name %r (first declared at line %d)."
                        % (relpath, lineno, name_literal, first_line),
                        hint=(
                            "Activity names must be unique within one template. "
                            "Rename one of the blocks, or split the template if the "
                            "regions should be tracked independently."
                        ),
                        id="djust.A071",
                        fix_hint=(
                            "Rename one of the two `{%% dj_activity %r %%}` blocks in `%s`."
                            % (name_literal, relpath)
                        ),
                        file_path=filepath,
                        line_number=lineno,
                    )
                )
            else:
                _seen_activity_names[name_literal] = lineno

        # A075 — `{% live_render ... sticky=True lazy=True %}` collision scan
        # (v0.9.1, #1146). The two kwargs are mutually exclusive: sticky
        # preservation requires the slot to exist at mount-frame time so
        # the WS reattach can ``replaceWith`` the stashed subtree, while
        # lazy by definition defers slot rendering until after mount.
        # ``live_tags.live_render`` already raises TemplateSyntaxError at
        # tag-eval time; A075 promotes that runtime check to startup so
        # ``manage.py check`` flags the misuse before any request hits.
        #
        # Re-uses ``_strip_verbatim_blocks`` so docs/marketing pages that
        # show the anti-pattern as a literal example don't false-positive
        # (mirrors the A070/A071 / #1004 fix).
        if not _is_check_suppressed("djust.A075"):
            _live_render_scan_source = _strip_verbatim_blocks(content)
            for match in _LIVE_RENDER_TAG_RE.finditer(_live_render_scan_source):
                args = match.group(1)
                # Reject FALSY assignments first so e.g. ``sticky=False
                # lazy=True`` is silently accepted.
                sticky_falsy = bool(_LIVE_RENDER_STICKY_FALSY_RE.search(args))
                lazy_falsy = bool(_LIVE_RENDER_LAZY_FALSY_RE.search(args))
                sticky_truthy = (
                    bool(_LIVE_RENDER_STICKY_TRUTHY_RE.search(args)) and not sticky_falsy
                )
                lazy_truthy = bool(_LIVE_RENDER_LAZY_TRUTHY_RE.search(args)) and not lazy_falsy
                if sticky_truthy and lazy_truthy:
                    lineno = content[: match.start()].count("\n") + 1
                    errors.append(
                        DjustWarning(
                            "%s:%d -- {%% live_render %%} has both sticky=True and "
                            "lazy=True — these kwargs are mutually exclusive." % (relpath, lineno),
                            hint=(
                                "Sticky preservation requires the slot to exist at "
                                "mount-frame time so the WebSocket reattach can "
                                "replaceWith the stashed subtree. Lazy defers slot "
                                "rendering until after mount, so the stash-target "
                                "doesn't exist when reattach runs. Pick one. "
                                "Suppress with DJUST_CONFIG = "
                                "{'suppress_checks': ['A075']} if you have a "
                                "deliberate reason."
                            ),
                            id="djust.A075",
                            fix_hint=(
                                "Remove either `sticky=True` or `lazy=True` from "
                                "the {%% live_render %%} tag at line %d in `%s`."
                                % (lineno, relpath)
                            ),
                            file_path=filepath,
                            line_number=lineno,
                        )
                    )

        # A090 — tally {% djust_markdown %} occurrences (v0.7.0). The
        # actual Info-level check is emitted once per project after the
        # per-file loop (below).
        for match in _DJ_MARKDOWN_TAG_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            djust_markdown_hits.append((relpath, lineno))

        # T016 — tally dj-navigate occurrences (#1733).
        for match in _DJ_NAVIGATE_RE.finditer(content):
            lineno = content[: match.start()].count("\n") + 1
            dj_navigate_hits.append((relpath, lineno))

    # T016 (#1733) — dj-navigate used but the URLconf-derived route map is
    # empty. Without LiveView routes in the route map, dj-navigate cannot
    # resolve a target view and silently falls back to a full page reload
    # instead of SPA-navigating over the WebSocket. Emitted once per project.
    if dj_navigate_hits and not _is_check_suppressed("djust.T016"):
        try:
            from djust.routing import build_route_map_from_urlconf

            derived_map = build_route_map_from_urlconf()
        except Exception:  # pragma: no cover - defensive; URLconf import errors
            # If the URLconf can't be resolved we can't make a claim about the
            # route map; stay silent rather than emit a misleading warning.
            derived_map = None
        if derived_map is not None and not derived_map:
            first_relpath, first_lineno = dj_navigate_hits[0]
            count = len(dj_navigate_hits)
            errors.append(
                DjustWarning(
                    "dj-navigate is used in %d location(s) (first: %s:%d) but no "
                    "LiveView routes were found in the URLconf, so the client "
                    "route map is empty — dj-navigate will silently full-reload "
                    "instead of navigating over the WebSocket."
                    % (count, first_relpath, first_lineno),
                    hint=(
                        "dj-navigate needs LiveView routes in the URLconf; none "
                        "were found. Ensure your views subclass djust.LiveView "
                        "and are wired into urlpatterns (e.g. "
                        "path('dashboard/', DashboardView.as_view())). "
                        "Suppress this check with "
                        "DJUST_CONFIG = {'suppress_checks': ['T016']}."
                    ),
                    id="djust.T016",
                    fix_hint=(
                        "dj-navigate needs LiveView routes in the URLconf; none "
                        "were found — ensure your views subclass djust.LiveView "
                        "and are in urlpatterns."
                    ),
                )
            )

    if djust_markdown_hits and not _is_check_suppressed("djust.A090"):
        first_relpath, first_lineno = djust_markdown_hits[0]
        count = len(djust_markdown_hits)
        errors.append(
            DjustInfo(
                "{%% djust_markdown %%} is used in %d location(s) (first: %s:%d) — "
                "djust is rendering Markdown server-side via the Rust pulldown-cmark "
                "backend with safe-by-default escaping "
                "(ENABLE_HTML never set, javascript: URLs neutralised, 10 MiB input cap)."
                % (count, first_relpath, first_lineno),
                hint=(
                    "This is informational. Suppress with "
                    "DJUST_CONFIG = {'suppress_checks': ['A090']} if you don't "
                    "want this notice. See the Streaming Markdown guide for "
                    "details: docs/website/guides/streaming-markdown.md."
                ),
                id="djust.A090",
            )
        )

    return errors


def _check_view_root_same_element(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T005: Detect when dj-view and dj-root are on different elements."""
    # Use regex to find HTML tags and check if both attributes co-occur
    # Find all tags that have either attribute
    tag_re = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
    has_combined_tag = False
    has_view_only = False
    has_root_only = False
    view_only_lineno = None
    for match in tag_re.finditer(content):
        tag = match.group(0)
        tag_has_view = "dj-view" in tag
        tag_has_root = "dj-root" in tag
        if tag_has_view and tag_has_root:
            has_combined_tag = True
            break
        if tag_has_view and not tag_has_root:
            has_view_only = True
            if view_only_lineno is None:
                view_only_lineno = content[: match.start()].count("\n") + 1
        if tag_has_root and not tag_has_view:
            has_root_only = True

    if has_view_only and has_root_only and not has_combined_tag:
        errors.append(
            DjustWarning(
                "%s -- dj-view and dj-root are on different elements." % relpath,
                hint=(
                    "dj-view and dj-root must be on the same root element. "
                    'Example: <div dj-root dj-view="myapp.views.MyView">'
                ),
                id="djust.T005",
                fix_hint=("Move dj-view and dj-root onto the same element in `%s`." % relpath),
                file_path=filepath,
                line_number=view_only_lineno,
            )
        )


# Tags still unsupported by the Rust renderer (after implementing widthratio,
# firstof, templatetag, spaceless, cycle, now in v0.3.3).
# Only opening tags are matched — end tags always accompany their openers.
#
# NOTE: {% extends %} and {% block %} are FULLY SUPPORTED since template
# inheritance was implemented (PR #272). {% regroup %} is FULLY SUPPORTED
# since the built-in assign-tag handler was added (djust.template_tags.regroup).
# Do not add either here.
_UNSUPPORTED_TAGS_RE = re.compile(r"\{%\s*(ifchanged|resetcycle|lorem|debug|filter|autoescape)\b")


def _check_unsupported_tags(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T011: Detect unsupported Django template tags in LiveView templates.

    The Rust renderer silently ignores these tags, rendering an HTML comment
    instead. This check warns developers at startup so they can use workarounds.
    """
    has_noqa = "{# noqa: T011 #}" in content or "{# noqa #}" in content
    if has_noqa:
        return

    for match in _UNSUPPORTED_TAGS_RE.finditer(content):
        tag_name = match.group(1)
        lineno = content[: match.start()].count("\n") + 1
        errors.append(
            DjustWarning(
                "%s:%d -- unsupported template tag '{%% %s %%}' will be silently "
                "ignored by Rust renderer." % (relpath, lineno, tag_name),
                hint=(
                    "Pre-compute the value in your view and pass it as a context "
                    "variable, or use a supported alternative."
                ),
                id="djust.T011",
                file_path=filepath,
                line_number=lineno,
            )
        )


def _check_click_for_navigation(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T010: Detect dj-click with navigation-style data attributes.

    Elements with both dj-click and navigation-style data attributes (data-view,
    data-tab, data-page, data-section) should use dj-patch instead for proper URL
    updates and back-button support.
    """
    tag_re = re.compile(r"<[a-zA-Z][^>]*>", re.DOTALL)
    for match in tag_re.finditer(content):
        tag = match.group(0)
        has_dj_click = "dj-click" in tag
        has_nav_data = _NAV_DATA_ATTRS.search(tag)

        if has_dj_click and has_nav_data:
            lineno = content[: match.start()].count("\n") + 1
            # Extract which data attribute was found for better messaging
            nav_match = _NAV_DATA_ATTRS.search(tag)
            nav_attr = nav_match.group(0) if nav_match else "data-*"

            errors.append(
                DjustWarning(
                    "%s:%d -- Element uses dj-click for navigation (%s) — use dj-patch for URL updates and history support."
                    % (relpath, lineno, nav_attr),
                    hint=(
                        "Navigation actions should use dj-patch instead of dj-click. "
                        "dj-patch updates the URL and enables back-button support. "
                        'Example: <button dj-patch="/view?tab=settings">Settings</button>\n'
                        "See: https://docs.djust.dev/guides/navigation"
                    ),
                    id="djust.T010",
                    fix_hint=(
                        "Replace dj-click with dj-patch at line %d in `%s` and handle "
                        "navigation parameters in handle_params() method." % (lineno, relpath)
                    ),
                    file_path=filepath,
                    line_number=lineno,
                )
            )


def _check_deprecated_data_dj_id(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T014: Detect deprecated data-dj-id attribute (renamed to dj-id in v1.0).

    data-dj-id was the internal VDOM tracking attribute in pre-1.0 versions.
    It has been renamed to dj-id to be consistent with all other dj- prefixed
    attributes (dj-view, dj-click, dj-model, etc.).
    """
    for match in _DEPRECATED_DATA_DJ_ID_RE.finditer(content):
        lineno = content[: match.start()].count("\n") + 1
        errors.append(
            DjustWarning(
                "%s:%d -- deprecated 'data-dj-id' attribute (renamed to 'dj-id' in v1.0)."
                % (relpath, lineno),
                hint=(
                    "data-dj-id has been renamed to dj-id for consistency with other dj- attributes. "
                    "If this is hand-authored HTML, replace data-dj-id with dj-id. "
                    "If it is generated by djust, upgrade to v1.0."
                ),
                id="djust.T014",
                fix_hint=(
                    "Replace 'data-dj-id=' with 'dj-id=' at line %d in `%s`." % (lineno, relpath)
                ),
                file_path=filepath,
                line_number=lineno,
            )
        )


def _check_legacy_root_attrs(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T015 (#1602): Detect the pre-1.0 legacy root attributes.

    djust 1.0 renamed the LiveView root markers from ``data-djust-root`` /
    ``data-djust-view`` to ``dj-root`` / ``dj-view`` (the ``data-`` prefix is
    no longer required). When a template still uses the old spelling, the
    generic T012 ("dj-* directives but no dj-view") doesn't recognise that a
    view IS declared — just with the deprecated name — so the path from symptom
    (the LiveView never connects over WebSocket) to fix is non-obvious. T015
    names the rename explicitly.

    SCOPE: this is a static system check only. It does NOT make the runtime
    accept the legacy attributes (that's a separate, larger change).
    """
    if _is_check_suppressed("djust.T015"):
        return
    for match in _LEGACY_ROOT_ATTR_RE.finditer(content):
        lineno = content[: match.start()].count("\n") + 1
        old_attr = match.group(0)  # e.g. "data-djust-view"
        new_attr = old_attr.replace("data-djust-", "dj-")  # -> "dj-view"
        errors.append(
            DjustWarning(
                "%s:%d -- legacy '%s' attribute detected." % (relpath, lineno, old_attr),
                hint=(
                    "djust 1.0 renamed root attributes — change 'data-djust-view' to "
                    "'dj-view' and 'data-djust-root' to 'dj-root'. The leading 'data-' "
                    "prefix is no longer required. Suppress this check with "
                    "DJUST_CONFIG = {'suppress_checks': ['T015']}."
                ),
                id="djust.T015",
                fix_hint=(
                    "Replace '%s' with '%s' at line %d in `%s`."
                    % (old_attr, new_attr, lineno, relpath)
                ),
                file_path=filepath,
                line_number=lineno,
            )
        )


def _check_table_section_root(
    content: str, relpath: str, filepath: str, errors: list[CheckMessage]
) -> None:
    """T017 (#1837): Detect dj-view / dj-root on an HTML table-section element.

    A LiveView whose root element is a table-section element (``<tbody>``,
    ``<thead>``, ``<tfoot>``, ``<tr>``, ``<td>``, ``<th>``, ``<caption>``,
    ``<col>``, ``<colgroup>``) renders to *silently broken* output:
    html5ever foster-parents the table elements out of the tree at render
    time. Empirically, a ``<tbody dj-view="...">{% for %}<tr>...{% endfor %}
    </tbody>`` template renders as ``<html><head></head><body>text</body>
    </html>`` -- all the ``<tr>`` / ``<td>`` structure is dropped, leaving
    only text, with NO error (#1837).

    The match is scoped to the SAME tag: ``[^>]*?`` stops at the
    table-section tag's own ``>``, so ``<div dj-view><table><tbody>...`` (the
    root attribute on a wrapping element) never false-matches. The
    word-boundary on the tag name rejects ``<tablefoo`` / ``<trx``.

    SCOPE: this is a static system check only. It does not change the
    renderer's foster-parenting behavior (that is html5ever's HTML5-spec
    parsing); it warns the developer at startup so the silent failure is
    caught before a request hits.
    """
    if _is_check_suppressed("djust.T017"):
        return
    for match in _DJ_TABLE_SECTION_ROOT_RE.finditer(content):
        lineno = content[: match.start()].count("\n") + 1
        tag_name = match.group(1).lower()
        attr = match.group(2).lower()
        errors.append(
            DjustWarning(
                "%s:%d -- '%s' is on a <%s> table-section element, which is "
                "foster-parented out of the tree at render time (the rendered "
                "output silently drops the table rows, with no error)."
                % (relpath, lineno, attr, tag_name),
                hint=(
                    "A table-section element (<tbody>, <thead>, <tfoot>, <tr>, "
                    "<td>, <th>, <caption>, <col>, <colgroup>) cannot be a "
                    "standalone parse root -- html5ever foster-parents it. Put "
                    "%s on a wrapping element (the <table> or a surrounding "
                    "<div>) instead. Suppress this check with "
                    "DJUST_CONFIG = {'suppress_checks': ['T017']}." % attr
                ),
                id="djust.T017",
                fix_hint=(
                    "Put `dj-view`/`dj-root` on a wrapping element (the "
                    "`<table>` or a surrounding `<div>`); a table-section "
                    "element is foster-parented at render time and cannot be "
                    "a parse root. (line %d in `%s`)" % (lineno, relpath)
                ),
                file_path=filepath,
                line_number=lineno,
            )
        )
