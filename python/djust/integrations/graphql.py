"""
GraphQL integration for djust LiveView.

Provides GraphQLMixin to connect GraphQL subscriptions to LiveView updates,
enabling real-time data synchronization with Strawberry, Graphene, or any
GraphQL server supporting websocket subscriptions.

Example usage:

    from djust import LiveView
    from djust.integrations import GraphQLMixin

    class DashboardView(GraphQLMixin, LiveView):
        template_name = "dashboard.html"
        
        graphql_endpoint = "ws://localhost:8000/graphql/"
        subscriptions = ['orderUpdated', 'inventoryChanged']
        
        async def mount(self, request, **kwargs):
            self.orders = []
            self.inventory = []
            # Subscriptions auto-start on mount
        
        async def on_subscription(self, name: str, data: dict):
            '''Called when subscription data is received.'''
            if name == 'orderUpdated':
                self.orders = await self.fetch_orders()
            elif name == 'inventoryChanged':
                self.inventory = await self.fetch_inventory()
        
        async def on_subscription_error(self, name: str, error: Exception):
            '''Called when a subscription error occurs.'''
            self.push_event("error", {"message": f"Subscription {name} failed"})

GraphQL Query Example:

    class ProductView(GraphQLMixin, LiveView):
        graphql_endpoint = "http://localhost:8000/graphql/"
        
        async def mount(self, request, **kwargs):
            result = await self.graphql_query('''
                query {
                    products {
                        id
                        name
                        price
                    }
                }
            ''')
            self.products = result.get('products', [])
        
        async def refresh_product(self, product_id: str):
            result = await self.graphql_query('''
                query GetProduct($id: ID!) {
                    product(id: $id) {
                        id
                        name
                        price
                        stock
                    }
                }
            ''', variables={'id': product_id})
            self.current_product = result.get('product')
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================================
# GraphQL Subscription Management
# ============================================================================


@dataclass
class GraphQLSubscription:
    """
    Represents an active GraphQL subscription.
    
    Attributes:
        name: Subscription operation name
        query: GraphQL subscription query string
        variables: Query variables
        callback: Function called when data is received
        active: Whether the subscription is currently active
    """
    name: str
    query: Optional[str] = None
    variables: Dict[str, Any] = field(default_factory=dict)
    callback: Optional[Callable[[dict], None]] = None
    active: bool = False
    _task: Optional[asyncio.Task] = None
    
    def to_dict(self) -> dict:
        """Serialize subscription info for client-side handling."""
        return {
            "name": self.name,
            "query": self.query,
            "variables": self.variables,
            "active": self.active,
        }


class GraphQLMixin:
    """
    Mixin for connecting GraphQL subscriptions to LiveView updates.
    
    Class Attributes:
        graphql_endpoint: WebSocket URL for GraphQL subscriptions (ws:// or wss://)
        graphql_http_endpoint: HTTP URL for queries/mutations (defaults to graphql_endpoint)
        subscriptions: List of subscription names to auto-start on mount
        subscription_queries: Dict mapping subscription names to query strings
        auto_subscribe: Whether to automatically start subscriptions on mount (default: True)
    
    Instance Attributes:
        _graphql_subscriptions: Active subscription objects
        _graphql_client: HTTP client for queries (httpx or similar)
        _graphql_ws: WebSocket connection for subscriptions
    """
    
    # Class attributes - override in subclass
    graphql_endpoint: Optional[str] = None
    graphql_http_endpoint: Optional[str] = None
    subscriptions: List[str] = []
    subscription_queries: Dict[str, str] = {}
    auto_subscribe: bool = True
    
    # Internal state
    _graphql_subscriptions: Dict[str, GraphQLSubscription]
    _graphql_client: Any  # httpx.AsyncClient when initialized
    _graphql_ws: Any  # WebSocket connection when active
    _graphql_connected: bool
    _subscription_tasks: Set[asyncio.Task]
    
    def _init_graphql(self) -> None:
        """Initialize GraphQL state. Called from mount if not already initialized."""
        if not hasattr(self, '_graphql_subscriptions'):
            self._graphql_subscriptions = {}
            self._graphql_client = None
            self._graphql_ws = None
            self._graphql_connected = False
            self._subscription_tasks = set()
    
    async def _ensure_http_client(self) -> Any:
        """Ensure HTTP client is available for queries."""
        if self._graphql_client is None:
            try:
                import httpx
                self._graphql_client = httpx.AsyncClient()
            except ImportError:
                raise ImportError(
                    "httpx is required for GraphQL support. "
                    "Install with: pip install httpx"
                )
        return self._graphql_client
    
    def _get_http_endpoint(self) -> str:
        """Get HTTP endpoint for queries, converting ws:// to http:// if needed."""
        if self.graphql_http_endpoint:
            return self.graphql_http_endpoint
        if self.graphql_endpoint:
            endpoint = self.graphql_endpoint
            if endpoint.startswith('ws://'):
                return endpoint.replace('ws://', 'http://', 1)
            elif endpoint.startswith('wss://'):
                return endpoint.replace('wss://', 'https://', 1)
            return endpoint
        raise ValueError("graphql_endpoint or graphql_http_endpoint must be set")
    
    # ========================================================================
    # Subscription Lifecycle
    # ========================================================================
    
    async def start_subscriptions(self) -> None:
        """
        Start all configured subscriptions.
        
        Called automatically on mount if auto_subscribe is True.
        Can be called manually for delayed subscription start.
        """
        self._init_graphql()
        
        if not self.graphql_endpoint:
            logger.warning("[GraphQL] No graphql_endpoint configured, skipping subscriptions")
            return
        
        for sub_name in self.subscriptions:
            await self.subscribe(sub_name)
    
    async def stop_subscriptions(self) -> None:
        """Stop all active subscriptions."""
        self._init_graphql()
        
        # Cancel all subscription tasks
        for task in list(self._subscription_tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._subscription_tasks.clear()
        
        # Mark all subscriptions as inactive
        for sub in self._graphql_subscriptions.values():
            sub.active = False
            sub._task = None
        
        # Close WebSocket if open
        if self._graphql_ws:
            try:
                await self._graphql_ws.close()
            except Exception as e:
                logger.warning(f"[GraphQL] Error closing WebSocket: {e}")
            self._graphql_ws = None
            self._graphql_connected = False
        
        logger.debug("[GraphQL] All subscriptions stopped")
    
    async def subscribe(
        self,
        name: str,
        query: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        callback: Optional[Callable[[dict], None]] = None,
    ) -> GraphQLSubscription:
        """
        Start a GraphQL subscription.
        
        Args:
            name: Subscription name (used for lookup and on_subscription callback)
            query: GraphQL subscription query (uses subscription_queries[name] if not provided)
            variables: Query variables
            callback: Optional callback function for this specific subscription
        
        Returns:
            GraphQLSubscription object
        """
        self._init_graphql()
        
        # Get query from class config if not provided
        if query is None:
            query = self.subscription_queries.get(name)
            if query is None:
                # Generate default subscription query
                query = f"""
                    subscription {name} {{
                        {name} {{
                            __typename
                        }}
                    }}
                """
        
        # Create subscription object
        sub = GraphQLSubscription(
            name=name,
            query=query,
            variables=variables or {},
            callback=callback,
            active=True,
        )
        self._graphql_subscriptions[name] = sub
        
        # Start subscription task
        task = asyncio.create_task(self._run_subscription(sub))
        sub._task = task
        self._subscription_tasks.add(task)
        task.add_done_callback(self._subscription_tasks.discard)
        
        logger.info(f"[GraphQL] Started subscription: {name}")
        return sub
    
    async def unsubscribe(self, name: str) -> None:
        """Stop a specific subscription."""
        self._init_graphql()
        
        sub = self._graphql_subscriptions.get(name)
        if sub and sub._task:
            sub.active = False
            sub._task.cancel()
            try:
                await sub._task
            except asyncio.CancelledError:
                pass
            sub._task = None
            logger.info(f"[GraphQL] Unsubscribed from: {name}")
    
    async def _run_subscription(self, sub: GraphQLSubscription) -> None:
        """
        Run a subscription, handling WebSocket connection and messages.
        
        Uses graphql-ws protocol for Strawberry/Apollo compatibility.
        """
        try:
            import websockets
        except ImportError:
            logger.error(
                "[GraphQL] websockets is required for subscriptions. "
                "Install with: pip install websockets"
            )
            return
        
        retry_count = 0
        max_retries = 5
        
        while sub.active and retry_count < max_retries:
            try:
                async with websockets.connect(
                    self.graphql_endpoint,
                    subprotocols=["graphql-transport-ws"],
                ) as ws:
                    self._graphql_ws = ws
                    self._graphql_connected = True
                    
                    # Connection init (graphql-ws protocol)
                    await ws.send(json.dumps({"type": "connection_init", "payload": {}}))
                    
                    # Wait for ack
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(response)
                    if msg.get("type") != "connection_ack":
                        logger.warning(f"[GraphQL] Unexpected response: {msg}")
                    
                    # Subscribe
                    sub_id = f"sub_{sub.name}_{id(sub)}"
                    await ws.send(json.dumps({
                        "id": sub_id,
                        "type": "subscribe",
                        "payload": {
                            "query": sub.query,
                            "variables": sub.variables,
                        },
                    }))
                    
                    logger.debug(f"[GraphQL] Subscribed to {sub.name}")
                    retry_count = 0  # Reset on successful connection
                    
                    # Listen for messages
                    async for message in ws:
                        if not sub.active:
                            break
                        
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")
                            
                            if msg_type == "next":
                                payload = data.get("payload", {})
                                await self._handle_subscription_data(sub, payload)
                            elif msg_type == "error":
                                errors = data.get("payload", [])
                                await self._handle_subscription_error(sub, errors)
                            elif msg_type == "complete":
                                logger.info(f"[GraphQL] Subscription {sub.name} completed")
                                break
                            elif msg_type == "ping":
                                await ws.send(json.dumps({"type": "pong"}))
                        except json.JSONDecodeError as e:
                            logger.warning(f"[GraphQL] Invalid JSON: {e}")
            
            except websockets.exceptions.ConnectionClosed:
                logger.warning(f"[GraphQL] Connection closed for {sub.name}")
            except asyncio.TimeoutError:
                logger.warning(f"[GraphQL] Connection timeout for {sub.name}")
            except Exception as e:
                logger.error(f"[GraphQL] Subscription error for {sub.name}: {e}")
            
            # Retry with exponential backoff
            if sub.active:
                retry_count += 1
                wait_time = min(2 ** retry_count, 30)
                logger.info(f"[GraphQL] Reconnecting {sub.name} in {wait_time}s (attempt {retry_count})")
                await asyncio.sleep(wait_time)
        
        self._graphql_connected = False
    
    async def _handle_subscription_data(
        self,
        sub: GraphQLSubscription,
        payload: dict,
    ) -> None:
        """Handle incoming subscription data."""
        data = payload.get("data", {})
        
        # Call subscription-specific callback if set
        if sub.callback:
            if asyncio.iscoroutinefunction(sub.callback):
                await sub.callback(data)
            else:
                sub.callback(data)
        
        # Call general on_subscription handler
        if asyncio.iscoroutinefunction(self.on_subscription):
            await self.on_subscription(sub.name, data)
        else:
            self.on_subscription(sub.name, data)
    
    async def _handle_subscription_error(
        self,
        sub: GraphQLSubscription,
        errors: list,
    ) -> None:
        """Handle subscription errors."""
        error = Exception(f"GraphQL subscription error: {errors}")
        logger.error(f"[GraphQL] Subscription {sub.name} error: {errors}")
        
        if asyncio.iscoroutinefunction(self.on_subscription_error):
            await self.on_subscription_error(sub.name, error)
        else:
            self.on_subscription_error(sub.name, error)
    
    # ========================================================================
    # Override Points
    # ========================================================================
    
    def on_subscription(self, name: str, data: dict) -> None:
        """
        Called when subscription data is received.
        
        Override this method to handle subscription updates.
        
        Args:
            name: Subscription name
            data: The data payload from the subscription
        """
        logger.debug(f"[GraphQL] Subscription {name} received data: {data}")
    
    def on_subscription_error(self, name: str, error: Exception) -> None:
        """
        Called when a subscription error occurs.
        
        Override this method to handle subscription errors.
        
        Args:
            name: Subscription name
            error: The exception that occurred
        """
        logger.error(f"[GraphQL] Subscription {name} error: {error}")
    
    # ========================================================================
    # GraphQL Queries & Mutations
    # ========================================================================
    
    async def graphql_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> dict:
        """
        Execute a GraphQL query or mutation.
        
        Args:
            query: GraphQL query string
            variables: Query variables
            operation_name: Optional operation name
        
        Returns:
            The 'data' portion of the GraphQL response
        
        Raises:
            Exception: If the query fails or returns errors
        """
        self._init_graphql()
        
        client = await self._ensure_http_client()
        endpoint = self._get_http_endpoint()
        
        payload = {
            "query": query,
        }
        if variables:
            payload["variables"] = variables
        if operation_name:
            payload["operationName"] = operation_name
        
        try:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            result = response.json()
            
            # Check for GraphQL errors
            if "errors" in result:
                errors = result["errors"]
                error_msg = "; ".join(e.get("message", str(e)) for e in errors)
                raise Exception(f"GraphQL error: {error_msg}")
            
            return result.get("data", {})
            
        except Exception as e:
            logger.error(f"[GraphQL] Query failed: {e}")
            raise
    
    async def graphql_mutate(
        self,
        mutation: str,
        variables: Optional[Dict[str, Any]] = None,
        operation_name: Optional[str] = None,
    ) -> dict:
        """
        Execute a GraphQL mutation.
        
        Alias for graphql_query for semantic clarity.
        """
        return await self.graphql_query(mutation, variables, operation_name)
    
    # ========================================================================
    # Cleanup
    # ========================================================================
    
    async def _cleanup_graphql(self) -> None:
        """Clean up GraphQL resources. Called on view disconnect."""
        await self.stop_subscriptions()
        
        if self._graphql_client:
            try:
                await self._graphql_client.aclose()
            except Exception as e:
                logger.warning(f"[GraphQL] Error closing HTTP client: {e}")
            self._graphql_client = None
