"""
Component registry for every djust component (python and theme), organized by category.

Provides:
- COMPONENT_CATEGORIES: dict mapping category names to lists of component names
- PYTHON_COMPONENT_EXAMPLES: sample kwargs for Python components
- get_all_components_by_category(): all components organized by category
- get_component_category(name): returns the category for a component
- get_all_components_with_metadata(): flat list with name, display_name, category, component_type
- render_python_component_example(component_name, kwargs_dict): renders a djust-component
"""

import importlib
import inspect
import logging
import re
from typing import Any

from django.utils.html import escape
from django.utils.safestring import mark_safe

from djust._log_utils import sanitize_for_log

logger = logging.getLogger(__name__)


def _to_class_name(component_name: str) -> str:
    """Convert snake_case component name to CamelCase class name."""
    return "".join(word.title() for word in component_name.split("_"))


COMPONENT_CATEGORIES: dict[str, list[str]] = {
    "Core UI": [
        "accordion",
        "alert",
        "avatar",
        "badge",
        "button",
        "callout",
        "card",
        "checkbox",
        "code_snippet",
        "collapsible",
        "dropdown",
        "dropdown_menu",
        "input",
        "kbd",
        "meter",
        "modal",
        "pagination",
        "popover",
        "progress",
        "radio",
        "rating",
        "select",
        "skeleton",
        "spinner",
        "switch",
        "tabs",
        "tag",
        "textarea",
        "toast",
        "toggle_group",
        "tooltip",
        "segmented_progress",
    ],
    "Navigation": [
        "breadcrumb",
        "breadcrumb_dropdown",
        "nav",
        "nav_group",
        "nav_item",
        "nav_menu",
        "scroll_spy",
        "scroll_to_top",
        "sidebar",
        "sidebar_nav",
        "stepper",
        "wizard",
        "sticky_header",
        "table_of_contents",
        "toolbar",
    ],
    "Forms": [
        "color_picker",
        "combobox",
        "cron_input",
        "currency_input",
        "date_picker",
        "dependent_select",
        "fieldset",
        "form_array",
        "form_group",
        "form_validation",
        "input_group",
        "mentions_input",
        "multi_select",
        "number_stepper",
        "otp_input",
        "rich_select",
        "signature_pad",
        "tag_input",
        "time_picker",
        "voice_input",
    ],
    "Data Display": [
        "activity_feed",
        "audit_log",
        "code_block",
        "terminal",
        "comparison_table",
        "data_card_grid",
        "data_grid",
        "data_table",
        "description_list",
        "diff_viewer",
        "expandable_text",
        "inline_edit",
        "json_viewer",
        "log_viewer",
        "markdown",
        "pivot_table",
        "source_citation",
        "table",
        "timeline",
        "tree_view",
        "truncated_list",
        "virtual_list",
    ],
    "Charts": [
        "bar_chart",
        "calendar_heatmap",
        "calendar_view",
        "gantt_chart",
        "gauge",
        "heatmap",
        "line_chart",
        "pie_chart",
        "sparkline",
        "treemap",
        "stat_card",
    ],
    "Media": [
        "aspect_ratio",
        "avatar_group",
        "carousel",
        "file_dropzone",
        "file_tree",
        "image_cropper",
        "image_lightbox",
        "image_upload_preview",
        "map_picker",
        "org_chart",
        "responsive_image",
    ],
    "Feedback": [
        "agent_step",
        "approval_gate",
        "connection_status",
        "content_loader",
        "empty_state",
        "error_boundary",
        "error_page",
        "live_indicator",
        "loading_overlay",
        "notification_badge",
        "notification_center",
        "notification_popover",
        "page_alert",
        "server_event_toast",
        "status_dot",
        "status_indicator",
        "thinking_indicator",
    ],
    "Layout": [
        "app_shell",
        "bottom_sheet",
        "dashboard_grid",
        "fab",
        "hover_card",
        "masonry_grid",
        "page_header",
        "resizable_panel",
        "ribbon",
        "scroll_area",
        "sheet",
        "split_pane",
    ],
    "Advanced": [
        "animated_number",
        "announcement_bar",
        "chat_bubble",
        "collab_selection",
        "command_palette",
        "context_menu",
        "conversation_thread",
        "cookie_consent",
        "copy_button",
        "copyable_text",
        "countdown",
        "cursors_overlay",
        "export_dialog",
        "feedback_widget",
        "filter_bar",
        "icon",
        "infinite_scroll",
        "import_wizard",
        "kanban_board",
        "live_counter",
        "markdown_editor",
        "markdown_textarea",
        "model_selector",
        "multimodal_input",
        "presence_avatars",
        "progress_circle",
        "prompt_editor",
        "qr_code",
        "reactions",
        "relative_time",
        "rich_text_editor",
        "skeleton_factory",
        "sortable_grid",
        "sortable_list",
        "split_button",
        "streaming_text",
        "theme_toggle",
        "thinking_indicator",
        "token_counter",
        "tour",
        "voice_input",
    ],
}

# Build reverse lookup: component_name -> category
_COMPONENT_TO_CATEGORY: dict[str, str] = {}
for _cat, _names in COMPONENT_CATEGORIES.items():
    for _name in _names:
        _COMPONENT_TO_CATEGORY[_name] = _cat


