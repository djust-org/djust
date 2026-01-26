# Rust Components

High-performance UI components backed by Rust with a Pythonic API, similar to JustPy and NiceGUI but integrated with djust's reactive system.

## Overview

Rust components provide:
- **10-100x faster rendering** through Rust implementation
- **Type-safe HTML generation** with auto-escaping
- **Framework-agnostic** - renders to Bootstrap5, Tailwind, or Plain HTML
- **Pythonic API** with both kwargs and builder pattern
- **LiveView integration** for reactive updates

## Architecture

```
┌─────────────────────────────────────┐
│  Python (djust.rust_components)     │
│  - Pythonic API                     │
│  - Type hints                       │
│  - Builder pattern                  │
└─────────────────────────────────────┘
            ↕️ PyO3 Bindings
┌─────────────────────────────────────┐
│  Rust (djust_components)            │
│  - Component trait                  │
│  - HtmlBuilder (type-safe)          │
│  - Framework renderers              │
└─────────────────────────────────────┘
```

## Quick Start

### Basic Usage

```python
from djust.rust_components import Button

# Simple button
btn = Button("my-btn", "Click Me")
html = btn.render()
# Output: <button class="btn btn-primary" type="button" id="my-btn">Click Me</button>

# With options
btn = Button(
    "submit-btn",
    "Submit Form",
    variant="success",
    size="large",
    on_click="handleSubmit"
)
```

### Builder Pattern

```python
btn = Button("builder-btn", "Save") \
    .with_variant("success") \
    .with_size("lg") \
    .with_icon("💾")

html = btn.render()
```

### Framework-Specific Rendering

```python
btn = Button("fw-btn", "Test", variant="primary")

# Render with specific framework
bootstrap_html = btn.render_with_framework("bootstrap5")
tailwind_html = btn.render_with_framework("tailwind")
plain_html = btn.render_with_framework("plain")
```

## Components

### Button

A versatile button component with multiple variants and states.

#### Parameters

- **id** (str, required): Unique component ID
- **label** (str, required): Button text
- **variant** (str, default="primary"): Button style
  - Options: `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`, `link`
- **size** (str, default="medium"): Button size
  - Options: `sm`/`small`, `md`/`medium`, `lg`/`large`
- **outline** (bool, default=False): Use outline style
- **disabled** (bool, default=False): Disable the button
- **full_width** (bool, default=False): Make button full width
- **icon** (str, optional): Icon HTML or text to prepend
- **on_click** (str, optional): Click event handler name
- **button_type** (str, default="button"): HTML button type

#### Properties

```python
# Get/set label
btn.label = "New Label"
print(btn.label)

# Get/set disabled state
btn.disabled = True
print(btn.disabled)

# Get component ID (read-only)
print(btn.id)
```

#### Methods

```python
# Render methods
btn.render()  # Auto-detects framework from config
btn.render_with_framework("bootstrap5")

# Variant setter
btn.set_variant("danger")

# Builder methods (chainable)
btn.with_variant("success")
btn.with_size("lg")
btn.with_outline(True)
btn.with_disabled(False)
btn.with_icon("✓")
btn.with_on_click("handleClick")
```

#### Examples

**Primary Button**
```python
btn = Button("primary", "Primary Button", variant="primary")
# <button class="btn btn-primary" type="button" id="primary">Primary Button</button>
```

**Success Button with Icon**
```python
btn = Button("save", "Save", variant="success", icon="💾", size="lg")
# <button class="btn btn-success btn-lg" type="button" id="save">💾 Save</button>
```

**Disabled Danger Button**
```python
btn = Button("delete", "Delete", variant="danger", disabled=True)
# <button class="btn btn-danger" type="button" id="delete" disabled="disabled">Delete</button>
```

**Full Width Submit**
```python
btn = Button(
    "submit",
    "Submit Form",
    variant="primary",
    size="large",
    full_width=True,
    on_click="submitForm",
    button_type="submit"
)
# <button class="btn btn-primary btn-lg w-100" type="submit" id="submit" dj-click="submitForm">Submit Form</button>
```

**Outline Button**
```python
btn = Button("outline", "Outline", variant="info", outline=True)
# <button class="btn btn-outline-info" type="button" id="outline">Outline</button>
```

**Builder Pattern**
```python
btn = Button("chain", "Chained") \
    .with_variant("warning") \
    .with_size("sm") \
    .with_icon("⚠️") \
    .with_on_click("showWarning")
# <button class="btn btn-warning btn-sm" type="button" id="chain" dj-click="showWarning">⚠️ Chained</button>
```

### Input

A versatile input component supporting multiple types, sizes, and validation states.

#### Parameters

