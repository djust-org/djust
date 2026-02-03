"""
REST API integration for djust LiveView.

Provides RESTMixin for fetching and syncing data from REST APIs,
enabling seamless integration with external services.

Example usage:

    from djust import LiveView
    from djust.integrations import RESTMixin
    from djust.decorators import event_handler

    class ProductView(RESTMixin, LiveView):
        template_name = "products.html"
        api_base = "/api/v1"
        
        async def mount(self, request, **kwargs):
            self.products = await self.api_get("/products/")
        
        @event_handler
        async def refresh(self):
            self.products = await self.api_get("/products/")
        
        @event_handler
        async def add_product(self, name: str, price: float):
            new_product = await self.api_post("/products/", {
                "name": name,
                "price": price,
            })
            self.products.append(new_product)

    # With authentication
    class AuthenticatedProductView(RESTMixin, LiveView):
        api_base = "https://api.example.com"
        api_headers = {"X-API-Key": "secret"}
        
        # Or use method for dynamic auth
        def get_api_headers(self):
            return {"Authorization": f"Bearer {self.request.user.api_token}"}

Polling Example:

    class StockView(RESTMixin, LiveView):
        api_base = "/api/v1"
        polling_interval = 5000  # Poll every 5 seconds
        polling_endpoints = ["/stocks/", "/prices/"]
        
        async def on_poll(self, endpoint: str, data: Any):
            if endpoint == "/stocks/":
                self.stocks = data
            elif endpoint == "/prices/":
                self.prices = data

Error Handling:

    class ProductView(RESTMixin, LiveView):
        async def on_api_error(self, error: APIError):
            self.error_message = error.message
            self.push_event("toast", {"type": "error", "message": error.message})
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


# ============================================================================
# API Response Types
# ============================================================================


@dataclass
class APIResponse:
    """
    Wrapper for REST API responses.
    
    Attributes:
        data: Response body (parsed JSON or raw text)
        status_code: HTTP status code
        headers: Response headers
        ok: True if status code is 2xx
    """
    data: Any
    status_code: int
    headers: Dict[str, str]
    ok: bool
    
    def json(self) -> Any:
        """Return data as JSON (already parsed)."""
        return self.data
    
    def __bool__(self) -> bool:
        return self.ok


class APIError(Exception):
    """
    Exception raised for API errors.
    
    Attributes:
        message: Error message
        status_code: HTTP status code (if applicable)
        response: Raw response object (if available)
        data: Response body (if available)
    """
    
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response: Any = None,
        data: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response
        self.data = data
    
    def __str__(self) -> str:
        if self.status_code:
            return f"APIError({self.status_code}): {self.message}"
        return f"APIError: {self.message}"


# ============================================================================
# REST API Mixin
# ============================================================================


class RESTMixin:
    """
    Mixin for integrating REST APIs with LiveViews.
    
    Class Attributes:
        api_base: Base URL for API requests (e.g., "/api/v1" or "https://api.example.com")
        api_headers: Default headers for all API requests
        api_timeout: Request timeout in seconds (default: 30)
        polling_interval: Interval for polling in milliseconds (0 = disabled)
        polling_endpoints: List of endpoints to poll
        optimistic_updates: Enable optimistic UI updates (default: True)
    
    Instance Attributes:
        _api_client: HTTP client (httpx.AsyncClient)
        _polling_task: Background polling task
        _pending_requests: Dict of pending optimistic updates
    """
    
    # Class attributes - override in subclass
    api_base: str = ""
    api_headers: Dict[str, str] = {}
    api_timeout: float = 30.0
    polling_interval: int = 0  # 0 = disabled
    polling_endpoints: List[str] = []
    optimistic_updates: bool = True
    
    # Internal state
    _api_client: Any  # httpx.AsyncClient
    _polling_task: Optional[asyncio.Task]
    _pending_requests: Dict[str, Any]
    _api_cache: Dict[str, Any]
    
    def _init_rest(self) -> None:
        """Initialize REST state. Called automatically before first API call."""
        if not hasattr(self, '_api_client'):
            self._api_client = None
            self._polling_task = None
            self._pending_requests = {}
            self._api_cache = {}
    
    async def _ensure_client(self) -> Any:
        """Ensure HTTP client is available."""
        self._init_rest()
        
        if self._api_client is None:
            try:
                import httpx
                self._api_client = httpx.AsyncClient(timeout=self.api_timeout)
            except ImportError:
                raise ImportError(
                    "httpx is required for REST API support. "
                    "Install with: pip install httpx"
                )
        return self._api_client
    
    def get_api_headers(self) -> Dict[str, str]:
        """
        Get headers for API requests.
        
        Override this method to provide dynamic headers (e.g., auth tokens).
        
        Returns:
            Dict of headers to include in API requests
        """
        return dict(self.api_headers)
    
    def _build_url(self, endpoint: str) -> str:
        """Build full URL from endpoint."""
        if endpoint.startswith(('http://', 'https://')):
            return endpoint
        
        base = self.api_base
        if not base:
            return endpoint
        
        # Handle relative URLs
        if base.startswith('/') and endpoint.startswith('/'):
            return base.rstrip('/') + endpoint
        
        return urljoin(base.rstrip('/') + '/', endpoint.lstrip('/'))
    
    # ========================================================================
    # Core API Methods
    # ========================================================================
    
    async def api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> APIResponse:
        """
        Make an HTTP request to the API.
        
        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE)
            endpoint: API endpoint path
            data: Request body (JSON serializable)
            params: Query parameters
            headers: Additional headers (merged with defaults)
            **kwargs: Additional arguments passed to httpx
        
        Returns:
            APIResponse object
        
        Raises:
            APIError: If request fails
        """
        client = await self._ensure_client()
        url = self._build_url(endpoint)
        
        # Merge headers
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request_headers.update(self.get_api_headers())
        if headers:
            request_headers.update(headers)
        
        try:
            # Make request
            response = await client.request(
                method=method,
                url=url,
                json=data if data is not None else None,
                params=params,
                headers=request_headers,
                **kwargs,
            )
            
            # Parse response
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = response.text
            
            api_response = APIResponse(
                data=response_data,
                status_code=response.status_code,
                headers=dict(response.headers),
                ok=response.is_success,
            )
            
            # Handle errors
            if not api_response.ok:
                error = APIError(
                    message=self._extract_error_message(response_data, response.status_code),
                    status_code=response.status_code,
                    response=response,
                    data=response_data,
                )
                await self._handle_api_error(error)
                raise error
            
            return api_response
            
        except Exception as e:
            if isinstance(e, APIError):
                raise
            
            # Wrap other exceptions
            error = APIError(
                message=str(e),
                status_code=None,
                response=None,
                data=None,
            )
            await self._handle_api_error(error)
            raise error from e
    
    def _extract_error_message(self, data: Any, status_code: int) -> str:
        """Extract error message from response data."""
        if isinstance(data, dict):
            # Common error response formats
            for key in ['message', 'error', 'detail', 'errors']:
                if key in data:
                    val = data[key]
                    if isinstance(val, str):
                        return val
                    elif isinstance(val, list) and val:
                        return str(val[0])
                    elif isinstance(val, dict):
                        return str(val)
        
        return f"HTTP {status_code} error"
    
    async def _handle_api_error(self, error: APIError) -> None:
        """Handle API errors by calling on_api_error hook."""
        try:
            if asyncio.iscoroutinefunction(self.on_api_error):
                await self.on_api_error(error)
            else:
                self.on_api_error(error)
        except Exception as e:
            logger.error(f"[REST] Error in on_api_error handler: {e}")
    
    # ========================================================================
    # Convenience Methods
    # ========================================================================
    
    async def api_get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Any:
        """
        Make a GET request.
        
        Returns:
            Response data (parsed JSON)
        """
        response = await self.api_request("GET", endpoint, params=params, **kwargs)
        return response.data
    
    async def api_post(
        self,
        endpoint: str,
        data: Optional[Any] = None,
        **kwargs,
    ) -> Any:
        """
        Make a POST request.
        
        Returns:
            Response data (parsed JSON)
        """
        response = await self.api_request("POST", endpoint, data=data, **kwargs)
        return response.data
    
    async def api_put(
        self,
        endpoint: str,
        data: Optional[Any] = None,
        **kwargs,
    ) -> Any:
        """
        Make a PUT request.
        
        Returns:
            Response data (parsed JSON)
        """
        response = await self.api_request("PUT", endpoint, data=data, **kwargs)
        return response.data
    
    async def api_patch(
        self,
        endpoint: str,
        data: Optional[Any] = None,
        **kwargs,
    ) -> Any:
        """
        Make a PATCH request.
        
        Returns:
            Response data (parsed JSON)
        """
        response = await self.api_request("PATCH", endpoint, data=data, **kwargs)
        return response.data
    
    async def api_delete(
        self,
        endpoint: str,
        **kwargs,
    ) -> Any:
        """
        Make a DELETE request.
        
        Returns:
            Response data (parsed JSON)
        """
        response = await self.api_request("DELETE", endpoint, **kwargs)
        return response.data
    
    # ========================================================================
    # Polling
    # ========================================================================
    
    async def start_polling(self) -> None:
        """
        Start polling configured endpoints.
        
        Called automatically on mount if polling_interval > 0.
        """
        self._init_rest()
        
        if self.polling_interval <= 0:
            return
        
        if not self.polling_endpoints:
            logger.warning("[REST] polling_interval set but polling_endpoints is empty")
            return
        
        if self._polling_task and not self._polling_task.done():
            return  # Already polling
        
        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info(f"[REST] Started polling every {self.polling_interval}ms")
    
    async def stop_polling(self) -> None:
        """Stop polling."""
        self._init_rest()
        
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
            logger.info("[REST] Stopped polling")
    
    async def _poll_loop(self) -> None:
        """Background polling loop."""
        while True:
            try:
                for endpoint in self.polling_endpoints:
                    try:
                        data = await self.api_get(endpoint)
                        
                        # Check if data changed (simple comparison)
                        cache_key = f"poll:{endpoint}"
                        old_data = self._api_cache.get(cache_key)
                        
                        if data != old_data:
                            self._api_cache[cache_key] = data
                            
                            # Call handler
                            if asyncio.iscoroutinefunction(self.on_poll):
                                await self.on_poll(endpoint, data)
                            else:
                                self.on_poll(endpoint, data)
                    
                    except APIError as e:
                        logger.warning(f"[REST] Poll failed for {endpoint}: {e}")
                
                # Wait for next poll
                await asyncio.sleep(self.polling_interval / 1000.0)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[REST] Polling error: {e}")
                await asyncio.sleep(1)  # Brief pause on error
    
    # ========================================================================
    # Optimistic Updates
    # ========================================================================
    
    async def api_optimistic(
        self,
        endpoint: str,
        method: str,
        data: Any,
        optimistic_value: Any,
        rollback_value: Any,
        target_attr: str,
    ) -> Any:
        """
        Make an API request with optimistic update.
        
        The target attribute is updated immediately with optimistic_value,
        then either kept (on success) or rolled back to rollback_value (on failure).
        
        Args:
            endpoint: API endpoint
            method: HTTP method
            data: Request data
            optimistic_value: Value to set immediately
            rollback_value: Value to set on failure
            target_attr: Attribute name to update
        
        Returns:
            API response data
        
        Example:
            # Optimistically add item to list
            await self.api_optimistic(
                "/items/",
                "POST",
                {"name": "New Item"},
                optimistic_value=[*self.items, {"id": "temp", "name": "New Item"}],
                rollback_value=self.items,
                target_attr="items",
            )
        """
        if not self.optimistic_updates:
            # Fall back to regular request
            return await self.api_request(method, endpoint, data=data)
        
        # Apply optimistic update
        old_value = getattr(self, target_attr, None)
        setattr(self, target_attr, optimistic_value)
        
        try:
            # Make actual request
            response = await self.api_request(method, endpoint, data=data)
            return response.data
            
        except APIError:
            # Rollback on error
            setattr(self, target_attr, rollback_value if rollback_value is not None else old_value)
            raise
    
    # ========================================================================
    # Override Points
    # ========================================================================
    
    def on_poll(self, endpoint: str, data: Any) -> None:
        """
        Called when polling returns new data.
        
        Override this method to handle polled data.
        
        Args:
            endpoint: The endpoint that was polled
            data: The response data
        """
        logger.debug(f"[REST] Poll received data from {endpoint}")
    
    def on_api_error(self, error: APIError) -> None:
        """
        Called when an API error occurs.
        
        Override this method to handle API errors.
        
        Args:
            error: The APIError that occurred
        """
        logger.error(f"[REST] API error: {error}")
    
    # ========================================================================
    # Cleanup
    # ========================================================================
    
    async def _cleanup_rest(self) -> None:
        """Clean up REST resources. Called on view disconnect."""
        await self.stop_polling()
        
        if self._api_client:
            try:
                await self._api_client.aclose()
            except Exception as e:
                logger.warning(f"[REST] Error closing HTTP client: {e}")
            self._api_client = None
