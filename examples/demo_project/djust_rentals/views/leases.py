"""
Lease Management Views

CRUD operations for leases with filtering and expiration tracking.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from datetime import date, timedelta
from ..models import Lease, Property, Tenant


class LeaseListView(BaseViewWithNavbar):
    """
    Lease list view with filtering by status and expiration.

    Features:
    - Filter by status (active, expired, upcoming, terminated)
    - Search by property or tenant
    - Expiration warnings
    - Sort by start date, end date
    """
    template_name = 'rentals/lease_list.html'

    def mount(self, request, **kwargs):
        """Initialize lease list state"""
        # Filter state
        self.search_query = ""
        self.filter_status = "active"  # all, active, expired, upcoming, terminated
        self.sort_by = "end_date"

        # Initialize hero
        self.hero = HeroSection(
            title="Leases",
            subtitle="Manage rental agreements",
            icon="📄"
        )

        # Load leases
        self._refresh_leases()

    def _refresh_leases(self):
        """Refresh lease list based on current filters"""
        leases = Lease.objects.select_related('property', 'tenant__user').all()

        # Apply search
        if self.search_query:
            leases = leases.filter(
                Q(property__name__icontains=self.search_query) |
                Q(property__address__icontains=self.search_query) |
                Q(tenant__user__first_name__icontains=self.search_query) |
                Q(tenant__user__last_name__icontains=self.search_query)
            )

        # Apply status filter
        if self.filter_status != "all":
            leases = leases.filter(status=self.filter_status)

        # Apply sorting
        if self.sort_by == "start_date":
            leases = leases.order_by('-start_date')
        elif self.sort_by == "end_date":
            leases = leases.order_by('end_date')
        elif self.sort_by == "rent":
            leases = leases.order_by('-monthly_rent')

        self.leases = leases

    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search leases with debouncing"""
        self.search_query = query
        self._refresh_leases()

    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter leases by status"""
        self.filter_status = status
        self._refresh_leases()

    def sort_leases(self, sort: str = "end_date", **kwargs):
        """Sort leases"""
        self.sort_by = sort
        self._refresh_leases()

    def get_context_data(self, **kwargs):
        """Add lease list context"""
        context = super().get_context_data(**kwargs)

        # Add expiration warnings
        lease_data = []
        for lease in self.leases:
            days_left = lease.days_until_expiration()
            warning = None
            if days_left is not None:
                if days_left <= 30:
                    warning = "urgent"
                elif days_left <= 60:
                    warning = "soon"

            lease_data.append({
                'lease': lease,
                'days_left': days_left,
                'warning': warning,
            })

        context.update({
            'lease_data': lease_data,
            'total_count': self.leases.count(),
            'search_query': self.search_query,
            'filter_status': self.filter_status,
            'sort_by': self.sort_by,
            'status_choices': Lease.STATUS_CHOICES,
        })

        return context


class LeaseDetailView(BaseViewWithNavbar):
    """
    Lease detail view showing full lease information.

    Features:
    - Lease terms and conditions
    - Property and tenant information
    - Payment history
    - Document access
    """
    template_name = 'rentals/lease_detail.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize lease detail view"""
        self.lease_id = pk

        # Load lease
        try:
            self.lease = Lease.objects.select_related(
                'property', 'tenant__user'
            ).get(pk=pk)
        except Lease.DoesNotExist:
            self.lease = None
            self.error_message = f"Lease with ID {pk} not found"
            return

        # Load payment history
        from ..models import Payment
        self.payments = Payment.objects.filter(lease=self.lease).order_by('-payment_date')

        # Initialize hero
        self.hero = HeroSection(
            title=f"Lease: {self.lease.property.name}",
            subtitle=f"{self.lease.tenant.user.get_full_name()} ({self.lease.start_date} - {self.lease.end_date})",
            icon="📄"
        )

    def get_context_data(self, **kwargs):
        """Add lease detail context"""
        context = super().get_context_data(**kwargs)

        if not self.lease:
            context['error_message'] = self.error_message
            return context

        # Calculate payment statistics
        from django.db.models import Sum, Count
        payment_stats = self.payments.aggregate(
            total_paid=Sum('amount'),
            payment_count=Count('id')
        )

        # Calculate expected vs actual payments
        from datetime import date
        from dateutil.relativedelta import relativedelta

        start_date = self.lease.start_date
        end_date = min(self.lease.end_date, date.today())
        months_elapsed = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
        expected_total = months_elapsed * float(self.lease.monthly_rent)

        balance = expected_total - float(payment_stats['total_paid'] or 0)

        context.update({
            'lease': self.lease,
            'payments': self.payments,
            'total_paid': payment_stats['total_paid'] or 0,
            'payment_count': payment_stats['payment_count'] or 0,
            'expected_total': expected_total,
            'balance': balance,
            'days_left': self.lease.days_until_expiration(),
        })

        return context


