- **`djust.forms.ModelFormMixin`: edit one authorized object without writing a
  custom `mount()` (ADR-035 F1).** Declare `model` (or `get_queryset()`), a
  `ModelForm` as `form_class`, and route the view with a `pk` or `slug`.
  - **Lookup and authorization.** Before the form is built, the object is
    looked up in `get_queryset()` and authorized with `has_object_permission()`.
    Every later event does both again.
  - **Denials.** A missing, filtered-out or forbidden object is the same
    permission denial on every transport, and no form or hook runs for it.
  - **Where the id comes from.** `self.kwargs` is the route's resolved kwargs
    on HTTP, WebSocket and SSE. Client mount parameters never select the object.
  - **`self.object`.** It is the object authorized for the current request or
    event, and is looked up once per mount or event. It renders as `object`,
    plus an opt-in `context_object_name`, and is never persisted.
    `self.object = form.save()` works; assigning any other record raises
    `ValueError`.
  - **Typing.** `ModelFormMixin[Project]` types `self.object`.
  - **New check.** `djust.S013` warns when an adapter view overrides neither
    `get_queryset()` nor `has_object_permission()`.

  `FormMixin` and its `_model_instance` pattern are unchanged.