PYTHON_COMPONENT_EXAMPLES: dict[str, list[dict]] = {
    "accordion": [
        {
            "items": [
                {"id": "1", "title": "What is djust?", "content": "A reactive Django framework."},
                {
                    "id": "2",
                    "title": "How does it work?",
                    "content": "Server-side rendering with live updates.",
                },
            ],
            "active": "1",
        },
    ],
    "table_of_contents": [
        {
            "items": [
                {"id": "intro", "label": "Introduction"},
                {"id": "usage", "label": "Usage"},
                {"id": "props", "label": "Parameters"},
            ],
            "active": "usage",
        },
    ],
    "dropdown_menu": [
        {
            "label": "Actions",
            "items": [
                {"label": "Edit", "event": "edit_item"},
                {"label": "Duplicate", "event": "duplicate_item"},
                {"divider": True},
                {"label": "Delete", "event": "delete_item", "danger": True},
            ],
            "open": True,
        },
    ],
    "infinite_scroll": [
        {"load_event": "load_more"},
        {"load_event": "load_more", "loading": True},
    ],
    "terminal": [
        {
            "output": ["$ djust new myapp", "Creating project…", "Done in 1.2s"],
            "title": "shell",
            "show_line_numbers": True,
        },
    ],
    "wizard": [
        {
            "steps": [
                {"id": "account", "label": "Account"},
                {"id": "profile", "label": "Profile"},
                {"id": "review", "label": "Review"},
            ],
            "active": "profile",
        },
    ],
    "spinner": [
        {"size": "sm"},
        {"size": "md"},
        {"size": "lg"},
    ],
    "stat_card": [
        {"label": "Revenue", "value": "$12,345", "trend": "up", "trend_value": "+12%"},
        {"label": "Users", "value": "1,234", "trend": "down", "trend_value": "-3%"},
        {"label": "Uptime", "value": "99.9%", "trend": "flat"},
    ],
    "switch": [
        # `action` is what the input's `dj-change` is built from, and a switch
        # with no `dj-change` cannot move: its slider is drawn from the
        # server-rendered `.dj-switch-checked`, so a browser-side toggle of the
        # hidden checkbox changes nothing anyone can see.
        {
            "name": "notifications",
            "label": "Enable notifications",
            "checked": True,
            "action": "toggle_switch",
        },
        {
            "name": "dark_mode",
            "label": "Dark mode",
            "checked": False,
            "action": "toggle_switch",
        },
    ],
    "kbd": [
        {"keys": ["⌘", "K"]},
        {"keys": ["Ctrl", "S"]},
    ],
    "tag": [
        {"label": "Python"},
        {"label": "Django", "variant": "info"},
        {"label": "New", "variant": "success"},
    ],
    # `max_stars`, not `max` — and `name` is not a parameter at all. Both old
    # examples passed the wrong keys, so the star count and the value fell back
    # to defaults while the preview still looked plausible.
    #
    # One example rather than two: every example on a catalogue page is
    # rendered against the *same* live state, so a second, deliberately
    # different rating (`readonly`, value 2) would silently mirror whatever the
    # first one was clicked to. `readonly` is documented in the PARAMETERS
    # table below rather than demonstrated at its own rating's expense.
    "rating": [
        {"value": 4, "max_stars": 5},
    ],
    # `Meter` renders `segments` against a `total` — it has no `value`/`min`/`max`
    # at all. The old example passed those three, every one of them landed in
    # `**kwargs` and went nowhere, and the preview showed an empty bar under a
    # label: a component that looked broken because its example used an API it
    # has never had.
    "meter": [
        {"segments": [{"value": 70, "label": "Used"}], "total": 100, "label": "Storage"},
        {"segments": [{"value": 30, "label": "Used"}], "total": 100, "label": "Memory"},
    ],
    "callout": [
        {"content": "This is an important notice.", "variant": "info", "title": "Info"},
        {"content": "Warning: action is irreversible.", "variant": "warning", "title": "Warning"},
    ],
    "collapsible": [
        {"trigger": "Show details", "content": "Hidden content shown when expanded."},
        {
            "trigger": "Already open",
            "content": "This one starts expanded.",
            "is_open": True,
        },
    ],
    "toggle_group": [
        {
            "name": "view",
            "options": [{"value": "list", "label": "List"}, {"value": "grid", "label": "Grid"}],
            "value": "list",
        },
    ],
    "segmented_progress": [
        {"steps": ["Cart", "Address", "Payment", "Done"], "current": 3, "size": "md"}
    ],
    "empty_state": [
        {
            "title": "No results found",
            "description": "Try adjusting your search or filters.",
            "icon": "🔍",
        },
    ],
    "error_page": [
        {
            "code": 404,
            "title": "Not Found",
            "message": "The page you are looking for does not exist.",
        },
    ],
    "page_alert": [
        {"message": "Your trial expires in 3 days.", "type": "warning", "dismissible": True},
    ],
    "status_dot": [{"status": "running", "variant": "success", "size": "md"}],
    "status_indicator": [
        {"status": "running", "label": "Service running"},
        {"status": "stopped", "label": "Service stopped"},
    ],
    "connection_status": [
        {},
        {"reconnecting_text": "Connection lost...", "connected_text": "Back online!"},
    ],
    "live_indicator": [{"user": {"name": "Ada"}, "field": "Title", "action": "typing"}],
    "thinking_indicator": [
        {"label": "Thinking..."},
    ],
    "copy_button": [
        {"text": "npm install djust-theming", "label": "Copy"},
    ],
    "copyable_text": [
        {"text": "pip install djust-theming"},
        {"text": "sk-abc123xyz", "copied_label": "Key copied!"},
    ],
    "icon": [
        {"name": "check", "size": "md"},
        {"name": "x", "size": "sm"},
        {"name": "search", "size": "lg"},
    ],
    "qr_code": [
        {"data": "https://djust.org", "size": "md"},
    ],
    "countdown": [
        {"target": "2026-12-31"},
        {"target": "2026-12-31", "labels": {"days": "sleeps", "seconds": "secs"}},
    ],
    # `auto_update` left at its default: without it the component renders the
    # raw ISO string as its text content rather than a relative label, because
    # the formatting is the client's job. Disabling it made the preview look
    # like a broken component.
    "relative_time": [{"datetime": "2026-09-17T09:00:00Z"}],
    "animated_number": [
        {"value": 1234, "duration": 1000},
    ],
    "live_counter": [
        {"value": 42, "label": "online"},
    ],
    "token_counter": [
        {"current": 1500, "max": 4096, "label": "tokens"},
        {"current": 3800, "max": 4096, "label": "tokens"},
    ],
    "progress_circle": [
        {"value": 75},
        {"value": 33, "color": "warning"},
    ],
    "code_snippet": [
        {"code": "pip install djust", "language": "bash"},
    ],
    "code_block": [
        {"code": 'print("Hello, world!")', "language": "python", "filename": "example.py"},
    ],
    "markdown": [
        {"text": "# Hello\n\nThis is **markdown** rendered inline."},
    ],
    "json_viewer": [
        {"data": {"name": "djust", "version": "0.4.0", "stable": True}},
    ],
    "description_list": [
        {
            "items": [
                {"term": "Framework", "description": "Django"},
                {"term": "Language", "description": "Python"},
            ]
        },
    ],
    "timeline": [
        {
            "items": [
                {"title": "Project started", "date": "Jan 2025"},
                {"title": "Beta release", "date": "Jun 2025"},
                {"title": "v1.0 released", "date": "Jan 2026"},
            ]
        },
    ],
    "activity_feed": [
        {
            "events": [
                {"user": "Ada", "action": "commented on", "target": "PR #412", "time": "2m ago"},
                {"user": "Grace", "action": "merged", "target": "main", "time": "1h ago"},
            ]
        }
    ],
    "notification_badge": [
        {"count": 5},
        {"count": 99},
        {"count": 0},
    ],
    "avatar_group": [
        {
            "users": [{"name": "Ada Lovelace"}, {"name": "Grace Hopper"}, {"name": "Alan Turing"}],
            "max_display": 2,
        }
    ],
    "ribbon": [{"text": "New", "variant": "primary", "position": "top-right"}],
    "fab": [
        {"label": "Create", "icon": "+"},
    ],
    "page_header": [
        {"title": "Dashboard", "subtitle": "Overview of your workspace"},
    ],
    "split_button": [
        {"label": "Save", "options": [{"label": "Save and continue"}, {"label": "Save as draft"}]},
    ],
    "theme_toggle": [
        {"current": "system"},
        {"current": "dark"},
    ],
    "stepper": [
        {"steps": [{"label": "Account"}, {"label": "Details"}, {"label": "Review"}], "active": 1},
    ],
    "toolbar": [
        {
            "content": '<button type="button">Bold</button><button type="button">Italic</button>',
            "align": "left",
        }
    ],
    "announcement_bar": [
        {"content": "Scheduled maintenance on Sunday.", "variant": "warning", "dismissible": True}
    ],
    "cookie_consent": [
        {"message": "We use cookies to improve your experience."},
    ],
    "feedback_widget": [{"mode": "thumbs", "value": "up"}],
    "streaming_text": [
        {"text": "Generating response..."},
        {"text": "Streaming with a markdown cursor.", "markdown": True},
    ],
}


