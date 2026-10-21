- **`permission_required` accepts "any of": `djust.auth.AnyOf(...)`.** A view
  attribute or `@permission_required` handler decorator set to
  `AnyOf("runs.agent_run", "chat.chat_general")` admits a user holding at
  least one of them; a list may mix strings and `AnyOf` groups (every string,
  one permission of each group). It is honoured by the HTTP GET, the WebSocket
  and SSE mounts, `reauth_on_event`, the client route map and the admin
  widgets. `djust_audit` reports the groups (`permission_required_any`),
  `--dump-permissions` writes them, and `--permissions` compares them with a
  new `permissions_any` key in `permissions.yaml` (P005 on a mismatch).
  Cases in `python/tests/test_auth_any_of.py` and
  `python/djust/tests/test_reauth_any_of.py`.
