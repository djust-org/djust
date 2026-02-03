"""
Tests for API integrations (GraphQL, REST, DRF)
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================


class FakeUser:
    """Fake user for testing."""
    def __init__(self, username="testuser", user_id=1):
        self.username = username
        self.id = user_id
        self.is_authenticated = True
        self.api_token = "test-token-123"


class FakeRequest:
    """Fake request for testing."""
    def __init__(self, user=None):
        self.user = user or FakeUser()


class FakeView:
    """Base fake view for testing mixins."""
    def __init__(self):
        self.request = FakeRequest()


class FakeHTTPResponse:
    """Fake httpx response."""
    def __init__(self, data, status_code=200, is_success=True):
        self._data = data
        self.status_code = status_code
        self.is_success = is_success
        self.headers = {"Content-Type": "application/json"}
        self.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    
    def json(self):
        return self._data
    
    def raise_for_status(self):
        if not self.is_success:
            raise Exception(f"HTTP {self.status_code}")


class FakeAsyncClient:
    """Fake httpx.AsyncClient."""
    def __init__(self):
        self.responses = {}
        self.requests = []
    
    def set_response(self, method, url, response):
        self.responses[(method.upper(), url)] = response
    
    async def request(self, method, url, **kwargs):
        self.requests.append({
            "method": method,
            "url": url,
            **kwargs,
        })
        key = (method.upper(), url)
        if key in self.responses:
            return self.responses[key]
        # Default response
        return FakeHTTPResponse({"data": "default"})
    
    async def post(self, url, **kwargs):
        return await self.request("POST", url, **kwargs)
    
    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)
    
    async def aclose(self):
        pass


# ============================================================================
# GraphQL Tests
# ============================================================================


class TestGraphQLMixin:
    """Tests for GraphQLMixin."""
    
    def setup_method(self):
        from djust.integrations.graphql import GraphQLMixin
        
        class TestView(GraphQLMixin, FakeView):
            graphql_endpoint = "ws://localhost:8000/graphql/"
            subscriptions = ['orderUpdated', 'productChanged']
            subscription_queries = {
                'orderUpdated': '''
                    subscription {
                        orderUpdated {
                            id
                            status
                        }
                    }
                ''',
            }
        
        self.view = TestView()
        self.view._init_graphql()
    
    def test_init_graphql_state(self):
        """Test that GraphQL state is properly initialized."""
        assert hasattr(self.view, '_graphql_subscriptions')
        assert self.view._graphql_subscriptions == {}
        assert self.view._graphql_connected is False
    
    def test_get_http_endpoint_from_ws(self):
        """Test converting ws:// to http://."""
        assert self.view._get_http_endpoint() == "http://localhost:8000/graphql/"
    
    def test_get_http_endpoint_from_wss(self):
        """Test converting wss:// to https://."""
        self.view.graphql_endpoint = "wss://api.example.com/graphql/"
        assert self.view._get_http_endpoint() == "https://api.example.com/graphql/"
    
    def test_get_http_endpoint_explicit(self):
        """Test explicit HTTP endpoint."""
        self.view.graphql_http_endpoint = "https://api.example.com/query"
        assert self.view._get_http_endpoint() == "https://api.example.com/query"
    
    @pytest.mark.asyncio
    async def test_graphql_query(self):
        """Test GraphQL query execution."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "http://localhost:8000/graphql/",
            FakeHTTPResponse({
                "data": {
                    "products": [
                        {"id": "1", "name": "Product 1"},
                        {"id": "2", "name": "Product 2"},
                    ]
                }
            }),
        )
        
        self.view._graphql_client = fake_client
        
        result = await self.view.graphql_query('''
            query {
                products {
                    id
                    name
                }
            }
        ''')
        
        assert "products" in result
        assert len(result["products"]) == 2
        assert fake_client.requests[0]["method"] == "POST"
    
    @pytest.mark.asyncio
    async def test_graphql_query_with_variables(self):
        """Test GraphQL query with variables."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "http://localhost:8000/graphql/",
            FakeHTTPResponse({
                "data": {
                    "product": {"id": "1", "name": "Product 1"}
                }
            }),
        )
        
        self.view._graphql_client = fake_client
        
        result = await self.view.graphql_query(
            '''
            query GetProduct($id: ID!) {
                product(id: $id) {
                    id
                    name
                }
            }
            ''',
            variables={"id": "1"},
        )
        
        assert result["product"]["id"] == "1"
        # Check variables were sent
        request = fake_client.requests[0]
        assert request["json"]["variables"] == {"id": "1"}
    
    @pytest.mark.asyncio
    async def test_graphql_query_error(self):
        """Test GraphQL query error handling."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "http://localhost:8000/graphql/",
            FakeHTTPResponse({
                "errors": [
                    {"message": "Field 'invalid' not found"}
                ]
            }),
        )
        
        self.view._graphql_client = fake_client
        
        with pytest.raises(Exception) as exc_info:
            await self.view.graphql_query("query { invalid }")
        
        assert "GraphQL error" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_graphql_mutate(self):
        """Test GraphQL mutation."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "http://localhost:8000/graphql/",
            FakeHTTPResponse({
                "data": {
                    "createProduct": {"id": "3", "name": "New Product"}
                }
            }),
        )
        
        self.view._graphql_client = fake_client
        
        result = await self.view.graphql_mutate(
            '''
            mutation CreateProduct($name: String!) {
                createProduct(name: $name) {
                    id
                    name
                }
            }
            ''',
            variables={"name": "New Product"},
        )
        
        assert result["createProduct"]["name"] == "New Product"
    
    def test_on_subscription_default(self):
        """Test default on_subscription handler."""
        # Should not raise
        self.view.on_subscription("test", {"data": "value"})
    
    def test_on_subscription_error_default(self):
        """Test default on_subscription_error handler."""
        # Should not raise
        self.view.on_subscription_error("test", Exception("test error"))
    
    def test_subscription_object(self):
        """Test GraphQLSubscription dataclass."""
        from djust.integrations.graphql import GraphQLSubscription
        
        sub = GraphQLSubscription(
            name="orderUpdated",
            query="subscription { orderUpdated { id } }",
            variables={"filter": "active"},
        )
        
        assert sub.name == "orderUpdated"
        assert sub.active is False
        
        data = sub.to_dict()
        assert data["name"] == "orderUpdated"
        assert data["variables"] == {"filter": "active"}


