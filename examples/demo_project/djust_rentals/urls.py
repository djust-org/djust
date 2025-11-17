"""
URL configuration for djust_rentals app
"""

from django.urls import path
from .views import *

app_name = 'rentals'

urlpatterns = [
    # Property Manager Portal
    path('', RentalDashboardView.as_view(), name='dashboard'),

    # Property Management
    path('properties/', PropertyListView.as_view(), name='property-list'),
    path('properties/<int:pk>/', PropertyDetailView.as_view(), name='property-detail'),
    path('properties/add/', PropertyFormView.as_view(), name='property-add'),
    path('properties/<int:pk>/edit/', PropertyFormView.as_view(), name='property-edit'),
    path('properties/<int:pk>/delete/', PropertyDeleteView.as_view(), name='property-delete'),

    # Tenant Management
    path('tenants/', TenantListView.as_view(), name='tenant-list'),
    path('tenants/<int:pk>/', TenantDetailView.as_view(), name='tenant-detail'),
    path('tenants/add/', TenantFormView.as_view(), name='tenant-add'),
    path('tenants/<int:pk>/edit/', TenantFormView.as_view(), name='tenant-edit'),

    # Lease Management
    path('leases/', LeaseListView.as_view(), name='lease-list'),
    path('leases/<int:pk>/', LeaseDetailView.as_view(), name='lease-detail'),
    path('leases/add/', LeaseFormView.as_view(), name='lease-add'),
    path('leases/<int:pk>/edit/', LeaseFormView.as_view(), name='lease-edit'),

    # Maintenance Management
    path('maintenance/', MaintenanceListView.as_view(), name='maintenance-list'),
    path('maintenance/<int:pk>/', MaintenanceDetailView.as_view(), name='maintenance-detail'),
    path('maintenance/<int:pk>/update/', MaintenanceUpdateView.as_view(), name='maintenance-update'),

    # Financial Reports
    path('financials/', FinancialDashboardView.as_view(), name='financials'),
    path('financials/income/', IncomeReportView.as_view(), name='income-report'),
    path('financials/expenses/', ExpenseReportView.as_view(), name='expense-report'),
    path('financials/profit-loss/', ProfitLossView.as_view(), name='profit-loss'),

    # Tenant Portal
    path('portal/', TenantDashboardView.as_view(), name='tenant-dashboard'),
    path('portal/maintenance/', TenantMaintenanceListView.as_view(), name='tenant-maintenance-list'),
    path('portal/maintenance/create/', TenantMaintenanceCreateView.as_view(), name='tenant-maintenance-create'),
    path('portal/payments/', TenantPaymentsView.as_view(), name='tenant-payments'),
]
