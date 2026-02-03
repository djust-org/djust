"""
Django REST Framework integration for djust LiveView.

Provides mixins for using DRF serializers and ViewSet patterns within LiveViews,
enabling seamless integration between reactive views and REST APIs.

Example usage:

    from djust import LiveView
    from djust.integrations import DRFMixin
    from myapp.serializers import ProductSerializer
    from myapp.models import Product

    class ProductView(DRFMixin, LiveView):
        template_name = "products.html"
        serializer_class = ProductSerializer
        queryset = Product.objects.all()
        
        async def mount(self, request, **kwargs):
            self.products = self.get_serialized_list()
        
        @event_handler
        async def create_product(self, **data):
            product = self.create(data)
            if product:
                self.products = self.get_serialized_list()
        
        @event_handler
        async def update_product(self, pk: int, **data):
            product = self.update(pk, data)
            if product:
                self.products = self.get_serialized_list()

Using DRF ViewSets:

    from rest_framework.viewsets import ModelViewSet
    from djust.integrations import DRFMixin

    class ProductViewSet(ModelViewSet):
        serializer_class = ProductSerializer
        queryset = Product.objects.all()

    class ProductView(DRFMixin, LiveView):
        viewset_class = ProductViewSet
        
        async def mount(self, request, **kwargs):
            self.products = await self.viewset_list()
        
        @event_handler
        async def create_product(self, **data):
            product = await self.viewset_create(data)
            if product:
                self.products.append(product)

Serializer-only Mixin:

    from djust.integrations import DRFSerializerMixin

    class OrderView(DRFSerializerMixin, LiveView):
        template_name = "orders.html"
        
        def get_serializer_class(self):
            return OrderSerializer
        
        async def mount(self, request, **kwargs):
            orders = Order.objects.filter(user=request.user)
            self.orders = self.serialize_many(orders)
        
        @event_handler
        async def validate_order(self, **data):
            errors = self.validate(data)
            if errors:
                self.form_errors = errors
            else:
                self.form_errors = {}
"""

import logging
from typing import Any, Dict, List, Optional, Type, Union

logger = logging.getLogger(__name__)


# ============================================================================
# Serializer Mixin (DRF not required)
# ============================================================================


class DRFSerializerMixin:
    """
    Mixin for using DRF serializers in LiveViews without full DRF integration.
    
    Class Attributes:
        serializer_class: DRF serializer class to use
    
    This mixin provides methods for serialization, validation, and data
    transformation using DRF serializers.
    """
    
    serializer_class: Optional[Type] = None
    
    def get_serializer_class(self) -> Type:
        """
        Get the serializer class.
        
        Override to return different serializers based on context.
        
        Returns:
            Serializer class
        
        Raises:
            ValueError: If no serializer class is configured
        """
        if self.serializer_class is None:
            raise ValueError(
                f"{self.__class__.__name__} must define serializer_class "
                "or override get_serializer_class()"
            )
        return self.serializer_class
    
    def get_serializer(
        self,
        instance: Any = None,
        data: Optional[Dict] = None,
        many: bool = False,
        **kwargs,
    ) -> Any:
        """
        Create a serializer instance.
        
        Args:
            instance: Object instance to serialize
            data: Data to deserialize/validate
            many: If True, instance should be a queryset/list
            **kwargs: Additional serializer arguments
        
        Returns:
            Serializer instance
        """
        serializer_class = self.get_serializer_class()
        
        # Add request context if available
        context = kwargs.pop('context', {})
        if hasattr(self, 'request'):
            context.setdefault('request', self.request)
        context.setdefault('view', self)
        
        return serializer_class(
            instance=instance,
            data=data,
            many=many,
            context=context,
            **kwargs,
        )
    
    def serialize(self, instance: Any, **kwargs) -> Dict:
        """
        Serialize a single object.
        
        Args:
            instance: Object to serialize
            **kwargs: Additional serializer arguments
        
        Returns:
            Serialized data dict
        """
        serializer = self.get_serializer(instance=instance, **kwargs)
        return serializer.data
    
    def serialize_many(self, queryset: Any, **kwargs) -> List[Dict]:
        """
        Serialize multiple objects.
        
        Args:
            queryset: Queryset or list of objects
            **kwargs: Additional serializer arguments
        
        Returns:
            List of serialized data dicts
        """
        serializer = self.get_serializer(instance=queryset, many=True, **kwargs)
        return serializer.data
    
    def validate(
        self,
        data: Dict,
        instance: Any = None,
        raise_exception: bool = False,
    ) -> Dict[str, List[str]]:
        """
        Validate data using the serializer.
        
        Args:
            data: Data to validate
            instance: Existing instance (for updates)
            raise_exception: If True, raise ValidationError on failure
        
        Returns:
            Dict of field errors (empty if valid)
        """
        serializer = self.get_serializer(instance=instance, data=data)
        
        if serializer.is_valid(raise_exception=raise_exception):
            return {}
        
        # Convert DRF errors to simple dict
        errors = {}
        for field, field_errors in serializer.errors.items():
            if isinstance(field_errors, list):
                errors[field] = [str(e) for e in field_errors]
            else:
                errors[field] = [str(field_errors)]
        
        return errors
    
    def get_validated_data(
        self,
        data: Dict,
        instance: Any = None,
    ) -> Optional[Dict]:
        """
        Validate data and return validated_data if valid.
        
        Args:
            data: Data to validate
            instance: Existing instance (for updates)
        
        Returns:
            validated_data dict if valid, None if invalid
        """
        serializer = self.get_serializer(instance=instance, data=data)
        
        if serializer.is_valid():
            return serializer.validated_data
        
        return None


