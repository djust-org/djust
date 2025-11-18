"""
Tenant Portal Views

Tenant-facing views for lease info, payments, and maintenance requests.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce
from django.utils import timezone
from ..models import Tenant, Lease, Payment, MaintenanceRequest, Property
from ..components import PageHeader


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

    def mount(self, request, **kwargs):
        """Initialize tenant dashboard"""
        # In real app, get tenant from request.user
        # For demo, we'll load first tenant
        try:
            self.tenant = Tenant.objects.select_related('user').first()
        except:
            self.tenant = None
            self.error_message = "No tenant profile found"
            return

        # Load tenant data
        self.current_lease = self.tenant.get_current_lease() if self.tenant else None
        self.current_property = self.tenant.get_current_property() if self.tenant else None

        if self.current_lease:
            # Recent payments
            self.recent_payments = Payment.objects.filter(
                lease=self.current_lease
            ).order_by('-payment_date')[:5]

            # Calculate rent due
            from datetime import date
            today = date.today()
            due_day = self.current_lease.rent_due_day
            if today.day < due_day:
                next_due_date = today.replace(day=due_day)
            else:
                # Next month
                next_month = (today.month % 12) + 1
                next_year = today.year + (1 if next_month == 1 else 0)
                next_due_date = today.replace(month=next_month, year=next_year, day=due_day)

            self.next_due_date = next_due_date
            self.days_until_due = (next_due_date - today).days
        else:
            self.recent_payments = []
            self.next_due_date = None
            self.days_until_due = None

        # Maintenance requests
        self.maintenance_requests = MaintenanceRequest.objects.filter(
            tenant=self.tenant
        ).order_by('-created_at')[:5] if self.tenant else []

    def get_context_data(self, **kwargs):
        """Add tenant dashboard context"""
        context = super().get_context_data(**kwargs)

        if not self.tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header
        title = f"Welcome, {self.tenant.user.get_full_name()}" if self.tenant else "Tenant Portal"
        page_header = PageHeader(
            title=title,
            subtitle="Your rental information and services",
            icon="home"
        )

        # Count maintenance by status
        maintenance_stats = {
            'open': self.maintenance_requests.filter(status='open').count() if self.maintenance_requests else 0,
            'in_progress': self.maintenance_requests.filter(status='in_progress').count() if self.maintenance_requests else 0,
            'completed': self.maintenance_requests.filter(status='completed').count() if self.maintenance_requests else 0,
        }

        context.update({
            'page_header': page_header.render(),
            'tenant': self.tenant,
            'current_lease': self.current_lease,
            'current_property': self.current_property,
            'recent_payments': self.recent_payments,
            'next_due_date': self.next_due_date,
            'days_until_due': self.days_until_due,
            'maintenance_requests': self.maintenance_requests,
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

    def mount(self, request, **kwargs):
        """Initialize tenant maintenance list"""
        # Get tenant from request.user (for demo, use first tenant)
        try:
            self.tenant = Tenant.objects.select_related('user').first()
        except:
            self.tenant = None
            self.error_message = "No tenant profile found"
            return

        # Filter state
        self.filter_status = "all"

        # Load requests
        self._refresh_requests()

    def _refresh_requests(self):
        """Refresh maintenance requests"""
        if not self.tenant:
            self.requests = []
            return

        requests = MaintenanceRequest.objects.filter(
            tenant=self.tenant
        )

        # Apply status filter
        if self.filter_status != "all":
            requests = requests.filter(status=self.filter_status)

        self.requests = requests.order_by('-created_at')

    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter requests by status"""
        self.filter_status = status
        self._refresh_requests()

    def get_context_data(self, **kwargs):
        """Add maintenance list context"""
        context = super().get_context_data(**kwargs)

        if not self.tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header
        page_header = PageHeader(
            title="My Maintenance Requests",
            subtitle="Track your maintenance requests",
            icon="wrench"
        )

        context.update({
            'page_header': page_header.render(),
            'requests': self.requests,
            'total_count': self.requests.count() if self.requests else 0,
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

    def mount(self, request, **kwargs):
        """Initialize maintenance creation form"""
        # Get tenant from request.user (for demo, use first tenant)
        try:
            self.tenant = Tenant.objects.select_related('user').first()
        except:
            self.tenant = None
            self.error_message = "No tenant profile found"
            return

        self.current_property = self.tenant.get_current_property() if self.tenant else None

        # Form state
        self.success_message = ""
        self.error_message = ""

    def submit_request(self, title="", description="", priority="medium", **kwargs):
        """Submit new maintenance request"""
        if not self.tenant or not self.current_property:
            self.error_message = "No active lease found"
            return

        try:
            # Create maintenance request
            request_obj = MaintenanceRequest.objects.create(
                property=self.current_property,
                tenant=self.tenant,
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

        if not self.tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header
        from ..components import PageHeader
        page_header = PageHeader(
            title="Submit Maintenance Request",
            subtitle=f"Report an issue with {self.current_property.name if self.current_property else 'your property'}",
            icon="wrench"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'tenant': self.tenant,
            'current_property': self.current_property,
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

    def mount(self, request, **kwargs):
        """Initialize tenant payments view"""
        # Get tenant from request.user (for demo, use first tenant)
        try:
            self.tenant = Tenant.objects.select_related('user').first()
        except:
            self.tenant = None
            self.error_message = "No tenant profile found"
            return

        # Load payment history
        self.current_lease = self.tenant.get_current_lease() if self.tenant else None

        if self.current_lease:
            self.payments = Payment.objects.filter(
                lease=self.current_lease
            ).order_by('-payment_date')
        else:
            self.payments = []

    def get_context_data(self, **kwargs):
        """Add payments context"""
        context = super().get_context_data(**kwargs)

        if not self.tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header
        page_header = PageHeader(
            title="Payment History",
            subtitle="View your rent payments",
            icon="credit-card"
        )

        # Calculate payment statistics
        from django.db.models import Sum, Count
        if self.payments:
            payment_stats = self.payments.aggregate(
                total_paid=Sum('amount'),
                payment_count=Count('id')
            )
        else:
            payment_stats = {'total_paid': 0, 'payment_count': 0}

        # Calculate expected payments
        if self.current_lease:
            from datetime import date
            from dateutil.relativedelta import relativedelta

            start_date = self.current_lease.start_date
            end_date = min(self.current_lease.end_date, date.today())
            months_elapsed = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
            expected_total = months_elapsed * float(self.current_lease.monthly_rent)
            balance = expected_total - float(payment_stats['total_paid'] or 0)
        else:
            expected_total = 0
            balance = 0

        context.update({
            'page_header': page_header.render(),
            'tenant': self.tenant,
            'current_lease': self.current_lease,
            'payments': self.payments,
            'total_paid': payment_stats['total_paid'] or 0,
            'payment_count': payment_stats['payment_count'] or 0,
            'expected_total': expected_total,
            'balance': balance,
        })

        return context
