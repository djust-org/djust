# Process canon

Rules the pipeline retros adopted, moved out of `CLAUDE.md` on 2026-11-20 so they
are not loaded into every session and every pipeline subagent (they were ~130 KB,
about 33k tokens, of a 150 KB file). The text is unchanged; only the location moved.

**Who reads what**

- Planning and Code Review: read this index, then `grep -rn` this directory for the
  issue numbers, files or failure class the change touches, and read only the hits.
- Implementation and fix passes: only the pages the plan names.
- Everyone else: nothing here unless a rule is cited.

**Citations.** Code comments cite these rules as `CLAUDE.md #1646`, `CLAUDE.md v1.1.1-2`
and similar. They now resolve with `grep -rn "#1646" docs/process-canon/`.

**Adding a rule.** A retro adds its rules as a new page here (one page per arc) and a
row below. It does not add a section to `CLAUDE.md`; `tests/test_claude_md_size.py`
fails if `CLAUDE.md` grows past its budget. A rule that every agent must know on every
task goes in `CLAUDE.md` in one or two lines, with a link to its page here.

Failure-class pages with instance tables live in [`docs/patterns/`](../patterns/README.md).

| Page | Arc | Size |
|---|---|---|
| [bug-report-triage.md](bug-report-triage.md) | Bug-report triage | 3 KB |
| [v0.6.x-v0.8.x-retro-arcs-backfill.md](v0.6.x-v0.8.x-retro-arcs-backfill.md) | Process canonicalizations from v0.6.x–v0.8.x retro arcs (backfill) | 7 KB |
| [pr-retros-2026-04-26-view-transitions-arc.md](pr-retros-2026-04-26-view-transitions-arc.md) | Process canonicalizations from PR retros (2026-04-26 View Transitions arc) | 3 KB |
| [v0.8.6-retro-arc.md](v0.8.6-retro-arc.md) | Process canonicalizations from v0.8.6 retro arc | 3 KB |
| [v0.9.0-retro-arc.md](v0.9.0-retro-arc.md) | Process canonicalizations from v0.9.0 retro arc | 3 KB |
| [v0.9.1-retro-arc.md](v0.9.1-retro-arc.md) | Process canonicalizations from v0.9.1 retro arc | 2 KB |
| [v0.9.4-retro-arc.md](v0.9.4-retro-arc.md) | Process canonicalizations from v0.9.4 retro arc | 4 KB |
| [v0.9.3-4-retro-arc.md](v0.9.3-4-retro-arc.md) | Process canonicalizations from v0.9.3-4 retro arc | 3 KB |
| [v0.9.6-1-retro-arc.md](v0.9.6-1-retro-arc.md) | Process canonicalizations from v0.9.6-1 retro arc | 4 KB |
| [v0.9.6-2-retro-arc.md](v0.9.6-2-retro-arc.md) | Process canonicalizations from v0.9.6-2 retro arc | 2 KB |
| [v0.9.7-2-retro-arc.md](v0.9.7-2-retro-arc.md) | Process canonicalizations from v0.9.7-2 retro arc | 2 KB |
| [v0.9.7-3-retro-arc.md](v0.9.7-3-retro-arc.md) | Process canonicalizations from v0.9.7-3 retro arc | 2 KB |
| [v1.0.0rc2-retro-arc.md](v1.0.0rc2-retro-arc.md) | Process canonicalizations from v1.0.0rc2 retro arc | 2 KB |
| [v1.0.0rc3-retro-arc.md](v1.0.0rc3-retro-arc.md) | Process canonicalizations from v1.0.0rc3 retro arc | 2 KB |
| [v1.0.0rc4-retro-arc.md](v1.0.0rc4-retro-arc.md) | Process canonicalizations from v1.0.0rc4 retro arc | 4 KB |
| [v1.0.0rc6-retro-arc.md](v1.0.0rc6-retro-arc.md) | Process canonicalizations from v1.0.0rc6 retro arc | 4 KB |
| [v1.0.0rc14-drain-arc.md](v1.0.0rc14-drain-arc.md) | Process canonicalizations from v1.0.0rc14 drain arc | 3 KB |
| [v1.0.2-retro-arc.md](v1.0.2-retro-arc.md) | Process canonicalizations from v1.0.2 retro arc | 2 KB |
| [v1.1.0-retro-arc-security-navigation.md](v1.1.0-retro-arc-security-navigation.md) | Process canonicalizations from v1.1.0 retro arc (security & navigation) | 2 KB |
| [v1.0.5-1-retro-arc-production-incident-drain.md](v1.0.5-1-retro-arc-production-incident-drain.md) | Process canonicalizations from v1.0.5-1 retro arc (production-incident drain) | 3 KB |
| [v1.0.5-2-retro-arc-render-path-cleanup-drain.md](v1.0.5-2-retro-arc-render-path-cleanup-drain.md) | Process canonicalizations from v1.0.5-2 retro arc (render-path + cleanup drain) | 3 KB |
| [v1.0.5-4-v1.0.5-5-retro-arc-dx-drain-sticky-recovery-p0.md](v1.0.5-4-v1.0.5-5-retro-arc-dx-drain-sticky-recovery-p0.md) | Process canonicalizations from v1.0.5-4 + v1.0.5-5 retro arc (DX drain + sticky-recovery P0) | 2 KB |
| [v1.0.6-1-v1.0.6-2-retro-arc-wire-version-security-drain.md](v1.0.6-1-v1.0.6-2-retro-arc-wire-version-security-drain.md) | Process canonicalizations from v1.0.6-1 + v1.0.6-2 retro arc (wire-version + security drain) | 3 KB |
| [v1.0.7-1-retro-arc-post-1.0.6-drain.md](v1.0.7-1-retro-arc-post-1.0.6-drain.md) | Process canonicalizations from v1.0.7-1 retro arc (post-1.0.6 drain) | 1 KB |
| [v1.0.7-3-v1.0.7-4-retro-arc-security-audit-drain-coordinated-disclosur.md](v1.0.7-3-v1.0.7-4-retro-arc-security-audit-drain-coordinated-disclosur.md) | Process canonicalizations from v1.0.7-3 + v1.0.7-4 retro arc (security audit drain + coordinated disclosure) | 3 KB |
| [v1.0.8-1-retro-arc-security-drift-prevention-program.md](v1.0.8-1-retro-arc-security-drift-prevention-program.md) | Process canonicalizations from v1.0.8-1 retro arc (security-drift prevention program) | 5 KB |
| [v1.1.0-3-retro-arc-viewruntime-convergence-adr-022.md](v1.1.0-3-retro-arc-viewruntime-convergence-adr-022.md) | Process canonicalizations from v1.1.0-3 retro arc (ViewRuntime convergence — ADR-022) | 6 KB |
| [v1.1-type-enforcement-arc-adr-023.md](v1.1-type-enforcement-arc-adr-023.md) | Process canonicalizations from the v1.1 type-enforcement arc (ADR-023) | 1 KB |
| [v1.1.0-4-retro-arc-post-convergence-open-issue-drain.md](v1.1.0-4-retro-arc-post-convergence-open-issue-drain.md) | Process canonicalizations from v1.1.0-4 retro arc (post-convergence open-issue drain) | 3 KB |
| [v1.1.0rc5-retro-main-1.1-branch-consolidation.md](v1.1.0rc5-retro-main-1.1-branch-consolidation.md) | Process canonicalizations from the v1.1.0rc5 retro (main/1.1 branch consolidation) | 4 KB |
| [v1.1.0-13-retro-arc.md](v1.1.0-13-retro-arc.md) | Process canonicalizations from v1.1.0-13 retro arc | 5 KB |
| [chain-shaped-issues-2142.md](chain-shaped-issues-2142.md) | Chain-shaped issues (#2142) | 2 KB |
| [v1.1.1-2-retro-arc-django-parity-drain.md](v1.1.1-2-retro-arc-django-parity-drain.md) | Process canonicalizations from v1.1.1-2 retro arc (Django-parity drain) | 4 KB |
| [v1.2.0-template-perf-drain-gate-off-harness-rules.md](v1.2.0-template-perf-drain-gate-off-harness-rules.md) | Process canonicalizations from the v1.2.0 template-perf drain (gate-off harness rules) | 4 KB |
| [v1.2.0-6-retro-arc-transport-fidelity-wire-versioning-check-coverage.md](v1.2.0-6-retro-arc-transport-fidelity-wire-versioning-check-coverage.md) | Process canonicalizations from the v1.2.0-6 retro arc (transport fidelity, wire versioning, check coverage) | 3 KB |
| [v1.3.0-11-12-retro-arc-release-audit-code-scanning.md](v1.3.0-11-12-retro-arc-release-audit-code-scanning.md) | Process canonicalizations from the v1.3.0-11/12 retro arc (release audit, code scanning) | 1 KB |