def get_component_category(component_name: str) -> str:
    """Return the category for a given component name, or 'Other' if not found."""
    return _COMPONENT_TO_CATEGORY.get(component_name, "Other")


def get_all_components_by_category() -> dict[str, list[str]]:
    """Return every registered component organized by category."""
    return dict(COMPONENT_CATEGORIES)


def get_all_components_with_metadata() -> list[dict]:
    """Return a flat list of all components with metadata.

    Each dict has: name, display_name, category, component_type, example_count.
    component_type is 'template' for the 24 contracted components, 'python' for the rest.
    """
    from djust.theming.contracts import COMPONENT_CONTRACTS

    result = []
    seen = set()

    for category, names in COMPONENT_CATEGORIES.items():
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            if name in COMPONENT_CONTRACTS:
                component_type = "template"
            else:
                component_type = "python"
            examples = PYTHON_COMPONENT_EXAMPLES.get(name, [])
            result.append(
                {
                    "name": name,
                    "display_name": name.replace("_", " ").title(),
                    "category": category,
                    "component_type": component_type,
                    "example_count": len(examples),
                    # Compat fields used by sidebar
                    "required_count": 0,
                    "optional_count": 0,
                    "slot_count": 0,
                    "a11y_count": 0,
                }
            )

    return result


def _load_component_class(component_name: str) -> tuple[Any, str]:
    """``(cls, class_name)`` for a python component, or ``(None, "")``.

    The class is NOT always the snake→CamelCase of the module name: ``qr_code``
    defines ``QRCode``, and guessing ``QrCode`` silently produced an empty
    preview, an empty signature table and a broken import line. Reading the
    module is what keeps the catalogue's USAGE import, its PARAMETERS table and
    its rendered example agreeing with each other.

    Never raises — callers decide what a missing class means.
    """
    module_path = f"djust.components.components.{component_name}"
    try:
        module = importlib.import_module(module_path)
    except Exception:  # noqa: BLE001 — an unimportable component is a finding, not a crash
        logger.debug("component module unavailable: %s", sanitize_for_log(module_path))
        return None, ""

    _path, names = get_python_component_import(component_name)
    for class_name in names:
        cls = getattr(module, class_name, None)
        if cls is not None:
            return cls, class_name
    logger.debug("no component class found in %s", sanitize_for_log(module_path))
    return None, ""


def render_python_component_example(component_name: str, kwargs_dict: dict) -> str:
    """Import and render a djust-component by name.

    Dynamically imports from djust.components.components.<name>, instantiates
    the class with kwargs_dict, and calls .render().

    Returns rendered HTML string, or an error message string if import/render fails.
    """
    cls, _class_name = _load_component_class(component_name)
    if cls is None:
        # Visible, not silent. Returning "" left the example slot blank with no
        # hint why — which is how three components shipped with a preview that
        # rendered nothing at all.
        return (
            f'<div class="dj-component-preview-error" role="status">'
            f"No component class found for <code>{escape(component_name)}</code>."
            f"</div>"
        )
    try:
        instance = cls(**kwargs_dict)
        # cls comes from a dynamic getattr (Any), so .render() is Any; coerce
        # to ``str`` at the boundary (render() returns the rendered HTML str).
        return str(instance.render())
    except ImportError:
        logger.debug(
            "djust_components not available for component: %s", sanitize_for_log(component_name)
        )
        return ""
    except Exception as exc:
        # Same reasoning as the missing-class branch above: a blank preview
        # with a DEBUG-only log is indistinguishable from a component that
        # legitimately renders nothing.
        logger.warning(
            "Could not render component %s: %s",
            sanitize_for_log(component_name),
            sanitize_for_log(str(exc)),
        )
        return (
            f'<div class="dj-component-preview-error" role="status">'
            f"<code>{escape(component_name)}</code> failed to render: "
            f"<code>{escape(type(exc).__name__)}: {escape(str(exc))}</code></div>"
        )


#: What ``get_python_component_signature`` writes for a parameter with no
#: default. Read rather than re-derived, so a legitimate ``None`` default is
#: never mistaken for a required parameter.
_NO_DEFAULT = "—"

#: What a component writes for "the caller did not pass this", when its
#: default is a private sentinel rather than a value. The repr of such an
#: object carries its memory address, which is different on every run — a
#: documentation page generated from it is not reproducible, and a props
#: table showing ``<object object at 0x102743770>`` tells a reader nothing.
NOT_SUPPLIED = "NOT_SUPPLIED"


def _is_required(param: dict) -> bool:
    """A caller must pass it: no default, and not ``*args`` / ``**kwargs``.

    A VAR_KEYWORD has no default either, which marked every component's
    ``**kwargs`` "required" — so both the catalogue and the generated
    reference told readers they had to pass something called ``kwargs``.
    """
    return param.get("default") == _NO_DEFAULT and param.get("kind") not in (
        "VAR_POSITIONAL",
        "VAR_KEYWORD",
    )


def _default_source(default: Any) -> str:
    """A parameter's default, as source a reader could type."""
    if default is inspect.Parameter.empty:
        return _NO_DEFAULT
    text = repr(default)
    return NOT_SUPPLIED if " object at 0x" in text else text


def get_python_component_signature(component_name: str) -> list[dict] | None:
    """Return parameter info for a Python component's __init__ method.

    Returns a list of dicts with keys: name, kind, default, annotation.
    Returns None if the component cannot be imported.
    """
    cls, _class_name = _load_component_class(component_name)
    if cls is None:
        return None
    try:
        sig = inspect.signature(cls.__init__)
        params = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            params.append(
                {
                    "name": param_name,
                    "kind": str(param.kind.name),
                    "default": _default_source(param.default),
                    "annotation": (
                        str(param.annotation)
                        if param.annotation is not inspect.Parameter.empty
                        else ""
                    ),
                }
            )
        return params
    except Exception:
        return None


