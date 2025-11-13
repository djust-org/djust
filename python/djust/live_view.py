"""
LiveView base class and decorator for reactive Django views
"""

import json
import logging
import sys
from datetime import datetime, date, time
from decimal import Decimal
from uuid import UUID
from typing import Any, Dict, Optional, Callable
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from django.db import models

# Configure logger
logger = logging.getLogger(__name__)

try:
    from ._rust import RustLiveView, create_session_actor, SessionActorHandle
except ImportError:
    RustLiveView = None
    create_session_actor = None
    SessionActorHandle = None


class DjangoJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles common Django and Python types.

    Automatically converts:
    - datetime/date/time → ISO format strings
    - UUID → string
    - Decimal → float
    - Component/LiveComponent → rendered HTML string
    - Django models → dict with id and __str__
    - QuerySets → list
    """

    def default(self, obj):
        # Handle Component and LiveComponent instances (render to HTML)
        from .component import Component, LiveComponent

        if isinstance(obj, (Component, LiveComponent)):
            return str(obj)  # Calls __str__() which calls render()

        # Handle datetime types
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()

        # Handle UUID
        if isinstance(obj, UUID):
            return str(obj)

        # Handle Decimal
        if isinstance(obj, Decimal):
            return float(obj)

        # Handle Django model instances
        if isinstance(obj, models.Model):
            result = {
                "id": str(obj.pk) if obj.pk else None,
                "__str__": str(obj),
                "__model__": obj.__class__.__name__,
            }

            # Serialize all model fields (excluding relations to avoid circular refs)
            for field in obj._meta.get_fields():
                # Skip reverse relations and many-to-many
                if field.is_relation and (field.one_to_many or field.many_to_many):
                    continue

                try:
                    field_name = field.name
                    value = getattr(obj, field_name, None)

                    # Skip None values and relations (ForeignKey, etc.)
                    if value is None:
                        result[field_name] = None
                    elif isinstance(value, models.Model):
                        # For ForeignKey, just include id and __str__
                        result[field_name] = {
                            "id": str(value.pk) if value.pk else None,
                            "__str__": str(value),
                        }
                    else:
                        result[field_name] = value
                except (AttributeError, ValueError):
                    pass

            # Include custom methods that don't start with _ and are callable
            # This allows model methods like get_status_display(), get_full_name(), etc.
            for attr_name in dir(obj):
                if not attr_name.startswith("_") and attr_name not in result:
                    attr = getattr(obj, attr_name, None)
                    # Include callable methods with no required parameters
                    if callable(attr) and attr_name.startswith("get_"):
                        try:
                            # Try calling with no args
                            value = attr()
                            # Only include simple return types
                            if isinstance(value, (str, int, float, bool, type(None))):
                                result[attr_name] = value
                        except Exception:
                            # Silently skip methods that fail (like get_next_by_*, DoesNotExist, etc.)
                            pass

            return result

        # Handle QuerySets
        if hasattr(obj, "model") and hasattr(obj, "__iter__"):
            # This is likely a QuerySet
            return list(obj)

        return super().default(obj)


# Global cache for RustLiveView instances
# Structure: {cache_key: (rust_view, timestamp)}
_rust_view_cache = {}

# Default TTL for sessions (1 hour)
DEFAULT_SESSION_TTL = 3600


def cleanup_expired_sessions(ttl: Optional[int] = None) -> int:
    """
    Clean up expired LiveView sessions from cache.

    Args:
        ttl: Time to live in seconds. Defaults to DEFAULT_SESSION_TTL.

    Returns:
        Number of sessions cleaned up
    """
    import time
    import logging

    logger = logging.getLogger(__name__)

    if ttl is None:
        ttl = DEFAULT_SESSION_TTL

    cutoff = time.time() - ttl
    expired_keys = []

    # Find expired sessions
    for key, (view, timestamp) in list(_rust_view_cache.items()):
        if timestamp < cutoff:
            expired_keys.append(key)

    # Remove expired sessions
    for key in expired_keys:
        del _rust_view_cache[key]

    if expired_keys:
        logger.info(f"Cleaned up {len(expired_keys)} expired LiveView sessions")

    return len(expired_keys)


def get_session_stats() -> Dict[str, Any]:
    """
    Get statistics about cached LiveView sessions.

    Returns:
        Dictionary with cache statistics
    """
    import time

    if not _rust_view_cache:
        return {
            "total_sessions": 0,
            "oldest_session_age": 0,
            "newest_session_age": 0,
            "average_age": 0,
        }

    current_time = time.time()
    ages = [current_time - timestamp for _, timestamp in _rust_view_cache.values()]

    return {
        "total_sessions": len(_rust_view_cache),
        "oldest_session_age": max(ages) if ages else 0,
        "newest_session_age": min(ages) if ages else 0,
        "average_age": sum(ages) / len(ages) if ages else 0,
    }


class LiveView(View):
    """
    Base class for reactive LiveView components.

    Usage:
        class CounterView(LiveView):
            template_name = 'counter.html'
            use_actors = True  # Enable actor-based state management (optional)

            def mount(self, request, **kwargs):
                self.count = 0

            def increment(self):
                self.count += 1

            def decrement(self):
                self.count -= 1
    """

    template_name: Optional[str] = None
    template_string: Optional[str] = None
    use_actors: bool = False  # Enable Tokio actor-based state management (Phase 5+)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rust_view: Optional[RustLiveView] = None
        self._actor_handle: Optional[SessionActorHandle] = None
        self._session_id: Optional[str] = None
        self._cache_key: Optional[str] = None
        self._handler_metadata: Optional[dict] = None  # Cache for decorator metadata
        self._components: Dict[str, Any] = {}  # Registry of child components by ID

    def get_template(self) -> str:
        """
        Get the Rust template source for this view.

        Supports template inheritance via {% extends %} and {% block %} tags.
        Templates are resolved using Rust template inheritance for performance.

        For templates with inheritance, extracts only [data-liveview-root] content
        for VDOM tracking to avoid tracking the entire document.
        """
        if self.template_string:
            return self.template_string
        elif self.template_name:
            # Load the raw template source
            from django.template import loader
            from django.conf import settings

            template = loader.get_template(self.template_name)
            template_source = template.template.source

            # Check if template uses {% extends %} - if so, resolve inheritance in Rust
            if "{% extends" in template_source or "{%extends" in template_source:
                # Get template directories from Django settings
                template_dirs = []
                for template_config in settings.TEMPLATES:
                    if "DIRS" in template_config:
                        template_dirs.extend(template_config["DIRS"])

                # Also add app template directories
                for template_config in settings.TEMPLATES:
                    if (
                        template_config["BACKEND"]
                        == "django.template.backends.django.DjangoTemplates"
                    ):
                        if template_config.get("APP_DIRS", False):
                            from django.apps import apps
                            from pathlib import Path

                            for app_config in apps.get_app_configs():
                                templates_dir = Path(app_config.path) / "templates"
                                if templates_dir.exists():
                                    template_dirs.append(str(templates_dir))

                # Convert to strings
                template_dirs_str = [str(d) for d in template_dirs]

                # Use Rust template inheritance resolution
                try:
                    from djust._rust import resolve_template_inheritance

                    resolved = resolve_template_inheritance(self.template_name, template_dirs_str)

                    # Store full template for initial GET rendering
                    self._full_template = resolved

                    # For VDOM tracking, use the child template only (without base.html)
                    # This prevents tracking the entire document (DOCTYPE, head, nav, etc.)
                    # The child template contains {% block content %}...{% endblock %} which wraps
                    # the LiveView root content
                    import re

                    # Remove {% extends %} and extract all blocks
                    child_content = re.sub(
                        r'{%\s*extends\s+["\'].*?["\']\s*%}', "", template_source
                    )

                    # Extract content from all {% block %} tags and concatenate
                    blocks = re.findall(
                        r"{%\s*block\s+\w+\s*%}(.*?){%\s*endblock\s*%}", child_content, re.DOTALL
                    )
                    if blocks:
                        # Join all block contents
                        vdom_template = "".join(blocks)
                        # Extract liveview-root div (with wrapper) for VDOM tracking
                        vdom_template = self._extract_liveview_root_with_wrapper(vdom_template)
                        print(
                            f"[LiveView] Using child template blocks for VDOM tracking ({len(vdom_template)} chars), full template for rendering ({len(resolved)} chars)",
                            file=sys.stderr,
                        )
                        return vdom_template
                    else:
                        # No blocks found, use the child template as-is (minus extends)
                        # Extract liveview-root div (with wrapper) for VDOM tracking
                        child_vdom = self._extract_liveview_root_with_wrapper(child_content)
                        print(
                            f"[LiveView] Using child template for VDOM tracking ({len(child_vdom)} chars)",
                            file=sys.stderr,
                        )
                        return child_vdom

                except Exception as e:
                    # Fallback to raw template if Rust resolution fails
                    print(f"[LiveView] Template inheritance resolution failed: {e}")
                    print("[LiveView] Falling back to raw template source")
                    # Store full template for render_full_template()
                    self._full_template = template_source
                    # Extract liveview-root div (with wrapper) for VDOM tracking
                    extracted = self._extract_liveview_root_with_wrapper(template_source)

                    # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                    extracted = self._strip_comments_and_whitespace(extracted)

                    print(
                        f"[LiveView] Extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                        file=sys.stderr,
                    )
                    return extracted

            # No template inheritance - store full template and extract liveview-root for VDOM
            # Store full template for render_full_template() to use in GET responses
            self._full_template = template_source

            # Extract liveview-root div (with wrapper) for VDOM tracking
            # This ensures server VDOM and client VDOM track the same structure
            extracted = self._extract_liveview_root_with_wrapper(template_source)

            # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
            # This ensures Rust VDOM baseline matches client DOM structure
            extracted = self._strip_comments_and_whitespace(extracted)

            print(
                f"[LiveView] No inheritance - extracted and stripped liveview-root: {len(extracted)} chars (from {len(template_source)} chars)",
                file=sys.stderr,
            )
            return extracted
        else:
            raise ValueError("Either template_name or template_string must be set")

    def mount(self, request, **kwargs):
        """
        Called when the view is mounted. Override to set initial state.

        Args:
            request: The Django request object
            **kwargs: URL parameters
        """
        pass

    def handle_component_event(self, component_id: str, event: str, data: Dict[str, Any]):
        """
        Handle events sent from child components.

        Override this method to respond to component events sent via send_parent().

        Args:
            component_id: Unique ID of the component sending the event
            event: Event name (e.g., "item_selected", "form_submitted")
            data: Event payload data

        Example:
            def handle_component_event(self, component_id, event, data):
                if event == "user_selected":
                    self.selected_user_id = data['user_id']
                    # Update child component props
                    if hasattr(self, 'user_detail'):
                        self.user_detail.update(user_id=data['user_id'])
                elif event == "item_added":
                    self.items.append(data['item'])
        """
        pass

    def update_component(self, component_id: str, **props):
        """
        Update a child component's props.

        Args:
            component_id: ID of the component to update
            **props: New prop values to pass to component

        Example:
            # Update user detail component with new user
            self.update_component(
                self.user_detail.component_id,
                user_id=selected_id
            )
        """
        from .component import LiveComponent

        component = self._components.get(component_id)
        if component and isinstance(component, LiveComponent):
            component.update(**props)

    def _register_component(self, component):
        """
        Register a child component for event handling.

        Internal method called during mount() to set up component callbacks.

        Args:
            component: LiveComponent instance to register
        """
        from .component import LiveComponent

        if isinstance(component, LiveComponent):
            # Register component by ID
            self._components[component.component_id] = component

            # Set up parent callback for event handling
            def component_callback(event_data):
                self.handle_component_event(
                    event_data["component_id"],
                    event_data["event"],
                    event_data["data"],
                )

            component._set_parent_callback(component_callback)

    def get_context_data(self, **kwargs) -> Dict[str, Any]:
        """
        Get the context data for rendering. Override to customize context.

        Returns:
            Dictionary of context variables

        Notes:
            - Automatically serializes datetime, UUID, Decimal, and Django models
            - Use DjangoJSONEncoder for custom type handling
        """
        from .component import Component, LiveComponent

        context = {}

        # Add all non-private attributes as context
        for key in dir(self):
            if not key.startswith("_"):
                try:
                    value = getattr(self, key)
                    if not callable(value):
                        # Include Component instances (stateless components)
                        # Include LiveComponent instances (stateful components)
                        # OR JSON-serializable values (for state storage)
                        if isinstance(value, (Component, LiveComponent)):
                            # Auto-register LiveComponents for event handling
                            if isinstance(value, LiveComponent):
                                self._register_component(value)
                            context[key] = value
                        else:
                            try:
                                # Use custom Django encoder for better type support
                                json.dumps(value, cls=DjangoJSONEncoder)
                                context[key] = value
                            except (TypeError, ValueError):
                                # Skip non-serializable objects (like request, etc)
                                pass
                except (AttributeError, TypeError):
                    # Skip class-only methods and other inaccessible attributes
                    continue

        return context

    def _initialize_rust_view(self, request=None):
        """Initialize the Rust LiveView backend"""

        print(
            f"[LiveView] _initialize_rust_view() called, _rust_view={self._rust_view}",
            file=sys.stderr,
        )

        if self._rust_view is None:
            # Try to get from cache if we have a session
            if request and hasattr(request, "session"):
                view_key = f"liveview_{request.path}"
                session_key = request.session.session_key
                if not session_key:
                    request.session.create()
                    session_key = request.session.session_key

                self._cache_key = f"{session_key}_{view_key}"
                print(f"[LiveView] Cache lookup: cache_key={self._cache_key}", file=sys.stderr)
                print(
                    f"[LiveView] Cache contents: {list(_rust_view_cache.keys())}", file=sys.stderr
                )

                # Try to get cached RustLiveView
                if self._cache_key in _rust_view_cache:
                    cached_view, timestamp = _rust_view_cache[self._cache_key]
                    self._rust_view = cached_view
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    # Update timestamp on access
                    import time

                    _rust_view_cache[self._cache_key] = (cached_view, time.time())
                    return
                else:
                    print("[LiveView] Cache MISS! Will create new RustLiveView", file=sys.stderr)

            # Create new RustLiveView with the template
            # The template includes <div data-liveview-root> which matches what the client patches
            # get_template() returns child blocks (with wrapper) for inheritance,
            # or raw template for non-inheritance
            template_source = self.get_template()

            print(
                f"[LiveView] Creating NEW RustLiveView for cache_key={self._cache_key}",
                file=sys.stderr,
            )
            print(f"[LiveView] Template length: {len(template_source)} chars", file=sys.stderr)
            print(f"[LiveView] Template preview: {template_source[:200]}...", file=sys.stderr)

            self._rust_view = RustLiveView(template_source)

            # Cache it if we have a cache key
            if self._cache_key:
                import time

                _rust_view_cache[self._cache_key] = (self._rust_view, time.time())

    def _sync_state_to_rust(self):
        """Sync Python state to Rust backend"""
        if self._rust_view:
            from .component import Component, LiveComponent

            context = self.get_context_data()

            # Pre-render components since Rust can't call Python methods
            rendered_context = {}
            for key, value in context.items():
                if isinstance(value, (Component, LiveComponent)):
                    # Create a dict with the pre-rendered HTML so {{ component.render }} works
                    rendered_html = str(value.render())
                    rendered_context[key] = {"render": rendered_html}
                else:
                    rendered_context[key] = value

            # Serialize and deserialize to ensure all types are JSON-compatible
            # This converts UUIDs, datetimes, etc. to their JSON representations
            json_str = json.dumps(rendered_context, cls=DjangoJSONEncoder)
            json_compatible_context = json.loads(json_str)

            self._rust_view.update_state(json_compatible_context)

    def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Extract decorator metadata from all event handlers.

        Inspects all methods for the _djust_decorators attribute
        added by decorators like @debounce, @throttle, @optimistic, etc.

        Results are cached after first extraction since decorator metadata
        is static for a given view class.

        Returns:
            Dictionary mapping handler names to their decorator metadata.

            Example:
                {
                    "search": {
                        "debounce": {"wait": 0.5, "max_wait": None},
                        "optimistic": True
                    },
                    "update_slider": {
                        "throttle": {"interval": 0.1, "leading": True, "trailing": True}
                    }
                }
        """
        # Return cached metadata if available
        if self._handler_metadata is not None:
            logger.debug(
                f"[LiveView] Using cached handler metadata for {self.__class__.__name__} "
                f"({len(self._handler_metadata)} handlers)"
            )
            return self._handler_metadata

        logger.debug(f"[LiveView] Extracting handler metadata for {self.__class__.__name__}")
        metadata = {}

        # Iterate all methods
        for name in dir(self):
            # Skip private methods
            if name.startswith("_"):
                continue

            try:
                method = getattr(self, name)

                # Check if it's callable
                if not callable(method):
                    continue

                # Check for decorator metadata
                if hasattr(method, "_djust_decorators"):
                    metadata[name] = method._djust_decorators
                    logger.debug(
                        f"[LiveView]   Found decorated handler: {name} -> "
                        f"{list(method._djust_decorators.keys())}"
                    )

            except (AttributeError, TypeError):
                # Skip attributes that can't be accessed
                continue

        # Cache the result
        self._handler_metadata = metadata
        logger.debug(
            f"[LiveView] Extracted {len(metadata)} decorated handlers, caching for future use"
        )

        return metadata

    def render(self, request=None) -> str:
        """
        Render the view to HTML.

        Returns the rendered HTML from the template. For WebSocket updates,
        caller should use _extract_liveview_content() to get innerHTML only.

        Args:
            request: The request object

        Returns:
            Rendered HTML with embedded handler metadata
        """
        self._initialize_rust_view(request)
        self._sync_state_to_rust()
        html = self._rust_view.render()

        # Post-process to hydrate React components
        html = self._hydrate_react_components(html)

        # Inject handler metadata for client-side decorators
        html = self._inject_handler_metadata(html)

        return html

    def _inject_handler_metadata(self, html: str) -> str:
        """
        Inject handler metadata script into HTML.

        Adds a <script> tag that sets window.handlerMetadata with
        decorator metadata for all handlers.

        Args:
            html: Rendered HTML

        Returns:
            HTML with injected metadata script
        """
        # Extract metadata
        metadata = self._extract_handler_metadata()

        # Skip injection if no metadata
        if not metadata:
            logger.debug("[LiveView] No handler metadata to inject, skipping script injection")
            return html

        logger.debug(f"[LiveView] Injecting handler metadata script for {len(metadata)} handlers")

        # Build script tag
        script = f"""
<script>
// Handler metadata for client-side decorators
window.handlerMetadata = window.handlerMetadata || {{}};
Object.assign(window.handlerMetadata, {json.dumps(metadata)});
</script>"""

        # Try to inject before </body>
        if "</body>" in html:
            html = html.replace("</body>", f"{script}\n</body>")
            logger.debug("[LiveView] Injected metadata script before </body>")
        # Fallback: inject before </html>
        elif "</html>" in html:
            html = html.replace("</html>", f"{script}\n</html>")
            logger.debug("[LiveView] Injected metadata script before </html>")
        # Fallback: append to end (for template fragments)
        else:
            html = html + script
            logger.debug("[LiveView] Appended metadata script to end of HTML")

        return html

    def _strip_comments_and_whitespace(self, html: str) -> str:
        """
        Strip HTML comments and normalize whitespace to match Rust VDOM parser behavior.

        The Rust VDOM parser (parser.rs) filters out comments and whitespace-only text nodes.
        We need the client DOM to match the server VDOM structure, so we strip comments
        from rendered HTML before sending to client.
        """
        import re

        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
        # Normalize whitespace (collapse multiple whitespace to single space)
        html = re.sub(r"\s+", " ", html)
        # Remove whitespace between tags
        html = re.sub(r">\s+<", "><", html)
        return html

    def _extract_liveview_content(self, html: str) -> str:
        """
        Extract the inner content of [data-liveview-root] from full HTML.

        This ensures the HTML sent over WebSocket matches what the client expects:
        just the content to insert into the existing [data-liveview-root] container.
        """
        import re

        # Find the opening tag for [data-liveview-root]
        opening_match = re.search(r"<div\s+data-liveview-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return full HTML
            return html

        start_pos = opening_match.end()

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(html):
            # Look for next <div or </div
            open_match = re.search(r"<div\b", html[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", html[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return inner content only
                    return html[start_pos:close_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return full HTML
        return html

    def _extract_liveview_root_with_wrapper(self, template: str) -> str:
        """
        Extract the <div data-liveview-root>...</div> section from a template (WITH the wrapper div).

        This is used to ensure server VDOM and client VDOM track the same structure:
        - Client's getNodeByPath([]) returns the div[data-liveview-root] element
        - Server VDOM must also track div[data-liveview-root] as the root
        - This ensures paths match between server patches and client DOM

        Args:
            template: Template string (may include DOCTYPE, html, head, body, etc.)

        Returns:
            Template with just the <div data-liveview-root>...</div> section (WITH wrapper)
        """
        import re

        # Find the opening tag for [data-liveview-root]
        opening_match = re.search(r"<div\s+data-liveview-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return template as-is
            return template

        start_pos = opening_match.start()  # Start of <div tag, not end
        inner_start_pos = opening_match.end()  # End of opening tag

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = inner_start_pos

        while depth > 0 and pos < len(template):
            # Look for next <div or </div
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return WITH wrapper
                    end_pos = pos + close_match.end()  # Include </div>
                    return template[start_pos:end_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return template as-is
        return template

    def _extract_liveview_template_content(self, template: str) -> str:
        """
        Extract the innerHTML of [data-liveview-root] from a TEMPLATE (not rendered HTML).

        This is used to establish VDOM baseline with only the innerHTML portion of the template,
        ensuring patches are calculated for the correct structure.

        Args:
            template: Template string with variables (e.g., "{{ count }}")

        Returns:
            Template innerHTML without the wrapper div
        """
        import re

        # Find the opening tag for [data-liveview-root]
        opening_match = re.search(r"<div\s+data-liveview-root[^>]*>", template, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return template as-is
            return template

        start_pos = opening_match.end()

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = start_pos

        while depth > 0 and pos < len(template):
            # Look for next <div or </div (but not in Django template tags)
            # This is a simplified parser - doesn't handle all edge cases
            open_match = re.search(r"<div\b", template[pos:], re.IGNORECASE)
            close_match = re.search(r"</div>", template[pos:], re.IGNORECASE)

            if close_match is None:
                break

            close_pos = pos + close_match.start()
            open_pos = pos + open_match.start() if open_match else float("inf")

            if open_pos < close_pos:
                # Found opening div first
                depth += 1
                pos = open_pos + 4  # Skip past '<div'
            else:
                # Found closing div first
                depth -= 1
                if depth == 0:
                    # This is the matching closing tag - return inner content only
                    return template[start_pos:close_pos]
                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return template as-is
        return template

    def render_full_template(self, request=None) -> str:
        """
        Render the full template including base template inheritance.
        Used for initial GET requests when using template inheritance.

        Returns the complete HTML document (DOCTYPE, html, head, body, etc.)
        """
        # Check if we have a full template from template inheritance
        if hasattr(self, "_full_template") and self._full_template:
            # Render the full template using Rust
            from djust._rust import RustLiveView

            temp_rust = RustLiveView(self._full_template)

            # Sync state to this temporary view
            from .component import Component, LiveComponent

            context = self.get_context_data()
            rendered_context = {}
            for key, value in context.items():
                if isinstance(value, (Component, LiveComponent)):
                    rendered_context[key] = {"render": str(value.render())}
                else:
                    rendered_context[key] = value

            temp_rust.update_state(rendered_context)
            html = temp_rust.render()
            return self._hydrate_react_components(html)
        else:
            # No full template - use regular render
            return self.render(request)

    def render_with_diff(
        self, request=None, extract_liveview_root=False
    ) -> tuple[str, Optional[str], int]:
        """
        Render the view and compute diff from last render.

        Args:
            extract_liveview_root: If True, extract innerHTML of [data-liveview-root]
                                  before establishing VDOM. This ensures Rust VDOM
                                  tracks exactly what the client's innerHTML contains.

        Returns:
            Tuple of (html, patches_json, version)
        """

        print(
            f"[LiveView] render_with_diff() called (extract_liveview_root={extract_liveview_root})",
            file=sys.stderr,
        )
        print(f"[LiveView] _rust_view before init: {self._rust_view}", file=sys.stderr)

        # Initialize Rust view if not already done
        self._initialize_rust_view(request)

        # If template_string is a property (dynamic), update the template
        # while preserving VDOM state for efficient patching
        if hasattr(self.__class__, "template_string") and isinstance(
            getattr(self.__class__, "template_string"), property
        ):
            print("[LiveView] template_string is a property - updating template", file=sys.stderr)
            new_template = self.get_template()
            self._rust_view.update_template(new_template)

        print(f"[LiveView] _rust_view after init: {self._rust_view}", file=sys.stderr)

        self._sync_state_to_rust()

        result = self._rust_view.render_with_diff()
        html, patches_json, version = result

        print(
            f"[LiveView] Rendered HTML length: {len(html)} chars, starts with: {html[:100]}...",
            file=sys.stderr,
        )

        # Extract [data-liveview-root] innerHTML if requested
        # This ensures Rust VDOM tracks exactly what the client's innerHTML contains
        if extract_liveview_root:
            html = self._extract_liveview_content(html)
            print(
                f"[LiveView] Extracted [data-liveview-root] content ({len(html)} chars)",
                file=sys.stderr,
            )

        print(
            f"[LiveView] Rust returned: version={version}, patches={'YES' if patches_json else 'NO'}",
            file=sys.stderr,
        )
        if not patches_json:
            print("[LiveView] NO PATCHES GENERATED!", file=sys.stderr)
        else:
            # Show first few patches for debugging (if enabled)
            from djust.config import config

            if config.get("debug_vdom", False):
                import json as json_module

                patches_list = json_module.loads(patches_json) if patches_json else []
                print(f"[LiveView] Generated {len(patches_list)} patches:", file=sys.stderr)
                for i, patch in enumerate(patches_list[:5]):  # Show first 5 patches
                    patch_type = patch.get("type", "Unknown")
                    path = patch.get("path", [])

                    # Add context about what we're patching
                    if patch_type == "SetAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' = '{patch.get('value')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "RemoveAttr":
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} '{patch.get('key')}' at path {path}",
                            file=sys.stderr,
                        )
                    elif patch_type == "SetText":
                        text_preview = patch.get("text", "")[:50]
                        print(
                            f"[LiveView]   Patch {i}: {patch_type} to '{text_preview}' at path {path}",
                            file=sys.stderr,
                        )
                    else:
                        print(f"[LiveView]   Patch {i}: {patch}", file=sys.stderr)

        return (html, patches_json, version)

    def _extract_component_state(self, component) -> dict:
        """
        Extract state from a component for session storage.

        Args:
            component: LiveComponent instance

        Returns:
            Dictionary of component state
        """
        import json as json_module

        state = {}
        for key in dir(component):
            if not key.startswith("_") and key not in ("template_name",):
                try:
                    value = getattr(component, key)
                    if not callable(value):
                        # Only store JSON-serializable values
                        try:
                            json_module.dumps(value)
                            state[key] = value
                        except (TypeError, ValueError):
                            # Skip non-serializable values
                            pass
                except (AttributeError, TypeError):
                    pass
        return state

    def _restore_component_state(self, component, state: dict):
        """
        Restore state to a component from session storage.

        Args:
            component: LiveComponent instance
            state: Dictionary of component state
        """
        for key, value in state.items():
            if not key.startswith("_"):
                try:
                    setattr(component, key, value)
                except (AttributeError, TypeError):
                    # Skip read-only properties
                    pass

    def _assign_component_ids(self):
        """
        Automatically assign IDs to components based on their attribute names.

        This is called after mount() to ensure components have stable, deterministic IDs
        instead of random UUIDs. For example:
            self.navbar_example = NavBar(...)  → automatically gets _auto_id="navbar_example"

        This makes HTTP mode work seamlessly with VDOM diffing, as IDs remain consistent
        across requests without needing session storage.
        """
        from .component import Component, LiveComponent

        for key, value in self.__dict__.items():
            if isinstance(value, (Component, LiveComponent)) and not key.startswith("_"):
                # Assign automatic ID based on variable name
                value._auto_id = key

    def _save_components_to_session(self, request, context: dict):
        """
        Save component state to session with stable IDs.

        This is the DRY method used by both GET and POST handlers to persist
        component state (including Component and LiveComponent IDs) across requests.
        Essential for HTTP mode where view instances are recreated on each request.

        Args:
            request: Django request object
            context: Context dictionary containing components
        """
        from .component import Component, LiveComponent

        view_key = f"liveview_{request.path}"
        component_state = {}

        for key, component in context.items():
            # Save state for both Component and LiveComponent to preserve IDs across requests
            if isinstance(component, (Component, LiveComponent)):
                # Automatic ID assignment: attribute name -> component_id
                # e.g., self.navbar_example gets component_id="navbar_example"
                component.component_id = key
                component_state[key] = self._extract_component_state(component)

        # Serialize component state to ensure session-compatible types
        component_state_json = json.dumps(component_state, cls=DjangoJSONEncoder)
        component_state_serializable = json.loads(component_state_json)
        request.session[f"{view_key}_components"] = component_state_serializable
        request.session.modified = True

    @method_decorator(ensure_csrf_cookie)
    def get(self, request, *args, **kwargs):
        """Handle GET requests - initial page load"""
        # IMPORTANT: mount() must be called first to initialize clean state
        self.mount(request, **kwargs)

        # Automatically assign deterministic IDs to components based on variable names
        self._assign_component_ids()

        # Ensure session exists and is saved
        if not request.session.session_key:
            request.session.create()

        # Store initial state in session (exclude components)
        view_key = f"liveview_{request.path}"
        from .component import LiveComponent

        context = self.get_context_data()
        state = {k: v for k, v in context.items() if not isinstance(v, LiveComponent)}

        # Serialize and deserialize to ensure session-compatible types (UUIDs, datetimes, etc.)
        state_json = json.dumps(state, cls=DjangoJSONEncoder)
        state_serializable = json.loads(state_json)
        request.session[view_key] = state_serializable

        # Save component state to session (DRY helper method)
        self._save_components_to_session(request, context)

        # NOTE: Don't clear the cache on GET! The cache persists across requests
        # to maintain VDOM state. Only clear if we detect the user explicitly
        # wants a fresh page (e.g., query param ?refresh=1)
        # if cache_key in _rust_view_cache:
        #     print(f"[LiveView] Clearing cached RustLiveView for fresh session", file=sys.stderr)
        #     del _rust_view_cache[cache_key]
        #     # Also clear our reference so a new one will be created
        #     self._rust_view = None
        #     self._cache_key = None

        # Initialize and render the LiveView content
        # For initial GET request, render the full template (with template inheritance)
        # This ensures the browser gets the complete HTML (DOCTYPE, head, nav, etc.)
        self._initialize_rust_view(request)

        # CRITICAL: Reset VDOM state on GET requests (page loads/reloads)
        # This ensures the client's fresh DOM matches the server's VDOM baseline.
        # In HTTP-only mode, GET requests represent fresh page loads, so the VDOM
        # should start from a clean state to match the client's newly rendered DOM.
        if self._rust_view:
            print(
                "[LiveView] Resetting VDOM state on GET request (HTTP-only mode)", file=sys.stderr
            )
            self._rust_view.reset()

        self._sync_state_to_rust()

        # IMPORTANT: Always call get_template() on GET requests to set _full_template
        # This is needed even if the Rust view is cached, because _full_template
        # is used by render_full_template() to return the complete HTML document
        self.get_template()

        # Initialize baseline VDOM for future patches
        # This establishes the initial VDOM state for diffing on subsequent renders
        _, _, _ = self.render_with_diff(request)

        # Render full template for the browser (includes full HTML structure)
        html = self.render_full_template(request)
        liveview_content = html

        # Wrap in Django template if wrapper_template is specified
        # (This is for the older wrapper pattern, not template inheritance)
        if hasattr(self, "wrapper_template") and self.wrapper_template:
            from django.template import loader

            wrapper = loader.get_template(self.wrapper_template)
            html = wrapper.render({"liveview_content": liveview_content}, request)
            # Inject LiveView content into [data-liveview-root] placeholder
            # Note: liveview_content already includes <div data-liveview-root>...</div>
            html = html.replace("<div data-liveview-root></div>", liveview_content)
        else:
            # No wrapper, return LiveView content directly
            html = liveview_content

        # Debug: Save the rendered HTML to a file for inspection
        if "registration" in request.path:
            form_start = html.find("<form")
            if form_start != -1:
                form_end = html.find("</form>", form_start) + 7
                form_html = html[form_start:form_end]
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".html", prefix="registration_form_"
                ) as f:
                    f.write(form_html)
                    print(f"[LiveView] Saved form HTML to {f.name}", file=sys.stderr)

        # Inject view path into data-liveview-root for WebSocket mounting
        view_path = f"{self.__class__.__module__}.{self.__class__.__name__}"
        html = html.replace(
            "<div data-liveview-root>", f'<div data-liveview-root data-live-view="{view_path}">'
        )

        # Inject LiveView client script
        html = self._inject_client_script(html)

        return HttpResponse(html)

    def post(self, request, *args, **kwargs):
        """Handle POST requests - event handling"""
        from .component import Component, LiveComponent

        try:
            data = json.loads(request.body)
            event_name = data.get("event")
            params = data.get("params", {})

            # Restore state from session
            view_key = f"liveview_{request.path}"
            saved_state = request.session.get(view_key, {})

            # IMPORTANT: Restore state BEFORE calling mount()
            # This ensures components are created with the correct restored state values
            for key, value in saved_state.items():
                if not key.startswith("_") and not callable(value):
                    try:
                        setattr(self, key, value)
                    except AttributeError:
                        # Skip read-only properties
                        pass

            # CRITICAL: Only call mount() if state was not restored from session
            # If state exists in session, we've already restored it above
            # Calling mount() would overwrite the restored state with initial values!
            if not saved_state:
                # No saved state - this is a new session, initialize with mount()
                self.mount(request, **kwargs)
            else:
                # State restored from session - skip mount() to preserve state
                # Components will be recreated with _assign_component_ids() below
                pass

            # Automatically assign deterministic IDs to components based on variable names
            self._assign_component_ids()

            # Restore component state (both Component and LiveComponent)
            # This preserves any component-specific state that wasn't captured by mount()
            component_state = request.session.get(f"{view_key}_components", {})
            for key, state in component_state.items():
                component = getattr(self, key, None)
                # Restore state for both Component and LiveComponent to preserve IDs
                if component and isinstance(component, (Component, LiveComponent)):
                    self._restore_component_state(component, state)

            # Call the event handler
            handler = getattr(self, event_name, None)
            if handler and callable(handler):
                if params:
                    handler(**params)
                else:
                    handler()

            # Save updated state back to session (exclude components)
            updated_context = self.get_context_data()
            state = {k: v for k, v in updated_context.items() if not isinstance(v, LiveComponent)}
            # Serialize to ensure session-compatible types (UUIDs, datetimes, etc.)
            state_json = json.dumps(state, cls=DjangoJSONEncoder)
            state_serializable = json.loads(state_json)
            request.session[view_key] = state_serializable

            # Save updated component state to session (DRY helper method)
            self._save_components_to_session(request, updated_context)

            # Render with diff to get patches
            html, patches_json, version = self.render_with_diff(request)

            import json as json_module

            # Threshold for when to use patches vs full HTML
            PATCH_THRESHOLD = 100

            if patches_json:
                patches = json_module.loads(patches_json)
                patch_count = len(patches)

                # If patches are reasonable size AND non-empty, send ONLY patches
                # Otherwise send full HTML for efficiency
                if patch_count > 0 and patch_count <= PATCH_THRESHOLD:
                    # Send only patches - client will apply them
                    # If patches fail on client, it should reload the page
                    return JsonResponse({"patches": patches, "version": version})
                else:
                    # Empty patches or too many patches - send full HTML instead
                    # Reset VDOM cache since we're sending full HTML
                    # This ensures next patches are calculated from the browser's normalized DOM
                    self._rust_view.reset()
                    return JsonResponse({"html": html, "version": version})
            else:
                # No patches generated - send full HTML
                return JsonResponse({"html": html, "version": version})

        except Exception as e:
            import traceback
            import logging
            from django.conf import settings

            logger = logging.getLogger(__name__)

            # Build detailed error message
            error_msg = f"Error in {self.__class__.__name__}"
            if event_name:
                error_msg += f".{event_name}()"
            error_msg += f": {type(e).__name__}: {str(e)}"

            # Log error with full traceback
            logger.error(error_msg, exc_info=True)

            # In DEBUG mode, include stack trace in response
            if settings.DEBUG:
                error_details = {
                    "error": error_msg,
                    "type": type(e).__name__,
                    "traceback": traceback.format_exc(),
                    "event": event_name,
                    "params": params,
                }
                return JsonResponse(error_details, status=500)
            else:
                # In production, just send user-friendly message
                return JsonResponse(
                    {
                        "error": "An error occurred processing your request. Please try again.",
                        "debug_hint": "Check server logs for details",
                    },
                    status=500,
                )

    def _hydrate_react_components(self, html: str) -> str:
        """
        Post-process HTML to hydrate React component placeholders with server-rendered content.

        The Rust renderer creates <div data-react-component="Name" data-react-props='{...}'>children</div>
        We need to call the Python renderer functions and inject their output.
        """
        import re
        from .react import react_components
        import json as json_module

        # Pattern to match React component divs
        pattern = r'<div data-react-component="([^"]+)" data-react-props=\'([^\']+)\'>(.*?)</div>'

        def replace_component(match):
            component_name = match.group(1)
            props_json = match.group(2)
            children = match.group(3)

            # Parse props
            try:
                props = json_module.loads(props_json)
            except json_module.JSONDecodeError:
                props = {}

            # Resolve any Django template variables in props (like {{ client_count }})
            context = self.get_context_data()
            resolved_props = {}
            for key, value in props.items():
                if isinstance(value, str) and "{{" in value and "}}" in value:
                    # Extract variable name from {{ var_name }}
                    var_match = re.search(r"\{\{\s*(\w+)\s*\}\}", value)
                    if var_match:
                        var_name = var_match.group(1)
                        if var_name in context:
                            resolved_props[key] = context[var_name]
                        else:
                            resolved_props[key] = value
                    else:
                        resolved_props[key] = value
                else:
                    resolved_props[key] = value

            # Get the renderer for this component
            renderer = react_components.get(component_name)

            if renderer:
                # Call the server-side renderer with resolved props
                rendered_content = renderer(resolved_props, children)
                # Create updated props JSON for client-side hydration
                resolved_props_json = json_module.dumps(resolved_props).replace('"', "&quot;")
                # Wrap with data attributes for client-side hydration
                return f"<div data-react-component=\"{component_name}\" data-react-props='{resolved_props_json}'>{rendered_content}</div>"
            else:
                # No renderer found, return placeholder
                return match.group(0)

        # Replace all React component placeholders
        html = re.sub(pattern, replace_component, html, flags=re.DOTALL)

        return html

    def _inject_client_script(self, html: str) -> str:
        """Inject the LiveView client JavaScript into the HTML"""
        from .config import config

        # Get WebSocket setting from config
        use_websocket = config.get("use_websocket", True)
        debug_vdom = config.get("debug_vdom", False)

        config_script = f"""
        <script>
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            window.DJUST_DEBUG_VDOM = {str(debug_vdom).lower()};
        </script>
        """

        script = """
        <script>
            // djust - WebSocket + HTTP Fallback Client

            // === WebSocket LiveView Client ===
            class LiveViewWebSocket {
                constructor() {
                    this.ws = null;
                    this.sessionId = null;
                    this.reconnectAttempts = 0;
                    this.maxReconnectAttempts = 5;
                    this.reconnectDelay = 1000;
                    this.viewMounted = false;
                    this.enabled = true;  // Can be disabled to use HTTP fallback
                }

                connect(url = null) {
                    if (!this.enabled) return;

                    if (!url) {
                        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                        const host = window.location.host;
                        url = `${protocol}//${host}/ws/live/`;
                    }

                    console.log('[LiveView] Connecting to WebSocket:', url);
                    this.ws = new WebSocket(url);

                    this.ws.onopen = (event) => {
                        console.log('[LiveView] WebSocket connected');
                        this.reconnectAttempts = 0;
                    };

                    this.ws.onclose = (event) => {
                        console.log('[LiveView] WebSocket disconnected');
                        this.viewMounted = false;

                        // Clear all decorator state on disconnect
                        // Phase 2: Debounce timers
                        debounceTimers.forEach(state => {
                            if (state.timerId) {
                                clearTimeout(state.timerId);
                            }
                        });
                        debounceTimers.clear();

                        // Phase 2: Throttle timers
                        throttleState.forEach(state => {
                            if (state.timeoutId) {
                                clearTimeout(state.timeoutId);
                            }
                        });
                        throttleState.clear();

                        // Phase 3: Optimistic updates
                        optimisticUpdates.clear();
                        pendingEvents.clear();

                        // Remove loading indicators from DOM
                        document.querySelectorAll('.optimistic-pending').forEach(el => {
                            el.classList.remove('optimistic-pending');
                        });

                        if (this.reconnectAttempts < this.maxReconnectAttempts) {
                            this.reconnectAttempts++;
                            const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
                            console.log(`[LiveView] Reconnecting in ${delay}ms...`);
                            setTimeout(() => this.connect(url), delay);
                        } else {
                            console.warn('[LiveView] Max reconnection attempts reached. Falling back to HTTP mode.');
                            this.enabled = false;
                        }
                    };

                    this.ws.onerror = (error) => {
                        console.error('[LiveView] WebSocket error:', error);
                    };

                    this.ws.onmessage = (event) => {
                        try {
                            const data = JSON.parse(event.data);
                            this.handleMessage(data);
                        } catch (error) {
                            console.error('[LiveView] Failed to parse message:', error);
                        }
                    };
                }

                handleMessage(data) {
                    console.log('[LiveView] Received:', data.type, data);

                    switch (data.type) {
                        case 'connected':
                            this.sessionId = data.session_id;
                            console.log('[LiveView] Session ID:', this.sessionId);
                            this.autoMount();
                            break;

                        case 'mounted':
                            this.viewMounted = true;
                            console.log('[LiveView] View mounted:', data.view);
                            if (data.html) {
                                // Replace page content with server-rendered HTML
                                let container = document.querySelector('[data-live-view]');
                                if (!container) {
                                    container = document.querySelector('[data-liveview-root]');
                                }
                                if (container) {
                                    container.innerHTML = data.html;
                                    bindLiveViewEvents();
                                }
                            }
                            break;

                        case 'patch':
                            if (data.patches && data.patches.length > 0) {
                                console.log('[LiveView] Applying', data.patches.length, 'patches');
                                this.applyPatches(data.patches);
                                bindLiveViewEvents();
                            }
                            break;

                        case 'html_update':
                            // Full HTML update for views with dynamic templates
                            console.log('[LiveView] Applying full HTML update');
                            let container = document.querySelector('[data-liveview-root]');
                            if (container) {
                                container.innerHTML = data.html;
                                bindLiveViewEvents();
                            }
                            break;

                        case 'error':
                            console.error('[LiveView] Server error:', data.error);
                            if (data.traceback) {
                                console.error('Traceback:', data.traceback);
                            }
                            break;

                        case 'pong':
                            // Heartbeat response
                            break;
                    }
                }

                mount(viewPath, params = {}) {
                    if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
                        return false;
                    }

                    console.log('[LiveView] Mounting view:', viewPath);
                    this.ws.send(JSON.stringify({
                        type: 'mount',
                        view: viewPath,
                        params: params
                    }));
                    return true;
                }

                autoMount() {
                    // Look for container with view path
                    let container = document.querySelector('[data-live-view]');
                    if (!container) {
                        // Fallback: look for data-liveview-root with data-live-view attribute
                        container = document.querySelector('[data-liveview-root][data-live-view]');
                    }

                    if (container) {
                        const viewPath = container.dataset.liveView;
                        if (viewPath) {
                            this.mount(viewPath);
                        } else {
                            console.warn('[LiveView] Container found but no view path specified');
                        }
                    } else {
                        console.warn('[LiveView] No LiveView container found for auto-mounting');
                    }
                }

                sendEvent(eventName, params = {}) {
                    if (!this.enabled || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
                        return false;
                    }

                    if (!this.viewMounted) {
                        console.warn('[LiveView] View not mounted. Event ignored:', eventName);
                        return false;
                    }

                    this.ws.send(JSON.stringify({
                        type: 'event',
                        event: eventName,
                        params: params
                    }));
                    return true;
                }

                applyPatches(patches) {
                    patches.forEach(patch => {
                        const element = this.getElementByPath(patch.path);
                        if (!element) {
                            console.warn('[LiveView] Element not found for path:', patch.path);
                            return;
                        }

                        switch (patch.type) {
                            case 'Replace':
                                this.patchReplace(element, patch.node);
                                break;
                            case 'SetText':
                                this.patchSetText(element, patch.text);
                                break;
                            case 'SetAttr':
                                this.patchSetAttr(element, patch.key, patch.value);
                                break;
                            case 'RemoveAttr':
                                this.patchRemoveAttr(element, patch.key);
                                break;
                            case 'InsertChild':
                                this.patchInsertChild(element, patch.index, patch.node);
                                break;
                            case 'RemoveChild':
                                this.patchRemoveChild(element, patch.index);
                                break;
                            case 'MoveChild':
                                this.patchMoveChild(element, patch.from, patch.to);
                                break;
                        }
                    });
                }

                getElementByPath(path) {
                    if (path.length === 0) {
                        return document.querySelector('[data-live-view]') || document.body;
                    }

                    let element = document.querySelector('[data-live-view]') || document.body;
                    for (const index of path) {
                        const children = Array.from(element.childNodes).filter(child =>
                            child.nodeType === Node.ELEMENT_NODE || child.nodeType === Node.TEXT_NODE
                        );
                        if (children[index]) {
                            element = children[index];
                        } else {
                            return null;
                        }
                    }
                    return element;
                }

                patchReplace(element, node) {
                    const newElement = this.vnodeToElement(node);
                    element.replaceWith(newElement);
                }

                patchSetText(element, text) {
                    element.textContent = text;
                }

                patchSetAttr(element, key, value) {
                    if (element.nodeType === Node.ELEMENT_NODE) {
                        element.setAttribute(key, value);
                    }
                }

                patchRemoveAttr(element, key) {
                    if (element.nodeType === Node.ELEMENT_NODE) {
                        element.removeAttribute(key);
                    }
                }

                patchInsertChild(element, index, node) {
                    const newElement = this.vnodeToElement(node);
                    const children = Array.from(element.childNodes).filter(child =>
                        child.nodeType === Node.ELEMENT_NODE || child.nodeType === Node.TEXT_NODE
                    );

                    if (index >= children.length) {
                        element.appendChild(newElement);
                    } else {
                        element.insertBefore(newElement, children[index]);
                    }
                }

                patchRemoveChild(element, index) {
                    const children = Array.from(element.childNodes).filter(child =>
                        child.nodeType === Node.ELEMENT_NODE || child.nodeType === Node.TEXT_NODE
                    );
                    if (children[index]) {
                        children[index].remove();
                    }
                }

                patchMoveChild(element, from, to) {
                    const children = Array.from(element.childNodes).filter(child =>
                        child.nodeType === Node.ELEMENT_NODE || child.nodeType === Node.TEXT_NODE
                    );

                    if (children[from]) {
                        const child = children[from];
                        if (to >= children.length) {
                            element.appendChild(child);
                        } else {
                            element.insertBefore(child, children[to]);
                        }
                    }
                }

                vnodeToElement(vnode) {
                    if (vnode.tag === '#text') {
                        return document.createTextNode(vnode.text || '');
                    }

                    const element = document.createElement(vnode.tag);

                    // Set attributes
                    for (const [key, value] of Object.entries(vnode.attrs || {})) {
                        element.setAttribute(key, value);
                    }

                    // Add children
                    for (const child of vnode.children || []) {
                        element.appendChild(this.vnodeToElement(child));
                    }

                    return element;
                }

                startHeartbeat(interval = 30000) {
                    setInterval(() => {
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send(JSON.stringify({ type: 'ping' }));
                        }
                    }, interval);
                }
            }

            // Global WebSocket instance
            let liveViewWS = null;

            // === HTTP Fallback LiveView Client ===

            // Track VDOM version for synchronization
            let clientVdomVersion = null;

            // State management for decorators
            const debounceTimers = new Map(); // Map<handlerName, {timerId, firstCallTime}>
            const throttleState = new Map();  // Map<handlerName, {lastCall, timeoutId, pendingData}>
            const optimisticUpdates = new Map(); // Map<eventName, {element, originalState}>
            const pendingEvents = new Set(); // Set<eventName> (for loading indicators)

            // Client-side React Counter component (vanilla JS implementation)
            function initReactCounters() {
                document.querySelectorAll('[data-react-component="Counter"]').forEach(container => {
                    const propsJson = container.dataset.reactProps;
                    let props = {};
                    try {
                        props = JSON.parse(propsJson.replace(/&quot;/g, '"'));
                    } catch(e) {}

                    let count = props.initialCount || 0;
                    const display = container.querySelector('.counter-display');
                    const minusBtn = container.querySelectorAll('.btn-sm')[0];
                    const plusBtn = container.querySelectorAll('.btn-sm')[1];

                    if (display && minusBtn && plusBtn) {
                        minusBtn.addEventListener('click', () => {
                            count--;
                            display.textContent = count;
                        });
                        plusBtn.addEventListener('click', () => {
                            count++;
                            display.textContent = count;
                        });
                    }
                });
            }

            // Smart default rate limiting by input type
            // Prevents VDOM version mismatches from high-frequency events
            const DEFAULT_RATE_LIMITS = {
                'range': { type: 'throttle', ms: 150 },      // Sliders
                'number': { type: 'throttle', ms: 100 },     // Number spinners
                'color': { type: 'throttle', ms: 150 },      // Color pickers
                'text': { type: 'debounce', ms: 300 },       // Text inputs
                'search': { type: 'debounce', ms: 300 },     // Search boxes
                'email': { type: 'debounce', ms: 300 },      // Email inputs
                'url': { type: 'debounce', ms: 300 },        // URL inputs
                'tel': { type: 'debounce', ms: 300 },        // Phone inputs
                'password': { type: 'debounce', ms: 300 },   // Password inputs
                'textarea': { type: 'debounce', ms: 300 }    // Multi-line text
            };

            function bindLiveViewEvents() {
                // Find all interactive elements
                const allElements = document.querySelectorAll('*');
                allElements.forEach(element => {
                    // Handle @click events
                    const clickHandler = element.getAttribute('@click');
                    if (clickHandler && !element.dataset.liveviewClickBound) {
                        element.dataset.liveviewClickBound = 'true';
                        element.addEventListener('click', async (e) => {
                            e.preventDefault();

                            // Extract all data-* attributes
                            const params = {};
                            Array.from(element.attributes).forEach(attr => {
                                if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                                    // Convert data-foo-bar to foo_bar
                                    const key = attr.name.substring(5).replace(/-/g, '_');
                                    params[key] = attr.value;
                                }
                            });

                            // Phase 4: Check if event is from a component (walk up DOM tree)
                            let currentElement = e.currentTarget;
                            while (currentElement && currentElement !== document.body) {
                                if (currentElement.dataset.componentId) {
                                    params.component_id = currentElement.dataset.componentId;
                                    break;
                                }
                                currentElement = currentElement.parentElement;
                            }

                            // Pass target element for optimistic updates (Phase 3)
                            params._targetElement = e.currentTarget;

                            await handleEvent(clickHandler, params);
                        });
                    }

                    // Handle @submit events on forms
                    const submitHandler = element.getAttribute('@submit');
                    if (submitHandler && !element.dataset.liveviewSubmitBound) {
                        element.dataset.liveviewSubmitBound = 'true';
                        element.addEventListener('submit', async (e) => {
                            e.preventDefault();
                            const formData = new FormData(e.target);
                            const params = Object.fromEntries(formData.entries());

                            // Phase 4: Check if event is from a component (walk up DOM tree)
                            let currentElement = e.target;
                            while (currentElement && currentElement !== document.body) {
                                if (currentElement.dataset.componentId) {
                                    params.component_id = currentElement.dataset.componentId;
                                    break;
                                }
                                currentElement = currentElement.parentElement;
                            }

                            // Pass target element for optimistic updates (Phase 3)
                            params._targetElement = e.target;

                            await handleEvent(submitHandler, params);
                            e.target.reset();
                        });
                    }

                    // Helper: Extract field name from element attributes
                    // Priority: data-field (explicit) > name (standard) > id (fallback)
                    function getFieldName(element) {
                        if (element.dataset.field) {
                            return element.dataset.field;
                        }
                        if (element.name) {
                            return element.name;
                        }
                        if (element.id) {
                            // Strip common prefixes like 'id_' (Django convention)
                            return element.id.replace(/^id_/, '');
                        }
                        return null;
                    }

                    // Handle @change events
                    const changeHandler = element.getAttribute('@change');
                    if (changeHandler && !element.dataset.liveviewChangeBound) {
                        element.dataset.liveviewChangeBound = 'true';
                        element.addEventListener('change', async (e) => {
                            const params = {};

                            // For checkboxes/radios, send the checked state
                            if (e.target.type === 'checkbox' || e.target.type === 'radio') {
                                params.checked = e.target.checked;
                                params.value = e.target.checked;
                            } else {
                                // For other inputs, send the value
                                params.value = e.target.value;
                            }

                            // Extract all data-* attributes
                            Array.from(e.target.attributes).forEach(attr => {
                                if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                                    // Convert data-foo-bar to foo_bar
                                    const key = attr.name.substring(5).replace(/-/g, '_');
                                    params[key] = attr.value;
                                }
                            });

                            // Auto-extract field name from element attributes
                            const fieldName = getFieldName(e.target);
                            if (fieldName) {
                                params.field_name = fieldName;
                            }

                            // Phase 4: Check if event is from a component (walk up DOM tree)
                            let currentElement = e.target;
                            while (currentElement && currentElement !== document.body) {
                                if (currentElement.dataset.componentId) {
                                    params.component_id = currentElement.dataset.componentId;
                                    break;
                                }
                                currentElement = currentElement.parentElement;
                            }

                            // Pass target element for optimistic updates (Phase 3)
                            params._targetElement = e.target;

                            await handleEvent(changeHandler, params);
                        });
                    }

                    // Handle @input events (real-time typing)
                    const inputHandler = element.getAttribute('@input');
                    if (inputHandler && !element.dataset.liveviewInputBound) {
                        element.dataset.liveviewInputBound = 'true';

                        // Create the base handler function
                        const baseHandler = async (e) => {
                            const params = {};

                            // For checkboxes, send the checked state
                            if (e.target.type === 'checkbox') {
                                params.value = e.target.checked;
                            } else {
                                // For other inputs, send the value
                                params.value = e.target.value;
                            }

                            // Extract all data-* attributes
                            Array.from(e.target.attributes).forEach(attr => {
                                if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                                    // Convert data-foo-bar to foo_bar
                                    const key = attr.name.substring(5).replace(/-/g, '_');
                                    params[key] = attr.value;
                                }
                            });

                            // Auto-extract field name from element attributes
                            const fieldName = getFieldName(e.target);
                            if (fieldName) {
                                params.field_name = fieldName;
                            }

                            // Phase 4: Check if event is from a component (walk up DOM tree)
                            let currentElement = e.target;
                            while (currentElement && currentElement !== document.body) {
                                if (currentElement.dataset.componentId) {
                                    params.component_id = currentElement.dataset.componentId;
                                    break;
                                }
                                currentElement = currentElement.parentElement;
                            }

                            // Pass target element for optimistic updates (Phase 3)
                            params._targetElement = e.target;

                            await handleEvent(inputHandler, params);
                        };

                        // Determine rate limiting strategy (priority order):
                        // 1. Explicit data-throttle or data-debounce attribute
                        // 2. Smart default based on input type
                        // 3. No rate limiting (immediate)
                        let rateLimitedHandler = baseHandler;

                        if (element.dataset.throttle) {
                            // Explicit throttle attribute
                            const ms = parseInt(element.dataset.throttle);
                            rateLimitedHandler = throttle(baseHandler, ms);
                        } else if (element.dataset.debounce) {
                            // Explicit debounce attribute
                            const ms = parseInt(element.dataset.debounce);
                            rateLimitedHandler = debounce(baseHandler, ms);
                        } else {
                            // Check for smart defaults
                            const inputType = element.tagName.toLowerCase() === 'textarea'
                                ? 'textarea'
                                : (element.type || 'text');
                            const defaultLimit = DEFAULT_RATE_LIMITS[inputType];

                            if (defaultLimit) {
                                if (defaultLimit.type === 'throttle') {
                                    rateLimitedHandler = throttle(baseHandler, defaultLimit.ms);
                                } else if (defaultLimit.type === 'debounce') {
                                    rateLimitedHandler = debounce(baseHandler, defaultLimit.ms);
                                }
                            }
                        }

                        // Create optimistic UI handler for immediate feedback
                        // This runs immediately (not rate-limited) to update display
                        const optimisticHandler = (e) => {
                            // Optimistic UI update for high-frequency inputs
                            if (e.target.type === 'range' || e.target.type === 'number' || e.target.type === 'color') {
                                updateDisplayValue(e.target);
                            }

                            // Then call rate-limited server update
                            rateLimitedHandler(e);
                        };

                        element.addEventListener('input', optimisticHandler);
                    }
                });
            }

            // DOM patching utilities
            // Find the root LiveView container
            function getLiveViewRoot() {
                // First, look for explicit LiveView container (used with template inheritance)
                const liveviewContainer = document.querySelector('[data-liveview-root]');
                if (liveviewContainer) {
                    return liveviewContainer;
                }

                // Fallback: first content element, skipping <link>, <style>, <script>
                const NON_CONTENT_TAGS = ['LINK', 'STYLE', 'SCRIPT'];
                for (const child of document.body.childNodes) {
                    if (child.nodeType === Node.ELEMENT_NODE && !NON_CONTENT_TAGS.includes(child.tagName)) {
                        return child;
                    }
                }
                return document.body;
            }

            function getNodeByPath(path) {
                // The Rust VDOM root matches getLiveViewRoot() (first content element)
                // Path [0] = first child of root, [0,0] = first child of that, etc.
                let node = getLiveViewRoot();

                // DEBUG: Log root info
                if (path.length > 0) {
                    console.log(`[DEBUG-ROOT] Starting traversal from <${node.tagName}>, path length=${path.length}, path=${JSON.stringify(path)}`);
                }

                // Empty path means the root itself
                if (path.length === 0) {
                    return node;
                }

                // Traverse the path directly - no adjustment needed since roots are aligned
                for (let i = 0; i < path.length; i++) {
                    const index = path[i];

                    // Get children - Rust DOES filter whitespace-only text nodes
                    // The parser.rs code filters with: if !text.trim().is_empty()
                    // So we should do the same here
                    const children = Array.from(node.childNodes).filter(child => {
                        // Keep element nodes
                        if (child.nodeType === Node.ELEMENT_NODE) return true;
                        // Keep text nodes that have non-whitespace content
                        if (child.nodeType === Node.TEXT_NODE) {
                            return child.textContent.trim().length > 0;
                        }
                        return false;
                    });

                    // DEBUG: Log at first few iterations
                    if (i < 3) {
                        console.log(`[DEBUG-PATH] Step ${i}: index=${index}, children=${children.length}, node=<${node.tagName || node.nodeName}>`);
                        if (children.length === 0) {
                            console.log(`[DEBUG-PATH] ZERO CHILDREN! node.childNodes.length=${node.childNodes.length}`);
                            for (let j = 0; j < node.childNodes.length; j++) {
                                const child = node.childNodes[j];
                                console.log(`  Raw child[${j}]: type=${child.nodeType}, tag=${child.tagName || child.nodeName}`);
                            }
                        }
                    }

                    // DEBUG: Log when accessing the form's parent (card-body)
                    if (i === 4 && path[0] === 0 && path[1] === 0 && path[2] === 0 && path[3] === 1 && path[4] === 2 && path.length > 5) {
                        console.log(`[DEBUG] About to descend into child[${index}] of card-body`);
                        console.log(`[DEBUG] card-body has ${children.length} children, about to access index ${index}`);
                        if (index < children.length && children[index].nodeType === Node.ELEMENT_NODE) {
                            const target = children[index];
                            console.log(`[DEBUG] Target is <${target.tagName.toLowerCase()}>`);
                            // Now show what THIS element's children are
                            const targetChildren = Array.from(target.childNodes).filter(child => {
                                if (child.nodeType === Node.ELEMENT_NODE) return true;
                                if (child.nodeType === Node.TEXT_NODE) {
                                    return child.textContent.trim().length > 0;
                                }
                                return false;
                            });
                            console.log(`[DEBUG] This element has ${targetChildren.length} filtered children`);
                            targetChildren.forEach((child, idx) => {
                                if (child.nodeType === Node.ELEMENT_NODE) {
                                    console.log(`  [${idx}] <${child.tagName.toLowerCase()} class="${child.className || ''}">`);
                                }
                            });
                        }
                    }

                    if (index >= children.length) {
                        console.warn(`[LiveView] Index ${index} out of bounds, only ${children.length} children at path`, path.slice(0, i+1));
                        return null;
                    }

                    node = children[index];
                }
                return node;
            }

            function createNodeFromVNode(vnode) {
                // VNode structure from Rust: { tag, text?, attrs, children, key? }
                // Text nodes have tag === "#text" and a text field
                if (vnode.tag === '#text') {
                    return document.createTextNode(vnode.text || '');
                }

                // Element nodes
                const elem = document.createElement(vnode.tag);

                // Set attributes and handle events
                if (vnode.attrs) {
                    for (const [key, value] of Object.entries(vnode.attrs)) {
                        // Handle LiveView event attributes (@click, @change, etc.)
                        if (key.startsWith('@')) {
                            const eventName = key.substring(1);
                            elem.addEventListener(eventName, (e) => {
                                e.preventDefault();

                                // Extract data-* attributes just like bindLiveViewEvents() does
                                const params = {};
                                Array.from(elem.attributes).forEach(attr => {
                                    if (attr.name.startsWith('data-') && !attr.name.startsWith('data-liveview')) {
                                        const key = attr.name.substring(5).replace(/-/g, '_');
                                        params[key] = attr.value;
                                    }
                                });

                                // Phase 4: Check if event is from a component (walk up DOM tree)
                                let currentElement = elem;
                                while (currentElement && currentElement !== document.body) {
                                    if (currentElement.dataset.componentId) {
                                        params.component_id = currentElement.dataset.componentId;
                                        break;
                                    }
                                    currentElement = currentElement.parentElement;
                                }

                                handleEvent(value, params);
                            });
                        } else {
                            // Special handling for input value property
                            if (key === 'value' && (elem.tagName === 'INPUT' || elem.tagName === 'TEXTAREA')) {
                                elem.value = value;
                            }
                            // Regular HTML attributes
                            elem.setAttribute(key, value);
                        }
                    }
                }

                // Add children recursively
                if (vnode.children && vnode.children.length > 0) {
                    for (const child of vnode.children) {
                        const childNode = createNodeFromVNode(child);
                        if (childNode) {
                            elem.appendChild(childNode);
                        }
                    }
                }

                return elem;
            }

            // Debug helper: log DOM structure
            function debugNode(node, prefix = '') {
                const children = Array.from(node.childNodes).filter(child => {
                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                    if (child.nodeType === Node.TEXT_NODE) {
                        return child.textContent.trim().length > 0;
                    }
                    return false;
                });

                return {
                    tag: node.tagName || 'TEXT',
                    text: node.nodeType === Node.TEXT_NODE ? node.textContent.substring(0, 20) : null,
                    childCount: children.length,
                    children: children.slice(0, 3).map((c, i) => `[${i}]: ${c.tagName || 'TEXT'}`)
                };
            }

            function applyPatches(patches) {
                // patches is already a JavaScript array (parsed by response.json())
                if (window.DJUST_DEBUG_VDOM) {
                    console.log('[LiveView] Applying', patches.length, 'patches');
                }

                // Sort patches to ensure RemoveChild operations are applied in descending index order
                // within the same path. This prevents index invalidation when removing multiple children.
                patches.sort((a, b) => {
                    // RemoveChild patches should come before other types at the same path
                    if (a.type === 'RemoveChild' && b.type === 'RemoveChild') {
                        // Same path? Sort by index descending (higher indices first)
                        const pathA = JSON.stringify(a.path);
                        const pathB = JSON.stringify(b.path);
                        if (pathA === pathB) {
                            return b.index - a.index; // Descending order
                        }
                    }
                    return 0; // Maintain relative order for other patches
                });

                // Debug: log patch types for small patch sets
                if (window.DJUST_DEBUG_VDOM && patches.length > 0 && patches.length <= 20) {
                    console.log('[LiveView] Patch count:', patches.length);
                    for (let i = 0; i < Math.min(5, patches.length); i++) {
                        console.log('  Patch', i, ':', patches[i].type, 'at', patches[i].path);
                    }
                }

                let failedCount = 0;
                let successCount = 0;
                for (const patch of patches) {
                    const node = getNodeByPath(patch.path);
                    if (!node) {
                        failedCount++;
                        if (failedCount <= 3) {
                            // Log first 3 failures in detail
                            const patchType = Object.keys(patch).filter(k => k !== 'path')[0];
                            console.warn(`[LiveView] Failed patch #${failedCount} at path:`, patch.path);
                            console.warn('Patch type:', patchType, patch[patchType]);

                            // Try to traverse as far as we can to see where it breaks
                            let debugNode = getLiveViewRoot();

                            for (let i = 0; i < patch.path.length; i++) {
                                const children = Array.from(debugNode.childNodes).filter(child => {
                                    if (child.nodeType === Node.ELEMENT_NODE) return true;
                                    if (child.nodeType === Node.TEXT_NODE) return child.textContent.trim().length > 0;
                                    return false;
                                });

                                console.warn(`  Path[${i}] = ${patch.path[i]}, available children:`, children.length,
                                    children.map((c,idx) => `[${idx}]=${c.tagName||'TEXT'}`).join(', '));

                                if (patch.path[i] >= children.length) {
                                    console.warn(`  FAILED: Index ${patch.path[i]} out of bounds (only ${children.length} children)`);
                                    console.warn(`  Current node HTML (first 500 chars):`);
                                    console.warn(debugNode.outerHTML ? debugNode.outerHTML.substring(0, 500) : 'N/A');
                                    break;
                                }

                                debugNode = children[patch.path[i]];

                                // After moving to the next node, show what it contains
                                if (i === 3) { // At the critical failing level [3, 0, 2, 1]
                                    console.warn(`  >>> At critical path[3], showing node details:`);
                                    console.warn(`      Tag: ${debugNode.tagName}, ID: ${debugNode.id || 'none'}, Class: ${debugNode.className || 'none'}`);
                                    console.warn(`      HTML (first 800 chars): ${debugNode.outerHTML ? debugNode.outerHTML.substring(0, 800) : 'N/A'}`);
                                }
                            }
                        }
                        continue;
                    }

                    successCount++;

                    if (patch.type === 'Replace') {
                        const newNode = createNodeFromVNode(patch.node);
                        node.parentNode.replaceChild(newNode, node);
                    } else if (patch.type === 'SetText') {
                        node.textContent = patch.text;
                    } else if (patch.type === 'SetAttr') {
                        // Special handling for input value to preserve user input
                        if (patch.key === 'value' && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA')) {
                            // Only update if the element is not currently focused (user is typing)
                            if (document.activeElement !== node) {
                                node.value = patch.value;
                            }
                            // Always update the attribute for consistency
                            node.setAttribute(patch.key, patch.value);
                        } else {
                            node.setAttribute(patch.key, patch.value);
                        }
                    } else if (patch.type === 'RemoveAttr') {
                        if (window.DJUST_DEBUG_VDOM) {
                            console.log(`[LiveView] Patch RemoveAttr '${patch.key}' at path (${patch.path.length}) [${patch.path.join(', ')}]`);
                            console.log(`[LiveView]   Target element: <${node.tagName || 'unknown'}> with ${(node.children || []).length} children`);
                            if (node.id) console.log(`[LiveView]     id="${node.id}"`);
                            if (node.className) console.log(`[LiveView]     class="${node.className}"`);
                        }
                        node.removeAttribute(patch.key);
                    } else if (patch.type === 'InsertChild') {
                        const newChild = createNodeFromVNode(patch.node);
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const refChild = children[patch.index];
                        if (refChild) {
                            node.insertBefore(newChild, refChild);
                        } else {
                            node.appendChild(newChild);
                        }
                    } else if (patch.type === 'RemoveChild') {
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const child = children[patch.index];
                        if (child) {
                            node.removeChild(child);
                        }
                    } else if (patch.type === 'MoveChild') {
                        // Use filtered children to match path traversal
                        const children = Array.from(node.childNodes).filter(child => {
                            if (child.nodeType === Node.ELEMENT_NODE) return true;
                            if (child.nodeType === Node.TEXT_NODE) {
                                return child.textContent.trim().length > 0;
                            }
                            return false;
                        });
                        const child = children[patch.from];
                        if (child) {
                            const refChild = children[patch.to];
                            if (refChild) {
                                node.insertBefore(child, refChild);
                            } else {
                                node.appendChild(child);
                            }
                        }
                    }
                }

                if (window.DJUST_DEBUG_VDOM) {
                    console.log(`[LiveView] Patch summary: ${successCount} succeeded, ${failedCount} failed`);
                }

                // If any patches failed, return false to trigger page reload
                if (failedCount > 0) {
                    console.error(`[LiveView] ${failedCount} patches failed, page will reload`);
                    return false;
                }

                return true; // All patches applied successfully
            }

            // Throttle utility: Limits function calls to at most once per interval
            // Guarantees the last call will always execute (trailing edge)
            function throttle(func, interval) {
                let lastCall = 0;
                let timeout = null;

                return function(...args) {
                    const now = Date.now();
                    const timeSinceLastCall = now - lastCall;

                    if (timeSinceLastCall >= interval) {
                        // Enough time has passed, execute immediately
                        lastCall = now;
                        func.apply(this, args);
                    } else {
                        // Too soon, schedule for later (ensures last value is sent)
                        clearTimeout(timeout);
                        timeout = setTimeout(() => {
                            lastCall = Date.now();
                            func.apply(this, args);
                        }, interval - timeSinceLastCall);
                    }
                };
            }

            // Debounce utility: Delays function call until after wait period of inactivity
            // Resets timer on each call (only executes after user stops)
            function debounce(func, wait) {
                let timeout = null;

                return function(...args) {
                    clearTimeout(timeout);
                    timeout = setTimeout(() => {
                        func.apply(this, args);
                    }, wait);
                };
            }

            // Optimistic UI update: Immediately update display elements
            // This provides instant visual feedback before server responds
            function updateDisplayValue(inputElement) {
                const value = inputElement.value;
                let updated = false;

                // Strategy 1: Check for explicit data-display-target attribute
                const targetId = inputElement.dataset.displayTarget;
                if (targetId) {
                    const targetElement = document.getElementById(targetId);
                    if (targetElement) {
                        targetElement.textContent = value;
                        updated = true;
                    }
                }

                // Strategy 2: Find nearby <output> element (semantic HTML)
                const outputElement = inputElement.parentElement?.querySelector('output');
                if (outputElement) {
                    outputElement.textContent = value;
                    updated = true;
                }

                // Strategy 3: Find badge in sibling label (Range component pattern)
                // Range structure: <label>Volume <span class="badge">50</span></label><input>
                const parentDiv = inputElement.parentElement;
                if (parentDiv) {
                    // Find label sibling
                    const label = parentDiv.querySelector('label');
                    if (label) {
                        const badge = label.querySelector('.badge');
                        if (badge) {
                            badge.textContent = value;
                            updated = true;
                        }
                    }
                }

                // Strategy 4: Find nearby <strong> element (percentage display)
                // Look in parent and grandparent for <strong> elements
                const searchContainers = [
                    inputElement.parentElement,
                    inputElement.parentElement?.parentElement
                ];

                for (const container of searchContainers) {
                    if (container) {
                        const strongElements = container.querySelectorAll('strong');
                        strongElements.forEach(strong => {
                            // Check if it contains a number (likely our display)
                            if (strong.textContent.match(/\d+/)) {
                                // Update with or without % suffix based on original content
                                if (strong.textContent.includes('%')) {
                                    strong.textContent = `${value}%`;
                                } else {
                                    strong.textContent = value;
                                }
                                updated = true;
                            }
                        });
                    }
                }

                // Strategy 5: Find sibling <span> or <div> with .value or .display class
                if (!updated && parentDiv) {
                    const displayElement =
                        parentDiv.querySelector('.value') ||
                        parentDiv.querySelector('.display') ||
                        parentDiv.querySelector('span[class*="value"]');

                    if (displayElement) {
                        displayElement.textContent = value;
                        updated = true;
                    }
                }
            }

            // ============================================================================
            // Decorator Functions (Phase 2: Debounce/Throttle, Phase 3: Optimistic)
            // ============================================================================

            /**
             * Debounce an event - delay until user stops triggering events
             */
            function debounceEvent(eventName, eventData, config) {
                const { wait, max_wait } = config;
                const now = Date.now();

                // Get or create state
                let state = debounceTimers.get(eventName);
                if (!state) {
                    state = { timerId: null, firstCallTime: now };
                    debounceTimers.set(eventName, state);
                }

                // Clear existing timer
                if (state.timerId) {
                    clearTimeout(state.timerId);
                }

                // Check if we've exceeded max_wait
                if (max_wait && (now - state.firstCallTime) >= (max_wait * 1000)) {
                    // Force execution - max wait exceeded
                    sendEventImmediate(eventName, eventData);
                    debounceTimers.delete(eventName);
                    if (window.djustDebug) {
                        console.log(`[LiveView:debounce] Force executing ${eventName} (max_wait exceeded)`);
                    }
                    return;
                }

                // Set new timer
                state.timerId = setTimeout(() => {
                    sendEventImmediate(eventName, eventData);
                    debounceTimers.delete(eventName);
                    if (window.djustDebug) {
                        console.log(`[LiveView:debounce] Executing ${eventName} after ${wait}s wait`);
                    }
                }, wait * 1000);

                if (window.djustDebug) {
                    console.log(`[LiveView:debounce] Debouncing ${eventName} (wait: ${wait}s, max_wait: ${max_wait || 'none'})`);
                }
            }

            /**
             * Throttle an event - limit execution frequency
             */
            function throttleEvent(eventName, eventData, config) {
                const { interval, leading, trailing } = config;
                const now = Date.now();

                if (!throttleState.has(eventName)) {
                    // First call - execute immediately if leading=true
                    if (leading) {
                        sendEventImmediate(eventName, eventData);
                        if (window.djustDebug) {
                            console.log(`[LiveView:throttle] Executing ${eventName} (leading edge)`);
                        }
                    }

                    // Set up state
                    const state = {
                        lastCall: leading ? now : 0,
                        timeoutId: null,
                        pendingData: null
                    };

                    throttleState.set(eventName, state);

                    // Schedule trailing call if needed
                    if (trailing && !leading) {
                        state.pendingData = eventData;
                        state.timeoutId = setTimeout(() => {
                            sendEventImmediate(eventName, state.pendingData);
                            throttleState.delete(eventName);
                            if (window.djustDebug) {
                                console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge - no leading)`);
                            }
                        }, interval * 1000);
                    }

                    return;
                }

                const state = throttleState.get(eventName);
                const elapsed = now - state.lastCall;

                if (elapsed >= (interval * 1000)) {
                    // Enough time has passed - execute now
                    sendEventImmediate(eventName, eventData);
                    state.lastCall = now;
                    state.pendingData = null;

                    // Clear any pending trailing call
                    if (state.timeoutId) {
                        clearTimeout(state.timeoutId);
                        state.timeoutId = null;
                    }

                    if (window.djustDebug) {
                        console.log(`[LiveView:throttle] Executing ${eventName} (interval elapsed: ${elapsed}ms)`);
                    }
                } else if (trailing) {
                    // Update pending data and reschedule trailing call
                    state.pendingData = eventData;

                    if (state.timeoutId) {
                        clearTimeout(state.timeoutId);
                    }

                    const remaining = (interval * 1000) - elapsed;
                    state.timeoutId = setTimeout(() => {
                        if (state.pendingData) {
                            sendEventImmediate(eventName, state.pendingData);
                            if (window.djustDebug) {
                                console.log(`[LiveView:throttle] Executing ${eventName} (trailing edge)`);
                            }
                        }
                        throttleState.delete(eventName);
                    }, remaining);

                    if (window.djustDebug) {
                        console.log(`[LiveView:throttle] Throttled ${eventName} (${remaining}ms until trailing)`);
                    }
                } else {
                    if (window.djustDebug) {
                        console.log(`[LiveView:throttle] Dropped ${eventName} (within interval, no trailing)`);
                    }
                }
            }

            /**
             * Send event immediately (bypassing decorators)
             */
            async function sendEventImmediate(eventName, params) {
                // This function delegates to the main handleEvent flow
                // We mark it as immediate to skip decorator checks
                params._skipDecorators = true;
                await handleEvent(eventName, params);
            }

            /**
             * Apply optimistic DOM updates before server validation
             */
            function applyOptimisticUpdate(eventName, params, targetElement) {
                if (!targetElement) {
                    return;
                }

                // Save original state before update
                saveOptimisticState(eventName, targetElement);

                // Apply update based on element type
                if (targetElement.type === 'checkbox' || targetElement.type === 'radio') {
                    optimisticToggle(targetElement, params);
                } else if (targetElement.tagName === 'INPUT' || targetElement.tagName === 'TEXTAREA') {
                    optimisticInputUpdate(targetElement, params);
                } else if (targetElement.tagName === 'SELECT') {
                    optimisticSelectUpdate(targetElement, params);
                } else if (targetElement.tagName === 'BUTTON') {
                    optimisticButtonUpdate(targetElement, params);
                }

                // Add loading indicator
                targetElement.classList.add('optimistic-pending');
                pendingEvents.add(eventName);

                if (window.djustDebug) {
                    console.log('[LiveView:optimistic] Applied optimistic update:', eventName);
                }
            }

            function saveOptimisticState(eventName, element) {
                const originalState = {};

                if (element.type === 'checkbox' || element.type === 'radio') {
                    originalState.checked = element.checked;
                }
                if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA' || element.tagName === 'SELECT') {
                    originalState.value = element.value;
                }
                if (element.tagName === 'BUTTON') {
                    originalState.disabled = element.disabled;
                    originalState.text = element.textContent;
                }

                optimisticUpdates.set(eventName, {
                    element: element,
                    originalState: originalState
                });
            }

            function clearOptimisticState(eventName) {
                if (optimisticUpdates.has(eventName)) {
                    const { element, originalState } = optimisticUpdates.get(eventName);

                    // Remove loading indicator
                    element.classList.remove('optimistic-pending');

                    // Restore original button state (if button)
                    if (element.tagName === 'BUTTON') {
                        if (originalState.disabled !== undefined) {
                            element.disabled = originalState.disabled;
                        }
                        if (originalState.text !== undefined) {
                            element.textContent = originalState.text;
                        }
                    }

                    optimisticUpdates.delete(eventName);
                }
                pendingEvents.delete(eventName);
            }

            function revertOptimisticUpdate(eventName) {
                if (!optimisticUpdates.has(eventName)) {
                    return;
                }

                const { element, originalState } = optimisticUpdates.get(eventName);

                // Restore original state
                if (originalState.checked !== undefined) {
                    element.checked = originalState.checked;
                }
                if (originalState.value !== undefined) {
                    element.value = originalState.value;
                }
                if (originalState.disabled !== undefined) {
                    element.disabled = originalState.disabled;
                }
                if (originalState.text !== undefined) {
                    element.textContent = originalState.text;
                }

                // Add error indicator
                element.classList.remove('optimistic-pending');
                element.classList.add('optimistic-error');
                setTimeout(() => element.classList.remove('optimistic-error'), 2000);

                clearOptimisticState(eventName);

                if (window.djustDebug) {
                    console.log('[LiveView:optimistic] Reverted optimistic update:', eventName);
                }
            }

            function optimisticToggle(element, params) {
                if (params.checked !== undefined) {
                    element.checked = params.checked;
                } else {
                    element.checked = !element.checked;
                }
            }

            function optimisticInputUpdate(element, params) {
                if (params.value !== undefined) {
                    element.value = params.value;
                }
            }

            function optimisticSelectUpdate(element, params) {
                if (params.value !== undefined) {
                    element.value = params.value;
                }
            }

            function optimisticButtonUpdate(element, params) {
                element.disabled = true;
                if (element.hasAttribute('data-loading-text')) {
                    element.textContent = element.getAttribute('data-loading-text');
                }
            }

            // ============================================================================
            // Event Handling
            // ============================================================================

            async function handleEvent(eventName, params) {
                console.log('[LiveView] Event:', eventName, params);

                // Skip decorator checks if this is an immediate send (from decorator)
                if (!params._skipDecorators) {
                    // Check for handler metadata (decorators)
                    const metadata = window.handlerMetadata?.[eventName];

                    // Warn if multiple decorators present (ambiguous behavior)
                    if (metadata?.debounce && metadata?.throttle) {
                        console.warn(
                            `[LiveView] Handler '${eventName}' has both @debounce and @throttle decorators. ` +
                            `Applying @debounce only. Use one decorator per handler.`
                        );
                    }

                    // Apply optimistic update FIRST (Phase 3)
                    // This happens immediately, regardless of debounce/throttle
                    if (metadata?.optimistic && params._targetElement) {
                        applyOptimisticUpdate(eventName, params, params._targetElement);
                    }

                    // Apply debounce if configured (Phase 2)
                    if (metadata?.debounce) {
                        debounceEvent(eventName, params, metadata.debounce);
                        return; // Don't send immediately
                    }

                    // Apply throttle if configured (Phase 2)
                    if (metadata?.throttle) {
                        throttleEvent(eventName, params, metadata.throttle);
                        return; // Don't send immediately
                    }
                }

                // Remove internal flags before sending
                delete params._skipDecorators;

                // Try WebSocket first
                if (liveViewWS && liveViewWS.sendEvent(eventName, params)) {
                    console.log('[LiveView] Event sent via WebSocket');
                    return;  // WebSocket handled it
                }

                // Fallback to HTTP
                console.log('[LiveView] Using HTTP fallback');
                try {
                    const response = await fetch(window.location.href.split('?')[0], {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': getCookie('csrftoken'),
                        },
                        body: JSON.stringify({
                            event: eventName,
                            params: params
                        })
                    });

                    if (response.ok) {
                        const data = await response.json();

                        // Check for version mismatch
                        if (data.version !== undefined) {
                            if (clientVdomVersion === null) {
                                // First response, initialize version
                                clientVdomVersion = data.version;
                                console.log('[LiveView] Initialized VDOM version:', clientVdomVersion);
                            } else if (clientVdomVersion !== data.version - 1) {
                                // Version mismatch detected! Client and server are out of sync
                                console.warn('[LiveView] VDOM version mismatch detected!');
                                console.warn(`  Client expected version ${clientVdomVersion + 1}, but server is at ${data.version}`);
                                console.warn('  Clearing client VDOM cache and using full HTML');

                                // Clear optimistic state before full reload
                                clearOptimisticState(eventName);

                                // Force full HTML reload to resync
                                if (data.html) {
                                    const parser = new DOMParser();
                                    const doc = parser.parseFromString(data.html, 'text/html');
                                    const liveviewRoot = getLiveViewRoot();
                                    const newRoot = doc.querySelector('[data-liveview-root]') || doc.body;
                                    liveviewRoot.innerHTML = newRoot.innerHTML;
                                    clientVdomVersion = data.version;
                                    initReactCounters();
                                    initTodoItems();
                                    bindLiveViewEvents();
                                }
                                return;
                            }
                            // Update client version
                            clientVdomVersion = data.version;
                        }

                        // Clear optimistic state BEFORE applying patches (Phase 3)
                        // This restores button state before DOM is modified
                        clearOptimisticState(eventName);

                        if (data.patches && Array.isArray(data.patches) && data.patches.length > 0) {
                            // Try to apply DOM patches (efficient!)
                            console.log('[LiveView] Applying', data.patches.length, 'patches');
                            const success = applyPatches(data.patches);
                            if (success === false) {
                                // Patches failed - reload the page to get fresh state
                                console.error('[LiveView] Patches failed to apply, reloading page...');
                                window.location.reload();
                                return;
                            }
                            console.log('[LiveView] Patches applied successfully');
                            // Re-bind event handlers to new/modified elements
                            initReactCounters();
                            initTodoItems();
                            bindLiveViewEvents();
                        } else if (data.html) {
                            // Server sent full HTML (no patches available)
                            console.log('[LiveView] Applying HTML update (length:', data.html.length, 'chars)');
                            const parser = new DOMParser();
                            const doc = parser.parseFromString(data.html, 'text/html');
                            const liveviewRoot = getLiveViewRoot();
                            const newRoot = doc.querySelector('[data-liveview-root]') || doc.body;
                            liveviewRoot.innerHTML = newRoot.innerHTML;
                            console.log('[LiveView] HTML update applied');
                            // Re-bind event handlers to new elements
                            initReactCounters();
                            initTodoItems();
                            bindLiveViewEvents();
                        } else {
                            console.warn('[LiveView] Response has neither patches nor html!', data);
                        }
                    } else {
                        // Handle HTTP errors (500, 404, etc.)
                        const errorData = await response.json().catch(() => ({}));

                        console.error('[LiveView] Server Error:', {
                            status: response.status,
                            statusText: response.statusText,
                            error: errorData
                        });

                        // Revert optimistic update on error (Phase 3)
                        revertOptimisticUpdate(eventName);

                        // Show user-friendly error notification
                        showErrorNotification(errorData, response.status);
                    }
                } catch (error) {
                    console.error('[LiveView] Network Error:', error);

                    // Revert optimistic update on network error (Phase 3)
                    revertOptimisticUpdate(eventName);

                    showErrorNotification({ error: 'Network error occurred. Please check your connection.' }, 0);
                }
            }

            function showErrorNotification(errorData, statusCode) {
                // Create error notification element
                const notification = document.createElement('div');
                notification.style.cssText = `
                    position: fixed;
                    top: 20px;
                    right: 20px;
                    max-width: 500px;
                    background: #dc3545;
                    color: white;
                    padding: 16px 20px;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                    z-index: 10000;
                    animation: slideIn 0.3s ease-out;
                `;

                // Build error message
                let message = '';
                if (errorData.error) {
                    message = `<strong>Error:</strong> ${escapeHtml(errorData.error)}`;

                    // Show detailed error info if available (DEBUG mode)
                    if (errorData.type) {
                        message += `<br><small><strong>${escapeHtml(errorData.type)}</strong></small>`;
                    }
                    if (errorData.event) {
                        message += `<br><small>Event: <code style="background: rgba(0,0,0,0.2); padding: 2px 4px; border-radius: 3px;">${escapeHtml(errorData.event)}</code></small>`;
                    }
                    if (errorData.traceback) {
                        message += `<br><details style="margin-top: 8px; cursor: pointer;">
                            <summary style="font-size: 12px;">Show Traceback</summary>
                            <pre style="font-size: 11px; background: rgba(0,0,0,0.3); padding: 8px; border-radius: 4px; overflow-x: auto; margin-top: 4px;">${escapeHtml(errorData.traceback)}</pre>
                        </details>`;
                    }
                } else {
                    message = `<strong>Error ${statusCode}:</strong> An error occurred while processing your request.`;
                }

                notification.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 12px;">
                        <div style="flex: 1;">${message}</div>
                        <button onclick="this.parentElement.parentElement.remove()"
                                style="background: none; border: none; color: white; font-size: 20px;
                                       cursor: pointer; padding: 0; line-height: 1; opacity: 0.8;"
                                title="Close">×</button>
                    </div>
                `;

                // Add animation styles
                const style = document.createElement('style');
                style.textContent = `
                    @keyframes slideIn {
                        from { transform: translateX(400px); opacity: 0; }
                        to { transform: translateX(0); opacity: 1; }
                    }
                `;
                if (!document.querySelector('style[data-liveview-error-styles]')) {
                    style.setAttribute('data-liveview-error-styles', '');
                    document.head.appendChild(style);
                }

                document.body.appendChild(notification);

                // Auto-remove after 10 seconds
                setTimeout(() => {
                    notification.style.animation = 'slideIn 0.3s ease-out reverse';
                    setTimeout(() => notification.remove(), 300);
                }, 10000);
            }

            function escapeHtml(text) {
                const div = document.createElement('div');
                div.textContent = text;
                return div.innerHTML;
            }

            function getCookie(name) {
                let cookieValue = null;
                if (document.cookie && document.cookie !== '') {
                    const cookies = document.cookie.split(';');
                    for (let i = 0; i < cookies.length; i++) {
                        const cookie = cookies[i].trim();
                        if (cookie.substring(0, name.length + 1) === (name + '=')) {
                            cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                            break;
                        }
                    }
                }
                return cookieValue;
            }

            // Client-side TodoItem interactions (only for React demo TodoItems)
            function initTodoItems() {
                // Handle checkbox changes for .todo-checkbox (React demo TodoItems)
                document.querySelectorAll('.todo-checkbox').forEach(checkbox => {
                    if (!checkbox.dataset.reactInitialized) {
                        checkbox.dataset.reactInitialized = 'true';
                        checkbox.addEventListener('change', function() {
                            const todoItem = this.closest('.todo-item');
                            if (this.checked) {
                                todoItem.classList.add('completed');
                            } else {
                                todoItem.classList.remove('completed');
                            }
                        });
                    }
                });

                // Handle delete button clicks for React demo TodoItems
                document.querySelectorAll('.todo-delete').forEach(button => {
                    if (!button.dataset.reactInitialized) {
                        button.dataset.reactInitialized = 'true';
                        button.addEventListener('click', async function() {
                            const todoText = this.dataset.todoText;
                            await handleEvent('delete_todo_item', { text: todoText });
                        });
                    }
                });
            }

            // Inject CSS styles for optimistic updates (Phase 3)
            function injectOptimisticStyles() {
                if (document.getElementById('djust-optimistic-styles')) {
                    return; // Already injected
                }

                const styles = `
                    /* Optimistic update loading indicators */
                    .optimistic-pending {
                        opacity: 0.6;
                        cursor: wait !important;
                        position: relative;
                    }

                    .optimistic-pending::after {
                        content: '';
                        position: absolute;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        background: rgba(255, 255, 255, 0.1);
                        pointer-events: none;
                    }

                    .optimistic-error {
                        animation: djust-shake 0.5s;
                        border-color: #dc3545 !important;
                    }

                    @keyframes djust-shake {
                        0%, 100% { transform: translateX(0); }
                        25% { transform: translateX(-5px); }
                        75% { transform: translateX(5px); }
                    }
                `;

                const styleElement = document.createElement('style');
                styleElement.id = 'djust-optimistic-styles';
                styleElement.textContent = styles;
                document.head.appendChild(styleElement);
            }

            document.addEventListener('DOMContentLoaded', function() {
                console.log('[LiveView] Initialized with Rust-powered rendering');

                // Expose handleEvent globally for custom scripts (e.g., throttle/debounce demos)
                window.djustHandleEvent = handleEvent;

                // Inject optimistic update CSS (Phase 3)
                injectOptimisticStyles();

                // Check if WebSocket is enabled (can be disabled via config)
                const useWebSocket = window.DJUST_USE_WEBSOCKET !== false;

                if (useWebSocket) {
                    // Initialize WebSocket connection
                    console.log('[LiveView] Using WebSocket mode');
                    liveViewWS = new LiveViewWebSocket();
                    liveViewWS.connect();
                    liveViewWS.startHeartbeat();
                } else {
                    // HTTP-only mode (no WebSocket)
                    console.log('[LiveView] Using HTTP-only mode (WebSocket disabled)');
                }

                initReactCounters();  // Initialize client-side React components
                initTodoItems();      // Initialize todo item checkboxes
                bindLiveViewEvents();

                // Expose sendEvent globally for manual event handling (e.g., optimistic updates)
                window.sendEvent = handleEvent;
            });
        </script>
        """

        # Inject both config and main script
        full_script = config_script + script

        if "</body>" in html:
            html = html.replace("</body>", f"{full_script}</body>")
        else:
            html += full_script

        return html


def live_view(template_name: Optional[str] = None, template_string: Optional[str] = None):
    """
    Decorator to convert a function-based view into a LiveView.

    Usage:
        @live_view(template_name='counter.html')
        def counter_view(request):
            count = 0

            def increment():
                nonlocal count
                count += 1

            def decrement():
                nonlocal count
                count -= 1

            return locals()

    Args:
        template_name: Path to Django template
        template_string: Inline template string

    Returns:
        View function
    """

    def decorator(func: Callable) -> Callable:
        def wrapper(request, *args, **kwargs):
            # Create a dynamic LiveView class
            class DynamicLiveView(LiveView):
                pass

            if template_name:
                DynamicLiveView.template_name = template_name
            if template_string:
                DynamicLiveView.template_string = template_string

            view = DynamicLiveView()

            # Execute the function to get initial state
            result = func(request, *args, **kwargs)
            if isinstance(result, dict):
                for key, value in result.items():
                    if not callable(value):
                        setattr(view, key, value)
                    else:
                        setattr(view, key, value)

            # Handle the request
            if request.method == "GET":
                return view.get(request, *args, **kwargs)
            elif request.method == "POST":
                return view.post(request, *args, **kwargs)

        return wrapper

    return decorator
