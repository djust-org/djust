- **`combobox` / `rich_select` validate their field, the combobox's search is
  opt-in, and `{% modal %}` takes a heading level and a description block.**
  - Picking an option sent `validate_field` without the field's name, so
    `FormMixin.validate_field` returned without validating. When the component is
    a form field (`field=`) or its `event` is `validate_field`, options now carry
    `data-field` (the field's `html_name`), which the click sends as `field`,
    like other inputs.
  - The combobox always wired a `dj-input="<name>_search"` handler, which an
    ordinary Django form does not have. Server search is now opt-in
    (`search_event=`); without it, typing filters the given options in the
    browser. The `Combobox` class behaves the same way. A view that relied on
    the implicit `<name>_search` event must now pass `search_event=` explicitly.
  - `{% modal %}` takes `heading_level=` (2-6, default 3) for the title, and a
    `{% modal_description %}...{% endmodal_description %}` block whose
    template markup the modal names in `aria-describedby` (both engines).
