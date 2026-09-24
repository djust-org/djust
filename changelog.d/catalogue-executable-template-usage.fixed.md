- **Executable template-component examples.** Dropdown, modal and tabs usage
  now includes working handlers that update the values read by their template
  tags. Generated examples preserve slot content and the modal's opening
  control. Regression tests execute the displayed Python and verify the
  before/after rendered state; catalogue compilation failures are no longer
  silently skipped.
  Accordion, collapsible, carousel and sheet examples also import the exact
  renderer used by their previews, instead of unmounted descriptor namesakes.
