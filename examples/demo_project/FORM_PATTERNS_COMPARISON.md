# Form Patterns: React Declarative vs Djust LiveView

## Overview

This document compares the **React Declarative Form System** (status_change_form.py) with **Djust LiveView** patterns for building complex, reactive forms.

---

## Pattern 1: Field Dependencies (Filtered Dropdowns)

### React Declarative Approach

```python
# status_change_form.py
FormSection("employee_information")
    .add_field(
        "executive_approver",
        FormField(
            size="large",
            label="Executive Approver",
            depends_on="business_line",  # Declarative dependency
            filter_by="executive_approvers,secondary_executive_approvers"
        ).make_required()
    )

# Field dependencies config
@staticmethod
def get_related_fields() -> Dict[str, Dict[str, Any]]:
    return {
        "executive_approver": {
            "field": "business_line",
            "related_method": "get_related_executive_approvers_from_business_line",
        },
    }

# React receives JSON config and handles filtering client-side
```

### Djust LiveView Approach

```python
# forms_djust_example.py
class StatusChangeForm(forms.Form):
    business_line = forms.ChoiceField(...)
    executive_approver = forms.ModelChoiceField(
        queryset=User.objects.none()  # Filtered dynamically
    )

    def __init__(self, *args, **kwargs):
        self.business_line_id = kwargs.pop('business_line_id', None)
        super().__init__(*args, **kwargs)

        # Filter executive_approver based on business_line
        if self.business_line_id:
            self.fields['executive_approver'].queryset = User.objects.filter(
                businessline__id=self.business_line_id
            )

class StatusChangeFormView(FormMixin, LiveView):
    @event_handler
    def on_business_line_change(self, business_line_id):
        """Real-time filtering via WebSocket"""
        self.business_line_id = business_line_id
        # Form re-renders with filtered options
```

**Key Difference:**
- React: Client-side filtering from preloaded data
- Djust: Server-side filtering with real-time updates

---

## Pattern 2: Auto-Population on Field Change

### React Declarative Approach

```python
# status_change_form.py
def handle_employee_change(instance, old_value, new_value):
    """Handler called when employee field changes."""
    populated = {}

    if new_value:
        user = User.objects.get(id=new_value)
        if hasattr(user, 'profile') and user.profile:
            populated['business_line'] = user.profile.business_line_id
            populated['previous_supervisor'] = user.profile.supervisor_id

    return populated

# In form definition
FormField(label="Employee")
    .make_required()
    .on_change(handle_employee_change)  # Callback registered
```

### Djust LiveView Approach

```python
# forms_djust_example.py
class StatusChangeFormView(FormMixin, LiveView):
    @event_handler
    def on_employee_change(self, employee_id):
        """Handle employee selection change."""
        self.employee_id = employee_id

        if employee_id:
            user = User.objects.get(id=employee_id)
            if hasattr(user, 'profile') and user.profile:
                # Auto-populate via form data
                self.form.data['business_line'] = user.profile.business_line_id
                self.form.data['previous_supervisor'] = user.profile.supervisor_id

        # Form re-renders with updated values over WebSocket
```

**Key Difference:**
- React: Callback returns dict, client updates form state
- Djust: Event handler updates server state, pushes VDOM patches

---

## Pattern 3: Conditional Field Visibility

### React Declarative Approach

```python
# status_change_form.py
FormSection(
    title="Add Project Information",
    needs_field="needs_projects",  # Controls section visibility
)
.add_field("needs_projects", FormField(label="Add Project Information?"))
.add_field(
    "expected_end_date",
    FormField(size="large")
    .with_label("Expected End Date")
    .when(
        when("project_duration").contains(
            ["Exceeds 12 Months"],
            label="Project Expected End Date",
            required=True,
        )
    )
)
```

### Djust LiveView Approach

