# Form Handling

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
[Markdown editor guide](../components-markdown-editor.md). Use a native textarea,
declarative widget attributes and the supplied controls template; keep FormMixin
validation, transport and submit behavior.