- **id** (str, required): Unique component ID
- **inputType** (str, default="text"): HTML input type
  - Options: `text`, `email`, `password`, `number`, `tel`, `url`, `search`, `date`, `time`, `datetime`, `color`, `file`
- **size** (str, default="medium"): Input size
  - Options: `sm`/`small`, `md`/`medium`, `lg`/`large`
- **name** (str, optional): Form field name
- **value** (str, optional): Input value
- **placeholder** (str, optional): Placeholder text
- **disabled** (bool, default=False): Disable the input
- **readonly** (bool, default=False): Make input read-only
- **required** (bool, default=False): Mark as required
- **on_input** (str, optional): Input event handler name
- **on_change** (str, optional): Change event handler name
- **on_blur** (str, optional): Blur event handler name
- **min** (str, optional): Minimum value (for number/date types)
- **max** (str, optional): Maximum value (for number/date types)
- **step** (str, optional): Step value (for number type)

#### Properties

```python
# Get/set value
input.value = "new value"
print(input.value)

# Get/set disabled state
input.disabled = True
print(input.disabled)

# Get component ID (read-only)
print(input.id)
```

#### Methods

```python
# Render methods
input.render()  # Auto-detects framework from config
input.render_with_framework("bootstrap5")

# Setter methods
input.set_value("new value")
input.set_disabled(True)
```

#### Examples

**Basic Text Input**
```python
from djust.rust_components import Input

input = Input("username", placeholder="Enter username")
# <input class="form-control" type="text" id="username" placeholder="Enter username" />
```

**Email Input with Value**
```python
input = Input(
    "email",
    inputType="email",
    placeholder="user@example.com",
    value="john@example.com",
    required=True
)
# <input class="form-control" type="email" id="email" placeholder="user@example.com" value="john@example.com" required="required" />
```

**Password Input**
```python
input = Input(
    "password",
    inputType="password",
    placeholder="Enter password",
    required=True,
    on_change="validatePassword"
)
# <input class="form-control" type="password" id="password" placeholder="Enter password" required="required" dj-change="validatePassword" />
```

**Number Input with Range**
```python
input = Input(
    "age",
    inputType="number",
    min="18",
    max="100",
    step="1",
    value="25"
)
# <input class="form-control" type="number" id="age" value="25" min="18" max="100" step="1" />
```

**Large Search Input**
```python
input = Input(
    "search",
    inputType="search",
    size="lg",
    placeholder="Search...",
    on_input="handleSearch"
)
# <input class="form-control form-control-lg" type="search" id="search" placeholder="Search..." dj-input="handleSearch" />
```

**Disabled Input**
```python
input = Input(
    "readonly-field",
    value="Cannot edit this",
    disabled=True
)
# <input class="form-control" type="text" id="readonly-field" value="Cannot edit this" disabled="disabled" />
```

### Text

A versatile text/typography component for labels, headings, paragraphs, and spans.

#### Parameters

- **content** (str, required): Text content to display
- **element** (str, default="span"): HTML element type
  - Options: `span`, `p`/`paragraph`, `label`, `div`, `h1`, `h2`, `h3`, `h4`, `h5`, `h6`
- **variant** (str, default="body"): Typography variant
  - Options: `heading`, `body`, `caption`, `overline`, `lead`
- **color** (str, optional): Text color
  - Options: `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`, `muted`
- **align** (str, optional): Text alignment
  - Options: `left`, `center`, `right`, `justify`
- **weight** (str, default="normal"): Font weight
  - Options: `normal`, `bold`, `light`
- **italic** (bool, default=False): Italic text
- **forInput** (str, optional): Input ID (for label elements)
- **id** (str, optional): Component ID

#### Properties

```python
# Get/set content
text.content = "New content"
print(text.content)
```

#### Methods

```python
# Render methods
text.render()  # Auto-detects framework from config
text.render_with_framework("bootstrap5")

# Setter method
text.set_content("New content")
```

#### Examples

**Basic Paragraph**
```python
from djust.rust_components import Text

text = Text("Hello, World!", element="p")
# <p>Hello, World!</p>
```

**Heading**
```python
text = Text("Page Title", element="h1")
# <h1>Page Title</h1>
```

**Label for Input**
```python
label = Text("Email Address", element="label", forInput="email-input")
# <label for="email-input">Email Address</label>
```

**Colored Text**
```python
text = Text("Success message", color="success")
# <span class="text-success">Success message</span>
```

**Bold Text**
```python
text = Text("Important!", weight="bold", color="danger")
# <span class="fw-bold text-danger">Important!</span>
```

**Lead Text**
```python
text = Text(
    "This is a lead paragraph that stands out.",
    element="p",
    variant="lead"
)
# <p class="lead">This is a lead paragraph that stands out.</p>
```

