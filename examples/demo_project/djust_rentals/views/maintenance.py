"""
Maintenance Management Views

Track and manage property maintenance requests.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce, client_state, optimistic
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from django.utils import timezone
from ..models import MaintenanceRequest, Property, Tenant
from ..components import StatCard, PageHeader, StatusBadge, DataTable


class MaintenanceListView(BaseViewWithNavbar):
    """
    Maintenance request list view with filtering and real-time updates.

    Features:
    - Filter by status, priority, property
    - Search by title, description
    - Real-time status updates with @client_state
    - Priority color coding
    """
    template_name = 'rentals/maintenance_list.html'

    def mount(self, request, **kwargs):
        """Initialize maintenance list state"""
        # Filter state
        self.search_query = ""
        self.filter_status = "pending"  # pending (open+in_progress), all, open, in_progress, completed, cancelled
        self.filter_priority = "all"
        self.sort_by = "created"

        # Load requests
        self._refresh_requests()

    def _refresh_requests(self):
        """Refresh maintenance requests based on current filters"""
        requests = MaintenanceRequest.objects.select_related(
            'property', 'tenant__user'
        ).all()

        # Apply search
        if self.search_query:
            requests = requests.filter(
                Q(title__icontains=self.search_query) |
                Q(description__icontains=self.search_query) |
                Q(property__name__icontains=self.search_query)
            )

        # Apply status filter
        if self.filter_status == "pending":
            requests = requests.filter(status__in=['open', 'in_progress'])
        elif self.filter_status != "all":
            requests = requests.filter(status=self.filter_status)

        # Apply priority filter
        if self.filter_priority != "all":
            requests = requests.filter(priority=self.filter_priority)

        # Apply sorting
        if self.sort_by == "created":
            requests = requests.order_by('-created_at')
        elif self.sort_by == "priority":
            # Custom priority order at database level using Case/When
            from django.db.models import Case, When, IntegerField
            requests = requests.annotate(
                priority_order=Case(
                    When(priority='urgent', then=0),
                    When(priority='high', then=1),
                    When(priority='medium', then=2),
                    When(priority='low', then=3),
                    default=4,
                    output_field=IntegerField(),
                )
            ).order_by('priority_order', '-created_at')
        elif self.sort_by == "property":
            requests = requests.order_by('property__name')

        # Store as private variable to avoid auto-JIT serialization
        # We'll assign to self.requests in get_context_data() instead
        self._requests = requests

    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search requests with debouncing"""
        self.search_query = query
        self._refresh_requests()

    @client_state(keys=["requests"])
    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter requests by status (with client state sync)"""
        self.filter_status = status
        self._refresh_requests()

    def filter_by_priority(self, priority: str = "all", **kwargs):
        """Filter requests by priority"""
        self.filter_priority = priority
        self._refresh_requests()

    def sort_requests(self, sort: str = "created", **kwargs):
        """Sort requests"""
        self.sort_by = sort
        self._refresh_requests()

    def get_context_data(self, **kwargs):
        """Add maintenance list context"""
        # Store requests as instance variable for JIT serialization
        self.requests = self._requests

        # Call parent - JIT serializes 'requests' with Rust + auto-generates 'requests_count'
        context = super().get_context_data(**kwargs)

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Maintenance Requests",
            subtitle="Track and manage property maintenance",
            icon="wrench",
            actions=[{
                "label": "Create Request",
                "url": "/rentals/maintenance/add/",
                "icon": "plus"
            }]
        )

        # Count by status
        all_requests = MaintenanceRequest.objects.all()
        total_count = all_requests.count()
        open_count = all_requests.filter(status='open').count()
        in_progress_count = all_requests.filter(status='in_progress').count()
        pending_count = open_count + in_progress_count

        # Count by priority (only pending requests)
        pending_requests = all_requests.filter(status__in=['open', 'in_progress'])
        urgent_count = pending_requests.filter(priority='urgent').count()

        # Create StatCard components for status overview (render to HTML)
        stat_cards_html = [
            StatCard(label="Total Requests", value=str(total_count), icon="wrench", color="primary").render(),
            StatCard(label="Pending", value=str(pending_count), icon="clock", color="yellow").render(),
            StatCard(label="In Progress", value=str(in_progress_count), icon="loader", color="blue").render(),
            StatCard(label="Urgent", value=str(urgent_count), icon="alert-triangle", color="red").render(),
        ]

        # Create DataTable rows from maintenance requests
        table_rows = []
        for request in self._requests:
            # Priority badge
            priority_badge = StatusBadge(status=request.priority, icon=None).render()

            # Status badge
            status_badge = StatusBadge(status=request.status).render()

            table_rows.append({
                "Title": request.title,
                "Property": request.property.name,
                "Priority": priority_badge,
                "Status": status_badge,
                "Created": request.created_at.strftime("%b %d, %Y"),
                "Actions": f'<a href="/rentals/maintenance/{request.pk}/" data-djust-navigate class="text-primary hover:underline text-sm inline-flex items-center gap-1"><i data-lucide="eye" class="w-3 h-3"></i> View</a>'
            })

        # Create DataTable component
        maintenance_table = DataTable(
            headers=["Title", "Property", "Priority", "Status", "Created", "Actions"],
            rows=table_rows,
            empty_message="No maintenance requests found. Try adjusting your filters or create a new request."
        )

        # Add non-model context
        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,
            'maintenance_table': maintenance_table.render(),

            # Filter state
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            'filter_priority': self.filter_priority,
            'sort_by': self.sort_by,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
        })

        return context


class MaintenanceDetailView(BaseViewWithNavbar):
    """
    Maintenance request detail view.

    Features:
    - Full request details
    - Status and priority
    - Property and tenant information
    - Cost tracking
    - Notes and updates
    """
    template_name = 'rentals/maintenance_detail.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize maintenance detail view"""
        self.request_id = pk

        # Load request
        try:
            self.maintenance_request = MaintenanceRequest.objects.select_related(
                'property', 'tenant__user'
            ).get(pk=pk)
        except MaintenanceRequest.DoesNotExist:
            self.maintenance_request = None
            self.error_message = f"Maintenance request with ID {pk} not found"
            return

    def get_context_data(self, **kwargs):
        """Add maintenance detail context"""
        context = super().get_context_data(**kwargs)

        if not self.maintenance_request:
            context['error_message'] = self.error_message
            return context

        # Create page header
        page_header = PageHeader(
            title=self.maintenance_request.title,
            subtitle=f"{self.maintenance_request.property.name} • Created {self.maintenance_request.created_at.strftime('%b %d, %Y')}",
            icon="wrench",
            actions=[
                {
                    "label": "Update Request",
                    "url": f"/rentals/maintenance/{self.maintenance_request.pk}/update/",
                    "icon": "edit",
                    "variant": "secondary"
                }
            ]
        )

        # Create status and priority badges
        status_badge = StatusBadge(status=self.maintenance_request.status).render()
        priority_badge = StatusBadge(status=self.maintenance_request.priority).render()

        # Calculate time metrics
        if self.maintenance_request.completed_at:
            time_to_complete = (
                self.maintenance_request.completed_at - self.maintenance_request.created_at
            ).days
        else:
            time_to_complete = None

        days_open = (timezone.now() - self.maintenance_request.created_at).days

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'status_badge': status_badge,
            'priority_badge': priority_badge,

            # Request data
            'request': self.maintenance_request,
            'time_to_complete': time_to_complete,
            'days_open': days_open,
        })

        return context


