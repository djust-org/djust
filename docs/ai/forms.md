# Form Handling

## Public construction hooks (unreleased)

`FormMixin` supplies Django-style `get_form_class()`, `get_initial()`,
`get_prefix()`, `get_form_kwargs()` and `get_form(form_class=None)`. Customize
these instead of adding another form factory. Event data reaches
`get_form_kwargs()` as `data`; an empty mapping is bound, while `None` is unbound.
Initial values use Django's field rules, including callable defaults and model
values. `get_initial()` returns a fresh mapping. Prefixes affect HTML names;
reactive `form_data` and `field_errors` retain logical field names.

Existing `_create_form(data=None)` overrides remain supported and delegate to
the public hooks. Do not call `_create_form()` from `get_form()` overrides:
that would recurse. Uploaded files must come from the authorized upload
lifecycle, not arbitrary JSON parameters.

These hooks do not change legacy model authorization or exposure.

## ModelFormMixin: edit one record (djust 1.3+)

**Available from djust 1.3** (not in the 1.3.0rc1 pre-release). Use it for
single-record edit views instead of `_model_instance`:

```python
from django import forms
from djust import LiveView
from djust.forms import ModelFormMixin
from .models import Article

class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "body"]  # never ownership fields such as author

class ArticleEditView(ModelFormMixin[Article], LiveView):
    template_name = "article_edit.html"
    model = Article
    form_class = ArticleForm
    login_required = True

    def get_queryset(self):
        return super().get_queryset().filter(author=self.request.user)

    def form_valid(self, form):
        self.object = form.save()
        self.success_message = "Saved!"
```

```html
<div dj-root>
    <h1>{{ object.title }}</h1>
    <form dj-submit="submit_form">
        {% csrf_token %}
        <input name="title" value="{{ form_data.title }}" dj-change="validate_field">
        <textarea name="body">{{ form_data.body }}</textarea>
        <button type="submit">Save</button>
    </form>
    <p>{{ success_message }}</p>
</div>
```

Rules:
- Route with `<int:pk>` (or `<slug:slug>`). The record comes only from the
  route (`self.kwargs`), never from client parameters. Do not write `mount()`
  to load it.
- `get_queryset()` scopes the records; `has_object_permission(request, obj)`
  authorizes each one. Override at least one (check `djust.S013`).
- Missing, filtered-out and forbidden records get the same denial, and no form
  or hook runs. Every event looks the record up and checks it again.
- `self.object` renders as `object` (plus `context_object_name` if set) and is
  never persisted. Only `self.object = form.save()` (the same record) may be
  assigned; anything else raises `ValueError`.
- `form_valid()` decides whether to save; djust never saves by itself.
- Create forms stay on `FormMixin` with a `ModelForm`. Never combine
  `ModelFormMixin` with `_model_instance`.

## FormMixin Pattern

```python
from djust import LiveView
from djust.forms import FormMixin

class EditView(FormMixin, LiveView):
    template_name = 'myapp/form.html'
    form_class = MyModelForm

    def mount(self, request, pk=None, **kwargs):
        # For edits: set _model_instance BEFORE super().mount()
        if pk:
            self._model_instance = MyModel.objects.get(pk=pk)
        super().mount(request, **kwargs)

    def form_valid(self, form):
        obj = form.save()
        self.success_message = "Saved!"

    def form_invalid(self, form):
        self.error_message = "Please fix errors below"
```

## Template

```html
<div>
    {% if success_message %}
    <div class="alert-success">{{ success_message }}</div>
    {% endif %}

    {% if error_message %}
    <div class="alert-error">{{ error_message }}</div>
    {% endif %}

    <form dj-submit="submit_form">
        {% csrf_token %}
        {{ form.as_p }}
        <button type="submit">Save</button>
    </form>
</div>
```

## Manual Form (without FormMixin)

```python
@event_handler()
def save(self, **form_data):
    form = MyForm(data=form_data)
    if form.is_valid():
        form.save()
        self.success = True
    else:
        self.errors = form.errors
    self._refresh()
```

Rules:
- Strict policy (opt-in, ADR-036): a strict `dj-submit` handler receives only the form fields it declares, or all of them through `**form_data`, and never `_target`
- Set `_model_instance` BEFORE calling `super().mount()` for edit forms
- FormMixin provides `submit_form` handler automatically
- `form_valid`/`form_invalid` are called after validation
- Always include `{% csrf_token %}` in form templates
- Use `dj-submit` (not HTML form action) for LiveView form handling


## Native HTTP event fallback

A semantic `<form dj-submit="save">`, a Django Form class, and a decorated save
handler are sufficient for a reactive form. `FormMixin` can supply validation,
state, and `submit_form` instead of writing a manual handler. Neither approach
requires a second application POST view.

```python
from djust import LiveView
from djust.decorators import event_handler
from .forms import ContactForm

class ContactView(LiveView):
    template_name = "contact.html"

    @event_handler()
    def save(self, **form_data):
        form = ContactForm(data=form_data)
        if form.is_valid():
            self.message = "Validated"
        else:
            self.errors = form.errors
```

```html
<form dj-submit="save">
    {% csrf_token %}
    <input name="email" type="email" required>
    <button type="submit">Save</button>
</form>
```

When WebSocket cannot be used, the browser client sends a CSRF-protected JSON
request to this LiveView's URL. The inherited `post()` dispatches the decorated
handler and returns JSON. Do not add an ordinary form-processing `post()` override
for this fallback. If the product deliberately accepts both protocols, its JSON
events must still delegate to `super().post()`.

Native HTTP fallback uses JavaScript (`fetch`). Plain URL-encoded/multipart HTML
form submission without JavaScript is an optional separate requirement. File
uploads also have their own transport; do not assume JSON event fallback carries
binary files.

For Markdown fields with optional Visual editing, see the
[Markdown editor guide](../website/guides/markdown-editor.md). Use a native textarea,
declarative widget attributes and the supplied controls template; keep FormMixin
validation, transport and submit behavior.
