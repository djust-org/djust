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

        except Exception as e:
            await self.send_json({
                'type': 'error',
                'error': str(e),
            })

    async def handle_mount(self, data: Dict[str, Any]):
        """Handle view mounting"""
        view_path = data.get('view')
        params = data.get('params', {})

        # Import and instantiate the view
        # This would need proper view resolution in production
        # For now, we'll skip the actual mounting

        await self.send_json({
            'type': 'mounted',
            'session_id': self.session_id,
        })

    async def handle_event(self, data: Dict[str, Any]):
        """Handle client events"""
        event_name = data.get('event')
        params = data.get('params', {})

        if not self.view_instance:
            await self.send_json({
                'type': 'error',
                'error': 'View not mounted',
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
                html, patches = self.view_instance.render_with_diff()

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
                        })

            except Exception as e:
                await self.send_json({
                    'type': 'error',
                    'error': str(e),
                })
        else:
            await self.send_json({
                'type': 'error',
                'error': f'Unknown event handler: {event_name}',
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
