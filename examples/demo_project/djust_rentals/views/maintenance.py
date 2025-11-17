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

        # Initialize hero
        self.hero = HeroSection(
            title="Maintenance Requests",
            subtitle="Track and manage property maintenance",
            icon="🔧"
        )

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
            # Custom priority order: urgent, high, medium, low
            priority_order = ['urgent', 'high', 'medium', 'low']
            requests = sorted(requests, key=lambda x: priority_order.index(x.priority))
        elif self.sort_by == "property":
            requests = requests.order_by('property__name')

        self.requests = requests

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
        context = super().get_context_data(**kwargs)

        # Count by status
        status_counts = {
            'open': 0,
            'in_progress': 0,
            'completed': 0,
            'cancelled': 0,
        }
        for req in MaintenanceRequest.objects.all():
            if req.status in status_counts:
                status_counts[req.status] += 1

        # Count by priority
        priority_counts = {
            'urgent': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
        }
        for req in MaintenanceRequest.objects.filter(status__in=['open', 'in_progress']):
            if req.priority in priority_counts:
                priority_counts[req.priority] += 1

        context.update({
            'requests': self.requests,
            'total_count': len(self.requests) if isinstance(self.requests, list) else self.requests.count(),
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            'filter_priority': self.filter_priority,
            'sort_by': self.sort_by,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
            'status_counts': status_counts,
            'priority_counts': priority_counts,
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

        # Initialize hero
        self.hero = HeroSection(
            title=self.maintenance_request.title,
            subtitle=f"{self.maintenance_request.property.name} - {self.maintenance_request.get_priority_display()} Priority",
            icon="🔧"
        )

    def get_context_data(self, **kwargs):
        """Add maintenance detail context"""
        context = super().get_context_data(**kwargs)

        if not self.maintenance_request:
            context['error_message'] = self.error_message
            return context

        # Calculate time metrics
        if self.maintenance_request.completed_at:
            time_to_complete = (
                self.maintenance_request.completed_at - self.maintenance_request.created_at
            ).days
        else:
            time_to_complete = None

        days_open = (timezone.now() - self.maintenance_request.created_at).days

        context.update({
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

        # Initialize hero
        self.hero = HeroSection(
            title=f"Update: {self.maintenance_request.title}",
            subtitle=self.maintenance_request.property.name,
            icon="🔧"
        )

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

        context.update({
            'request': self.maintenance_request,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
        })

        return context