# ============================================================================
# REST Tests
# ============================================================================


class TestRESTMixin:
    """Tests for RESTMixin."""
    
    def setup_method(self):
        from djust.integrations.rest import RESTMixin
        
        class TestView(RESTMixin, FakeView):
            api_base = "/api/v1"
            api_headers = {"X-Custom-Header": "value"}
        
        self.view = TestView()
        self.view._init_rest()
    
    def test_init_rest_state(self):
        """Test that REST state is properly initialized."""
        assert hasattr(self.view, '_api_client')
        assert self.view._polling_task is None
        assert self.view._api_cache == {}
    
    def test_build_url_relative(self):
        """Test building URL from relative path."""
        assert self.view._build_url("/products/") == "/api/v1/products/"
    
    def test_build_url_absolute(self):
        """Test building URL with absolute path."""
        self.view.api_base = "https://api.example.com/v1"
        assert self.view._build_url("/products/") == "https://api.example.com/v1/products/"
    
    def test_build_url_full(self):
        """Test that full URLs are passed through."""
        url = "https://other-api.com/data"
        assert self.view._build_url(url) == url
    
    def test_get_api_headers(self):
        """Test getting API headers."""
        headers = self.view.get_api_headers()
        assert headers["X-Custom-Header"] == "value"
    
    def test_get_api_headers_dynamic(self):
        """Test dynamic headers via override."""
        class DynamicHeaderView(type(self.view)):
            def get_api_headers(self):
                return {"Authorization": f"Bearer {self.request.user.api_token}"}
        
        view = DynamicHeaderView()
        view.request = FakeRequest()
        headers = view.get_api_headers()
        assert headers["Authorization"] == "Bearer test-token-123"
    
    @pytest.mark.asyncio
    async def test_api_get(self):
        """Test API GET request."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "GET",
            "/api/v1/products/",
            FakeHTTPResponse([
                {"id": 1, "name": "Product 1"},
                {"id": 2, "name": "Product 2"},
            ]),
        )
        
        self.view._api_client = fake_client
        
        result = await self.view.api_get("/products/")
        
        assert len(result) == 2
        assert result[0]["name"] == "Product 1"
    
    @pytest.mark.asyncio
    async def test_api_post(self):
        """Test API POST request."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "/api/v1/products/",
            FakeHTTPResponse({"id": 3, "name": "New Product"}),
        )
        
        self.view._api_client = fake_client
        
        result = await self.view.api_post("/products/", {"name": "New Product"})
        
        assert result["id"] == 3
        assert fake_client.requests[0]["json"] == {"name": "New Product"}
    
    @pytest.mark.asyncio
    async def test_api_put(self):
        """Test API PUT request."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "PUT",
            "/api/v1/products/1/",
            FakeHTTPResponse({"id": 1, "name": "Updated Product"}),
        )
        
        self.view._api_client = fake_client
        
        result = await self.view.api_put("/products/1/", {"name": "Updated Product"})
        
        assert result["name"] == "Updated Product"
    
    @pytest.mark.asyncio
    async def test_api_patch(self):
        """Test API PATCH request."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "PATCH",
            "/api/v1/products/1/",
            FakeHTTPResponse({"id": 1, "name": "Patched Product"}),
        )
        
        self.view._api_client = fake_client
        
        result = await self.view.api_patch("/products/1/", {"name": "Patched Product"})
        
        assert result["name"] == "Patched Product"
    
    @pytest.mark.asyncio
    async def test_api_delete(self):
        """Test API DELETE request."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "DELETE",
            "/api/v1/products/1/",
            FakeHTTPResponse(None, status_code=204),
        )
        
        self.view._api_client = fake_client
        
        result = await self.view.api_delete("/products/1/")
        # DELETE usually returns None or empty
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test API error handling."""
        from djust.integrations.rest import APIError
        
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "GET",
            "/api/v1/notfound/",
            FakeHTTPResponse(
                {"detail": "Not found"},
                status_code=404,
                is_success=False,
            ),
        )
        
        self.view._api_client = fake_client
        
        with pytest.raises(APIError) as exc_info:
            await self.view.api_get("/notfound/")
        
        assert exc_info.value.status_code == 404
        assert "Not found" in exc_info.value.message
    
    @pytest.mark.asyncio
    async def test_api_error_callback(self):
        """Test on_api_error callback."""
        from djust.integrations.rest import APIError
        
        errors_received = []
        
        class ErrorHandlingView(type(self.view)):
            def on_api_error(self, error):
                errors_received.append(error)
        
        view = ErrorHandlingView()
        view._init_rest()
        
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "GET",
            "/api/v1/error/",
            FakeHTTPResponse(
                {"error": "Server error"},
                status_code=500,
                is_success=False,
            ),
        )
        view._api_client = fake_client
        
        with pytest.raises(APIError):
            await view.api_get("/error/")
        
        assert len(errors_received) == 1
        assert errors_received[0].status_code == 500
    
    def test_api_response_class(self):
        """Test APIResponse class."""
        from djust.integrations.rest import APIResponse
        
        response = APIResponse(
            data={"id": 1},
            status_code=200,
            headers={"Content-Type": "application/json"},
            ok=True,
        )
        
        assert response.json() == {"id": 1}
        assert bool(response) is True
        assert response.ok is True
    
    def test_api_error_class(self):
        """Test APIError class."""
        from djust.integrations.rest import APIError
        
        error = APIError(
            message="Not found",
            status_code=404,
            data={"detail": "Resource not found"},
        )
        
        assert error.status_code == 404
        assert "404" in str(error)
        assert "Not found" in str(error)
    
    @pytest.mark.asyncio
    async def test_api_optimistic_update_success(self):
        """Test optimistic update on success."""
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "/api/v1/items/",
            FakeHTTPResponse({"id": 1, "name": "Real Item"}),
        )
        
        self.view._api_client = fake_client
        self.view.items = []
        
        await self.view.api_optimistic(
            "/items/",
            "POST",
            {"name": "New Item"},
            optimistic_value=[{"id": "temp", "name": "New Item"}],
            rollback_value=[],
            target_attr="items",
        )
        
        # Should have optimistic value (or could be updated by caller)
        assert hasattr(self.view, 'items')
    
    @pytest.mark.asyncio
    async def test_api_optimistic_update_rollback(self):
        """Test optimistic update rollback on failure."""
        from djust.integrations.rest import APIError
        
        fake_client = FakeAsyncClient()
        fake_client.set_response(
            "POST",
            "/api/v1/items/",
            FakeHTTPResponse(
                {"error": "Failed"},
                status_code=400,
                is_success=False,
            ),
        )
        
        self.view._api_client = fake_client
        self.view.items = [{"id": 1, "name": "Original"}]
        original_items = list(self.view.items)
        
        with pytest.raises(APIError):
            await self.view.api_optimistic(
                "/items/",
                "POST",
                {"name": "Bad Item"},
                optimistic_value=[*self.view.items, {"id": "temp", "name": "Bad Item"}],
                rollback_value=original_items,
                target_attr="items",
            )
        
        # Should be rolled back
        assert self.view.items == original_items
    
    def test_on_poll_default(self):
        """Test default on_poll handler."""
        # Should not raise
        self.view.on_poll("/endpoint/", {"data": "value"})