def get_python_component_import(component_name: str) -> tuple[str, list[str]]:
    """``(module_path, class_names)`` to document for a python component.

    The class is NOT always the snake→CamelCase of the module name, and three
    components prove it: ``qr_code`` defines ``QRCode`` (not ``QrCode``),
    ``form_validation`` defines two components (``FormErrors``, ``FieldError``),
    and ``server_event_toast`` defines only a mixin — no component class at all.

    Guessing produced a USAGE snippet that did not import. This reads the module
    and reports what is actually there: the single component class when there is
    one, every component class when there are several, and an empty list when
    the module has none (the caller then documents no import rather than a wrong
    one).
    """
    import inspect

    from djust.components.base import Component

    module_path = f"djust.components.components.{component_name}"
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return module_path, []

    owns = [
        name
        for name, obj in vars(module).items()
        if inspect.isclass(obj) and obj.__module__ == module.__name__ and issubclass(obj, Component)
    ]
    owns.sort()

    guessed = _to_class_name(component_name)
    if guessed in owns:
        return module_path, [guessed]
    return module_path, owns


# --- Derived examples -------------------------------------------------------
#
# Examples generated from each component's own __init__ signature: required
# parameters get a typed placeholder, optional ones keep their default. Each
# one is asserted to render non-empty markup by
# tests/test_gallery_component_sweep.py, so a component that cannot render with
# its own signature is a failing test rather than a blank preview.
#
# Components absent here render nothing until opened (a closed `modal`, a
# `bottom_sheet`, a `tour`), which is correct — a blank preview would be
# misleading, so their pages keep the explicit "no examples" message.
PYTHON_COMPONENT_EXAMPLES.update(
    {
        "agent_step": [{}],
        "alert": [{"message": "Example"}],
        "app_shell": [{"content": "<h1>Dashboard</h1><p>Main content area.</p>"}],
        "approval_gate": [{}],
        # A box with nothing inside it had no width in the preview's flex row,
        # so the ratio drew as nothing at all.
        "aspect_ratio": [
            {
                "content": (
                    '<div style="width:320px;height:100%;display:grid;place-items:center;'
                    'background:hsl(var(--muted));border-radius:var(--radius-md,0.375rem)">'
                    "16 / 9</div>"
                ),
                "ratio": "16/9",
            }
        ],
        "audit_log": [{}],
        "avatar": [{"initials": "JD", "alt": "Jane Doe", "size": "md"}],
        "badge": [{"label": "Example"}],
        "bar_chart": [
            {
                "data": [12, 19, 8, 15],
                "labels": ["Q1", "Q2", "Q3", "Q4"],
                "title": "Quarterly revenue",
            }
        ],
        "breadcrumb": [
            {
                "items": [
                    {"label": "Home", "url": "#"},
                    {"label": "Library", "url": "#library"},
                    {"label": "Data", "active": True},
                ]
            }
        ],
        "breadcrumb_dropdown": [
            {
                "items": [
                    {"label": "Home", "url": "#"},
                    {"label": "Library", "url": "#library"},
                    {"label": "Data", "url": "#library-data"},
                    {"label": "Tables", "url": "#library-data-tables"},
                    {"label": "Current page"},
                ],
                "max_visible": 3,
            }
        ],
        "button": [{"label": "Example"}],
        # Without data every cell sits at the empty level, so the preview was
        # an empty grid. One month of commit-shaped activity gives the scale
        # something to colour.
        "calendar_heatmap": [
            {
                "data": {
                    "2026-01-02": 1,
                    "2026-01-03": 3,
                    "2026-01-05": 5,
                    "2026-01-06": 8,
                    "2026-01-07": 2,
                    "2026-01-09": 4,
                    "2026-01-10": 11,
                    "2026-01-11": 6,
                    "2026-01-12": 1,
                    "2026-01-14": 2,
                    "2026-01-15": 7,
                    "2026-01-16": 9,
                    "2026-01-17": 3,
                    "2026-01-19": 1,
                    "2026-01-20": 5,
                    "2026-01-21": 12,
                    "2026-01-22": 8,
                    "2026-01-23": 4,
                    "2026-01-25": 2,
                    "2026-01-26": 6,
                    "2026-01-27": 10,
                    "2026-01-28": 3,
                    "2026-01-29": 1,
                    "2026-01-31": 7,
                },
                "year": 2026,
                "title": "Contributions",
            }
        ],
        "calendar_view": [{}],
        "card": [{"header": "Card title", "content": "Card body text.", "footer": "Card footer"}],
        "carousel": [
            {
                "images": [
                    {
                        "src": "https://picsum.photos/seed/one/600/300",
                        "alt": "First slide",
                        "caption": "Slide one",
                    },
                    {
                        "src": "https://picsum.photos/seed/two/600/300",
                        "alt": "Second slide",
                        "caption": "Slide two",
                    },
                ],
                "active": 0,
            }
        ],
        "chat_bubble": [{}],
        "collab_selection": [{}],
        "color_picker": [
            {
                "name": "accent",
                "event": "set_color",
                "value": "#3b82f6",
                "label": "Accent",
                "swatches": ["#3b82f6", "#10b981", "#f59e0b", "#ef4444"],
            }
        ],
        "combobox": [
            {
                "name": "language",
                "event": "set_language",
                "label": "Language",
                "value": "python",
                "options": [
                    {"value": "python", "label": "Python"},
                    {"value": "rust", "label": "Rust"},
                    {"value": "go", "label": "Go"},
                ],
            }
        ],
        # Overlays render OPEN: the preview box contains a position-fixed
        # element, so an open overlay stays inside it. Closing hands the preview
        # a "Show again" (see `live_views._hide`).
        "command_palette": [
            {
                "is_open": True,
                "content": (
                    '<div class="palette-item">New component</div>'
                    '<div class="palette-item">Open settings</div>'
                ),
            }
        ],
        "comparison_table": [
            {
                "plans": [
                    {"name": "Free", "price": "$0"},
                    {"name": "Pro", "price": "$20", "highlighted": True},
                    {"name": "Team", "price": "$60"},
                ],
                "features": [
                    {"name": "Projects", "values": ["1", "Unlimited", "Unlimited"]},
                    {"name": "Support", "values": ["Community", "Email", "Priority"]},
                ],
            }
        ],
        "content_loader": [{"loaded": False, "placeholder": "<p>Loading…</p>"}],
        "context_menu": [{}],
        "conversation_thread": [
            {
                "messages": [
                    {
                        "sender": "user",
                        "name": "You",
                        "text": "Summarise this thread.",
                        "time": "10:04",
                    },
                    {
                        "sender": "assistant",
                        "name": "Assistant",
                        "text": "Three points so far.",
                        "time": "10:04",
                    },
                ]
            }
        ],
        "cron_input": [{}],
        "currency_input": [{}],
        "cursors_overlay": [{}],
        "dashboard_grid": [
            {
                "panels": [
                    {
                        "id": "p1",
                        "title": "Revenue",
                        "col": 1,
                        "row": 1,
                        "width": 2,
                        "height": 1,
                        "content": "<p>$12,345</p>",
                    },
                    {
                        "id": "p2",
                        "title": "Users",
                        "col": 3,
                        "row": 1,
                        "width": 2,
                        "height": 1,
                        "content": "<p>1,234</p>",
                    },
                ]
            }
        ],
        "data_card_grid": [
            {
                "columns": 3,
                "items": [
                    {"title": "Alpha", "description": "First example project.", "category": "Web"},
                    {"title": "Beta", "description": "Second example project.", "category": "Web"},
                    {"title": "Gamma", "description": "Third example project.", "category": "CLI"},
                ],
            }
        ],
        "data_grid": [
            {
                "columns": [
                    {"key": "name", "label": "Name"},
                    {"key": "owner", "label": "Owner"},
                    {"key": "status", "label": "Status"},
                ],
                "rows": [
                    {"name": "Alpha", "owner": "Ada", "status": "Active"},
                    {"name": "Beta", "owner": "Grace", "status": "Paused"},
                ],
            }
        ],
        "data_table": [
            {
                "columns": [
                    {"key": "name", "label": "Name"},
                    {"key": "status", "label": "Status"},
                    {"key": "updated", "label": "Updated"},
                ],
                "rows": [
                    {"name": "Alpha", "status": "Active", "updated": "2m ago"},
                    {"name": "Beta", "status": "Paused", "updated": "1h ago"},
                    {"name": "Gamma", "status": "Active", "updated": "yesterday"},
                ],
            }
        ],
        "date_picker": [{}],
        "dependent_select": [{}],
        "diff_viewer": [{}],
        # Was `[{}]` — every argument defaulted, so the preview rendered a button
        # reading "Menu" and nothing else. `Dropdown` renders its menu only when
        # `is_open`, so a closed preview with no `content` is a lone button with
        # nothing to open: the descriptor worked perfectly and there was nothing to
        # show. The menu items are ordinary markup because the component takes
        # `content` as a string rather than a list.
        "dropdown": [
            {
                "label": "Actions",
                "content": (
                    '<a class="dropdown-item" role="menuitem" href="#">Edit</a>'
                    '<a class="dropdown-item" role="menuitem" href="#">Duplicate</a>'
                    '<a class="dropdown-item" role="menuitem" href="#">Archive</a>'
                ),
            }
        ],
        # The healthy state first, then the fallback a failure shows; its Retry
        # clears the error, as a host's handler would after reloading.
        # `content` is escaped text, not HTML (unlike `loading_overlay`'s).
        "error_boundary": [
            {"content": "The chart rendered normally."},
            {
                "content": "The chart rendered normally.",
                "error": "TimeoutError",
                "fallback": "The chart could not load.",
                "retry_event": "retry_load",
            },
        ],
        "expandable_text": [{}],
        "fieldset": [
            {"legend": "Shipping address", "content": '<label>Street <input type="text"></label>'}
        ],
        "file_dropzone": [{}],
        "file_tree": [
            {
                "nodes": [
                    {
                        "name": "src",
                        "type": "folder",
                        "children": [
                            {"name": "app.py", "type": "file"},
                            {"name": "util.py", "type": "file"},
                        ],
                    },
                    {"name": "README.md", "type": "file"},
                ],
                "selected": "src/app.py",
            }
        ],
        "filter_bar": [{"content": "<span>Status: Active</span>", "active_count": 1}],
        "form_array": [{}],
        "form_group": [
            {
                "label": "Email",
                "content": '<input type="email" placeholder="you@example.com">',
                "helper": "We never share it.",
            }
        ],
        "gantt_chart": [
            {
                "title": "Sprint plan",
                "tasks": [
                    {"name": "Design", "start": 0, "duration": 3},
                    {"name": "Build", "start": 2, "duration": 5},
                    {"name": "Ship", "start": 6, "duration": 2},
                ],
            }
        ],
        # A gauge at 0 with no label shows nothing about what a gauge is.
        "gauge": [{"value": 72, "max_value": 100, "label": "Disk used"}],
        "heatmap": [
            {
                "data": [[1, 4, 2], [3, 0, 5], [2, 6, 1]],
                "x_labels": ["Mon", "Tue", "Wed"],
                "y_labels": ["Week 1", "Week 2", "Week 3"],
            }
        ],
        "hover_card": [
            {"trigger": "Hover me", "content": "<p>Shown on hover.</p>", "position": "top"}
        ],
        "image_cropper": [{}],
        "image_upload_preview": [{}],
        "import_wizard": [{}],
        "inline_edit": [{"name": "title", "value": "Click to edit", "editing": False}],
        "input_group": [
            {
                "content": '<span>$</span><input type="number" value="20"><span>.00</span>',
                "size": "md",
            }
        ],
        "kanban_board": [
            {
                "columns": [
                    {
                        "id": "todo",
                        "title": "To do",
                        "cards": [{"id": "c1", "title": "Write docs"}],
                    },
                    {
                        "id": "doing",
                        "title": "In progress",
                        "cards": [{"id": "c2", "title": "Fix crash"}],
                    },
                    {"id": "done", "title": "Done", "cards": []},
                ]
            }
        ],
        "line_chart": [
            {
                "series": [
                    {"name": "This year", "data": [4, 8, 6, 11]},
                    {"name": "Last year", "data": [3, 5, 7, 8]},
                ],
                "labels": ["Q1", "Q2", "Q3", "Q4"],
            }
        ],
        # The overlay is drawn over `content` and only while `active`; with
        # neither supplied the preview was an empty wrapper, which reads as a
        # broken component rather than as a component with nothing to show.
        "loading_overlay": [
            {
                "content": (
                    '<p style="margin:0 0 0.5rem">This card stays visible '
                    "underneath the overlay.</p>"
                    '<button type="button" dj-click="toggle_loading">'
                    "Toggle the overlay</button>"
                ),
                "text": "Loading…",
            },
            {
                "content": '<p style="margin:0">Already loading on first paint.</p>',
                "active": True,
                "text": "Uploading…",
            },
        ],
        "log_viewer": [
            {
                "lines": [
                    "[info] server started",
                    "[info] connected to database",
                    "[warn] slow query: 1.2s",
                ]
            }
        ],
        "map_picker": [{"lat": 51.5074, "lng": -0.1278}],
        "markdown_editor": [{}],
        "markdown_textarea": [{}],
        "masonry_grid": [
            {
                "items": [
                    {"content": "<p>First card</p>", "height": "120px"},
                    {"content": "<p>Second card</p>", "height": "90px"},
                    {"content": "<p>Third card</p>", "height": "140px"},
                ]
            }
        ],
        "mentions_input": [
            {
                "name": "comment",
                "users": [
                    {"id": "u1", "name": "Ada Lovelace"},
                    {"id": "u2", "name": "Grace Hopper"},
                ],
                "placeholder": "Mention someone…",
            }
        ],
        "model_selector": [
            {
                "name": "model",
                "label": "Model",
                "options": [
                    {
                        "value": "claude-opus-5",
                        "label": "Opus 5",
                        "description": "Most capable",
                        "context_window": "200k",
                        "tier": "premium",
                    },
                    {
                        "value": "claude-sonnet-5",
                        "label": "Sonnet 5",
                        "description": "Balanced",
                        "context_window": "200k",
                        "tier": "standard",
                    },
                    {
                        "value": "claude-haiku-4-5",
                        "label": "Haiku 4.5",
                        "description": "Fast and cost-effective",
                        "context_window": "200k",
                        "tier": "free",
                    },
                ],
                "value": "claude-sonnet-5",
                "event": "select_model",
            },
        ],
        "multi_select": [
            {
                "name": "frameworks",
                "label": "Frameworks",
                "options": [
                    {"value": "django", "label": "Django"},
                    {"value": "flask", "label": "Flask"},
                    {"value": "fastapi", "label": "FastAPI"},
                ],
                "selected": ["django"],
                # Explicit, so the preview's handler does not depend on what
                # the example happens to be called (`name` is the fallback).
                "event": "set_frameworks",
            }
        ],
        "multimodal_input": [{"name": "prompt", "placeholder": "Ask anything…"}],
        "nav_menu": [
            {
                "brand": "MyApp",
                "items": [
                    {"label": "Home", "href": "#", "active": True},
                    {"label": "Docs", "href": "#docs"},
                    {"label": "Blog", "href": "#blog"},
                ],
            }
        ],
        "notification_center": [{}],
        "notification_popover": [
            {
                "notifications": [
                    {
                        "id": "n1",
                        "title": "New comment",
                        "body": "Ada replied to your post.",
                        "time": "2m",
                        "read": False,
                    },
                    {
                        "id": "n2",
                        "title": "Deploy finished",
                        "body": "build #412 is live.",
                        "time": "1h",
                        "read": True,
                    },
                ],
                "unread_count": 1,
            }
        ],
        "number_stepper": [{}],
        "org_chart": [
            {
                "nodes": [
                    {"id": "ceo", "name": "Ada Lovelace", "title": "CEO"},
                    {"id": "cto", "name": "Grace Hopper", "title": "CTO", "parent": "ceo"},
                    {"id": "eng", "name": "Alan Turing", "title": "Engineer", "parent": "cto"},
                ]
            }
        ],
        "otp_input": [
            {"name": "code", "digits": 6, "label": "Verification code", "event": "verify_code"}
        ],
        "pagination": [{}],
        "pie_chart": [
            {
                "segments": [
                    {"label": "Direct", "value": 45},
                    {"label": "Search", "value": 35},
                    {"label": "Referral", "value": 20},
                ],
                "title": "Traffic sources",
            }
        ],
        "pivot_table": [
            {
                "rows": "region",
                "cols": "quarter",
                # `values` names the numeric field to aggregate. Without it the
                # table renders its header row and nothing else — a pivot with
                # no numbers, which looks like a broken component rather than a
                # missing argument.
                "values": "revenue",
                "data": [
                    {"region": "North", "quarter": "Q1", "revenue": 120},
                    {"region": "North", "quarter": "Q2", "revenue": 150},
                    {"region": "South", "quarter": "Q1", "revenue": 90},
                ],
            }
        ],
        "popover": [{}],
        "presence_avatars": [
            {
                "users": [
                    {"name": "Ada Lovelace", "status": "online"},
                    {"name": "Grace Hopper", "status": "away"},
                    {"name": "Alan Turing", "status": "online"},
                ]
            }
        ],
        "prompt_editor": [
            {
                "template": "Summarise {{topic}} for {{audience}} in three bullet points.",
                "variables": {"topic": "djust LiveView", "audience": "a Django developer"},
            }
        ],
        "progress": [{"value": 60, "max": 100, "label": "60% complete", "variant": "success"}],
        "reactions": [
            {
                "options": ["", "👍", "🎉", "❤️"],
                "counts": {"👍": 3, "🎉": 1, "❤️": 0},
                "active": ["👍"],
            }
        ],
        "resizable_panel": [{"content": "<p>Drag the edge to resize.</p>"}],
        "responsive_image": [
            {
                "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='90'%3E%3Crect width='160' height='90' fill='%233b82f6'/%3E%3C/svg%3E",
                "alt": "A blue placeholder",
            }
        ],
        "rich_select": [{}],
        "rich_text_editor": [{"value": "Draft <strong>release notes</strong> here."}],
        "scroll_area": [{"content": "<p>Scrollable body text.</p>" * 12}],
        "scroll_spy": [{"sections": ["overview", "features", "pricing"], "active": "overview"}],
        "scroll_to_top": [{}],
        "sheet": [
            {
                "is_open": True,
                "title": "Filters",
                "content": '<p style="margin:0">Status, owner and date range.</p>',
            }
        ],
        "bottom_sheet": [
            {
                "open": True,
                "title": "Share",
                # `content` is escaped text here, unlike `sheet`'s.
                "content": "Copy link · Email · Embed",
            }
        ],
        "export_dialog": [
            {
                "open": True,
                "formats": ["csv", "xlsx", "json"],
                "columns": [
                    {"id": "name", "label": "Name"},
                    {"id": "email", "label": "Email"},
                    {"id": "joined", "label": "Joined", "checked": False},
                ],
            }
        ],
        "image_lightbox": [
            {
                "open": True,
                "images": [
                    {
                        "src": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='480' height='270'><rect width='100%' height='100%' fill='%2334473a'/><text x='50%' y='50%' fill='%23edf1ed' font-family='sans-serif' font-size='28' text-anchor='middle' dominant-baseline='middle'>1</text></svg>",
                        "alt": "First slide",
                        "caption": "First",
                    },
                    {
                        "src": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='480' height='270'><rect width='100%' height='100%' fill='%2334473a'/><text x='50%' y='50%' fill='%23edf1ed' font-family='sans-serif' font-size='28' text-anchor='middle' dominant-baseline='middle'>2</text></svg>",
                        "alt": "Second slide",
                        "caption": "Second",
                    },
                ],
            }
        ],
        "sidebar": [
            {
                "title": "Workspace",
                "items": [
                    {"label": "Dashboard", "href": "#", "active": True},
                    {"label": "Projects", "href": "#projects"},
                    {"label": "Settings", "href": "#settings"},
                ],
            }
        ],
        "signature_pad": [{}],
        "skeleton": [{}],
        "skeleton_factory": [{}],
        "sortable_grid": [
            {
                "items": [
                    {"id": "a", "label": "Alpha"},
                    {"id": "b", "label": "Beta"},
                    {"id": "c", "label": "Gamma"},
                ]
            }
        ],
        "sortable_list": [
            {
                "items": [
                    {"id": "1", "label": "First item"},
                    {"id": "2", "label": "Second item"},
                    {"id": "3", "label": "Third item"},
                ]
            }
        ],
        "source_citation": [{}],
        "sparkline": [{"data": [3, 5, 4, 8, 6, 9, 7], "variant": "line"}],
        "split_pane": [{}],
        "sticky_header": [
            {"content": "<strong>Section title</strong> — stays pinned while its section scrolls."}
        ],
        "tabs": [
            {
                "tabs": [{"id": "one", "label": "Overview"}, {"id": "two", "label": "Activity"}],
                "active": "one",
                "content": "<p>Overview content.</p>",
            }
        ],
        "tag_input": [
            {"name": "labels", "label": "Labels", "event": "add_tag", "tags": ["django", "rust"]}
        ],
        "time_picker": [{"name": "start", "value": "09:30", "label": "Start time"}],
        "toast": [{"message": "Example"}],
        "tooltip": [
            {
                "text": "Helpful hint",
                "content": '<button type="button">Hover me</button>',
                "position": "top",
            }
        ],
        "tree_view": [
            {
                "nodes": [
                    {
                        "id": "src",
                        "label": "src",
                        "expanded": True,
                        "children": [
                            {"id": "src/app", "label": "app.py"},
                            {"id": "src/util", "label": "util.py"},
                        ],
                    },
                    {"id": "tests", "label": "tests", "children": []},
                ]
            }
        ],
        "treemap": [
            {
                "data": [
                    {"name": "Search", "size": 45},
                    {"name": "Direct", "size": 30},
                    {"name": "Social", "size": 15},
                ]
            }
        ],
        "truncated_list": [
            {
                "items": ["First item", "Second item", "Third item", "Fourth item", "Fifth item"],
                "max": 3,
            }
        ],
        "virtual_list": [{}],
        "voice_input": [{"lang": "en-US"}],
    }
)


