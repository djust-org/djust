# Scaffolding — Model-to-LiveView Generator

`djust_gen_live` is a CLI scaffolding generator that creates a complete CRUD LiveView from a model definition. It is the direct equivalent of Phoenix's `mix phx.gen.live` for djust.

## Installation

`djust_gen_live` is included with djust. No additional installation is required. Run it from your Django project root:

```bash
python manage.py djust_gen_live
```

## Quick Start

Generate a LiveView for a ``Post`` model with ``title``, ``body``, and ``published`` fields:

```bash
python manage.py djust_gen_live blog Post title:string body:text published:boolean
```

This generates:

```
blog/
    views.py              — PostListView LiveView class
    urls.py               — URL routing
    templates/blog/
        post_list.html    — List view with inline show/edit panel
    tests/
        test_post_crud.py — LiveViewTestClient smoke tests
```

Then add the model to ``blog/models.py`` and run migrations:

```bash
python manage.py makemigrations blog
python manage.py migrate
```

Start your server and visit ``/posts/`` to see the fully functional CRUD interface.

## CLI Reference

### Basic Syntax

```bash
python manage.py djust_gen_live <app_name> <ModelName> [field:name ...]
```

| Argument | Description | Example |
|----------|-------------|---------|
| ``app_name`` | Django app name (lowercase, must exist) | ``blog`` |
| ``ModelName`` | Model class name (PascalCase) | ``Post`` |
| ``field:name`` | Field definition (see Field Types below) | ``title:string`` |

### Field Types

| CLI Type | Django Field | Notes |
|----------|--------------|-------|
| ``string`` | ``CharField(max_length=255)`` | Short text |
| ``text`` | ``TextField()`` | Long text, no limit |
| ``integer`` | ``IntegerField()`` | Whole numbers |
| ``float`` | ``FloatField()`` | Decimal numbers |
| ``boolean`` | ``BooleanField(default=False)`` | Checkbox |
| ``date`` | ``DateField()`` | Calendar date |
| ``datetime`` | ``DateTimeField()`` | Date + time |
| ``email`` | ``EmailField()`` | Validated email |
| ``url`` | ``URLField()`` | Validated URL |
| ``slug`` | ``SlugField()`` | URL-safe identifier |
| ``decimal`` | ``DecimalField()`` | Precise decimal numbers |
| ``fk:ModelName`` | ``ForeignKey(ModelName)`` | Belongs-to relationship |

### Flags

| Flag | Description |
|------|-------------|
| ``--belongs-to=ModelName`` | Add a foreign key to the specified model automatically |
| ``--model=ModelName`` | Introspect an existing Django model instead of passing field definitions |
| ``--no-tests`` | Skip generating the test file |
| ``--force`` | Overwrite existing files without prompting |
| ``--api`` | Generate JSON API LiveView using ``render_json()`` instead of templates |
| ``--dry-run`` | Print what would be generated without writing any files |

## Examples

### Blog Posts

```bash
python manage.py djust_gen_live blog Post title:string body:text published:boolean
```

### Comments with Foreign Keys

```bash
python manage.py djust_gen_live comments Comment post:fk:Post author:fk:User body:text
```

### From an Existing Model

```bash
python manage.py djust_gen_live blog Post --model=Post
```

This reads the field definitions from the existing ``Post`` model in ``blog/models.py`` and generates a LiveView matching its structure.

### API Mode (No Templates)

```bash
python manage.py djust_gen_live api_posts Post title:string body:text --api
```

Generates a LiveView that returns ``render_json()`` responses instead of rendering HTML templates. Use this for building JSON APIs.

### Dry Run

```bash
python manage.py djust_gen_live blog Post title:string --dry-run
```

Shows exactly what files would be created and their content, without writing anything to disk.

## Generated Files

### ``views.py``

A single ``PostListView`` LiveView class that handles all CRUD state transitions via WebSocket events:

```python
class PostListView(LiveView):
    template_name = "blog/post_list.html"

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.items = []
        self.selected = None
        self._compute()

    def _compute(self):
        qs = Post.objects.all()
        if self.search_query:
            qs = qs.filter(title__icontains=self.search_query)
        self.items = list(qs)

    @event_handler()
    def search(self, value: str = "", **kwargs):
        self.search_query = value
        self._compute()

    @event_handler()
    def show(self, item_id: int = 0, **kwargs):
        try:
            self.selected = Post.objects.get(pk=item_id)
        except Post.DoesNotExist:
            self.selected = None

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        Post.objects.filter(pk=item_id).delete()
        self._compute()

    @event_handler()
    def create(self, title: str = "", body: str = "", **kwargs):
        if title.strip():
            Post.objects.create(title=title.strip(), body=body)
            self._compute()

    @event_handler()
    def update(self, item_id: int = 0, title: str = "", body: str = "", **kwargs):
        try:
            obj = Post.objects.get(pk=item_id)
            obj.title = title
            obj.body = body
            obj.save()
            self.selected = obj
        except Post.DoesNotExist:
            pass
        self._compute()
```

All state transitions (list, show, new, edit) happen within a single LiveView via events — no separate URL routes needed.

### ``urls.py``

```python
from django.urls import path
from .views import PostListView

urlpatterns = [
    path("posts/", PostListView.as_view(), name="post_list"),
]
```

### ``templates/blog/post_list.html``

A list view with:
- Search input (``dj-input="search"``)
- Add new button with inline form
- Table with rows: show, edit, delete buttons
- Show/edit panel that slides in when a record is selected

### ``tests/test_post_crud.py``

``LiveViewTestClient`` smoke tests for all event handlers:
- ``test_search`` — filters the list
- ``test_show`` — selects a record
- ``test_create`` — adds a new record
- ``test_update`` — edits an existing record
- ``test_delete`` — removes a record

## Introspecting Existing Models

If you already have a model in ``models.py``:

```bash
python manage.py djust_gen_live blog Post --model=Post
```

This reads the actual field definitions from ``Post._meta.fields`` and generates a LiveView that matches your model exactly, including foreign key relationships.

## Merging vs. Overwriting

**Existing files are never silently overwritten.**

- If ``views.py`` or ``urls.py`` already exists, new content is merged (the new view class is appended)
- If templates or tests already exist, the command fails with an error
- Use ``--force`` to overwrite existing files

## Programmatic API

You can also use the generator from Python code:

```python
from djust.scaffolding.gen_live import parse_field_defs, generate_liveview

# Parse field definitions
fields = parse_field_defs(["title:string", "body:text", "published:boolean"])

# Generate files
generate_liveview(
    app_name="blog",
    model_name="Post",
    fields=fields,
    options={"force": False, "dry_run": False},
)
```

### ``parse_field_defs(field_defs)``

Parse a list of field definition strings into field dicts.

```python
fields = parse_field_defs(["title:string", "body:text", "fk:User"])
# Returns: [{"name": "title", "type": "string", ...}, ...]
```

Raises ``ValueError`` for invalid field syntax or unknown types.

### ``introspect_model(model_class_name, app_name)``

Read field definitions from an existing Django model.

```python
fields = introspect_model("Post", "blog")
```

Raises ``ValueError`` if the model cannot be found or has no fields.

### ``generate_liveview(app_name, model_name, fields, options)``

Generate all LiveView files.

Options:
- ``force`` — overwrite existing files
- ``dry_run`` — print without writing
- ``no_tests`` — skip test file
- ``api`` — generate JSON API mode
