---
table: python
removed: `LiveViewForm` (`djust.LiveViewForm`, `djust.forms.LiveViewForm`)
use: A plain `django.forms.Form`, with `FormMixin` on the view. `get_field_errors_json()` becomes `json.dumps(form.errors)`; `get_field_value(name)` becomes `form.cleaned_data` or `form.data`.
---
