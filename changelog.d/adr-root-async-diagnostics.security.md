- **Explicit background diagnostics:** Root background failures and stale-task
  diagnostics no longer log callback values, task names or tracebacks for
  explicit or invalid exposure policies. Late policy transitions cannot opt
  those failures into detailed legacy logging.
