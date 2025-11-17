"""
Property Manager Dashboard View

Main dashboard showing key metrics, recent activity, and quick actions.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import cache, debounce
from djust_shared.components.ui import HeroSection, FeatureCard
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta
from ..models import Property, Tenant, Lease, MaintenanceRequest, Payment, Expense


class RentalDashboardView(BaseViewWithNavbar):
    """
    Main dashboard for rental property management.

    Features:
    - Key metrics (properties, tenants, vacancy, income, maintenance)
    - Recent activity feed
    - Quick action buttons
    - Search and filter capabilities
    - Real-time updates using @client_state
    """
    template_name = 'rentals/dashboard.html'

    def mount(self, request, **kwargs):
        """Initialize dashboard state"""
        # Search and filter state
        self.search_query = ""
        self.filter_status = "all"  # all, active, maintenance, vacant

        # Initialize hero component
        self.hero = HeroSection(
            title="Rental Property Dashboard",
            subtitle="Manage your rental properties, tenants, and finances",
            icon="🏠"
        )

        # Load initial data
        self._refresh_data()

    def _refresh_data(self):
        """Refresh dashboard data (called internally)"""
        # Get all data for calculations
        self.properties = Property.objects.all()
        self.tenants = Tenant.objects.all()
        self.active_leases = Lease.objects.filter(status='active')

        # Don't slice yet - need to filter by priority in get_context_data
        self.pending_maintenance_qs = MaintenanceRequest.objects.filter(
            status__in=['open', 'in_progress']
        ).order_by('-created_at')

        # Calculate recent payments (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        self.recent_payments = Payment.objects.filter(
            payment_date__gte=thirty_days_ago,
            status='completed'
        ).order_by('-payment_date')[:10]

        # Calculate recent expenses (last 30 days)
        self.recent_expenses = Expense.objects.filter(
            date__gte=thirty_days_ago
        ).order_by('-date')[:10]

    @debounce(wait=0.5)
    def search_properties(self, query: str = "", **kwargs):
        """
        Search properties with debouncing.

        Searches in:
        - Property name
        - Address
        - City
        """
        self.search_query = query
        if query:
            self.properties = Property.objects.filter(
                Q(name__icontains=query) |
                Q(address__icontains=query) |
                Q(city__icontains=query)
            )
        else:
            self.properties = Property.objects.all()

    def filter_properties(self, status: str = "all", **kwargs):
        """Filter properties by status"""
        self.filter_status = status

        if status == "all":
            self.properties = Property.objects.all()
        elif status == "active":
            # Properties with active leases
            active_property_ids = self.active_leases.values_list('property_id', flat=True)
            self.properties = Property.objects.filter(id__in=active_property_ids)
        elif status == "vacant":
            # Properties without active leases
            active_property_ids = self.active_leases.values_list('property_id', flat=True)
            self.properties = Property.objects.exclude(id__in=active_property_ids)
        elif status == "maintenance":
            # Properties with pending maintenance
            maintenance_property_ids = self.pending_maintenance_qs.values_list('property_id', flat=True)
            self.properties = Property.objects.filter(id__in=maintenance_property_ids)

    def get_context_data(self, **kwargs):
        """Add dashboard statistics to context"""
        context = super().get_context_data(**kwargs)

        # Calculate key metrics
        total_properties = self.properties.count()
        active_tenants = self.active_leases.count()

        # Calculate vacancy rate
        if total_properties > 0:
            vacancy_rate = round(((total_properties - active_tenants) / total_properties) * 100, 1)
        else:
            vacancy_rate = 0

        # Calculate total monthly income
        monthly_income = self.active_leases.aggregate(
            total=Sum('monthly_rent')
        )['total'] or 0

        # Count pending maintenance requests
        pending_maintenance_count = self.pending_maintenance_qs.count()

        # Get maintenance by priority
        urgent_maintenance = self.pending_maintenance_qs.filter(priority='urgent').count()
        high_maintenance = self.pending_maintenance_qs.filter(priority='high').count()

        # Get top 10 for display
        pending_maintenance_display = self.pending_maintenance_qs[:10]

        # Recent activity (last 7 days)
        seven_days_ago = timezone.now() - timedelta(days=7)
        recent_leases = Lease.objects.filter(created_at__gte=seven_days_ago).count()
        recent_maintenance_requests = MaintenanceRequest.objects.filter(
            created_at__gte=seven_days_ago
        ).count()
        recent_payments_count = Payment.objects.filter(
            created_at__gte=seven_days_ago,
            status='completed'
        ).count()

        # Calculate this month's financials
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        month_income = Payment.objects.filter(
            payment_date__gte=month_start,
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or 0

        month_expenses = Expense.objects.filter(
            date__gte=month_start
        ).aggregate(total=Sum('amount'))['total'] or 0

        month_profit = month_income - month_expenses

        # Leases expiring soon (next 60 days)
        sixty_days_from_now = timezone.now().date() + timedelta(days=60)
        expiring_soon = Lease.objects.filter(
            status='active',
            end_date__lte=sixty_days_from_now
        ).order_by('end_date')[:5]

        # Serialize properties for template
        properties_list = []
        for prop in self.properties[:10]:
            properties_list.append({
                'name': prop.name,
                'address': prop.address,
                'monthly_rent': prop.monthly_rent,
                'status': prop.status,
                'status_display': prop.get_status_display(),
            })

        # Serialize maintenance requests
        maintenance_list = []
        for req in pending_maintenance_display:
            maintenance_list.append({
                'title': req.title,
                'property_name': req.property.name,
                'priority': req.priority,
                'priority_display': req.get_priority_display(),
                'created_at': req.created_at,
            })

        # Serialize expiring leases
        expiring_list = []
        for lease in expiring_soon:
            expiring_list.append({
                'pk': lease.pk,
                'property_name': lease.property.name,
                'tenant_name': lease.tenant.user.get_full_name(),
                'end_date': lease.end_date,
                'days_until_expiration': lease.days_until_expiration(),
            })

        # Update context
        context.update({
            # Stats
            'total_properties': total_properties,
            'active_tenants': active_tenants,
            'vacancy_rate': vacancy_rate,
            'monthly_income': monthly_income,
            'pending_maintenance_count': pending_maintenance_count,
            'urgent_maintenance': urgent_maintenance,
            'high_maintenance': high_maintenance,

            # Recent activity
            'recent_leases_count': recent_leases,
            'recent_maintenance_count': recent_maintenance_requests,
            'recent_payments_count': recent_payments_count,

            # Financials
            'month_income': month_income,
            'month_expenses': month_expenses,
            'month_profit': month_profit,

            # Lists (serialized)
            'properties': properties_list,
            'pending_maintenance': maintenance_list,
            'expiring_soon': expiring_list,

            # Filter state
            'search_query': self.search_query,
            'filter_status': self.filter_status,
        })

        return context
