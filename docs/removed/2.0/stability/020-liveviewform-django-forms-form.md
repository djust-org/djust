### `LiveViewForm` → `django.forms.Form`

djust's form reactivity comes from `FormMixin` on the `LiveView`, not from a
special form base class; `LiveViewForm` only added two helpers to Django's
`forms.Form`. Migrate by subclassing `django.forms.Form` directly. If you called
`form.get_field_errors_json()`, use `json.dumps(form.errors)`; for
`form.get_field_value(name)`, read `form.cleaned_data` (or `form.data` before
validation):

```python
# Before
from djust.forms import LiveViewForm

class ContactForm(LiveViewForm):
    name = forms.CharField()

# After
from django import forms

class ContactForm(forms.Form):
    name = forms.CharField()
```
