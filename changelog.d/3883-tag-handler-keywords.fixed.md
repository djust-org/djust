- **A resolved keyword value of a `TagHandler` tag is read once (#3883).** `{% djust_pwa_manifest
  name=v %}` with `v = "label=x"` rendered the Python tuple `('label', 'x')`, and a padded value
  lost its padding. The value now passes through as the text it is. A resolved string that is
  itself quoted still loses its quotes: the text cannot tell it from a quoted literal, and telling
  them apart needs the typed channel the component handlers have.
