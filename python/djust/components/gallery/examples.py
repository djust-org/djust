"""Example data definitions for every component in the gallery.

To add a new component to the gallery:
1. Add an entry to EXAMPLES (for template tags) or CLASS_EXAMPLES (for component classes)
2. Include at least one variant with a 'name' and 'template' (or 'render' for classes)
3. Add a one-line 'purpose', plus 'keywords' and 'related' tuples
4. Register child tags in CHILD_TAGS with their parent entry
5. Set the 'category' to one of the keys in CATEGORIES
6. Run tests to verify: .venv/bin/python -m pytest tests/test_gallery.py -v
"""

from typing import Any, Dict, Iterator

# Category slug -> display label mapping
CATEGORIES = {
    "layout": "Layout",
    "form": "Form",
    "overlay": "Overlay",
    "feedback": "Feedback",
    "data": "Data",
    "navigation": "Navigation",
    "indicator": "Indicator",
    "typography": "Typography",
    "misc": "Misc",
}

# Stable ordering for prev/next navigation in category pages
CATEGORY_ORDER = [
    "layout",
    "form",
    "data",
    "navigation",
    "overlay",
    "feedback",
    "indicator",
    "typography",
    "misc",
]

# ─── Template Tag Examples ───
# Each key must match a registered template tag name.
# 'variants' is a list of dicts: {"name": str, "template": str, "context": dict (optional)}

