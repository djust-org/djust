# Process canonicalizations from PR retros (2026-04-26 View Transitions arc)

Each rule below was a Stage 11 finding or retro-tracker item from the View
Transitions PR-A → PR-B arc and the downstream-consumer gap-fix arc. Canonicalized
here so the next migration / mechanical-replacement / mixin-forwarding /
filter-shape PR doesn't repeat the failure mode.

- **Async-migration regex pass: ALWAYS run a completeness-grep after** (#1100).
  After `sed`-style adding `await` to every `funcName(...)` callsite, run
  `grep -nE '(^|[^t])(funcName|otherFn)\(' tests/ src/ | …` and visually
  scan for hits inside `async` bodies that lack `await`. The regex misses
  method invocations like `obj.handleMessage(...)` when keyed on top-level
  identifiers. Caught 4 test files in PR #1112; canonicalized after the
  same gap surfaced in PR #1099.

- **ADR scope-estimation: count test-file callers, not just src callers** (#1101).
  For any function whose signature changes (sync→async, single→variadic,
  return-type widening), test-file scope is typically 2-3× production
  scope. Run `grep -lr <symbol> tests/` upfront and put the count in the
  ADR. ADR-013 said "~5 caller sites"; actual was 13.

- **Forward kwargs in mixins: `is None` coalesce, NOT `setdefault`** (#1103).
  `kwargs.setdefault('x', self.default_x)` does NOT overwrite a
  caller-passed `None` — the key already exists. When the value flows
  through to a dict-key write (e.g. `attrs[kwargs['x']] = ...`), `None`
  becomes `attrs[None]` and emits broken HTML. Use:
  ```python
  if kwargs.get('x') is None:
      kwargs['x'] = self.default_x
  ```

- **Mechanical replacement: N similar sites need N tests** (#1104).
  When a PR makes the same change at N call sites, the test suite must
  cover all N — not "a representative few". Identical-looking ≠ tested;
  one site's surrounding context can subtly differ. PR #1102 missed the
  radio site (`frameworks.py:345`) of 5 because tests only covered 4.

- **CHANGELOG additions to existing test files: name the CLASS, not
  the file** (#1106). The pre-push hook
  `scripts/check-changelog-test-counts.py` reads
  `N regression cases in path/to/file.py` as a claim about the FILE's
  total count. When adding K tests to a file with M existing tests,
  write `New cases in TestNewBehavior` — never
  `K regression cases in tests/test_existing.py`. Tripped twice in 24h
  (PR #1105, PR #1112).

- **Filter-shape parameters: contract is `Iterable[T]`, not `list[T]`** (#1108).
  When a parameter is used for membership checks (`fname in filter_x`),
  the contract is "any iterable supporting `in`" — list, tuple, set,
  frozenset all work. Don't annotate as `list[T] | None`; that lies
  about the contract. Test at least one non-list shape (tuple OR set)
  to lock it in.

- **Test fixtures with class-varying state: dynamic subclass, not class
  mutation** (#1109). When a test fixture needs different class-level
  state per instance, use `type('Name', (Base,), {'attr': value})` to
  build a fresh subclass per call. Do NOT do `type(self).attr = value`
  in `__init__` — that mutates a shared object and leaks across tests.

- **Async-callback test stubs MUST yield a microtask** (PR #1113 retro).
  When stubbing a browser API whose real implementation runs callbacks
  in a microtask (`startViewTransition`, `MutationObserver`,
  `IntersectionObserver`, etc.), the stub MUST do
  `await Promise.resolve()` BEFORE invoking the callback. Sync
  invocation lies about real-browser semantics — PR #1092 shipped a
  bug because of exactly this. Add a regression test that asserts
  intermediate state is UNCHANGED before await; that test fails-fast
  against any future stub regression.

- **Multi-issue batch PRs: include an issue × file × test mapping table
  in the PR body** (PR #1115 retro). For batch PRs closing >2 issues, a
  single table mapping each issue → modified files → covering tests
  makes Stage 11 reviewers' job faster. Without it, the reviewer has
  to derive the mapping from prose.
