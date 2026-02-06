# PR #236 Review: feat: add navigation, presence, streaming, uploads, hooks & directives

**Reviewer**: Claude Code (automated)
**Date**: 2026-02-06
**Verdict**: **CHANGES REQUESTED**

---

## Pre-Review Quick Checks

| Check | Status | Notes |
|-------|--------|-------|
| PR Title format | PASS | `feat:` prefix, clear description |
| PR Description | PASS | Detailed feature list, test plan |
| Breaking Changes | PASS | No breaking changes (all additive) |
| Linked Issues | N/A | Port from experimental — no issue tracker |
| Target Branch | PASS | Targets `main` |
| All files tracked | PASS | No untracked dependencies |

## Testing Requirements

### Test Coverage

| Check | Status | Notes |
|-------|--------|-------|
| New features have tests | PARTIAL | Python: excellent. JS: **no tests for 5 new JS files** |
| Bug fixes have regression tests | N/A | Not a bug fix PR |
| Test coverage maintained | PASS | 712 Python + 419 JS all pass |
| Edge cases covered | PASS | Security blocking, boundary conditions, error paths well covered |
| Existing tests pass | PASS | All 712 Python + 419 JS tests pass |
| Integration tests pass | PASS | WebSocket consumer, multi-user presence, full lifecycle tests |

### Test Quality

| Check | Status | Notes |
|-------|--------|-------|
| Tests are deterministic | PASS | No flaky tests observed |
| Test names are descriptive | PASS | Clear naming throughout |
| Test isolation | PASS | Proper fixtures, `autouse=True` cleanup, fresh instances per test |
| Test-implementation alignment | PASS | All imports match actual module paths |
| Import names match shipped code | PASS | No phantom modules |
| Performance tests | N/A | No VDOM-affecting changes |

**Issue**: No JavaScript tests for the 5 new client-side feature files:
- `15-uploads.js` — no tests for chunked upload, progress, drag-and-drop
- `17-streaming.js` — no tests for stream message handling, DOM ops
- `18-navigation.js` — no tests for live_patch/redirect client-side
- `19-hooks.js` — no tests for hook lifecycle
- `20-model-binding.js` — no tests for dj-model two-way binding

---

## Code Quality — AUTO-REJECT ISSUES

### 1. F-string Logger Calls (CRITICAL — Auto-Reject)

**16 new f-string logger calls** introduced by this PR. Per CLAUDE.md and checklist: "Use `%s`-style formatting, never f-strings."

**presence.py** (3 violations):
- Line 174: `logger.warning(f"Presence key format error: {e}...")`
- Line 230: `logger.exception(f"Error in handle_presence_join: {e}")`
- Line 248: `logger.exception(f"Error in handle_presence_leave: {e}")`

**uploads.py** (6 violations):
- Line 382: `logger.warning(f"No upload config for '{upload_name}'")`
- Line 391: `logger.warning(f"Max entries ({config.max_entries}) reached...")`
- Line 396: `logger.warning(f"File too large: {client_size} > {config.max_file_size}")`
- Line 401: `logger.warning(f"Extension not accepted: {client_name}")`
- Line 406: `logger.warning(f"MIME type not accepted: {client_type}")`
- Line 451: `logger.warning(f"Upload validation failed for {ref}: {entry.error}")`

**model_binding.py** (4 violations):
- Line 64: `logger.warning(f"[dj-model] Blocked attempt to set private field: {field}")`
- Line 68: `logger.warning(f"[dj-model] Blocked attempt to set forbidden field: {field}")`
- Line 73: `logger.warning(f"[dj-model] Field '{field}' not in allowed_model_fields")`
- Line 106: `logger.debug(f"[dj-model] Set {self.__class__.__name__}.{field} = {value!r}")`

**websocket.py** (3 new violations in diff — others pre-existing):
- `logger.warning(f"Error cleaning up presence: {e}")`
- `logger.warning(f"Error cleaning up uploads: {e}")`
- `logger.warning(f"Error cleaning up embedded children: {e}")`
- `logger.warning(f"Error setting up presence group: {e}")`
- `logger.error(f"Error updating presence heartbeat: {e}")`
- `logger.error(f"Error handling cursor move: {e}")`

