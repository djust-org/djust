"""
WebSocket consumer for LiveView real-time updates
"""

import json
import logging
import sys
import msgpack
from typing import Any, Dict, Optional
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from .serialization import DjangoJSONEncoder
from .validation import validate_handler_params
from .profiler import profiler
from .security import handle_exception, sanitize_for_log
from .config import config as djust_config
from .rate_limit import ConnectionRateLimiter, ip_tracker
from .websocket_utils import (
    _call_handler,
    _check_event_security,  # noqa: F401 - re-exported for tests
    _ensure_handler_rate_limit,  # noqa: F401 - re-exported for tests
    _safe_error,
    _validate_event_security,
    get_handler_coerce_setting,
)

logger = logging.getLogger(__name__)
hotreload_logger = logging.getLogger("djust.hotreload")

try:
    from ._rust import create_session_actor, SessionActorHandle
except ImportError:
    create_session_actor = None
    SessionActorHandle = None


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

    async def send_error(self, error: str, **context) -> None:
        """
        Send an error response to the client with consistent formatting.

        Args:
            error: Human-readable error message
            **context: Additional context to include in the response
                (e.g., validation_details, expected_params)
        """
        response: Dict[str, Any] = {"type": "error", "error": error}
        response.update(context)
        await self.send_json(response)

    async def _send_update(
        self,
        patches: Optional[list] = None,
        html: Optional[str] = None,
        version: int = 0,
        cache_request_id: Optional[str] = None,
        reset_form: bool = False,
        timing: Optional[Dict[str, Any]] = None,
        performance: Optional[Dict[str, Any]] = None,
        hotreload: bool = False,
        file_path: Optional[str] = None,
    ) -> None:
        """
        Send a patch or full HTML update to the client.

        Handles both JSON and binary (MessagePack) modes, building the response
        with all optional fields.

        Args:
            patches: VDOM patches to apply (if available, can be empty list)
            html: Full HTML content (fallback when patches is None)
            version: VDOM version for client sync
            cache_request_id: Optional ID for client-side caching (@cache decorator)
            reset_form: Whether to reset form state after update
            timing: Basic timing data for backward compatibility
            performance: Comprehensive performance data
            hotreload: Whether this is a hot reload update
            file_path: File path that triggered hot reload (if hotreload=True)
        """
        # Note: patches=[] (empty list) is valid and should be sent as "patch" type
        # Only patches=None indicates we should send html_update
        if patches is not None:
            if self.use_binary:
                patches_data = msgpack.packb(patches)
                await self.send(bytes_data=patches_data)
            else:
                response: Dict[str, Any] = {
                    "type": "patch",
                    "patches": patches,
                    "version": version,
                }
                if timing:
                    response["timing"] = timing
                if performance:
                    response["performance"] = performance
                if reset_form:
                    response["reset_form"] = True
                if cache_request_id:
                    response["cache_request_id"] = cache_request_id
                if hotreload:
                    response["hotreload"] = True
                    if file_path:
                        response["file"] = file_path
                await self.send_json(response)
        else:
            response = {
                "type": "html_update",
                "html": html,
                "version": version,
            }
            if reset_form:
                response["reset_form"] = True
            if cache_request_id:
                response["cache_request_id"] = cache_request_id
            await self.send_json(response)

    def _get_client_ip(self) -> Optional[str]:
        """Extract client IP from scope, with X-Forwarded-For support."""
        headers = dict(self.scope.get("headers", []))
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            return forwarded.decode("utf-8").split(",")[0].strip()
        client = self.scope.get("client")
        if client:
            return client[0]
        return None

    async def connect(self):
        """Handle WebSocket connection"""
        await self.accept()

        # Generate session ID
        import uuid

        self.session_id = str(uuid.uuid4())

        # Per-IP connection limit and cooldown check
        self._client_ip = self._get_client_ip()
        rl_cfg = djust_config.get("rate_limit", {})
        if not isinstance(rl_cfg, dict):
            rl_cfg = {}
        if self._client_ip:
            max_per_ip = rl_cfg.get("max_connections_per_ip", 10)
            if not ip_tracker.connect(self._client_ip, max_per_ip):
                logger.warning("Connection rejected for IP %s (limit or cooldown)", self._client_ip)
                await self.close(code=4429)
                return

        # Add to hot reload broadcast group
        await self.channel_layer.group_add("djust_hotreload", self.channel_name)

        # Initialize per-connection rate limiter
        self._rate_limiter = ConnectionRateLimiter(
            rate=rl_cfg.get("rate", 100),
            burst=rl_cfg.get("burst", 20),
            max_warnings=rl_cfg.get("max_warnings", 3),
        )

        # Send connection acknowledgment
        await self.send_json(
            {
                "type": "connect",
                "session_id": self.session_id,
            }
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        # Release IP connection slot
        client_ip = getattr(self, "_client_ip", None)
        if client_ip:
            ip_tracker.disconnect(client_ip)

        # Remove from hot reload broadcast group
        await self.channel_layer.group_discard("djust_hotreload", self.channel_name)

        # Clean up actor if using actors
        if self.use_actors and self.actor_handle:
            try:
                await self.actor_handle.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down actor: {e}")

        # Clean up session state
        self.view_instance = None
        self.actor_handle = None

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages"""
        print(
            f"[WebSocket] receive called: text_data={text_data[:100] if text_data else None}, bytes_data={bytes_data is not None}",
            file=sys.stderr,
        )

        try:
            # Check message size
            max_msg_size = djust_config.get("max_message_size", 65536)
            if bytes_data:
                raw_size = len(bytes_data)
            elif text_data:
                char_len = len(text_data)
                # Only skip encode when even worst-case (4 bytes/char) is under limit
                raw_size = (
                    char_len if char_len * 4 <= max_msg_size else len(text_data.encode("utf-8"))
                )
            else:
                raw_size = 0
            if max_msg_size and raw_size > max_msg_size:
                logger.warning("Message too large (%d bytes, max %d)", raw_size, max_msg_size)
                await self.send_error(f"Message too large ({raw_size} bytes)")
                return

            # Decode message
            if bytes_data:
                data = msgpack.unpackb(bytes_data, raw=False)
            else:
                data = json.loads(text_data)

            msg_type = data.get("type")

            # Global rate limit check — applies to ALL message types (#107)
            if not self._rate_limiter.check(msg_type or "unknown"):
                if self._rate_limiter.should_disconnect():
                    logger.warning("Rate limit exceeded, disconnecting client")
                    if getattr(self, "_client_ip", None):
                        _rl = djust_config.get("rate_limit", {})
                        cooldown = _rl.get("reconnect_cooldown", 5) if isinstance(_rl, dict) else 5
                        ip_tracker.add_cooldown(self._client_ip, cooldown)
                    await self.close(code=4429)
                    return
                await self.send_error("Rate limit exceeded")
                return

            if msg_type == "event":
                await self.handle_event(data)
            elif msg_type == "mount":
                await self.handle_mount(data)
            elif msg_type == "ping":
                await self.send_json({"type": "pong"})
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                await self.send_error(f"Unknown message type: {msg_type}")

        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in WebSocket message: {str(e)}"
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "Invalid message format"))
        except Exception as e:
            # Handle exception: logs (with stack trace only in DEBUG) and returns safe response
            response = handle_exception(
                e,
                error_type="default",
                logger=logger,
                log_message="Error in WebSocket receive",
            )
            await self.send_json(response)

    async def handle_mount(self, data: Dict[str, Any]):
        """
        Handle view mounting with proper view resolution.

        Dynamically imports and instantiates a LiveView class, creates a request
        context, mounts the view, and returns the initial HTML.
        """
        from django.test import RequestFactory
        from django.conf import settings

        logger = logging.getLogger(__name__)

        view_path = data.get("view")
        params = data.get("params", {})
        has_prerendered = data.get("has_prerendered", False)

        if not view_path:
            await self.send_error("Missing view path in mount request")
            return

        # Security: Check if view is in allowed modules
        allowed_modules = getattr(settings, "LIVEVIEW_ALLOWED_MODULES", [])
        if allowed_modules:
            # Check if view_path starts with any allowed module
            if not any(view_path.startswith(module) for module in allowed_modules):
                logger.warning(
                    f"Blocked attempt to mount view from unauthorized module: {view_path}"
                )
                await self.send_error(
                    _safe_error(f"View {view_path} is not in allowed modules", "View not found")
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
            await self.send_error(_safe_error(error_msg, "View not found"))
            return
        except ImportError as e:
            error_msg = f"Failed to import module {module_path}: {str(e)}"
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "View not found"))
            return
        except AttributeError:
            error_msg = f"Class {class_name} not found in module {module_path}"
            logger.error(error_msg)
            await self.send_error(_safe_error(error_msg, "View not found"))
            return

        # Instantiate the view
        try:
            self.view_instance = view_class()

            # Store WebSocket session_id in view for consistent VDOM caching
            # This ensures mount and all subsequent events use the same VDOM instance
            self.view_instance._websocket_session_id = self.session_id
            # Store path and query string for path-aware cache keys
            # This ensures /emails/ and /emails/?sender=1 get separate VDOM caches
            self.view_instance._websocket_path = self.scope.get("path", "/")
            self.view_instance._websocket_query_string = self.scope.get("query_string", b"").decode(
                "utf-8"
            )

            # Check if view uses actor-based state management
            self.use_actors = getattr(view_class, "use_actors", False)

            if self.use_actors and create_session_actor:
                # Create SessionActor for this session
                logger.info(f"Creating SessionActor for {view_path}")
                self.actor_handle = await create_session_actor(self.session_id)
                logger.info(f"SessionActor created: {self.actor_handle.session_id}")

        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Failed to instantiate {view_path}",
            )
            await self.send_json(response)
            return

        # Create request with session
        try:
            from urllib.parse import urlencode

            factory = RequestFactory()
            # Include URL query params (e.g., ?sender=80) in the request
            query_string = urlencode(params) if params else ""
            # Use the actual page URL from the client, not hardcoded "/"
            page_url = data.get("url", "/")
            path_with_query = f"{page_url}?{query_string}" if query_string else page_url
            request = factory.get(path_with_query)

            # Add session from WebSocket scope
            # NOTE: session_key is an ATTRIBUTE of the session object, not a dict key
            from django.contrib.sessions.backends.db import SessionStore

            scope_session = self.scope.get("session")
            if scope_session and hasattr(scope_session, "session_key"):
                session_key = scope_session.session_key
                request.session = SessionStore(session_key=session_key)
            else:
                request.session = SessionStore()

            # Add user if available
            if "user" in self.scope:
                request.user = self.scope["user"]

        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                logger=logger,
                log_message="Failed to create request context",
            )
            await self.send_json(response)
            return

        # Mount the view (needs sync_to_async for database operations)

        try:
            # Initialize temporary assigns before mount
            await sync_to_async(self.view_instance._initialize_temporary_assigns)()

            # Run synchronous view operations in a thread pool
            await sync_to_async(self.view_instance.mount)(request, **params)
        except Exception as e:
            response = handle_exception(
                e,
                error_type="mount",
                view_class=view_path,
                logger=logger,
                log_message=f"Error in {sanitize_for_log(view_path)}.mount()",
            )
            await self.send_json(response)
            return

        # Get initial HTML (skip if client already has pre-rendered content)
        html = None
        version = 1

        try:
            if has_prerendered:
                # Client has pre-rendered HTML but we still need to send hydrated HTML
                # when using ID-based patching (data-dj attributes) for reliable VDOM sync
                logger.info(
                    "Client has pre-rendered content - sending hydrated HTML for ID-based patching"
                )

                if self.use_actors and self.actor_handle:
                    # Initialize actor with empty render (just establish state)
                    context_data = await sync_to_async(self.view_instance.get_context_data)()
                    result = await self.actor_handle.mount(
                        view_path,
                        context_data,
                        self.view_instance,
                    )
                    html = result.get("html")
                    version = result.get("version", 1)
                else:
                    # Initialize Rust view and sync state for future patches
                    await sync_to_async(self.view_instance._initialize_rust_view)(request)
                    await sync_to_async(self.view_instance._sync_state_to_rust)()

                    # Generate hydrated HTML with data-dj attributes for reliable patch targeting
                    html, _, version = await sync_to_async(self.view_instance.render_with_diff)()

                    # Strip comments and normalize whitespace
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )

                    # Extract innerHTML of [data-djust-root]
                    html = await sync_to_async(self.view_instance._extract_liveview_content)(html)

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

                # Extract innerHTML of [data-djust-root] for WebSocket client
                # Client expects just the content to insert into existing container
                html = await sync_to_async(self.view_instance._extract_liveview_content)(html)

        except Exception as e:
            response = handle_exception(
                e,
                error_type="render",
                view_class=view_path,
                logger=logger,
                log_message=f"Error rendering {sanitize_for_log(view_path)}",
            )
            await self.send_json(response)
            return

        # Send success response (HTML only if generated)
        logger.info(f"Successfully mounted view: {view_path}")
        response = {
            "type": "mount",
            "session_id": self.session_id,
            "view": view_path,
            "version": version,  # Include VDOM version for client sync
        }

        # Only include HTML if it was generated (not skipped due to pre-rendering)
        if html is not None:
            response["html"] = html
            # Flag indicating HTML has data-dj-id attributes for ID-based patching
            has_ids = "data-dj-id=" in html
            response["has_ids"] = has_ids

        # Include cache configuration for handlers with @cache decorator
        cache_config = self._extract_cache_config()
        if cache_config:
            response["cache_config"] = cache_config

        await self.send_json(response)

    async def handle_event(self, data: Dict[str, Any]):
        """Handle client events"""
        import time
        from djust.performance import PerformanceTracker

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

        # Extract positional arguments from inline handler syntax
        # e.g., @click="set_period('month')" sends params._args = ['month']
        positional_args = params.pop("_args", [])

        print(
            f"[WebSocket] handle_event called: {event_name} with params: {params}", file=sys.stderr
        )

        if not self.view_instance:
            await self.send_error("View not mounted. Please reload the page.")
            return

        # Handle the event

        if self.use_actors and self.actor_handle:
            # Phase 5: Use actor system for event handling
            try:
                logger.info(f"Handling event '{event_name}' with actor system")

                # Security checks (shared with non-actor paths)
                handler = await _validate_event_security(
                    self, event_name, self.view_instance, self._rate_limiter
                )
                if handler is None:
                    return

                # Validate parameters before sending to actor
                coerce = get_handler_coerce_setting(handler)
                validation = validate_handler_params(
                    handler, params, event_name, coerce=coerce, positional_args=positional_args
                )
                if not validation["valid"]:
                    logger.error(f"Parameter validation failed: {validation['error']}")
                    await self.send_error(
                        validation["error"],
                        validation_details={
                            "expected_params": validation["expected"],
                            "provided_params": validation["provided"],
                            "type_errors": validation["type_errors"],
                        },
                    )
                    return

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
                else:
                    # No patches - send full HTML update
                    logger.info(
                        f"No patches from actor, sending full HTML update (length: {len(html) if html else 0})"
                    )

                await self._send_update(
                    patches=patches,
                    html=html,
                    version=version,
                    cache_request_id=cache_request_id,
                )

            except Exception as e:
                view_class_name = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                response = handle_exception(
                    e,
                    error_type="event",
                    event_name=event_name,
                    view_class=view_class_name,
                    logger=logger,
                    log_message=f"Error in actor event handling for {view_class_name}.{sanitize_for_log(event_name)}()",
                )
                await self.send_json(response)

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
                        await self.send_error(error_msg)
                        return

                    # Security checks (shared with actor and view paths)
                    handler = await _validate_event_security(
                        self, event_name, component, self._rate_limiter
                    )
                    if handler is None:
                        return

                    # Extract component_id and remove from params
                    event_data = params.copy()
                    event_data.pop("component_id", None)

                    # Validate parameters before calling handler
                    # Pass positional_args so they can be mapped to named parameters
                    coerce = get_handler_coerce_setting(handler)
                    validation = validate_handler_params(
                        handler,
                        event_data,
                        event_name,
                        coerce=coerce,
                        positional_args=positional_args,
                    )
                    if not validation["valid"]:
                        logger.error(f"Parameter validation failed: {validation['error']}")
                        await self.send_error(
                            validation["error"],
                            validation_details={
                                "expected_params": validation["expected"],
                                "provided_params": validation["provided"],
                                "type_errors": validation["type_errors"],
                            },
                        )
                        return

                    # Use coerced params (with positional args merged in)
                    coerced_event_data = validation.get("coerced_params", event_data)

                    # Call component's event handler (supports both sync and async)
                    # This may call send_parent() which triggers handle_component_event()
                    handler_start = time.perf_counter()
                    await _call_handler(handler, coerced_event_data if coerced_event_data else None)
                    timing["handler"] = (
                        time.perf_counter() - handler_start
                    ) * 1000  # Convert to ms
                else:
                    # Security checks (shared with actor and component paths)
                    handler = await _validate_event_security(
                        self, event_name, self.view_instance, self._rate_limiter
                    )
                    if handler is None:
                        return

                    # Validate parameters before calling handler
                    # Pass positional_args so they can be mapped to named parameters
                    coerce = get_handler_coerce_setting(handler)
                    validation = validate_handler_params(
                        handler, params, event_name, coerce=coerce, positional_args=positional_args
                    )
                    if not validation["valid"]:
                        logger.error(f"Parameter validation failed: {validation['error']}")
                        await self.send_error(
                            validation["error"],
                            validation_details={
                                "expected_params": validation["expected"],
                                "provided_params": validation["provided"],
                                "type_errors": validation["type_errors"],
                            },
                        )
                        return

                    # Use coerced params (with positional args merged in)
                    coerced_params = validation.get("coerced_params", params)

                    # Wrap everything in a root "Event Processing" tracker
                    with tracker.track("Event Processing"):
                        # Call handler with tracking (supports both sync and async handlers)
                        handler_start = time.perf_counter()
                        with tracker.track(
                            "Event Handler", event_name=event_name, params=coerced_params
                        ):
                            with profiler.profile(profiler.OP_EVENT_HANDLE):
                                await _call_handler(
                                    handler, coerced_params if coerced_params else None
                                )
                        timing["handler"] = (
                            time.perf_counter() - handler_start
                        ) * 1000  # Convert to ms

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
                                    html, patches, version = await sync_to_async(
                                        self.view_instance.render_with_diff
                                    )()
                                patch_list = None  # Initialize for later use
                                # patches can be: JSON string with patches, "[]" for empty, or None
                                if patches is not None:
                                    patch_list = json.loads(patches) if patches else []
                                    tracker.track_patches(len(patch_list), patch_list)
                                    profiler.record(profiler.OP_DIFF, 0)  # Mark diff occurred
                        timing["render"] = (
                            time.perf_counter() - render_start
                        ) * 1000  # Convert to ms

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
                # Note: patch_list was already parsed earlier for performance tracking
                if patches and patch_list:
                    patch_count = len(patch_list)
                    if patch_count > PATCH_COUNT_THRESHOLD:
                        # Compare sizes to decide whether to send patches or HTML
                        patches_size = len(patches.encode("utf-8"))
                        html_size = len(html.encode("utf-8"))
                        # If HTML is at least 30% smaller, send HTML instead
                        if html_size < patches_size * 0.7:
                            logger.debug(
                                "Patch compression: %d patches (%dB) -> sending HTML (%dB) instead",
                                patch_count,
                                patches_size,
                                html_size,
                            )
                            # Reset VDOM and send HTML
                            if (
                                hasattr(self.view_instance, "_rust_view")
                                and self.view_instance._rust_view
                            ):
                                self.view_instance._rust_view.reset()
                            patches = None
                            patch_list = None

                # Note: patch_list can be [] (empty list) which is valid - means no changes needed
                # Only send full HTML if patches is None (not just falsy)
                if patches is not None and patch_list is not None:
                    # Calculate timing for JSON mode
                    timing["total"] = (time.perf_counter() - start_time) * 1000  # Total server time
                    perf_summary = tracker.get_summary()

                    await self._send_update(
                        patches=patch_list,
                        version=version,
                        cache_request_id=cache_request_id,
                        reset_form=should_reset_form,
                        timing=timing,
                        performance=perf_summary,
                    )
                else:
                    # patches=None means VDOM diff failed or was skipped - send full HTML
                    # Strip comments and whitespace to match Rust VDOM parser
                    html = await sync_to_async(self.view_instance._strip_comments_and_whitespace)(
                        html
                    )
                    # Extract innerHTML to avoid nesting <div data-djust-root> divs
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

                    await self._send_update(
                        html=html_content,
                        version=version,
                        cache_request_id=cache_request_id,
                        reset_form=should_reset_form,
                    )

            except Exception as e:
                view_class_name = (
                    self.view_instance.__class__.__name__ if self.view_instance else "Unknown"
                )
                event_type = "component event" if component_id else "event"
                response = handle_exception(
                    e,
                    error_type="event",
                    event_name=event_name,
                    view_class=view_class_name,
                    logger=logger,
                    log_message=f"Error in {view_class_name}.{sanitize_for_log(event_name)}() ({event_type})",
                )
                await self.send_json(response)

    def _extract_cache_config(self) -> Dict[str, Any]:
        """
        Extract cache configuration from handlers with @cache decorator.

        Returns a dict mapping handler names to their cache config:
        {
            "search": {"ttl": 300, "key_params": ["query"]},
            "get_stats": {"ttl": 60, "key_params": []}
        }
        """
        if not self.view_instance:
            return {}

        cache_config = {}

        # Inspect all methods for @cache decorator metadata
        for name in dir(self.view_instance):
            if name.startswith("_"):
                continue

            try:
                method = getattr(self.view_instance, name)
                if callable(method) and hasattr(method, "_djust_decorators"):
                    decorators = method._djust_decorators
                    if "cache" in decorators:
                        cache_info = decorators["cache"]
                        cache_config[name] = {
                            "ttl": cache_info.get("ttl", 60),
                            "key_params": cache_info.get("key_params", []),
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
        from django.template import engines

        caches_cleared = 0

        for engine in engines.all():
            if hasattr(engine, "engine"):
                try:
                    # Clear cached templates from loaders
                    if hasattr(engine.engine, "template_loaders"):
                        for loader in engine.engine.template_loaders:
                            if hasattr(loader, "reset"):
                                loader.reset()
                                caches_cleared += 1
                except Exception as e:
                    hotreload_logger.warning(
                        f"Could not clear template cache for {engine.name}: {e}"
                    )

        hotreload_logger.debug(f"Cleared {caches_cleared} template caches")
        return caches_cleared

    async def hotreload(self, event):
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
        import time
        from channels.db import database_sync_to_async
        from django.template import TemplateDoesNotExist

        file_path = event.get("file", "unknown")

        # If we have an active view, re-render and send patch
        if self.view_instance:
            start_time = time.time()

            try:
                # Clear Django's template cache so we pick up the file changes
                self._clear_template_caches()

                # Force view to reload template by clearing cached template
                if hasattr(self.view_instance, "_template"):
                    delattr(self.view_instance, "_template")

                # Get the new template content
                try:
                    new_template = await database_sync_to_async(self.view_instance.get_template)()
                except TemplateDoesNotExist as e:
                    hotreload_logger.error(f"Template not found for hot reload: {e}")
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Update the RustLiveView with the new template (keeps old VDOM for diffing!)
                if hasattr(self.view_instance, "_rust_view") and self.view_instance._rust_view:
                    hotreload_logger.debug("Updating template in existing RustLiveView")
                    await database_sync_to_async(self.view_instance._rust_view.update_template)(
                        new_template
                    )

                # Re-render the view to get patches (track time)
                render_start = time.time()
                html, patches, version = await database_sync_to_async(
                    self.view_instance.render_with_diff
                )()
                render_time = (time.time() - render_start) * 1000  # Convert to ms

                patch_count = len(patches) if patches else 0
                hotreload_logger.info(
                    f"Generated {patch_count} patches in {render_time:.2f}ms, version={version}"
                )

                # Warn if patch generation is slow
                if render_time > 100:
                    hotreload_logger.warning(
                        f"Slow patch generation: {render_time:.2f}ms for {file_path}"
                    )

                # Handle case where no patches are generated
                if not patches:
                    hotreload_logger.info("No patches generated, sending full reload")
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Parse patches if they're a JSON string
                try:
                    if isinstance(patches, str):
                        patches = json.loads(patches)
                except (json.JSONDecodeError, ValueError) as e:
                    hotreload_logger.error(f"Failed to parse patches JSON: {e}")
                    await self.send_json(
                        {
                            "type": "reload",
                            "file": file_path,
                        }
                    )
                    return

                # Send the patches to the client
                await self._send_update(
                    patches=patches,
                    version=version,
                    hotreload=True,
                    file_path=file_path,
                )

                total_time = (time.time() - start_time) * 1000
                hotreload_logger.info(
                    f"Sent {patch_count} patches for {file_path} (total: {total_time:.2f}ms)"
                )

            except Exception as e:
                # Catch-all for unexpected errors
                hotreload_logger.exception(f"Error generating patches for {file_path}: {e}")
                # Fallback to full reload on error
                await self.send_json(
                    {
                        "type": "reload",
                        "file": file_path,
                    }
                )
        else:
            # No active view, just reload the page
            hotreload_logger.debug(f"No active view, sending full reload for {file_path}")
            await self.send_json(
                {
                    "type": "reload",
                    "file": file_path,
                }
            )

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
                    "type": "hotreload",
                    "file": file_path,
                },
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
