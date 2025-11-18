"""
Tenant Management Views

CRUD operations for tenants with search and filtering.
"""

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

    def mount(self, request, **kwargs):
        """Initialize tenant list state"""
        # Search state
        self.search_query = ""
        self.filter_status = "all"  # all, active, inactive

        # Load tenants
        self._refresh_tenants()

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

        # Store as private variable to avoid auto-JIT serialization
        # We'll assign to self.tenants in get_context_data() instead
        self._tenants = tenants.order_by('user__last_name', 'user__first_name')

    @event_handler()
    @debounce(wait=0.5)
    def search(self, query: str = "", **kwargs):
        """Search tenants with debouncing"""
        self.search_query = query
        self._refresh_tenants()

    @event_handler()
    def filter_by_status(self, status: str = "all", **kwargs):
        """Filter tenants by lease status"""
        self.filter_status = status
        self._refresh_tenants()

    def get_context_data(self, **kwargs):
        """Add tenant list context"""
        # Store tenants as instance variable for JIT serialization
        self.tenants = self._tenants

        # Call parent - JIT serializes 'tenants' with Rust + auto-generates 'tenants_count'
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
        self.tenant_id = pk

        # Load tenant
        try:
            self.tenant = Tenant.objects.select_related('user').get(pk=pk)
        except Tenant.DoesNotExist:
            self.tenant = None
            self.error_message = f"Tenant with ID {pk} not found"
            return

        # Load related data
        self.current_lease = self.tenant.get_current_lease()
        self.current_property = self.tenant.get_current_property()
        self.lease_history = Lease.objects.filter(tenant=self.tenant).order_by('-start_date')
        self.payment_history = Payment.objects.filter(
            lease__tenant=self.tenant
        ).order_by('-payment_date')[:20]
        self.maintenance_requests = MaintenanceRequest.objects.filter(
            tenant=self.tenant
        ).order_by('-created_at')[:10]

    def get_context_data(self, **kwargs):
        """Add tenant detail context"""
        context = super().get_context_data(**kwargs)

        if not self.tenant:
            context['error_message'] = self.error_message
            return context

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title=self.tenant.user.get_full_name(),
            subtitle=self.tenant.user.email,
            icon="user",
            actions=[
                {
                    "label": "Edit Tenant",
                    "url": f"/rentals/tenants/{self.tenant.pk}/edit/",
                    "icon": "edit",
                    "variant": "secondary"
                }
            ]
        )

        # Calculate payment statistics
        from django.db.models import Sum, Count
        total_payments = self.payment_history.aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        # Count maintenance requests by status
        maintenance_stats = {
            'total': self.maintenance_requests.count(),
            'open': self.maintenance_requests.filter(status='open').count(),
            'in_progress': self.maintenance_requests.filter(status='in_progress').count(),
            'completed': self.maintenance_requests.filter(status='completed').count(),
        }

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Tenant data
            'tenant': self.tenant,
            'current_lease': self.current_lease,
            'current_property': self.current_property,
            'lease_history': self.lease_history,
            'payment_history': self.payment_history,
            'maintenance_requests': self.maintenance_requests,
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

    def mount(self, request, pk=None, **kwargs):
        """Initialize tenant form"""
        self.tenant_id = pk
        self.is_edit = pk is not None

        if self.is_edit:
            # Load existing tenant
            try:
                self.tenant = Tenant.objects.select_related('user').get(pk=pk)
            except Tenant.DoesNotExist:
                self.tenant = None
                self.error_message = f"Tenant with ID {pk} not found"
                return
        else:
            # New tenant
            self.tenant = None

        # Form state
        self.success_message = ""
        self.error_message = ""
        self.validation_errors = {}

    @event_handler()
    def save_tenant(self, first_name="", last_name="", email="", phone="",
                   emergency_contact_name="", emergency_contact_phone="",
                   employer="", monthly_income=None, notes="", **kwargs):
        """Save tenant (create or update)"""
        try:
            if self.is_edit and self.tenant:
                # Update existing tenant
                self.tenant.user.first_name = first_name
                self.tenant.user.last_name = last_name
                self.tenant.user.email = email
                self.tenant.user.save()

                self.tenant.phone = phone
                self.tenant.emergency_contact_name = emergency_contact_name
                self.tenant.emergency_contact_phone = emergency_contact_phone
                self.tenant.employer = employer
                self.tenant.monthly_income = monthly_income if monthly_income else None
                self.tenant.notes = notes
                self.tenant.save()

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
                self.tenant = Tenant.objects.create(
                    user=user,
                    phone=phone,
                    emergency_contact_name=emergency_contact_name,
                    emergency_contact_phone=emergency_contact_phone,
                    employer=employer,
                    monthly_income=monthly_income if monthly_income else None,
                    notes=notes
                )

                self.success_message = "Tenant created successfully!"
                self.is_edit = True
                self.tenant_id = self.tenant.id

            self.error_message = ""
            self.validation_errors = {}

        except Exception as e:
            self.error_message = f"Error saving tenant: {str(e)}"

    def get_context_data(self, **kwargs):
        """Add form context"""
        context = super().get_context_data(**kwargs)

        # Create page header
        title = "Edit Tenant" if self.is_edit else "Add New Tenant"
        subtitle = "Update tenant details" if self.is_edit else "Fill in tenant details"
        page_header = PageHeader(
            title=title,
            subtitle=subtitle,
            icon="user"
        )

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),

            # Form data
            'tenant': self.tenant,
            'is_edit': self.is_edit,
            'success_message': self.success_message,
            'error_message': self.error_message,
            'validation_errors': self.validation_errors,
        })

        return context