EXAMPLES: Dict[str, Any] = {
    # ── Layout ──
    "modal": {
        "purpose": "Overlay dialog with a title, close action and configurable size",
        "keywords": (),
        "related": (),
        "label": "Modal",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": '{% modal open=True title="Confirm Action" %}Are you sure you want to proceed?{% endmodal %}',
            },
            {
                "name": "Large",
                "template": '{% modal open=True title="Details" size="lg" %}Detailed content goes here.{% endmodal %}',
            },
            {
                "name": "Small",
                "template": '{% modal open=True title="Quick" size="sm" %}Small modal.{% endmodal %}',
            },
        ],
    },
    "card": {
        "purpose": "Group content in a titled panel with optional subtitle and elevation",
        "keywords": (),
        "related": (),
        "label": "Card",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": '{% card title="Project Status" %}Card body content.{% endcard %}',
            },
            {
                "name": "With Subtitle",
                "template": '{% card title="Metrics" subtitle="Last 30 days" %}Data here.{% endcard %}',
            },
            {
                "name": "Elevated",
                "template": '{% card title="Elevated" variant="elevated" %}Shadow card.{% endcard %}',
            },
        ],
    },
    "accordion": {
        "purpose": "Expand one section at a time using titled accordion items",
        "keywords": (),
        "related": (),
        "label": "Accordion",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% accordion id="acc1" active=active %}'
                    '{% accordion_item id="s1" title="Section 1" %}Content for section 1.{% endaccordion_item %}'
                    '{% accordion_item id="s2" title="Section 2" %}Content for section 2.{% endaccordion_item %}'
                    "{% endaccordion %}"
                ),
            },
        ],
    },
    "tabs": {
        "purpose": "Switch between labelled content panels using tab navigation",
        "keywords": (),
        "related": (),
        "label": "Tabs",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% tabs id="t1" active="overview" %}'
                    '{% tab id="overview" label="Overview" %}Overview content.{% endtab %}'
                    '{% tab id="settings" label="Settings" %}Settings content.{% endtab %}'
                    "{% endtabs %}"
                ),
            },
        ],
    },
    "collapsible": {
        "purpose": "Show or hide a content block with a trigger button",
        "keywords": (),
        "related": (),
        "label": "Collapsible",
        "category": "layout",
        "variants": [
            {
                "name": "Closed",
                "template": '{% collapsible trigger="Show Details" %}Hidden content here.{% endcollapsible %}',
            },
            {
                "name": "Open",
                "template": '{% collapsible trigger="Hide Details" open=True %}Visible content.{% endcollapsible %}',
            },
        ],
    },
    "sheet": {
        "purpose": "Slide-over side panel drawer for settings or navigation",
        "keywords": ("drawer", "side panel", "slide-over", "offcanvas"),
        "related": ("modal", "bottom_sheet"),
        "label": "Sheet / Drawer",
        "category": "layout",
        "variants": [
            {
                "name": "Right (default)",
                "template": '{% sheet open=True title="Settings" %}Sheet body content.{% endsheet %}',
            },
            {
                "name": "Left",
                "template": '{% sheet open=True title="Navigation" side="left" %}Nav items.{% endsheet %}',
            },
        ],
    },
    "split_pane": {
        "purpose": "Divide content into two panes with a draggable resize handle",
        "keywords": (),
        "related": (),
        "label": "Split Pane",
        "category": "layout",
        "variants": [
            {
                "name": "Horizontal",
                "template": (
                    '{% split_pane direction="horizontal" initial="50" %}'
                    "<p>Left pane</p>"
                    "{% pane %}"
                    "<p>Right pane</p>"
                    "{% endsplit_pane %}"
                ),
            },
        ],
    },
    # ── Form ──
    "dj_button": {
        "purpose": "Trigger an action with a styled button, optional icon and loading state",
        "keywords": (),
        "related": (),
        "label": "Button",
        "category": "form",
        "variants": [
            {"name": "Primary", "template": '{% dj_button label="Save" variant="primary" %}'},
            {"name": "Danger", "template": '{% dj_button label="Delete" variant="danger" %}'},
            {"name": "Outline", "template": '{% dj_button label="Cancel" variant="outline" %}'},
            {"name": "Loading", "template": '{% dj_button label="Processing..." loading=True %}'},
            {"name": "With Icon", "template": '{% dj_button label="Download" icon="⬇" %}'},
            {"name": "Small", "template": '{% dj_button label="Small" size="sm" %}'},
            {"name": "Large", "template": '{% dj_button label="Large" size="lg" %}'},
        ],
    },
    "dj_input": {
        "purpose": "Labelled text input inside a form-group wrapper",
        "keywords": (),
        "related": (),
        "label": "Input",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dj_input name="email" label="Email" placeholder="you@example.com" %}',
            },
            {
                "name": "With Value",
                "template": '{% dj_input name="name" label="Name" value="John Doe" %}',
            },
            {
                "name": "Password",
                "template": '{% dj_input name="pass" label="Password" input_type="password" %}',
            },
        ],
    },
    "dj_select": {
        "purpose": "Labelled native select with options inside a form-group wrapper",
        "keywords": (),
        "related": (),
        "label": "Select",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dj_select name="color" label="Color" options=options %}',
                "context": {
                    "options": [
                        {"value": "red", "label": "Red"},
                        {"value": "blue", "label": "Blue"},
                        {"value": "green", "label": "Green"},
                    ]
                },
            },
        ],
    },
    "dj_textarea": {
        "purpose": "Labelled multiline text input inside a form-group wrapper",
        "keywords": (),
        "related": (),
        "label": "Textarea",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dj_textarea name="notes" label="Notes" placeholder="Write something..." %}',
            },
        ],
    },
    "dj_checkbox": {
        "purpose": "Single checkbox input with a label and checked state",
        "keywords": (),
        "related": (),
        "label": "Checkbox",
        "category": "form",
        "variants": [
            {
                "name": "Unchecked",
                "template": '{% dj_checkbox name="agree" label="I agree to the terms" %}',
            },
            {
                "name": "Checked",
                "template": '{% dj_checkbox name="agree" label="I agree to the terms" checked=True %}',
            },
        ],
    },
    "dj_radio": {
        "purpose": "Single radio input for choosing one value in a named group",
        "keywords": (),
        "related": (),
        "label": "Radio",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% dj_radio name="plan" label="Free" value="free" %}'},
            {
                "name": "Selected",
                "template": '{% dj_radio name="plan" label="Pro" value="pro" current_value="pro" %}',
            },
        ],
    },
    "switch": {
        "purpose": "Accessible on/off toggle with a checked state",
        "keywords": (),
        "related": (),
        "label": "Switch",
        "category": "form",
        "variants": [
            {"name": "Off", "template": '{% switch name="dark" label="Dark Mode" %}'},
            {"name": "On", "template": '{% switch name="dark" label="Dark Mode" checked=True %}'},
        ],
    },
    "color_picker": {
        "purpose": "Choose colors using preset swatches and a hex input",
        "keywords": (),
        "related": (),
        "label": "Color Picker",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% color_picker name="theme" label="Theme Color" value="#3B82F6" %}',
            },
        ],
    },
    "combobox": {
        "purpose": "Searchable select dropdown with server-driven option filtering",
        "keywords": ("dropdown", "autocomplete", "typeahead", "searchable"),
        "related": ("rich_select", "autocomplete"),
        "label": "Combobox",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% combobox name="lang" label="Language" options=options %}',
                "context": {
                    "options": [
                        {"value": "py", "label": "Python"},
                        {"value": "js", "label": "JavaScript"},
                        {"value": "rs", "label": "Rust"},
                    ]
                },
            },
        ],
    },
    "date_picker": {
        "purpose": "Server-driven calendar date picker with optional date range",
        "keywords": (),
        "related": (),
        "label": "Date Picker",
        "category": "form",
        "variants": [
            {"name": "Default", "template": "{% date_picker year=2026 month=3 %}"},
        ],
    },
    "file_dropzone": {
        "purpose": "Drag-and-drop file upload zone with a browse control",
        "keywords": (),
        "related": (),
        "label": "File Dropzone",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% file_dropzone name="upload" label="Drop files here" %}',
            },
            {
                "name": "Multiple",
                "template": '{% file_dropzone name="uploads" label="Drop files" multiple=True %}',
            },
        ],
    },
    "form_group": {
        "purpose": "Wrap form controls with a label, help text and validation error",
        "keywords": (),
        "related": (),
        "label": "Form Group",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% form_group label="Full Name" %}{% dj_input name="fullname" %}{% endform_group %}',
            },
        ],
    },
    # ── Overlay ──
    "dropdown": {
        "purpose": "Toggle a menu beneath a labelled trigger",
        "keywords": (),
        "related": (),
        "label": "Dropdown",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% dropdown id="d1" label="Options" %}'
                    '<a class="dropdown-item">Edit</a>'
                    '<a class="dropdown-item">Delete</a>'
                    "{% enddropdown %}"
                ),
            },
        ],
    },
    "tooltip": {
        "purpose": "Show a short hint beside wrapped content on hover",
        "keywords": (),
        "related": (),
        "label": "Tooltip",
        "category": "overlay",
        "variants": [
            {
                "name": "Top",
                "template": '{% tooltip text="Helpful tip" position="top" %}Hover me{% endtooltip %}',
            },
            {
                "name": "Bottom",
                "template": '{% tooltip text="More info" position="bottom" %}Hover me{% endtooltip %}',
            },
        ],
    },
    "popover": {
        "purpose": "Toggle a floating content panel beside a trigger",
        "keywords": (),
        "related": (),
        "label": "Popover",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": '{% popover trigger="Click me" title="Info" %}Popover content here.{% endpopover %}',
            },
        ],
    },
    "command_palette": {
        "purpose": "Search a command list in a dialog with selectable palette items",
        "keywords": (),
        "related": (),
        "label": "Command Palette",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": (
                    "{% command_palette open=True %}"
                    '{% palette_item label="New File" shortcut="Ctrl+N" event="new_file" %}'
                    '{% palette_item label="Open File" shortcut="Ctrl+O" event="open_file" %}'
                    "{% endcommand_palette %}"
                ),
            },
        ],
    },
    "context_menu": {
        "purpose": "Display actions in a menu attached to wrapped content",
        "keywords": (),
        "related": (),
        "label": "Context Menu",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% context_menu label="Right-click here" %}'
                    '{% context_menu_item label="Copy" event="copy" icon="📋" %}'
                    '{% context_menu_item label="Delete" event="delete" danger=True %}'
                    "{% endcontext_menu %}"
                ),
            },
        ],
    },
    # ── Feedback ──
    "alert": {
        "purpose": "Inline status message with optional dismiss action",
        "keywords": (),
        "related": (),
        "label": "Alert",
        "category": "feedback",
        "variants": [
            {
                "name": "Info",
                "template": '{% alert variant="info" %}This is an info alert.{% endalert %}',
            },
            {
                "name": "Success",
                "template": '{% alert variant="success" %}Operation succeeded!{% endalert %}',
            },
            {
                "name": "Warning",
                "template": '{% alert variant="warning" %}Please review.{% endalert %}',
            },
            {
                "name": "Danger",
                "template": '{% alert variant="danger" %}Something went wrong.{% endalert %}',
            },
        ],
    },
    "toast_container": {
        "purpose": "Stack dismissible toast notifications from a supplied message list",
        "keywords": ("notification", "flash", "message", "snackbar", "save"),
        "related": (),
        "label": "Toast",
        "category": "feedback",
        "variants": [
            {
                "name": "Default",
                "template": "{% toast_container toasts %}",
                "context": {
                    "toasts": [
                        {"id": "1", "type": "success", "message": "File saved!"},
                        {"id": "2", "type": "error", "message": "Upload failed."},
                    ]
                },
            },
        ],
    },
    "progress": {
        "purpose": "Show completion percentage in a labelled progress bar",
        "keywords": (),
        "related": (),
        "label": "Progress",
        "category": "feedback",
        "variants": [
            {"name": "25%", "template": "{% progress 25 %}"},
            {"name": "75%", "template": "{% progress 75 %}"},
            {"name": "100%", "template": "{% progress 100 %}"},
        ],
    },
    "spinner": {
        "purpose": "Animated loading spinner with configurable size and color",
        "keywords": (),
        "related": (),
        "label": "Spinner",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": "{% spinner %}"},
            {"name": "Small", "template": '{% spinner size="sm" %}'},
            {"name": "Large", "template": '{% spinner size="lg" %}'},
        ],
    },
    "skeleton": {
        "purpose": "Loading placeholders for text, avatars or cards",
        "keywords": (),
        "related": (),
        "label": "Skeleton",
        "category": "feedback",
        "variants": [
            {"name": "Text", "template": '{% skeleton skeleton_type="text" lines=3 %}'},
            {"name": "Circle", "template": '{% skeleton skeleton_type="circle" %}'},
            {"name": "Rectangle", "template": '{% skeleton skeleton_type="rect" %}'},
        ],
    },
    "empty_state": {
        "purpose": "Explain an empty result with an optional call to action",
        "keywords": (),
        "related": (),
        "label": "Empty State",
        "category": "feedback",
        "variants": [
            {
                "name": "Default",
                "template": '{% empty_state title="No results" description="Try adjusting your search." icon="🔍" action_label="Clear filters" action_event="clear" %}',
            },
        ],
    },
    # ── Data ──
    "data_table": {
        "purpose": "Sortable table with search, filters, bulk row selection, pagination and editing",
        "keywords": (
            "grid",
            "rows",
            "sort",
            "paginate",
            "list",
            "bulk",
            "select",
            "infinite",
            "scroll",
        ),
        "related": ("infinite_scroll", "filter_bar", "pagination"),
        "label": "Data Table",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% data_table rows columns %}",
                "context": {
                    "rows": [
                        {"name": "Alice", "role": "Admin"},
                        {"name": "Bob", "role": "User"},
                        {"name": "Carol", "role": "Editor"},
                    ],
                    "columns": [
                        {"key": "name", "label": "Name"},
                        {"key": "role", "label": "Role"},
                    ],
                },
            },
        ],
    },
    "pagination": {
        "purpose": "Navigate pages with previous, next and numbered page controls",
        "keywords": (),
        "related": (),
        "label": "Pagination",
        "category": "data",
        "variants": [
            {"name": "Default", "template": "{% pagination page=3 total_pages=10 %}"},
        ],
    },
    "virtual_list": {
        "purpose": "Paginated list for large datasets with load-more controls",
        "keywords": (),
        "related": (),
        "label": "Virtual List",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% virtual_list items=items total=100 page=1 page_size=5 %}",
                "context": {
                    "items": [
                        {"id": "1", "content": "Item 1"},
                        {"id": "2", "content": "Item 2"},
                        {"id": "3", "content": "Item 3"},
                    ]
                },
            },
        ],
    },
    "kanban_board": {
        "purpose": "Arrange cards in status columns with move actions",
        "keywords": (),
        "related": (),
        "label": "Kanban Board",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% kanban_board columns=cols %}",
                "context": {
                    "cols": [
                        {"id": "todo", "title": "To Do", "cards": [{"id": "1", "title": "Task 1"}]},
                        {
                            "id": "doing",
                            "title": "In Progress",
                            "cards": [{"id": "2", "title": "Task 2"}],
                        },
                        {"id": "done", "title": "Done", "cards": []},
                    ]
                },
            },
        ],
    },
    "tree_view": {
        "purpose": "Browse hierarchical nodes with expand and selection actions",
        "keywords": (),
        "related": (),
        "label": "Tree View",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% tree_view nodes=nodes %}",
                "context": {
                    "nodes": [
                        {
                            "id": "1",
                            "label": "Root",
                            "children": [
                                {"id": "2", "label": "Child A", "children": []},
                                {"id": "3", "label": "Child B", "children": []},
                            ],
                        },
                    ]
                },
            },
        ],
    },
    # ── Navigation ──
    "breadcrumb": {
        "purpose": "Link back through an ordered navigation hierarchy",
        "keywords": (),
        "related": (),
        "label": "Breadcrumb",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": "{% breadcrumb items=items %}",
                "context": {
                    "items": [
                        {"label": "Home", "url": "/"},
                        {"label": "Products", "url": "/products/"},
                        {"label": "Widget"},
                    ]
                },
            },
        ],
    },
    "stepper": {
        "purpose": "Show progress through labelled steps with a step selection action",
        "keywords": (),
        "related": (),
        "label": "Stepper",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": "{% stepper steps=steps active=1 %}",
                "context": {
                    "steps": [
                        {"label": "Account", "complete": True},
                        {"label": "Profile", "complete": False},
                        {"label": "Confirm", "complete": False},
                    ]
                },
            },
        ],
    },
    "table_of_contents": {
        "purpose": "Navigate to document sections from a supplied list of headings",
        "keywords": (),
        "related": (),
        "label": "Table of Contents",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": '{% table_of_contents items=items active="intro" %}',
                "context": {
                    "items": [
                        {"id": "intro", "label": "Introduction", "level": 1},
                        {"id": "setup", "label": "Setup", "level": 1},
                        {"id": "config", "label": "Configuration", "level": 2},
                    ]
                },
            },
        ],
    },
    "timeline": {
        "purpose": "Display event content along a vertical timeline",
        "keywords": (),
        "related": (),
        "label": "Timeline",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": (
                    "{% timeline %}"
                    '{% timeline_item title="Created" time="9:00 AM" %}Initial setup.{% endtimeline_item %}'
                    '{% timeline_item title="Updated" time="2:00 PM" %}Config changed.{% endtimeline_item %}'
                    "{% endtimeline %}"
                ),
            },
        ],
    },
    # ── Indicator ──
    "badge": {
        "purpose": "Compact status label with an optional pulsing indicator",
        "keywords": (),
        "related": (),
        "label": "Badge (Tag)",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% badge label="Active" %}'},
            {"name": "Online", "template": '{% badge label="Online" status="online" %}'},
            {"name": "Error", "template": '{% badge label="Error" status="error" %}'},
            {"name": "Warning", "template": '{% badge label="Pending" status="warning" %}'},
            {"name": "Pulse", "template": '{% badge label="Live" status="online" pulse=True %}'},
        ],
    },
    "avatar": {
        "purpose": "Display a user image or initials with an optional status indicator",
        "keywords": (),
        "related": (),
        "label": "Avatar",
        "category": "indicator",
        "variants": [
            {"name": "Initials", "template": '{% avatar initials="JD" alt="John Doe" %}'},
            {"name": "With Status", "template": '{% avatar initials="AB" status="online" %}'},
            {"name": "Large", "template": '{% avatar initials="XY" size="lg" %}'},
        ],
    },
    "rating": {
        "purpose": "Display or choose a star rating",
        "keywords": (),
        "related": (),
        "label": "Rating",
        "category": "indicator",
        "variants": [
            {"name": "3 of 5", "template": "{% rating value=3 %}"},
            {"name": "Readonly", "template": "{% rating value=4 readonly=True %}"},
        ],
    },
    "gauge": {
        "purpose": "Visualize a value as an SVG donut gauge",
        "keywords": (),
        "related": (),
        "label": "Gauge",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% gauge value=65 label="CPU" %}'},
            {
                "name": "Full",
                "template": '{% gauge value=100 max_value=100 label="Memory" color="danger" %}',
            },
        ],
    },
    "stat_card": {
        "purpose": "Display a metric with its label and optional trend",
        "keywords": (),
        "related": (),
        "label": "Stat Card",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": '{% stat_card label="Users" value="1,234" trend="+12%" trend_direction="up" %}',
            },
            {
                "name": "Down",
                "template": '{% stat_card label="Errors" value="42" trend="-5%" trend_direction="down" %}',
            },
        ],
    },
    # ── Typography ──
    "code_block": {
        "purpose": "Syntax-highlighted code with an optional copy button",
        "keywords": (),
        "related": (),
        "label": "Code Block",
        "category": "typography",
        "variants": [
            {
                "name": "Python",
                "template": '{% code_block code="def hello():\\n    print(\'Hello!\')" language="python" %}',
            },
        ],
    },
    "kbd": {
        "purpose": "Display keyboard shortcut keys as keycaps",
        "keywords": (),
        "related": (),
        "label": "Kbd",
        "category": "typography",
        "variants": [
            {"name": "Single", "template": '{% kbd "Ctrl" %}'},
            {"name": "Combo", "template": '{% kbd "Ctrl" "C" %}'},
        ],
    },
    # ── Misc ──
    "dj_tag": {
        "purpose": "Label content with a small chip and optional remove action",
        "keywords": (),
        "related": (),
        "label": "Tag",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% dj_tag label="python" %}'},
            {"name": "Dismissible", "template": '{% dj_tag label="removable" dismissible=True %}'},
        ],
    },
    "dj_divider": {
        "purpose": "Separate content with a horizontal or vertical rule and optional label",
        "keywords": (),
        "related": (),
        "label": "Divider",
        "category": "misc",
        "variants": [
            {"name": "Horizontal", "template": "{% dj_divider %}"},
            {"name": "With Label", "template": '{% dj_divider label="OR" %}'},
        ],
    },
    "carousel": {
        "purpose": "Navigate an image slideshow with previous and next controls",
        "keywords": (),
        "related": (),
        "label": "Carousel",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": "{% carousel images=images %}",
                "context": {
                    "images": [
                        {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='600' height='300'%3E%3Crect fill='%233B82F6' width='600' height='300'/%3E%3Ctext x='50%25' y='50%25' fill='white' text-anchor='middle' dy='.35em' font-size='24'%3ESlide 1%3C/text%3E%3C/svg%3E",
                            "alt": "Slide 1",
                        },
                        {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='600' height='300'%3E%3Crect fill='%2310B981' width='600' height='300'/%3E%3Ctext x='50%25' y='50%25' fill='white' text-anchor='middle' dy='.35em' font-size='24'%3ESlide 2%3C/text%3E%3C/svg%3E",
                            "alt": "Slide 2",
                        },
                    ]
                },
            },
        ],
    },
    "copy_button": {
        "purpose": "Copy supplied text to the clipboard with a confirmation label",
        "keywords": (),
        "related": (),
        "label": "Copy Button",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% copy_button text="Copied text here" %}'},
        ],
    },
    "notification_center": {
        "purpose": "Notification bell with unread count and a dropdown message list",
        "keywords": (),
        "related": (),
        "label": "Notification Center",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": "{% notification_center notifications=notifs unread_count=2 %}",
                "context": {
                    "notifs": [
                        {
                            "id": "1",
                            "title": "New message",
                            "body": "You have a new message.",
                            "time": "2m ago",
                            "read": False,
                        },
                        {
                            "id": "2",
                            "title": "Deploy done",
                            "body": "v1.2.0 deployed.",
                            "time": "1h ago",
                            "read": True,
                        },
                    ]
                },
            },
        ],
    },
    "rich_text_editor": {
        "purpose": "Edit formatted text with a contenteditable area and toolbar",
        "keywords": (),
        "related": (),
        "label": "Rich Text Editor",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% rich_text_editor name="content" value="<p>Hello world</p>" %}',
            },
        ],
    },
    # ══════════════════════════════════════════════════════════════════════
    # Gallery examples for remaining template tags
    # ══════════════════════════════════════════════════════════════════════
    # ── Layout (additional) ──
    "app_shell": {
        "purpose": "App layout with sidebar, header and main content slots",
        "keywords": (),
        "related": (),
        "label": "App Shell",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    "{% app_shell %}"
                    "{% app_header %}<div>My App</div>{% endapp_header %}"
                    "{% app_sidebar %}<nav>Sidebar</nav>{% endapp_sidebar %}"
                    "{% app_content %}<main>Main content</main>{% endapp_content %}"
                    "{% endapp_shell %}"
                ),
            },
        ],
    },
    "sidebar": {
        "purpose": "Hierarchical navigation with active items and section headings",
        "keywords": (),
        "related": (),
        "label": "Sidebar",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% sidebar id="main-sidebar" title="Navigation" %}'
                    '{% sidebar_item label="Dashboard" icon="home" href="/" active=True %}{% endsidebar_item %}'
                    '{% sidebar_item label="Settings" icon="cog" href="/settings/" %}{% endsidebar_item %}'
                    "{% endsidebar %}"
                ),
            },
        ],
    },
    "aspect_ratio": {
        "purpose": "Keep wrapped content at a fixed width-to-height ratio",
        "keywords": (),
        "related": (),
        "label": "Aspect Ratio",
        "category": "layout",
        "variants": [
            {
                "name": "16/9",
                "template": '{% aspect_ratio ratio="16/9" %}<img src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%27320%27 height=%27180%27%3E%3Crect fill=%27%236B7280%27 width=%27320%27 height=%27180%27/%3E%3Ctext x=%2750%25%27 y=%2750%25%27 fill=%27white%27 text-anchor=%27middle%27 dy=%27.35em%27 font-size=%2716%27%3E320x180%3C/text%3E%3C/svg%3E" alt="Widescreen">{% endaspect_ratio %}',
            },
            {
                "name": "1/1",
                "template": '{% aspect_ratio ratio="1/1" %}<img src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%27200%27 height=%27200%27%3E%3Crect fill=%27%236B7280%27 width=%27200%27 height=%27200%27/%3E%3Ctext x=%2750%25%27 y=%2750%25%27 fill=%27white%27 text-anchor=%27middle%27 dy=%27.35em%27 font-size=%2716%27%3E200x200%3C/text%3E%3C/svg%3E" alt="Square">{% endaspect_ratio %}',
            },
        ],
    },
    "dashboard_grid": {
        "purpose": "Arrange dashboard panels in a configurable grid with move and resize events",
        "keywords": (),
        "related": (),
        "label": "Dashboard Grid",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": "{% dashboard_grid panels=panels columns=3 %}{% enddashboard_grid %}",
                "context": {
                    "panels": [
                        {
                            "id": "p1",
                            "title": "Revenue",
                            "content": "$12,340",
                            "col": 1,
                            "row": 1,
                            "width": 1,
                            "height": 1,
                        },
                        {
                            "id": "p2",
                            "title": "Users",
                            "content": "1,023",
                            "col": 2,
                            "row": 1,
                            "width": 1,
                            "height": 1,
                        },
                    ]
                },
            },
        ],
    },
    "masonry_grid": {
        "purpose": "Distribute variable-height items into balanced columns",
        "keywords": (),
        "related": (),
        "label": "Masonry Grid",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": "{% masonry_grid items=items columns=3 %}",
                "context": {
                    "items": [
                        {"height": 120, "content": "<p>Card 1</p>"},
                        {"height": 180, "content": "<p>Card 2</p>"},
                        {"height": 100, "content": "<p>Card 3</p>"},
                    ]
                },
            },
        ],
    },
    "resizable_panel": {
        "purpose": "Resize wrapped content using a drag handle",
        "keywords": (),
        "related": (),
        "label": "Resizable Panel",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% resizable_panel direction="horizontal" initial_size="50%" %}'
                    "<p>Resizable content</p>"
                    "{% endresizable_panel %}"
                ),
            },
        ],
    },
    "scroll_area": {
        "purpose": "Constrain content to a keyboard-focusable scrolling region",
        "keywords": (),
        "related": (),
        "label": "Scroll Area",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% scroll_area max_height="200px" %}'
                    "<p>Scrollable content line 1</p>"
                    "<p>Scrollable content line 2</p>"
                    "<p>Scrollable content line 3</p>"
                    "<p>Scrollable content line 4</p>"
                    "<p>Scrollable content line 5</p>"
                    "{% endscroll_area %}"
                ),
            },
        ],
    },
    "sticky_header": {
        "purpose": "Keep wrapped header content visible while scrolling",
        "keywords": (),
        "related": (),
        "label": "Sticky Header",
        "category": "layout",
        "variants": [
            {
                "name": "Default",
                "template": "{% sticky_header %}<h2>Sticky Section</h2>{% endsticky_header %}",
            },
        ],
    },
    # ── Form (additional) ──
    "autocomplete": {
        "purpose": "Text input with server-driven suggestions",
        "keywords": (),
        "related": (),
        "label": "Autocomplete",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% autocomplete name="city" label="City" placeholder="Type a city..." source_event="search_city" %}',
            },
        ],
    },
    "currency_input": {
        "purpose": "Enter a monetary value with currency formatting",
        "keywords": (),
        "related": (),
        "label": "Currency Input",
        "category": "form",
        "variants": [
            {
                "name": "USD",
                "template": '{% currency_input name="price" label="Price" currency="USD" value="49.99" %}',
            },
            {
                "name": "EUR",
                "template": '{% currency_input name="amount" label="Amount" currency="EUR" placeholder="0.00" %}',
            },
        ],
    },
    "cron_input": {
        "purpose": "Edit the five fields of a cron schedule with a description",
        "keywords": (),
        "related": (),
        "label": "Cron Input",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% cron_input name="schedule" value="0 9 * * 1" %}'},
        ],
    },
    "dependent_select": {
        "purpose": "Reload dropdown options when a parent field changes",
        "keywords": (),
        "related": (),
        "label": "Dependent Select",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dependent_select name="city" parent="country" source_event="load_cities" label="City" %}',
            },
        ],
    },
    "dj_form": {
        "purpose": "Render a Django form with field controls and validation messages",
        "keywords": (),
        "related": (),
        "label": "Form",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dj_form submit_label="Save" submit_event="save" %}',
            },
        ],
    },
    "dj_label": {
        "purpose": "Label a form control with an optional required marker",
        "keywords": (),
        "related": (),
        "label": "Label",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% dj_label for="email" %}Email Address{% enddj_label %}',
            },
            {
                "name": "Required",
                "template": '{% dj_label for="name" required=True %}Full Name{% enddj_label %}',
            },
        ],
    },
    "field_error": {
        "purpose": "Display validation errors for one Django form field",
        "keywords": (),
        "related": (),
        "label": "Field Error",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": "{% field_error field=field %}",
                "context": {"field": type("Field", (), {"errors": ["This field is required."]})()},
            },
        ],
    },
    "fieldset": {
        "purpose": "Group form controls under a legend with an optional description",
        "keywords": (),
        "related": (),
        "label": "Fieldset",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% fieldset legend="Account Details" %}'
                    '{% dj_input name="username" label="Username" %}'
                    "{% endfieldset %}"
                ),
            },
        ],
    },
    "form_array": {
        "purpose": "Add and remove repeated form rows within configured bounds",
        "keywords": (),
        "related": (),
        "label": "Form Array",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% form_array name="emails" rows=rows %}{% endform_array %}',
                "context": {"rows": [{"value": "alice@example.com"}, {"value": ""}]},
            },
        ],
    },
    "form_errors": {
        "purpose": "Display non-field validation errors from a Django form",
        "keywords": (),
        "related": (),
        "label": "Form Errors",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": "{% form_errors form=form %}",
                "context": {
                    "form": type(
                        "Form",
                        (),
                        {"non_field_errors": lambda self: ["Please correct the errors below."]},
                    )()
                },
            },
        ],
    },
    "image_cropper": {
        "purpose": "Select an image crop region with configurable aspect ratio",
        "keywords": (),
        "related": (),
        "label": "Image Cropper",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% image_cropper src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%27400%27 height=%27300%27%3E%3Crect fill=%27%236B7280%27 width=%27400%27 height=%27300%27/%3E%3Ctext x=%2750%25%27 y=%2750%25%27 fill=%27white%27 text-anchor=%27middle%27 dy=%27.35em%27 font-size=%2718%27%3E400x300%3C/text%3E%3C/svg%3E" crop_event="save_crop" aspect_ratio="16/9" %}',
            },
        ],
    },
    "image_upload_preview": {
        "purpose": "Upload multiple images with thumbnail previews and remove actions",
        "keywords": (),
        "related": (),
        "label": "Image Upload Preview",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% image_upload_preview name="photos" max=3 %}'},
        ],
    },
    "inline_edit": {
        "purpose": "Switch a text value between display and inline editing modes",
        "keywords": (),
        "related": (),
        "label": "Inline Edit",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% inline_edit value="Click to edit" event="save_field" field="title" %}',
            },
        ],
    },
    "input_group": {
        "purpose": "Combine an input with prefix or suffix addons",
        "keywords": (),
        "related": (),
        "label": "Input Group",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": (
                    "{% input_group %}"
                    '{% input_addon position="prefix" %}https://{% endinput_addon %}'
                    '{% dj_input name="domain" placeholder="example.com" %}'
                    "{% endinput_group %}"
                ),
            },
        ],
    },
    "markdown_editor": {
        "purpose": "Edit Markdown with formatting controls and a rendered preview",
        "keywords": (),
        "related": (),
        "label": "Markdown Editor",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% markdown_editor name="body" placeholder="Write markdown..." %}',
            },
        ],
    },
    "markdown_textarea": {
        "purpose": "Markdown textarea with formatting toolbar and preview mode",
        "keywords": (),
        "related": (),
        "label": "Markdown Textarea",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% markdown_textarea name="notes" placeholder="Write markdown here..." rows=4 %}',
            },
        ],
    },
    "mentions_input": {
        "purpose": "Text input with mention suggestions from supplied users",
        "keywords": (),
        "related": (),
        "label": "Mentions Input",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% mentions_input name="comment" users=users placeholder="Type @ to mention..." %}',
                "context": {
                    "users": [
                        {"id": "1", "name": "Alice", "avatar": ""},
                        {"id": "2", "name": "Bob", "avatar": ""},
                    ]
                },
            },
        ],
    },
    "multi_select": {
        "purpose": "Choose multiple options with search filtering and selected tags",
        "keywords": (),
        "related": (),
        "label": "Multi Select",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% multi_select name="tags" label="Tags" options=options %}',
                "context": {
                    "options": [
                        {"value": "python", "label": "Python"},
                        {"value": "django", "label": "Django"},
                        {"value": "rust", "label": "Rust"},
                    ]
                },
            },
        ],
    },
    "number_stepper": {
        "purpose": "Adjust a numeric value with increment and decrement buttons",
        "keywords": (),
        "related": (),
        "label": "Number Stepper",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% number_stepper name="qty" value=1 min_val=0 max_val=10 label="Quantity" %}',
            },
        ],
    },
    "otp_input": {
        "purpose": "Enter a one-time code using individual digit boxes",
        "keywords": (),
        "related": (),
        "label": "OTP Input",
        "category": "form",
        "variants": [
            {
                "name": "6-digit",
                "template": '{% otp_input name="code" digits=6 label="Verification Code" %}',
            },
        ],
    },
    "password_input": {
        "purpose": "Password input with visibility toggle and optional strength meter",
        "keywords": (),
        "related": (),
        "label": "Password Input",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% password_input name="password" label="Password" placeholder="Enter password" %}',
            },
            {
                "name": "With Strength",
                "template": '{% password_input name="password" label="Password" show_strength=True %}',
            },
        ],
    },
    "prompt_editor": {
        "purpose": "Edit a prompt template and preview substituted variables",
        "keywords": (),
        "related": (),
        "label": "Prompt Editor",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% prompt_editor template="Hello {name}, welcome to {service}!" event="save_prompt" %}',
            },
        ],
    },
    "rich_select": {
        "purpose": "Searchable dropdown options with icons, images and descriptions",
        "keywords": (),
        "related": (),
        "label": "Rich Select",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% rich_select name="framework" label="Framework" options=options searchable=True %}',
                "context": {
                    "options": [
                        {"value": "django", "label": "Django"},
                        {"value": "flask", "label": "Flask"},
                        {"value": "fastapi", "label": "FastAPI"},
                    ]
                },
            },
        ],
    },
    "search_input": {
        "purpose": "Search field with icon, clear button and loading indicator",
        "keywords": (),
        "related": (),
        "label": "Search Input",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% search_input name="q" placeholder="Search..." event="search" %}',
            },
        ],
    },
    "signature_pad": {
        "purpose": "Draw a signature on a canvas with clear and save actions",
        "keywords": (),
        "related": (),
        "label": "Signature Pad",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% signature_pad name="sig" save_event="save_signature" %}',
            },
        ],
    },
    "slider": {
        "purpose": "Choose a numeric value or range with a horizontal slider",
        "keywords": (),
        "related": (),
        "label": "Slider",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% slider name="volume" label="Volume" min=0 max=100 value=50 %}',
            },
            {
                "name": "Range",
                "template": '{% slider name="price" label="Price Range" min=0 max=1000 value=200 value_end=800 %}',
            },
        ],
    },
    "tag_input": {
        "purpose": "Create and remove tags in a text input",
        "keywords": (),
        "related": (),
        "label": "Tag Input",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% tag_input name="skills" tags=tags placeholder="Add skill..." %}',
                "context": {"tags": ["Python", "Django"]},
            },
        ],
    },
    "time_picker": {
        "purpose": "Choose hours and minutes with optional AM/PM selection",
        "keywords": (),
        "related": (),
        "label": "Time Picker",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% time_picker name="meeting_time" label="Meeting Time" value="14:30" %}',
            },
        ],
    },
    "toggle_group": {
        "purpose": "Choose one or multiple values from segmented toggle buttons",
        "keywords": (),
        "related": (),
        "label": "Toggle Group",
        "category": "form",
        "variants": [
            {
                "name": "Default",
                "template": '{% toggle_group name="view" options=options value="grid" %}',
                "context": {
                    "options": [
                        {"value": "list", "label": "List"},
                        {"value": "grid", "label": "Grid"},
                        {"value": "board", "label": "Board"},
                    ]
                },
            },
        ],
    },
    "voice_input": {
        "purpose": "Capture speech with a microphone control and configurable language",
        "keywords": (),
        "related": (),
        "label": "Voice Input",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% voice_input event="transcribe" lang="en-US" %}'},
        ],
    },
    # ── Overlay (additional) ──
    "bottom_sheet": {
        "purpose": "Show a dialog panel rising from the bottom of the viewport",
        "keywords": (),
        "related": (),
        "label": "Bottom Sheet",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": '{% bottom_sheet open=True title="Actions" %}Choose an option below.{% endbottom_sheet %}',
            },
        ],
    },
    "confirm_dialog": {
        "purpose": "Confirm or cancel an action in a titled dialog",
        "keywords": (),
        "related": (),
        "label": "Confirm Dialog",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": '{% confirm_dialog open=True title="Delete Item" message="This action cannot be undone." variant="danger" %}',
            },
        ],
    },
    "dropdown_menu": {
        "purpose": "Display action items and dividers under a menu trigger",
        "keywords": (),
        "related": (),
        "label": "Dropdown Menu",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% dropdown_menu label="Actions" open=True %}'
                    '{% menu_item label="Edit" event="edit" icon="pencil" %}'
                    "{% menu_divider %}"
                    '{% menu_item label="Delete" event="delete" danger=True %}'
                    "{% enddropdown_menu %}"
                ),
            },
        ],
    },
    "export_dialog": {
        "purpose": "Choose export format and columns in a dialog",
        "keywords": (),
        "related": (),
        "label": "Export Dialog",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": '{% export_dialog open=True formats=formats columns=columns title="Export Report" %}',
                "context": {
                    "formats": [{"id": "csv", "label": "CSV"}, {"id": "xlsx", "label": "Excel"}],
                    "columns": [
                        {"id": "name", "label": "Name", "checked": True},
                        {"id": "email", "label": "Email", "checked": True},
                    ],
                },
            },
        ],
    },
    "hover_card": {
        "purpose": "Reveal richer content in a card beside a hover trigger",
        "keywords": (),
        "related": (),
        "label": "Hover Card",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": '{% hover_card trigger="Hover me" position="bottom" %}Additional details shown on hover.{% endhover_card %}',
            },
        ],
    },
    "lightbox": {
        "purpose": "View images in an overlay with previous and next navigation",
        "keywords": (),
        "related": (),
        "label": "Lightbox",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": "{% lightbox images=images open=True active=0 %}",
                "context": {
                    "images": [
                        {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect fill='%234B5563' width='800' height='600'/%3E%3Ctext x='50%25' y='50%25' fill='white' text-anchor='middle' dy='.35em' font-size='24'%3E800x600%3C/text%3E%3C/svg%3E",
                            "alt": "Photo 1",
                            "caption": "First photo",
                        },
                        {
                            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect fill='%234B5563' width='800' height='600'/%3E%3Ctext x='50%25' y='50%25' fill='white' text-anchor='middle' dy='.35em' font-size='24'%3E800x600%3C/text%3E%3C/svg%3E",
                            "alt": "Photo 2",
                            "caption": "Second photo",
                        },
                    ]
                },
            },
        ],
    },
    "notification_popover": {
        "purpose": "Notification bell with a message popover and unread count",
        "keywords": (),
        "related": (),
        "label": "Notification Popover",
        "category": "overlay",
        "variants": [
            {
                "name": "Open",
                "template": "{% notification_popover notifications=notifs unread_count=1 open=True %}",
                "context": {
                    "notifs": [
                        {
                            "id": "1",
                            "title": "New comment",
                            "body": "Someone replied to your post.",
                            "time": "5m ago",
                            "read": False,
                        },
                    ]
                },
            },
        ],
    },
    "popconfirm": {
        "purpose": "Confirm an action in a small popover around its trigger",
        "keywords": (),
        "related": (),
        "label": "Popconfirm",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": '{% popconfirm message="Delete this record?" confirm_event="delete" %}Delete{% endpopconfirm %}',
            },
        ],
    },
    "cookie_consent": {
        "purpose": "Present cookie consent with accept and decline actions",
        "keywords": (),
        "related": (),
        "label": "Cookie Consent",
        "category": "overlay",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% cookie_consent message="We use cookies to improve your experience." accept_event="accept_cookies" privacy_url="/privacy/" %}'
                    "{% endcookie_consent %}"
                ),
            },
        ],
    },
    # ── Feedback (additional) ──
    "announcement_bar": {
        "purpose": "Page-wide announcement with optional dismiss action",
        "keywords": (),
        "related": (),
        "label": "Announcement Bar",
        "category": "feedback",
        "variants": [
            {
                "name": "Info",
                "template": '{% announcement_bar type="info" dismissible=True %}New version 2.0 is available!{% endannouncement_bar %}',
            },
        ],
    },
    "callout": {
        "purpose": "Highlight explanatory content with a semantic tone and optional icon",
        "keywords": (),
        "related": (),
        "label": "Callout",
        "category": "feedback",
        "variants": [
            {
                "name": "Default",
                "template": '{% callout title="Note" %}This is an important callout.{% endcallout %}',
            },
            {
                "name": "Warning",
                "template": '{% callout type="warning" title="Caution" %}Proceed carefully.{% endcallout %}',
            },
        ],
    },
    "connection_status": {
        "purpose": "Display connected, disconnected or reconnecting status",
        "keywords": (),
        "related": (),
        "label": "Connection Status",
        "category": "feedback",
        "variants": [
            {
                "name": "Default",
                "template": '{% connection_status reconnecting_text="Reconnecting..." connected_text="Connected" %}',
            },
        ],
    },
    "error_boundary": {
        "purpose": "Replace wrapped content with an error message and retry action",
        "keywords": (),
        "related": (),
        "label": "Error Boundary",
        "category": "feedback",
        "variants": [
            {
                "name": "Default",
                "template": '{% error_boundary fallback="Something went wrong" retry_event="retry" %}Protected content here.{% enderror_boundary %}',
            },
        ],
    },
    "error_page": {
        "purpose": "Display an error code, message and recovery actions",
        "keywords": (),
        "related": (),
        "label": "Error Page",
        "category": "feedback",
        "variants": [
            {
                "name": "404",
                "template": '{% error_page code=404 title="Page Not Found" message="The page you are looking for does not exist." %}',
            },
            {
                "name": "500",
                "template": '{% error_page code=500 title="Server Error" message="Something went wrong on our end." %}',
            },
        ],
    },
    "loading_overlay": {
        "purpose": "Cover wrapped content with a loading indicator while busy",
        "keywords": (),
        "related": (),
        "label": "Loading Overlay",
        "category": "feedback",
        "variants": [
            {
                "name": "Active",
                "template": '{% loading_overlay active=True text="Loading data..." %}Content behind overlay.{% endloading_overlay %}',
            },
        ],
    },
    "page_alert": {
        "purpose": "Page-level message with semantic styling and optional dismiss action",
        "keywords": (),
        "related": (),
        "label": "Page Alert",
        "category": "feedback",
        "variants": [
            {
                "name": "Info",
                "template": '{% page_alert type="info" dismissible=True %}Your account has been verified.{% endpage_alert %}',
            },
            {
                "name": "Warning",
                "template": '{% page_alert type="warning" %}Your subscription expires soon.{% endpage_alert %}',
            },
        ],
    },
    "progress_circle": {
        "purpose": "Visualize completion percentage in a circular progress indicator",
        "keywords": (),
        "related": (),
        "label": "Progress Circle",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": "{% progress_circle value=72 %}"},
            {"name": "Complete", "template": '{% progress_circle value=100 color="primary" %}'},
        ],
    },
    "server_toast_container": {
        "purpose": "Receive server toast notifications in a positioned live region",
        "keywords": ("notification", "flash", "message", "snackbar", "save"),
        "related": ("toast_container", "page_alert"),
        "label": "Server Toast Container",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": '{% server_toast_container position="top-right" %}'},
        ],
    },
    "skeleton_for": {
        "purpose": "Create table, card, list or text loading placeholders",
        "keywords": (),
        "related": (),
        "label": "Skeleton For",
        "category": "feedback",
        "variants": [
            {"name": "Table", "template": '{% skeleton_for component="table" columns=4 rows=5 %}'},
            {"name": "Text", "template": '{% skeleton_for component="text" %}'},
        ],
    },
    "thinking_indicator": {
        "purpose": "Show an animated thinking or processing status",
        "keywords": (),
        "related": (),
        "label": "Thinking Indicator",
        "category": "feedback",
        "variants": [
            {
                "name": "Thinking",
                "template": '{% thinking_indicator status="thinking" label="Generating response..." %}',
            },
        ],
    },
    # ── Data (additional) ──
    "activity_feed": {
        "purpose": "Display timestamped activity items with optional streaming updates",
        "keywords": (),
        "related": (),
        "label": "Activity Feed",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% activity_feed events=events %}",
                "context": {
                    "events": [
                        {
                            "user": "Alice",
                            "action": "created",
                            "target": "Project Alpha",
                            "time": "2m ago",
                            "icon": "plus",
                        },
                        {
                            "user": "Bob",
                            "action": "deployed",
                            "target": "v1.2.0",
                            "time": "1h ago",
                            "icon": "rocket",
                        },
                    ]
                },
            },
        ],
    },
    "audit_log": {
        "purpose": "Display user actions in a timestamped audit table with optional streaming",
        "keywords": (),
        "related": (),
        "label": "Audit Log",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% audit_log entries=entries %}",
                "context": {
                    "entries": [
                        {
                            "timestamp": "2026-03-25 10:00",
                            "user": "admin",
                            "action": "UPDATE",
                            "resource": "User #42",
                            "detail": "Changed role to editor",
                        },
                        {
                            "timestamp": "2026-03-25 09:30",
                            "user": "system",
                            "action": "CREATE",
                            "resource": "API Key",
                            "detail": "New key generated",
                        },
                    ]
                },
            },
        ],
    },
    "bar_chart": {
        "purpose": "Compare values using an SVG bar chart",
        "keywords": (),
        "related": (),
        "label": "Bar Chart",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% bar_chart data=data labels=labels title="Monthly Sales" %}',
                "context": {
                    "data": [120, 200, 150, 80, 240],
                    "labels": ["Jan", "Feb", "Mar", "Apr", "May"],
                },
            },
        ],
    },
    "calendar": {
        "purpose": "Display events on a navigable monthly calendar",
        "keywords": (),
        "related": (),
        "label": "Calendar",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% calendar year=2026 month=3 events=events %}",
                "context": {
                    "events": [
                        {"date": "2026-03-15", "title": "Sprint Review", "color": "blue"},
                        {"date": "2026-03-20", "title": "Release Day", "color": "green"},
                    ]
                },
            },
        ],
    },
    "calendar_heatmap": {
        "purpose": "Visualize daily activity intensity on an SVG calendar",
        "keywords": (),
        "related": (),
        "label": "Calendar Heatmap",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% calendar_heatmap data=data year=2026 title="Contributions" %}',
                "context": {
                    "data": {"2026-01-05": 3, "2026-01-12": 7, "2026-02-01": 5, "2026-03-10": 10}
                },
            },
        ],
    },
    "comparison_table": {
        "purpose": "Compare features across plans with an optional highlighted plan",
        "keywords": (),
        "related": (),
        "label": "Comparison Table",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% comparison_table plans=plans features=features %}",
                "context": {
                    "plans": [
                        {"name": "Free", "price": "$0/mo", "highlighted": False},
                        {"name": "Pro", "price": "$29/mo", "highlighted": True},
                    ],
                    "features": [
                        {"name": "Projects", "values": ["3", "Unlimited"]},
                        {"name": "Storage", "values": ["1 GB", "100 GB"]},
                    ],
                },
            },
        ],
    },
    "conversation_thread": {
        "purpose": "Display chat messages with roles and timestamps",
        "keywords": (),
        "related": (),
        "label": "Conversation Thread",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% conversation_thread messages=messages %}",
                "context": {
                    "messages": [
                        {
                            "sender": "user",
                            "name": "Alice",
                            "text": "How do I deploy?",
                            "time": "10:00 AM",
                        },
                        {
                            "sender": "assistant",
                            "name": "Bot",
                            "text": "Run `make deploy` from the project root.",
                            "time": "10:01 AM",
                        },
                    ]
                },
            },
        ],
    },
    "data_card_grid": {
        "purpose": "Display records as cards in a configurable column grid",
        "keywords": (),
        "related": (),
        "label": "Data Card Grid",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% data_card_grid items=items columns=2 %}",
                "context": {
                    "items": [
                        {
                            "title": "Widget A",
                            "description": "A useful widget.",
                            "category": "tools",
                        },
                        {
                            "title": "Widget B",
                            "description": "Another widget.",
                            "category": "tools",
                        },
                    ]
                },
            },
        ],
    },
    "data_grid": {
        "purpose": "Edit spreadsheet-style cells with keyboard navigation and frozen columns",
        "keywords": (),
        "related": (),
        "label": "Data Grid",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% data_grid columns=columns rows=rows striped=True %}",
                "context": {
                    "columns": [
                        {"key": "name", "label": "Name", "editable": False},
                        {"key": "email", "label": "Email", "editable": True},
                    ],
                    "rows": [
                        {"id": "1", "name": "Alice", "email": "alice@example.com"},
                        {"id": "2", "name": "Bob", "email": "bob@example.com"},
                    ],
                },
            },
        ],
    },
    "description_list": {
        "purpose": "Display labelled values in a definition list",
        "keywords": (),
        "related": (),
        "label": "Description List",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% description_list items=items %}",
                "context": {
                    "items": [
                        {"term": "Name", "detail": "Alice Johnson"},
                        {"term": "Role", "detail": "Administrator"},
                        {"term": "Status", "detail": "Active"},
                    ]
                },
            },
        ],
    },
    "diff_viewer": {
        "purpose": "Compare text changes in unified or side-by-side views",
        "keywords": (),
        "related": (),
        "label": "Diff Viewer",
        "category": "data",
        "variants": [
            {
                "name": "Split",
                "template": '{% diff_viewer old=old_text new=new_text mode="split" %}',
                "context": {
                    "old_text": "Hello World\nFoo Bar",
                    "new_text": "Hello World\nFoo Baz",
                },
            },
        ],
    },
    "file_tree": {
        "purpose": "Browse files and folders with expansion and selection actions",
        "keywords": (),
        "related": (),
        "label": "File Tree",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% file_tree nodes=nodes event="select_file" %}',
                "context": {
                    "nodes": [
                        {
                            "id": "1",
                            "label": "src/",
                            "children": [
                                {"id": "2", "label": "main.py", "children": []},
                                {"id": "3", "label": "utils.py", "children": []},
                            ],
                        },
                        {"id": "4", "label": "README.md", "children": []},
                    ]
                },
            },
        ],
    },
    "gantt_chart": {
        "purpose": "Visualize task start and end dates on a project timeline",
        "keywords": (),
        "related": (),
        "label": "Gantt Chart",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% gantt_chart tasks=tasks title="Project Timeline" %}',
                "context": {
                    "tasks": [
                        {"name": "Design", "start": 0, "duration": 3, "progress": 100},
                        {"name": "Development", "start": 2, "duration": 5, "progress": 60},
                        {"name": "Testing", "start": 6, "duration": 2, "progress": 0},
                    ]
                },
            },
        ],
    },
    "heatmap": {
        "purpose": "Visualize matrix values using color intensity",
        "keywords": (),
        "related": (),
        "label": "Heatmap",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% heatmap data=data x_labels=x_labels y_labels=y_labels title="Activity" %}',
                "context": {
                    "data": [[1, 5, 3], [8, 2, 6]],
                    "x_labels": ["Mon", "Wed", "Fri"],
                    "y_labels": ["Morning", "Afternoon"],
                },
            },
        ],
    },
    "json_viewer": {
        "purpose": "Inspect nested JSON with expandable nodes",
        "keywords": (),
        "related": (),
        "label": "JSON Viewer",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% json_viewer data=data %}",
                "context": {
                    "data": {
                        "name": "djust",
                        "version": "0.4.0",
                        "features": ["LiveView", "VDOM", "Components"],
                    }
                },
            },
        ],
    },
    "line_chart": {
        "purpose": "Plot one or more series as an SVG line chart",
        "keywords": (),
        "related": (),
        "label": "Line Chart",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% line_chart series=series labels=labels title="Performance" %}',
                "context": {
                    "series": [{"name": "Requests", "data": [100, 150, 120, 200, 180]}],
                    "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                },
            },
        ],
    },
    "log_viewer": {
        "purpose": "Display log lines with severity filtering and optional streaming",
        "keywords": (),
        "related": (),
        "label": "Log Viewer",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% log_viewer lines=lines show_line_numbers=True %}",
                "context": {
                    "lines": [
                        "[INFO] Server started on port 8000",
                        "[DEBUG] Loading configuration...",
                        "[WARN] Deprecated setting detected",
                        "[INFO] Ready to accept connections",
                    ]
                },
            },
        ],
    },
    "model_table": {
        "purpose": "Render a Django queryset table with inferred model columns and filters",
        "keywords": (),
        "related": (),
        "label": "Model Table",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% model_table queryset=rows include=include striped=True %}",
                "context": {
                    "rows": [
                        {"id": 1, "name": "Widget", "price": "$9.99"},
                        {"id": 2, "name": "Gadget", "price": "$19.99"},
                    ],
                    "include": ["name", "price"],
                },
            },
        ],
    },
    "org_chart": {
        "purpose": "Display a hierarchical organization tree with node selection",
        "keywords": (),
        "related": (),
        "label": "Org Chart",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% org_chart nodes=nodes root="ceo" %}',
                "context": {
                    "nodes": [
                        {"id": "ceo", "label": "CEO", "parent": ""},
                        {"id": "cto", "label": "CTO", "parent": "ceo"},
                        {"id": "cfo", "label": "CFO", "parent": "ceo"},
                    ]
                },
            },
        ],
    },
    "pie_chart": {
        "purpose": "Show proportions in an SVG pie or donut chart",
        "keywords": (),
        "related": (),
        "label": "Pie Chart",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% pie_chart segments=segments title="Traffic Sources" %}',
                "context": {
                    "segments": [
                        {"label": "Direct", "value": 40},
                        {"label": "Search", "value": 35},
                        {"label": "Social", "value": 25},
                    ]
                },
            },
            {
                "name": "Donut",
                "template": '{% pie_chart segments=segments title="Revenue" donut=True %}',
                "context": {
                    "segments": [
                        {"label": "Product", "value": 60},
                        {"label": "Services", "value": 40},
                    ]
                },
            },
        ],
    },
    "pivot_table": {
        "purpose": "Aggregate records by row and column groups with totals",
        "keywords": (),
        "related": (),
        "label": "Pivot Table",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% pivot_table data=data rows="region" cols="quarter" values="sales" agg="sum" %}',
                "context": {
                    "data": [
                        {"region": "North", "quarter": "Q1", "sales": 100},
                        {"region": "North", "quarter": "Q2", "sales": 150},
                        {"region": "South", "quarter": "Q1", "sales": 200},
                        {"region": "South", "quarter": "Q2", "sales": 175},
                    ]
                },
            },
        ],
    },
    "sortable_grid": {
        "purpose": "Reorder items in a grid using drag handles",
        "keywords": (),
        "related": (),
        "label": "Sortable Grid",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% sortable_grid items=items columns=3 move_event="reorder" %}',
                "context": {
                    "items": [
                        {"id": "1", "label": "Item A"},
                        {"id": "2", "label": "Item B"},
                        {"id": "3", "label": "Item C"},
                    ]
                },
            },
        ],
    },
    "sortable_list": {
        "purpose": "Reorder list items with drag handles",
        "keywords": (),
        "related": (),
        "label": "Sortable List",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% sortable_list items=items move_event="reorder" %}',
                "context": {
                    "items": [
                        {"id": "1", "label": "First item"},
                        {"id": "2", "label": "Second item"},
                        {"id": "3", "label": "Third item"},
                    ]
                },
            },
        ],
    },
    "sparkline": {
        "purpose": "Show a compact SVG line chart for a value series",
        "keywords": (),
        "related": (),
        "label": "Sparkline",
        "category": "data",
        "variants": [
            {
                "name": "Line",
                "template": '{% sparkline data=data variant="line" %}',
                "context": {"data": [10, 25, 15, 30, 20, 35]},
            },
            {
                "name": "Bar",
                "template": '{% sparkline data=data variant="bar" %}',
                "context": {"data": [5, 12, 8, 20, 15]},
            },
        ],
    },
    "terminal": {
        "purpose": "Display terminal output with ANSI colors and optional streaming",
        "keywords": (),
        "related": (),
        "label": "Terminal",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% terminal output=output title="Console" show_line_numbers=True %}',
                "context": {
                    "output": [
                        "$ python manage.py runserver",
                        "Watching for file changes with StatReloader",
                        "Starting development server at http://127.0.0.1:8000/",
                    ]
                },
            },
        ],
    },
    "treemap": {
        "purpose": "Show relative values as proportional SVG rectangles",
        "keywords": (),
        "related": (),
        "label": "Treemap",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": '{% treemap data=data title="Disk Usage" %}',
                "context": {
                    "data": [
                        {"name": "Documents", "size": 450},
                        {"name": "Photos", "size": 300},
                        {"name": "Videos", "size": 800},
                        {"name": "Code", "size": 200},
                    ]
                },
            },
        ],
    },
    # ── Navigation (additional) ──
    "breadcrumb_dropdown": {
        "purpose": "Navigate breadcrumb levels with sibling dropdown options",
        "keywords": (),
        "related": (),
        "label": "Breadcrumb Dropdown",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": "{% breadcrumb_dropdown items=items max_visible=3 %}",
                "context": {
                    "items": [
                        {"label": "Home", "url": "/"},
                        {"label": "Products", "url": "/products/"},
                        {"label": "Electronics", "url": "/products/electronics/"},
                        {"label": "Phones", "url": "/products/electronics/phones/"},
                        {"label": "iPhone 15"},
                    ]
                },
            },
        ],
    },
    "nav_menu": {
        "purpose": "Navigate using labelled links with active state and dropdown children",
        "keywords": (),
        "related": (),
        "label": "Nav Menu",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% nav_menu id="main-nav" brand="MyApp" active="home" %}'
                    '{% nav_item id="home" label="Home" href="/" %}Home{% endnav_item %}'
                    '{% nav_item id="about" label="About" href="/about/" %}About{% endnav_item %}'
                    "{% endnav_menu %}"
                ),
            },
        ],
    },
    "page_header": {
        "purpose": "Page title with subtitle, breadcrumbs and an actions slot",
        "keywords": (),
        "related": (),
        "label": "Page Header",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% page_header title="Dashboard" subtitle="Overview of your account" %}'
                    '{% page_header_actions %}{% dj_button label="New Project" variant="primary" %}{% endpage_header_actions %}'
                    "{% endpage_header %}"
                ),
            },
        ],
    },
    "scroll_spy": {
        "purpose": "Navigate sections with an active-section indicator",
        "keywords": (),
        "related": (),
        "label": "Scroll Spy",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": '{% scroll_spy sections=sections active="intro" %}',
                "context": {
                    "sections": [
                        {"id": "intro", "label": "Introduction"},
                        {"id": "features", "label": "Features"},
                        {"id": "pricing", "label": "Pricing"},
                    ]
                },
            },
        ],
    },
    "toolbar": {
        "purpose": "Group action controls with separators and an overflow menu",
        "keywords": (),
        "related": (),
        "label": "Toolbar",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": (
                    "{% toolbar %}"
                    '{% dj_button label="Bold" size="sm" %}'
                    "{% toolbar_separator %}"
                    '{% dj_button label="Italic" size="sm" %}'
                    "{% endtoolbar %}"
                ),
            },
        ],
    },
    "wizard": {
        "purpose": "Guide users through labelled steps with back and next actions",
        "keywords": (),
        "related": (),
        "label": "Wizard",
        "category": "navigation",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% wizard steps=steps active="step1" %}'
                    "<p>Step 1 content goes here.</p>"
                    "{% endwizard %}"
                ),
                "context": {
                    "steps": [
                        {"id": "step1", "label": "Account"},
                        {"id": "step2", "label": "Profile"},
                        {"id": "step3", "label": "Confirm"},
                    ]
                },
            },
        ],
    },
    # ── Indicator (additional) ──
    "animated_number": {
        "purpose": "Animate transitions between numeric values",
        "keywords": (),
        "related": (),
        "label": "Animated Number",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": '{% animated_number value=1234 prefix="$" duration=800 %}',
            },
            {
                "name": "Percentage",
                "template": '{% animated_number value=97 suffix="%" decimals=1 %}',
            },
        ],
    },
    "avatar_group": {
        "purpose": "Display overlapping user avatars with an overflow count",
        "keywords": (),
        "related": (),
        "label": "Avatar Group",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": "{% avatar_group users=users max=3 %}",
                "context": {
                    "users": [
                        {"name": "Alice", "src": ""},
                        {"name": "Bob", "src": ""},
                        {"name": "Carol", "src": ""},
                        {"name": "Dave", "src": ""},
                    ]
                },
            },
        ],
    },
    "countdown": {
        "purpose": "Show time remaining until a target date",
        "keywords": (),
        "related": (),
        "label": "Countdown",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% countdown target="2026-12-31T23:59:59" %}'},
        ],
    },
    "icon": {
        "purpose": "Display a named SVG icon with configurable size",
        "keywords": (),
        "related": (),
        "label": "Icon",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% icon name="check" %}'},
            {"name": "Large", "template": '{% icon name="star" size="lg" %}'},
        ],
    },
    "live_counter": {
        "purpose": "Display a numeric counter with an optional live update event",
        "keywords": (),
        "related": (),
        "label": "Live Counter",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% live_counter value=42 label="Online Users" %}'},
        ],
    },
    "live_indicator": {
        "purpose": "Indicate live activity with a status dot and optional pulse",
        "keywords": (),
        "related": (),
        "label": "Live Indicator",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": '{% live_indicator user=user field="document" action="typing" active=True %}',
                "context": {"user": {"name": "Alice", "avatar": ""}},
            },
        ],
    },
    "meter": {
        "purpose": "Show a bounded value with configurable threshold colors",
        "keywords": (),
        "related": (),
        "label": "Meter",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": '{% meter segments=segments total=100 label="Storage" %}',
                "context": {
                    "segments": [
                        {"value": 40, "color": "#3B82F6", "label": "Documents"},
                        {"value": 25, "color": "#10B981", "label": "Photos"},
                        {"value": 15, "color": "#F59E0B", "label": "Other"},
                    ]
                },
            },
        ],
    },
    "notification_badge": {
        "purpose": "Show an unread count attached to wrapped content",
        "keywords": (),
        "related": (),
        "label": "Notification Badge",
        "category": "indicator",
        "variants": [
            {"name": "Count", "template": "{% notification_badge count=5 %}"},
            {"name": "Overflow", "template": "{% notification_badge count=150 max=99 %}"},
            {"name": "Dot", "template": "{% notification_badge dot=True pulse=True %}"},
        ],
    },
    "presence_avatars": {
        "purpose": "Show online users as avatars with an overflow count",
        "keywords": (),
        "related": (),
        "label": "Presence Avatars",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": "{% presence_avatars users=users max=4 %}",
                "context": {
                    "users": [
                        {"name": "Alice", "status": "online"},
                        {"name": "Bob", "status": "away"},
                        {"name": "Carol", "status": "online"},
                    ]
                },
            },
        ],
    },
    "qr_code": {
        "purpose": "Generate a QR code for supplied text or a URL",
        "keywords": (),
        "related": (),
        "label": "QR Code",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% qr_code data="https://djust.org" size="md" %}'},
        ],
    },
    "relative_time": {
        "purpose": "Display a timestamp as relative elapsed time",
        "keywords": (),
        "related": (),
        "label": "Relative Time",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% relative_time datetime="2026-03-25T10:00:00Z" %}'},
        ],
    },
    "ribbon": {
        "purpose": "Attach a labelled corner ribbon to wrapped content",
        "keywords": (),
        "related": (),
        "label": "Ribbon",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% ribbon text="New" variant="primary" %}'},
            {
                "name": "Sale",
                "template": '{% ribbon text="Sale" variant="danger" position="top-left" %}',
            },
        ],
    },
    "segmented_progress": {
        "purpose": "Display completion across multiple labelled progress segments",
        "keywords": (),
        "related": (),
        "label": "Segmented Progress",
        "category": "indicator",
        "variants": [
            {
                "name": "Default",
                "template": "{% segmented_progress steps=steps current=2 %}",
                "context": {
                    "steps": [
                        {"label": "Upload"},
                        {"label": "Process"},
                        {"label": "Complete"},
                    ]
                },
            },
        ],
    },
    "status_indicator": {
        "purpose": "Display a labelled status with a colored indicator",
        "keywords": (),
        "related": (),
        "label": "Status Indicator",
        "category": "indicator",
        "variants": [
            {"name": "Online", "template": '{% status_indicator status="online" label="Server" %}'},
            {
                "name": "Offline",
                "template": '{% status_indicator status="offline" label="Database" %}',
            },
            {
                "name": "Warning",
                "template": '{% status_indicator status="warning" label="Cache" pulse=True %}',
            },
        ],
    },
    "token_counter": {
        "purpose": "Show token usage against a configurable budget",
        "keywords": (),
        "related": (),
        "label": "Token Counter",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": "{% token_counter current=1500 max=4096 %}"},
        ],
    },
    # ── Typography (additional) ──
    "code_snippet": {
        "purpose": "Display compact code with language label and copy action",
        "keywords": (),
        "related": (),
        "label": "Code Snippet",
        "category": "typography",
        "variants": [
            {
                "name": "Python",
                "template": '{% code_snippet code="print(\'Hello, World!\')" language="python" %}',
            },
        ],
    },
    "copyable_text": {
        "purpose": "Display a text value with a clipboard copy control",
        "keywords": (),
        "related": (),
        "label": "Copyable Text",
        "category": "typography",
        "variants": [
            {
                "name": "Default",
                "template": "{% copyable_text %}pip install djust{% endcopyable_text %}",
            },
        ],
    },
    "expandable_text": {
        "purpose": "Truncate long text with expand and collapse controls",
        "keywords": (),
        "related": (),
        "label": "Expandable Text",
        "category": "typography",
        "variants": [
            {
                "name": "Default",
                "template": (
                    "{% expandable_text max_lines=2 %}"
                    "This is a long paragraph of text that will be truncated after a few lines. "
                    "Click the button to expand and read the full content of this text block."
                    "{% endexpandable_text %}"
                ),
            },
        ],
    },
    "streaming_text": {
        "purpose": "Display streamed text with an optional typing cursor",
        "keywords": (),
        "related": (),
        "label": "Streaming Text",
        "category": "typography",
        "variants": [
            {
                "name": "Default",
                "template": '{% streaming_text text="The response is being generated..." cursor=True %}',
            },
        ],
    },
    "truncated_list": {
        "purpose": "Limit visible items and show the remaining count",
        "keywords": (),
        "related": (),
        "label": "Truncated List",
        "category": "data",
        "variants": [
            {
                "name": "Default",
                "template": "{% truncated_list items=items max=2 %}",
                "context": {
                    "items": [
                        {"name": "Alice"},
                        {"name": "Bob"},
                        {"name": "Carol"},
                        {"name": "Dave"},
                    ]
                },
            },
        ],
    },
    # ── Misc (additional) ──
    "agent_step": {
        "purpose": "Show an agent action with status, duration and expandable details",
        "keywords": (),
        "related": (),
        "label": "Agent Step",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% agent_step tool="web_search" status="complete" duration="1.2s" %}Found 3 relevant results.{% endagent_step %}',
            },
        ],
    },
    "approval_gate": {
        "purpose": "Present an action for approval or rejection with a risk indicator",
        "keywords": (),
        "related": (),
        "label": "Approval Gate",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% approval_gate message="Deploy to production?" risk="high" approve_event="approve" reject_event="reject" %}',
            },
        ],
    },
    "await": {
        "purpose": "Show loading or error feedback until wrapped content is ready",
        "keywords": (),
        "related": (),
        "label": "Await",
        "category": "misc",
        "variants": [
            {
                "name": "Loading",
                "template": "{% await loaded=False %}Loading...{% endawait %}",
            },
            {
                "name": "Loaded",
                "template": "{% await loaded=True %}Data is ready.{% endawait %}",
            },
        ],
    },
    "chat_bubble": {
        "purpose": "Display a chat message with role, timestamp and delivery status",
        "keywords": (),
        "related": (),
        "label": "Chat Bubble",
        "category": "misc",
        "variants": [
            {
                "name": "User",
                "template": "{% chat_bubble message=msg %}",
                "context": {
                    "msg": {
                        "sender": "user",
                        "name": "Alice",
                        "text": "Hello there!",
                        "time": "10:00 AM",
                    }
                },
            },
            {
                "name": "Assistant",
                "template": "{% chat_bubble message=msg %}",
                "context": {
                    "msg": {
                        "sender": "assistant",
                        "name": "Bot",
                        "text": "How can I help?",
                        "time": "10:01 AM",
                    }
                },
            },
        ],
    },
    "collab_selection": {
        "purpose": "Display other users' text selection highlights",
        "keywords": (),
        "related": (),
        "label": "Collab Selection",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": "{% collab_selection users=users %}",
                "context": {
                    "users": [
                        {
                            "name": "Alice",
                            "color": "#3B82F6",
                            "text": "selected text",
                            "start": 0,
                            "end": 13,
                        },
                    ]
                },
            },
        ],
    },
    "cursors": {
        "purpose": "Overlay named cursors for collaborating users",
        "keywords": (),
        "related": (),
        "label": "Cursors",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": "{% cursors users=users %}",
                "context": {
                    "users": [
                        {"name": "Alice", "color": "#3B82F6", "x": 100, "y": 200},
                        {"name": "Bob", "color": "#10B981", "x": 300, "y": 150},
                    ]
                },
            },
        ],
    },
    "fab": {
        "purpose": "Floating action button with optional secondary speed-dial actions",
        "keywords": (),
        "related": (),
        "label": "Floating Action Button",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% fab icon="+" event="create" label="Create" %}'},
        ],
    },
    "feedback": {
        "purpose": "Collect feedback using thumbs, stars or emoji choices",
        "keywords": (),
        "related": (),
        "label": "Feedback Widget",
        "category": "misc",
        "variants": [
            {"name": "Thumbs", "template": '{% feedback event="rate_response" mode="thumbs" %}'},
            {"name": "Stars", "template": '{% feedback event="rate_response" mode="stars" %}'},
        ],
    },
    "filter_bar": {
        "purpose": "Combine search, select and date-range filters with a clear action",
        "keywords": ("table", "filters"),
        "related": (),
        "label": "Filter Bar",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": (
                    '{% filter_bar event="filter_change" %}'
                    '{% filter_select name="status" label="Status" options=options %}'
                    '{% filter_search name="q" placeholder="Search..." %}'
                    "{% endfilter_bar %}"
                ),
                "context": {
                    "options": [
                        {"value": "active", "label": "Active"},
                        {"value": "archived", "label": "Archived"},
                    ]
                },
            },
        ],
    },
    "import_wizard": {
        "purpose": "Guide file import through upload, column mapping and preview steps",
        "keywords": (),
        "related": (),
        "label": "Import Wizard",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% import_wizard accepted_formats=".csv" model_fields=fields event="import_data" %}',
                "context": {
                    "fields": [
                        {"id": "name", "label": "Name"},
                        {"id": "email", "label": "Email"},
                    ]
                },
            },
        ],
    },
    "infinite_scroll": {
        "purpose": "Load more list items when a scrolling sentinel becomes visible",
        "keywords": (),
        "related": (),
        "label": "Infinite Scroll",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% infinite_scroll load_event="load_more" %}<p>Item 1</p><p>Item 2</p>{% endinfinite_scroll %}',
            },
        ],
    },
    "map_picker": {
        "purpose": "Choose a geographic position on an interactive map",
        "keywords": (),
        "related": (),
        "label": "Map Picker",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% map_picker lat=40.7128 lng=-74.0060 zoom=13 pick_event="set_location" %}',
            },
        ],
    },
    "model_selector": {
        "purpose": "Choose an AI model with provider, tier and pricing details",
        "keywords": (),
        "related": (),
        "label": "Model Selector",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% model_selector name="model" options=options value="gpt-4" label="Model" %}',
                "context": {
                    "options": [
                        {"value": "gpt-4", "label": "GPT-4"},
                        {"value": "claude", "label": "Claude"},
                    ]
                },
            },
        ],
    },
    "multimodal_input": {
        "purpose": "Compose a message with text, file and voice controls",
        "keywords": (),
        "related": (),
        "label": "Multimodal Input",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% multimodal_input name="message" event="send" accept_files=True %}',
            },
        ],
    },
    "reactions": {
        "purpose": "Display emoji reaction counts with toggle actions",
        "keywords": (),
        "related": (),
        "label": "Reactions",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% reactions options=options counts=counts event="react" %}',
                "context": {
                    "options": [
                        {"emoji": "thumbsup", "label": "Like"},
                        {"emoji": "heart", "label": "Love"},
                    ],
                    "counts": {"thumbsup": 5, "heart": 2},
                },
            },
        ],
    },
    "responsive_image": {
        "purpose": "Display an image with srcset, lazy loading and an optional placeholder",
        "keywords": (),
        "related": (),
        "label": "Responsive Image",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% responsive_image src="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 width=%27800%27 height=%27400%27%3E%3Crect fill=%27%234B5563%27 width=%27800%27 height=%27400%27/%3E%3Ctext x=%2750%25%27 y=%2750%25%27 fill=%27white%27 text-anchor=%27middle%27 dy=%27.35em%27 font-size=%2724%27%3E800x400%3C/text%3E%3C/svg%3E" alt="Hero image" aspect_ratio="2/1" %}',
            },
        ],
    },
    "scroll_to_top": {
        "purpose": "Scroll the page back to the top using a floating button",
        "keywords": (),
        "related": (),
        "label": "Scroll to Top",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% scroll_to_top label="Back to top" %}'},
        ],
    },
    "source_citation": {
        "purpose": "Display a numbered source reference with title and link",
        "keywords": (),
        "related": (),
        "label": "Source Citation",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% source_citation index=1 title="Wikipedia: Django" url="https://en.wikipedia.org/wiki/Django" %}',
            },
        ],
    },
    "split_button": {
        "purpose": "Primary action button with a dropdown of secondary actions",
        "keywords": (),
        "related": (),
        "label": "Split Button",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% split_button label="Save" event="save" options=options %}',
                "context": {
                    "options": [
                        {"label": "Save as Draft", "event": "save_draft"},
                        {"label": "Save & Publish", "event": "save_publish"},
                    ]
                },
            },
        ],
    },
    "theme_toggle": {
        "purpose": "Choose light, dark or system color mode",
        "keywords": (),
        "related": (),
        "label": "Theme Toggle",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": '{% theme_toggle current="system" event="set_theme" %}',
            },
        ],
    },
    "tour": {
        "purpose": "Guide users through positioned onboarding steps",
        "keywords": (),
        "related": (),
        "label": "Tour",
        "category": "misc",
        "variants": [
            {
                "name": "Default",
                "template": "{% tour steps=steps active=0 %}",
                "context": {
                    "steps": [
                        {
                            "target": "#header",
                            "title": "Welcome",
                            "content": "This is the main header.",
                        },
                        {
                            "target": "#sidebar",
                            "title": "Navigation",
                            "content": "Browse sections here.",
                        },
                    ]
                },
            },
        ],
    },
}


