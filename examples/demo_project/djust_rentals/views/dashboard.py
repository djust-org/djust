"""
Property Manager Dashboard View

Main dashboard showing key metrics, recent activity, and quick actions.
"""

from djust import state
from djust.decorators import event_handler
from djust_shared.views import BaseViewWithNavbar
from djust.decorators import cache, debounce
from djust_shared.components.ui import HeroSection, FeatureCard
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta
from ..models import Property, Tenant, Lease, MaintenanceRequest, Payment, Expense
from ..components import StatCard, PageHeader, StatusBadge, DataTable


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

    # Search and filter inputs; persist="server" keeps them across the HTTP
    # fallback and a reconnect. The data is queried per render from them.
    search_query = state("", persist="server")
    filter_status = state("all", persist="server")  # all, active, maintenance, vacant

    def _filtered_properties(self, active_leases, pending_maintenance_qs):
        """Properties matching the current search and status filter."""
        properties = Property.objects.all()
        if self.search_query:
            properties = properties.filter(
                Q(name__icontains=self.search_query) |
                Q(address__icontains=self.search_query) |
                Q(city__icontains=self.search_query)
            )
        status = self.filter_status
        if status == "active":
            # Properties with active leases
            properties = properties.filter(id__in=active_leases.values_list('property_id', flat=True))
        elif status == "vacant":
            # Properties without active leases
            properties = properties.exclude(id__in=active_leases.values_list('property_id', flat=True))
        elif status == "maintenance":
            # Properties with pending maintenance
            properties = properties.filter(
                id__in=pending_maintenance_qs.values_list('property_id', flat=True)
            )
        return properties

    @event_handler
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

    @event_handler
    def filter_properties(self, status: str = "all", **kwargs):
        """Filter properties by status"""
        self.filter_status = status

    def get_context_data(self, **kwargs):
        """Add dashboard statistics to context"""
        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Dashboard",
            subtitle="Manage your rental properties, tenants, and finances",
            icon="layout-dashboard"
        )

        active_leases = Lease.objects.filter(status='active')
        pending_maintenance_qs = MaintenanceRequest.objects.filter(
            status__in=['open', 'in_progress']
        ).order_by('-created_at')
        properties = self._filtered_properties(active_leases, pending_maintenance_qs)

        # Calculate key metrics
        total_properties = properties.count()
        active_tenants = active_leases.count()

        # Calculate vacancy rate
        if total_properties > 0:
            vacancy_rate = round(((total_properties - active_tenants) / total_properties) * 100, 1)
        else:
            vacancy_rate = 0

        # Calculate total monthly income
        monthly_income = active_leases.aggregate(
            total=Sum('monthly_rent')
        )['total'] or 0

        # Count pending maintenance requests
        pending_maintenance_count = pending_maintenance_qs.count()

        # Get maintenance by priority
        urgent_maintenance = pending_maintenance_qs.filter(priority='urgent').count()
        high_maintenance = pending_maintenance_qs.filter(priority='high').count()

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

        # The QuerySets go to the template as native values: the renderer
        # reads the fields the template names.
        context = super().get_context_data(**kwargs)
        context.update({
            'properties': properties[:10],  # Top 10 properties
            'pending_maintenance': pending_maintenance_qs[:10],  # Top 10 maintenance
            'expiring_soon': expiring_soon,
        })

        # Create StatCard components for key metrics (render to HTML)
        stat_cards_html = [
            StatCard(
                label="Total Properties",
                value=str(total_properties),
                icon="home",
                color="primary"
            ).render(),
            StatCard(
                label="Active Tenants",
                value=str(active_tenants),
                icon="users",
                color="primary"
            ).render(),
            StatCard(
                label="Vacancy Rate",
                value=f"{vacancy_rate}%",
                icon="key",
                color="yellow" if vacancy_rate > 20 else "primary"
            ).render(),
            StatCard(
                label="Monthly Income",
                value=f"${monthly_income:,.0f}",
                icon="dollar-sign",
                color="green"
            ).render(),
            StatCard(
                label="Pending Maintenance",
                value=str(pending_maintenance_count),
                icon="wrench",
                color="red" if urgent_maintenance > 0 else "yellow"
            ).render(),
        ]

        # Add non-model context
        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,

            # Stats (for backwards compatibility)
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

            # Filter state
            'search_query': self.search_query,
            'filter_status': self.filter_status,
        })

        return context
