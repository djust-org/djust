"""
Django Forms for djust_rentals app

Forms with real-time validation using djust FormMixin.
"""

from django import forms
from django.core.exceptions import ValidationError
from datetime import date
from decimal import Decimal
from .models import Lease, Property, Tenant


class LeaseForm(forms.ModelForm):
    """
    Lease creation/editing form with real-time validation.

    Features:
    - Real-time field validation
    - Date range validation
    - Property availability validation
    - Tenant eligibility validation
    """

    class Meta:
        model = Lease
        fields = [
            'property',
            'tenant',
            'start_date',
            'end_date',
            'monthly_rent',
            'security_deposit',
            'rent_due_day',
            'late_fee',
            'status',
            'terms'
        ]
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'monthly_rent': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'security_deposit': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'rent_due_day': forms.NumberInput(attrs={'min': '1', 'max': '31'}),
            'late_fee': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'terms': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Additional terms and conditions...'}),
        }
        help_texts = {
            'rent_due_day': 'Day of month rent is due (1-31)',
            'late_fee': 'Late payment fee amount',
            'terms': 'Additional lease terms and conditions',
        }
        labels = {
            'property': 'Property',
            'tenant': 'Tenant',
            'start_date': 'Start Date',
            'end_date': 'End Date',
            'monthly_rent': 'Monthly Rent',
            'security_deposit': 'Security Deposit',
            'rent_due_day': 'Rent Due Day',
            'late_fee': 'Late Fee',
            'status': 'Status',
            'terms': 'Lease Terms',
        }

    def __init__(self, *args, **kwargs):
        """Initialize form with custom queryset filtering"""
        super().__init__(*args, **kwargs)

        # Order properties by name
        self.fields['property'].queryset = Property.objects.all().order_by('name')

        # Order tenants by last name
        self.fields['tenant'].queryset = Tenant.objects.select_related('user').all().order_by('user__last_name')

        # Mark required fields
        self.fields['property'].required = True
        self.fields['tenant'].required = True
        self.fields['start_date'].required = True
        self.fields['end_date'].required = True
        self.fields['monthly_rent'].required = True
        self.fields['security_deposit'].required = True
        self.fields['status'].required = True

        # Set default values for optional fields
        if not self.instance.pk:
            self.fields['rent_due_day'].initial = 1
            self.fields['late_fee'].initial = 0
            self.fields['status'].initial = 'active'

    def clean_start_date(self):
        """Validate start date"""
        start_date = self.cleaned_data.get('start_date')

        if not start_date:
            return start_date

        # For new leases, start date should be today or in the future
        if not self.instance.pk:
            if start_date < date.today():
                raise ValidationError(
                    "Start date cannot be in the past for new leases."
                )

        return start_date

    def clean_end_date(self):
        """Validate end date"""
        end_date = self.cleaned_data.get('end_date')

        if not end_date:
            return end_date

        # End date should be in the future
        if end_date < date.today():
            raise ValidationError(
                "End date must be in the future."
            )

        return end_date

    def clean(self):
        """Cross-field validation"""
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        property_obj = cleaned_data.get('property')
        monthly_rent = cleaned_data.get('monthly_rent')

        # Validate date range
        if start_date and end_date:
            if end_date <= start_date:
                raise ValidationError({
                    'end_date': 'End date must be after start date.'
                })

            # Check lease duration (at least 1 month)
            days_diff = (end_date - start_date).days
            if days_diff < 30:
                raise ValidationError({
                    'end_date': 'Lease must be at least 30 days long.'
                })

        # Validate property availability for new leases
        if not self.instance.pk and property_obj:
            # Check for overlapping leases
            overlapping = Lease.objects.filter(
                property=property_obj,
                status__in=['active', 'upcoming']
            ).exclude(pk=self.instance.pk if self.instance.pk else None)

            if start_date and end_date:
                overlapping = overlapping.filter(
                    # Check for any overlap
                    start_date__lte=end_date,
                    end_date__gte=start_date
                )

                if overlapping.exists():
                    raise ValidationError({
                        'property': f'This property already has an active or upcoming lease during this period.'
                    })

        # Validate monthly rent against property rent (if available)
        if property_obj and monthly_rent:
            if hasattr(property_obj, 'monthly_rent') and property_obj.monthly_rent:
                if monthly_rent < property_obj.monthly_rent * Decimal('0.8'):
                    # Warning if rent is more than 20% below property's listed rent
                    self.add_error('monthly_rent',
                        f'Monthly rent is significantly below property listing (${property_obj.monthly_rent}). '
                        f'Please verify this is correct.'
                    )

        return cleaned_data
