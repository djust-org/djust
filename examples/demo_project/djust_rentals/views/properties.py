"""
Property Management Views

CRUD operations for rental properties with search, filter, and real-time updates.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce, optimistic
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from ..models import Property, Lease, MaintenanceRequest


class PropertyListView(BaseViewWithNavbar):
    """
    Property list view with search and filtering capabilities.

    Features:
    - Search by name, address, city
    - Filter by status, property type
    - Real-time search with @debounce
    - Sort by various fields
    """
    template_name = 'rentals/property_list.html'

    def mount(self, request, **kwargs):
        """Initialize property list state"""
        # Search and filter state
        self.search_query = ""
        self.filter_status = "all"
        self.filter_type = "all"
        self.sort_by = "name"

        # Initialize hero
        self.hero = HeroSection(
            title="Properties",
            subtitle="Manage your rental properties",
            icon="🏘️"
        )

        # Load properties
        self._refresh_properties()

    def _refresh_properties(self):
        """Refresh property list based on current filters"""
        properties = Property.objects.all()

        # Apply search
        if self.search_query:
            properties = properties.filter(
                Q(name__icontains=self.search_query) |
                Q(address__icontains=self.search_query) |
                Q(city__icontains=self.search_query)
            )

        # Apply status filter
        if self.filter_status != "all":
            properties = properties.filter(status=self.filter_status)

        # Apply type filter
        if self.filter_type != "all":
            properties = properties.filter(property_type=self.filter_type)

        # Apply sorting
        if self.sort_by == "name":
            properties = properties.order_by('name')
        elif self.sort_by == "rent_low":
            properties = properties.order_by('monthly_rent')
        elif self.sort_by == "rent_high":
            properties = properties.order_by('-monthly_rent')
        elif self.sort_by == "bedrooms":
            properties = properties.order_by('-bedrooms')

        self.properties = properties

    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search properties with debouncing"""
        self.search_query = query
        self._refresh_properties()

    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter properties by status"""
        self.filter_status = status
        self._refresh_properties()

    def filter_by_type(self, property_type: str = "all", **kwargs):
        """Filter properties by type"""
        self.filter_type = property_type
        self._refresh_properties()

    def sort_properties(self, sort: str = "name", **kwargs):
        """Sort properties"""
        self.sort_by = sort
        self._refresh_properties()

    def get_context_data(self, **kwargs):
        """Add property list context"""
        context = super().get_context_data(**kwargs)

        # Calculate status counts
        all_properties = Property.objects.all()
        occupied_count = all_properties.filter(status='occupied').count()
        available_count = all_properties.filter(status='available').count()
        maintenance_count = all_properties.filter(status='maintenance').count()

        context.update({
            'properties': self.properties,
            'total_count': self.properties.count(),
            'occupied_count': occupied_count,
            'available_count': available_count,
            'maintenance_count': maintenance_count,
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            'filter_type': self.filter_type,
            'sort_by': self.sort_by,
            # Property type choices for filter dropdown
            'property_types': Property.PROPERTY_TYPES,
            'status_choices': Property.STATUS_CHOICES,
        })

        return context


class PropertyDetailView(BaseViewWithNavbar):
    """
    Property detail view showing comprehensive property information.

    Features:
    - Property details and amenities
    - Current lease and tenant
    - Lease history
    - Maintenance history
    - Financial summary
    """
    template_name = 'rentals/property_detail.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize property detail view"""
        self.property_id = pk

        # Load property
        try:
            self.property = Property.objects.get(pk=pk)
        except Property.DoesNotExist:
            self.property = None
            self.error_message = f"Property with ID {pk} not found"
            return

        # Load related data
        self.current_lease = self.property.get_current_lease()
        self.current_tenant = self.property.get_current_tenant()
        self.lease_history = Lease.objects.filter(property=self.property).order_by('-start_date')
        self.maintenance_history = MaintenanceRequest.objects.filter(
            property=self.property
        ).order_by('-created_at')[:10]

        # Initialize hero
        self.hero = HeroSection(
            title=self.property.name,
            subtitle=self.property.address,
            icon="🏠"
        )

    def get_context_data(self, **kwargs):
        """Add property detail context"""
        context = super().get_context_data(**kwargs)

        if not self.property:
            context['error_message'] = self.error_message
            return context

        # Calculate financial metrics
        from django.db.models import Sum
        total_income = self.lease_history.aggregate(
            total=Sum('monthly_rent')
        )['total'] or 0

        # Parse amenities (comma-separated string to list)
        amenities_list = [a.strip() for a in self.property.amenities.split(',') if a.strip()]

        context.update({
            'property': self.property,
            'current_lease': self.current_lease,
            'current_tenant': self.current_tenant,
            'lease_history': self.lease_history,
            'maintenance_history': self.maintenance_history,
            'total_income': total_income,
            'amenities': amenities_list,
        })

        return context