# ============================================================================
# Full DRF Mixin
# ============================================================================


class DRFMixin(DRFSerializerMixin):
    """
    Full DRF integration mixin for LiveViews.
    
    Provides ViewSet-like functionality for CRUD operations.
    
    Class Attributes:
        serializer_class: DRF serializer class
        queryset: Django queryset for the model
        viewset_class: Optional DRF ViewSet class (for delegation)
        lookup_field: Field used for object lookup (default: 'pk')
        filter_backends: List of filter backends to apply
        ordering: Default ordering for querysets
    """
    
    queryset: Optional[Any] = None
    viewset_class: Optional[Type] = None
    lookup_field: str = 'pk'
    filter_backends: List[Type] = []
    ordering: Optional[List[str]] = None
    
    # Internal state
    _viewset_instance: Any = None
    
    def get_queryset(self) -> Any:
        """
        Get the queryset for this view.
        
        Override to customize queryset (e.g., filter by user).
        
        Returns:
            Django queryset
        """
        if self.queryset is not None:
            return self.queryset.all()  # Return a fresh queryset
        
        if self.viewset_class is not None:
            vs = self._get_viewset()
            return vs.get_queryset()
        
        raise ValueError(
            f"{self.__class__.__name__} must define queryset "
            "or override get_queryset()"
        )
    
    def _get_viewset(self) -> Any:
        """Get or create a ViewSet instance."""
        if self._viewset_instance is None and self.viewset_class:
            self._viewset_instance = self.viewset_class()
            # Set request context if available
            if hasattr(self, 'request'):
                self._viewset_instance.request = self.request
        return self._viewset_instance
    
    def filter_queryset(self, queryset: Any) -> Any:
        """
        Apply filters to the queryset.
        
        Args:
            queryset: Base queryset
        
        Returns:
            Filtered queryset
        """
        for backend_class in self.filter_backends:
            queryset = backend_class().filter_queryset(
                self.request if hasattr(self, 'request') else None,
                queryset,
                self,
            )
        
        # Apply ordering
        if self.ordering:
            queryset = queryset.order_by(*self.ordering)
        
        return queryset
    
    def get_object(self, pk: Any) -> Any:
        """
        Get a single object by primary key.
        
        Args:
            pk: Primary key value
        
        Returns:
            Model instance
        
        Raises:
            ObjectDoesNotExist: If object not found
        """
        queryset = self.get_queryset()
        filter_kwargs = {self.lookup_field: pk}
        return queryset.get(**filter_kwargs)
    
    # ========================================================================
    # CRUD Operations
    # ========================================================================
    
    def get_serialized_list(
        self,
        queryset: Optional[Any] = None,
        **kwargs,
    ) -> List[Dict]:
        """
        Get all objects as serialized list.
        
        Args:
            queryset: Optional queryset (defaults to get_queryset)
            **kwargs: Additional serializer arguments
        
        Returns:
            List of serialized objects
        """
        if queryset is None:
            queryset = self.get_queryset()
        
        queryset = self.filter_queryset(queryset)
        return self.serialize_many(queryset, **kwargs)
    
    def get_serialized_object(self, pk: Any, **kwargs) -> Optional[Dict]:
        """
        Get a single object as serialized dict.
        
        Args:
            pk: Primary key
            **kwargs: Additional serializer arguments
        
        Returns:
            Serialized object or None if not found
        """
        try:
            obj = self.get_object(pk)
            return self.serialize(obj, **kwargs)
        except Exception as e:
            logger.warning(f"[DRF] Object not found: {e}")
            return None
    
    def create(
        self,
        data: Dict,
        **kwargs,
    ) -> Optional[Any]:
        """
        Create a new object.
        
        Args:
            data: Object data
            **kwargs: Additional serializer arguments
        
        Returns:
            Created object or None if validation failed
        """
        serializer = self.get_serializer(data=data, **kwargs)
        
        if not serializer.is_valid():
            logger.warning(f"[DRF] Create validation failed: {serializer.errors}")
            self._handle_validation_errors(serializer.errors)
            return None
        
        try:
            obj = serializer.save()
            logger.info(f"[DRF] Created object: {obj}")
            return obj
        except Exception as e:
            logger.error(f"[DRF] Create failed: {e}")
            return None
    
    def update(
        self,
        pk: Any,
        data: Dict,
        partial: bool = False,
        **kwargs,
    ) -> Optional[Any]:
        """
        Update an existing object.
        
        Args:
            pk: Primary key of object to update
            data: Updated data
            partial: If True, allow partial updates (PATCH semantics)
            **kwargs: Additional serializer arguments
        
        Returns:
            Updated object or None if validation failed or not found
        """
        try:
            instance = self.get_object(pk)
        except Exception as e:
            logger.warning(f"[DRF] Object not found for update: {e}")
            return None
        
        serializer = self.get_serializer(
            instance=instance,
            data=data,
            partial=partial,
            **kwargs,
        )
        
        if not serializer.is_valid():
            logger.warning(f"[DRF] Update validation failed: {serializer.errors}")
            self._handle_validation_errors(serializer.errors)
            return None
        
        try:
            obj = serializer.save()
            logger.info(f"[DRF] Updated object: {obj}")
            return obj
        except Exception as e:
            logger.error(f"[DRF] Update failed: {e}")
            return None
    
    def delete(self, pk: Any) -> bool:
        """
        Delete an object.
        
        Args:
            pk: Primary key of object to delete
        
        Returns:
            True if deleted, False if not found
        """
        try:
            instance = self.get_object(pk)
            instance.delete()
            logger.info(f"[DRF] Deleted object with pk={pk}")
            return True
        except Exception as e:
            logger.warning(f"[DRF] Delete failed: {e}")
            return False
    
    # ========================================================================
    # ViewSet Delegation
    # ========================================================================
    
    async def viewset_list(self, **kwargs) -> List[Dict]:
        """
        Get list via ViewSet.
        
        Useful when the ViewSet has complex filtering/permissions.
        """
        if not self.viewset_class:
            raise ValueError("viewset_class must be set to use viewset_* methods")
        
        vs = self._get_viewset()
        vs.action = 'list'
        vs.kwargs = kwargs
        
        queryset = vs.filter_queryset(vs.get_queryset())
        serializer = vs.get_serializer(queryset, many=True)
        return serializer.data
    
    async def viewset_retrieve(self, pk: Any) -> Optional[Dict]:
        """Get single object via ViewSet."""
        if not self.viewset_class:
            raise ValueError("viewset_class must be set to use viewset_* methods")
        
        vs = self._get_viewset()
        vs.action = 'retrieve'
        vs.kwargs = {self.lookup_field: pk}
        
        try:
            instance = vs.get_object()
            serializer = vs.get_serializer(instance)
            return serializer.data
        except Exception:
            return None
    
    async def viewset_create(self, data: Dict) -> Optional[Dict]:
        """Create via ViewSet."""
        if not self.viewset_class:
            raise ValueError("viewset_class must be set to use viewset_* methods")
        
        vs = self._get_viewset()
        vs.action = 'create'
        
        serializer = vs.get_serializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return serializer.data
        
        self._handle_validation_errors(serializer.errors)
        return None
    
    async def viewset_update(
        self,
        pk: Any,
        data: Dict,
        partial: bool = False,
    ) -> Optional[Dict]:
        """Update via ViewSet."""
        if not self.viewset_class:
            raise ValueError("viewset_class must be set to use viewset_* methods")
        
        vs = self._get_viewset()
        vs.action = 'partial_update' if partial else 'update'
        vs.kwargs = {self.lookup_field: pk}
        
        try:
            instance = vs.get_object()
            serializer = vs.get_serializer(instance, data=data, partial=partial)
            if serializer.is_valid():
                serializer.save()
                return serializer.data
            
            self._handle_validation_errors(serializer.errors)
            return None
        except Exception:
            return None
    
    async def viewset_destroy(self, pk: Any) -> bool:
        """Delete via ViewSet."""
        if not self.viewset_class:
            raise ValueError("viewset_class must be set to use viewset_* methods")
        
        vs = self._get_viewset()
        vs.action = 'destroy'
        vs.kwargs = {self.lookup_field: pk}
        
        try:
            instance = vs.get_object()
            instance.delete()
            return True
        except Exception:
            return False
    
    # ========================================================================
    # Validation Error Handling
    # ========================================================================
    
    def _handle_validation_errors(self, errors: Dict) -> None:
        """
        Handle validation errors.
        
        Override to customize error handling (e.g., set form errors).
        
        Args:
            errors: DRF validation errors dict
        """
        # Convert to simple format and call hook
        simple_errors = {}
        for field, field_errors in errors.items():
            if isinstance(field_errors, list):
                simple_errors[field] = [str(e) for e in field_errors]
            else:
                simple_errors[field] = [str(field_errors)]
        
        self.on_validation_error(simple_errors)
    
    def on_validation_error(self, errors: Dict[str, List[str]]) -> None:
        """
        Called when validation fails.
        
        Override to handle validation errors.
        
        Args:
            errors: Dict mapping field names to error messages
        """
        logger.debug(f"[DRF] Validation errors: {errors}")
        
        # Set form_errors if the view has it
        if hasattr(self, 'form_errors'):
            self.form_errors = errors