```python
# forms_djust_example.py
class StatusChangeFormView(FormMixin, LiveView):
    def mount(self, request, **kwargs):
        self.show_project_fields = False
        self.show_expected_end_date = False

    @event_handler
    def on_needs_projects_toggle(self, checked):
        self.show_project_fields = checked

    @event_handler
    def on_project_duration_change(self, duration):
        self.show_expected_end_date = (duration == 'over_12')

# Template
{% if show_project_fields %}
    <div class="row">
        {% if show_expected_end_date %}
            {{ form.expected_end_date }}
        {% endif %}
    </div>
{% endif %}
```

**Key Difference:**
- React: Declarative `.when()` chains in Python → evaluated client-side
- Djust: Imperative Python logic + template conditionals

---

## Pattern 4: Field Calculations

### React Declarative Approach

```python
# status_change_form.py
FormField(size="large")
    .with_label("Total Monthly Meals Per Diem")
    .with_calculation(
        "(data.days_per_month_on_job_site || 0) * (data.new_meals_per_diem_amount || 0)",
        depends_on=[
            "days_per_month_on_job_site",
            "new_meals_per_diem_amount",
        ],
    )
```

### Djust LiveView Approach

```python
# forms_djust_example.py
@event_handler
def calculate_monthly_per_diem(self):
    """Calculate total monthly per diem."""
    days = int(self.form.data.get('days_per_month_on_job_site', 0))
    rate = float(self.form.data.get('new_meals_per_diem_amount', 0))
    self.form.data['total_monthly_meals_per_diem'] = days * rate

# Template triggers calculation
<input type="number" @input="calculate_monthly_per_diem">
```

**Key Difference:**
- React: JavaScript expression evaluated client-side
- Djust: Python function triggered via WebSocket

---

## Pattern 5: ToFrom Section Layout

### React Declarative Approach

```python
# status_change_form.py
FormSection(
    title="Change Supervisor",
    section_type="tofrom",  # Special React component
)
.add_field("previous_supervisor", FormField(label="Previous Supervisor"))
.add_field("new_supervisor", FormField(label="New Supervisor").make_highlighted())

# React renders with special ToFrom component layout
```

### Djust LiveView Approach

```html
<!-- status_change_djust.html -->
<div class="tofrom-layout">
    <div class="tofrom-previous">
        <label>Previous Supervisor</label>
        {{ form.previous_supervisor }}
    </div>
    <div class="tofrom-new">
        <label>New Supervisor *</label>
        {{ form.new_supervisor }}
    </div>
</div>

<style>
.tofrom-layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
}
.tofrom-new {
    background: #e7f3ff;
    border: 2px solid #0d6efd;
}
</style>
```

**Key Difference:**
- React: `section_type="tofrom"` triggers specialized component
- Djust: CSS Grid layout in template

---

## Pattern 6: Complex Conditional Validation

### React Declarative Approach

```python
# status_change_form.py
@staticmethod
def get_validation_rules() -> Dict[str, Any]:
    return {
        "always_required": ["effective_date", "employee"],
        "conditional_required": {
            "new_company": "needs_company === true",
            "new_supervisor": "needs_supervisor === true",
            "health_and_welfare": "needs_sca_db === true && sca === true"
        }
    }

# Sent to React as JSON, evaluated client-side + server-side validation
```

### Djust LiveView Approach

```python
# forms_djust_example.py
class StatusChangeForm(forms.Form):
    def clean(self):
        """Django form validation"""
        cleaned_data = super().clean()

        # Conditional required fields
        if cleaned_data.get('needs_company'):
            if not cleaned_data.get('new_company'):
                self.add_error('new_company', 'Required when changing company')

        if cleaned_data.get('needs_supervisor'):
            if not cleaned_data.get('new_supervisor'):
                self.add_error('new_supervisor', 'Required when changing supervisor')

        return cleaned_data
```

**Key Difference:**
- React: JSON validation rules evaluated both sides
- Djust: Standard Django form validation (server-only)

---

## Pattern 7: Section Expansion State

### React Declarative Approach

