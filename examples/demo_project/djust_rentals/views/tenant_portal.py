"""
Tenant Portal Views

Tenant-facing views for lease info, payments, and maintenance requests.
"""

from djust import state
from djust_shared.views import BaseViewWithNavbar
from ._rows import RowById
from djust.decorators import event_handler
from djust.decorators import debounce
from django.utils import timezone
from ..models import Tenant, Lease, Payment, MaintenanceRequest, Property
from ..components import PageHeader


def _first_tenant_id():
    """The demo's tenant: the first one (a real app reads request.user)."""
    return Tenant.objects.order_by('pk').values_list('pk', flat=True).first()


def _tenant_row(pk):
    return Tenant.objects.select_related('user').filter(pk=pk).first()


class TenantDashboardView(BaseViewWithNavbar):
    """
    Tenant dashboard showing current lease, payments, and maintenance.

    Features:
    - Current lease information
    - Rent due date and amount
    - Recent payments
    - Maintenance request status
    - Quick actions
    """
    template_name = 'rentals/tenant_dashboard.html'

    def _load(self):
        # Read on every render, not kept from mount(): between events the HTTP
        # fallback restores a saved model as its field dict, not a row.
        # In real app, get tenant from request.user
        # For demo, we'll load first tenant
        # .first() returns None on an empty table rather than raising, so test
        # for it: a bare except here never ran and the page 500'd (#3994).
        self._tenant = Tenant.objects.select_related('user').first()
        self._error_message = ""
        if self._tenant is None:
            self._error_message = "No tenant profile found"
            return

        # Load tenant data
        self._current_lease = self._tenant.get_current_lease() if self._tenant else None
        self._current_property = self._tenant.get_current_property() if self._tenant else None

        if self._current_lease:
            # Recent payments
            self._recent_payments = Payment.objects.filter(
                lease=self._current_lease
            ).order_by('-payment_date')[:5]

            # Calculate rent due
            from datetime import date
            today = date.today()
            due_day = self._current_lease.rent_due_day
            if today.day < due_day:
                next_due_date = today.replace(day=due_day)
            else:
                # Next month
                next_month = (today.month % 12) + 1
                next_year = today.year + (1 if next_month == 1 else 0)
                next_due_date = today.replace(month=next_month, year=next_year, day=due_day)

            self._next_due_date = next_due_date
            self._days_until_due = (next_due_date - today).days
        else:
            self._recent_payments = []
            self._next_due_date = None
            self._days_until_due = None

        # Maintenance requests
        self._maintenance_requests = MaintenanceRequest.objects.filter(
            tenant=self._tenant
        ).order_by('-created_at')[:5] if self._tenant else []

    def get_context_data(self, **kwargs):
        """Add tenant dashboard context"""
        self._load()
        context = super().get_context_data(**kwargs)

        if not self._tenant:
            context['error_message'] = self._error_message
            return context

        # Create page header
        title = f"Welcome, {self._tenant.user.get_full_name()}" if self._tenant else "Tenant Portal"
        page_header = PageHeader(
            title=title,
            subtitle="Your rental information and services",
            icon="home"
        )

        # Count maintenance by status over all of the tenant's requests:
        # self._maintenance_requests is the sliced "recent 5", and a sliced
        # queryset cannot be filtered again (#3994).
        all_requests = MaintenanceRequest.objects.filter(tenant=self._tenant)
        maintenance_stats = {
            'open': all_requests.filter(status='open').count(),
            'in_progress': all_requests.filter(status='in_progress').count(),
            'completed': all_requests.filter(status='completed').count(),
        }

        context.update({
            'page_header': page_header.render(),
            'tenant': self._tenant,
            'current_lease': self._current_lease,
            'current_property': self._current_property,
            'recent_payments': self._recent_payments,
            'next_due_date': self._next_due_date,
            'days_until_due': self._days_until_due,
            'maintenance_requests': self._maintenance_requests,
            'maintenance_stats': maintenance_stats,
        })

        return context


