"""
Djust LiveView Implementation of StatusChange Form

This demonstrates how to convert the declarative React form definition
to djust's server-side reactive LiveView approach.

Key Differences:
- React: Client-side state management, declarative Python → JSON → React
- Djust: Server-side state management, LiveView with real-time updates
"""

from django import forms
from django.contrib.auth.models import User
from djust import LiveView
from djust.forms import FormMixin
from djust.decorators import event_handler


# ============================================================================
# Django Form Definition (replaces FormSection/FormField declarations)
# ============================================================================

class StatusChangeForm(forms.Form):
    """
    Django form with all fields from StatusChange definition.

    In djust, you use standard Django Forms instead of custom FormSection/FormField.
    """

    # Employee Information Section
    on_behalf_of = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
        label="On Behalf Of",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    employee = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True,
        label="Employee",
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'})
    )

    business_line = forms.ChoiceField(
        choices=[],  # Populated dynamically
        required=True,
        label="Business Line",
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'})
    )

    executive_approver = forms.ModelChoiceField(
        queryset=User.objects.none(),  # Filtered based on business_line
        required=True,
        label="Executive Approver",
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'})
    )

    effective_date = forms.DateField(
        required=True,
        label="Effective Date",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-lg'})
    )

    # Company Changes Section (ToFrom pattern)
    needs_company = forms.BooleanField(
        required=False,
        label="Change Company?",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    previous_company = forms.CharField(
        required=False,
        label="Previous Company",
        widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': True})
    )
    new_company = forms.CharField(
        required=False,
        label="New Company",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    # Supervisor Changes Section
    needs_supervisor = forms.BooleanField(
        required=False,
        label="Change Supervisor?",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    previous_supervisor = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
        label="Previous Supervisor",
        widget=forms.Select(attrs={'class': 'form-select', 'disabled': True})
    )
    new_supervisor = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=False,
        label="New Supervisor",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    # Project Information Section
    needs_projects = forms.BooleanField(
        required=False,
        label="Add Project Information?",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    project_number = forms.CharField(
        required=False,
        label="Project Number",
        widget=forms.TextInput(attrs={'class': 'form-control form-control-lg'})
    )
    project_duration = forms.ChoiceField(
        choices=[
            ('', 'Select Duration'),
            ('under_12', 'Under 12 Months'),
            ('over_12', 'Exceeds 12 Months'),
        ],
        required=False,
        label="Project Duration",
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'})
    )
    expected_end_date = forms.DateField(
        required=False,
        label="Expected End Date",
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-lg'})
    )

    # Meals Per Diem Section
    needs_meals_per_diem = forms.BooleanField(
        required=False,
        label="Employee needs meals per diem?",
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )
    days_per_month_on_job_site = forms.IntegerField(
        required=False,
        label="Days Per Month on Jobsite",
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-lg'})
    )
    new_meals_per_diem_amount = forms.DecimalField(
        required=False,
        label="Per Diem Rate for Area",
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-lg', 'step': '0.01'})
    )
    total_monthly_meals_per_diem = forms.DecimalField(
        required=False,
        label="Total Monthly Meals Per Diem",
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-lg', 'readonly': True})
    )

    notes = forms.CharField(
        required=False,
        label="Additional Notes",
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4})
    )

    supervisor_signature = forms.CharField(
        required=True,
        label="Supervisor Signature",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        # Extract custom kwargs
        self.business_line_id = kwargs.pop('business_line_id', None)
        self.employee_id = kwargs.pop('employee_id', None)

        super().__init__(*args, **kwargs)

        # Filter executive_approver based on business_line
        if self.business_line_id:
            # In real app: filter by business_line relationship
            self.fields['executive_approver'].queryset = User.objects.filter(
                # businessline__id=self.business_line_id
                is_staff=True
            )

    def clean(self):
        """Custom validation logic (replaces validation_rules)"""
        cleaned_data = super().clean()

        # Conditional required fields
        if cleaned_data.get('needs_company'):
            if not cleaned_data.get('new_company'):
                self.add_error('new_company', 'New company is required when changing company')

        if cleaned_data.get('needs_supervisor'):
            if not cleaned_data.get('new_supervisor'):
                self.add_error('new_supervisor', 'New supervisor is required when changing supervisor')

        if cleaned_data.get('project_duration') == 'over_12':
            if not cleaned_data.get('expected_end_date'):
                self.add_error('expected_end_date', 'Expected end date required for projects exceeding 12 months')

        return cleaned_data


# ============================================================================
# LiveView Implementation (replaces React component + form definition)
# ============================================================================

class StatusChangeFormView(FormMixin, LiveView):
    """
    Djust LiveView for StatusChange form with real-time reactive updates.

    This replaces:
    - React form component
    - StatusChangeDefinition.to_react_config()
    - Client-side state management

    Benefits:
    - Server-side validation
    - Real-time field updates over WebSocket
    - No JSON serialization needed
    - Simpler mental model
    """

    form_class = StatusChangeForm
    template_name = 'forms/status_change_djust.html'

    def mount(self, request, **kwargs):
        """Initialize the LiveView (called on page load)"""
        self.employee_id = None
        self.business_line_id = None
        self.show_expected_end_date = False
        self.show_company_fields = False
        self.show_supervisor_fields = False
        self.show_project_fields = False
        self.show_meals_fields = False

        # Section expansion state
        self.sections_expanded = {
            'employee_info': True,
            'company_changes': True,
            'supervisor_changes': True,
            'project_info': True,
            'meals_per_diem': True,
        }

    def get_form_kwargs(self):
        """Pass additional kwargs to form"""
        kwargs = super().get_form_kwargs()
        kwargs['business_line_id'] = self.business_line_id
        kwargs['employee_id'] = self.employee_id
        return kwargs

    # ========================================================================
    # Event Handlers (replace React onChange handlers)
    # ========================================================================

    @event_handler
    def on_employee_change(self, employee_id):
        """
        Handle employee selection change.
        Auto-populates fields from employee profile.

        Replaces: handle_employee_change() callback
        """
        self.employee_id = employee_id

        if employee_id:
            try:
                user = User.objects.get(id=employee_id)

                if hasattr(user, 'profile') and user.profile:
                    # Auto-populate business_line
                    if user.profile.business_line_id:
                        self.business_line_id = user.profile.business_line_id
                        self.form.data['business_line'] = user.profile.business_line_id

                    # Auto-populate previous_supervisor
                    if user.profile.supervisor_id:
                        self.form.data['previous_supervisor'] = user.profile.supervisor_id

            except User.DoesNotExist:
                pass

    @event_handler
    def on_business_line_change(self, business_line_id):
        """
        Handle business line change.
        Filters executive_approver dropdown.

        Replaces: field_dependencies in React config
        """
        self.business_line_id = business_line_id
        # Form will re-render with filtered executive_approver options

    @event_handler
    def on_project_duration_change(self, duration):
        """
        Handle project duration change.
        Shows/hides expected_end_date field.

        Replaces: when() conditional logic
        """
        self.show_expected_end_date = (duration == 'over_12')

    @event_handler
    def on_needs_company_toggle(self, checked):
        """Toggle company change fields visibility"""
        self.show_company_fields = checked

    @event_handler
    def on_needs_supervisor_toggle(self, checked):
        """Toggle supervisor change fields visibility"""
        self.show_supervisor_fields = checked

    @event_handler
    def on_needs_projects_toggle(self, checked):
        """Toggle project information fields visibility"""
        self.show_project_fields = checked

    @event_handler
    def on_needs_meals_toggle(self, checked):
        """Toggle meals per diem fields visibility"""
        self.show_meals_fields = checked

    @event_handler
    def calculate_monthly_per_diem(self):
        """
        Calculate total monthly per diem.

        Replaces: FormField.with_calculation()
        """
        days = int(self.form.data.get('days_per_month_on_job_site', 0))
        rate = float(self.form.data.get('new_meals_per_diem_amount', 0))
        self.form.data['total_monthly_meals_per_diem'] = days * rate

    @event_handler
    def toggle_section(self, section_id):
        """Toggle section expansion (replaces React state)"""
        self.sections_expanded[section_id] = not self.sections_expanded.get(section_id, True)

    # ========================================================================
    # Form Submission
    # ========================================================================

    def form_valid(self, form):
        """Handle successful form submission"""
        # Save to database
        # StatusChange.objects.create(**form.cleaned_data)

        self.success_message = "Status change submitted successfully!"
        return True

    def form_invalid(self, form):
        """Handle form validation errors"""
        self.error_message = "Please correct the errors below"
        return False

    def get_context_data(self, **kwargs):
        """Provide template context"""
        context = super().get_context_data(**kwargs)
        context.update({
            'show_expected_end_date': self.show_expected_end_date,
            'show_company_fields': self.show_company_fields,
            'show_supervisor_fields': self.show_supervisor_fields,
            'show_project_fields': self.show_project_fields,
            'show_meals_fields': self.show_meals_fields,
            'sections_expanded': self.sections_expanded,
        })
        return context


# ============================================================================
# Key Differences: React Declarative vs Djust LiveView
# ============================================================================

"""
┌─────────────────────────────────────────────────────────────────────────┐
│ REACT APPROACH (Original)                                                │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. Define form structure in Python (FormSection/FormField)               │
│ 2. Convert to JSON via to_react_config()                                 │
│ 3. Send JSON to React frontend                                           │
│ 4. React renders form from JSON config                                   │
│ 5. Client-side state management                                          │
│ 6. Form submission sends data to backend API                             │
│                                                                           │
│ Pros:                                                                     │
│   - Declarative Python → React bridge                                    │
│   - Complex conditional UI logic                                         │
│   - Rich client-side interactions                                        │
│                                                                           │
│ Cons:                                                                     │
│   - Custom abstraction layer (FormSection/FormField)                     │
│   - JSON serialization complexity                                        │
│   - Client/server state sync                                             │
│   - Requires React expertise                                             │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ DJUST LIVEVIEW APPROACH                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ 1. Define Django Form (standard Django patterns)                         │
│ 2. Create LiveView class with FormMixin                                  │
│ 3. Add event handlers for interactivity                                  │
│ 4. Template renders form (server-side)                                   │
│ 5. User interactions trigger WebSocket events                            │
│ 6. Server updates state, re-renders, sends VDOM patches                  │
│                                                                           │
│ Pros:                                                                     │
│   - Standard Django Forms (familiar)                                     │
│   - No JSON config needed                                                │
│   - Server-side state management                                         │
│   - Real-time updates (~2-8ms latency)                                   │
│   - Python-only (no JavaScript required)                                 │
│                                                                           │
│ Cons:                                                                     │
│   - Less rich client-side interactions                                   │
│   - LiveView reactive features need WebSocket (but could fallback)       │
│   - More server resources per user (if using LiveView)                   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ FEATURE COMPARISON                                                        │
├──────────────────────────┬────────────────────┬────────────────────────┤
│ Feature                  │ React Declarative  │ Djust LiveView         │
├──────────────────────────┼────────────────────┼────────────────────────┤
│ Field Dependencies       │ JSON config        │ @event_handler         │
│ Conditional Visibility   │ when() chains      │ Python if/else         │
│ Auto-population          │ Callback functions │ @event_handler         │
│ Field Calculations       │ with_calculation() │ @event_handler         │
│ Validation               │ Client + Server    │ Server-side            │
│ Real-time Updates        │ React setState     │ WebSocket VDOM patches │
│ Form State               │ Client-side        │ Server-side            │
│ Bundle Size              │ Large (React)      │ Tiny (~5KB client.js)  │
│ Learning Curve           │ High               │ Low (Django devs)      │
└──────────────────────────┴────────────────────┴────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ WHEN TO USE EACH                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│ Use REACT DECLARATIVE when:                                               │
│   • Need offline support                                                  │
│   • Heavy client-side interactions (drag-drop, animations)                │
│   • Mobile app (React Native)                                             │
│   • Large forms with complex wizard flows                                 │
│   • Already have React expertise/infrastructure                           │
│                                                                           │
│ Use DJUST LIVEVIEW when:                                                  │
│   • Django-centric team                                                   │
│   • Server-side validation is critical                                    │
│   • Want real-time collaborative editing                                  │
│   • Minimize frontend complexity                                          │
│   • Internal tools (always online)                                        │
│   • Rapid prototyping                                                     │
└─────────────────────────────────────────────────────────────────────────┘
"""
