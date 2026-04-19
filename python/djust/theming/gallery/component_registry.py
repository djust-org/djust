"""
Component registry for all 169 djust-components, organized by category.

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
        {"name": "notifications", "label": "Enable notifications", "checked": True},
        {"name": "dark_mode", "label": "Dark mode", "checked": False},
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
    "rating": [
        {"value": 4, "max": 5, "name": "rating1"},
        {"value": 2, "max": 5, "name": "rating2", "readonly": True},
    ],
    "meter": [
        {"value": 70, "min": 0, "max": 100, "label": "Storage"},
        {"value": 30, "min": 0, "max": 100, "label": "Memory"},
    ],
    "callout": [
        {"message": "This is an important notice.", "variant": "info", "title": "Info"},
        {"message": "Warning: action is irreversible.", "variant": "warning", "title": "Warning"},
    ],
    "collapsible": [
        {"title": "Show details", "content": "Hidden content shown when expanded."},
    ],
    "toggle_group": [
        {
            "name": "view",
            "options": [{"value": "list", "label": "List"}, {"value": "grid", "label": "Grid"}],
            "value": "list",
        },
    ],
    "segmented_progress": [
        {
            "segments": [
                {"label": "Done", "value": 40, "variant": "success"},
                {"label": "In Progress", "value": 30, "variant": "warning"},
                {"label": "Todo", "value": 30},
            ]
        },
    ],
    "empty_state": [
        {
            "title": "No results found",
            "message": "Try adjusting your search or filters.",
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
        {"message": "Your trial expires in 3 days.", "variant": "warning", "dismissible": True},
    ],
    "status_dot": [
        {"status": "online", "label": "Online"},
        {"status": "offline", "label": "Offline"},
        {"status": "busy", "label": "Busy"},
    ],
    "status_indicator": [
        {"status": "running", "label": "Service running"},
        {"status": "stopped", "label": "Service stopped"},
    ],
    "connection_status": [
        {"connected": True},
        {"connected": False},
    ],
    "live_indicator": [
        {"active": True, "label": "Live"},
        {"active": False, "label": "Offline"},
    ],
    "thinking_indicator": [
        {"label": "Thinking..."},
    ],
    "copy_button": [
        {"text": "npm install djust-theming", "label": "Copy"},
    ],
    "copyable_text": [
        {"text": "pip install djust-theming", "label": "Install"},
    ],
    "icon": [
        {"name": "check", "size": 24},
        {"name": "x", "size": 24},
        {"name": "search", "size": 24},
    ],
    "qr_code": [
        {"data": "https://djust.org", "size": 150},
    ],
    "countdown": [
        {"target": "2026-12-31", "label": "Until New Year"},
    ],
    "relative_time": [
        {"timestamp": "2026-03-01T12:00:00"},
    ],
    "animated_number": [
        {"value": 1234, "duration": 1000},
    ],
    "live_counter": [
        {"value": 42, "label": "online"},
    ],
    "token_counter": [
        {"count": 1500, "max": 4096, "label": "tokens"},
    ],
    "progress_circle": [
        {"value": 75, "max": 100, "label": "75%"},
        {"value": 33, "max": 100, "label": "33%"},
    ],
    "code_snippet": [
        {"code": "pip install djust", "language": "bash"},
    ],
    "code_block": [
        {"code": 'print("Hello, world!")', "language": "python", "title": "example.py"},
    ],
    "markdown": [
        {"content": "# Hello\n\nThis is **markdown** rendered inline."},
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
            "items": [
                {"actor": "Alice", "action": "created issue", "target": "#123", "time": "2h ago"},
                {"actor": "Bob", "action": "merged PR", "target": "#45", "time": "4h ago"},
            ]
        },
    ],
    "notification_badge": [
        {"count": 5},
        {"count": 99},
        {"count": 0},
    ],
    "avatar_group": [
        {"avatars": [{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}], "max": 3},
    ],
    "ribbon": [
        {"label": "New", "variant": "success"},
        {"label": "Beta", "variant": "warning"},
    ],
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
        {"label": "Toggle theme"},
    ],
    "stepper": [
        {"steps": [{"label": "Account"}, {"label": "Details"}, {"label": "Review"}], "current": 1},
    ],
    "toolbar": [
        {
            "items": [
                {"label": "Bold", "action": "bold"},
                {"label": "Italic", "action": "italic"},
                {"label": "Underline", "action": "underline"},
            ]
        },
    ],
    "announcement_bar": [
        {"message": "🎉 djust v1.0 is now available!", "variant": "success"},
    ],
    "cookie_consent": [
        {"message": "We use cookies to improve your experience."},
    ],
    "feedback_widget": [
        {"question": "Was this helpful?"},
    ],
    "streaming_text": [
        {"text": "Generating response...", "active": True},
    ],
}


def get_component_category(component_name: str) -> str:
    """Return the category for a given component name, or 'Other' if not found."""
    return _COMPONENT_TO_CATEGORY.get(component_name, "Other")


def get_all_components_by_category() -> dict[str, list[str]]:
    """Return all 169 components organized by category."""
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


def render_python_component_example(component_name: str, kwargs_dict: dict) -> str:
    """Import and render a djust-component by name.

    Dynamically imports from djust.components.components.<name>, instantiates
    the class with kwargs_dict, and calls .render().

    Returns rendered HTML string, or an error message string if import/render fails.
    """
    class_name = _to_class_name(component_name)
    try:
        module = importlib.import_module(f"djust.components.components.{component_name}")
        cls = getattr(module, class_name)
        instance = cls(**kwargs_dict)
        return instance.render()
    except ImportError:
        logger.debug("djust_components not available for component: %s", component_name)
        return ""
    except Exception as exc:
        logger.debug("Could not render component %s: %s", component_name, exc)
        return ""


def get_python_component_signature(component_name: str) -> list[dict] | None:
    """Return parameter info for a Python component's __init__ method.

    Returns a list of dicts with keys: name, kind, default, annotation.
    Returns None if the component cannot be imported.
    """
    class_name = _to_class_name(component_name)
    try:
        module = importlib.import_module(f"djust.components.components.{component_name}")
        cls = getattr(module, class_name)
        sig = inspect.signature(cls.__init__)
        params = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            params.append(
                {
                    "name": param_name,
                    "kind": str(param.kind.name),
                    "default": (
                        repr(param.default) if param.default is not inspect.Parameter.empty else "—"
                    ),
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
