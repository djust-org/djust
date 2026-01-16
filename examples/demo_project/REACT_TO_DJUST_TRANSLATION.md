# React Declarative Forms → Djust LiveView Translation Guide

Complete guide for translating your `status_change_form.py` React Declarative Form system to Djust LiveView.

---

## Key Translation Patterns

### 1. FormSection/FormField → Django Forms

**React Declarative:**
```python
FormSection("employee_information")
    .add_field("employee", FormField(size="large").make_required())
```

**Djust LiveView:**
```python
class StatusChangeForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=User.objects.all(),
        required=True,
        widget=forms.Select(attrs={'class': 'form-select form-select-lg'})
    )
```

**Key Difference:** Use standard Django Forms instead of custom FormSection/FormField DSL.

---

### 2. Conditional Logic → Event Handlers

**React Declarative:**
```python
.when(when("project_duration").contains(["Exceeds 12 Months"], required=True))
```

**Djust LiveView:**
```python
@event_handler
def on_project_duration_change(self, duration):
    self.show_expected_end_date = (duration == 'over_12')
```

**Template:**
```html
{% if show_expected_end_date %}
    <div class="col-md-6">
        <label>Expected End Date *</label>
        {{ form.expected_end_date }}
    </div>
{% endif %}
```

**Key Difference:** `.when()` chains become event handlers + template conditionals.

---

### 3. Field Dependencies → Real-time Filtering

**React Declarative:**
```python
FormField(
    label="Executive Approver",
    depends_on="business_line",  # Declarative dependency
    filter_by="executive_approvers,secondary_executive_approvers"
)

# Separate config
@staticmethod
def get_related_fields():
    return {
        "executive_approver": {
            "field": "business_line",
            "related_method": "get_related_executive_approvers_from_business_line",
        },
    }
```

**Djust LiveView:**
```python
class StatusChangeForm(forms.Form):
    def __init__(self, *args, **kwargs):
        self.business_line_id = kwargs.pop('business_line_id', None)
        super().__init__(*args, **kwargs)

        # Filter based on business_line
        if self.business_line_id:
            self.fields['executive_approver'].queryset = User.objects.filter(
                businessline__id=self.business_line_id
            )

class StatusChangeFormView(FormMixin, LiveView):
    @event_handler
    def on_business_line_change(self, business_line_id):
        """Real-time filtering via WebSocket"""
        self.business_line_id = business_line_id
        # Form re-renders with filtered executive_approver options
```

**Key Difference:** Declarative `depends_on` becomes event handler + queryset filtering.

---

### 4. Auto-Population → Event Handlers

**React Declarative:**
```python
def handle_employee_change(instance, old_value, new_value):
    """Handler called when employee field changes."""
    populated = {}

    if new_value:
        user = User.objects.get(id=new_value)
        if hasattr(user, 'profile'):
            populated['business_line'] = user.profile.business_line_id
            populated['previous_supervisor'] = user.profile.supervisor_id

    return populated

# In form definition
FormField(label="Employee")
    .make_required()
    .on_change(handle_employee_change)
```

**Djust LiveView:**
```python
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

**Key Difference:** Callback returns dict → Event handler updates form.data directly.

---

### 5. Field Calculations → Event Handlers

**React Declarative:**
```python
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

**Djust LiveView:**
```python
@event_handler
def calculate_monthly_per_diem(self):
    """Calculate total monthly per diem."""
    days = int(self.form.data.get('days_per_month_on_job_site', 0))
    rate = float(self.form.data.get('new_meals_per_diem_amount', 0))
    self.form.data['total_monthly_meals_per_diem'] = days * rate
```

**Template:**
```html
<input type="number" name="days_per_month_on_job_site"
       @input="calculate_monthly_per_diem">
<input type="number" name="new_meals_per_diem_amount"
       @input="calculate_monthly_per_diem">
<input type="number" name="total_monthly_meals_per_diem" readonly>
```

**Key Difference:** JavaScript expression → Python function triggered via @input.

---

### 6. ToFrom Section Layout

**React Declarative:**
```python
FormSection(
    title="Change Supervisor",
    section_type="tofrom",  # Special React component
)
.add_field("previous_supervisor", FormField(label="Previous Supervisor"))
.add_field("new_supervisor", FormField(label="New Supervisor").make_highlighted())
```

