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

<!-- djust-example: ai-article-edit scenario=model-form-edit -->
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
- Opt-ins (added after 1.3.2; neither changes a write): `object_recheck = "writes"`
  lets only read-only events (`validate_field`, plus names in
  `object_read_only_events`) reuse the authorized record; every other event,
  including `submit_form` and every handler of yours, is checked in full.
  `get_object_lookup()` (keys `pk_url_kwarg` / `slug_url_kwarg`) selects the
  record from view state such as a `?open=<id>` param kept in `handle_params`;
  with `object_optional = True` "nothing selected" is `object = None` and no
  form, while a selected record that does not resolve is still a denial.
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

## Rendering fields with the adapters

Prefer the adapters to hand-written field markup. In a `FormMixin` view,
`{% live_field view "email" %}` (`view.as_live_field("email")`; `{% live_form view %}`
for every field) renders label, control, help text and errors with the
configured `css_framework`, bound to `validate_field`:

```html
{% load live_tags %}
<form dj-submit="submit_form">
    {% csrf_token %}
    {% live_field view "name" %}
    {% live_field view "size" group_variant="segmented" %}
    <button type="submit">Save</button>
</form>
```

What every adapter renders (unreleased):
- The control's id is `id_<name>` (`auto_id=` overrides it). Help text is
  `<id>-help` and errors `<id>-error`; the control's `aria-describedby` lists the
  ones rendered, a field with errors is `aria-invalid="true"`, a required one
  `required`. The required `*` is `aria-hidden`.
- `Select`: a native `<select dj-change>`. An optional field gets one empty
  option, never a second one when its choices already have a `""` choice;
  grouped choices render as `<optgroup>`.
- `RadioSelect`: `<fieldset role="radiogroup" aria-labelledby="<id>-legend">`
  with a `<legend>`, `aria-required`, and the group's `aria-describedby` /
  `aria-invalid`; each choice is `<input type="radio" id="<id>_<n>" dj-change="validate_field">`
  plus its `<label for>`. A change sends `field` (the name) and `value`, which
  is what `validate_field` takes; `dj-submit` FormData and the HTTP fallback send
  the checked value under the name. Hook classes: `dj-radio-group`,
  `dj-radio-option`, and `dj-radio-group--<variant>` from `group_variant=`.
- `CheckboxSelectMultiple`: a `<fieldset class="dj-checkbox-group">` of
  checkboxes, not bound to `validate_field` unless `dom_event=` is passed (a
  checkbox change sends its checked state, not the list of values).
- A `BooleanField` has one label, after the checkbox.

Theme classes by slot, without copying adapter code:

```python
LIVEVIEW_CONFIG = {
    "form_classes": {
        "input": "input", "label": "field-label", "help": "field-help",
        "error": "field-error", "radio_group": "segmented",
        "radio_option": "segmented-option",
    },
}
```

Or subclass: `class BrandAdapter(Bootstrap5Adapter): css_classes = {"input": "brand-input"}`,
then `register_adapter("brand", BrandAdapter())` and `css_framework="brand"`.
`css_classes` wins over `form_classes`, which wins over the framework's map;
unset slots keep the framework's classes; `""` means no class. Slots:
`djust.frameworks.FORM_CLASS_SLOTS` and the CSS frameworks guide.

Long option lists: the adapter's native `<select>` is the accessible default.
The `djust_audit` rule X102 asks for a searchable combobox only for a
hand-written `<select dj-change>` with more than 10 or loop-generated options;
for such a form field render
`{% combobox field=view.form_instance.country options=country_options event="validate_field" %}`
(`djust_components`; `country_options` is a list of `{"value", "label"}` dicts)
instead of `live_field`. `field=` fills name, label, value, help, error and id.

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
- Strict policy (opt-in, ADR-036): a strict `dj-submit` handler receives only the form fields it declares (the pressed submit button's `name=value` is one of them), or all of them through `**form_data`, and never `_target`
- Several values: a checkbox group (2+ same-named checkboxes in one form), a `<select multiple>` and any repeated name reach `dj-submit` as a list (`tags=["x", "y"]`; `[]` when a group is unticked), and `dj-change` on a group/`<select multiple>` sends `value` as that list. A lone checkbox stays scalar, so a per-row `dj-change` toggle whose boxes share a name now gets the list (read it as a list, or give the boxes distinct names). Declare `list[str]` under the strict policy
- Pressed button: under the strict policy a `dj-submit` handler declaring `intent` receives the pressed `<button name="intent" value="...">`; legacy handlers keep `_target` and get no new field
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
from djust import LiveView, state
from djust.decorators import event_handler
from .forms import ContactForm

class ContactView(LiveView):
    template_name = "contact.html"
    message = state("")
    errors = state(default_factory=dict)

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

For a Markdown field, use a native textarea (or `{% markdown_textarea %}`) and keep
FormMixin validation, transport and submit behavior. djust 2.0 no longer ships the
Visual/Markdown editor (`{% markdown_editor %}`, removed in #4002).
