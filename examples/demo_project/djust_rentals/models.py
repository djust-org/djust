"""
Database models for rental property management system
"""

from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal
from datetime import date


class Property(models.Model):
    """
    Rental property model - represents a property available for rent
    """
    PROPERTY_TYPES = [
        ('apartment', 'Apartment'),
        ('house', 'House'),
        ('condo', 'Condo'),
        ('townhouse', 'Townhouse'),
        ('studio', 'Studio'),
        ('duplex', 'Duplex'),
    ]

    STATUS_CHOICES = [
        ('available', 'Available'),
        ('occupied', 'Occupied'),
        ('maintenance', 'Under Maintenance'),
        ('unavailable', 'Unavailable'),
    ]

    # Basic Information
    name = models.CharField(max_length=200, help_text="Property name or identifier")
    address = models.TextField(help_text="Full property address")
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    zip_code = models.CharField(max_length=10)

    # Property Details
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPES)
    bedrooms = models.IntegerField(validators=[MinValueValidator(0)])
    bathrooms = models.DecimalField(max_digits=3, decimal_places=1, validators=[MinValueValidator(0)])
    square_feet = models.IntegerField(validators=[MinValueValidator(0)])

    # Financial
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    security_deposit = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available')

    # Additional Information
    description = models.TextField(blank=True)
    amenities = models.TextField(blank=True, help_text="Comma-separated list of amenities")
    parking_spaces = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    pet_friendly = models.BooleanField(default=False)
    furnished = models.BooleanField(default=False)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Property'
        verbose_name_plural = 'Properties'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} - {self.address}"

    def get_current_lease(self):
        """Get the currently active lease for this property"""
        return self.lease_set.filter(status='active').first()

    def get_current_tenant(self):
        """Get the current tenant (if any)"""
        current_lease = self.get_current_lease()
        return current_lease.tenant if current_lease else None

    def is_occupied(self):
        """Check if property is currently occupied"""
        return self.status == 'occupied' and self.get_current_lease() is not None

    @property
    def status_display(self):
        """Expose get_status_display() as property for JIT serialization"""
        return self.get_status_display()


class Tenant(models.Model):
    """
    Tenant model - represents a renter
    """
    # User Account
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='tenant_profile')

    # Contact Information
    phone = models.CharField(max_length=20)
    emergency_contact_name = models.CharField(max_length=200)
    emergency_contact_phone = models.CharField(max_length=20)

    # Tenant Details
    move_in_date = models.DateField(null=True, blank=True)
    employer = models.CharField(max_length=200, blank=True)
    monthly_income = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Notes
    notes = models.TextField(blank=True, help_text="Internal notes about tenant")

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenants'
        ordering = ['user__last_name', 'user__first_name']

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.user.email})"

    def get_current_lease(self):
        """Get the currently active lease for this tenant"""
        return self.lease_set.filter(status='active').first()

    def get_current_property(self):
        """Get the property currently leased by this tenant"""
        current_lease = self.get_current_lease()
        return current_lease.property if current_lease else None


class Lease(models.Model):
    """
    Lease model - represents a rental agreement
    """
    # Save reference to built-in property before field definition shadows it
    _property = property

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('expired', 'Expired'),
        ('terminated', 'Terminated'),
        ('upcoming', 'Upcoming'),
    ]

    # Relationships
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='leases')
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

    # Lease Terms
    start_date = models.DateField()
    end_date = models.DateField()
    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    security_deposit = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])

    # Payment Terms
    rent_due_day = models.IntegerField(default=1, validators=[MinValueValidator(1)], help_text="Day of month rent is due")
    late_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    # Additional Terms
    terms = models.TextField(blank=True, help_text="Additional lease terms and conditions")

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Lease'
        verbose_name_plural = 'Leases'
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.property.name} - {self.tenant.user.get_full_name()} ({self.start_date} to {self.end_date})"

    def is_active(self):
        """Check if lease is currently active"""
        today = date.today()
        return self.status == 'active' and self.start_date <= today <= self.end_date

    def days_until_expiration(self):
        """Calculate days until lease expires"""
        if self.status != 'active':
            return None
        today = date.today()
        if today > self.end_date:
            return 0
        return (self.end_date - today).days

    @_property
    def days_left(self):
        """Alias for days_until_expiration for template use"""
        return self.days_until_expiration()

    @_property
    def warning(self):
        """Get warning level based on days until expiration"""
        days = self.days_left
        if days is None:
            return None
        if days <= 30:
            return "urgent"
        elif days <= 60:
            return "soon"
        return None

    @_property
    def property_name(self):
        """Property name for JIT serialization"""
        return self.property.name

    @_property
    def tenant_name(self):
        """Tenant full name for JIT serialization"""
        return self.tenant.user.get_full_name()

    @_property
    def rental_property(self):
        """Alias for property field for backwards compatibility"""
        return self.property


