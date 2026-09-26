- **Documented: interactive components (ADR-034, available from djust 1.3).**
  - A new "Interactive Components" guide covers the ownership rule, then
    `DropdownMenu`: one instance, two menus, client-owned popovers with
    observations, delegated row actions versus keyed collections, and
    persistence, keyboard and security. All its examples are executed by
    `python/djust/tests/test_adr034_documented_examples.py`.
  - The components API reference gains tables generated from the component's
    contracts by `scripts/generate-interactive-reference.py`
    (`make interactive-reference`). A drift test and a pre-commit hook fail
    when they go stale.
  - The core-concepts page and the AI components reference point to the new
    guide.
  - ADR-034 is accepted.