def _mark_example_markup(value: Any) -> Any:
    """Mark the example strings that are markup (they start with ``<``) safe.

    Python components HTML-escape their string arguments unless the value is
    marked safe, so an example that passes markup into a content slot has to
    pass it the way an app would: through ``mark_safe``. Plain-text example
    values are left as they are, and are escaped when rendered.
    """
    if isinstance(value, str) and value.lstrip().startswith("<"):
        return mark_safe(value)
    if isinstance(value, list):
        return [_mark_example_markup(v) for v in value]
    if isinstance(value, dict):
        return {k: _mark_example_markup(v) for k, v in value.items()}
    return value


for _name, _examples in PYTHON_COMPONENT_EXAMPLES.items():
    PYTHON_COMPONENT_EXAMPLES[_name] = _mark_example_markup(_examples)


#: The keys :func:`describe_component` always returns. Pinned as a constant so
#: a consumer in another repository (the documentation generator) can assert
#: the shape it depends on rather than discovering a missing key at build time.
COMPONENT_DESCRIPTION_KEYS = (
    "name",
    "display_name",
    "category",
    "component_type",
    "description",
    "import_line",
    "class_name",
    "params",
    "examples",
    "events",
    "accessibility",
    "slots",
    "style_paths",
    "python_class",
    "client",
)


