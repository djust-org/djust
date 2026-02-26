# Rust Template Engine API Reference

## Overview

The djust Rust template engine (`djust_templates` crate) provides a high-performance Django-compatible template processor written in Rust. This engine delivers sub-millisecond rendering and is fully compatible with Django template syntax.

**Performance Benefits:**
- **10-100x faster** template rendering vs Django
- **Sub-millisecond** parsing and rendering for typical templates
- **Zero Python overhead** for template processing
- **Compiled AST** for efficient re-rendering

**Compatibility:**
- Django template syntax (variables, filters, tags, inheritance)
- Template inheritance with `{% extends %}` and `{% block %}`
- Comprehensive filter support (see [Filters](#filters))
- Template variable extraction for JIT optimization

---

## Table of Contents

- [Core API](#core-api)
  - [Template Class](#template-class)
  - [render_template Function](#render_template-function)
  - [extract_template_variables Function](#extract_template_variables-function)
- [Template Inheritance](#template-inheritance)
  - [TemplateLoader Trait](#templateloader-trait)
  - [build_inheritance_chain Function](#build_inheritance_chain-function)
  - [resolve_template_inheritance Function](#resolve_template_inheritance-function)
- [Lexer & Parser](#lexer--parser)
  - [tokenize Function](#tokenize-function)
  - [parse Function](#parse-function)
- [Renderer](#renderer)
  - [render_nodes Function](#render_nodes-function)
- [Filters](#filters)
  - [apply_filter Function](#apply_filter-function)
  - [Supported Filters](#supported-filters)
- [Error Handling](#error-handling)
- [Performance Benchmarks](#performance-benchmarks)

---

## Core API

### Template Class

The main entry point for template compilation and rendering.

#### Python API

```python
from djust._rust import Template

# Create a compiled template
template = Template("Hello {{ name }}!")

# Render with context
context = {"name": "World"}
html = template.render(context)  # "Hello World!"

# Access original source
print(template.source)  # "Hello {{ name }}!"
```

#### Rust API

```rust
use djust_templates::Template;
use djust_core::Context;

// Create template
let template = Template::new("Hello {{ name }}!")?;

// Render with context
let mut context = Context::new();
context.set("name".to_string(), Value::String("World".to_string()));
let html = template.render(&context)?;
```

#### Methods

**`Template::new(source: &str) -> Result<Template>`**
- Compiles a template string into an AST
- **Parameters**: `source` - Template source code
- **Returns**: Compiled `Template` instance
- **Errors**: Returns `DjangoRustError::TemplateError` for syntax errors, including `{% if %}`/`{% for %}` block tags found inside HTML attribute values (issue #388 — these cause VDOM path index mismatches and are rejected at compile time). Use inline conditionals (`{{ 'cls' if cond else '' }}`) in attribute values instead.
- **Performance**: Typically <1ms for standard templates

**`template.render(&self, context: &Context) -> Result<String>`**
- Renders the template with given context
- **Parameters**: `context` - Variable context for rendering
- **Returns**: Rendered HTML string
- **Errors**: Returns errors for missing parent templates or invalid filters
- **Performance**: Sub-millisecond for most templates

**`template.render_with_loader<L: TemplateLoader>(&self, context: &Context, loader: &L) -> Result<String>`**
- Renders template with custom template loader for inheritance
- **Parameters**:
  - `context` - Variable context
  - `loader` - Custom template loader implementing `TemplateLoader` trait
- **Returns**: Rendered HTML with inheritance resolved
- **Use Case**: Required for `{% extends %}` tags

**`template.source(&self) -> String` (Python getter)**
- Returns the original template source code
- **Returns**: Template source string

#### Example: Template Inheritance

```python
from djust._rust import Template
from djust.live_view import FileTemplateLoader

# Create loader
loader = FileTemplateLoader(template_dirs=["/path/to/templates"])

# Child template with inheritance
template = Template("""
    {% extends "base.html" %}
    {% block title %}My Page{% endblock %}
    {% block content %}Hello World{% endblock %}
""")

# Render with loader
html = template.render_with_loader(context, loader)
```

---

### render_template Function

Fast one-shot template rendering without creating a `Template` instance.

#### Python API

```python
from djust._rust import render_template

html = render_template(
    "Hello {{ name }}!",
    {"name": "World"}
)  # "Hello World!"
```

#### Signature

```rust
fn render_template(source: String, context: HashMap<String, Value>) -> PyResult<String>
```

#### Parameters

- **`source`**: Template source code as string
- **`context`**: Dictionary/HashMap of context variables

#### Returns

Rendered HTML string

#### Errors

Returns `PyErr` on template syntax errors or rendering failures

#### Use Cases

- One-off template rendering
- Dynamic templates that don't need caching
- Testing and debugging

#### Performance

~10-15% slower than pre-compiled `Template` due to compilation overhead. Use `Template` class for repeated rendering.

---

### extract_template_variables Function

Extracts all variable access patterns from a template for JIT optimization.

#### Python API

```python
from djust._rust import extract_template_variables

template = "{{ lease.property.name }} {{ lease.tenant.user.email }}"
variables = extract_template_variables(template)

# Returns:
# {
#     "lease": ["property.name", "tenant.user.email"]
# }
```

#### Signature

```rust
pub fn extract_template_variables(
    template: &str
) -> Result<std::collections::HashMap<String, Vec<String>>>
```

#### Parameters

- **`template`**: Template source code

#### Returns

HashMap mapping variable names to lists of accessed paths:
- **Key**: Root variable name (e.g., `"lease"`)
- **Value**: List of attribute paths (e.g., `["property.name", "tenant.user.email"]`)

#### Features

- **Loop variable tracking**: Preserves loop variables for IDE autocomplete
- **Nested path extraction**: Captures full attribute chains
- **Deduplication**: Automatically removes duplicate paths
- **Template tag support**: Extracts from `{% for %}`, `{% if %}`, `{% with %}`, etc.

#### Example Output

```python
# Template:
"""
{% for property in properties %}
    {{ property.name }}
    {{ property.bedrooms }}
    {% if property.tenant.user.is_active %}
        {{ property.tenant.user.email }}
    {% endif %}
{% endfor %}
"""

# Result:
{
    "properties": ["name", "bedrooms", "tenant.user.email", "tenant.user.is_active"],
    "property": ["name", "bedrooms", "tenant.user.email", "tenant.user.is_active"]
}
```

#### Use Cases

1. **JIT Query Optimization**: Generate `select_related()` calls
2. **IDE Autocomplete**: Provide type hints for template variables
3. **Documentation**: Auto-generate template variable docs
4. **Static Analysis**: Detect missing context variables

#### Performance

Typically <5ms for standard templates. See benchmarks for details.

#### Edge Cases

**Empty templates**: Returns empty HashMap
```python
extract_template_variables("")  # {}
```

**Malformed templates**: Returns error
```python
extract_template_variables("{{ unclosed")  # Error: Unexpected end of template
```

**Filters**: Ignores filters but preserves variable paths
```python
extract_template_variables("{{ name|upper }}")  # {"name": []}
```

**Loop variables**: Kept for autocomplete, paths transferred to iterable
```python
# Template: {% for item in items %}{{ item.name }}{% endfor %}
# Result: {"items": ["name"], "item": ["name"]}
```

---

## Template Inheritance

### TemplateLoader Trait

Custom trait for loading parent templates during inheritance resolution.

#### Rust API

```rust
pub trait TemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>>;
}
```

#### Methods

**`load_template(&self, name: &str) -> Result<Vec<Node>>`**
- Loads and parses a template by name
- **Parameters**: `name` - Template name (e.g., `"base.html"`)
- **Returns**: Parsed AST nodes
- **Errors**: Returns error if template not found

#### Implementation Example

```rust
struct FileTemplateLoader {
    template_dirs: Vec<PathBuf>,
}

impl TemplateLoader for FileTemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>> {
        // Search template directories in order
        for dir in &self.template_dirs {
            let path = dir.join(name);
            if path.exists() {
                let source = std::fs::read_to_string(&path)?;
                let tokens = lexer::tokenize(&source)?;
                return parser::parse(&tokens);
            }
        }
        Err(DjangoRustError::TemplateError(
            format!("Template not found: {}", name)
        ))
    }
}
```

#### Python Integration

Python loaders are automatically wrapped to implement this trait:

```python
class MyLoader:
    def load_template(self, name: str) -> List[Node]:
        # Load template source
        source = self._read_file(name)
        # Parse and return AST
        return parse_template(source)
```

---

### build_inheritance_chain Function

Builds the complete inheritance chain from a child template.

#### Signature

```rust
pub fn build_inheritance_chain<L: TemplateLoader>(
    child_nodes: Vec<Node>,
    loader: &L,
    max_depth: usize
) -> Result<InheritanceChain>
```

#### Parameters

- **`child_nodes`**: Parsed AST of child template
- **`loader`**: Template loader implementation
- **`max_depth`**: Maximum inheritance depth (prevents infinite loops)

#### Returns

`InheritanceChain` containing:
- All templates in the chain (child to root)
- Block overrides from each level
- Final merged template nodes

#### Errors

- **`TemplateError`**: Template not found
- **`InheritanceError`**: Circular inheritance or depth exceeded
- **`ParseError`**: Invalid template syntax

#### Example

```rust
// Child template
let child = parse("{% extends 'base.html' %}{% block title %}My Page{% endblock %}")?;

// Build chain
let chain = build_inheritance_chain(child, &loader, 10)?;

// Get root nodes with overrides applied
let root_nodes = chain.get_root_nodes();
let final_nodes = chain.apply_block_overrides(root_nodes);

// Render
let html = render_nodes(&final_nodes, &context)?;
```

#### Inheritance Chain Structure

```
child.html → parent.html → base.html
   |             |              |
   ↓             ↓              ↓
blocks:      blocks:        blocks:
  title        header         title (default)
  content      footer         header (default)
                               content (default)
                               footer (default)
```

Final merged template:
- Uses child's `title` block
- Uses parent's `header` block
- Uses child's `content` block
- Uses parent's `footer` block

---

### resolve_template_inheritance Function

High-level function for resolving template inheritance with file-based loading.

#### Python API

```python
from djust._rust import resolve_template_inheritance

html = resolve_template_inheritance(
    child_template_path="/templates/child.html",
    template_dirs=["/templates", "/app/templates"],
    context={"title": "My Page"}
)
```

#### Signature

```rust
pub fn resolve_template_inheritance(
    child_path: &str,
    template_dirs: Vec<String>,
    context: HashMap<String, Value>
) -> Result<String>
```

#### Parameters

- **`child_path`**: Path to child template file
- **`template_dirs`**: List of template directories to search
- **`context`**: Rendering context

#### Returns

Fully rendered HTML with inheritance resolved

#### Template Directory Search Order

Templates are searched in the order provided in `template_dirs`:

```python
template_dirs = [
    "/project/templates",      # Searched FIRST (DIRS)
    "/app1/templates",         # Searched SECOND (APP_DIRS)
    "/app2/templates"          # Searched THIRD (APP_DIRS)
]
```

This matches Django's template search order. See [TEMPLATE_RESOLUTION.md](TEMPLATE_RESOLUTION.md) for details.

---

## Lexer & Parser

### tokenize Function

Lexical analysis - converts template source into tokens.

#### Signature

```rust
pub fn tokenize(source: &str) -> Result<Vec<Token>>
```

#### Parameters

- **`source`**: Template source code

#### Returns

Vector of `Token` instances representing lexical elements

#### Token Types

```rust
pub enum Token {
    Text(String),           // Plain text
    Variable(String),       // {{ variable }}
    Tag(String, String),    // {% tag content %}
    Comment(String),        // {# comment #}
}
```

#### Example

```rust
let tokens = tokenize("Hello {{ name }}!")?;
// [
//     Token::Text("Hello "),
//     Token::Variable("name"),
//     Token::Text("!")
// ]
```

#### Errors

- **`LexerError`**: Unclosed tags or invalid syntax
- **`UnexpectedChar`**: Invalid characters in template

#### Performance

~100-200μs for typical templates (1-2KB)

---

### parse Function

Parses tokens into an Abstract Syntax Tree (AST).

#### Signature

```rust
pub fn parse(tokens: &[Token]) -> Result<Vec<Node>>
```

### parse_html_continue Function

Parses HTML while maintaining ID counter state from previous parsing operations. This is essential for preventing ID collisions when dynamically inserting content (like form validation error messages).

#### Python API

```python
from djust._rust import parse_html_continue, get_id_counter, set_id_counter

# Get current ID counter state
current_counter = get_id_counter()

# Parse new content, continuing from current counter
html, patches = parse_html_continue(template_string, context)

# Or explicitly set counter before parsing
set_id_counter(100)  # Start IDs from 100
```

#### Use Cases

- **Form validation**: Error messages inserted mid-session need unique IDs
- **Dynamic content injection**: Any content added after initial render
- **Incremental updates**: Avoiding ID conflicts with existing DOM elements

---

### optimize_ast Function

Optimizes the parsed AST by merging adjacent text nodes and removing comment nodes.

#### Signature

```rust
pub fn optimize_ast(nodes: Vec<Node>) -> Vec<Node>
```

#### What It Does

1. **Merges adjacent Text nodes**: Multiple consecutive `Node::Text` elements are combined into a single node, reducing allocations during rendering
2. **Removes Comment nodes**: Comment nodes (`{# ... #}`) produce no output and are stripped during optimization
3. **Recursively optimizes children**: Child nodes in `If`, `For`, `Block`, `With`, and `ReactComponent` nodes are also optimized

#### Example

Before optimization:
```rust
[
    Node::Text("Hello "),
    Node::Text("World"),
    Node::Comment("This is removed"),
    Node::Text("!"),
]
```

After optimization:
```rust
[
    Node::Text("Hello World!"),
]
```

#### Performance Impact

| Template Type | Speedup |
|--------------|---------|
| Text-heavy templates | 10-15% |
| Mixed content | 5-10% |
| Logic-heavy templates | 2-5% |

The optimization is automatically applied during template compilation. No manual intervention required.

---

#### Parameters

- **`tokens`**: Tokens from lexer

#### Returns

Vector of `Node` representing the AST

#### Node Types

```rust
pub enum Node {
    Text(String),
    Variable(String, Vec<Filter>),
    If { condition, true_nodes, false_nodes },
    For { var_names, iterable, nodes, reversed, empty_nodes },
    Block { name, nodes },
    Extends(String),
    Include(String),               // {% include "partial.html" %}
    Url { name, args, kwargs },    // {% url 'name' arg1 kwarg=val %} (preprocessed by Python)
    With { assignments, nodes },
    // ... more node types
}
```

**Condition operators**: The `If` node supports comparison operators:
- Equality: `==`, `!=`
- Comparison: `>`, `<`, `>=`, `<=`
- Boolean: `and`, `or`, `not`
- Branching: `{% elif %}` is supported (converted to nested `If` nodes)

#### Example

```rust
let tokens = tokenize("{% if user %}Hello {{ user.name }}{% endif %}")?;
let nodes = parse(&tokens)?;
// [
//     Node::If {
//         condition: "user",
//         true_nodes: [
//             Node::Text("Hello "),
//             Node::Variable("user.name", [])
//         ],
//         false_nodes: []
//     }
// ]
```

#### Errors

- **`ParseError`**: Invalid template syntax
- **`UnexpectedTag`**: Unknown or mismatched tags
- **`MissingEndTag`**: Unclosed block tags

---

## Renderer

### render_nodes Function

Renders AST nodes into HTML.

#### Signature

```rust
pub fn render_nodes(nodes: &[Node], context: &Context) -> Result<String>
```

#### Parameters

- **`nodes`**: AST nodes from parser
- **`context`**: Variable context

#### Returns

Rendered HTML string

#### Example

```rust
use djust_templates::renderer::render_nodes;

let html = render_nodes(&nodes, &context)?;
```

#### Context Access

Variables are accessed from context:
- **Simple**: `{{ name }}` → `context.get("name")`
- **Nested**: `{{ user.name }}` → `context.get("user")?.get("name")`
- **Missing**: Returns empty string (Django-compatible)

#### Performance

Sub-millisecond for typical templates

---

## Filters

### apply_filter Function

Applies a template filter to a value.

#### Signature

```rust
pub fn apply_filter(
    filter_name: &str,
    value: &Value,
    arg: Option<&str>
) -> Result<Value>
```

#### Parameters

- **`filter_name`**: Name of filter (e.g., `"upper"`, `"default"`)
- **`value`**: Input value
- **`arg`**: Optional filter argument

#### Returns

Transformed value

#### Example

```rust
use djust_templates::filters::apply_filter;
use djust_core::Value;

let value = Value::String("hello".to_string());
let result = apply_filter("upper", &value, None)?;
// Value::String("HELLO")
```

---

### Supported Filters

#### String Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `upper` | Uppercase | `{{ "hello"\|upper }}` | `"HELLO"` |
| `lower` | Lowercase | `{{ "HELLO"\|lower }}` | `"hello"` |
| `title` | Title case | `{{ "hello world"\|title }}` | `"Hello World"` |
| `capfirst` | Capitalize first | `{{ "hello"\|capfirst }}` | `"Hello"` |
| `length` | String length | `{{ "hello"\|length }}` | `5` |
| `truncatewords` | Truncate words | `{{ text\|truncatewords:3 }}` | First 3 words + `...` |
| `safe` | Mark as safe HTML | `{{ html\|safe }}` | Unescaped HTML |
| `escape` | Escape HTML | `{{ "<b>"\|escape }}` | `"&lt;b&gt;"` |
| `force_escape` | Force HTML escape | `{{ html\|force_escape }}` | Always escapes (unlike `escape`) |
| `default` | Default value | `{{ ""\|default:"N/A" }}` | `"N/A"` |
| `join` | Join list | `{{ list\|join:", " }}` | `"a, b, c"` |
| `slugify` | URL-safe slug | `{{ "Hello World!"\|slugify }}` | `"hello-world"` |
| `cut` | Remove substring | `{{ text\|cut:" " }}` | Removes all spaces |
| `addslashes` | Escape quotes | `{{ text\|addslashes }}` | `it\'s` |
| `striptags` | Strip HTML tags | `{{ "<b>Hi</b>"\|striptags }}` | `"Hi"` |
| `wordcount` | Count words | `{{ text\|wordcount }}` | `4` |
| `wordwrap` | Wrap at width | `{{ text\|wordwrap:40 }}` | Newlines at word boundaries |
| `ljust` | Left-align, pad | `{{ "hi"\|ljust:10 }}` | `"hi        "` |
| `rjust` | Right-align, pad | `{{ "hi"\|rjust:10 }}` | `"        hi"` |
| `center` | Center, pad | `{{ "hi"\|center:10 }}` | `"    hi    "` |
| `linebreaks` | Newlines to HTML | `{{ text\|linebreaks }}` | `<p>` and `<br>` tags |
| `linebreaksbr` | Newlines to `<br>` | `{{ text\|linebreaksbr }}` | `<br>` tags |
| `truncatechars` | Truncate chars | `{{ text\|truncatechars:10 }}` | First 7 chars + `...` |
| `make_list` | String to char list | `{{ "abc"\|make_list }}` | `["a", "b", "c"]` |

#### Numeric Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `add` | Addition | `{{ 5\|add:3 }}` | `8` |
| `floatformat` | Format decimal | `{{ 1.234\|floatformat:2 }}` | `"1.23"` |
| `divisibleby` | Divisibility check | `{{ 10\|divisibleby:2 }}` | `true` |
| `filesizeformat` | Human file size | `{{ 1048576\|filesizeformat }}` | `"1.0 MB"` |
| `stringformat` | Printf format | `{{ 42\|stringformat:"05d" }}` | `"00042"` |
| `pluralize` | Plural suffix | `{{ count\|pluralize }}` | `"s"` if count != 1 |

#### Date/Time Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `date` | Format date | `{{ date\|date:"Y-m-d" }}` | `"2025-01-15"` |
| `time` | Format time | `{{ time\|time:"H:i" }}` | `"14:30"` |
| `timesince` | Time since date | `{{ past_date\|timesince }}` | `"3 days"` |
| `timeuntil` | Time until date | `{{ future_date\|timeuntil }}` | `"2 hours"` |

#### List Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `first` | First item | `{{ list\|first }}` | First element |
| `last` | Last item | `{{ list\|last }}` | Last element |
| `length` | List length | `{{ list\|length }}` | Number of items |
| `slice` | Slice list | `{{ list\|slice:":3" }}` | First 3 items |
| `random` | Random item | `{{ list\|random }}` | Random element |
| `dictsort` | Sort dicts by key | `{{ list\|dictsort:"name" }}` | Sorted by name |
| `dictsortreversed` | Reverse sort dicts | `{{ list\|dictsortreversed:"name" }}` | Reverse sorted |

#### URL Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `urlencode` | URL-safe encoding | `{{ "hello world"\|urlencode }}` | `"hello%20world"` |
| `urlencode:""` | Encode all chars | `{{ "a/b"\|urlencode:"" }}` | `"a%2Fb"` |

#### Utility Filters

| Filter | Description | Example | Result |
|--------|-------------|---------|--------|
| `yesno` | Boolean to text | `{{ True\|yesno:"Yes,No" }}` | `"Yes"` |
| `default_if_none` | Null default | `{{ None\|default_if_none:"N/A" }}` | `"N/A"` |
| `json_script` | JSON in `<script>` | `{{ data\|json_script:"my-id" }}` | Safe `<script>` tag |

---

## Error Handling

### Error Types

```rust
pub enum DjangoRustError {
    TemplateError(String),      // Template syntax errors
    LexerError(String),         // Tokenization errors
    ParseError(String),         // AST parsing errors
    RenderError(String),        // Rendering failures
    InheritanceError(String),   // Template inheritance issues
    FilterError(String),        // Unknown or invalid filters
    ValueError(String),         // Invalid value types
}
```

### Python Error Handling

```python
from djust._rust import Template

try:
    template = Template("{{ unclosed")
except Exception as e:
    print(f"Template error: {e}")
    # "Unexpected end of template"
```

### Rust Error Handling

```rust
match Template::new(source) {
    Ok(template) => { /* use template */ },
    Err(DjangoRustError::TemplateError(msg)) => {
        eprintln!("Template syntax error: {}", msg);
    },
    Err(e) => {
        eprintln!("Other error: {}", e);
    }
}
```

---

## Performance Benchmarks

### Template Compilation

| Template Size | Rust | Django | Speedup |
|--------------|------|--------|---------|
| Small (1KB) | 0.2ms | 3.5ms | **17.5x** |
| Medium (10KB) | 0.8ms | 25ms | **31.2x** |
| Large (50KB) | 3.2ms | 180ms | **56.2x** |

### Template Rendering

| Template Type | Rust | Django | Speedup |
|--------------|------|--------|---------|
| Simple variables | 0.05ms | 1.2ms | **24x** |
| Loops (100 items) | 0.3ms | 8.5ms | **28.3x** |
| Inheritance (3 levels) | 0.4ms | 12ms | **30x** |
| Complex (all features) | 1.5ms | 45ms | **30x** |

### Variable Extraction

| Template Size | Time | Throughput |
|--------------|------|------------|
| Typical (1-2KB) | <5ms | 200+ templates/sec |
| Large (10KB) | 15ms | 66 templates/sec |

**Note**: Benchmarks measured on Apple M1. See `crates/djust_templates/benches/` for detailed benchmarks.

---

## Best Practices

### 1. Template Caching

**Do**: Pre-compile templates and reuse
```python
# ✅ Good - compile once
template = Template(source)
for context in contexts:
    html = template.render(context)
```

**Don't**: Recompile on every render
```python
# ❌ Bad - recompiles every time
for context in contexts:
    html = render_template(source, context)
```

### 2. Context Preparation

**Do**: Use native Python types
```python
context = {
    "name": "John",
    "age": 30,
    "items": ["a", "b", "c"]
}
```

**Don't**: Pass complex objects
```python
# ❌ May not serialize properly
context = {"obj": some_custom_object}
```

### 3. Template Inheritance

**Do**: Keep inheritance depth reasonable (<5 levels)
```
child.html → parent.html → base.html  ✅
```

**Don't**: Create deep inheritance chains
```
a.html → b.html → c.html → d.html → e.html → f.html  ❌
```

### 4. Error Handling

**Do**: Handle errors gracefully
```python
try:
    template = Template(source)
    html = template.render(context)
except Exception as e:
    logger.error(f"Template error: {e}")
    html = render_error_template()
```

### 5. Variable Extraction

**Do**: Cache extraction results
```python
# Extract once per template
variables = extract_template_variables(template_source)
# Use for multiple query optimizations
```

---

## Related Documentation

- **[Template Resolution](TEMPLATE_RESOLUTION.md)** - How templates are located and loaded
- **[JIT Serialization](JIT_SERIALIZATION_PATTERN.md)** - Using variable extraction for optimization
- **[Event Handlers](EVENT_HANDLERS.md)** - LiveView event handling patterns
- **[Debug Panel](DEBUG_PANEL.md)** - Debugging template rendering

---

## Changelog

### v0.3.3 (2026-02-26)
- **Compile-time validation**: `Template::new()` now rejects `{% if %}`, `{% elif %}`, `{% else %}`, `{% endif %}`, `{% for %}`, and `{% endfor %}` block tags inside HTML attribute values. These caused VDOM path index mismatches (issue #388) because browsers cannot place DOM comment anchors inside attribute values. Templates must use inline conditionals (`{{ 'cls' if cond else '' }}`) in attribute contexts instead.
- Added `validate_no_block_tags_in_attrs()` in `lexer.rs` — a state-machine validator that fires before tokenisation and emits a clear error message with an inline-conditional suggestion.

### v0.1.7 (2026-01-24)
- Added `optimize_ast()` function to merge adjacent Text nodes (5-15% render speedup)
- Added `parse_html_continue()` for ID counter continuity across parsing operations
- Added `get_id_counter()` and `set_id_counter()` for session persistence
- Improved whitespace preservation in pre/code/textarea/script/style elements
- Comment nodes now removed during AST optimization

### v0.1.6 (2026-01-24)
- Added `{% url %}` tag support (Python-side preprocessing)
- Added `{% include %}` tag with template directory support
- Added `urlencode` filter
- Added comparison operators (`>`, `<`, `>=`, `<=`) in `{% if %}` tags
- Added auto-serialization for Django types (datetime, Decimal, UUID, FieldFile)

### v0.1.0 (2025-01-15)
- Initial release
- Django template syntax support
- Template inheritance
- Variable extraction for JIT optimization
- 30x performance improvement over Django

---

## Contributing

Found a bug or missing feature? See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

### Running Tests

```bash
# Rust tests
cargo test -p djust_templates

# Python integration tests
pytest python/tests/test_template_*.py
```

### Running Benchmarks

```bash
# Rust benchmarks
cargo bench -p djust_templates

# Python benchmarks
python benchmarks/benchmark_templates.py
```
