- **Storybook registry and ADR-033 narrowing gaps.** `dropdown_menu`,
  `infinite_scroll`, `terminal` and `wizard` are registered with examples, so
  they appear in the storybook and are covered by the typed-events pin;
  `DependentSelect`, `MasonryGrid` and `Treemap` declare `fingerprint_fields`
  like the other data-holding components; the pin resolves classes through the
  registry's own resolver, so `qr_code` (`QRCode`) is exercised instead of
  skipped.
