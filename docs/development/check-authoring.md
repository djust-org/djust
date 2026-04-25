# Authoring Django System Checks for djust

This guide covers patterns and conventions for writing new system checks in
djust (the `A###` / `C###` / `T###` / `S###` / `V###` / `W###` series). It
captures patterns surfaced during the v0.7.x check-refinement milestone
(v0.7.2 + v0.7.3) so future check authors don't re-discover them.

## Naming and registration

djust system checks live in two places:

- `python/djust/checks.py` — core framework checks (A/C/T/S/V).
- `python/djust/theming/checks.py` — theming-specific checks (W).

Each check has an ID like `djust.A070`, `djust_theming.W001`, etc. New
check IDs should be:

- Named in the same series as the area they cover (A = ActivityMixin /
  authentication, C = configuration / build, T = template / tag,
  S = security / settings, V = view / lifecycle, W = warning).
- Globally unique across djust + djust_theming.
- Documented in the user-facing checks reference (where applicable).

## Patterns

### Whitespace-preserving redaction for line-number-aware regex scanners

When a regex scanner needs to ignore a region of text — e.g. the contents
of a `{% verbatim %}` block, a `{% comment %}` block, a `<script>` /
`<style>` element, or a fenced markdown code block — the cheapest correct
solution is to **replace the body with whitespace** (preserving newlines)
before running the scan, rather than stripping the region or maintaining
parallel offset tables.

**Canonical reference**: `_strip_verbatim_blocks(content)` in
`python/djust/checks.py` (used by A070 / A071 to ignore
`{% verbatim %}...{% endverbatim %}` regions). Shipped in PR #1014
(closes #1004).

**Why preserve newlines**: line numbers from `match.start()` calculations
on the redacted source must stay aligned with the original source for any
matches *outside* the redacted region. Replace every non-newline character
with a space; keep `\n` verbatim. The scanner sees the same offsets as
the user-readable source.

**Fast-path the common case**: most templates don't contain the redacted
construct. Test for the bare keyword first and return the original
content unchanged when there's nothing to redact:

```python
def _strip_verbatim_blocks(content: str) -> str:
    if "verbatim" not in content:
        return content                       # zero-cost fast path

    def _redact(match: re.Match) -> str:
        body = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in body)

    return _VERBATIM_BLOCK_RE.sub(_redact, content)
```

**When to use this pattern**: any new check that scans template source
as raw text (rather than parsing it through Django's template engine).
Future candidates: `{% comment %}` redaction, `<script>` content
redaction for client-JS scanners, fenced code-block redaction in
markdown templates.

### Config-driven check scope

When a check's behavior depends on a user-configurable scope — e.g.
"check only the active app vs. all installed apps", "check only the
configured preset vs. every registered preset", "check only HEAD vs.
full history" — extract the scope decision into a **named helper**
rather than inline-branching the iterator.

**Canonical reference**: `_contrast_check_scope()` and
`_presets_to_check()` in `python/djust/theming/checks.py` (used by
`check_preset_contrast` for `djust_theming.W001`). Shipped in PR #1015
(closes #1005).

**Why**: the scope helper becomes a clean test seam. You can exercise
the four-branch decision (default / opt-in-all / missing-scope-target /
unknown-value) in 4 small tests without dragging in the full Django
settings stack for each. Inline branching forces every test to mock
the entire iterator.

**Safe-default contract**: unknown values for the scope setting **must**
fall back to the signal-preserving option (typically the smaller scope),
not the silent-noise option (typically the wider scope). A typo in a
config setting should never cause hundreds of warnings to start firing.

```python
def _contrast_check_scope() -> str:
    """Return 'active' or 'all'. Unknown values fall back to 'active'."""
    cfg = getattr(settings, "DJUST_THEMING", {}) or {}
    scope = cfg.get("contrast_check_scope", "active")
    return "all" if scope == "all" else "active"   # safe default


def _presets_to_check():
    """Yield (name, preset) pairs honoring the configured scope."""
    registry = get_registry()
    if _contrast_check_scope() == "all":
        yield from registry.list_presets().items()
        return
    # 'active' branch
    active = get_theme_config().get("preset", "default")
    if not registry.has_preset(active):
        return                              # E002 already fires for this
    yield active, registry.list_presets()[active]
```

**Coordination with existing error checks**: when the scope-target
doesn't exist (e.g. configured preset isn't registered), prefer to
yield zero pairs and let the existing
"is-this-config-valid?" error check carry the load (`check_preset_valid`
fires E002 for the W001 example above). Don't double-warn.

### Misleading existing tests are part of the bug

When fixing a check or invariant, audit the existing tests for fixtures
that **exemplify the broken behavior** the issue describes. If you find
one, *update* it — don't just add a new test alongside it.

**Canonical reference**: PR #1008 (closes #1003).
`test_c011_passes_when_output_exists` had been writing an 18-byte
placeholder `output.css` (`/* compiled css */`) and asserting no C011
fired — which was exactly the bug #1003 was about. The test had
codified the broken behavior. The fix updated the existing test to
use a realistic ~16 KB Tailwind-style fixture, and added new tests
for the placeholder / empty-file / sub-threshold cases.

**Why this matters**: a test that passes for the wrong reason is worse
than no test, because future readers assume the contract is locked
when it isn't. Adding a new test alongside a misleading existing one
leaves the contradiction in the code; the next "improvement" PR will
trip over the conflict.

This is a PR review checklist item — see
`docs/PULL_REQUEST_CHECKLIST.md` for the reviewer-facing version.

## Test conventions for checks

- **Direct-call tests** (preferred for unit coverage): import the check
  function and call it with a mocked registry / mocked filesystem. See
  `python/djust/tests/test_theming_checks.py::TestCheckPresetContrastScope`
  for the canonical pattern.
- **End-to-end tests** (for integration coverage): use `tmp_path` +
  `monkeypatch.chdir()` + `settings.STATICFILES_DIRS` to stand up a
  realistic project layout, then call `check_configuration(None)` (or
  `manage.py check` via Django's test client). See
  `python/tests/test_checks.py::TestC011MissingCompiledCss` for the
  canonical pattern.

Both approaches have value; prefer direct-call tests when the check's
logic is the interesting part, and end-to-end tests when the
interaction with Django's settings / staticfiles / template machinery
is the contract under test.

## Adding a new check

1. Pick a check ID from the appropriate series (and update any
   global registry / docs that enumerates IDs).
2. Add the check function to `checks.py` (or `theming/checks.py`).
3. Register it via `@register(Tags.compatibility)` (or the appropriate tag).
4. Write tests using one of the patterns above.
5. Document the check in the user-facing checks reference.
6. If the check depends on user config, extract the config-decision
   helper (see "Config-driven check scope" above).
7. If the check scans template source as raw text, use the
   whitespace-preserving redaction pattern for any regions Django
   would render literally (`{% verbatim %}`, `{% comment %}`, etc.).

## See also

- `docs/PULL_REQUEST_CHECKLIST.md` — reviewer-facing checklist that
  summarizes some of these patterns.
- `python/djust/checks.py` — core check implementations.
- `python/djust/theming/checks.py` — theming check implementations.
- ADR-012 (`docs/adr/012-framework-internal-attrs-filter-vs-rename.md`) —
  why we use a filter instead of a rename for framework-internal attrs.
