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


# Default TTL for sessions (1 hour)
DEFAULT_SESSION_TTL = 3600


def cleanup_expired_sessions(ttl: Optional[int] = None) -> int:
    """
    Clean up expired LiveView sessions from state backend.

    Args:
        ttl: Time to live in seconds. Defaults to DEFAULT_SESSION_TTL.

    Returns:
        Number of sessions cleaned up
    """
    from .state_backend import get_backend

    backend = get_backend()
    return backend.cleanup_expired(ttl)


def get_session_stats() -> Dict[str, Any]:
    """
    Get statistics about cached LiveView sessions from state backend.

    Returns:
        Dictionary with cache statistics
    """
    from .state_backend import get_backend

    backend = get_backend()
    return backend.get_stats()


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
    template: Optional[str] = None
    use_actors: bool = False  # Enable Tokio actor-based state management (Phase 5+)

    # ============================================================================
    # INITIALIZATION & SETUP
    # ============================================================================

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
        if self.template:
            return self.template
        elif self.template_name:
            # Load the raw template source
            from django.template import loader
            from django.conf import settings

            template = loader.get_template(self.template_name)
            template_source = template.template.source

            # Check if template uses {% extends %} - if so, resolve inheritance in Rust
            if "{% extends" in template_source or "{%extends" in template_source:
                # Get template directories from Django settings in the EXACT same order Django searches
                # Django's search order:
                # 1. DIRS from each TEMPLATES config (in order)
                # 2. APP_DIRS (if enabled) - searches app templates in app order
                template_dirs = []

                # Step 1: Add DIRS from all TEMPLATES configs
                for template_config in settings.TEMPLATES:
                    if "DIRS" in template_config:
                        template_dirs.extend(template_config["DIRS"])

                # Step 2: Add app template directories (only for DjangoTemplates with APP_DIRS=True)
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

                # Get the actual path Django resolved for verification
                django_resolved_path = (
                    template.origin.name
                    if hasattr(template, "origin") and template.origin
                    else None
                )

                # Use Rust template inheritance resolution
                try:
                    from djust._rust import resolve_template_inheritance

                    resolved = resolve_template_inheritance(self.template_name, template_dirs_str)

                    # Verify Rust found the same template as Django
                    # This ensures our template search order matches Django's exactly
                    if django_resolved_path:
                        import os
                        import sys

                        # Check if any of our template dirs + template_name matches Django's path
                        rust_would_find = None
                        for template_dir in template_dirs_str:
                            candidate = os.path.join(template_dir, self.template_name)
                            if os.path.exists(candidate):
                                rust_would_find = os.path.abspath(candidate)
                                break

                        if (
                            rust_would_find
                            and os.path.abspath(django_resolved_path) != rust_would_find
                        ):
                            print(
                                f"[WARNING] Template resolution mismatch!\n"
                                f"  Django found: {django_resolved_path}\n"
                                f"  Rust found:   {rust_would_find}\n"
                                f"  Template dirs order: {template_dirs_str[:3]}...",
                                file=sys.stderr,
                            )

                    # Store full template for initial GET rendering
                    self._full_template = resolved

                    # For VDOM tracking, extract liveview-root from the RESOLVED template
                    # This ensures Rust VDOM tracks exactly what the client receives
                    # Extract liveview-root div (with wrapper) for VDOM tracking
                    vdom_template = self._extract_liveview_root_with_wrapper(resolved)

                    # CRITICAL: Strip comments and whitespace from template BEFORE Rust VDOM sees it
                    # This ensures Rust VDOM baseline matches client DOM structure
                    vdom_template = self._strip_comments_and_whitespace(vdom_template)

                    print(
                        f"[LiveView] Template inheritance resolved ({len(resolved)} chars), extracted liveview-root for VDOM ({len(vdom_template)} chars)",
                        file=sys.stderr,
                    )
                    return vdom_template

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
            raise ValueError("Either template_name or template must be set")

    # ============================================================================
    # COMPONENT MANAGEMENT
    # ============================================================================

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

    # ============================================================================
    # CONTEXT & STATE SYNCHRONIZATION
    # ============================================================================

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

                from .state_backend import get_backend

                backend = get_backend()
                self._cache_key = f"{session_key}_{view_key}"
                print(f"[LiveView] Cache lookup: cache_key={self._cache_key}", file=sys.stderr)

                # Try to get cached RustLiveView from backend
                cached = backend.get(self._cache_key)
                if cached:
                    cached_view, timestamp = cached
                    self._rust_view = cached_view
                    print("[LiveView] Cache HIT! Using cached RustLiveView", file=sys.stderr)
                    # Update timestamp on access

                    backend.set(self._cache_key, cached_view)
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
                from .state_backend import get_backend

                backend = get_backend()
                backend.set(self._cache_key, self._rust_view)

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

    # ============================================================================
    # TEMPLATE RENDERING
    # ============================================================================

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

        IMPORTANT: Preserve whitespace inside <pre> and <code> tags to maintain formatting.
        """
        import re

        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Preserve whitespace inside <pre> and <code> tags
        # Extract and temporarily replace these blocks
        preserved_blocks = []

        def preserve_block(match):
            preserved_blocks.append(match.group(0))
            return f"__PRESERVED_BLOCK_{len(preserved_blocks) - 1}__"

        # Preserve <pre> blocks (including nested content)
        html = re.sub(r"<pre[^>]*>.*?</pre>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE)
        # Preserve <code> blocks not inside <pre> (already handled above)
        html = re.sub(
            r"<code[^>]*>.*?</code>", preserve_block, html, flags=re.DOTALL | re.IGNORECASE
        )

        # Now normalize whitespace in the rest of the HTML
        # Collapse multiple whitespace to single space
        html = re.sub(r"\s+", " ", html)
        # Remove whitespace between tags
        html = re.sub(r">\s+<", "><", html)

        # Restore preserved blocks
        for i, block in enumerate(preserved_blocks):
            html = html.replace(f"__PRESERVED_BLOCK_{i}__", block)

        return html

    def _extract_liveview_content(self, html: str) -> str:
        """
        Extract the inner content of [data-liveview-root] from full HTML.

        This ensures the HTML sent over WebSocket matches what the client expects:
        just the content to insert into the existing [data-liveview-root] container.
        """
        import re

        # Find the opening tag for [data-liveview-root]
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", html, re.IGNORECASE)

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
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", template, re.IGNORECASE)

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
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", template, re.IGNORECASE)

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

    def _strip_liveview_root_in_html(self, html: str) -> str:
        """
        Strip comments and whitespace from [data-liveview-root] div in full HTML page.

        This ensures the liveview-root div structure matches the stripped template used
        by the server VDOM, while preserving the rest of the page (DOCTYPE, head, body, etc.)
        as-is.

        Args:
            html: Full HTML page including DOCTYPE, html, head, body tags

        Returns:
            HTML with liveview-root div stripped but rest of page preserved
        """
        import re

        # Find the liveview-root div (WITH wrapper)
        # Match data-liveview-root anywhere in the div tag attributes
        opening_match = re.search(r"<div\s+[^>]*data-liveview-root[^>]*>", html, re.IGNORECASE)

        if not opening_match:
            # No [data-liveview-root] found - return HTML as-is
            return html

        start_pos = opening_match.start()  # Start of <div tag
        inner_start_pos = opening_match.end()  # End of opening tag

        # Count nested divs to find the matching closing tag
        depth = 1
        pos = inner_start_pos

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
                    # This is the matching closing tag
                    end_pos = pos + close_match.end()  # Include </div>

                    # Extract the liveview-root div (WITH wrapper)
                    liveview_div = html[start_pos:end_pos]

                    # Strip comments and whitespace from this div only
                    stripped_div = self._strip_comments_and_whitespace(liveview_div)

                    # Replace the original liveview-root div with the stripped version
                    return html[:start_pos] + stripped_div + html[end_pos:]

                pos = close_pos + 6  # Skip past '</div>'

        # If we get here, couldn't find matching closing tag - return HTML as-is
        return html

    # ============================================================================
    # FULL TEMPLATE RENDERING (with inheritance)
    # ============================================================================

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

            html = self._hydrate_react_components(html)

            # Inject handler metadata for client-side decorators
            html = self._inject_handler_metadata(html)

            return html
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

        # If template is a property (dynamic), update the template
        # while preserving VDOM state for efficient patching
        if hasattr(self.__class__, "template") and isinstance(
            getattr(self.__class__, "template"), property
        ):
            print("[LiveView] template is a property - updating template", file=sys.stderr)
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

    # ============================================================================
    # COMPONENT STATE PERSISTENCE
    # ============================================================================

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
    # ============================================================================
    # HTTP REQUEST HANDLERS
    # ============================================================================

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

    # ============================================================================
    # POST-PROCESSING & CLIENT INJECTION
    # ============================================================================

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
        import json
        import os

        # Get WebSocket setting from config
        use_websocket = config.get("use_websocket", True)
        debug_vdom = config.get("debug_vdom", False)
        loading_grouping_classes = config.get(
            "loading_grouping_classes",
            ["d-flex", "btn-group", "input-group", "form-group", "btn-toolbar"],
        )

        # Convert Python list to JavaScript array
        loading_classes_js = json.dumps(loading_grouping_classes)

        config_script = f"""
        <script>
            // djust configuration
            window.DJUST_USE_WEBSOCKET = {str(use_websocket).lower()};
            window.DJUST_DEBUG_VDOM = {str(debug_vdom).lower()};
            window.DJUST_LOADING_GROUPING_CLASSES = {loading_classes_js};
        </script>
        """

        # Load client JavaScript from static file
        client_js_path = os.path.join(os.path.dirname(__file__), "static", "djust", "client.js")
        with open(client_js_path, "r", encoding="utf-8") as f:
            client_js = f.read()

        script = f"""
        <script>
            {client_js}
        </script>
        """

        # Inject both config and main script
        full_script = config_script + script

        if "</body>" in html:
            html = html.replace("</body>", f"{full_script}</body>")
        else:
            html += full_script

        return html


def live_view(template_name: Optional[str] = None, template: Optional[str] = None):
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
        template: Inline template string

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
            if template:
                DynamicLiveView.template = template

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
