# ADR-037 D2: gate-offs for the documentation-example harness

Date: 2026-09-25. Re-run after the review fixes at `2aad42b19` (branch `feat/adr-037-d2-d3`).

Command: `.venv-wt/bin/python scripts/doc-examples-gateoff.py`

Each row reverts one layer of `python/djust/tests/_doc_examples.py` and runs
`test_doc_examples.py` and `test_doc_example_drift.py` with `-x`, under a
900-second timeout. The script asserts that each mutation's text is present and
that the source changed. It reports ERROR (collection broke) and HUNG as
themselves, and restores the file with `write_text`.

| Layer | Mutation |
|---|---|
| extractor | the comment run above a fence is no longer read, so every block loses its marker |
| pairing | each Python block is paired with the first `html` block **before** it |
| scenario driving | `Page.event` returns 200 without posting the event |
| drift | an unmarked block is no longer reported |

Result:

| Layer | Outcome |
|---|---|
| extractor | RED: ============= 1 failed, 5 passed, 1 skipped, 12 warnings in 0.94s ============== |
| pairing | RED: ======================== 1 failed, 12 warnings in 0.85s ======================== |
| scenario driving | RED: ======================== 1 failed, 12 warnings in 0.76s ======================== |
| drift | RED: ================== 1 failed, 23 passed, 12 warnings in 1.49s =================== |

The extractor row's failure is in the drift test: with no markers read,
`RUNNABLE` is empty (the one skipped case) and the drift check reports every
covered block as unmarked. That is the layer that catches the extractor
losing examples; the scenario tests would otherwise pass vacuously.

After the run, `git diff python/djust/tests/_doc_examples.py` was empty.
