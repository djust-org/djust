"""
WebSocket consumer for LiveView real-time updates
"""

import json
import sys
import msgpack
from typing import Callable, Dict, Any, Optional
from channels.generic.websocket import AsyncWebsocketConsumer
from .live_view import DjangoJSONEncoder
from .validation import validate_handler_params
from .profiler import profiler

try:
    from ._rust import create_session_actor, SessionActorHandle
except ImportError:
    create_session_actor = None
    SessionActorHandle = None


def get_handler_coerce_setting(handler: Callable) -> bool:
    """
    Get the coerce_types setting from a handler's @event_handler decorator.

    Args:
        handler: The event handler method

    Returns:
        True if type coercion should be enabled (default), False if disabled
    """
    if hasattr(handler, '_djust_decorators'):
        return handler._djust_decorators.get("event_handler", {}).get("coerce_types", True)
    return True


class LiveViewConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for handling LiveView connections.

    This consumer handles:
    - Initial connection and session setup
    - Event dispatching from client
    - Sending DOM patches to client
    - Session state management
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.view_instance: Optional[Any] = None
        self.actor_handle: Optional[SessionActorHandle] = None
        self.session_id: Optional[str] = None
        self.use_binary = False  # Use JSON for now (MessagePack support TODO)
        self.use_actors = False  # Will be set based on view class

    async def connect(self):
        """Handle WebSocket connection"""
        await self.accept()

        # Generate session ID
        import uuid

        self.session_id = str(uuid.uuid4())

        # Add to hot reload broadcast group
        await self.channel_layer.group_add("djust_hotreload", self.channel_name)

        # Send connection acknowledgment
        await self.send_json(
            {
                "type": "connected",
                "session_id": self.session_id,
            }
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        # Remove from hot reload broadcast group
        await self.channel_layer.group_discard("djust_hotreload", self.channel_name)

        # Clean up actor if using actors
        if self.use_actors and self.actor_handle:
            try:
                await self.actor_handle.shutdown()
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Error shutting down actor: {e}")

        # Clean up session state
        self.view_instance = None
        self.actor_handle = None

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages"""
        import logging
        import traceback

        logger = logging.getLogger(__name__)

        print(
            f"[WebSocket] receive called: text_data={text_data[:100] if text_data else None}, bytes_data={bytes_data is not None}",
            file=sys.stderr,
        )

        try:
            # Decode message
            if bytes_data:
                data = msgpack.unpackb(bytes_data, raw=False)
            else:
                data = json.loads(text_data)

            msg_type = data.get("type")
            print(f"[WebSocket] Message type: {msg_type}, data: {data}", file=sys.stderr)

            if msg_type == "event":
                await self.handle_event(data)
            elif msg_type == "mount":
                await self.handle_mount(data)
            elif msg_type == "ping":
                await self.send_json({"type": "pong"})
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                await self.send_json(
                    {
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    }
                )

        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in WebSocket message: {str(e)}"
            logger.error(error_msg)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
        except Exception as e:
            error_msg = f"Error in WebSocket receive: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            # In debug mode, include traceback
            from django.conf import settings

            if settings.DEBUG:
                await self.send_json(
                    {
                        "type": "error",
                        "error": error_msg,
                        "traceback": traceback.format_exc(),
                    }
                )
            else:
                await self.send_json(
                    {
                        "type": "error",
                        "error": "An error occurred. Please check server logs.",
                    }
                )

    async def handle_mount(self, data: Dict[str, Any]):
        """
        Handle view mounting with proper view resolution.

        Dynamically imports and instantiates a LiveView class, creates a request
        context, mounts the view, and returns the initial HTML.
        """
        import logging
        import traceback
        from django.test import RequestFactory
        from django.conf import settings

        logger = logging.getLogger(__name__)

        view_path = data.get("view")
        params = data.get("params", {})
        has_prerendered = data.get("has_prerendered", False)

        if not view_path:
            await self.send_json(
                {
                    "type": "error",
                    "error": "Missing view path in mount request",
                }
            )
            return

        # Security: Check if view is in allowed modules
        allowed_modules = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", [])
        if allowed_modules:
            # Check if view_path starts with any allowed module
            if not any(view_path.startswith(module) for module in allowed_modules):
                logger.warning(
                    f"Blocked attempt to mount view from unauthorized module: {view_path}"
                )
                await self.send_json(
                    {
                        "type": "error",
                        "error": f"View {view_path} is not in allowed modules",
                    }
                )
                return

        # Import the view class
        try:
            module_path, class_name = view_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            view_class = getattr(module, class_name)
        except ValueError:
            error_msg = (
                f"Invalid view path format: {view_path}. Expected format: module.path.ClassName"
            )
            logger.error(error_msg)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
            return
        except ImportError as e:
            error_msg = f"Failed to import module {module_path}: {str(e)}"
            logger.error(error_msg)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
            return
        except AttributeError:
            error_msg = f"Class {class_name} not found in module {module_path}"
            logger.error(error_msg)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
            return

        # Instantiate the view
        try:
            self.view_instance = view_class()

            # Store WebSocket session_id in view for consistent VDOM caching
            # This ensures mount and all subsequent events use the same VDOM instance
            self.view_instance._websocket_session_id = self.session_id

            # Check if view uses actor-based state management
            self.use_actors = getattr(view_class, "use_actors", False)

            if self.use_actors and create_session_actor:
                # Create SessionActor for this session
                logger.info(f"Creating SessionActor for {view_path}")
                self.actor_handle = await create_session_actor(self.session_id)
                logger.info(f"SessionActor created: {self.actor_handle.session_id}")

        except Exception as e:
            error_msg = f"Failed to instantiate {view_path}: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
            return

        # Create request with session
        try:
            factory = RequestFactory()
            request = factory.get("/")

            # Add session from WebSocket scope
            from django.contrib.sessions.backends.db import SessionStore

            session_key = self.scope.get("session", {}).get("session_key")
            if session_key:
                request.session = SessionStore(session_key=session_key)
            else:
                request.session = SessionStore()

            # Add user if available
            if "user" in self.scope:
                request.user = self.scope["user"]

        except Exception as e:
            error_msg = f"Failed to create request context: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            await self.send_json(
                {
                    "type": "error",
                    "error": error_msg,
                }
            )
            return

        # Mount the view (needs sync_to_async for database operations)
        from asgiref.sync import sync_to_async

        try:
            # Initialize temporary assigns before mount
            await sync_to_async(self.view_instance._initialize_temporary_assigns)()

            # Run synchronous view operations in a thread pool
            await sync_to_async(self.view_instance.mount)(request, **params)
        except Exception as e:
            error_msg = f"Error in {view_path}.mount(): {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            if settings.DEBUG:
                await self.send_json(
                    {
                        "type": "error",
                        "error": error_msg,
                        "traceback": traceback.format_exc(),
                    }
                )
            else:
                await self.send_json(
                    {
                        "type": "error",
                        "error": "Failed to mount view",
                    }
                )
            return

        # Get initial HTML (skip if client already has pre-rendered content)
        html = None
        version = 1

        try:
            if has_prerendered:
                # OPTIMIZATION: Client already has pre-rendered HTML from initial HTTP request
                # Skip rendering and only establish VDOM baseline for future patches
                logger.info("Skipping HTML generation - client has pre-rendered content")

                if self.use_actors and self.actor_handle:
                    # Initialize actor with empty render (just establish state)
                    context_data = await sync_to_async(self.view_instance.get_context_data)()
                    result = await self.actor_handle.mount(
                        view_path,
                        context_data,
                        self.view_instance,
                    )
                    version = result.get("version", 1)
                else:
                    # Initialize Rust view and sync state for future patches
                    await sync_to_async(self.view_instance._initialize_rust_view)(request)
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                    # Establish VDOM baseline without sending HTML
                    _, _, version = await sync_to_async(self.view_instance.render_with_diff)()

            elif self.use_actors and self.actor_handle:
                # Phase 5: Use actor system for rendering
                logger.info(f"Mounting {view_path} with actor system")

                # Get initial state from Python view
                context_data = await sync_to_async(self.view_instance.get_context_data)()

                # Mount with actor system (passes Python view instance)
                result = await self.actor_handle.mount(
                    view_path,
                    context_data,
                    self.view_instance,  # Pass Python view for event handlers!
                )

                html = result["html"]
                logger.info(f"Actor mount successful, HTML length: {len(html)}")

            else:
                # Non-actor mode: Use traditional flow
                # Initialize Rust view and sync state
                await sync_to_async(self.view_instance._initialize_rust_view)(request)
                await sync_to_async(self.view_instance._sync_state_to_rust)()

                # IMPORTANT: Use render_with_diff() to establish initial VDOM baseline
                # This ensures the first event will be able to generate patches instead of falling back to html_update
                html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()

                # Strip comments and normalize whitespace to match Rust VDOM parser
                html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(html)

                # Extract innerHTML of [data-liveview-root] for WebSocket client
                # Client expects just the content to insert into existing container
                html = await sync_to_async(self.view_instance._extract_liveview_content)(html)

        except Exception as e:
            error_msg = f"Error rendering {view_path}: {type(e).__name__}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            if settings.DEBUG:
                await self.send_json(
                    {
                        "type": "error",
                        "error": error_msg,
                        "traceback": traceback.format_exc(),
                    }
                )
            else:
                await self.send_json(
                    {
                        "type": "error",
                        "error": "Failed to render view",
                    }
                )
            return

        # Send success response (HTML only if generated)
        logger.info(f"Successfully mounted view: {view_path}")
        response = {
            "type": "mounted",
            "session_id": self.session_id,
            "view": view_path,
            "version": version,  # Include VDOM version for client sync
        }

        # Only include HTML if it was generated (not skipped due to pre-rendering)
        if html is not None:
            response["html"] = html

        # Include cache configuration for handlers with @cache decorator
        cache_config = self._extract_cache_config()
        if cache_config:
            response["cache_config"] = cache_config

        await self.send_json(response)

    async def handle_event(self, data: Dict[str, Any]):
        """Handle client events"""
        import logging
        import traceback
        import time
        from djust.performance import PerformanceTracker

        logger = logging.getLogger(__name__)

        # Start comprehensive performance tracking
        tracker = PerformanceTracker()
        PerformanceTracker.set_current(tracker)

        # Start timing
        start_time = time.perf_counter()
        timing = {}  # Keep for backward compatibility

        event_name = data.get("event")
        params = data.get("params", {})

        # Extract cache request ID if present (for @cache decorator)
        cache_request_id = params.get("_cacheRequestId")

        print(
            f"[WebSocket] handle_event called: {event_name} with params: {params}", file=sys.stderr
        )

        if not self.view_instance:
            await self.send_json(
                {
                    "type": "error",
                    "error": "View not mounted. Please reload the page.",
                }
            )
            return

        # Handle the event
        from asgiref.sync import sync_to_async

        if self.use_actors and self.actor_handle:
            # Phase 5: Use actor system for event handling
            try:
                logger.info(f"Handling event '{event_name}' with actor system")

                # Call actor event handler (will call Python handler internally)
                result = await self.actor_handle.event(event_name, params)

                # Send patches if available, otherwise full HTML
                patches = result.get("patches")
                html = result.get("html")
                version = result.get("version", 0)

                if patches:
                    # Parse patches JSON string to list
                    if isinstance(patches, str):
                        patches = json.loads(patches)

                    if self.use_binary:
                        # Send as MessagePack
                        patches_data = msgpack.packb(patches)
                        await self.send(bytes_data=patches_data)
                    else:
                        # Send as JSON
                        response = {
                            "type": "patch",
                            "patches": patches,
                            "version": version,
                        }
                        # Include cache request ID if present (for @cache decorator)
                        if cache_request_id:
                            response["cache_request_id"] = cache_request_id
                        await self.send_json(response)
                else:
                    # No patches - send full HTML update
                    logger.info(
                        f"No patches from actor, sending full HTML update (length: {len(html) if html else 0})"
                    )
                    response = {
                        "type": "html_update",
                        "html": html,
                        "version": version,
                    }
                    # Include cache request ID if present (for @cache decorator)
                    if cache_request_id:
                        response["cache_request_id"] = cache_request_id
                    await self.send_json(response)

            except Exception as e:
                view_class = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                error_msg = f"Error in actor event handling for {view_class}.{event_name}(): {type(e).__name__}: {str(e)}"
                logger.error(error_msg, exc_info=True)

                # In debug mode, send detailed error
                from django.conf import settings

                if settings.DEBUG:
                    await self.send_json(
                        {
                            "type": "error",
                            "error": error_msg,
                            "traceback": traceback.format_exc(),
                            "event": event_name,
                            "params": params,
                        }
                    )
                else:
                    await self.send_json(
                        {
                            "type": "error",
                            "error": "An error occurred processing your request.",
                        }
                    )

        else:
            # Non-actor mode: Use traditional flow
            # Check if this is a component event (Phase 4)
            component_id = params.get("component_id")
            html = None
            patches = None
            version = 0

            try:
                if component_id:
                    # Component event: route to component's event handler method
                    # Find the component instance
                    component = self.view_instance._components.get(component_id)
                    if not component:
                        error_msg = f"Component not found: {component_id}"
                        logger.error(error_msg)
                        await self.send_json({"type": "error", "error": error_msg})
                        return

                    # Get component's event handler
                    handler = getattr(component, event_name, None)
                    if not handler or not callable(handler):
                        error_msg = (
                            f"Component {component.__class__.__name__} has no handler: {event_name}"
                        )
                        logger.error(error_msg)
                        await self.send_json({"type": "error", "error": error_msg})
                        return

                    # Extract component_id and remove from params
                    event_data = params.copy()
                    event_data.pop("component_id", None)

                    # Validate parameters before calling handler
                    coerce = get_handler_coerce_setting(handler)
                    validation = validate_handler_params(handler, event_data, event_name, coerce=coerce)
                    if not validation["valid"]:
                        logger.error(f"Parameter validation failed: {validation['error']}")
                        await self.send_json(
                            {
                                "type": "error",
                                "error": validation["error"],
                                "validation_details": {
                                    "expected_params": validation["expected"],
                                    "provided_params": validation["provided"],
                                    "type_errors": validation["type_errors"],
                                },
                            }
                        )
                        return

                    # Call component's event handler (needs sync_to_async)
                    # This may call send_parent() which triggers handle_component_event()
                    handler_start = time.perf_counter()
                    if event_data:
                        await sync_to_async(handler)(**event_data)
                    else:
                        await sync_to_async(handler)()
                    timing['handler'] = (time.perf_counter() - handler_start) * 1000  # Convert to ms
                else:
                    # Regular event: route to handler method
                    handler = getattr(self.view_instance, event_name, None)
                    if not handler or not callable(handler):
                        error_msg = f"No handler found for event: {event_name}"
                        logger.warning(error_msg)
                        await self.send_json({"type": "error", "error": error_msg})
                        return

                    # Validate parameters before calling handler
                    coerce = get_handler_coerce_setting(handler)
                    validation = validate_handler_params(handler, params, event_name, coerce=coerce)
                    if not validation["valid"]:
                        logger.error(f"Parameter validation failed: {validation['error']}")
                        await self.send_json(
                            {
                                "type": "error",
                                "error": validation["error"],
                                "validation_details": {
                                    "expected_params": validation["expected"],
                                    "provided_params": validation["provided"],
                                    "type_errors": validation["type_errors"],
                                },
                            }
                        )
                        return

                    # Wrap everything in a root "Event Processing" tracker
                    with tracker.track("Event Processing"):
                        # Call handler with tracking
                        handler_start = time.perf_counter()
                        with tracker.track("Event Handler", event_name=event_name, params=params):
                            with profiler.profile(profiler.OP_EVENT_HANDLE):
                                if params:
                                    await sync_to_async(handler)(**params)
                                else:
                                    await sync_to_async(handler)()
                        timing['handler'] = (time.perf_counter() - handler_start) * 1000  # Convert to ms

                        # Get updated HTML and patches with tracking
                        render_start = time.perf_counter()
                        with tracker.track("Template Render"):
                            # Get context with tracking
                            with tracker.track("Context Preparation"):
                                context = await sync_to_async(self.view_instance.get_context_data)()
                                tracker.track_context_size(context)

                            # Render and generate patches with tracking
                            with tracker.track("VDOM Diff"):
                                with profiler.profile(profiler.OP_RENDER):
                                    html, patches, version = await sync_to_async(self.view_instance.render_with_diff)()
                                if patches:
                                    patch_list = json.loads(patches)
                                    tracker.track_patches(len(patch_list), patch_list)
                                    profiler.record(profiler.OP_DIFF, 0)  # Mark diff occurred
                        timing['render'] = (time.perf_counter() - render_start) * 1000  # Convert to ms

                # Check if form reset is requested (FormMixin sets this flag)
                should_reset_form = getattr(self.view_instance, "_should_reset_form", False)
                if should_reset_form:
                    # Clear the flag
                    self.view_instance._should_reset_form = False

                # For component events, send full HTML instead of patches
                # Component VDOM is separate from parent VDOM, causing path mismatches
                # TODO Phase 4.1: Implement per-component VDOM tracking
                if component_id:
                    patches = None

                # For views with dynamic templates (template as property),
                # patches may be empty because VDOM state is lost on recreation.
                # In that case, send full HTML update.

                # Patch compression: if patch count exceeds threshold and HTML is smaller,
                # send HTML instead of patches for better performance
                PATCH_COUNT_THRESHOLD = 100
                patch_list = None
                if patches:
                    patch_list = json.loads(patches)
                    patch_count = len(patch_list)
                    if patch_count > PATCH_COUNT_THRESHOLD:
                        # Compare sizes to decide whether to send patches or HTML
                        patches_size = len(patches.encode('utf-8'))
                        html_size = len(html.encode('utf-8'))
                        # If HTML is at least 30% smaller, send HTML instead
                        if html_size < patches_size * 0.7:
                            print(
                                f"[WebSocket] Patch compression: {patch_count} patches ({patches_size}B) "
                                f"-> sending HTML ({html_size}B) instead",
                                file=sys.stderr,
                            )
                            # Reset VDOM and send HTML
                            if hasattr(self.view_instance, '_rust_view') and self.view_instance._rust_view:
                                self.view_instance._rust_view.reset()
                            patches = None
                            patch_list = None

                if patches and patch_list:
                    if self.use_binary:
                        # Send as MessagePack (reuse parsed patch_list)
                        patches_data = msgpack.packb(patch_list)
                        await self.send(bytes_data=patches_data)
                    else:
                        # Send as JSON
                        timing['total'] = (time.perf_counter() - start_time) * 1000  # Total server time

                        # Get comprehensive performance summary
                        perf_summary = tracker.get_summary()

                        response = {
                            "type": "patch",
                            "patches": patch_list,  # Reuse parsed patch_list
                            "version": version,
                            "timing": timing,  # Include basic timing for backward compatibility
                            "performance": perf_summary,  # Include comprehensive performance data
                        }
                        # Include reset_form flag if set
                        if should_reset_form:
                            response["reset_form"] = True
                        # Include cache request ID if present (for @cache decorator)
                        if cache_request_id:
                            response["cache_request_id"] = cache_request_id
                        await self.send_json(response)
                else:
                    # No patches - send full HTML update for views with dynamic templates
                    # Strip comments and whitespace to match Rust VDOM parser
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )
                    # Extract innerHTML to avoid nesting <div data-liveview-root> divs
                    html_content = await sync_to_async(
                        self.view_instance._extract_liveview_content
                    )(html)

                    print(
                        "[WebSocket] No patches generated, sending full HTML update",
                        file=sys.stderr,
                    )
                    print(
                        f"[WebSocket] html_content length: {len(html_content)}, starts with: {html_content[:150]}...",
                        file=sys.stderr,
                    )
                    response = {
                        "type": "html_update",
                        "html": html_content,
                        "version": version,
                    }
                    # Include reset_form flag if set
                    if should_reset_form:
                        response["reset_form"] = True
                    # Include cache request ID if present (for @cache decorator)
                    if cache_request_id:
                        response["cache_request_id"] = cache_request_id
                    await self.send_json(response)

            except Exception as e:
                view_class = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                event_type = "component event" if component_id else "event"
                error_msg = f"Error in {view_class}.{event_name}() ({event_type}): {type(e).__name__}: {str(e)}"
                logger.error(error_msg, exc_info=True)

                # In debug mode, send detailed error
                from django.conf import settings

                if settings.DEBUG:
                    await self.send_json(
                        {
                            "type": "error",
                            "error": error_msg,
                            "traceback": traceback.format_exc(),
                            "event": event_name,
                            "params": params,
                        }
                    )
                else:
                    await self.send_json(
                        {
                            "type": "error",
                            "error": "An error occurred processing your request.",
                        }
                    )

    def _extract_cache_config(self) -> Dict[str, Any]:
        """
        Extract cache configuration from handlers with @cache decorator.

        Returns a dict mapping handler names to their cache config:
        {
            "search": {"ttl": 300, "key_params": ["query"]},
            "get_stats": {"ttl": 60, "key_params": []}
        }
        """
        import logging
        logger = logging.getLogger(__name__)

        if not self.view_instance:
            return {}

        cache_config = {}

        # Inspect all methods for @cache decorator metadata
        for name in dir(self.view_instance):
            if name.startswith('_'):
                continue

            try:
                method = getattr(self.view_instance, name)
                if callable(method) and hasattr(method, '_djust_decorators'):
                    decorators = method._djust_decorators
                    if 'cache' in decorators:
                        cache_info = decorators['cache']
                        cache_config[name] = {
                            'ttl': cache_info.get('ttl', 60),
                            'key_params': cache_info.get('key_params', [])
                        }
            except Exception as e:
                # Skip methods that can't be inspected, but log for debugging
                logger.debug(f"Could not inspect method '{name}' for cache config: {e}")

        return cache_config

    async def send_json(self, data: Dict[str, Any]):
        """Send JSON message to client with Django type support"""
        await self.send(text_data=json.dumps(data, cls=DjangoJSONEncoder))

    @staticmethod
    def _clear_template_caches():
        """
        Clear Django's template loader caches.

        This ensures hot reload picks up template changes by clearing:
        - Template loader caches (cached_property on loaders)
        - Engine-level template caches

        Supports Django's built-in template backends:
        - django.template.backends.django.DjangoTemplates
        - django.template.backends.jinja2.Jinja2 (if installed)

        Returns:
            int: Number of caches cleared successfully
        """
        import logging
        logger = logging.getLogger('djust.hotreload')

        from django.template import engines
        caches_cleared = 0

        for engine in engines.all():
            if hasattr(engine, 'engine'):
                try:
                    # Clear cached templates from loaders
                    if hasattr(engine.engine, 'template_loaders'):
                        for loader in engine.engine.template_loaders:
                            if hasattr(loader, 'reset'):
                                loader.reset()
                                caches_cleared += 1
                except Exception as e:
                    logger.warning(f"Could not clear template cache for {engine.name}: {e}")

        logger.debug(f"Cleared {caches_cleared} template caches")
        return caches_cleared

    async def hotreload_message(self, event):
        """
        Handle hot reload broadcast messages from channel layer.

        This is called when a file change is detected and a reload message
        is broadcast to the djust_hotreload group.

        Instead of full page reload, we re-render the view and send a VDOM patch.

        Args:
            event: Channel layer event containing 'file' key

        Raises:
            None - All exceptions are caught and trigger full reload fallback
        """
        import logging
        import time
        from channels.db import database_sync_to_async
        from django.template import TemplateDoesNotExist
        from json import JSONDecodeError

        logger = logging.getLogger('djust.hotreload')
        file_path = event.get("file", "unknown")

        # If we have an active view, re-render and send patch
        if self.view_instance:
            start_time = time.time()

            try:
                # Clear Django's template cache so we pick up the file changes
                self._clear_template_caches()

                # Force view to reload template by clearing cached template
                if hasattr(self.view_instance, '_template'):
                    delattr(self.view_instance, '_template')

                # Get the new template content
                try:
                    new_template = await database_sync_to_async(
                        self.view_instance.get_template
                    )()
                except TemplateDoesNotExist as e:
                    logger.error(f"Template not found for hot reload: {e}")
                    await self.send_json({
                        "type": "reload",
                        "file": file_path,
                    })
                    return

                # Update the RustLiveView with the new template (keeps old VDOM for diffing!)
                if hasattr(self.view_instance, '_rust_view') and self.view_instance._rust_view:
                    logger.debug("Updating template in existing RustLiveView")
                    await database_sync_to_async(
                        self.view_instance._rust_view.update_template
                    )(new_template)

                # Re-render the view to get patches (track time)
                render_start = time.time()
                html, patches, version = await database_sync_to_async(
                    self.view_instance.render_with_diff
                )()
                render_time = (time.time() - render_start) * 1000  # Convert to ms

                patch_count = len(patches) if patches else 0
                logger.info(f"Generated {patch_count} patches in {render_time:.2f}ms, version={version}")

                # Warn if patch generation is slow
                if render_time > 100:
                    logger.warning(f"Slow patch generation: {render_time:.2f}ms for {file_path}")

                # Handle case where no patches are generated
                if not patches:
                    logger.info("No patches generated, sending full reload")
                    await self.send_json({
                        "type": "reload",
                        "file": file_path,
                    })
                    return

                # Parse patches if they're a JSON string
                try:
                    import json as json_module
                    if isinstance(patches, str):
                        patches = json_module.loads(patches)
                except (JSONDecodeError, ValueError) as e:
                    logger.error(f"Failed to parse patches JSON: {e}")
                    await self.send_json({
                        "type": "reload",
                        "file": file_path,
                    })
                    return

                # Send the patches to the client
                await self.send_json({
                    "type": "patch",
                    "patches": patches,
                    "version": version,
                    "hotreload": True,
                    "file": file_path,
                })

                total_time = (time.time() - start_time) * 1000
                logger.info(f"✅ Sent {patch_count} patches for {file_path} (total: {total_time:.2f}ms)")

            except Exception as e:
                # Catch-all for unexpected errors
                logger.exception(f"Error generating patches for {file_path}: {e}")
                # Fallback to full reload on error
                await self.send_json({
                    "type": "reload",
                    "file": file_path,
                })
        else:
            # No active view, just reload the page
            logger.debug(f"No active view, sending full reload for {file_path}")
            await self.send_json({
                "type": "reload",
                "file": file_path,
            })

    @classmethod
    async def broadcast_reload(cls, file_path: str):
        """
        Broadcast a reload message to all connected clients.

        This is called by the hot reload file watcher when files change.

        Args:
            file_path: Path of the file that changed
        """
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            await channel_layer.group_send(
                "djust_hotreload",
                {
                    "type": "hotreload.message",
                    "file": file_path,
                }
            )


class LiveViewRouter:
    """
    Router for LiveView WebSocket connections.

    Maps URL patterns to LiveView classes.
    """

    _routes: Dict[str, type] = {}

    @classmethod
    def register(cls, path: str, view_class: type):
        """Register a LiveView route"""
        cls._routes[path] = view_class

    @classmethod
    def get_view(cls, path: str) -> Optional[type]:
        """Get the view class for a path"""
        return cls._routes.get(path)
