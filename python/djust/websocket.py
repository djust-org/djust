"""
WebSocket consumer for LiveView real-time updates
"""

import json
import msgpack
from typing import Dict, Any, Optional
from channels.generic.websocket import AsyncWebsocketConsumer


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
        self.session_id: Optional[str] = None
        self.use_binary = True  # Use MessagePack by default

    async def connect(self):
        """Handle WebSocket connection"""
        await self.accept()

        # Generate session ID
        import uuid
        self.session_id = str(uuid.uuid4())

        # Send connection acknowledgment
        await self.send_json({
            'type': 'connected',
            'session_id': self.session_id,
        })

    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        # Clean up session state
        self.view_instance = None

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming WebSocket messages"""
        import logging
        import traceback

        logger = logging.getLogger(__name__)

        try:
            # Decode message
            if bytes_data:
                data = msgpack.unpackb(bytes_data, raw=False)
            else:
                data = json.loads(text_data)

            msg_type = data.get('type')

            if msg_type == 'event':
                await self.handle_event(data)
            elif msg_type == 'mount':
                await self.handle_mount(data)
            elif msg_type == 'ping':
                await self.send_json({'type': 'pong'})
            else:
                logger.warning(f'Unknown message type: {msg_type}')
                await self.send_json({
                    'type': 'error',
                    'error': f'Unknown message type: {msg_type}',
                })

        except json.JSONDecodeError as e:
            error_msg = f'Invalid JSON in WebSocket message: {str(e)}'
            logger.error(error_msg)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
        except Exception as e:
            error_msg = f'Error in WebSocket receive: {type(e).__name__}: {str(e)}'
            logger.error(error_msg, exc_info=True)

            # In debug mode, include traceback
            from django.conf import settings
            if settings.DEBUG:
                await self.send_json({
                    'type': 'error',
                    'error': error_msg,
                    'traceback': traceback.format_exc(),
                })
            else:
                await self.send_json({
                    'type': 'error',
                    'error': 'An error occurred. Please check server logs.',
                })

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

        view_path = data.get('view')
        params = data.get('params', {})

        if not view_path:
            await self.send_json({
                'type': 'error',
                'error': 'Missing view path in mount request',
            })
            return

        # Security: Check if view is in allowed modules
        allowed_modules = getattr(settings, 'LIVEVIEW_ALLOWED_MODULES', [])
        if allowed_modules:
            # Check if view_path starts with any allowed module
            if not any(view_path.startswith(module) for module in allowed_modules):
                logger.warning(f'Blocked attempt to mount view from unauthorized module: {view_path}')
                await self.send_json({
                    'type': 'error',
                    'error': f'View {view_path} is not in allowed modules',
                })
                return

        # Import the view class
        try:
            module_path, class_name = view_path.rsplit('.', 1)
            module = __import__(module_path, fromlist=[class_name])
            view_class = getattr(module, class_name)
        except ValueError:
            error_msg = f'Invalid view path format: {view_path}. Expected format: module.path.ClassName'
            logger.error(error_msg)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
            return
        except ImportError as e:
            error_msg = f'Failed to import module {module_path}: {str(e)}'
            logger.error(error_msg)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
            return
        except AttributeError as e:
            error_msg = f'Class {class_name} not found in module {module_path}'
            logger.error(error_msg)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
            return

        # Instantiate the view
        try:
            self.view_instance = view_class()
        except Exception as e:
            error_msg = f'Failed to instantiate {view_path}: {type(e).__name__}: {str(e)}'
            logger.error(error_msg, exc_info=True)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
            return

        # Create request with session
        try:
            factory = RequestFactory()
            request = factory.get('/')

            # Add session from WebSocket scope
            from django.contrib.sessions.backends.db import SessionStore
            session_key = self.scope.get('session', {}).get('session_key')
            if session_key:
                request.session = SessionStore(session_key=session_key)
            else:
                request.session = SessionStore()

            # Add user if available
            if 'user' in self.scope:
                request.user = self.scope['user']

        except Exception as e:
            error_msg = f'Failed to create request context: {type(e).__name__}: {str(e)}'
            logger.error(error_msg, exc_info=True)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })
            return

        # Mount the view
        try:
            self.view_instance.mount(request, **params)
        except Exception as e:
            error_msg = f'Error in {view_path}.mount(): {type(e).__name__}: {str(e)}'
            logger.error(error_msg, exc_info=True)

            if settings.DEBUG:
                await self.send_json({
                    'type': 'error',
                    'error': error_msg,
                    'traceback': traceback.format_exc(),
                })
            else:
                await self.send_json({
                    'type': 'error',
                    'error': 'Failed to mount view',
                })
            return

        # Get initial HTML
        try:
            html = self.view_instance.render_to_string()
        except Exception as e:
            error_msg = f'Error rendering {view_path}: {type(e).__name__}: {str(e)}'
            logger.error(error_msg, exc_info=True)

            if settings.DEBUG:
                await self.send_json({
                    'type': 'error',
                    'error': error_msg,
                    'traceback': traceback.format_exc(),
                })
            else:
                await self.send_json({
                    'type': 'error',
                    'error': 'Failed to render view',
                })
            return

        # Send success response with initial HTML
        logger.info(f'Successfully mounted view: {view_path}')
        await self.send_json({
            'type': 'mounted',
            'session_id': self.session_id,
            'html': html,
            'view': view_path,
        })

    async def handle_event(self, data: Dict[str, Any]):
        """Handle client events"""
        import logging
        import traceback

        logger = logging.getLogger(__name__)

        event_name = data.get('event')
        params = data.get('params', {})

        if not self.view_instance:
            await self.send_json({
                'type': 'error',
                'error': 'View not mounted. Please reload the page.',
            })
            return

        # Call the event handler
        handler = getattr(self.view_instance, event_name, None)
        if handler and callable(handler):
            try:
                # Call handler
                if params:
                    handler(**params)
                else:
                    handler()

                # Get updated HTML and patches
                html, patches, version = self.view_instance.render_with_diff()

                # Send patches to client
                if patches:
                    if self.use_binary:
                        # Send as MessagePack
                        patches_data = msgpack.packb(json.loads(patches))
                        await self.send(bytes_data=patches_data)
                    else:
                        # Send as JSON
                        await self.send_json({
                            'type': 'patch',
                            'patches': json.loads(patches),
                            'version': version,
                        })

            except Exception as e:
                view_class = self.view_instance.__class__.__name__ if self.view_instance else 'Unknown'
                error_msg = f'Error in {view_class}.{event_name}(): {type(e).__name__}: {str(e)}'
                logger.error(error_msg, exc_info=True)

                # In debug mode, send detailed error
                from django.conf import settings
                if settings.DEBUG:
                    await self.send_json({
                        'type': 'error',
                        'error': error_msg,
                        'traceback': traceback.format_exc(),
                        'event': event_name,
                        'params': params,
                    })
                else:
                    await self.send_json({
                        'type': 'error',
                        'error': 'An error occurred processing your request.',
                    })
        else:
            error_msg = f'Unknown event handler: {event_name}'
            if self.view_instance:
                view_class = self.view_instance.__class__.__name__
                available_handlers = [m for m in dir(self.view_instance)
                                    if not m.startswith('_') and callable(getattr(self.view_instance, m))]
                error_msg += f' in {view_class}. Available handlers: {", ".join(available_handlers[:5])}'

            logger.warning(error_msg)
            await self.send_json({
                'type': 'error',
                'error': error_msg,
            })

    async def send_json(self, data: Dict[str, Any]):
        """Send JSON message to client"""
        await self.send(text_data=json.dumps(data))


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