```python
# status_change_form.py
FormSection(
    title="Employee Information",
    icon="fas fa-user",
    expanded=True,  # Initial state
)

# React manages expansion state client-side
```

### Djust LiveView Approach

```python
# forms_djust_example.py
class StatusChangeFormView(FormMixin, LiveView):
    def mount(self, request, **kwargs):
        self.sections_expanded = {
            'employee_info': True,
            'company_changes': True,
        }

    @event_handler
    def toggle_section(self, section_id):
        self.sections_expanded[section_id] = not self.sections_expanded[section_id]

# Template
<div class="section-header" @click="toggle_section('employee_info')">
    <i class="fas fa-chevron-{{ sections_expanded.employee_info|yesno:'up,down' }}"></i>
</div>
```

**Key Difference:**
- React: Client-side state management
- Djust: Server-side state synced via WebSocket

---

## Summary Comparison Table

| Feature | React Declarative | Djust LiveView |
|---------|------------------|----------------|
| **Form Definition** | Custom FormSection/FormField | Django Forms |
| **Conditional Logic** | `.when()` chains | Python if/else + template |
| **Field Dependencies** | JSON config | Event handlers |
| **Auto-population** | Callback functions | @event_handler |
| **Calculations** | JavaScript expressions | Python functions |
| **Validation** | Client + Server | Server-only |
| **State Management** | React client-side | Server-side |
| **Real-time Updates** | React setState | WebSocket VDOM |
| **Bundle Size** | Large (React + config) | Tiny (~5KB) |
| **Abstraction Level** | High | Low (standard Django) |
| **Learning Curve** | Steep | Gentle |
| **Flexibility** | Very flexible UI | More constrained |

---

## When to Choose Each

### Choose React Declarative When:

✅ Need offline support
✅ Heavy client-side interactions (drag-drop, animations)
✅ Mobile app (React Native compatibility)
✅ Complex wizard flows with branching logic
✅ Team already has React expertise
✅ Need maximum UI flexibility

### Choose Djust LiveView When:

✅ Django-centric team
✅ Server-side validation is critical
✅ Want collaborative real-time editing
✅ Minimize frontend complexity
✅ Internal tools (always online)
✅ Rapid prototyping
✅ Want to use standard Django patterns

---

## Performance Comparison

### React Declarative
- Initial page load: ~300-500ms (React bundle + hydration)
- Interactions: <16ms (60fps client-side)
- Validation: Client-side instant, server round-trip on submit

### Djust LiveView
- Initial page load: ~50-100ms (server render + ~5KB client)
- Interactions: ~2-8ms (WebSocket VDOM patches)
- Validation: Real-time server-side on every change

**Winner depends on use case:**
- **Complex UI interactions**: React wins
- **Form validation & data consistency**: Djust wins
- **Initial load time**: Djust wins
- **Offline support**: React wins

---

## Code Organization Comparison

### React Declarative Structure
```
project/
├── forms/
│   ├── status_change_form.py        # Form definition
│   └── base.py                       # FormSection/FormField classes
├── views/
│   └── api.py                        # to_react_config() endpoint
└── frontend/
    └── components/
        ├── FormRenderer.tsx          # Generic form renderer
        ├── ToFromSection.tsx         # Section component
        └── NeedsSection.tsx          # Section component
```

### Djust LiveView Structure
```
project/
├── forms.py                          # Django forms (standard)
├── views/
│   └── forms_djust_example.py       # LiveView classes
└── templates/
    └── forms/
        └── status_change_djust.html  # Template
```

**Lines of Code:**
- React: ~2000 LOC (Python definition + React components)
- Djust: ~800 LOC (Django form + LiveView + template)

---

## Conclusion

Both approaches solve the same problem differently:

**React Declarative**: Build complex forms with custom Python DSL → JSON → React
**Djust LiveView**: Build complex forms with standard Django → LiveView → WebSocket

Choose based on your team's expertise, requirements, and architectural preferences.
