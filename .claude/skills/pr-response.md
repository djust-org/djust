# PR Response Skill

## Description

This skill reads the latest PR review, addresses all feedback, and saves a response document. It automatically creates GitHub issues for any deferred tasks.

**When to use this skill:**
- User invokes `/respond <pr-number>`
- User wants to address review feedback systematically
- User needs to track deferred tasks as issues

**How it works:**
1. Reads latest review from `.claude/reviews/pr-{number}/latest.md`
2. Parses all feedback (Critical, Minor, Nice-to-Have)
3. For each item:
   - Addressable now → Makes code changes
   - Deferred → Creates GitHub issue
4. Saves response to `.claude/responses/pr-{number}/response-{timestamp}.md`
5. Updates response index

**Output:**
- Complete response document with all actions taken
- GitHub issues created for deferred tasks
- Summary of changes made
- Links to issues created

---

## Instructions for Agent

You are responding to PR review feedback autonomously.

### 1. Read Latest Review

**Find and read the review:**
```bash
cat .claude/reviews/pr-{number}/latest.md
```

**Parse the review to extract:**
- PR title and number
- Review status (APPROVED/APPROVED_WITH_CONDITIONS/CHANGES_REQUESTED)
- Critical Issues (❌)
- Minor Issues (⚠️)
- Nice-to-Have Improvements (💡)
- Questions for Author
- Recommendations

### 2. Categorize Feedback Items

For each feedback item, determine:

**Category A - Address Now** (make code changes):
- Critical issues that must be fixed
- Minor issues that are quick to fix
- Improvements with clear implementation path
- Simple refactorings

**Category B - Defer to Issue** (create GitHub issue):
- Large refactorings requiring significant time
- Features requiring design decisions
- Tasks blocked by external dependencies
- Items marked as "follow-up PR" in review

**Category C - Acknowledge** (no action needed):
- Questions answered in response
- Suggestions declined with rationale
- Already addressed in latest commit

### 3. Address Feedback (Category A)

For each "Address Now" item:

**Make the code changes:**
1. Use Read to examine relevant files
2. Use Edit or Write to make changes
3. Test changes if applicable
4. Document what was changed

**Track in response:**
```markdown
### ✅ Addressed: {Issue Title}

**Review Feedback:**
> {quoted feedback from review}

**Action Taken:**
- Changed {file}:{line} to {description}
- {specific changes made}

**Files Modified:**
- `{file_path}` (+{additions}/-{deletions})

**Verification:**
- [ ] Code compiles/runs
- [ ] Tests pass (if applicable)
- [ ] Follows project conventions
```

### 4. Create Issues for Deferred Tasks (Category B)

For each "Defer to Issue" item:

**Create GitHub issue:**
```bash
gh issue create \
  --title "{clear, actionable title}" \
  --body "$(cat <<'EOF'
## Context

From PR #{number} review: {review_date}

## Feedback

{quoted feedback from review}

## Proposed Solution

{implementation approach if known}

## Acceptance Criteria

- [ ] {criterion 1}
- [ ] {criterion 2}

## Related

- PR #{number}
- Review: .claude/reviews/pr-{number}/latest.md

## Labels

{labels: enhancement, tech-debt, testing, documentation, etc.}
EOF
)" \
  --label "{appropriate_label}"
```

**Track in response:**
```markdown
### 📋 Deferred: {Issue Title}

**Review Feedback:**
> {quoted feedback from review}

**Reason for Deferral:**
{why this wasn't addressed immediately}

**GitHub Issue Created:**
- **Issue**: #{issue_number} - {title}
- **Link**: https://github.com/{owner}/{repo}/issues/{issue_number}
- **Labels**: {labels}

**Timeline:**
{estimated timeline if known}
```

### 5. Answer Questions (Category C)

For questions from reviewer:

```markdown
### ❓ Question: {Question}

**Review Question:**
> {quoted question}

**Answer:**
{clear, thorough answer}

**Additional Context:**
{any relevant context or rationale}
```

### 6. Save Response Document

**Create directory:**
```bash
mkdir -p .claude/responses/pr-{number}
```

**Generate timestamp:**
```bash
timestamp=$(date +"%Y-%m-%d-%H%M%S")
```

**Save to file:**
`.claude/responses/pr-{number}/response-${timestamp}.md`

