- **The djust admin loads rows through the ModelAdmin's `get_queryset()`.**
  A `DjustModelAdmin` that scopes rows in `get_queryset(request)` was honoured
  by the change list only: bulk actions ran on the selected primary keys
  through `Model.objects`, and the change and delete views loaded their
  object with `Model.objects.get()`. As in Django's admin, an action now runs
  on the selected rows that `get_queryset(request)` holds (other selected keys
  are ignored), and the change and delete views load their object with the
  new `DjustModelAdmin.get_object(request, object_id)`, which looks it up in
  that queryset; a row it leaves out, or a key that names no row, is a 404.