class MaintenanceUpdateView(BaseViewWithNavbar):
    """
    Maintenance request update view.

    Features:
    - Update status with @optimistic
    - Change priority
    - Assign contractor
    - Add notes
    - Track costs
    """
    template_name = 'rentals/maintenance_update.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize maintenance update view"""
        self.request_id = pk

        # Load request
        try:
            self.maintenance_request = MaintenanceRequest.objects.select_related(
                'property', 'tenant__user'
            ).get(pk=pk)
        except MaintenanceRequest.DoesNotExist:
            self.maintenance_request = None
            self.error_message = f"Maintenance request with ID {pk} not found"
            return

        # Form state
        self.success_message = ""
        self.error_message = ""

    @client_state(keys=["maintenance_request"])
    @optimistic
    def update_status(self, status: str = "", **kwargs):
        """Update request status"""
        if status and status in dict(MaintenanceRequest.STATUS_CHOICES):
            self.maintenance_request.status = status

            # Set completed timestamp if marking as completed
            if status == 'completed' and not self.maintenance_request.completed_at:
                self.maintenance_request.completed_at = timezone.now()

            self.maintenance_request.save()
            self.success_message = f"Status updated to {self.maintenance_request.get_status_display()}"

    @optimistic
    def update_priority(self, priority: str = "", **kwargs):
        """Update request priority"""
        if priority and priority in dict(MaintenanceRequest.PRIORITY_CHOICES):
            self.maintenance_request.priority = priority
            self.maintenance_request.save()
            self.success_message = f"Priority updated to {self.maintenance_request.get_priority_display()}"

    def update_details(self, assigned_to="", estimated_cost=None, actual_cost=None, notes="", **kwargs):
        """Update request details"""
        try:
            if assigned_to:
                self.maintenance_request.assigned_to = assigned_to

            if estimated_cost:
                self.maintenance_request.estimated_cost = float(estimated_cost)

            if actual_cost:
                self.maintenance_request.actual_cost = float(actual_cost)

            if notes:
                # Append to existing notes
                timestamp = timezone.now().strftime("%Y-%m-%d %H:%M")
                if self.maintenance_request.notes:
                    self.maintenance_request.notes += f"\n\n[{timestamp}] {notes}"
                else:
                    self.maintenance_request.notes = f"[{timestamp}] {notes}"

            self.maintenance_request.save()
            self.success_message = "Details updated successfully"

        except Exception as e:
            self.error_message = f"Error updating details: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add update form context"""
        context = super().get_context_data(**kwargs)

        if not self.maintenance_request:
            context['error_message'] = self.error_message
            return context

        # Create page header
        page_header = PageHeader(
            title=f"Update: {self.maintenance_request.title}",
            subtitle=self.maintenance_request.property.name,
            icon="wrench",
            actions=[
                {
                    "label": "View Details",
                    "url": f"/rentals/maintenance/{self.maintenance_request.pk}/",
                    "icon": "eye",
                    "variant": "secondary"
                }
            ]
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Request data
            'request': self.maintenance_request,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
        })

        return context