**Centered Heading**
```python
text = Text(
    "Welcome",
    element="h2",
    align="center",
    color="primary"
)
# <h2 class="text-center text-primary">Welcome</h2>
```

**Muted Caption**
```python
text = Text(
    "Posted 5 minutes ago",
    element="span",
    variant="caption",
    color="muted"
)
# <span class="text-muted">Posted 5 minutes ago</span>
```

### Card

A content card component with header, body, and footer sections.

#### Parameters

- **body** (str, required): Card body content
- **header** (str, optional): Card header content
- **footer** (str, optional): Card footer content
- **variant** (str, default="default"): Card style variant
  - Options: `default`, `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`
- **border** (bool, default=True): Show card border
- **shadow** (bool, default=False): Add shadow effect
- **id** (str, optional): Component ID

#### Properties

```python
# Get/set body content
card.body = "New body content"
print(card.body)
```

#### Methods

```python
# Render methods
card.render()  # Auto-detects framework from config
card.render_with_framework("bootstrap5")
```

#### Examples

**Simple Card**
```python
from djust.rust_components import Card

card = Card(body="Card content")
# Renders card with default styling
```

**Card with Header and Footer**
```python
card = Card(
    body="Main content here",
    header="Card Title",
    footer="Last updated: Today"
)
```

**Success Card with Shadow**
```python
card = Card(
    body="Operation completed successfully!",
    header="Success",
    variant="success",
    shadow=True
)
```

**Primary Card**
```python
card = Card(
    body="Important information",
    header="Notice",
    footer="<button>Learn More</button>",
    variant="primary"
)
```

### Alert

An alert/notification component with dismissible option and multiple variants.

#### Parameters

- **message** (str, required): Alert message text
- **variant** (str, default="info"): Alert style variant
  - Options: `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`
- **dismissible** (bool, default=False): Make alert dismissible
- **icon** (str, optional): Icon HTML or text to prepend
- **id** (str, optional): Component ID

#### Properties

```python
# Get/set message
alert.message = "Updated message"
print(alert.message)
```

#### Methods

```python
# Render methods
alert.render()  # Auto-detects framework from config
alert.render_with_framework("bootstrap5")
```

#### Examples

**Basic Info Alert**
```python
from djust.rust_components import Alert

alert = Alert(message="This is an info message")
# <div class="alert alert-info">This is an info message</div>
```

**Success Alert**
```python
alert = Alert(message="Saved successfully!", variant="success")
```

**Danger Alert with Icon**
```python
alert = Alert(
    message="An error occurred",
    variant="danger",
    icon="⚠️"
)
```

**Dismissible Warning**
```python
alert = Alert(
    message="Please review your changes",
    variant="warning",
    dismissible=True
)
```

**Alert with ID**
```python
alert = Alert(
    message="Custom alert",
    variant="primary",
    id="my-alert"
)
```

### Modal

A modal/dialog overlay component with header, body, and footer sections.

#### Parameters

- **id** (str, required): Unique modal ID
- **body** (str, required): Modal body content
- **title** (str, optional): Modal title (header)
- **footer** (str, optional): Modal footer content (typically buttons)
- **size** (str, default="medium"): Modal size
  - Options: `small`/`sm`, `medium`/`md`, `large`/`lg`, `extralarge`/`xl`
- **centered** (bool, default=False): Center modal vertically
- **scrollable** (bool, default=False): Make modal body scrollable

#### Properties

```python
# Get/set body content
modal.body = "Updated content"
print(modal.body)
```

#### Methods

```python
# Render methods
modal.render()  # Auto-detects framework from config
modal.render_with_framework("bootstrap5")
```

#### Examples

**Simple Modal**
```python
from djust.rust_components import Modal

modal = Modal(id="simpleModal", body="Modal content")
```

**Modal with Title**
```python
modal = Modal(
    id="titleModal",
    title="Confirmation",
    body="Are you sure you want to continue?"
)
```

**Modal with Footer Buttons**
```python
modal = Modal(
    id="confirmModal",
    title="Delete Item",
    body="This action cannot be undone.",
    footer='<button class="btn btn-danger">Delete</button><button class="btn btn-secondary">Cancel</button>'
)
```

**Large Centered Modal**
```python
modal = Modal(
    id="largeModal",
    title="Terms and Conditions",
    body="<p>Long content...</p>",
    size="large",
    centered=True
)
```

**Scrollable Modal**
```python
modal = Modal(
    id="scrollModal",
    title="Privacy Policy",
    body="<p>Very long content that scrolls...</p>",
    scrollable=True
)
```

