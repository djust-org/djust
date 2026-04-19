"""
Developer-tool template tags — Terminal, MarkdownEditor, JsonViewer,
LogViewer, FileTree.

Extracted from the monolithic djust_components.py for maintainability.
All tags register on the shared ``register`` from ``_registry``.
"""

import json as _json
import re as _re

from django import template

from ._registry import register, _resolve, _parse_kv_args, conditional_escape, mark_safe

# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------


class TerminalNode(template.Node):
    ANSI_RE = _re.compile(r"\033\[([0-9;]*)m")
    ANSI_COLORS = {
        "30": "#000",
        "31": "#e74c3c",
        "32": "#2ecc71",
        "33": "#f1c40f",
        "34": "#3498db",
        "35": "#9b59b6",
        "36": "#1abc9c",
        "37": "#ecf0f1",
        "90": "#7f8c8d",
        "91": "#ff6b6b",
        "92": "#55efc4",
        "93": "#ffeaa7",
        "94": "#74b9ff",
        "95": "#a29bfe",
        "96": "#81ecec",
        "97": "#fff",
    }

    def __init__(self, kwargs):
        self.kwargs = kwargs

    @classmethod
    def _ansi_to_html(cls, text):
        result = []
        open_spans = 0
        last_end = 0
        for m in cls.ANSI_RE.finditer(text):
            start, end = m.span()
            result.append(conditional_escape(text[last_end:start]))
            last_end = end
            codes = m.group(1).split(";")
            for code in codes:
                if code == "0" or code == "":
                    result.append("</span>" * open_spans)
                    open_spans = 0
                elif code == "1":
                    result.append('<span style="font-weight:bold">')
                    open_spans += 1
                elif code in cls.ANSI_COLORS:
                    color = cls.ANSI_COLORS[code]
                    result.append(f'<span style="color:{color}">')
                    open_spans += 1
        result.append(conditional_escape(text[last_end:]))
        result.append("</span>" * open_spans)
        return "".join(result)

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        output = kw.get("output", [])
        title = kw.get("title", "")
        stream_event = kw.get("stream_event", "")
        show_line_numbers = kw.get("show_line_numbers", False)
        wrap = kw.get("wrap", False)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        classes = ["dj-terminal"]
        if wrap:
            classes.append("dj-terminal--wrap")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(output, list):
            output = []

        title_html = ""
        if title:
            e_title = conditional_escape(str(title))
            title_html = (
                f'<div class="dj-terminal__titlebar">'
                f'<span class="dj-terminal__title">{e_title}</span>'
                f'<span class="dj-terminal__dots">'
                f'<span class="dj-terminal__dot dj-terminal__dot--red"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--yellow"></span>'
                f'<span class="dj-terminal__dot dj-terminal__dot--green"></span>'
                f"</span></div>"
            )

        lines_html = []
        for i, line in enumerate(output):
            line_text = self._ansi_to_html(str(line))
            num_html = ""
            if show_line_numbers:
                num_html = f'<span class="dj-terminal__line-num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-terminal__line">{num_html}'
                f'<span class="dj-terminal__text">{line_text}</span></div>'
            )

        stream_attr = ""
        if stream_event:
            e_stream = conditional_escape(str(stream_event))
            stream_attr = f' data-stream-event="{e_stream}"'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="Terminal"{stream_attr}>'
            f"{title_html}"
            f'<div class="dj-terminal__body">{"".join(lines_html)}</div>'
            f"</div>"
        )


