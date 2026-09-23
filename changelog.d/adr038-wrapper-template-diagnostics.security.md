- **Explicit views: `wrapper_template` render failures follow the DEBUG
  contract.** The wrapper render runs the project's context processors,
  which are application code, and its failure was logged with the exception
  text. It is now value-free for an explicit view in production and detailed
  under `DEBUG`. Legacy logging is unchanged.
