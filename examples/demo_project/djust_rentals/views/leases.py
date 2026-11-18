"""
Lease Management Views

CRUD operations for leases with filtering and expiration tracking.
"""

from typing import Any

from djust import state
from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce, event_handler
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from datetime import date, timedelta
from ..models import Lease, Property, Tenant
from ..components import StatCard, PageHeader, StatusBadge, DataTable


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

    # Search and filter inputs; persist="server" keeps them across the HTTP
    # fallback and a reconnect. The list is queried per render from them.
    search_query = state("", persist="server")
    filter_status = state("active", persist="server")  # all, active, expired, upcoming, terminated
    sort_by = state("end_date", persist="server")

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

        self._leases = leases

    @event_handler()
    @debounce(wait=0.5)
    def search(self, value: str = "", **kwargs):
        """Search leases with debouncing - value parameter matches what @input sends"""
        self.search_query = value

    @event_handler()
    def filter_by_status(self, value: str = "all", **kwargs):
        """Filter leases by status - value parameter matches what @change sends"""
        self.filter_status = value

    @event_handler()
    def sort_leases(self, value: str = "end_date", **kwargs):
        """Sort leases - value parameter matches what @change sends"""
        self.sort_by = value

    def get_context_data(self, **kwargs):
        """Add lease list context"""
        self._refresh_leases()
        context = super().get_context_data(**kwargs)

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Leases",
            subtitle="Manage rental agreements and lease terms",
            icon="file-text",
            actions=[{
                "label": "Create Lease",
                "url": "/rentals/leases/add/",
                "icon": "plus"
            }]
        )

        # Calculate status counts
        all_leases = Lease.objects.all()
        total_count = all_leases.count()
        active_count = all_leases.filter(status='active').count()
        expired_count = all_leases.filter(status='expired').count()

        # Count expiring soon (next 60 days)
        sixty_days_from_now = date.today() + timedelta(days=60)
        expiring_soon_count = all_leases.filter(
            status='active',
            end_date__lte=sixty_days_from_now
        ).count()

        # Create StatCard components for status overview (render to HTML)
        stat_cards_html = [
            StatCard(label="Total Leases", value=str(total_count), icon="file-text", color="primary").render(),
            StatCard(label="Active", value=str(active_count), icon="check-circle", color="green").render(),
            StatCard(label="Expired", value=str(expired_count), icon="x-circle", color="red").render(),
            StatCard(label="Expiring Soon", value=str(expiring_soon_count), icon="alert-circle", color="yellow").render(),
        ]

        # Create DataTable rows from leases
        table_rows = []
        today = date.today()
        for lease in self._leases:
            # Calculate days left
            days_left = (lease.end_date - today).days if lease.end_date >= today else 0

            # Determine status badge
            status_badge = StatusBadge(status=lease.status).render()

            # Determine expiration warning
            expiration_html = f"{lease.end_date}"
            if lease.status == 'active' and days_left <= 60:
                if days_left <= 30:
                    expiration_html = f'<span class="text-red-600 dark:text-red-400 font-medium">{lease.end_date} ({days_left} days)</span>'
                else:
                    expiration_html = f'<span class="text-yellow-600 dark:text-yellow-400 font-medium">{lease.end_date} ({days_left} days)</span>'

            table_rows.append({
                "Property": lease.property.name,
                "Tenant": lease.tenant.user.get_full_name(),
                "Start Date": str(lease.start_date),
                "End Date": expiration_html,
                "Monthly Rent": f'<span class="text-green-600 dark:text-green-400 font-semibold">${lease.monthly_rent:,.0f}</span>',
                "Status": status_badge,
                "Actions": f'<a href="/rentals/leases/{lease.pk}/" data-djust-navigate class="text-primary hover:underline text-sm inline-flex items-center gap-1"><i data-lucide="eye" class="w-3 h-3"></i> View</a>'
            })

        # Create DataTable component
        lease_table = DataTable(
            headers=["Property", "Tenant", "Start Date", "End Date", "Monthly Rent", "Status", "Actions"],
            rows=table_rows,
            empty_message="No leases found. Try adjusting your filters or create a new lease."
        )

        # Add non-model context
        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,
            'lease_table': lease_table.render(),

            # Filter state
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
        self._pk = pk

    def _load(self):
        # Read on every render, not kept from mount(): between events the HTTP
        # fallback restores a saved model as its field dict, not a row.
        pk = self._pk
        self._lease_id = pk

        # Load lease
        try:
            self._lease = Lease.objects.select_related(
                'property', 'tenant__user'
            ).get(pk=pk)
        except Lease.DoesNotExist:
            self._lease = None
            self._error_message = f"Lease with ID {pk} not found"
            return

        # Load payment history
        from ..models import Payment
        self._payments = Payment.objects.filter(lease=self._lease).order_by('-payment_date')

    def get_context_data(self, **kwargs):
        """Add lease detail context"""
        self._load()
        context = super().get_context_data(**kwargs)

        if not self._lease:
            context['error_message'] = self._error_message
            return context

        # Create page header
        page_header = PageHeader(
            title=f"Lease: {self._lease.property.name}",
            subtitle=f"{self._lease.tenant.user.get_full_name()} • {self._lease.start_date} - {self._lease.end_date}",
            icon="file-text",
            actions=[
                {
                    "label": "Edit Lease",
                    "url": f"/rentals/leases/{self._lease.pk}/edit/",
                    "icon": "edit",
                    "variant": "secondary"
                }
            ]
        )

        # Create status badge
        status_badge = StatusBadge(status=self._lease.status).render()

        # Calculate payment statistics
        from django.db.models import Sum, Count
        payment_stats = self._payments.aggregate(
            total_paid=Sum('amount'),
            payment_count=Count('id')
        )

        # Calculate expected vs actual payments
        from datetime import date

        start_date = self._lease.start_date
        end_date = min(self._lease.end_date, date.today())
        months_elapsed = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1
        expected_total = months_elapsed * float(self._lease.monthly_rent)

        balance = expected_total - float(payment_stats['total_paid'] or 0)

        context.update({
            # Components
            'page_header': page_header.render(),
            'status_badge': status_badge,

            # Lease data
            'lease': self._lease,
            'payments': self._payments,
            'total_paid': payment_stats['total_paid'] or 0,
            'payment_count': payment_stats['payment_count'] or 0,
            'expected_total': expected_total,
            'balance': balance,
            'days_left': self._lease.days_until_expiration(),
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

    # The edited lease's id: from the URL, or set when a new lease is saved.
    # persist="server" keeps it across the HTTP fallback, so a second save
    # updates that lease instead of creating another.
    lease_id = state(None, persist="server", render=False)
    success_message = state("", render=False)
    error_message = state("", render=False)
    validation_errors = state(default_factory=dict, render=False)

    def mount(self, request, pk=None, **kwargs):
        """Initialize lease form"""
        self.lease_id = pk
        if pk is not None and self._lease() is None:
            self.error_message = f"Lease with ID {pk} not found"

    def _lease(self):
        """The edited lease, or None for a new one."""
        if self.lease_id is None:
            return None
        return Lease.objects.select_related('property', 'tenant__user').filter(
            pk=self.lease_id
        ).first()

    @event_handler
    def save_lease(self, property_id: Any = None, tenant_id: Any = None, start_date: Any = None,
                  end_date: Any = None, monthly_rent: Any = None, security_deposit: Any = None,
                  rent_due_day: Any = 1, late_fee: Any = 0, status: str = "active",
                  terms: str = "", **kwargs):
        """Save lease (create or update)"""
        try:
            # Validate
            if not property_id or not tenant_id:
                self.error_message = "Property and tenant are required"
                return

            property_obj = Property.objects.get(pk=property_id)
            tenant_obj = Tenant.objects.get(pk=tenant_id)

            lease = self._lease()
            if lease is not None:
                # Update existing lease
                lease.property = property_obj
                lease.tenant = tenant_obj
                lease.start_date = start_date
                lease.end_date = end_date
                lease.monthly_rent = monthly_rent
                lease.security_deposit = security_deposit
                lease.rent_due_day = rent_due_day
                lease.late_fee = late_fee if late_fee else 0
                lease.status = status
                lease.terms = terms
                lease.save()

                self.success_message = "Lease updated successfully!"
            else:
                # Create new lease
                lease = Lease.objects.create(
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
                self.lease_id = lease.id

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
        is_edit = self.lease_id is not None
        lease = self._lease()
        found = lease is not None or not is_edit

        # Create page header
        title = "Edit Lease" if is_edit else "Create New Lease"
        subtitle = "Update lease details" if is_edit else "Fill in lease details"
        page_header = PageHeader(
            title=title,
            subtitle=subtitle,
            icon="file-text"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'lease': lease,
            'is_edit': is_edit,
            # A missing lease shows its error and no form choices (#3994).
            'properties': Property.objects.all().order_by('name') if found else Property.objects.none(),
            'tenants': (
                Tenant.objects.select_related('user').all().order_by('user__last_name')
                if found else Tenant.objects.none()
            ),
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
            'status_choices': Lease.STATUS_CHOICES,
        })

        return context