@register.tag("terminal")
def do_terminal(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return TerminalNode(kwargs)


# ---------------------------------------------------------------------------
# Markdown Editor
# ---------------------------------------------------------------------------


class MarkdownEditorNode(template.Node):
    TOOLBAR_BUTTONS = [
        ("bold", "B", "**", "**"),
        ("italic", "I", "_", "_"),
        ("code", "&lt;/&gt;", "`", "`"),
        ("link", "Link", "[", "](url)"),
        ("heading", "H", "## ", ""),
    ]

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        name = kw.get("name", "content")
        value = kw.get("value", "")
        preview = kw.get("preview", True)
        toolbar = kw.get("toolbar", True)
        placeholder = kw.get("placeholder", "Write markdown...")
        rows = kw.get("rows", 12)
        disabled = kw.get("disabled", False)
        event = kw.get("event", "")
        custom_class = kw.get("class", "")

        e_name = conditional_escape(str(name))
        e_value = conditional_escape(str(value))
        e_placeholder = conditional_escape(str(placeholder))
        e_class = conditional_escape(str(custom_class))

        try:
            rows = int(rows)
        except (ValueError, TypeError):
            rows = 12

        classes = ["dj-md-editor"]
        if preview:
            classes.append("dj-md-editor--split")
        if disabled:
            classes.append("dj-md-editor--disabled")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        disabled_attr = " disabled" if disabled else ""
        event_attr = ""
        if event:
            e_event = conditional_escape(str(event))
            event_attr = f' dj-input="{e_event}"'

        toolbar_html = ""
        if toolbar:
            btns = []
            for btn_id, label, prefix, suffix in self.TOOLBAR_BUTTONS:
                btns.append(
                    f'<button type="button" class="dj-md-editor__btn" '
                    f'data-action="{btn_id}" data-prefix="{conditional_escape(prefix)}" '
                    f'data-suffix="{conditional_escape(suffix)}" '
                    f'aria-label="{btn_id.title()}">{label}</button>'
                )
            toolbar_html = f'<div class="dj-md-editor__toolbar">{"".join(btns)}</div>'

        textarea_html = (
            f'<textarea class="dj-md-editor__textarea" name="{e_name}" '
            f'placeholder="{e_placeholder}" rows="{rows}"'
            f"{disabled_attr}{event_attr}>{e_value}</textarea>"
        )

        preview_html = ""
        if preview:
            preview_html = '<div class="dj-md-editor__preview" aria-label="Preview"></div>'

        panes = f'<div class="dj-md-editor__panes">{textarea_html}{preview_html}</div>'

        return mark_safe(
            f'<div class="{class_str}" dj-hook="MarkdownEditor">{toolbar_html}{panes}</div>'
        )


@register.tag("markdown_editor")
def do_markdown_editor(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return MarkdownEditorNode(kwargs)


# ---------------------------------------------------------------------------
# JSON Viewer
# ---------------------------------------------------------------------------


class JsonViewerNode(template.Node):
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def _render_node(self, value, depth, collapsed_depth):
        collapsed = depth >= collapsed_depth

        if isinstance(value, dict):
            if not value:
                return '<span class="dj-json__bracket">{}</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for k, v in value.items():
                e_key = conditional_escape(str(k))
                items.append(
                    f'<div class="dj-json__pair">'
                    f'<span class="dj-json__key">"{e_key}"</span>'
                    f'<span class="dj-json__colon">: </span>'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}</div>"
                )
            count = f' <span class="dj-json__count">({len(value)} keys)</span>' if collapsed else ""
            return (
                f'<div class="dj-json__node dj-json__node--object{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">{{</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">}}</span></div>'
            )

        if isinstance(value, list):
            if not value:
                return '<span class="dj-json__bracket">[]</span>'
            collapse_cls = " dj-json__node--collapsed" if collapsed else ""
            toggle = (
                f'<span class="dj-json__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"false" if collapsed else "true"}">'
                f"{'&#9654;' if collapsed else '&#9660;'}</span>"
            )
            items = []
            for i, v in enumerate(value):
                items.append(
                    f'<div class="dj-json__item">'
                    f"{self._render_node(v, depth + 1, collapsed_depth)}"
                    f"{',' if i < len(value) - 1 else ''}</div>"
                )
            count = (
                f' <span class="dj-json__count">({len(value)} items)</span>' if collapsed else ""
            )
            return (
                f'<div class="dj-json__node dj-json__node--array{collapse_cls}">'
                f"{toggle}"
                f'<span class="dj-json__bracket">[</span>{count}'
                f'<div class="dj-json__children">{"".join(items)}</div>'
                f'<span class="dj-json__bracket">]</span></div>'
            )

        if isinstance(value, str):
            return f'<span class="dj-json__value dj-json__value--string">"{conditional_escape(value)}"</span>'
        if isinstance(value, bool):
            return f'<span class="dj-json__value dj-json__value--bool">{"true" if value else "false"}</span>'
        if isinstance(value, (int, float)):
            return f'<span class="dj-json__value dj-json__value--number">{value}</span>'
        if value is None:
            return '<span class="dj-json__value dj-json__value--null">null</span>'

        return f'<span class="dj-json__value">{conditional_escape(str(value))}</span>'

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        data = kw.get("data", None)
        collapsed_depth = kw.get("collapsed_depth", 2)
        root_label = kw.get("root_label", "root")
        copy_button = kw.get("copy_button", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_label = conditional_escape(str(root_label))

        try:
            collapsed_depth = int(collapsed_depth)
        except (ValueError, TypeError):
            collapsed_depth = 2

        classes = ["dj-json-viewer"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        copy_html = ""
        if copy_button:
            copy_html = (
                '<button class="dj-json-viewer__copy" type="button" '
                'aria-label="Copy JSON">Copy</button>'
            )

        try:
            raw_json = _json.dumps(data, indent=2, default=str)
        except (TypeError, ValueError):
            raw_json = str(data)

        tree_html = self._render_node(data, 0, collapsed_depth)

        return mark_safe(
            f'<div class="{class_str}" dj-hook="JsonViewer" '
            f'data-collapsed-depth="{collapsed_depth}">'
            f'<div class="dj-json-viewer__header">'
            f'<span class="dj-json-viewer__label">{e_label}</span>'
            f"{copy_html}</div>"
            f'<div class="dj-json-viewer__tree">{tree_html}</div>'
            f'<script type="application/json" class="dj-json-viewer__raw">'
            f"{conditional_escape(raw_json)}</script>"
            f"</div>"
        )


@register.tag("json_viewer")
def do_json_viewer(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return JsonViewerNode(kwargs)


# ---------------------------------------------------------------------------
# Log Viewer
# ---------------------------------------------------------------------------


class LogViewerNode(template.Node):
    LEVEL_RE = _re.compile(
        r"\b(INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|CRITICAL)\b", _re.IGNORECASE
    )

    def __init__(self, kwargs):
        self.kwargs = kwargs

    @classmethod
    def _detect_level(cls, line):
        m = cls.LEVEL_RE.search(line)
        if m:
            level = m.group(1).upper()
            if level in ("WARN", "WARNING"):
                return "warn"
            if level in ("ERROR", "FATAL", "CRITICAL"):
                return "error"
            if level in ("DEBUG", "TRACE"):
                return "debug"
            return "info"
        return ""

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        lines = kw.get("lines", [])
        stream_event = kw.get("stream_event", "")
        show_line_numbers = kw.get("show_line_numbers", True)
        auto_scroll = kw.get("auto_scroll", True)
        filter_level = kw.get("filter_level", "")
        wrap = kw.get("wrap", False)
        max_lines = kw.get("max_lines", 0)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))

        classes = ["dj-log-viewer"]
        if wrap:
            classes.append("dj-log-viewer--wrap")
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(lines, list):
            lines = []

        try:
            max_lines = int(max_lines)
        except (ValueError, TypeError):
            max_lines = 0

        display_lines = lines
        if max_lines and max_lines > 0:
            display_lines = display_lines[-max_lines:]

        lines_html = []
        for i, line in enumerate(display_lines):
            line_str = str(line)
            level = self._detect_level(line_str)
            e_line = conditional_escape(line_str)

            if filter_level and level != str(filter_level).lower():
                continue

            level_cls = f" dj-log-viewer__line--{level}" if level else ""
            num_html = ""
            if show_line_numbers:
                num_html = f'<span class="dj-log-viewer__num">{i + 1}</span>'
            lines_html.append(
                f'<div class="dj-log-viewer__line{level_cls}">'
                f'{num_html}<span class="dj-log-viewer__text">{e_line}</span></div>'
            )

        stream_attr = ""
        if stream_event:
            e_stream = conditional_escape(str(stream_event))
            stream_attr = f' data-stream-event="{e_stream}"'

        scroll_attr = ' data-auto-scroll="true"' if auto_scroll else ""

        return mark_safe(
            f'<div class="{class_str}" dj-hook="LogViewer"'
            f'{stream_attr}{scroll_attr} role="log" aria-live="polite">'
            f'<div class="dj-log-viewer__body">{"".join(lines_html)}</div>'
            f"</div>"
        )


@register.tag("log_viewer")
def do_log_viewer(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return LogViewerNode(kwargs)


# ---------------------------------------------------------------------------
# File Tree
# ---------------------------------------------------------------------------


class FileTreeNode(template.Node):
    FOLDER_ICON = "&#x1F4C1;"
    FOLDER_OPEN_ICON = "&#x1F4C2;"
    DEFAULT_FILE_ICON = "&#x1F4C4;"

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def _render_tree_node(self, node, depth, event, show_icons, selected):
        if not isinstance(node, dict):
            return ""

        name = str(node.get("name", ""))
        node_type = str(node.get("type", "file"))
        children = node.get("children", [])
        expanded = node.get("expanded", True)
        e_name = conditional_escape(name)
        e_type = conditional_escape(node_type)

        is_selected = name == selected
        selected_cls = " dj-file-tree__node--selected" if is_selected else ""
        type_cls = f" dj-file-tree__node--{e_type}"

        icon_html = ""
        if show_icons:
            if node_type == "folder":
                icon = self.FOLDER_OPEN_ICON if expanded else self.FOLDER_ICON
            else:
                icon = self.DEFAULT_FILE_ICON
            icon_html = f'<span class="dj-file-tree__icon" aria-hidden="true">{icon}</span>'

        indent_style = f' style="padding-left:{depth * 1.25}rem"'
        e_event = conditional_escape(str(event))

        if node_type == "folder" and isinstance(children, list) and children:
            expand_cls = " dj-file-tree__node--expanded" if expanded else ""
            toggle = (
                f'<span class="dj-file-tree__toggle" role="button" tabindex="0" '
                f'aria-expanded="{"true" if expanded else "false"}">'
                f"{'&#9660;' if expanded else '&#9654;'}</span>"
            )
            children_html = []
            for child in children:
                children_html.append(
                    self._render_tree_node(child, depth + 1, event, show_icons, selected)
                )
            child_display = ' style="display:none"' if not expanded else ""
            return (
                f'<div class="dj-file-tree__node{type_cls}{selected_cls}{expand_cls}"'
                f'{indent_style} data-name="{e_name}" data-type="{e_type}">'
                f"{toggle}{icon_html}"
                f'<span class="dj-file-tree__name">{e_name}</span></div>'
                f'<div class="dj-file-tree__children"{child_display}>'
                f"{''.join(children_html)}</div>"
            )

        return (
            f'<div class="dj-file-tree__node{type_cls}{selected_cls}"'
            f'{indent_style} data-name="{e_name}" data-type="{e_type}" '
            f'dj-click="{e_event}" role="treeitem" tabindex="0">'
            f'{icon_html}<span class="dj-file-tree__name">{e_name}</span></div>'
        )

    def render(self, context):
        kw = {k: _resolve(v, context) for k, v in self.kwargs.items()}
        nodes = kw.get("nodes", [])
        selected = kw.get("selected", "")
        event = kw.get("event", "select_file")
        show_icons = kw.get("show_icons", True)
        custom_class = kw.get("class", "")

        e_class = conditional_escape(str(custom_class))
        e_event = conditional_escape(str(event))
        e_selected = conditional_escape(str(selected))

        classes = ["dj-file-tree"]
        if e_class:
            classes.append(e_class)
        class_str = " ".join(classes)

        if not isinstance(nodes, list):
            nodes = []

        nodes_html = []
        for node in nodes:
            nodes_html.append(self._render_tree_node(node, 0, event, show_icons, str(selected)))

        return mark_safe(
            f'<div class="{class_str}" dj-hook="FileTree" '
            f'data-event="{e_event}" data-selected="{e_selected}" '
            f'role="tree">{"".join(nodes_html)}</div>'
        )


@register.tag("file_tree")
def do_file_tree(parser, token):
    bits = token.split_contents()[1:]
    kwargs = _parse_kv_args(bits, parser)
    return FileTreeNode(kwargs)