#: Why a component's page shows no preview, when it cannot. The generic
#: "needs runtime dependencies or has no examples" was true of none of these:
#: most render nothing until opened, one is a mixin, one needs a bound form.
EMPTY_PREVIEW_REASONS: dict[str, str] = {
    "tour": "Renders nothing until started — it draws over the page's own elements.",
    "form_validation": "Renders a bound Django form field's errors — it needs a form to show anything.",
    "server_event_toast": (
        "A LiveView mixin, not a component — it shows toasts your view pushes, "
        "so there is nothing to render on its own."
    ),
}


#: A sentence under a preview that renders but shows nothing ON PURPOSE, so
#: the empty box is not read as a broken component.
PREVIEW_NOTES: dict[str, str] = {
    "connection_status": "Hidden while connected — it appears when the WebSocket drops.",
    "scroll_to_top": "Hidden until the page is scrolled past its threshold.",
    "infinite_scroll": (
        "An invisible sentinel: when it scrolls into view it sends load_more, "
        "and your view appends the next page."
    ),
    "collab_selection": (
        "Draws other users' selections over your content through a client hook djust "
        "does not ship — see below."
    ),
}


#: Contracted components whose same-named Python class renders markup djust
#: ships NO CSS for (#2993). The catalogue previews the styled
#: ``{% theme_<name> %}`` tag, so without a note a reader who reaches for the
#: class gets bare markup. ``test_components_css_2996_2993_3008`` fails when a
#: shipped stylesheet gains a rule for one of these, so the entry is removed
#: when the class stops being unstyled.
#: Maps the component name to ``(class name, root CSS class)``.
UNSTYLED_PYTHON_CLASSES: dict[str, tuple[str, str]] = {
    "alert": ("Alert", "dj-alert"),
    "avatar": ("Avatar", "dj-avatar"),
    "progress": ("Progress", "dj-progress"),
}