**Complete Modal Example**
```python
modal = Modal(
    id="completeModal",
    title="Edit Profile",
    body='''
        <form>
            <div class="mb-3">
                <label>Name</label>
                <input type="text" class="form-control" />
            </div>
            <div class="mb-3">
                <label>Email</label>
                <input type="email" class="form-control" />
            </div>
        </form>
    ''',
    footer='<button class="btn btn-primary">Save</button><button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>',
    size="large",
    centered=True
)
```

### Dropdown

A dropdown/select component for choosing from a list of options.

#### Parameters

- **id** (str, required): Unique component ID
- **items** (list, required): List of items with `label` and `value` keys
- **selected** (str, optional): Currently selected value
- **variant** (str, default="primary"): Dropdown button style
  - Options: `primary`, `secondary`, `success`, `danger`, `warning`, `info`, `light`, `dark`
- **size** (str, default="medium"): Dropdown size
  - Options: `sm`/`small`, `md`/`medium`, `lg`/`large`
- **disabled** (bool, default=False): Disable the dropdown
- **placeholder** (str, optional): Placeholder text when nothing selected

#### Properties

```python
# Get/set selected value
dropdown.selected = "opt2"
print(dropdown.selected)
```

#### Methods

```python
# Render methods
dropdown.render()  # Auto-detects framework from config
dropdown.render_with_framework("bootstrap5")
```

#### Examples

**Basic Dropdown**
```python
from djust.rust_components import Dropdown

dropdown = Dropdown("myDropdown") \
    .item("Option 1", "opt1") \
    .item("Option 2", "opt2") \
    .item("Option 3", "opt3")
```

**Dropdown with Selection**
```python
dropdown = Dropdown("myDropdown") \
    .item("Red", "red") \
    .item("Green", "green") \
    .item("Blue", "blue") \
    .selected("green")
```

**Success Dropdown**
```python
dropdown = Dropdown("statusDropdown") \
    .item("Active", "active") \
    .item("Inactive", "inactive") \
    .variant(DropdownVariant::Success)
```

**Large Dropdown with Placeholder**
```python
dropdown = Dropdown("largeDropdown") \
    .item("Item 1", "1") \
    .item("Item 2", "2") \
    .size(DropdownSize::Large) \
    .placeholder("Select an item...")
```

**Disabled Dropdown**
```python
dropdown = Dropdown("disabledDropdown") \
    .item("Option", "opt") \
    .disabled(True)
```

### Tabs

A tabbed interface component for organizing content into multiple panels.

#### Parameters

- **id** (str, required): Unique component ID
- **tabs** (list, required): List of tabs with `id`, `label`, and `content` keys
- **active** (str, optional): ID of the active tab (defaults to first tab)
- **variant** (str, default="default"): Tab style variant
  - Options: `default`, `pills`, `underline`
- **vertical** (bool, default=False): Use vertical orientation

#### Properties

```python
# Get/set active tab
tabs.active = "tab2"
print(tabs.active)
```

#### Methods

```python
# Render methods
tabs.render()  # Auto-detects framework from config
tabs.render_with_framework("bootstrap5")
```

#### Examples

**Basic Tabs**
```python
from djust.rust_components import Tabs

tabs = Tabs("myTabs") \
    .tab("home", "Home", "<p>Home content</p>") \
    .tab("profile", "Profile", "<p>Profile content</p>") \
    .tab("settings", "Settings", "<p>Settings content</p>")
```

**Pills Variant**
```python
tabs = Tabs("pillsTabs") \
    .tab("tab1", "First", "Content 1") \
    .tab("tab2", "Second", "Content 2") \
    .variant(TabVariant::Pills)
```

**Vertical Tabs**
```python
tabs = Tabs("verticalTabs") \
    .tab("overview", "Overview", "<h3>Overview</h3><p>Details...</p>") \
    .tab("details", "Details", "<h3>Details</h3><p>More info...</p>") \
    .vertical(True)
```

**Tabs with Active Selection**
```python
tabs = Tabs("activeTabs") \
    .tab("tab1", "Tab 1", "Content 1") \
    .tab("tab2", "Tab 2", "Content 2") \
    .tab("tab3", "Tab 3", "Content 3") \
    .active("tab2")
```

**Underline Tabs**
```python
tabs = Tabs("underlineTabs") \
    .tab("code", "Code", "<pre>code here</pre>") \
    .tab("preview", "Preview", "<p>Preview here</p>") \
    .variant(TabVariant::Underline)
```

## Framework Support

### Bootstrap 5

Renders standard Bootstrap 5 button classes:

```html
<button class="btn btn-primary btn-lg w-100" type="button" id="example">
  Click Me
</button>
```