class MaintenanceFormView(BaseViewWithNavbar):
    """
    Maintenance request create/edit form view.

    Features:
    - Create new maintenance requests
    - Edit existing requests
    - Real-time form validation
    - Property and tenant selection
    """
    template_name = 'rentals/maintenance_form.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize maintenance form"""
        self.request_id = pk
        self.is_edit = pk is not None

        if self.is_edit:
            # Load existing request
            try:
                self.maintenance_request = MaintenanceRequest.objects.select_related(
                    'property', 'tenant__user'
                ).get(pk=pk)
            except MaintenanceRequest.DoesNotExist:
                self.maintenance_request = None
                self.error_message = f"Maintenance request with ID {pk} not found"
                return
        else:
            # New request - initialize with defaults
            self.maintenance_request = None

        # Form state
        self.success_message = ""
        self.error_message = ""
        self.validation_errors = {}

        # Initialize hero
        title = "Edit Maintenance Request" if self.is_edit else "Create Maintenance Request"
        self.hero = HeroSection(
            title=title,
            subtitle="Fill in request details",
            icon="🔧"
        )

        # Load properties and tenants for dropdowns
        self.properties = Property.objects.all().order_by('name')
        self.tenants = Tenant.objects.select_related('user').all().order_by('user__last_name')

    def save_request(self, property_id=None, tenant_id=None, title="", description="",
                    priority="medium", status="open", assigned_to="",
                    estimated_cost=None, actual_cost=None, notes="", **kwargs):
        """Save maintenance request (create or update)"""
        try:
            if self.is_edit and self.maintenance_request:
                # Update existing request
                if property_id:
                    self.maintenance_request.property = Property.objects.get(pk=property_id)
                if tenant_id:
                    self.maintenance_request.tenant = Tenant.objects.get(pk=tenant_id)

                self.maintenance_request.title = title
                self.maintenance_request.description = description
                self.maintenance_request.priority = priority
                self.maintenance_request.status = status
                self.maintenance_request.assigned_to = assigned_to

                if estimated_cost:
                    self.maintenance_request.estimated_cost = float(estimated_cost)
                if actual_cost:
                    self.maintenance_request.actual_cost = float(actual_cost)
                if notes:
                    self.maintenance_request.notes = notes

                self.maintenance_request.save()
                self.success_message = "Maintenance request updated successfully!"
            else:
                # Create new request
                if not property_id or not title or not description:
                    self.error_message = "Property, title, and description are required"
                    return

                property_obj = Property.objects.get(pk=property_id)
                tenant_obj = Tenant.objects.get(pk=tenant_id) if tenant_id else None

                self.maintenance_request = MaintenanceRequest.objects.create(
                    property=property_obj,
                    tenant=tenant_obj,
                    title=title,
                    description=description,
                    priority=priority,
                    status=status,
                    assigned_to=assigned_to,
                    estimated_cost=float(estimated_cost) if estimated_cost else None,
                    actual_cost=float(actual_cost) if actual_cost else None,
                    notes=notes,
                )

                self.success_message = "Maintenance request created successfully!"
                self.is_edit = True
                self.request_id = self.maintenance_request.id

            self.error_message = ""
            self.validation_errors = {}

        except Property.DoesNotExist:
            self.error_message = "Selected property not found"
        except Tenant.DoesNotExist:
            self.error_message = "Selected tenant not found"
        except Exception as e:
            self.error_message = f"Error saving maintenance request: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'request': self.maintenance_request,
            'is_edit': self.is_edit,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
            'properties': self.properties,
            'tenants': self.tenants,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
        })

        return context