def unstyled_python_class(component_name: str) -> tuple[str, str]:
    """``(class name, root CSS class)`` of this component's unstyled Python
    class, or ``("", "")`` when it has none."""
    return UNSTYLED_PYTHON_CLASSES.get(component_name, ("", ""))


def preview_note(component_name: str) -> str:
    """The sentence shown under a preview that is empty by design."""
    return PREVIEW_NOTES.get(component_name, "")


def empty_preview_reason(component_name: str) -> str:
    """The sentence a catalogue page shows in place of a preview."""
    return EMPTY_PREVIEW_REASONS.get(
        component_name, "No preview — this component has no example to render yet."
    )


def describe_component(component_name: str) -> dict:
    """Everything known about one component, as data.

    This is the contract the component catalogue and the prose documentation
    share. The catalogue renders it; ``docs.djust.org`` generates its reference
    page from the same call, so the two cannot describe a component
    differently — before this, the generator read constructor signatures only
    and had no access to the descriptions, examples, events, accessibility
    rules or slots the registry carries.

    Returns a dict with exactly :data:`COMPONENT_DESCRIPTION_KEYS`:

    ``params``
        ``[{"name", "type", "default", "doc"}]`` — the constructor's own
        parameters for a python component, the template contract's context
        variables for a contracted one.
    ``examples``
        The kwarg dicts the catalogue previews, usable verbatim in a snippet.
    ``events``
        Server event names the host view must answer: what the first example's
        markup emits (minus names the example wrote into that markup itself),
        then the component's own ``event`` / ``*_event`` parameters, which
        cover states the example does not show. See
        :func:`~djust.theming.gallery.catalogue.contract_events`.
    ``style_paths``
        ``[(label, path)]`` — where to override it: the module or template,
        and the stylesheet that defines its classes.
    ``python_class``
        ``{"class_name", "import_line", "params"}`` when a Python class of the
        same name ALSO exists, which is the case for most contracted
        components: ``{% theme_alert %}`` and ``Alert`` are two ways to render
        one component, and a reader on either side needs to know the other is
        there. ``None`` when there is no such class, and for a component that
        IS a class (its own keys carry it).

    Raises ``KeyError`` for an unknown component, as
    ``build_catalogue_detail_context`` does.
    """
    from .catalogue import (
        build_catalogue_detail_context,
        component_description,
        contract_events,
    )

    ctx = build_catalogue_detail_context(component_name, render_examples=False)
    is_template = ctx.get("component_type") == "template"

    if is_template:
        # A template contract records a variable's name, type and whether it
        # is required, but never what it MEANS. Most contracted components
        # are also a Python class whose docstring documents exactly these
        # names, so that is where the descriptions come from — otherwise both
        # this page and the catalogue's props table show an em dash in every
        # row.
        contract_cls, _contract_class_name = _load_component_class(component_name)
        contract_docs = _docstring_args(contract_cls)
        params = [
            {
                "name": p.get("name", ""),
                "type": str(p.get("type", "") or ""),
                "default": p.get("default"),
                "doc": p.get("description") or contract_docs.get(p.get("name", ""), ""),
                "required": required,
                "kind": "",
            }
            for required, group in (
                (True, ctx.get("required_context") or []),
                (False, ctx.get("optional_context") or []),
            )
            for p in group
        ]
    else:
        cls, _class_name = _load_component_class(component_name)
        arg_docs = _docstring_args(cls)
        params = [
            {
                "name": p.get("name", ""),
                # ``<class 'float'>`` is the repr of a type, not a type name.
                "type": _annotation_name(p.get("annotation")),
                "default": p.get("default"),
                "doc": p.get("description") or arg_docs.get(p.get("name", ""), ""),
                "required": _is_required(p),
                "kind": p.get("kind", ""),
            }
            for p in ctx.get("python_params") or []
        ]

    examples = list(ctx.get("examples") or []) or list(
        PYTHON_COMPONENT_EXAMPLES.get(component_name) or []
    )

    html = ""
    if examples:
        try:
            if is_template:
                from .catalogue import _render_template_examples

                rendered = _render_template_examples(component_name, examples[:1])
                html = "".join(e.get("html", "") for e in rendered)
            else:
                html = render_python_component_example(component_name, dict(examples[0]))
        except Exception:  # noqa: BLE001 — a component that cannot render emits nothing to scan
            logger.debug("could not scan events for %s", sanitize_for_log(component_name))
    # A tag's events are what its markup sends; a class also declares its
    # events as parameters, which covers states the example does not show.
    events = contract_events(
        [] if is_template else params, dict(examples[0]) if examples else {}, html
    )

    style_paths = []
    if ctx.get("template_path"):
        style_paths.append(("template", str(ctx["template_path"])))
    if ctx.get("module_path"):
        style_paths.append(("module", str(ctx["module_path"])))
    if ctx.get("css_path"):
        style_paths.append(("css", str(ctx["css_path"])))

    python_class = None
    if is_template:
        cls, class_name = _load_component_class(component_name)
        if cls is not None:
            arg_docs = _docstring_args(cls)
            signature = get_python_component_signature(component_name) or []
            python_class = {
                "class_name": class_name,
                "import_line": f"from djust.components import {class_name}",
                "params": [
                    {
                        "name": p.get("name", ""),
                        "type": _annotation_name(p.get("annotation")),
                        "default": p.get("default"),
                        "doc": p.get("description") or arg_docs.get(p.get("name", ""), ""),
                        "required": _is_required(p),
                        "kind": p.get("kind", ""),
                    }
                    for p in signature
                ],
            }

    return {
        "name": component_name,
        "display_name": ctx.get("display_name", component_name.replace("_", " ").title()),
        "category": ctx.get("category", ""),
        "component_type": ctx.get("component_type", "python"),
        # The detail context does not carry the one-line description (the
        # index page computes it); the reference entry leads with it, so it is
        # part of this contract rather than something a consumer re-derives.
        "description": ctx.get("description") or component_description(component_name) or "",
        "import_line": ctx.get("import_line", "") or "",
        "class_name": ctx.get("class_name", "") or "",
        "params": params,
        "examples": examples,
        "events": events,
        "accessibility": list(ctx.get("accessibility") or []),
        "slots": list(ctx.get("available_slots") or []),
        "style_paths": style_paths,
        "python_class": python_class,
        "client": component_client(component_name),
    }


