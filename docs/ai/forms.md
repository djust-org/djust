# Form Handling

## FormMixin Pattern

```python
from djust import LiveView
from djust.forms import FormMixin
from django.urls import reverse

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
        self.redirect_url = reverse('detail', kwargs={'pk': obj.pk})

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