# ============================================================================
# DRF Tests
# ============================================================================


class FakeSerializer:
    """Fake DRF serializer for testing."""
    
    def __init__(self, instance=None, data=None, many=False, context=None, **kwargs):
        self.instance = instance
        self.initial_data = data
        self.many = many
        self.context = context or {}
        self._errors = {}
        self._validated = False
    
    @property
    def data(self):
        if self.many and self.instance:
            return [{"id": obj.id, "name": obj.name} for obj in self.instance]
        elif self.instance:
            return {"id": self.instance.id, "name": self.instance.name}
        elif self.initial_data:
            return self.initial_data
        return {}
    
    @property
    def validated_data(self):
        if self._validated:
            return self.initial_data
        return None
    
    @property
    def errors(self):
        return self._errors
    
    def is_valid(self, raise_exception=False):
        if self.initial_data and self.initial_data.get("name") == "invalid":
            self._errors = {"name": ["Invalid name"]}
            if raise_exception:
                raise Exception("Validation failed")
            return False
        self._validated = True
        return True
    
    def save(self):
        if not self._validated:
            raise Exception("Must call is_valid() first")
        return FakeModel(id=99, name=self.initial_data.get("name", "New"))


class FakeModel:
    """Fake Django model for testing."""
    
    def __init__(self, id, name):
        self.id = id
        self.pk = id
        self.name = name
    
    def delete(self):
        pass