_HOOK_ATTR_RE = re.compile(r'dj-hook="([A-Za-z_]\w*)"')


def _components_static_dir() -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "components" / "static" / "djust_components"


def _client_hook_sources() -> str:
    """Every script djust ships that could define a component hook."""
    from pathlib import Path

    djust_root = Path(__file__).resolve().parents[2]
    texts = []
    for folder in (_components_static_dir(), djust_root / "static" / "djust" / "src"):
        if folder.is_dir():
            texts += [f.read_text(errors="ignore") for f in sorted(folder.glob("*.js"))]
    return "\n".join(texts)


def component_client(component_name: str) -> dict:
    """What a component needs in the browser beyond djust's client.

    ``{"hook", "script", "hook_shipped"}``:

    ``hook``
        The ``dj-hook`` name its markup carries, read from the class source so
        it is found even when no example renders it; ``""`` if none.
    ``script``
        The static path of the script djust ships for it
        (``djust_components/countdown.js``), or ``""``. A page must include it:
        the catalogue did not, so these previews were inert.
    ``hook_shipped``
        Whether any shipped script answers that hook. For most hooks none
        does — the component renders its markup and the interaction its
        docstring describes (drag, draw, crop …) is left to the app.
    """
    cls, _class_name = _load_component_class(component_name)
    hook = ""
    if cls is not None:
        try:
            found = _HOOK_ATTR_RE.search(inspect.getsource(cls))
        except (OSError, TypeError):
            found = None
        hook = found.group(1) if found else ""
    script_name = component_name.replace("_", "-") + ".js"
    script = (
        f"djust_components/{script_name}"
        if (_components_static_dir() / script_name).is_file()
        else ""
    )
    hook_shipped = False
    if hook:
        sources = _client_hook_sources()
        hook_shipped = bool(
            re.search(rf'hooks\.{hook}\s*=|dj-hook="{hook}"|\b{hook}\s*:\s*\{{', sources)
        )
    return {"hook": hook, "script": script, "hook_shipped": hook_shipped}


def _docstring_args(cls: Any) -> dict:
    """``{parameter: description}`` from a class's ``Args:`` block.

    The parameter descriptions a component's author wrote. Neither the
    signature nor the registry carries them, so both the catalogue's
    parameters table and the generated reference showed an em dash for every
    row until this read them off the docstring.
    """
    if cls is None:
        return {}
    doc = inspect.getdoc(cls) or ""
    match = re.search(r"(?:^|\n)Args:\n(.*?)(?=\n\S|\Z)", doc, re.S)
    if not match:
        return {}
    described: dict = {}
    current = None
    for line in match.group(1).splitlines():
        entry = re.match(r"^\s+(\w+)(?:\s*\([^)]*\))?:\s*(.*)", line)
        if entry:
            current = entry[1]
            described[current] = entry[2].strip()
        elif current and line.strip():
            described[current] += " " + line.strip()
    return described


def _annotation_name(annotation: Any) -> str:
    text = str(annotation or "")
    if text.startswith("<class '") and text.endswith("'>"):
        return text[8:-2]
    return text
