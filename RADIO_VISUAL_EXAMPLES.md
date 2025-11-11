# Radio Component Visual Examples

This document shows visual examples of the Radio component in different configurations.

## Basic Stacked Layout

```python
radio = Radio(
    name="size",
    label="Select Size",
    options=[
        {'value': 's', 'label': 'Small'},
        {'value': 'm', 'label': 'Medium'},
        {'value': 'l', 'label': 'Large'},
    ],
)
```

**Renders as:**
```
Select Size
◯ Small
◯ Medium
◯ Large
```

## Pre-selected Value

```python
radio = Radio(
    name="priority",
    label="Select Priority",
    options=[
        {'value': 'low', 'label': 'Low'},
        {'value': 'medium', 'label': 'Medium'},
        {'value': 'high', 'label': 'High'},
    ],
    value="medium"
)
```

**Renders as:**
```
Select Priority
◯ Low
◉ Medium  ← selected
◯ High
```

## Inline Layout

```python
radio = Radio(
    name="answer",
    label="Do you agree?",
    options=[
        {'value': 'yes', 'label': 'Yes'},
        {'value': 'no', 'label': 'No'},
    ],
    inline=True,
    value="yes"
)
```

**Renders as:**
```
Do you agree?
◉ Yes  ◯ No
```

## With Disabled Option

```python
radio = Radio(
    name="plan",
    label="Choose a Plan",
    options=[
        {'value': 'free', 'label': 'Free'},
        {'value': 'pro', 'label': 'Pro'},
        {'value': 'enterprise', 'label': 'Enterprise', 'disabled': True},
    ],
    value="free",
    help_text="Upgrade to Pro for advanced features"
)
```

**Renders as:**
```
Choose a Plan
◉ Free
◯ Pro
⊘ Enterprise  ← disabled
Upgrade to Pro for advanced features
```

## With Help Text

```python
radio = Radio(
    name="notifications",
    label="Email Notifications",
    options=[
        {'value': 'all', 'label': 'All'},
        {'value': 'important', 'label': 'Important Only'},
        {'value': 'none', 'label': 'None'},
    ],
    value="important",
    inline=True,
    help_text="How often would you like to receive email notifications?"
)
```

**Renders as:**
```
Email Notifications
◯ All  ◉ Important Only  ◯ None
How often would you like to receive email notifications?
```

## No Group Label (Minimal)

```python
radio = Radio(
    name="rating",
    options=[
        {'value': '1', 'label': '⭐'},
        {'value': '2', 'label': '⭐⭐'},
        {'value': '3', 'label': '⭐⭐⭐'},
        {'value': '4', 'label': '⭐⭐⭐⭐'},
        {'value': '5', 'label': '⭐⭐⭐⭐⭐'},
    ],
    value="5",
    inline=True
)
```

**Renders as:**
```
◯ ⭐  ◯ ⭐⭐  ◯ ⭐⭐⭐  ◯ ⭐⭐⭐⭐  ◉ ⭐⭐⭐⭐⭐
```

## Complex Example: Payment Method Selector

```python
radio = Radio(
    name="payment_method",
    label="Payment Method",
    options=[
        {'value': 'credit_card', 'label': 'Credit Card'},
        {'value': 'paypal', 'label': 'PayPal'},
        {'value': 'bank_transfer', 'label': 'Bank Transfer'},
        {'value': 'crypto', 'label': 'Cryptocurrency', 'disabled': True},
    ],
    value="credit_card",
    help_text="Choose your preferred payment method"
)
```

**Renders as:**
```
Payment Method
◉ Credit Card
◯ PayPal
◯ Bank Transfer
⊘ Cryptocurrency  ← disabled (coming soon)
Choose your preferred payment method
```

## Bootstrap 5 Styling

All examples use Bootstrap 5 classes:

- Group wrapper: `mb-3` (margin-bottom)
- Group label: `form-label`
- Radio wrapper: `form-check` (or `form-check form-check-inline` for inline)
- Radio input: `form-check-input`
- Radio label: `form-check-label`
- Help text: `form-text`

## Browser Appearance

When rendered in a Bootstrap 5 styled page, the radio buttons will have:

