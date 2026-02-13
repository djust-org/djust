"""
Schema extraction for the djust framework.

Provides machine-readable metadata about framework directives, lifecycle methods,
decorators, and project-specific LiveView introspection. Used by:
- ``python manage.py djust_schema`` — JSON schema output
- ``python manage.py djust_ai_context`` — AI-friendly project documentation
- MCP server for IDE integration
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ============================================================================
# Framework Schema — static metadata, no Django setup required
# ============================================================================

#: Complete registry of dj-* template directives
DIRECTIVES: List[Dict[str, Any]] = [
    # --- Event directives (send server events) ---
    {
        "name": "dj-click",
        "category": "event",
        "description": "Send event to server on click",
        "value": "handler_name or handler_name('arg1', arg2)",
        "dom_event": "click",
        "example": '<button dj-click="increment">+1</button>',
        "params_sent": ["data-* attributes", "positional args from handler syntax"],
        "modifiers": [],
    },
    {
        "name": "dj-submit",
        "category": "event",
        "description": "Send form data to server on submit. Calls e.preventDefault() and e.target.reset().",
        "value": "handler_name",
        "dom_event": "submit",
        "example": '<form dj-submit="create_item"><input name="title"><button>Add</button></form>',
        "params_sent": ["all form field name/value pairs"],
        "modifiers": [],
    },
    {
        "name": "dj-change",
        "category": "event",
        "description": "Send event on input change (fires on blur for text, immediately for checkboxes/selects)",
        "value": "handler_name",
        "dom_event": "change",
        "example": '<select dj-change="filter_by"><option>All</option></select>',
        "params_sent": ["value", "field"],
        "modifiers": [],
    },
    {
        "name": "dj-input",
        "category": "event",
        "description": "Send event on every keystroke (auto-debounced 300ms for text, throttled for range/number)",
        "value": "handler_name",
        "dom_event": "input",
        "example": '<input dj-input="search" name="query">',
        "params_sent": ["value", "field"],
        "modifiers": [],
        "related_attributes": ["data-debounce", "data-throttle"],
    },
    {
        "name": "dj-blur",
        "category": "event",
        "description": "Send event when element loses focus",
        "value": "handler_name",
        "dom_event": "blur",
        "example": '<input dj-blur="validate_field" name="email">',
        "params_sent": ["value", "field"],
        "modifiers": [],
    },
    {
        "name": "dj-focus",
        "category": "event",
        "description": "Send event when element receives focus",
        "value": "handler_name",
        "dom_event": "focus",
        "example": '<input dj-focus="track_focus" name="search">',
        "params_sent": ["value", "field"],
        "modifiers": [],
    },
    {
        "name": "dj-keydown",
        "category": "event",
        "description": "Send event on keydown. Supports key modifiers (e.g., dj-keydown.enter).",
        "value": "handler_name or handler_name.enter",
        "dom_event": "keydown",
        "example": '<input dj-keydown.enter="submit_search">',
        "params_sent": ["key", "code", "value", "field"],
        "modifiers": ["enter", "escape", "space"],
    },
    {
        "name": "dj-keyup",
        "category": "event",
        "description": "Send event on keyup. Supports key modifiers like dj-keydown.",
        "value": "handler_name or handler_name.escape",
        "dom_event": "keyup",
        "example": '<input dj-keyup.escape="clear_search">',
        "params_sent": ["key", "code", "value", "field"],
        "modifiers": ["enter", "escape", "space"],
    },
    {
        "name": "dj-poll",
        "category": "event",
        "description": "Declarative polling: periodically send event to server. Pauses when tab is hidden.",
        "value": "handler_name",
        "dom_event": "timer (setInterval)",
        "example": '<div dj-poll="refresh_data" dj-poll-interval="5000">...</div>',
        "params_sent": ["data-* attributes"],
        "modifiers": [],
        "related_attributes": ["dj-poll-interval"],
    },
    # --- Two-way binding ---
    {
        "name": "dj-model",
        "category": "binding",
        "description": "Two-way data binding between form element and server state. "
        "Syncs on 'input' event by default.",
        "value": "field_name",
        "dom_event": "input",
        "example": '<input type="text" dj-model="search_query">',
        "params_sent": ["field", "value"],
        "modifiers": ["lazy", "debounce-N"],
        "modifier_details": {
            "lazy": "Sync on 'change' event (blur) instead of every keystroke",
            "debounce-N": "Debounce by N milliseconds (e.g., dj-model.debounce-500)",
        },
    },
    # --- DOM update control ---
    {
        "name": "dj-update",
        "category": "dom",
        "description": "Control how server HTML updates are applied to this element",
        "value": "append | prepend | replace | ignore",
        "example": '<ul dj-update="append" id="messages">{% for msg in messages %}<li>{{ msg }}</li>{% endfor %}</ul>',
        "note": "Element MUST have an id attribute. Use with temporary_assigns for memory-efficient lists.",
    },
    {
        "name": "dj-target",
        "category": "dom",
        "description": "Scope the server re-render to a specific element (CSS selector)",
        "value": "CSS selector (e.g., #sidebar, .panel)",
        "example": '<button dj-click="refresh_sidebar" dj-target="#sidebar">Refresh</button>',
    },
    # --- Loading states ---
    {
        "name": "dj-loading.disable",
        "category": "loading",
        "description": "Disable element while event is in flight",
        "value": "(no value needed)",
        "example": '<button dj-click="save" dj-loading.disable>Save</button>',
    },
    {
        "name": "dj-loading.class",
        "category": "loading",
        "description": "Add CSS class while event is in flight",
        "value": "class_name",
        "example": '<button dj-click="save" dj-loading.class="opacity-50">Save</button>',
    },
    {
        "name": "dj-loading.show",
        "category": "loading",
        "description": "Show element (set display) while event is in flight",
        "value": "display_value (default: 'block')",
        "example": '<div dj-loading.show="flex" style="display:none">Loading...</div>',
    },
    {
        "name": "dj-loading.hide",
        "category": "loading",
        "description": "Hide element while event is in flight",
        "value": "(no value needed)",
        "example": "<div dj-loading.hide>Normal content</div>",
    },
    # --- Client-side actions (no server round-trip) ---
    {
        "name": "dj-copy",
        "category": "client",
        "description": "Copy attribute value to clipboard on click (client-only, no server event)",
        "value": "text to copy (can use template variables)",
        "example": '<button dj-copy="{{ api_key }}">Copy Key</button>',
    },
    {
        "name": "dj-confirm",
        "category": "modifier",
        "description": "Show browser confirm() dialog before sending event. Cancels if user declines.",
        "value": "confirmation message",
        "example": '<button dj-click="delete_item" dj-confirm="Are you sure?">Delete</button>',
    },
    # --- Hooks (client JS lifecycle) ---
    {
        "name": "dj-hook",
        "category": "hooks",
        "description": "Attach a client-side JS hook to this element. Hook receives mounted/updated/destroyed callbacks.",
        "value": "HookName (registered in window.djust.hooks)",
        "example": '<canvas dj-hook="MyChart" data-values="{{ chart_data }}"></canvas>',
        "hook_callbacks": [
            "mounted",
            "updated",
            "destroyed",
            "disconnected",
            "reconnected",
            "beforeUpdate",
        ],
        "hook_api": [
            "this.el",
            "this.viewName",
            "this.pushEvent(event, payload)",
            "this.handleEvent(event, callback)",
        ],
    },
    # --- Navigation ---
    {
        "name": "dj-patch",
        "category": "navigation",
        "description": "Update URL params without remounting the view (client-side pushState + server url_change)",
        "value": "URL path or query string",
        "example": '<a dj-patch="?page=2&sort=name">Page 2</a>',
    },
    {
        "name": "dj-navigate",
        "category": "navigation",
        "description": "Navigate to a different LiveView over the existing WebSocket (no page reload)",
        "value": "URL path",
        "example": '<a dj-navigate="/items/42/">View Item</a>',
    },
    # --- Streaming ---
    {
        "name": "dj-stream",
        "category": "streaming",
        "description": "Mark element as a stream target for server-pushed DOM operations",
        "value": "stream_name",
        "example": '<ul dj-stream="messages">{% for msg in streams.messages %}<li>{{ msg }}</li>{% endfor %}</ul>',
    },
    {
        "name": "dj-stream-mode",
        "category": "streaming",
        "description": "Default insertion mode for streaming text content",
        "value": "append | replace | prepend",
        "example": '<div dj-stream="output" dj-stream-mode="append"></div>',
    },
    # --- File uploads ---
    {
        "name": "dj-upload",
        "category": "upload",
        "description": "Bind a file input to an upload slot (binary WebSocket upload)",
        "value": "upload_slot_name",
        "example": '<input type="file" dj-upload="avatar">',
    },
    {
        "name": "dj-upload-drop",
        "category": "upload",
        "description": "Mark element as a drag-and-drop zone for file uploads",
        "value": "upload_slot_name",
        "example": '<div dj-upload-drop="attachments">Drop files here</div>',
    },
    {
        "name": "dj-upload-preview",
        "category": "upload",
        "description": "Container for image upload previews (auto-populated with thumbnails)",
        "value": "upload_slot_name",
        "example": '<div dj-upload-preview="avatar"></div>',
    },
    {
        "name": "dj-upload-progress",
        "category": "upload",
        "description": "Container for upload progress bars",
        "value": "upload_slot_name",
        "example": '<div dj-upload-progress="attachments"></div>',
    },
    # --- Supplementary attributes (used alongside event directives) ---
    {
        "name": "dj-poll-interval",
        "category": "modifier",
        "description": "Polling interval in milliseconds for dj-poll (default: 5000)",
        "value": "milliseconds",
        "example": '<div dj-poll="refresh" dj-poll-interval="3000">...</div>',
    },
]

#: Data attribute type coercion suffixes for passing typed params
DATA_ATTRIBUTE_TYPES = {
    "int": 'Parse as integer (data-count:int="42" -> 42)',
    "float": 'Parse as float (data-price:float="19.99" -> 19.99)',
    "bool": 'Parse as boolean (data-enabled:bool="true" -> True)',
    "json": 'Parse as JSON (data-tags:json=\'["a","b"]\' -> ["a","b"])',
    "list": 'Split by comma (data-items:list="a,b,c" -> ["a","b","c"])',
}

#: LiveView lifecycle methods
LIFECYCLE_METHODS: List[Dict[str, Any]] = [
    {
        "name": "mount",
        "signature": "def mount(self, request, **kwargs):",
        "description": "Called once when the view is first loaded (HTTP GET) and when "
        "WebSocket connects. Initialize state here.",
        "phase": "initialization",
        "required": False,
    },
    {
        "name": "get_context_data",
        "signature": "def get_context_data(self, **kwargs) -> dict:",
        "description": "Return dict of template context variables. By default, all public "
        "(non-underscore) attributes are included automatically.",
        "phase": "rendering",
        "required": False,
    },
    {
        "name": "handle_params",
        "signature": "def handle_params(self, params: dict, uri: str):",
        "description": "Called when URL params change (via live_patch or browser back/forward). "
        "Override to update state from URL.",
        "phase": "navigation",
        "required": False,
    },
    {
        "name": "handle_tick",
        "signature": "def handle_tick(self):",
        "description": "Called periodically when tick_interval is set (in ms). "
        "Use for polling or periodic updates.",
        "phase": "lifecycle",
        "required": False,
    },
    {
        "name": "unmount",
        "signature": "def unmount(self):",
        "description": "Called when WebSocket disconnects. Clean up resources here.",
        "phase": "teardown",
        "required": False,
    },
    {
        "name": "connected",
        "signature": "def connected(self):",
        "description": "Called when WebSocket connection is established after initial HTTP load.",
        "phase": "lifecycle",
        "required": False,
    },
    {
        "name": "disconnected",
        "signature": "def disconnected(self):",
        "description": "Called when WebSocket connection is lost (before unmount).",
        "phase": "lifecycle",
        "required": False,
    },
]

#: Class-level configuration attributes
CLASS_ATTRIBUTES: List[Dict[str, Any]] = [
    {
        "name": "template_name",
        "type": "Optional[str]",
        "description": "Path to Django template file",
        "example": "template_name = 'myapp/counter.html'",
    },
    {
        "name": "template",
        "type": "Optional[str]",
        "description": "Inline template string (alternative to template_name)",
        "example": 'template = "<div>{{ count }}</div>"',
    },
    {
        "name": "use_actors",
        "type": "bool",
        "default": "False",
        "description": "Enable Tokio actor-based state management for high-concurrency views",
    },
    {
        "name": "tick_interval",
        "type": "Optional[int]",
        "description": "Periodic tick interval in milliseconds (e.g., 2000 for 2s polling)",
    },
    {
        "name": "temporary_assigns",
        "type": "Dict[str, Any]",
        "default": "{}",
        "description": "Assigns to clear from server memory after each render. "
        "Values are the reset defaults. Use with dj-update='append' in template.",
        "example": "temporary_assigns = {'messages': [], 'feed_items': []}",
    },
    {
        "name": "login_required",
        "type": "Optional[bool]",
        "description": "Require authenticated user. Set to True to protect, False to acknowledge public.",
    },
    {
        "name": "permission_required",
        "type": "Optional[Union[str, List[str]]]",
        "description": "Django permission string(s) required to access this view",
    },
    {
        "name": "login_url",
        "type": "Optional[str]",
        "description": "Override settings.LOGIN_URL for this view's auth redirect",
    },
]

#: Decorator metadata
DECORATORS: List[Dict[str, Any]] = [
    {
        "name": "@event_handler",
        "import": "from djust.decorators import event_handler",
        "description": "Mark method as an event handler with automatic signature introspection. "
        "Extracts params, types, and descriptions from the function signature.",
        "params": {
            "params": "Optional[List[str]] — explicit parameter list (overrides auto-extraction)",
            "description": "str — human-readable description (overrides docstring)",
            "coerce_types": "bool — auto-coerce string params to expected types (default: True)",
        },
        "usage": [
            "@event_handler\ndef search(self, value: str = '', **kwargs):",
            "@event_handler(description='Update quantity')\ndef update_item(self, item_id: int, quantity: int, **kwargs):",
        ],
    },
    {
        "name": "@debounce",
        "import": "from djust.decorators import debounce",
        "description": "Client-side debounce: wait N seconds after last event before triggering handler.",
        "params": {
            "wait": "float — seconds to wait (default: 0.3)",
            "max_wait": "Optional[float] — max seconds before forcing execution",
        },
        "usage": ["@debounce(wait=0.5)\ndef search(self, query: str = '', **kwargs):"],
    },
    {
        "name": "@throttle",
        "import": "from djust.decorators import throttle",
        "description": "Client-side throttle: limit handler to once per interval.",
        "params": {
            "interval": "float — minimum interval in seconds (default: 0.1)",
            "leading": "bool — execute on leading edge (default: True)",
            "trailing": "bool — execute on trailing edge (default: True)",
        },
        "usage": ["@throttle(interval=0.1)\ndef on_scroll(self, scroll_y: int = 0, **kwargs):"],
    },
    {
        "name": "@optimistic",
        "import": "from djust.decorators import optimistic",
        "description": "Client-side optimistic update: UI updates instantly, server corrects if needed.",
        "params": {},
        "usage": ["@optimistic\ndef toggle_todo(self, todo_id: int = 0, **kwargs):"],
    },
    {
        "name": "@cache",
        "import": "from djust.decorators import cache",
        "description": "Client-side response caching with TTL.",
        "params": {
            "ttl": "int — cache time-to-live in seconds (default: 60)",
            "key_params": "Optional[List[str]] — params to include in cache key",
        },
        "usage": [
            "@cache(ttl=60, key_params=['query'])\ndef search(self, query: str = '', **kwargs):"
        ],
    },
    {
        "name": "@client_state",
        "import": "from djust.decorators import client_state",
        "description": "Share state via client-side StateBus (pub/sub). "
        "When handler executes, specified keys are published.",
        "params": {
            "keys": "List[str] — state keys to publish/subscribe",
        },
        "usage": [
            "@client_state(keys=['filter'])\ndef update_filter(self, filter: str = '', **kwargs):",
        ],
    },
    {
        "name": "@rate_limit",
        "import": "from djust.decorators import rate_limit",
        "description": "Server-side rate limiting using token bucket algorithm.",
        "params": {
            "rate": "float — tokens per second (default: 10)",
            "burst": "int — maximum burst capacity (default: 5)",
        },
        "usage": [
            "@rate_limit(rate=5, burst=3)\n@event_handler\ndef expensive_op(self, **kwargs):",
        ],
    },
    {
        "name": "@permission_required",
        "import": "from djust.decorators import permission_required",
        "description": "Require Django permission(s) to call this handler. "
        "Checked server-side before execution.",
        "params": {
            "perm": "Union[str, List[str]] — Django permission string(s)",
        },
        "usage": [
            '@permission_required("myapp.delete_item")\n@event_handler()\ndef delete_item(self, item_id: int, **kwargs):',
        ],
    },
    {
        "name": "@reactive",
        "import": "from djust.decorators import reactive",
        "description": "Create a reactive property that triggers re-render on change.",
        "params": {},
        "usage": [
            "@reactive\ndef count(self):\n    return self._count",
        ],
    },
    {
        "name": "state()",
        "import": "from djust.decorators import state",
        "description": "Descriptor for reactive state. Cleaner than setting attributes in mount(). "
        "Automatically included in context and triggers re-renders.",
        "params": {
            "default": "Any — default value for the state property",
        },
        "usage": [
            "count = state(default=0)\nmessage = state(default='Hello')",
        ],
    },
    {
        "name": "@computed",
        "import": "from djust.decorators import computed",
        "description": "Computed property derived from state. Available in templates, "
        "auto-recalculated when state changes.",
        "params": {},
        "usage": [
            "@computed\ndef count_doubled(self):\n    return self.count * 2",
        ],
    },
]

#: Server-side navigation methods available on LiveView instances
NAVIGATION_METHODS: List[Dict[str, str]] = [
    {
        "name": "live_patch",
        "signature": "self.live_patch(params=None, path=None, replace=False)",
        "description": "Update browser URL without remounting. Triggers handle_params().",
    },
    {
        "name": "live_redirect",
        "signature": "self.live_redirect(path, params=None, replace=False)",
        "description": "Navigate to a different LiveView over the existing WebSocket (no page reload).",
    },
]

#: Stream methods available on LiveView instances
STREAM_METHODS: List[Dict[str, str]] = [
    {
        "name": "stream",
        "signature": "self.stream(name, items)",
        "description": "Initialize a stream collection. Items are available as streams.<name> in template.",
    },
    {
        "name": "stream_insert",
        "signature": "self.stream_insert(name, item, at=-1)",
        "description": "Insert item into stream. at=-1 appends, at=0 prepends.",
    },
    {
        "name": "stream_delete",
        "signature": "self.stream_delete(name, item_or_id)",
        "description": "Delete item from stream by object or ID.",
    },
]

#: Push event method
PUSH_EVENT_METHODS: List[Dict[str, str]] = [
    {
        "name": "push_event",
        "signature": "self.push_event(event_name, payload=None)",
        "description": "Push a custom event to the client. "
        "Client receives via dj-hook handleEvent() or window event listener.",
    },
]

#: Optional mixins users can add to their LiveView
OPTIONAL_MIXINS: List[Dict[str, Any]] = [
    {
        "name": "PresenceMixin",
        "import": "from djust.presence import PresenceMixin",
        "description": "Track which users are currently viewing this page. "
        "Provides presence list and cursor tracking.",
    },
    {
        "name": "FormMixin",
        "import": "from djust.forms import FormMixin",
        "description": "Real-time form validation with Django forms. "
        "Validates on blur/change and shows inline errors.",
    },
    {
        "name": "TenantMixin",
        "import": "from djust.tenants import TenantMixin",
        "description": "Multi-tenant support via Django Channels groups.",
    },
    {
        "name": "TenantScopedMixin",
        "import": "from djust.tenants import TenantScopedMixin",
        "description": "Scope DB queries to current tenant automatically.",
    },
    {
        "name": "PWAMixin",
        "import": "from djust.pwa import PWAMixin",
        "description": "Progressive Web App support (service worker, offline).",
    },
    {
        "name": "OfflineMixin",
        "import": "from djust.pwa import OfflineMixin",
        "description": "Offline-first support with queue and sync.",
    },
    {
        "name": "SyncMixin",
        "import": "from djust.sync import SyncMixin",
        "description": "Cross-tab state synchronization.",
    },
]

#: Public/private variable convention
CONVENTIONS = {
    "public_private": {
        "description": "Variables starting with _ are private (not exposed to templates). "
        "All other attributes are automatically included in template context via "
        "get_context_data().",
        "examples": {
            "self.count": "Public — available as {{ count }} in template",
            "self._internal_cache": "Private — not exposed to template",
        },
    },
    "jit_serialization": {
        "description": "Django model instances and QuerySets are automatically serialized "
        "to dicts/lists using JIT (just-in-time) serialization. Only fields "
        "actually used in the template are serialized for performance.",
    },
    "handler_naming": {
        "description": "Event handlers are called by the exact name in the dj-* attribute. "
        "Use @event_handler decorator for validation and metadata. Methods named "
        "handle_*, on_*, toggle_*, update_*, etc. without @event_handler trigger "
        "a system check warning (djust.V004).",
    },
}


# ============================================================================
# Best Practices — comprehensive reference for AI code generation
# ============================================================================

BEST_PRACTICES = {
    "setup": {
        "description": "Minimal setup for a djust project",
        "settings": (
            "INSTALLED_APPS = ['djust', ...]\n" "ASGI_APPLICATION = 'myproject.asgi.application'"
        ),
        "asgi": (
            "from djust.routing import live_session\n"
            "from channels.routing import ProtocolTypeRouter, URLRouter\n"
            "from channels.auth import AuthMiddlewareStack\n"
            "application = ProtocolTypeRouter({\n"
            "    'http': get_asgi_application(),\n"
            "    'websocket': AuthMiddlewareStack(URLRouter([\n"
            "        live_session('app/', include('myapp.urls')),\n"
            "    ])),\n"
            "})"
        ),
        "urls": (
            "from djust.routing import live_session\n"
            "urlpatterns = [live_session('myview/', MyView, name='myview')]"
        ),
    },
    "lifecycle": {
        "flow": (
            "mount() -> _refresh() -> get_context_data() -> template renders "
            "-> event -> handler -> _refresh() -> get_context_data() -> re-render"
        ),
        "example": (
            "class ItemListView(LiveView):\n"
            "    template_name = 'items/list.html'\n"
            "\n"
            "    def mount(self, request, **kwargs):\n"
            "        self.search = ''\n"
            "        self.filter = 'all'\n"
            "        self._refresh()\n"
            "\n"
            "    def _refresh(self):\n"
            "        qs = Item.objects.all()\n"
            "        if self.search:\n"
            "            qs = qs.filter(name__icontains=self.search)\n"
            "        self._items = qs  # PRIVATE variable\n"
            "\n"
            "    def get_context_data(self, **kwargs):\n"
            "        self.items = self._items  # PUBLIC = triggers Rust JIT\n"
            "        return super().get_context_data(**kwargs)\n"
            "\n"
            "    @event_handler()\n"
            "    @debounce(wait=0.5)\n"
            "    def search_items(self, value: str = '', **kwargs):\n"
            "        self.search = value\n"
            "        self._refresh()"
        ),
    },
    "event_handlers": {
        "rules": [
            "All handlers MUST use @event_handler() decorator",
            "All handlers MUST accept **kwargs",
            "All handler params MUST have default values",
            "Input/change events use 'value' parameter name",
            "Button data attributes: data-item-id='5' -> item_id=5",
            "Form submission: all fields as kwargs",
        ],
        "examples": {
            "input_event": (
                "@event_handler()\n"
                "def on_input(self, value: str = '', **kwargs):\n"
                "    self.text = value"
            ),
            "button_with_data": (
                "@event_handler()\n"
                "def delete(self, item_id: int = 0, **kwargs):\n"
                "    Item.objects.filter(id=item_id).delete()\n"
                "    self._refresh()"
            ),
            "form_submit": (
                "@event_handler()\n"
                "def submit(self, **form_data):\n"
                "    name = form_data.get('name')\n"
                "    email = form_data.get('email')"
            ),
            "debounced_search": (
                "@event_handler()\n"
                "@debounce(wait=0.5)\n"
                "def search(self, value: str = '', **kwargs):\n"
                "    self.query = value\n"
                "    self._refresh()"
            ),
            "throttled": (
                "@event_handler()\n"
                "@throttle(interval=1.0)\n"
                "def on_scroll(self, position: int = 0, **kwargs):\n"
                "    self.scroll_pos = position"
            ),
        },
    },
    "template_directives": {
        "click": '<button dj-click="increment">+1</button>',
        "click_with_data": '<button dj-click="delete" data-item-id="{{ item.id }}">Delete</button>',
        "input": '<input type="text" dj-input="search" value="{{ query }}" />',
        "change": (
            '<select dj-change="filter">\n'
            '    <option value="all">All</option>\n'
            '    <option value="active">Active</option>\n'
            "</select>"
        ),
        "form_submit": (
            '<form dj-submit="save">\n'
            "    {%% csrf_token %%}\n"
            '    <input name="title" type="text" />\n'
            '    <button type="submit">Save</button>\n'
            "</form>"
        ),
        "keydown": '<input dj-keydown.enter="submit_search" />',
        "hook": '<div dj-hook="chart" id="chart-container"></div>',
        "update_ignore": '<div dj-update="ignore">User-controlled DOM here</div>',
        "keyed_list": (
            "{%% for item in items %%}\n"
            '<div data-key="{{ item.id }}">{{ item.name }}</div>\n'
            "{%% endfor %%}"
        ),
    },
    "jit_serialization": {
        "description": (
            "QuerySets MUST be stored in private variables (self._items) and assigned "
            "to public variables (self.items) only inside get_context_data(). This "
            "enables Rust-based serialization (10-100x faster than Python)."
        ),
        "correct": (
            "def _refresh(self):\n"
            "    self._items = Item.objects.filter(active=True)  # private\n"
            "\n"
            "def get_context_data(self, **kwargs):\n"
            "    self.items = self._items  # public <- private (JIT happens here)\n"
            "    return super().get_context_data(**kwargs)"
        ),
        "wrong_examples": [
            "self.items = Item.objects.all()  # never assign QuerySet to public directly",
            "self._items = list(Item.objects.all())  # never convert to list — disables JIT",
        ],
    },
    "forms": {
        "description": "Use FormMixin for Django form integration with real-time validation",
        "example": (
            "from djust.forms import FormMixin\n"
            "\n"
            "class MyFormView(FormMixin, LiveView):\n"
            "    template_name = 'form.html'\n"
            "    form_class = MyForm\n"
            "\n"
            "    def mount(self, request, pk=None, **kwargs):\n"
            "        if pk:\n"
            "            self._model_instance = MyModel.objects.get(pk=pk)\n"
            "        super().mount(request, **kwargs)  # AFTER setting _model_instance\n"
            "\n"
            "    def form_valid(self, form):\n"
            "        obj = form.save()\n"
            "        self.success_message = 'Saved!'\n"
            "        self.redirect_url = reverse('detail', kwargs={'pk': obj.pk})\n"
            "\n"
            "    def form_invalid(self, form):\n"
            "        self.error_message = 'Please fix errors below'"
        ),
    },
    "security": {
        "rules": [
            "Check auth in mount() and re-check in event handlers",
            "Use type hints for automatic coercion (item_id: int)",
            "Always {% csrf_token %} in forms",
            "Never use |safe filter on user-controlled variables",
            "Never use mark_safe(f'...') — use format_html() instead",
            "Use @permission_required for Django permission checks",
            "Use @rate_limit to prevent abuse on expensive handlers",
        ],
        "example": (
            "class SecureView(LiveView):\n"
            "    def mount(self, request, pk=None, **kwargs):\n"
            "        if not request.user.is_authenticated:\n"
            "            raise PermissionDenied('Login required')\n"
            "        if pk:\n"
            "            obj = MyModel.objects.get(pk=pk)\n"
            "            if obj.owner != request.user:\n"
            "                raise PermissionDenied('Not authorized')\n"
            "            self._obj = obj\n"
            "\n"
            "    @event_handler()\n"
            "    def delete(self, item_id: int = 0, **kwargs):\n"
            "        item = MyModel.objects.get(id=item_id)\n"
            "        if item.owner != self.request.user:\n"
            "            raise PermissionDenied\n"
            "        item.delete()"
        ),
    },
    "state_management": {
        "serialization": {
            "description": (
                "djust serializes view state for WebSocket transport. Only JSON-serializable "
                "values can be stored as instance attributes. Non-serializable objects "
                "(service clients, DB connections, file handles) cause runtime errors."
            ),
            "serializable": [
                "str, int, float, bool, None",
                "list, dict, tuple (with serializable contents)",
                "Django model instances (auto-serialized via JIT)",
                "QuerySets (auto-serialized via Rust JIT — keep as private _var)",
                "datetime, date, time, Decimal, UUID (auto-coerced)",
            ],
            "not_serializable": [
                "Service/API clients (boto3.client, httpx.Client, requests.Session)",
                "Database connections or cursors",
                "File handles or sockets",
                "Thread/process objects",
                "Class instances with non-serializable attributes",
            ],
            "fix_pattern": (
                "# WRONG: storing service instance in state\n"
                "def mount(self, request, **kwargs):\n"
                "    self.s3_client = boto3.client('s3')  # Will fail on serialize\n"
                "\n"
                "# CORRECT: use a helper method\n"
                "def _get_s3_client(self):\n"
                "    return boto3.client('s3')\n"
                "\n"
                "def upload_file(self, **kwargs):\n"
                "    client = self._get_s3_client()  # Created fresh each call\n"
                "    client.upload_file(...)"
            ),
        },
    },
    "templates": {
        "required_attributes": {
            "description": (
                "djust templates require two data attributes on the root element for "
                "the VDOM diffing engine to function correctly."
            ),
            "attributes": [
                "dj-view: Identifies the view class for WebSocket routing",
                "dj-root: Marks the root element for VDOM patching scope",
            ],
            "example": (
                '<div dj-view="{{ view_name }}" dj-root>\n'
                "  <!-- your template content -->\n"
                "</div>"
            ),
        },
    },
    "event_handler_signature": {
        "description": (
            "All event handlers MUST accept **kwargs to handle extra parameters "
            "sent by the client (data-* attributes, form fields, etc.). Missing "
            "**kwargs causes TypeError when unexpected params arrive."
        ),
        "correct": (
            "@event_handler()\n"
            "def delete_item(self, item_id: int = 0, **kwargs):\n"
            "    Item.objects.filter(id=item_id).delete()"
        ),
        "wrong": (
            "@event_handler()\n"
            "def delete_item(self, item_id: int = 0):  # Missing **kwargs!\n"
            "    Item.objects.filter(id=item_id).delete()"
        ),
    },
    "common_pitfalls": [
        {
            "id": 1,
            "problem": "Service instances stored in state",
            "why": (
                "Objects like boto3 clients, httpx.Client, or requests.Session are not "
                "JSON-serializable. djust serializes all view state for WebSocket transport, "
                "so storing these as self.client causes serialization errors."
            ),
            "solution": (
                "Use a helper method pattern: define a private method like _get_client() "
                "that creates the service instance on demand. Call it inside event handlers "
                "instead of storing the result on self."
            ),
            "related_doc": "docs/guides/services.md",
        },
        {
            "id": 2,
            "problem": "Missing dj-root attribute on template root element",
            "why": (
                "The VDOM diffing engine needs dj-root to identify the patch scope. "
                "Without it, DOM updates silently fail or produce incorrect diffs."
            ),
            "solution": (
                "Add both dj-view and dj-root to the outermost element: "
                '<div dj-view="{{ view_name }}" dj-root>'
            ),
            "related_doc": "docs/guides/template-requirements.md",
        },
        {
            "id": 3,
            "problem": "Event handler missing **kwargs",
            "why": (
                "The client sends additional context (data-* attributes, form fields) as "
                "keyword arguments. Without **kwargs, Python raises TypeError on unexpected args."
            ),
            "solution": "Add **kwargs to every event handler signature.",
            "related_check": "djust.V004",
        },
        {
            "id": 4,
            "problem": "ASGI config without WebSocket routing",
            "why": (
                "djust requires both HTTP and WebSocket protocols. Using only "
                "get_asgi_application() handles HTTP but drops WebSocket connections."
            ),
            "solution": (
                "Use ProtocolTypeRouter with both 'http' and 'websocket' keys. "
                "Wrap websocket routes in AuthMiddlewareStack and URLRouter."
            ),
            "related_doc": "docs/guides/error-codes.md",
        },
        {
            "id": 5,
            "problem": "Missing WhiteNoise middleware when using Daphne/uvicorn",
            "why": (
                "ASGI servers don't serve static files by default. Without WhiteNoise, "
                "client.js and other static assets return 404."
            ),
            "solution": (
                "Add 'whitenoise.middleware.WhiteNoiseMiddleware' to MIDDLEWARE after "
                "SecurityMiddleware. Run collectstatic for production."
            ),
            "related_doc": "docs/guides/error-codes.md",
        },
        {
            "id": 6,
            "problem": "Search input without debouncing",
            "why": (
                "Every keystroke sends a WebSocket message and triggers a full re-render. "
                "This floods the server and causes poor UX with flickering."
            ),
            "solution": (
                "Add @debounce(wait=0.5) decorator to the search handler. "
                "This waits 500ms after the last keystroke before firing."
            ),
            "related_check": "djust.Q001",
        },
        {
            "id": 7,
            "problem": "Converting QuerySets to lists before template rendering",
            "why": (
                "Calling list() on a QuerySet forces Python-side serialization, bypassing "
                "the Rust JIT engine that is 10-100x faster."
            ),
            "solution": (
                "Pass QuerySets directly. Store as self._items (private) in _refresh(), "
                "assign to self.items (public) in get_context_data()."
            ),
            "related_doc": "docs/guides/services.md",
        },
        {
            "id": 8,
            "problem": "Manually loading client.js in templates",
            "why": (
                "djust auto-injects client.js via middleware. Manual <script> tags cause "
                "double-loading, duplicate WebSocket connections, and race conditions."
            ),
            "solution": (
                "Remove any manual <script> tags for djust client.js. "
                "The middleware handles injection automatically."
            ),
            "related_doc": "docs/guides/template-requirements.md",
        },
    ],
}


def get_framework_schema() -> Dict[str, Any]:
    """
    Return complete static framework metadata.

    This does NOT require Django setup — it returns hardcoded metadata about
    the framework's directives, lifecycle, decorators, and conventions.

    Returns:
        Dict with keys: version, directives, lifecycle_methods, class_attributes,
        decorators, navigation_methods, stream_methods, push_event_methods,
        optional_mixins, data_attribute_types, conventions
    """
    return {
        "version": _get_version(),
        "directives": DIRECTIVES,
        "lifecycle_methods": LIFECYCLE_METHODS,
        "class_attributes": CLASS_ATTRIBUTES,
        "decorators": DECORATORS,
        "navigation_methods": NAVIGATION_METHODS,
        "stream_methods": STREAM_METHODS,
        "push_event_methods": PUSH_EVENT_METHODS,
        "optional_mixins": OPTIONAL_MIXINS,
        "data_attribute_types": DATA_ATTRIBUTE_TYPES,
        "conventions": CONVENTIONS,
    }


# ============================================================================
# Project Schema — requires Django setup (introspects live classes)
# ============================================================================


def get_project_schema() -> Dict[str, Any]:
    """
    Return project-specific metadata by introspecting all LiveView subclasses.

    Requires Django to be set up (apps loaded). Discovers all user-defined
    LiveViews and LiveComponents, extracts their handlers, state, templates,
    and configuration.

    Returns:
        Dict with keys: views, components, routes
    """
    views = []
    components = []

    try:
        from djust.live_view import LiveView
        from djust.management.commands.djust_audit import (
            _walk_subclasses,
            _is_user_class,
        )

        for cls in _walk_subclasses(LiveView):
            if not _is_user_class(cls):
                continue
            views.append(_extract_view_schema(cls, [LiveView]))
    except ImportError:
        logger.debug("LiveView not available — skipping view introspection")

    try:
        from djust.components.base import LiveComponent
        from djust.management.commands.djust_audit import (
            _walk_subclasses,
            _is_user_class,
        )

        for cls in _walk_subclasses(LiveComponent):
            if not _is_user_class(cls):
                continue
            components.append(_extract_view_schema(cls, [LiveComponent]))
    except ImportError:
        logger.debug("LiveComponent not available — skipping component introspection")

    routes = _extract_routes()

    return {
        "views": views,
        "components": components,
        "routes": routes,
    }


def _extract_view_schema(cls: type, base_classes: list) -> Dict[str, Any]:
    """Extract schema metadata from a single LiveView or LiveComponent class."""
    from djust.management.commands.djust_audit import (
        _get_handler_metadata,
        _extract_exposed_state,
        _extract_auth_info,
        KNOWN_MIXINS,
    )

    # Basic info
    module = getattr(cls, "__module__", "")
    class_name = cls.__qualname__
    full_path = "%s.%s" % (module, class_name)

    # Template
    template = getattr(cls, "template_name", None)
    if template is None and getattr(cls, "template", None):
        template = "(inline)"

    # Mixins
    mixins = [c.__name__ for c in cls.__mro__ if c.__name__ in KNOWN_MIXINS]

    # Handlers
    handlers = []
    for name, meta in _get_handler_metadata(cls, base_classes=base_classes):
        eh = meta.get("event_handler", {})
        handler_info = {
            "name": name,
            "params": eh.get("params", []),
            "description": eh.get("description", ""),
            "accepts_kwargs": eh.get("accepts_kwargs", False),
            "decorators": {},
        }
        # Include decorator metadata
        for key in (
            "debounce",
            "throttle",
            "rate_limit",
            "cache",
            "optimistic",
            "client_state",
            "permission_required",
        ):
            if key in meta:
                handler_info["decorators"][key] = meta[key]
        handlers.append(handler_info)

    # Exposed state
    exposed_state = _extract_exposed_state(cls)

    # Auth
    auth = _extract_auth_info(cls)

    # Config
    config = {}
    tick = getattr(cls, "tick_interval", None)
    if tick is not None:
        config["tick_interval"] = tick
    temp = getattr(cls, "temporary_assigns", None)
    if temp:
        config["temporary_assigns"] = list(temp.keys()) if isinstance(temp, dict) else temp
    if getattr(cls, "use_actors", False):
        config["use_actors"] = True

    return {
        "class": full_path,
        "module": module,
        "template": template,
        "mixins": mixins,
        "handlers": handlers,
        "exposed_state": {k: v["source"] for k, v in exposed_state.items()},
        "auth": auth,
        "config": config,
    }


def _extract_routes() -> List[Dict[str, str]]:
    """Extract URL routes that map to LiveView classes."""
    routes = []
    try:
        from django.urls import get_resolver

        resolver = get_resolver()
        _walk_url_patterns(resolver.url_patterns, "", routes)
    except Exception:
        logger.debug("Could not extract URL routes")
    return routes


def _walk_url_patterns(patterns: list, prefix: str, routes: List[Dict[str, str]]) -> None:
    """Recursively walk Django URL patterns to find LiveView routes."""
    for pattern in patterns:
        full_pattern = prefix + str(getattr(pattern, "pattern", ""))

        if hasattr(pattern, "url_patterns"):
            # URLResolver — recurse
            _walk_url_patterns(pattern.url_patterns, full_pattern, routes)
        elif hasattr(pattern, "callback"):
            callback = pattern.callback
            # Check if it's a LiveView class-based view
            view_class = getattr(callback, "view_class", None)
            if view_class:
                try:
                    from djust.live_view import LiveView

                    if issubclass(view_class, LiveView):
                        routes.append(
                            {
                                "pattern": "/" + full_pattern,
                                "view": "%s.%s"
                                % (
                                    view_class.__module__,
                                    view_class.__qualname__,
                                ),
                                "name": getattr(pattern, "name", None) or "",
                            }
                        )
                except (ImportError, TypeError):
                    pass


def _get_version() -> str:
    """Get the djust framework version."""
    try:
        from djust import __version__

        return __version__
    except (ImportError, AttributeError):
        return "unknown"