class MaintenanceDeleteView(BaseViewWithNavbar):
    """
    Maintenance request delete confirmation view.

    Features:
    - Confirmation dialog
    - Shows request details
    - Optimistic deletion with @optimistic
    """
    template_name = 'rentals/maintenance_delete.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize maintenance delete view"""
        self.request_id = pk

        try:
            self.maintenance_request = MaintenanceRequest.objects.select_related(
                'property', 'tenant__user'
            ).get(pk=pk)
        except MaintenanceRequest.DoesNotExist:
            self.maintenance_request = None
            self.error_message = f"Maintenance request with ID {pk} not found"
            return

        self.deleted = False
        self.error_message = ""

        # Initialize hero
        self.hero = HeroSection(
            title=f"Delete: {self.maintenance_request.title}",
            subtitle=self.maintenance_request.property.name,
            icon="🔧"
        )

    @optimistic
    def confirm_delete(self):
        """Delete the maintenance request"""
        try:
            self.maintenance_request.delete()
            self.deleted = True
            self.success_message = "Maintenance request deleted successfully!"
        except Exception as e:
            self.error_message = f"Error deleting maintenance request: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add delete confirmation context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'request': self.maintenance_request,
            'deleted': self.deleted,
            'error_message': self.error_message,
        })

        return context