Classes used:
- `btn` - Base button class
- `btn-{variant}` or `btn-outline-{variant}` - Variant styling
- `btn-sm`, `btn-lg` - Size modifiers
- `w-100` - Full width

### Tailwind CSS

Renders Tailwind utility classes:

```html
<button class="rounded font-medium transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 text-base" type="button" id="example">
  Click Me
</button>
```

Features:
- Responsive hover states
- Focus ring for accessibility
- Smooth transitions
- Size-based padding

### Plain HTML

Renders semantic CSS classes for custom styling:

```html
<button class="button button-primary" type="button" id="example">
  Click Me
</button>
```

Classes used:
- `button` - Base class
- `button-{variant}` - Variant class
- `button-sm`, `button-lg` - Size classes
- `button-outline` - Outline modifier
- `button-block` - Full width

## Using in Templates

Rust components can be used directly in Django templates with JSX-like syntax:

### Basic Template Usage

```html
<!-- Simple button -->
<div class="container">
    <h1>Welcome</h1>
    <RustButton id="welcome-btn" label="Click Me!" />
</div>
```

### With Props

```html
<!-- Button with variant and size -->
<RustButton
    id="submit-btn"
    label="Submit Form"
    variant="success"
    size="lg"
/>

<!-- Button with event handler -->
<RustButton
    id="save-btn"
    label="Save"
    variant="primary"
    onClick="handleSave"
/>
```

### With Template Variables

```html
<!-- Props from context variables -->
<RustButton
    id="{{ button_id }}"
    label="{{ button_label }}"
    variant="{{ button_variant }}"
/>
```

### In For Loops

```html
<!-- Render multiple buttons from a list -->
{% for action in actions %}
    <RustButton
        id="{{ action.id }}"
        label="{{ action.label }}"
        variant="{{ action.variant }}"
        onClick="{{ action.handler }}"
    />
{% endfor %}
```

### With Conditionals

```html
<!-- Conditional rendering -->
{% if user.is_authenticated %}
    <RustButton
        id="logout-btn"
        label="Logout"
        variant="secondary"
    />
{% else %}
    <RustButton
        id="login-btn"
        label="Login"
        variant="primary"
    />
{% endif %}
```

### Input Components in Templates

**Basic Input**
```html
<div class="form-group">
    <RustInput id="username" placeholder="Enter username" />
</div>
```

**Email Input with Label**
```html
<div class="mb-3">
    <RustText content="Email Address" element="label" forInput="email" />
    <RustInput id="email" inputType="email" placeholder="user@example.com" required="true" />
</div>
```

**Password Input**
```html
<div class="mb-3">
    <RustText content="Password" element="label" forInput="password" />
    <RustInput
        id="password"
        inputType="password"
        placeholder="Enter password"
        required="true"
        onInput="validatePassword"
    />
</div>
```

**Number Input with Range**
```html
<RustInput
    id="age"
    inputType="number"
    min="18"
    max="100"
    step="1"
    value="{{ user_age }}"
/>
```

**Search Input**
```html
<RustInput
    id="search"
    inputType="search"
    size="lg"
    placeholder="Search..."
    onInput="handleSearch"
/>
```

### Text Components in Templates

**Heading**
```html
<RustText content="{{ page_title }}" element="h1" />
```

**Colored Paragraph**
```html
<RustText
    content="Your changes have been saved successfully."
    element="p"
    color="success"
/>
```

**Bold Error Message**
```html
<RustText
    content="{{ error_message }}"
    color="danger"
    weight="bold"
/>
```

**Label for Input**
```html
<RustText
    content="{{ field_label }}"
    element="label"
    forInput="{{ field_id }}"
/>
```

### Card Components in Templates

**Simple Card**
```html
<RustCard body="Card content here" />
```

**Card with Header**
```html
<RustCard
    header="Card Title"
    body="Main content of the card"
/>
```

**Card with Header and Footer**
```html
<RustCard
    header="Product Info"
    body="<p>Product description and details go here.</p>"
    footer="<button>Add to Cart</button>"
/>
```

**Success Card with Shadow**
```html
<RustCard
    header="Success!"
    body="Your order has been placed."
    variant="success"
    shadow="true"
/>
```

**Card with Variables**
```html
<RustCard
    header="{{ card_title }}"
    body="{{ card_content }}"
    variant="{{ card_variant }}"
/>
```

### Alert Components in Templates

**Basic Info Alert**
```html
<RustAlert message="This is an informational message" />
```

**Success Alert**
```html
<RustAlert message="Changes saved successfully!" variant="success" />
```

**Error Alert with Icon**
```html
<RustAlert
    message="An error occurred while processing your request"
    variant="danger"
    icon="⚠️"
/>
```