- **Circular buttons** with proper spacing
- **Blue accent color** when selected (Bootstrap primary)
- **Hover effects** for better UX
- **Disabled styling** (grayed out) for disabled options
- **Proper alignment** between radio buttons and labels
- **Responsive design** that works on mobile and desktop

## Accessibility Features

Each radio button includes:

- **Unique ID:** `{name}_0`, `{name}_1`, etc.
- **Associated label:** `<label for="{name}_0">`
- **Keyboard navigation:** Tab to move between options, Space to select
- **Screen reader support:** Proper semantic HTML
- **Disabled indication:** Via `disabled` attribute
- **Help text association:** Via proximity and `form-text` class

## Integration Example

Here's how the Radio component looks when integrated into a Django form:

```python
from djust import LiveView
from djust.components.ui import Radio

class SettingsView(LiveView):
    template_name = 'settings.html'

    def mount(self, request):
        self.theme_radio = Radio(
            name="theme",
            label="Theme Preference",
            options=[
                {'value': 'light', 'label': 'Light'},
                {'value': 'dark', 'label': 'Dark'},
                {'value': 'auto', 'label': 'Auto'},
            ],
            value="auto",
            inline=True,
            help_text="Choose your preferred color theme"
        )

    def get_context_data(self):
        return {
            'theme_radio': self.theme_radio,
        }
```

**Template (settings.html):**
```html
<div class="container">
    <h1>Settings</h1>
    <form @submit="save_settings">
        {{ theme_radio.render|safe }}
        <button type="submit" class="btn btn-primary">Save</button>
    </form>
</div>
```

**Renders as:**
```
Settings
───────────────────────────────────

Theme Preference
◯ Light  ◯ Dark  ◉ Auto
Choose your preferred color theme

[Save]
```

## Comparison: Stacked vs Inline

### Stacked (Default)
Best for:
- Long option labels
- Many options (5+)
- Vertical space available
- Mobile-first design

```
Size
◯ Extra Small (XS)
◯ Small (S)
◉ Medium (M)
◯ Large (L)
◯ Extra Large (XL)
```

### Inline
Best for:
- Short option labels
- Few options (2-4)
- Horizontal space available
- Desktop-optimized design

```
Size
◯ XS  ◯ S  ◉ M  ◯ L  ◯ XL
```

## Real-World Use Cases

### 1. Yes/No Questions
```python
Radio(name="agree", options=[
    {'value': 'yes', 'label': 'Yes'},
    {'value': 'no', 'label': 'No'}
], inline=True)
```
```
◉ Yes  ◯ No
```

### 2. Priority Levels
```python
Radio(name="priority", label="Priority", options=[
    {'value': 'low', 'label': 'Low'},
    {'value': 'medium', 'label': 'Medium'},
    {'value': 'high', 'label': 'High'}
])
```
```
Priority
◯ Low
◉ Medium
◯ High
```

### 3. Shipping Options
```python
Radio(name="shipping", label="Shipping Method", options=[
    {'value': 'standard', 'label': 'Standard (5-7 days)'},
    {'value': 'express', 'label': 'Express (2-3 days)'},
    {'value': 'overnight', 'label': 'Overnight (Next day)'}
])
```
```
Shipping Method
◉ Standard (5-7 days)
◯ Express (2-3 days)
◯ Overnight (Next day)
```

### 4. User Roles
```python
Radio(name="role", label="User Role", options=[
    {'value': 'viewer', 'label': 'Viewer'},
    {'value': 'editor', 'label': 'Editor'},
    {'value': 'admin', 'label': 'Admin', 'disabled': True}
], help_text="Contact support to become an admin")
```
```
User Role
◉ Viewer
◯ Editor
⊘ Admin
Contact support to become an admin
```

### 5. Rating/Survey
```python
Radio(name="satisfaction", label="How satisfied are you?", options=[
    {'value': '1', 'label': 'Very Unsatisfied'},
    {'value': '2', 'label': 'Unsatisfied'},
    {'value': '3', 'label': 'Neutral'},
    {'value': '4', 'label': 'Satisfied'},
    {'value': '5', 'label': 'Very Satisfied'}
])
```
```
How satisfied are you?
◯ Very Unsatisfied
◯ Unsatisfied
◉ Neutral
◯ Satisfied
◯ Very Satisfied
```