**Document format:**
```markdown
# Response to PR #{number} Review

**PR**: #{number} - {title}
**Review Date**: {review_date}
**Review Status**: {APPROVED/APPROVED_WITH_CONDITIONS/CHANGES_REQUESTED}
**Response Date**: {YYYY-MM-DD HH:MM}
**Responder**: {author_name}

**Review File**: [.claude/reviews/pr-{number}/latest.md](.claude/reviews/pr-{number}/latest.md)

---

## Summary

{1-2 paragraph summary of response}

**Actions Taken:**
- ✅ {count} issues addressed with code changes
- 📋 {count} issues deferred (GitHub issues created)
- ❓ {count} questions answered
- 💡 {count} suggestions acknowledged

**Files Modified:** {file_count}
**Issues Created:** {issue_count}

---

## Feedback Addressed

### Critical Issues ❌

{for each critical issue}

### Minor Issues ⚠️

{for each minor issue}

### Nice-to-Have Improvements 💡

{for each improvement}

### Questions from Reviewer ❓

{for each question}

---

## Files Modified

{list all files changed with diff summary}

---

## GitHub Issues Created

{list all issues created with links}

---

## Next Steps

**Before Merge:**
- [ ] {action item}
- [ ] {action item}

**After Merge:**
- [ ] {follow-up item}
- [ ] {follow-up item}

---

## Additional Notes

{any additional context or notes}

---

**Responded by**: {author_name}
**Response Date**: {YYYY-MM-DD}
**All Feedback Addressed**: {Yes/No/Partially}
```

**Create symlink:**
```bash
cd .claude/responses/pr-{number}
ln -sf response-${timestamp}.md latest.md
```

### 7. Update Response Index

**Create/read** `.claude/responses/index.md`

**Update these sections:**

1. **Recent Responses table** (top 10)
2. **Responses by PR**
3. **Statistics**

**Index format:**
```markdown
# PR Review Responses Index

**Total Responses**: {count}
**Last Updated**: {YYYY-MM-DD HH:MM}

---

## Recent Responses

| PR | Response Date | Issues Addressed | Issues Deferred | Link |
|----|---------------|------------------|-----------------|------|
| #{number} | {date} | {count} | {count} | [View](pr-{number}/latest.md) |

---

## Responses by PR

### PR #{number} - {title}

**Responses**: {count} total

- [{timestamp}](pr-{number}/response-{timestamp}.md)
  - Addressed: {count}
  - Deferred: {count}
  - Issues created: #{issue1}, #{issue2}

---

**Last Updated**: {YYYY-MM-DD}
```

### 8. Report Completion

Return this message:

```
✅ Review feedback addressed!

**PR**: #{number} - {title}
**Response File**: .claude/responses/pr-{number}/response-{timestamp}.md

**Summary:**
- ✅ {count} issues addressed with code changes
- 📋 {count} issues deferred to GitHub issues
- ❓ {count} questions answered

**GitHub Issues Created:**
{list issues with links}

**Files Modified:**
{list files}

**Quick access:**
- Response: cat .claude/responses/pr-{number}/latest.md
- Review: cat .claude/reviews/pr-{number}/latest.md

**Next Steps:**
{any remaining actions}
```

---

## Decision Framework

Use this framework to decide how to handle each feedback item:

### Address Now (Make Code Changes)

**Criteria:**
- Can be completed in < 30 minutes
- No external dependencies
- Clear implementation path
- Non-controversial change
- Won't destabilize PR

**Examples:**
- Add missing docstring
- Fix typo or formatting
- Add simple validation
- Improve variable naming
- Add logging statement

### Defer to Issue

**Criteria:**
- Requires > 30 minutes
- Needs design discussion
- Blocked by external dependency
- Requires new tests/infrastructure
- Marked as "follow-up PR" in review

**Examples:**
- Large refactoring
- New feature addition
- Performance optimization requiring benchmarks
- Breaking API changes
- Comprehensive test suite

### Acknowledge Only

**Criteria:**
- Question that just needs answer
- Suggestion declined (with rationale)
- Already addressed in latest commit
- Out of scope for this PR

**Examples:**
- "Why did you choose approach X?" → Answer with rationale
- "Consider using library Y" → Explain why current approach is better
- "Add feature Z" → Out of scope, will consider for future

---

## GitHub Issue Template

When creating issues for deferred tasks, use this template:

```markdown
## Context

From PR #{pr_number} review ({review_date})

**Original Feedback:**
> {quoted feedback from review}

## Problem

{clear description of what needs to be done}

## Proposed Solution

{implementation approach if known, or "TBD - needs design"}

## Acceptance Criteria

- [ ] {specific, measurable criterion}
- [ ] {specific, measurable criterion}
- [ ] {specific, measurable criterion}

## Implementation Notes

{any relevant context, constraints, or considerations}

## Related

- PR #{pr_number}: {pr_title}
- Review: `.claude/reviews/pr-{number}/latest.md`
- Response: `.claude/responses/pr-{number}/latest.md`

## Priority

{P0 (blocker) | P1 (high) | P2 (medium) | P3 (low)}

## Labels

{appropriate labels: enhancement, bug, tech-debt, testing, documentation, performance, security}
```

---

## Examples

### Example 1: Simple Feedback (Address Now)