class TenantMaintenanceListView(BaseViewWithNavbar):
    """
    Tenant maintenance request list.

    Features:
    - View own maintenance requests
    - Filter by status
    - Track request progress
    """
    template_name = 'rentals/tenant_maintenance_list.html'

    # persist="server" keeps the filter across the HTTP fallback and a
    # reconnect; the list is queried per render from it.
    filter_status = state("all", persist="server")
    # Read by id on every turn (see ._rows).
    _tenant = RowById("_tenant_id", _tenant_row)

    def mount(self, request, **kwargs):
        """Initialize tenant maintenance list"""
        # Get tenant from request.user (for demo, use first tenant)
        # .first() returns None on an empty table rather than raising, so test
        # for it: a bare except here never ran and the page 500'd (#3994).
        self._tenant_id = _first_tenant_id()
        self._error_message = ""
        if self._tenant is None:
            self._error_message = "No tenant profile found"
            return

    def _refresh_requests(self):
        """Refresh maintenance requests"""
        if not self._tenant:
            self._requests = []
            return

        requests = MaintenanceRequest.objects.filter(
            tenant=self._tenant
        )

        # Apply status filter
        if self.filter_status != "all":
            requests = requests.filter(status=self.filter_status)

        self._requests = requests.order_by('-created_at')

    @event_handler
    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter requests by status"""
        self.filter_status = status

    def get_context_data(self, **kwargs):
        """Add maintenance list context"""
        self._refresh_requests()
        context = super().get_context_data(**kwargs)

        if not self._tenant:
            context['error_message'] = self._error_message
            return context

        # Create page header
        page_header = PageHeader(
            title="My Maintenance Requests",
            subtitle="Track your maintenance requests",
            icon="wrench"
        )

        context.update({
            'page_header': page_header.render(),
            'requests': self._requests,
            'total_count': self._requests.count() if self._requests else 0,
            'filter_status': self.filter_status,
            'status_choices': MaintenanceRequest.STATUS_CHOICES,
        })

        return context


class TenantMaintenanceCreateView(BaseViewWithNavbar):
    """
    Tenant maintenance request creation form.

    Features:
    - Submit new maintenance requests
    - Select priority
    - Add description and photos
    """
    template_name = 'rentals/tenant_maintenance_create.html'

    # Set by submit_request and rendered through get_context_data().
    success_message = state("", render=False)
    error_message = state("", render=False)
    # Read by id on every turn (see ._rows).
    _tenant = RowById("_tenant_id", _tenant_row)
    _current_property = RowById(
        "_tenant_id", lambda pk: getattr(_tenant_row(pk), "get_current_property", lambda: None)()
    )

    def mount(self, request, **kwargs):
        """Initialize maintenance creation form"""
        # Get tenant from request.user (for demo, use first tenant)
        # .first() returns None on an empty table rather than raising, so test
        # for it: a bare except here never ran and the page 500'd (#3994).
        self._tenant_id = _first_tenant_id()
        self.error_message = ""
        if self._tenant is None:
            self.error_message = "No tenant profile found"
            return

    @event_handler
    def submit_request(self, title: str = "", description: str = "", priority: str = "medium",
                       **kwargs):
        """Submit new maintenance request"""
        if not self._tenant or not self._current_property:
            self.error_message = "No active lease found"
            return

        try:
            # Create maintenance request
            request_obj = MaintenanceRequest.objects.create(
                property=self._current_property,
                tenant=self._tenant,
                title=title,
                description=description,
                priority=priority,
                status='open'
            )

            self.success_message = f"Maintenance request #{request_obj.id} submitted successfully!"
            self.error_message = ""

        except Exception as e:
            self.error_message = f"Error submitting request: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)

        if not self._tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header
        from ..components import PageHeader
        page_header = PageHeader(
            title="Submit Maintenance Request",
            subtitle=f"Report an issue with {self._current_property.name if self._current_property else 'your property'}",
            icon="wrench"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'tenant': self._tenant,
            'current_property': self._current_property,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'priority_choices': MaintenanceRequest.PRIORITY_CHOICES,
        })

        return context


class TenantPaymentsView(BaseViewWithNavbar):
    """
    Tenant payment history view.

    Features:
    - View all payments
    - Payment receipts
    - Outstanding balance
    - Payment reminders
    """
    template_name = 'rentals/tenant_payments.html'

    def _load(self):
        # Read on every render, not kept from mount(): between events the HTTP
        # fallback restores a saved model as its field dict, not a row.
        # Get tenant from request.user (for demo, use first tenant)
        # .first() returns None on an empty table rather than raising, so test
        # for it: a bare except here never ran and the page 500'd (#3994).
        self._tenant = Tenant.objects.select_related('user').first()
        self._error_message = ""
        if self._tenant is None:
            self._error_message = "No tenant profile found"
            return

        # Load payment history
        self._current_lease = self._tenant.get_current_lease() if self._tenant else None

        if self._current_lease:
            self._payments = Payment.objects.filter(
                lease=self._current_lease
            ).order_by('-payment_date')
        else:
            self._payments = []

    def get_context_data(self, **kwargs):
        """Add payments context"""
        self._load()
        context = super().get_context_data(**kwargs)

        if not self._tenant:
            context['error_message'] = self._error_message
            return context

        # Create page header
        page_header = PageHeader(
            title="Payment History",
            subtitle="View your rent payments",
            icon="credit-card"
        )

        # Calculate payment statistics
        from django.db.models import Sum, Count
        if self._payments:
            payment_stats = self._payments.aggregate(
                total_paid=Sum('amount'),
                payment_count=Count('id')
            )
        else:
            payment_stats = {'total_paid': 0, 'payment_count': 0}

        # Calculate expected payments
        if self._current_lease:
            from datetime import date

            start_date = self._current_lease.start_date
            end_date = min(self._current_lease.end_date, date.today())
            months_elapsed = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
            expected_total = months_elapsed * float(self._current_lease.monthly_rent)
            balance = expected_total - float(payment_stats['total_paid'] or 0)
        else:
            expected_total = 0
            balance = 0

        context.update({
            'page_header': page_header.render(),
            'tenant': self._tenant,
            'current_lease': self._current_lease,
            'payments': self._payments,
            'total_paid': payment_stats['total_paid'] or 0,
            'payment_count': payment_stats['payment_count'] or 0,
            'expected_total': expected_total,
            'balance': balance,
        })

        return context