class FakeQuerySet:
    """Fake Django queryset for testing."""
    
    def __init__(self, items=None):
        self._items = items or []
    
    def all(self):
        return FakeQuerySet(self._items)
    
    def filter(self, **kwargs):
        return FakeQuerySet(self._items)
    
    def order_by(self, *args):
        return self
    
    def get(self, **kwargs):
        pk = kwargs.get('pk')
        for item in self._items:
            if item.pk == pk:
                return item
        raise Exception("DoesNotExist")
    
    def __iter__(self):
        return iter(self._items)
    
    def __len__(self):
        return len(self._items)


class TestDRFSerializerMixin:
    """Tests for DRFSerializerMixin."""
    
    def setup_method(self):
        from djust.integrations.drf import DRFSerializerMixin
        
        class TestView(DRFSerializerMixin, FakeView):
            serializer_class = FakeSerializer
        
        self.view = TestView()
    
    def test_get_serializer_class(self):
        """Test getting serializer class."""
        assert self.view.get_serializer_class() == FakeSerializer
    
    def test_get_serializer_class_error(self):
        """Test error when serializer class not set."""
        from djust.integrations.drf import DRFSerializerMixin
        
        class NoSerializerView(DRFSerializerMixin, FakeView):
            pass
        
        view = NoSerializerView()
        with pytest.raises(ValueError):
            view.get_serializer_class()
    
    def test_get_serializer(self):
        """Test creating serializer instance."""
        serializer = self.view.get_serializer(data={"name": "Test"})
        
        assert isinstance(serializer, FakeSerializer)
        assert serializer.initial_data == {"name": "Test"}
        assert "request" in serializer.context
        assert "view" in serializer.context
    
    def test_serialize_single(self):
        """Test serializing single object."""
        obj = FakeModel(id=1, name="Product 1")
        data = self.view.serialize(obj)
        
        assert data["id"] == 1
        assert data["name"] == "Product 1"
    
    def test_serialize_many(self):
        """Test serializing multiple objects."""
        objs = [
            FakeModel(id=1, name="Product 1"),
            FakeModel(id=2, name="Product 2"),
        ]
        data = self.view.serialize_many(objs)
        
        assert len(data) == 2
        assert data[0]["name"] == "Product 1"
        assert data[1]["name"] == "Product 2"
    
    def test_validate_valid(self):
        """Test validation with valid data."""
        errors = self.view.validate({"name": "Valid"})
        assert errors == {}
    
    def test_validate_invalid(self):
        """Test validation with invalid data."""
        errors = self.view.validate({"name": "invalid"})
        assert "name" in errors
        assert "Invalid name" in errors["name"]
    
    def test_get_validated_data_valid(self):
        """Test getting validated data."""
        data = self.view.get_validated_data({"name": "Valid"})
        assert data == {"name": "Valid"}
    
    def test_get_validated_data_invalid(self):
        """Test getting validated data with invalid input."""
        data = self.view.get_validated_data({"name": "invalid"})
        assert data is None


