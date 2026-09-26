- **The MCP tools no longer report event handlers without `**kwargs` (ADR-037).**
  `validate_view` and `detect_common_issues` still carried the retired V007 rule.
  A closed signature is encouraged; `manage.py check` compares template bindings
  with handlers instead (`djust.T020`). The AI schema's handler guidance says the
  same, including the legacy `field`/`_target` case.
