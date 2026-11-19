"""
Tenant Management Views

CRUD operations for tenants with search and filtering.
"""

from typing import Any

from djust import state
from djust_shared.views import BaseViewWithNavbar
from djust.decorators import debounce, event_handler
from djust_shared.components.ui import HeroSection
from django.db.models import Q
from django.contrib.auth.models import User
from ..models import Tenant, Lease, MaintenanceRequest, Payment
from ..components import StatCard, PageHeader, StatusBadge, DataTable


class TenantListView(BaseViewWithNavbar):
    """
    Tenant list view with search and filtering.

    Features:
    - Search by name, email, phone
    - Filter by lease status
    - View current property for each tenant
    """
    template_name = 'rentals/tenant_list.html'

    # Search and filter inputs; persist="server" keeps them across the HTTP
    # fallback and a reconnect. The list is queried per render from them.
    search_query = state("", persist="server")
    filter_status = state("all", persist="server")  # all, active, inactive

    def _refresh_tenants(self):
        """Refresh tenant list based on current filters"""
        from django.db.models import Prefetch

        # Optimize QuerySet with prefetch for current leases and their properties
        tenants = Tenant.objects.select_related('user').prefetch_related(
            Prefetch(
                'lease_set',
                queryset=Lease.objects.filter(status='active').select_related('property'),
                to_attr='active_leases'
            )
        ).all()

        # Apply search
        if self.search_query:
            tenants = tenants.filter(
                Q(user__first_name__icontains=self.search_query) |
                Q(user__last_name__icontains=self.search_query) |
                Q(user__email__icontains=self.search_query) |
                Q(phone__icontains=self.search_query)
            )

        # Apply status filter
        if self.filter_status == "active":
            # Tenants with active leases
            active_tenant_ids = Lease.objects.filter(status='active').values_list('tenant_id', flat=True)
            tenants = tenants.filter(id__in=active_tenant_ids)
        elif self.filter_status == "inactive":
            # Tenants without active leases
            active_tenant_ids = Lease.objects.filter(status='active').values_list('tenant_id', flat=True)
            tenants = tenants.exclude(id__in=active_tenant_ids)

        self._tenants = tenants.order_by('user__last_name', 'user__first_name')

    @event_handler()
    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search tenants with debouncing"""
        self.search_query = query

    @event_handler()
    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter tenants by lease status"""
        self.filter_status = status

    def get_context_data(self, **kwargs):
        """Add tenant list context"""
        self._refresh_tenants()
        context = super().get_context_data(**kwargs)

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Tenants",
            subtitle="Manage your tenants and their leases",
            icon="users",
            actions=[{
                "label": "Add Tenant",
                "url": "/rentals/tenants/add/",
                "icon": "plus"
            }]
        )

        # Calculate status counts
        all_tenants = Tenant.objects.all()
        total_count = all_tenants.count()
        active_tenant_ids = Lease.objects.filter(status='active').values_list('tenant_id', flat=True).distinct()
        active_count = all_tenants.filter(id__in=active_tenant_ids).count()
        inactive_count = total_count - active_count

        # Create StatCard components for status overview (render to HTML)
        stat_cards_html = [
            StatCard(label="Total Tenants", value=str(total_count), icon="users", color="primary").render(),
            StatCard(label="Active Leases", value=str(active_count), icon="key", color="green").render(),
            StatCard(label="Inactive", value=str(inactive_count), icon="user-x", color="gray").render(),
        ]

        # Create DataTable rows from tenants
        table_rows = []
        for tenant in self._tenants:
            # Get current property
            current_property = ""
            if hasattr(tenant, 'active_leases') and tenant.active_leases:
                property_name = tenant.active_leases[0].property.name
                current_property = f'<span class="text-green-600 dark:text-green-400">{property_name}</span>'
            else:
                current_property = '<span class="text-muted-foreground">No active lease</span>'

            table_rows.append({
                "Name": tenant.user.get_full_name(),
                "Email": f'<span class="text-muted-foreground">{tenant.user.email}</span>',
                "Phone": tenant.phone,
                "Current Property": current_property,
                "Actions": f'<a href="/rentals/tenants/{tenant.pk}/" data-djust-navigate class="text-primary hover:underline text-sm inline-flex items-center gap-1"><i data-lucide="eye" class="w-3 h-3"></i> View</a> | <a href="/rentals/tenants/{tenant.pk}/edit/" data-djust-navigate class="text-muted-foreground hover:underline text-sm">Edit</a>'
            })

        # Create DataTable component
        tenant_table = DataTable(
            headers=["Name", "Email", "Phone", "Current Property", "Actions"],
            rows=table_rows,
            empty_message="No tenants found. Try adjusting your filters or add a new tenant."
        )

        # Add non-model context
        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,
            'tenant_table': tenant_table.render(),

            # Filter state
            'search_query': self.search_query,
            'filter_status': self.filter_status,
        })

        return context


