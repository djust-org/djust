"""Example data definitions for every component in the gallery.

To add a new component to the gallery:
1. Add an entry to EXAMPLES (for template tags) or CLASS_EXAMPLES (for component classes)
2. Include at least one variant with a 'name' and 'template' (or 'render' for classes)
3. Set the 'category' to one of the keys in CATEGORIES
4. Run tests to verify: .venv/bin/python -m pytest tests/test_gallery.py -v
"""

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

EXAMPLES = {
    # ── Layout ──
    "modal": {
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
        "label": "Switch",
        "category": "form",
        "variants": [
            {"name": "Off", "template": '{% switch name="dark" label="Dark Mode" %}'},
            {"name": "On", "template": '{% switch name="dark" label="Dark Mode" checked=True %}'},
        ],
    },
    "color_picker": {
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
        "label": "Date Picker",
        "category": "form",
        "variants": [
            {"name": "Default", "template": "{% date_picker year=2026 month=3 %}"},
        ],
    },
    "file_dropzone": {
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
        "label": "Progress",
        "category": "feedback",
        "variants": [
            {"name": "25%", "template": "{% progress 25 %}"},
            {"name": "75%", "template": "{% progress 75 %}"},
            {"name": "100%", "template": "{% progress 100 %}"},
        ],
    },
    "spinner": {
        "label": "Spinner",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": "{% spinner %}"},
            {"name": "Small", "template": '{% spinner size="sm" %}'},
            {"name": "Large", "template": '{% spinner size="lg" %}'},
        ],
    },
    "skeleton": {
        "label": "Skeleton",
        "category": "feedback",
        "variants": [
            {"name": "Text", "template": '{% skeleton skeleton_type="text" lines=3 %}'},
            {"name": "Circle", "template": '{% skeleton skeleton_type="circle" %}'},
            {"name": "Rectangle", "template": '{% skeleton skeleton_type="rect" %}'},
        ],
    },
    "empty_state": {
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
        "label": "Pagination",
        "category": "data",
        "variants": [
            {"name": "Default", "template": "{% pagination page=3 total_pages=10 %}"},
        ],
    },
    "virtual_list": {
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
        "label": "Avatar",
        "category": "indicator",
        "variants": [
            {"name": "Initials", "template": '{% avatar initials="JD" alt="John Doe" %}'},
            {"name": "With Status", "template": '{% avatar initials="AB" status="online" %}'},
            {"name": "Large", "template": '{% avatar initials="XY" size="lg" %}'},
        ],
    },
    "rating": {
        "label": "Rating",
        "category": "indicator",
        "variants": [
            {"name": "3 of 5", "template": "{% rating value=3 %}"},
            {"name": "Readonly", "template": "{% rating value=4 readonly=True %}"},
        ],
    },
    "gauge": {
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
        "label": "Kbd",
        "category": "typography",
        "variants": [
            {"name": "Single", "template": '{% kbd "Ctrl" %}'},
            {"name": "Combo", "template": '{% kbd "Ctrl" "C" %}'},
        ],
    },
    # ── Misc ──
    "dj_tag": {
        "label": "Tag",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% dj_tag label="python" %}'},
            {"name": "Dismissible", "template": '{% dj_tag label="removable" dismissible=True %}'},
        ],
    },
    "dj_divider": {
        "label": "Divider",
        "category": "misc",
        "variants": [
            {"name": "Horizontal", "template": "{% dj_divider %}"},
            {"name": "With Label", "template": '{% dj_divider label="OR" %}'},
        ],
    },
    "carousel": {
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
        "label": "Copy Button",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% copy_button text="Copied text here" %}'},
        ],
    },
    "notification_center": {
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
        "label": "Cron Input",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% cron_input name="schedule" value="0 9 * * 1" %}'},
        ],
    },
    "dependent_select": {
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
        "label": "Image Upload Preview",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% image_upload_preview name="photos" max=3 %}'},
        ],
    },
    "inline_edit": {
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
        "label": "Voice Input",
        "category": "form",
        "variants": [
            {"name": "Default", "template": '{% voice_input event="transcribe" lang="en-US" %}'},
        ],
    },
    # ── Overlay (additional) ──
    "bottom_sheet": {
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
        "label": "Progress Circle",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": "{% progress_circle value=72 %}"},
            {"name": "Complete", "template": '{% progress_circle value=100 color="primary" %}'},
        ],
    },
    "server_toast_container": {
        "label": "Server Toast Container",
        "category": "feedback",
        "variants": [
            {"name": "Default", "template": '{% server_toast_container position="top-right" %}'},
        ],
    },
    "skeleton_for": {
        "label": "Skeleton For",
        "category": "feedback",
        "variants": [
            {"name": "Table", "template": '{% skeleton_for component="table" columns=4 rows=5 %}'},
            {"name": "Text", "template": '{% skeleton_for component="text" %}'},
        ],
    },
    "thinking_indicator": {
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
        "label": "Countdown",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% countdown target="2026-12-31T23:59:59" %}'},
        ],
    },
    "icon": {
        "label": "Icon",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% icon name="check" %}'},
            {"name": "Large", "template": '{% icon name="star" size="lg" %}'},
        ],
    },
    "live_counter": {
        "label": "Live Counter",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% live_counter value=42 label="Online Users" %}'},
        ],
    },
    "live_indicator": {
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
        "label": "Notification Badge",
        "category": "indicator",
        "variants": [
            {"name": "Count", "template": "{% notification_badge count=5 %}"},
            {"name": "Overflow", "template": "{% notification_badge count=150 max=99 %}"},
            {"name": "Dot", "template": "{% notification_badge dot=True pulse=True %}"},
        ],
    },
    "presence_avatars": {
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
        "label": "QR Code",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% qr_code data="https://djust.org" size="md" %}'},
        ],
    },
    "relative_time": {
        "label": "Relative Time",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": '{% relative_time datetime="2026-03-25T10:00:00Z" %}'},
        ],
    },
    "ribbon": {
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
        "label": "Token Counter",
        "category": "indicator",
        "variants": [
            {"name": "Default", "template": "{% token_counter current=1500 max=4096 %}"},
        ],
    },
    # ── Typography (additional) ──
    "code_snippet": {
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
        "label": "Floating Action Button",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% fab icon="+" event="create" label="Create" %}'},
        ],
    },
    "feedback": {
        "label": "Feedback Widget",
        "category": "misc",
        "variants": [
            {"name": "Thumbs", "template": '{% feedback event="rate_response" mode="thumbs" %}'},
            {"name": "Stars", "template": '{% feedback event="rate_response" mode="stars" %}'},
        ],
    },
    "filter_bar": {
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
        "label": "Scroll to Top",
        "category": "misc",
        "variants": [
            {"name": "Default", "template": '{% scroll_to_top label="Back to top" %}'},
        ],
    },
    "source_citation": {
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


def _make_class_examples():
    """Build CLASS_EXAMPLES lazily to avoid import-time issues with djust stubs."""
    from djust.components.components import Badge, Button, Card, Markdown, StatusDot

    return {
        "Badge": {
            "label": "Badge (Class)",
            "category": "indicator",
            "variants": [
                {
                    "name": "Status Running",
                    "render": lambda: Badge.status("running")._render_custom(),
                },
                {"name": "Status Error", "render": lambda: Badge.status("error")._render_custom()},
                {"name": "Priority P0", "render": lambda: Badge.priority("P0")._render_custom()},
                {"name": "Priority P3", "render": lambda: Badge.priority("P3")._render_custom()},
            ],
        },
        "Button": {
            "label": "Button (Class)",
            "category": "form",
            "variants": [
                {
                    "name": "Primary",
                    "render": lambda: Button("Save", variant="primary")._render_custom(),
                },
                {
                    "name": "Danger",
                    "render": lambda: Button("Delete", variant="danger")._render_custom(),
                },
                {
                    "name": "Loading",
                    "render": lambda: Button("Wait...", loading=True)._render_custom(),
                },
            ],
        },
        "Card": {
            "label": "Card (Class)",
            "category": "layout",
            "variants": [
                {
                    "name": "Default",
                    "render": lambda: Card(content="<p>Card content</p>")._render_custom(),
                },
                {
                    "name": "Elevated",
                    "render": lambda: Card(
                        content="<p>Elevated</p>", variant="elevated"
                    )._render_custom(),
                },
            ],
        },
        "StatusDot": {
            "label": "StatusDot (Class)",
            "category": "indicator",
            "variants": [
                {"name": "Running", "render": lambda: StatusDot("running")._render_custom()},
                {"name": "Stopped", "render": lambda: StatusDot("stopped")._render_custom()},
                {"name": "Completed", "render": lambda: StatusDot("completed")._render_custom()},
            ],
        },
        "Markdown": {
            "label": "Markdown (Class)",
            "category": "typography",
            "variants": [
                {
                    "name": "Simple",
                    "render": lambda: Markdown("**Bold** and *italic* text.")._render_custom(),
                },
                {
                    "name": "Code",
                    "render": lambda: Markdown(
                        "Inline `code` and:\n\n```python\nprint('hello')\n```"
                    )._render_custom(),
                },
            ],
        },
    }


# Lazy singleton
_class_examples_cache = None


def _get_class_examples():
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

    def __getitem__(self, key):
        return _get_class_examples()[key]

    def __contains__(self, key):
        return key in _get_class_examples()

    def __iter__(self):
        return iter(_get_class_examples())

    def __len__(self):
        return len(_get_class_examples())

    def keys(self):
        return _get_class_examples().keys()

    def values(self):
        return _get_class_examples().values()

    def items(self):
        return _get_class_examples().items()

    def get(self, key, default=None):
        return _get_class_examples().get(key, default)


CLASS_EXAMPLES = _ClassExamplesProxy()