### 2. console.log Without Debug Guards (WARNING)

**11 console.log calls** added to production JS without `djustDebug` guards:

- `03-websocket.js`: heartbeat, connection, and close logging
- `15-uploads.js`: registration and completion logging
- `18-navigation.js`: live_patch and live_redirect logging

These should be wrapped in `if (globalThis.djustDebug)` guards or removed.

### 3. print() Statements in Production Code (PRE-EXISTING)

The `websocket.py` contains `print(..., file=sys.stderr)` debug statements. The diff shows these are mostly pre-existing (moved lines), not newly added. Still worth noting for a follow-up cleanup.

---

## Security Review

| Check | Status | Notes |
|-------|--------|-------|
| XSS prevention | PASS | No `mark_safe(f'...')` with user input |
| mark_safe usage | ACCEPTABLE | `routing.py:128` uses `mark_safe()` on a `<script>` built with `json.dumps()` — safe escaping |
| CSRF protection | PASS | No `@csrf_exempt` added |
| Input validation | PASS | `model_binding.py` has FORBIDDEN_MODEL_FIELDS blocklist, private field blocking |
| Upload validation | PASS | Magic bytes validation, file size limits, extension/MIME checking |
| SQL injection | N/A | No raw SQL added |
| Tenant isolation | PASS | No tenant-affecting changes |

---

## Documentation Review

| Check | Status | Notes |
|-------|--------|-------|
| Module-level docstrings | PASS | All 10 new Python modules have docstrings with usage examples |
| Public API docstrings | PASS | Good coverage with Args/Returns sections |
| Type hints on public APIs | PARTIAL | Missing `-> None` on several `__init__` methods; `Optional` missing on nullable params |
| CHANGELOG.md updated | FAIL | Not updated |
| User documentation | FAIL | No user guide updates for 6 new features |

### Type Hint Gaps (Minor)

- `presence.py`: `update_heartbeat()`, `update_cursor()`, `remove_cursor()` missing return type
- `presence.py`: `update_cursor()` has `meta: Dict[str, Any] = None` — should be `Optional[Dict[str, Any]]`
- `streaming.py`, `uploads.py`, `push_events.py`, `backends/memory.py`, `backends/redis.py`: `__init__` methods missing `-> None`

---

## Performance Review

| Check | Status | Notes |
|-------|--------|-------|
| JS bundle size | ACCEPTABLE | +1,679 lines to client.js, reasonable for 5 new features |
| WebSocket efficiency | PASS | Streaming batched at ~60fps, heartbeat-based presence |
| Memory usage | PASS | In-memory backend uses cleanup on stale entries |
| DOM manipulation | PASS | Targeted updates via CSS selectors |

---

## Summary of Required Changes

### Must Fix (Auto-Reject)
1. **16+ f-string logger calls** → Convert to `%s` formatting
2. **11 unguarded console.log** → Add `djustDebug` guards or remove

### Should Fix
3. **No JS tests** for 5 new client-side feature files
4. **CHANGELOG.md** not updated
5. **Type hint gaps** — `Optional[]` and `-> None` on init methods

### Nice to Have
6. User documentation for new features
7. Clean up pre-existing `print()` statements in websocket.py

---

## Checklist Improvement Suggestions

The current checklist is thorough. Suggested additions:

1. **Add a "JavaScript logging" auto-reject rule**: "console.log in production JS code without `globalThis.djustDebug` guard" — currently the checklist says "No console.log in production code" but doesn't mention the debug guard pattern.

2. **Add "JS test coverage" requirement**: The checklist mentions "New features have tests" generically but doesn't explicitly require JS tests for new JS files. Add: "New JavaScript feature files must have corresponding test files in `tests/js/`."

3. **Add "CHANGELOG.md updated" to auto-reject list**: Currently it's listed under Documentation Requirements but not in the auto-reject section. For feature PRs it should be mandatory.