class PropertyFormView(BaseViewWithNavbar):
    """
    Property create/edit form view.

    Features:
    - Create new properties
    - Edit existing properties
    - Real-time form validation
    - Image upload support
    """
    template_name = 'rentals/property_form.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize property form"""
        self.property_id = pk
        self.is_edit = pk is not None

        if self.is_edit:
            # Load existing property
            try:
                self.property = Property.objects.get(pk=pk)
            except Property.DoesNotExist:
                self.property = None
                self.error_message = f"Property with ID {pk} not found"
                return
        else:
            # New property - initialize with defaults
            self.property = None

        # Form state
        self.success_message = ""
        self.error_message = ""
        self.validation_errors = {}

        # Initialize hero
        title = "Edit Property" if self.is_edit else "Add New Property"
        self.hero = HeroSection(
            title=title,
            subtitle="Fill in property details",
            icon="🏠"
        )

    def save_property(self, **form_data):
        """Save property (create or update)"""
        try:
            if self.is_edit and self.property:
                # Update existing property
                for field, value in form_data.items():
                    if hasattr(self.property, field):
                        setattr(self.property, field, value)
                self.property.save()
                self.success_message = "Property updated successfully!"
            else:
                # Create new property
                self.property = Property.objects.create(**form_data)
                self.success_message = "Property created successfully!"
                self.is_edit = True
                self.property_id = self.property.id

            self.error_message = ""
            self.validation_errors = {}

        except Exception as e:
            self.error_message = f"Error saving property: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'property': self.property,
            'is_edit': self.is_edit,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
            'property_types': Property.PROPERTY_TYPES,
            'status_choices': Property.STATUS_CHOICES,
        })

        return context


class PropertyDeleteView(BaseViewWithNavbar):
    """
    Property delete confirmation view.

    Features:
    - Confirmation dialog
    - Shows related data that will be affected
    - Optimistic deletion with @optimistic
    """
    template_name = 'rentals/property_delete.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize property delete view"""
        self.property_id = pk

        try:
            self.property = Property.objects.get(pk=pk)
        except Property.DoesNotExist:
            self.property = None
            self.error_message = f"Property with ID {pk} not found"
            return

        # Check for related data
        self.has_active_lease = Lease.objects.filter(
            property=self.property,
            status='active'
        ).exists()

        self.lease_count = Lease.objects.filter(property=self.property).count()
        self.maintenance_count = MaintenanceRequest.objects.filter(property=self.property).count()

        self.deleted = False
        self.error_message = ""

    @optimistic
    def confirm_delete(self):
        """Delete the property"""
        if self.has_active_lease:
            self.error_message = "Cannot delete property with active lease. Please terminate the lease first."
            return

        try:
            self.property.delete()
            self.deleted = True
            self.success_message = "Property deleted successfully!"
        except Exception as e:
            self.error_message = f"Error deleting property: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add delete confirmation context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'property': self.property,
            'has_active_lease': self.has_active_lease,
            'lease_count': self.lease_count,
            'maintenance_count': self.maintenance_count,
            'deleted': self.deleted,
            'error_message': self.error_message,
        })

        return context