# ─── Component Class Examples ───
# For Python component classes (Badge, Button, Card, StatusDot, Markdown)
# Each variant has a 'render' callable that returns HTML.


def _component_class(name: str) -> Any:
    """Import rendering classes only when a gallery variant is rendered."""
    from djust.components import components

    return getattr(components, name)


def _make_class_examples() -> Dict[str, Any]:
    """Build CLASS_EXAMPLES lazily to avoid import-time issues with djust stubs."""
    return {
        "Badge": {
            "purpose": "Create a styled status badge from Python",
            "keywords": (),
            "related": (),
            "snippet": 'Badge("Running", variant="success")',
            "label": "Badge (Class)",
            "category": "indicator",
            "variants": [
                {
                    "name": "Status Running",
                    "render": lambda: _component_class("Badge").status("running")._render_custom(),
                },
                {
                    "name": "Status Error",
                    "render": lambda: _component_class("Badge").status("error")._render_custom(),
                },
                {
                    "name": "Priority P0",
                    "render": lambda: _component_class("Badge").priority("P0")._render_custom(),
                },
                {
                    "name": "Priority P3",
                    "render": lambda: _component_class("Badge").priority("P3")._render_custom(),
                },
            ],
        },
        "Button": {
            "purpose": "Create a styled action button from Python",
            "keywords": (),
            "related": (),
            "snippet": 'Button("Save", variant="primary")',
            "label": "Button (Class)",
            "category": "form",
            "variants": [
                {
                    "name": "Primary",
                    "render": lambda: _component_class("Button")(
                        "Save", variant="primary"
                    )._render_custom(),
                },
                {
                    "name": "Danger",
                    "render": lambda: _component_class("Button")(
                        "Delete", variant="danger"
                    )._render_custom(),
                },
                {
                    "name": "Loading",
                    "render": lambda: _component_class("Button")(
                        "Wait...", loading=True
                    )._render_custom(),
                },
            ],
        },
        "Card": {
            "purpose": "Create a titled content card from Python",
            "keywords": (),
            "related": (),
            "snippet": 'Card(header="Overview", content="Details")',
            "label": "Card (Class)",
            "category": "layout",
            "variants": [
                {
                    "name": "Default",
                    "render": lambda: _component_class("Card")(
                        content="<p>Card content</p>"
                    )._render_custom(),
                },
                {
                    "name": "Elevated",
                    "render": lambda: _component_class("Card")(
                        content="<p>Elevated</p>", variant="elevated"
                    )._render_custom(),
                },
            ],
        },
        "StatusDot": {
            "purpose": "Create a colored status dot with an optional pulse from Python",
            "keywords": (),
            "related": (),
            "snippet": 'StatusDot("running")',
            "label": "StatusDot (Class)",
            "category": "indicator",
            "variants": [
                {
                    "name": "Running",
                    "render": lambda: _component_class("StatusDot")("running")._render_custom(),
                },
                {
                    "name": "Stopped",
                    "render": lambda: _component_class("StatusDot")("stopped")._render_custom(),
                },
                {
                    "name": "Completed",
                    "render": lambda: _component_class("StatusDot")("completed")._render_custom(),
                },
            ],
        },
        "Markdown": {
            "purpose": "Render sanitized Markdown content from Python",
            "keywords": (),
            "related": (),
            "snippet": 'Markdown(text="**Hello**")',
            "label": "Markdown (Class)",
            "category": "typography",
            "variants": [
                {
                    "name": "Simple",
                    "render": lambda: _component_class("Markdown")(
                        "**Bold** and *italic* text."
                    )._render_custom(),
                },
                {
                    "name": "Code",
                    "render": lambda: _component_class("Markdown")(
                        "Inline `code` and:\n\n```python\nprint('hello')\n```"
                    )._render_custom(),
                },
            ],
        },
    }


