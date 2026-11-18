"""
Property Management Views

CRUD operations for rental properties with search, filter, and real-time updates.
"""

from djust import state
from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce, event_handler
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from ..models import Property, Lease, MaintenanceRequest
from ..components import StatCard, PageHeader, StatusBadge, DataTable


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

    # Search and filter inputs; persist="server" keeps them across the HTTP
    # fallback and a reconnect. The list is queried per render from them.
    search_query = state("", persist="server")
    filter_status = state("all", persist="server")
    filter_type = state("all", persist="server")
    sort_by = state("name", persist="server")

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

        self._properties = properties

    @event_handler()
    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search properties with debouncing"""
        self.search_query = query

    @event_handler()
    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter properties by status"""
        self.filter_status = status

    @event_handler()
    def filter_by_type(self, property_type: str = "all", **kwargs):
        """Filter properties by type"""
        self.filter_type = property_type

    @event_handler()
    def sort_properties(self, sort: str = "name", **kwargs):
        """Sort properties"""
        self.sort_by = sort

    def get_context_data(self, **kwargs):
        """Add property list context"""
        self._refresh_properties()
        context = super().get_context_data(**kwargs)

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Properties",
            subtitle="Manage your rental properties",
            icon="home",
            actions=[{
                "label": "Add Property",
                "url": "/rentals/properties/add/",
                "icon": "plus"
            }]
        )

        # Calculate status counts
        all_properties = Property.objects.all()
        total_count = all_properties.count()
        occupied_count = all_properties.filter(status='occupied').count()
        available_count = all_properties.filter(status='available').count()
        maintenance_count = all_properties.filter(status='maintenance').count()

        # Create StatCard components for status overview (render to HTML)
        stat_cards_html = [
            StatCard(label="Total Properties", value=str(total_count), icon="home", color="primary").render(),
            StatCard(label="Occupied", value=str(occupied_count), icon="key", color="green").render(),
            StatCard(label="Available", value=str(available_count), icon="home", color="blue").render(),
            StatCard(label="Maintenance", value=str(maintenance_count), icon="wrench", color="yellow").render(),
        ]

        # Create DataTable rows from properties
        table_rows = []
        for prop in self._properties:
            status_badge = StatusBadge(status=prop.status)
            table_rows.append({
                "Property": prop.name,
                "Address": prop.address,
                "Type": prop.get_property_type_display(),
                "Beds/Baths": f"{prop.bedrooms} / {prop.bathrooms}",
                "Rent": f"${prop.monthly_rent:,.0f}",
                "Status": status_badge.render(),
                "Actions": f'<a href="/rentals/properties/{prop.pk}/" data-djust-navigate class="text-primary hover:underline text-sm inline-flex items-center gap-1"><i data-lucide="eye" class="w-3 h-3"></i> View</a>'
            })

        # Create DataTable component
        property_table = DataTable(
            headers=["Property", "Address", "Type", "Beds/Baths", "Rent", "Status", "Actions"],
            rows=table_rows,
            empty_message="No properties found. Try adjusting your filters."
        )

        # Add non-model context
        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,
            'property_table': property_table.render(),

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
        self._pk = pk

    def _load(self):
        # Read on every render, not kept from mount(): between events the HTTP
        # fallback restores a saved model as its field dict, not a row.
        pk = self._pk
        self._property_id = pk

        # Load property
        try:
            self._property = Property.objects.get(pk=pk)
        except Property.DoesNotExist:
            self._property = None
            self._error_message = f"Property with ID {pk} not found"
            return

        # Load related data
        self._current_lease = self._property.get_current_lease()
        self._current_tenant = self._property.get_current_tenant()
        self._lease_history = Lease.objects.filter(property=self._property).order_by('-start_date')
        self._maintenance_history = MaintenanceRequest.objects.filter(
            property=self._property
        ).order_by('-created_at')[:10]

    def get_context_data(self, **kwargs):
        """Add property detail context"""
        self._load()
        context = super().get_context_data(**kwargs)

        if not self._property:
            context['error_message'] = self._error_message
            return context

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title=self._property.name,
            subtitle=self._property.address,
            icon="home",
            actions=[
                {
                    "label": "Edit Property",
                    "url": f"/rentals/properties/{self._property.pk}/edit/",
                    "icon": "edit",
                    "variant": "secondary"
                },
                {
                    "label": "Delete",
                    "url": f"/rentals/properties/{self._property.pk}/delete/",
                    "icon": "trash-2",
                    "variant": "destructive"
                }
            ]
        )

        # Create status badge
        status_badge = StatusBadge(status=self._property.status).render()

        # Calculate financial metrics
        from django.db.models import Sum
        total_income = self._lease_history.aggregate(
            total=Sum('monthly_rent')
        )['total'] or 0

        # Parse amenities (comma-separated string to list)
        amenities_list = [a.strip() for a in self._property.amenities.split(',') if a.strip()]

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'status_badge': status_badge,

            # Property data
            'property': self._property,
            'current_lease': self._current_lease,
            'current_tenant': self._current_tenant,
            'lease_history': self._lease_history,
            'maintenance_history': self._maintenance_history,
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

    # The edited property's id: from the URL, or set when a new property is
    # saved. persist="server" keeps it across the HTTP fallback, so a second
    # save updates that property instead of creating another.
    property_id = state(None, persist="server", render=False)
    success_message = state("", render=False)
    error_message = state("", render=False)
    validation_errors = state(default_factory=dict, render=False)

    def mount(self, request, pk=None, **kwargs):
        """Initialize property form"""
        self.property_id = pk
        if pk is not None and self._property() is None:
            self.error_message = f"Property with ID {pk} not found"

    def _property(self):
        """The edited property, or None for a new one."""
        if self.property_id is None:
            return None
        return Property.objects.filter(pk=self.property_id).first()

    @event_handler()
    def save_property(self, **form_data):
        """Save property (create or update)"""
        try:
            prop = self._property()
            if prop is not None:
                # Update existing property
                for field, value in form_data.items():
                    if hasattr(prop, field):
                        setattr(prop, field, value)
                prop.save()
                self.success_message = "Property updated successfully!"
            else:
                # Create new property
                prop = Property.objects.create(**form_data)
                self.success_message = "Property created successfully!"
                self.property_id = prop.id

            self.error_message = ""
            self.validation_errors = {}

        except Exception as e:
            self.error_message = f"Error saving property: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)
        is_edit = self.property_id is not None

        # Create page header
        title = "Edit Property" if is_edit else "Add New Property"
        subtitle = "Update property details" if is_edit else "Fill in property details"
        page_header = PageHeader(
            title=title,
            subtitle=subtitle,
            icon="home"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'property': self._property(),
            'is_edit': is_edit,
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

    # deleted/messages are set by confirm_delete and rendered through
    # get_context_data(); the property and its counts are read per render.
    deleted = state(False, persist="server", render=False)
    success_message = state("", render=False)
    error_message = state("", render=False)

    def mount(self, request, pk=None, **kwargs):
        """Initialize property delete view"""
        self._property_id = pk
        if self._property() is None:
            self.error_message = f"Property with ID {pk} not found"

    def _property(self):
        return Property.objects.filter(pk=self._property_id).first()

    def _has_active_lease(self, prop):
        return Lease.objects.filter(property=prop, status='active').exists()

    @event_handler()
    def confirm_delete(self):
        """Delete the property"""
        prop = self._property()
        if prop is None:
            return
        if self._has_active_lease(prop):
            self.error_message = "Cannot delete property with active lease. Please terminate the lease first."
            return

        try:
            prop.delete()
            self.deleted = True
            self.error_message = ""
            self.success_message = "Property deleted successfully!"
        except Exception as e:
            self.error_message = f"Error deleting property: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add delete confirmation context"""
        context = super().get_context_data(**kwargs)
        prop = self._property()

        context.update({
            'property': prop,
            'has_active_lease': prop is not None and self._has_active_lease(prop),
            'lease_count': Lease.objects.filter(property=prop).count() if prop else 0,
            'maintenance_count': (
                MaintenanceRequest.objects.filter(property=prop).count() if prop else 0
            ),
            'deleted': self.deleted,
            'error_message': self.error_message,
        })

        return context