**Dismissible Warning**
```html
<RustAlert
    message="Please review your changes before submitting"
    variant="warning"
    dismissible="true"
/>
```

**Alert with Context Variables**
```html
<RustAlert
    message="{{ alert_message }}"
    variant="{{ alert_type }}"
    dismissible="true"
    id="flash-alert"
/>
```

**Multiple Alerts in Loop**
```html
{% for message in messages %}
    <RustAlert
        message="{{ message.text }}"
        variant="{{ message.level }}"
        dismissible="true"
    />
{% endfor %}
```

### Modal Components in Templates

**Simple Modal**
```html
<RustModal
    id="simpleModal"
    body="This is a simple modal dialog"
/>
```

**Modal with Title**
```html
<RustModal
    id="confirmModal"
    title="Confirm Action"
    body="Are you sure you want to proceed?"
/>
```

**Modal with Footer Buttons**
```html
<RustModal
    id="deleteModal"
    title="Delete Item"
    body="This action cannot be undone. Continue?"
    footer='<button class="btn btn-danger">Delete</button><button class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>'
/>
```

**Large Centered Modal**
```html
<RustModal
    id="termsModal"
    title="Terms and Conditions"
    body="<p>Long terms content...</p>"
    size="large"
    centered="true"
/>
```

**Scrollable Modal with Form**
```html
<RustModal
    id="editModal"
    title="Edit Profile"
    body='
        <div class="mb-3">
            <RustText content="Name" element="label" forInput="name" />
            <RustInput id="name" value="{{ user.name }}" />
        </div>
        <div class="mb-3">
            <RustText content="Email" element="label" forInput="email" />
            <RustInput id="email" inputType="email" value="{{ user.email }}" />
        </div>
    '
    footer='<button class="btn btn-primary">Save Changes</button>'
    scrollable="true"
    size="large"
/>
```

**Modal with Variables**
```html
<RustModal
    id="{{ modal_id }}"
    title="{{ modal_title }}"
    body="{{ modal_content }}"
    size="{{ modal_size }}"
    centered="{{ modal_centered }}"
/>
```

### Dropdown Components in Templates

**Basic Dropdown**
```html
<RustDropdown
    id="myDropdown"
    items='[{"label": "Option 1", "value": "opt1"}, {"label": "Option 2", "value": "opt2"}]'
/>
```

**Dropdown with Selection**
```html
<RustDropdown
    id="colorDropdown"
    items='[{"label": "Red", "value": "red"}, {"label": "Green", "value": "green"}, {"label": "Blue", "value": "blue"}]'
    selected="green"
/>
```

**Dropdown with Variant and Size**
```html
<RustDropdown
    id="statusDropdown"
    items='[{"label": "Active", "value": "active"}, {"label": "Inactive", "value": "inactive"}]'
    variant="success"
    size="large"
/>
```

**Dropdown with Placeholder**
```html
<RustDropdown
    id="selectDropdown"
    items='[{"label": "Item 1", "value": "1"}, {"label": "Item 2", "value": "2"}]'
    placeholder="Choose an option..."
/>
```

**Disabled Dropdown**
```html
<RustDropdown
    id="disabledDropdown"
    items='[{"label": "Option", "value": "opt"}]'
    disabled="true"
/>
```

**Dropdown with Context Variables**
```html
<!-- In your Python view, set context with items list -->
<RustDropdown
    id="dynamicDropdown"
    items='{{ dropdown_items_json }}'
    selected="{{ selected_value }}"
/>
```

### Tabs Components in Templates

**Basic Tabs**
```html
<RustTabs
    id="myTabs"
    tabs='[
        {"id": "home", "label": "Home", "content": "<p>Home content here</p>"},
        {"id": "profile", "label": "Profile", "content": "<p>Profile content here</p>"},
        {"id": "settings", "label": "Settings", "content": "<p>Settings content here</p>"}
    ]'
/>
```

**Pills Variant Tabs**
```html
<RustTabs
    id="pillsTabs"
    tabs='[
        {"id": "tab1", "label": "First", "content": "First tab content"},
        {"id": "tab2", "label": "Second", "content": "Second tab content"}
    ]'
    variant="pills"
/>
```

**Vertical Tabs**
```html
<RustTabs
    id="verticalTabs"
    tabs='[
        {"id": "overview", "label": "Overview", "content": "<h3>Overview</h3><p>Details here...</p>"},
        {"id": "details", "label": "Details", "content": "<h3>Details</h3><p>More information...</p>"}
    ]'
    vertical="true"
/>
```

**Tabs with Active Selection**
```html
<RustTabs
    id="activeTabs"
    tabs='[
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
        {"id": "tab2", "label": "Tab 2", "content": "Content 2"},
        {"id": "tab3", "label": "Tab 3", "content": "Content 3"}
    ]'
    active="tab2"
/>
```

