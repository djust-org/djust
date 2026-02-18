# Forms

djust handles forms over WebSocket — no page reloads, no JavaScript, no API layer.

The full forms guide is at [guides/forms.md](../guides/forms.md).

## Quick Reference

### Simple Form (no validation)

```python
from djust import LiveView
from djust.decorators import event_handler

class TodoView(LiveView):
    template_name = "todos.html"

    def mount(self, request, **kwargs):
        self.items = []

    @event_handler()
    def add_item(self, title="", **kwargs):
        if title.strip():
            self.items.append(title.strip())
```

```html
<form dj-submit="add_item">
    {% csrf_token %}
    <input type="text" name="title" placeholder="New item" />
    <button type="submit">Add</button>
</form>
```

### With Django Forms Validation

```python
from django import forms
from djust import LiveView
from djust.forms import FormMixin

class ContactForm(forms.Form):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

class ContactView(FormMixin, LiveView):
    template_name = "contact.html"
    form_class = ContactForm

    def form_valid(self, form):
        send_email(form.cleaned_data)
        self.success_message = "Message sent!"

    def form_invalid(self, form):
        self.error_message = "Please fix the errors below."
```

```html
<form dj-submit="submit_form">
    {% csrf_token %}
    <input name="name" value="{{ form_data.name }}" dj-change="validate_field" />
    {% if field_errors.name %}<span>{{ field_errors.name.0 }}</span>{% endif %}

    <input name="email" type="email" value="{{ form_data.email }}" dj-change="validate_field" />
    {% if field_errors.email %}<span>{{ field_errors.email.0 }}</span>{% endif %}

    <button type="submit">Send</button>
</form>
```

`dj-change="validate_field"` validates each field on blur — instant inline errors without a full submission.

### Editing Existing Records (ModelForm)

```python
class ArticleEditView(FormMixin, LiveView):
    template_name = "article_form.html"
    form_class = ArticleForm

    def mount(self, request, pk=None, **kwargs):
        if pk:
            self._model_instance = Article.objects.get(pk=pk)
        super().mount(request, **kwargs)

    def form_valid(self, form):
        form.save()
        self.success_message = "Saved!"
```

## FormMixin Template Variables

| Variable | Type | Description |
|----------|------|-------------|
| `form_data` | dict | Current field values |
| `field_errors` | dict | Per-field errors `{field: [errors]}` |
| `form_errors` | list | Non-field errors from `clean()` |
| `is_valid` | bool | Result of last submission |
| `form_instance` | Form | Current Django Form instance |

## Full Guide

See [guides/forms.md](../guides/forms.md) for:
- Real-time validation details
- `form.as_live` auto-rendering
- Confirmation dialogs (`dj-confirm`)
- `dj-model` vs `dj-submit` comparison
- Draft mode auto-save (see [State Management](../state/index.md))