class TenantDetailView(BaseViewWithNavbar):
    """
    Tenant detail view showing comprehensive tenant information.

    Features:
    - Tenant contact details
    - Current lease and property
    - Lease history
    - Payment history
    - Maintenance requests
    """
    template_name = 'rentals/tenant_detail.html'

    def mount(self, request, pk=None, **kwargs):
        """Initialize tenant detail view"""
        self._pk = pk

    def _load(self):
        # Read on every render, not kept from mount(): between events the HTTP
        # fallback restores a saved model as its field dict, not a row.
        pk = self._pk
        self._tenant_id = pk

        # Load tenant
        try:
            self._tenant = Tenant.objects.select_related('user').get(pk=pk)
        except Tenant.DoesNotExist:
            self._tenant = None
            self._error_message = f"Tenant with ID {pk} not found"
            return

        # Load related data
        self._current_lease = self._tenant.get_current_lease()
        self._current_property = self._tenant.get_current_property()
        self._lease_history = Lease.objects.filter(tenant=self._tenant).order_by('-start_date')
        self._payment_history = Payment.objects.filter(
            lease__tenant=self._tenant
        ).order_by('-payment_date')[:20]
        self._maintenance_requests = MaintenanceRequest.objects.filter(
            tenant=self._tenant
        ).order_by('-created_at')[:10]

    def get_context_data(self, **kwargs):
        """Add tenant detail context"""
        self._load()
        context = super().get_context_data(**kwargs)

        if not self._tenant:
            context['error_message'] = self._error_message
            return context

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title=self._tenant.user.get_full_name(),
            subtitle=self._tenant.user.email,
            icon="user",
            actions=[
                {
                    "label": "Edit Tenant",
                    "url": f"/rentals/tenants/{self._tenant.pk}/edit/",
                    "icon": "edit",
                    "variant": "secondary"
                }
            ]
        )

        # Calculate payment statistics
        from django.db.models import Sum, Count
        total_payments = self._payment_history.aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        # Count maintenance requests by status
        # Over all of the tenant's requests: self._maintenance_requests is the
        # sliced "recent 10", and a sliced queryset cannot be filtered (#3994).
        all_requests = MaintenanceRequest.objects.filter(tenant=self._tenant)
        maintenance_stats = {
            'total': all_requests.count(),
            'open': all_requests.filter(status='open').count(),
            'in_progress': all_requests.filter(status='in_progress').count(),
            'completed': all_requests.filter(status='completed').count(),
        }

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Tenant data
            'tenant': self._tenant,
            'current_lease': self._current_lease,
            'current_property': self._current_property,
            'lease_history': self._lease_history,
            'payment_history': self._payment_history,
            'maintenance_requests': self._maintenance_requests,
            'total_paid': total_payments['total'] or 0,
            'payment_count': total_payments['count'] or 0,
            'maintenance_stats': maintenance_stats,
        })

        return context


class TenantFormView(BaseViewWithNavbar):
    """
    Tenant create/edit form view.

    Features:
    - Create new tenants (with user account)
    - Edit existing tenant information
    - Real-time form validation
    """
    template_name = 'rentals/tenant_form.html'

    # The edited tenant's id: from the URL, or set when a new tenant is saved.
    # persist="server" keeps it across the HTTP fallback, so a second save
    # updates that tenant instead of creating another.
    tenant_id = state(None, persist="server", render=False)
    success_message = state("", render=False)
    error_message = state("", render=False)
    validation_errors = state(default_factory=dict, render=False)

    def mount(self, request, pk=None, **kwargs):
        """Initialize tenant form"""
        self.tenant_id = pk
        if pk is not None and self._tenant() is None:
            self.error_message = f"Tenant with ID {pk} not found"

    def _tenant(self):
        """The edited tenant, or None for a new one."""
        if self.tenant_id is None:
            return None
        return Tenant.objects.select_related('user').filter(pk=self.tenant_id).first()

    @event_handler()
    def save_tenant(self, first_name: str = "", last_name: str = "", email: str = "",
                    phone: str = "", emergency_contact_name: str = "",
                    emergency_contact_phone: str = "", employer: str = "",
                    monthly_income: Any = None, notes: str = "", **kwargs):
        """Save tenant (create or update)"""
        try:
            tenant = self._tenant()
            if tenant is not None:
                # Update existing tenant
                tenant.user.first_name = first_name
                tenant.user.last_name = last_name
                tenant.user.email = email
                tenant.user.save()

                tenant.phone = phone
                tenant.emergency_contact_name = emergency_contact_name
                tenant.emergency_contact_phone = emergency_contact_phone
                tenant.employer = employer
                tenant.monthly_income = monthly_income if monthly_income else None
                tenant.notes = notes
                tenant.save()

                self.success_message = "Tenant updated successfully!"
            else:
                # Create new tenant with user account
                # Generate username from email
                username = email.split('@')[0]
                # Ensure unique username
                base_username = username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}{counter}"
                    counter += 1

                # Create user
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    password=User.objects.make_random_password()  # Random password
                )

                # Create tenant
                tenant = Tenant.objects.create(
                    user=user,
                    phone=phone,
                    emergency_contact_name=emergency_contact_name,
                    emergency_contact_phone=emergency_contact_phone,
                    employer=employer,
                    monthly_income=monthly_income if monthly_income else None,
                    notes=notes
                )

                self.success_message = "Tenant created successfully!"
                self.tenant_id = tenant.id

            self.error_message = ""
            self.validation_errors = {}

        except Exception as e:
            self.error_message = f"Error saving tenant: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)
        is_edit = self.tenant_id is not None

        # Create page header
        title = "Edit Tenant" if is_edit else "Add New Tenant"
        subtitle = "Update tenant details" if is_edit else "Fill in tenant details"
        page_header = PageHeader(
            title=title,
            subtitle=subtitle,
            icon="user"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'tenant': self._tenant(),
            'is_edit': is_edit,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
        })

        return context