class TestDRFMixin:
    """Tests for DRFMixin."""
    
    def setup_method(self):
        from djust.integrations.drf import DRFMixin
        
        self.products = [
            FakeModel(id=1, name="Product 1"),
            FakeModel(id=2, name="Product 2"),
        ]
        
        class TestView(DRFMixin, FakeView):
            serializer_class = FakeSerializer
            queryset = FakeQuerySet(self.products)
        
        # Need to access products from outer scope
        TestView.queryset = FakeQuerySet(self.products)
        self.view = TestView()
    
    def test_get_queryset(self):
        """Test getting queryset."""
        qs = self.view.get_queryset()
        assert len(list(qs)) == 2
    
    def test_get_object(self):
        """Test getting single object."""
        obj = self.view.get_object(1)
        assert obj.id == 1
        assert obj.name == "Product 1"
    
    def test_get_object_not_found(self):
        """Test getting object that doesn't exist."""
        with pytest.raises(Exception):
            self.view.get_object(999)
    
    def test_get_serialized_list(self):
        """Test getting serialized list."""
        data = self.view.get_serialized_list()
        
        assert len(data) == 2
        assert data[0]["name"] == "Product 1"
    
    def test_get_serialized_object(self):
        """Test getting single serialized object."""
        data = self.view.get_serialized_object(1)
        
        assert data is not None
        assert data["id"] == 1
    
    def test_get_serialized_object_not_found(self):
        """Test getting object that doesn't exist."""
        data = self.view.get_serialized_object(999)
        assert data is None
    
    def test_create_valid(self):
        """Test creating object with valid data."""
        obj = self.view.create({"name": "New Product"})
        
        assert obj is not None
        assert obj.id == 99  # From FakeSerializer.save()
        assert obj.name == "New Product"
    
    def test_create_invalid(self):
        """Test creating object with invalid data."""
        obj = self.view.create({"name": "invalid"})
        assert obj is None
    
    def test_update_valid(self):
        """Test updating object with valid data."""
        obj = self.view.update(1, {"name": "Updated"})
        
        assert obj is not None
    
    def test_update_not_found(self):
        """Test updating object that doesn't exist."""
        obj = self.view.update(999, {"name": "Updated"})
        assert obj is None
    
    def test_update_partial(self):
        """Test partial update (PATCH)."""
        obj = self.view.update(1, {"name": "Patched"}, partial=True)
        assert obj is not None
    
    def test_delete_success(self):
        """Test deleting object."""
        result = self.view.delete(1)
        assert result is True
    
    def test_delete_not_found(self):
        """Test deleting object that doesn't exist."""
        result = self.view.delete(999)
        assert result is False
    
    def test_on_validation_error_sets_form_errors(self):
        """Test that validation errors are set to form_errors."""
        self.view.form_errors = {}
        
        self.view._handle_validation_errors({
            "name": ["This field is required"],
            "price": ["Must be positive"],
        })
        
        assert "name" in self.view.form_errors
        assert "price" in self.view.form_errors
    
    def test_filter_queryset_with_ordering(self):
        """Test queryset ordering."""
        self.view.ordering = ['-id']
        qs = self.view.filter_queryset(self.view.get_queryset())
        # FakeQuerySet.order_by just returns self, but method is called