# Lazy singleton
_class_examples_cache: Dict[str, Any] | None = None


def _get_class_examples() -> Dict[str, Any]:
    """Return the CLASS_EXAMPLES dict, building it on first call.

    Deferred so that ``djust_components.components`` (which imports ``djust``)
    is not loaded at module import time -- important for test environments that
    stub out the ``djust`` module.
    """
    global _class_examples_cache
    if _class_examples_cache is None:
        _class_examples_cache = _make_class_examples()
    return _class_examples_cache


class _ClassExamplesProxy:
    """Dict-like proxy that lazily loads CLASS_EXAMPLES on first access.

    Implements the ``Mapping`` protocol (``__getitem__``, ``__contains__``,
    ``__iter__``, ``__len__``, ``keys``, ``values``, ``items``, ``get``)
    so it can be used anywhere a regular dict is expected.
    """

    def __getitem__(self, key: str) -> Any:
        return _get_class_examples()[key]

    def __contains__(self, key: object) -> bool:
        return key in _get_class_examples()

    def __iter__(self) -> Iterator[str]:
        return iter(_get_class_examples())

    def __len__(self) -> int:
        return len(_get_class_examples())

    def keys(self) -> Any:
        return _get_class_examples().keys()

    def values(self) -> Any:
        return _get_class_examples().values()

    def items(self) -> Any:
        return _get_class_examples().items()

    def get(self, key: str, default: Any = None) -> Any:
        return _get_class_examples().get(key, default)


CLASS_EXAMPLES = _ClassExamplesProxy()


# Child tags are discoverable through their parent catalog entry.
CHILD_TAGS: Dict[str, str] = {
    "accordion_item": "accordion",
    "app_content": "app_shell",
    "app_header": "app_shell",
    "app_sidebar": "app_shell",
    "context_menu_item": "context_menu",
    "filter_date_range": "filter_bar",
    "filter_search": "filter_bar",
    "filter_select": "filter_bar",
    "input_addon": "input_group",
    "menu_divider": "dropdown_menu",
    "menu_item": "dropdown_menu",
    "nav_item": "nav_menu",
    "page_header_actions": "page_header",
    "palette_item": "command_palette",
    "sidebar_item": "sidebar",
    "sidebar_section": "sidebar",
    "tab": "tabs",
    "timeline_item": "timeline",
    "toolbar_overflow": "toolbar",
    "toolbar_separator": "toolbar",
}
