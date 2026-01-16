"""
Django admin configuration for rental property management models
"""

from django.contrib import admin
from .models import Property, Tenant, Lease, MaintenanceRequest, Payment, Expense


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'property_type', 'bedrooms', 'bathrooms', 'monthly_rent', 'status')
    list_filter = ('property_type', 'status')
    search_fields = ('name', 'address')
    ordering = ('name',)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'move_in_date')
    search_fields = ('user__first_name', 'user__last_name', 'phone')
    ordering = ('user__last_name',)


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ('property', 'tenant', 'start_date', 'end_date', 'monthly_rent', 'status')
    list_filter = ('status',)
    search_fields = ('property__name', 'tenant__user__last_name')
    ordering = ('-start_date',)


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    list_display = ('title', 'property', 'tenant', 'priority', 'status', 'created_at')
    list_filter = ('priority', 'status')
    search_fields = ('title', 'description', 'property__name')
    ordering = ('-created_at',)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('lease', 'amount', 'payment_date', 'payment_method', 'status')
    list_filter = ('status', 'payment_method')
    search_fields = ('lease__property__name', 'lease__tenant__user__last_name')
    ordering = ('-payment_date',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('property', 'category', 'amount', 'date', 'description')
    list_filter = ('category',)
    search_fields = ('property__name', 'description')
    ordering = ('-date',)