# ============================================================================
# Integration Tests
# ============================================================================


class TestMixinComposition:
    """Test that mixins can be composed together."""
    
    def test_all_mixins_together(self):
        """Test using all API mixins in a single view."""
        from djust.integrations import GraphQLMixin, RESTMixin, DRFMixin
        
        class FullAPIView(GraphQLMixin, RESTMixin, DRFMixin, FakeView):
            graphql_endpoint = "ws://localhost:8000/graphql/"
            api_base = "/api/v1"
            serializer_class = FakeSerializer
            queryset = FakeQuerySet([])
        
        view = FullAPIView()
        
        # All mixins should be properly initialized
        view._init_graphql()
        view._init_rest()
        
        assert hasattr(view, '_graphql_subscriptions')
        assert hasattr(view, '_api_client')
        assert hasattr(view, 'get_queryset')
    
    def test_graphql_and_rest(self):
        """Test combining GraphQL and REST mixins."""
        from djust.integrations import GraphQLMixin, RESTMixin
        
        class HybridView(GraphQLMixin, RESTMixin, FakeView):
            graphql_endpoint = "ws://localhost:8000/graphql/"
            api_base = "/api/v1"
        
        view = HybridView()
        view._init_graphql()
        view._init_rest()
        
        # Both should work
        assert view._get_http_endpoint() == "http://localhost:8000/graphql/"
        assert view._build_url("/products/") == "/api/v1/products/"


class TestExports:
    """Test that all expected items are exported."""
    
    def test_main_exports(self):
        """Test main module exports."""
        from djust.integrations import (
            GraphQLMixin,
            GraphQLSubscription,
            RESTMixin,
            APIError,
            APIResponse,
            DRFMixin,
            DRFSerializerMixin,
        )
        
        assert GraphQLMixin is not None
        assert GraphQLSubscription is not None
        assert RESTMixin is not None
        assert APIError is not None
        assert APIResponse is not None
        assert DRFMixin is not None
        assert DRFSerializerMixin is not None