**Tabs with Context Variables**
```html
<!-- In your Python view, prepare tabs list -->
<RustTabs
    id="dynamicTabs"
    tabs='{{ tabs_list_json }}'
    active="{{ active_tab_id }}"
    variant="underline"
/>
```

**Tabs with Rich HTML Content**
```html
<RustTabs
    id="contentTabs"
    tabs='[
        {
            "id": "code",
            "label": "Code",
            "content": "<pre><code>function hello() { console.log(\"Hello!\"); }</code></pre>"
        },
        {
            "id": "preview",
            "label": "Preview",
            "content": "<div class=\"alert alert-success\">Preview output here</div>"
        }
    ]'
/>
```

### Complete Form Example

```html
<div class="card">
    <div class="card-header">
        <RustText content="{{ form_title }}" element="h3" />
    </div>
    <div class="card-body">
        <form dj-submit="handleSubmit">
            <div class="mb-3">
                <RustText content="Email" element="label" forInput="email" />
                <RustInput
                    id="email"
                    inputType="email"
                    placeholder="user@example.com"
                    value="{{ user.email }}"
                    required="true"
                    onChange="validateEmail"
                />
            </div>

            <div class="mb-3">
                <RustText content="Password" element="label" forInput="password" />
                <RustInput
                    id="password"
                    inputType="password"
                    placeholder="Enter password"
                    required="true"
                    onInput="checkPasswordStrength"
                />
            </div>

            <div class="mb-3">
                <RustText content="Age" element="label" forInput="age" />
                <RustInput
                    id="age"
                    inputType="number"
                    min="18"
                    max="100"
                    value="{{ user.age }}"
                />
            </div>

            <div class="mb-3">
                <RustText
                    content="{{ status_message }}"
                    color="{{ status_color }}"
                    weight="bold"
                />
            </div>

            <div class="d-flex gap-2">
                <RustButton
                    id="submit-btn"
                    label="Submit"
                    variant="primary"
                    buttonType="submit"
                />
                <RustButton
                    id="cancel-btn"
                    label="Cancel"
                    variant="secondary"
                    onClick="handleCancel"
                />
            </div>
        </form>
    </div>
</div>
```

## Using with LiveView

Rust components integrate seamlessly with djust's LiveView system:

```python
from djust import LiveView
from djust.rust_components import Button

class MyView(LiveView):
    template_string = """
    <div>
        {{ button_html | safe }}
    </div>
    """

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        # Render button in Python
        btn = Button(
            "counter-btn",
            f"Clicked {self.count} times",
            variant="primary",
            on_click="increment"
        )

        return {
            'button_html': btn.render(),
            'count': self.count
        }

    def increment(self):
        """Event handler called by button click"""
        self.count += 1
```

## Performance

Rust components are significantly faster than pure Python implementations:

| Operation | Python | Rust | Speedup |
|-----------|--------|------|---------|
| Button render | ~100μs | ~1μs | 100x |
| Form render | ~1ms | ~10μs | 100x |
| Complex page | ~10ms | ~100μs | 100x |

Benefits:
- Sub-microsecond rendering
- Zero-copy HTML generation
- No GIL contention
- Efficient memory usage

## Type Safety

The Rust implementation provides compile-time guarantees:

```rust
// This won't compile - invalid variant
button.variant = "invalid";  // Compile error!

// Type-safe enum ensures only valid variants
button.variant = ButtonVariant::Success;  // ✓
```

Python benefits from this through runtime validation in the PyO3 bindings.

## HTML Escaping

All component content is automatically HTML-escaped to prevent XSS:

```python
btn = Button("xss", "<script>alert('xss')</script>")
html = btn.render()
# Output: <button ...>&lt;script&gt;alert('xss')&lt;/script&gt;</button>
```

The `icon` parameter also escapes content unless you use raw HTML from trusted sources.

## Error Handling

```python
from djust.rust_components import ComponentNotAvailableError

try:
    btn = Button("test", "Test")
except ComponentNotAvailableError:
    # Rust components not compiled
    # Fall back to Python components or show error
    print("Please build with: maturin develop --features python")
```

## Extending Components

To add new components:

1. **Define Rust Component** (`crates/djust_components/src/ui/my_component.rs`):
```rust
pub struct MyComponent {
    pub id: String,
    // ... fields
}

impl Component for MyComponent {
    fn render(&self, framework: Framework) -> Result<String, ComponentError> {
        // Implement rendering
    }
}
```