**Review Feedback:**
> ⚠️ **Missing docstring**: The `debounceEvent()` function lacks a docstring explaining its parameters.

**Response:**
```markdown
### ✅ Addressed: Missing docstring for debounceEvent()

**Review Feedback:**
> The `debounceEvent()` function lacks a docstring explaining its parameters.

**Action Taken:**
Added comprehensive JSDoc docstring to `debounceEvent()` in `client.js:214-256`

**Files Modified:**
- `python/djust/static/djust/client.js` (+15/-0)

**Changes:**
```javascript
/**
 * Debounce an event - delay until user stops triggering events
 *
 * @param {string} eventName - Handler name (e.g., "search")
 * @param {object} eventData - Event parameters to send
 * @param {object} config - Configuration {wait: number, max_wait: number|null}
 */
debounceEvent(eventName, eventData, config) {
    // ... implementation
}
```

**Verification:**
- [x] JSDoc format correct
- [x] All parameters documented
- [x] Follows project conventions
```

### Example 2: Large Task (Defer to Issue)

**Review Feedback:**
> 💡 **Unit tests deferred**: JavaScript unit tests should be added before production release.

**Response:**
```markdown
### 📋 Deferred: Add JavaScript Unit Tests

**Review Feedback:**
> JavaScript unit tests should be added before production release.

**Reason for Deferral:**
- Requires Jest setup and configuration (~2 hours)
- 24+ tests needed for comprehensive coverage
- Acceptable for Alpha, required for Beta

**GitHub Issue Created:**
- **Issue**: #45 - Phase 2: Add JavaScript Unit Tests for Debounce/Throttle
- **Link**: https://github.com/johnrtipton/djust/issues/45
- **Labels**: `testing`, `phase-2`, `beta-blocker`

**Timeline:**
Target completion: 1-2 weeks (before Beta release)

**Acceptance Criteria** (from issue):
- [ ] Jest test framework configured
- [ ] Debounce basic functionality tests
- [ ] Debounce max_wait tests
- [ ] Throttle leading/trailing edge tests
- [ ] Integration tests with metadata
- [ ] 100% code coverage for new functions
```

### Example 3: Question (Acknowledge)

**Review Feedback:**
> ❓ **Multiple Decorators**: Should we warn if handler has both `@debounce` and `@throttle`?

**Response:**
```markdown
### ❓ Question: Warning for Multiple Decorators

**Review Question:**
> Should we warn if handler has both `@debounce` and `@throttle`?

**Answer:**
Yes, excellent suggestion! Added warning in commit `abc1234`:

**Implementation:**
```javascript
if (metadata?.debounce && metadata?.throttle) {
    console.warn(`[LiveView] Handler ${eventName} has both @debounce and @throttle. Using @debounce only.`);
}
```

**Behavior:**
- Warns when both decorators detected
- Applies `@debounce` (predictable priority)
- Clear message guides developers to use one decorator per handler

**Files Modified:**
- `python/djust/static/djust/client.js` (+3/-0)
```

---

## Error Handling

### Review File Not Found

```
❌ Error: No review found for PR #{number}

Expected location: .claude/reviews/pr-{number}/latest.md

Available reviews:
{list available PR reviews}

Run /review-save {number} first to create a review.
```

### GitHub Issue Creation Fails

```
⚠️ Warning: Failed to create GitHub issue for deferred task

Error: {error message}

**Manual action required:**
Create issue manually with these details:
- Title: {title}
- Body: {body}
- Labels: {labels}

Continuing with response...
```

### Code Changes Fail

```
⚠️ Warning: Failed to address feedback item

Item: {feedback item}
Error: {error message}

**Status**: Deferred to GitHub issue #{issue_number}

Continuing with remaining feedback...
```

---

## Best Practices

### Writing Good Issue Titles

**Good:**
- "Add JavaScript unit tests for debounce/throttle decorators"
- "Refactor throttleEvent() to reduce complexity"
- "Document WebSocket reconnection behavior"

**Bad:**
- "Fix tests" (too vague)
- "TODO from review" (not actionable)
- "Improvement" (not specific)

### Prioritizing Feedback

**Priority order:**
1. Critical Issues (❌) - Must address before merge
2. Minor Issues (⚠️) - Address now if quick, defer if complex
3. Questions (❓) - Answer all in response
4. Nice-to-Have (💡) - Defer most to issues

### Commit Messages

When making changes, use clear commit messages:

```bash
git commit -m "fix(review): Add docstring to debounceEvent()

Addresses feedback from PR #40 review.
Added comprehensive JSDoc documentation for all parameters.

Review: .claude/reviews/pr-40/latest.md"
```

---

**Skill Version**: 1.0.0
**Last Updated**: 2025-01-12
**Maintained By**: Claude Code Team