**Djust LiveView:**
```html
<div class="tofrom-layout">
    <div class="tofrom-previous">
        <label>Previous Supervisor</label>
        {{ form.previous_supervisor }}
        <small class="text-muted">Auto-populated</small>
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
.tofrom-previous {
    background: #f8f9fa;
    padding: 1rem;
}
.tofrom-new {
    background: #e7f3ff;
    border: 2px solid #0d6efd;
}
</style>
```

**Key Difference:** `section_type="tofrom"` → CSS Grid layout in template.

---

### 7. Conditional Validation

**React Declarative:**
```python
@staticmethod
def get_validation_rules():
    return {
        "always_required": ["effective_date", "employee"],
        "conditional_required": {
            "new_company": "needs_company === true",
            "new_supervisor": "needs_supervisor === true",
            "health_and_welfare": "needs_sca_db === true && sca === true"
        }
    }
```

**Djust LiveView:**
```python
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

        if cleaned_data.get('needs_sca_db') and cleaned_data.get('sca'):
            if not cleaned_data.get('health_and_welfare'):
                self.add_error('health_and_welfare', 'Required for SCA employees')

        return cleaned_data
```

**Key Difference:** JSON validation rules → Django Form.clean() method.

---

### 8. Section Expansion State

**React Declarative:**
```python
FormSection(
    title="Employee Information",
    icon="fas fa-user",
    expanded=True,  # Initial state
)
```

**Djust LiveView:**
```python
class StatusChangeFormView(FormMixin, LiveView):
    def mount(self, request, **kwargs):
        self.sections_expanded = {
            'employee_info': True,
            'company_changes': True,
            'supervisor_changes': True,
        }

    @event_handler
    def toggle_section(self, section_id):
        self.sections_expanded[section_id] = not self.sections_expanded[section_id]
```

**Template:**
```html
<div class="section-header" @click="toggle_section('employee_info')">
    <i class="fas fa-user"></i>
    <h5>Employee Information</h5>
    <i class="fas fa-chevron-{{ sections_expanded.employee_info|yesno:'up,down' }}"></i>
</div>

{% if sections_expanded.employee_info %}
<div class="card-body">
    <!-- Section content -->
</div>
{% endif %}
```

**Key Difference:** Client-side React state → Server-side state synced via WebSocket.

---

## The Big Differences Summary

| Aspect | React Declarative | Djust LiveView |
|--------|------------------|----------------|
| **Form Definition** | Custom FormSection/FormField DSL | Standard Django Forms |
| **Conditional Logic** | `.when()` chains | Event handlers + template conditionals |
| **Field Dependencies** | JSON config (`depends_on`) | Event handlers + queryset filtering |
| **Auto-population** | Callback functions returning dict | Event handlers updating form.data |
| **Calculations** | JavaScript expressions | Python functions |
| **Validation** | JSON rules (client + server) | Django Form.clean() (server-only) |
| **State Management** | React client-side | Server-side synced via WebSocket |
| **Updates** | React setState | WebSocket VDOM patches |
| **Code Volume** | ~2000 LOC | ~800 LOC |
| **Learning Curve** | High (React + custom DSL) | Low (if you know Django) |
| **Bundle Size** | Large (React + config) | Tiny (~5KB client.js) |
| **Abstraction Level** | High | Low (explicit) |

---

## Your Specific Features: All Supported! ✅

### ✅ ToFrom Sections
- **React**: `section_type="tofrom"` → Special component
- **Djust**: CSS Grid layout

### ✅ Needs Sections
- **React**: `needs_field="needs_projects"` → Visibility logic
- **Djust**: Boolean toggles + `{% if show_project_fields %}`

### ✅ Auto-Population
- **React**: `.on_change(handle_employee_change)` callback
- **Djust**: `@event_handler` updates form.data

### ✅ Field Dependencies
- **React**: `depends_on="business_line"` + `get_related_fields()`
- **Djust**: Event handler filters queryset dynamically

### ✅ Complex Conditionals
- **React**: Nested `.when()` chains
- **Djust**: Python if/else + template `{% if %}`

### ✅ Real-time Calculations
- **React**: `with_calculation()` JavaScript expressions
- **Djust**: `@event_handler` Python functions

### ✅ Section Expansion
- **React**: React state management
- **Djust**: Server-side state + `toggle_section()`

### ✅ Validation
- **React**: JSON validation rules
- **Djust**: Django `Form.clean()` method

---

## Tradeoffs

### React Declarative Pros
- ✅ Declarative Python → React bridge
- ✅ Complex conditional UI logic
- ✅ Rich client-side interactions
- ✅ Offline support
- ✅ Mobile app (React Native)