2. **Add PyO3 Bindings** (`crates/djust_components/src/python.rs`):
```rust
#[pyclass(name = "RustMyComponent")]
pub struct PyMyComponent {
    inner: MyComponent,
}

#[pymethods]
impl PyMyComponent {
    #[new]
    fn new(id: String, /* params */) -> PyResult<Self> {
        // ...
    }
}
```

3. **Export in djust_live** (`crates/djust_live/src/lib.rs`):
```rust
#[pymodule]
fn _rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<djust_components::python::PyMyComponent>()?;
    Ok(())
}
```

4. **Create Python Wrapper** (`python/djust/rust_components.py`):
```python
class MyComponent:
    def __init__(self, id: str, ...):
        self._inner = RustMyComponent(id, ...)

    def render(self) -> str:
        return self._inner.render()
```

## Best Practices

### 1. Use for Performance-Critical Components

Rust components excel when rendering many instances:

```python
# Render 1000 buttons - ~1ms total
buttons = [
    Button(f"btn-{i}", f"Button {i}", variant="primary")
    for i in range(1000)
]
html = "\n".join(btn.render() for btn in buttons)
```

### 2. Combine with LiveView for Interactivity

```python
class DynamicButtons(LiveView):
    def mount(self, request, **kwargs):
        self.buttons = []

    def add_button(self):
        btn_id = f"btn-{len(self.buttons)}"
        self.buttons.append(
            Button(btn_id, f"Button {len(self.buttons)}", variant="success")
        )

    def get_context_data(self, **kwargs):
        return {
            'buttons_html': [btn.render() for btn in self.buttons]
        }
```

### 3. Cache Component Instances When Possible

```python
class CachedView(LiveView):
    def mount(self, request, **kwargs):
        # Create button once
        self._submit_btn = Button(
            "submit",
            "Submit",
            variant="primary",
            on_click="submit"
        )

    def get_context_data(self, **kwargs):
        # Render is fast, but instance creation has overhead
        return {'submit_btn': self._submit_btn.render()}
```

### 4. Use Builder Pattern for Complex Configuration

```python
btn = Button("complex", "Complex Button") \
    .with_variant("success") \
    .with_size("lg") \
    .with_icon("✓") \
    .with_on_click("handleClick") \
    .with_outline(False)
```

## Troubleshooting

### Components Not Available

**Error**: `ModuleNotFoundError: No module named 'djust._rust'` or `ComponentNotAvailableError`

**Solution**: Build the Rust extensions:
```bash
make dev-build
# or
maturin develop --features python
```

### Incorrect HTML Output

**Issue**: Button classes not rendering correctly

**Check**:
1. Verify framework configuration in djust settings
2. Ensure you're using `render_with_framework()` if needed
3. Check that the component was built with latest changes

### Performance Issues

**Issue**: Component rendering slower than expected

**Check**:
1. Avoid creating new component instances in tight loops
2. Cache component instances when possible
3. Use bulk rendering for multiple components

## Implemented Components

The following components are fully implemented with Rust backend, PyO3 bindings, and template support:

- ✅ **Button**: Multiple variants, sizes, and states
- ✅ **Input**: Text inputs with validation and multiple types
- ✅ **Text**: Labels, headings, and text display
- ✅ **Container**: Layout containers (fluid, fixed, responsive)
- ✅ **Row**: Flex row layout with alignment and gutters
- ✅ **Column**: Grid column with responsive sizing
- ✅ **Card**: Content cards with header, body, and footer
- ✅ **Alert**: Notifications with dismissible option
- ✅ **Modal**: Dialog/popup overlays with multiple sizes
- ✅ **Dropdown**: Select/dropdown menus with variants and sizes
- ✅ **Tabs**: Tabbed interfaces with multiple styles

## Future Components

Planned components (see roadmap):

- **Grid**: CSS Grid layout
- **Badge**: Status indicators
- **Progress**: Progress bars
- **Spinner**: Loading indicators
- **Tooltip**: Hover tooltips
- **Accordion**: Collapsible sections
- **Breadcrumb**: Navigation breadcrumbs
- **Pagination**: Page navigation
- **Navbar**: Navigation bar
- **Form**: Complete form wrapper

## Contributing

To contribute new components:

1. Create Rust implementation in `crates/djust_components/src/`
2. Add PyO3 bindings in `crates/djust_components/src/python.rs`
3. Create Python wrapper in `python/djust/rust_components.py`
4. Add tests in `test_rust_button.py` (or new test file)
5. Update this documentation
6. Submit PR with examples

## See Also

- [LiveView Documentation](LIVEVIEW.md)
- [Template Engine](TEMPLATES.md)
- [VDOM System](VDOM_PATCHING_ISSUE.md)
- [Forms Integration](PYTHONIC_FORMS_IMPLEMENTATION.md)
