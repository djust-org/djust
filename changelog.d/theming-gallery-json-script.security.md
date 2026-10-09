- **The theme editor's inline preset and design-system data is now escaped for
  its `<script>` block.** The editor embedded `json.dumps` output in an inline
  script with `|safe`, and `json.dumps` leaves `<`, `>` and `&` raw, so a
  registered preset or design-system value containing `</script>` closed the
  element. Both blobs now go through `escape_json_for_script`, the same helper
  the debug panel uses; the data the page's script reads is unchanged. 3 cases
  in `python/djust/tests/test_theming_editor_json_script.py`.