class LeaseFormView(BaseViewWithNavbar):
    """
    Lease create/edit form view.

    Features:
    - Create new leases
    - Edit existing leases
    - Auto-populate rent from property
    - Lease term calculation
    """
    template_name = 'rentals/lease_form.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize lease form"""
        self.lease_id = pk
        self.is_edit = pk is not None

        if self.is_edit:
            # Load existing lease
            try:
                self.lease = Lease.objects.select_related(
                    'property', 'tenant__user'
                ).get(pk=pk)
            except Lease.DoesNotExist:
                self.lease = None
                self.error_message = f"Lease with ID {pk} not found"
                return
        else:
            # New lease
            self.lease = None

        # Form state
        self.success_message = ""
        self.error_message = ""
        self.validation_errors = {}

        # Load properties and tenants for dropdowns
        self.properties = Property.objects.all().order_by('name')
        self.tenants = Tenant.objects.select_related('user').all().order_by('user__last_name')

        # Initialize hero
        title = "Edit Lease" if self.is_edit else "Create New Lease"
        self.hero = HeroSection(
            title=title,
            subtitle="Fill in lease details",
            icon="📄"
        )

    def save_lease(self, property_id=None, tenant_id=None, start_date=None,
                  end_date=None, monthly_rent=None, security_deposit=None,
                  rent_due_day=1, late_fee=0, status="active", terms="", **kwargs):
        """Save lease (create or update)"""
        try:
            # Validate
            if not property_id or not tenant_id:
                self.error_message = "Property and tenant are required"
                return

            property_obj = Property.objects.get(pk=property_id)
            tenant_obj = Tenant.objects.get(pk=tenant_id)

            if self.is_edit and self.lease:
                # Update existing lease
                self.lease.property = property_obj
                self.lease.tenant = tenant_obj
                self.lease.start_date = start_date
                self.lease.end_date = end_date
                self.lease.monthly_rent = monthly_rent
                self.lease.security_deposit = security_deposit
                self.lease.rent_due_day = rent_due_day
                self.lease.late_fee = late_fee if late_fee else 0
                self.lease.status = status
                self.lease.terms = terms
                self.lease.save()

                self.success_message = "Lease updated successfully!"
            else:
                # Create new lease
                self.lease = Lease.objects.create(
                    property=property_obj,
                    tenant=tenant_obj,
                    start_date=start_date,
                    end_date=end_date,
                    monthly_rent=monthly_rent,
                    security_deposit=security_deposit,
                    rent_due_day=rent_due_day,
                    late_fee=late_fee if late_fee else 0,
                    status=status,
                    terms=terms
                )

                # Update property status to occupied
                property_obj.status = 'occupied'
                property_obj.save()

                self.success_message = "Lease created successfully!"
                self.is_edit = True
                self.lease_id = self.lease.id

            self.error_message = ""
            self.validation_errors = {}

        except Property.DoesNotExist:
            self.error_message = "Invalid property selected"
        except Tenant.DoesNotExist:
            self.error_message = "Invalid tenant selected"
        except Exception as e:
            self.error_message = f"Error saving lease: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'lease': self.lease,
            'is_edit': self.is_edit,
            'properties': self.properties,
            'tenants': self.tenants,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
            'status_choices': Lease.STATUS_CHOICES,
        })

        return context