### React Declarative Cons
- ❌ Custom abstraction layer
- ❌ JSON serialization complexity
- ❌ Client/server state sync
- ❌ Requires React expertise
- ❌ Large bundle size

### Djust LiveView Pros
- ✅ Standard Django Forms (familiar)
- ✅ No JSON config needed
- ✅ Server-side state management
- ✅ Real-time updates (~2-8ms)
- ✅ Python-only (no JS required)
- ✅ Tiny bundle (~5KB)
- ✅ Simple mental model

### Djust LiveView Cons
- ❌ Less rich client-side interactions
- ❌ LiveView reactive features need WebSocket
- ❌ More server resources per user
- ❌ No offline support

---

## Performance Comparison

### Initial Page Load
- **React Declarative**: ~300-500ms (React bundle + hydration)
- **Djust LiveView**: ~50-100ms (server render + ~5KB client)
- **Winner**: Djust LiveView

### Interactions
- **React Declarative**: <16ms (60fps client-side)
- **Djust LiveView**: ~2-8ms (WebSocket VDOM patches)
- **Winner**: Djust LiveView

### Validation
- **React Declarative**: Instant client-side, round-trip on submit
- **Djust LiveView**: Real-time server-side on every change
- **Winner**: Tie (different approaches)

### Complex UI Interactions
- **React Declarative**: Excellent (drag-drop, animations)
- **Djust LiveView**: Limited
- **Winner**: React Declarative

### Offline Support
- **React Declarative**: Supported
- **Djust LiveView**: Requires connection
- **Winner**: React Declarative

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

## Code Organization

### React Declarative Structure
```
project/
├── forms/
│   ├── status_change_form.py        # Form definition (~600 LOC)
│   └── base.py                       # FormSection/FormField (~400 LOC)
├── views/
│   └── api.py                        # to_react_config() endpoint (~200 LOC)
└── frontend/
    └── components/
        ├── FormRenderer.tsx          # Generic renderer (~400 LOC)
        ├── ToFromSection.tsx         # Section component (~200 LOC)
        └── NeedsSection.tsx          # Section component (~200 LOC)

Total: ~2000 LOC
```

### Djust LiveView Structure
```
project/
├── forms.py                          # Django forms (~300 LOC)
├── views/
│   └── forms_djust_example.py       # LiveView classes (~300 LOC)
└── templates/
    └── forms/
        └── status_change_djust.html  # Template (~200 LOC)

Total: ~800 LOC
```

**Code Reduction**: ~60% less code with Djust LiveView

---

## Migration Path

If you're migrating from React Declarative to Djust LiveView:

### Step 1: Convert FormSection/FormField to Django Forms
```python
# From:
FormSection("employee_info").add_field("employee", FormField(...))

# To:
class StatusChangeForm(forms.Form):
    employee = forms.ModelChoiceField(...)
```

### Step 2: Convert `.when()` chains to event handlers
```python
# From:
.when(when("field").contains(["value"], required=True))

# To:
@event_handler
def on_field_change(self, value):
    self.show_dependent_field = (value == 'value')
```

### Step 3: Convert callbacks to event handlers
```python
# From:
def handle_change(instance, old, new):
    return {'field': new_value}

# To:
@event_handler
def on_change(self, value):
    self.form.data['field'] = new_value
```

### Step 4: Convert validation rules to Form.clean()
```python
# From:
"conditional_required": {"field": "condition === true"}

# To:
def clean(self):
    if self.cleaned_data.get('condition'):
        if not self.cleaned_data.get('field'):
            self.add_error('field', 'Required')
```

### Step 5: Create template with conditionals
```html
{% if show_field %}
    {{ form.field }}
{% endif %}
```

---

## Complete Example Files

See these files for complete working implementations:

1. **`forms_djust_example.py`** - Full LiveView implementation with all patterns
2. **`templates/forms/status_change_djust.html`** - Complete template
3. **`FORM_PATTERNS_COMPARISON.md`** - Detailed pattern-by-pattern comparison

---

## Conclusion

Both approaches solve complex form requirements:

- **React Declarative**: Custom Python DSL → JSON → React → Complex UI
- **Djust LiveView**: Standard Django → LiveView → WebSocket → Real-time

Choose based on:
- Team expertise (React vs Django)
- Requirements (offline, mobile, rich UI vs server-side validation)
- Architectural preferences (client-side vs server-side state)

For most Django teams building internal tools, **Djust LiveView offers a simpler, more maintainable path** with real-time reactivity and standard Django patterns.
