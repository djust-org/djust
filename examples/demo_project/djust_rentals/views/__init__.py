"""
Views for djust_rentals app

Organized by functional area:
- dashboard: Property manager dashboard
- properties: Property CRUD operations
- tenants: Tenant management
- leases: Lease tracking
- maintenance: Maintenance request handling
- financials: Financial reports
- tenant_portal: Tenant-facing views
"""

# Import all views
from .dashboard import RentalDashboardView
from .properties import PropertyListView, PropertyDetailView, PropertyFormView, PropertyDeleteView
from .tenants import TenantListView, TenantDetailView, TenantFormView
from .leases import LeaseListView, LeaseDetailView, LeaseFormView
from .maintenance import MaintenanceListView, MaintenanceDetailView, MaintenanceUpdateView, MaintenanceFormView, MaintenanceDeleteView
from .financials import FinancialDashboardView, IncomeReportView, ExpenseReportView, ProfitLossView
from .tenant_portal import TenantDashboardView, TenantMaintenanceListView, TenantMaintenanceCreateView, TenantPaymentsView

__all__ = [
    # Dashboard
    'RentalDashboardView',

    # Properties
    'PropertyListView',
    'PropertyDetailView',
    'PropertyFormView',
    'PropertyDeleteView',

    # Tenants
    'TenantListView',
    'TenantDetailView',
    'TenantFormView',

    # Leases
    'LeaseListView',
    'LeaseDetailView',
    'LeaseFormView',

    # Maintenance
    'MaintenanceListView',
    'MaintenanceDetailView',
    'MaintenanceUpdateView',
    'MaintenanceFormView',
    'MaintenanceDeleteView',

    # Financials
    'FinancialDashboardView',
    'IncomeReportView',
    'ExpenseReportView',
    'ProfitLossView',

    # Tenant Portal
    'TenantDashboardView',
    'TenantMaintenanceListView',
    'TenantMaintenanceCreateView',
    'TenantPaymentsView',
]