class MaintenanceRequest(models.Model):
    """
    Maintenance request model - tracks property maintenance issues
    """
    # Save reference to built-in property before field definition shadows it
    _property = property

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    # Relationships
    property = models.ForeignKey(Property, on_delete=models.CASCADE)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True)

    # Request Details
    title = models.CharField(max_length=200)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')

    # Assignment
    assigned_to = models.CharField(max_length=200, blank=True, help_text="Contractor or staff assigned")

    # Progress Tracking
    notes = models.TextField(blank=True, help_text="Internal notes and updates")
    estimated_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    actual_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Maintenance Request'
        verbose_name_plural = 'Maintenance Requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.property.name} ({self.get_status_display()})"

    def mark_completed(self):
        """Mark the request as completed"""
        from django.utils import timezone
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.save()

    # Can't use @property decorator here because we have a field named 'property'
    # which shadows Python's built-in 'property' decorator.
    # Use _property reference saved at class level instead.
    def get_priority_display_prop(self):
        """Expose get_priority_display() for JIT serialization"""
        return self.get_priority_display()
    priority_display = _property(get_priority_display_prop)

    def get_property_name_prop(self):
        """Property name for JIT serialization"""
        return self.property.name
    property_name = _property(get_property_name_prop)

    def get_rental_property_prop(self):
        """Alias for property field for consistency with Lease model"""
        return self.property
    rental_property = _property(get_rental_property_prop)


class Payment(models.Model):
    """
    Payment model - tracks rent and other payments
    """
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('check', 'Check'),
        ('credit_card', 'Credit Card'),
        ('bank_transfer', 'Bank Transfer'),
        ('online', 'Online Payment'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    # Relationships
    lease = models.ForeignKey(Lease, on_delete=models.CASCADE)

    # Payment Details
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')

    # Additional Information
    notes = models.TextField(blank=True)
    transaction_id = models.CharField(max_length=200, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        ordering = ['-payment_date']

    def __str__(self):
        return f"${self.amount} - {self.lease.property.name} ({self.payment_date})"


class Expense(models.Model):
    """
    Expense model - tracks property-related expenses
    """
    CATEGORY_CHOICES = [
        ('maintenance', 'Maintenance'),
        ('repairs', 'Repairs'),
        ('utilities', 'Utilities'),
        ('insurance', 'Insurance'),
        ('property_tax', 'Property Tax'),
        ('HOA', 'HOA Fees'),
        ('landscaping', 'Landscaping'),
        ('cleaning', 'Cleaning'),
        ('advertising', 'Advertising'),
        ('legal', 'Legal'),
        ('other', 'Other'),
    ]

    # Relationships
    property = models.ForeignKey(Property, on_delete=models.CASCADE)

    # Expense Details
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    date = models.DateField()
    description = models.TextField()

    # Payment Information
    vendor = models.CharField(max_length=200, blank=True)
    receipt_number = models.CharField(max_length=100, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'
        ordering = ['-date']

    def __str__(self):
        return f"{self.get_category_display()} - ${self.amount} ({self.property.name})"
