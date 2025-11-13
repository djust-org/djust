# Respond to PR Review

Launch an autonomous agent to address PR review feedback and save response.

**Arguments**:
`{pr-number}`

**Examples**:
```bash
/respond 40
```

---

## Instructions

Launch a general-purpose agent to respond to PR review feedback autonomously.

**Parse arguments:**
- Extract PR number from {ARG}

**Then use the Task tool to launch an agent:**

**Description**: "Respond to PR review feedback"

**Prompt**:
```
You are responding to the review for Pull Request #{number}.

Your task is to autonomously address all review feedback and save a response document.

## Steps to Complete

### 1. Read Latest Review

**Read the review file:**
```bash
cat .claude/reviews/pr-{number}/latest.md
```

**Parse and extract:**
- Review date and status
- Critical Issues (❌)
- Minor Issues (⚠️)
- Nice-to-Have Improvements (💡)
- Questions for Author (❓)
- Recommendations and next steps

### 2. Categorize Each Feedback Item

For each item, decide:

**Category A - Address Now** (make code changes):
- Quick fixes (< 30 minutes)
- Clear implementation path
- Non-controversial changes
- Examples: Add docstring, fix typo, improve naming

**Category B - Defer to Issue** (create GitHub issue):
- Large tasks (> 30 minutes)
- Needs design discussion
- Requires new infrastructure
- Examples: Add test suite, refactor architecture, new feature

**Category C - Acknowledge** (answer only):
- Questions needing explanation
- Suggestions to decline (with rationale)
- Already addressed in latest commit

### 3. Address Feedback (Category A)

For each "Address Now" item:

1. **Read relevant files** with Read tool
2. **Make changes** with Edit or Write tool
3. **Verify changes** compile/work
4. **Document in response** what was changed

**Track each change:**
```markdown
### ✅ Addressed: {Issue Title}

**Review Feedback:**
> {quoted feedback}

**Action Taken:**
- {description of changes made}

**Files Modified:**
- `{file}` (+{additions}/-{deletions})

**Verification:**
- [x] {verification item}
```

### 4. Create GitHub Issues (Category B)

For each "Defer to Issue" item:

**Create issue with gh CLI:**
```bash
gh issue create \
  --title "{clear, actionable title}" \
  --body "## Context

From PR #{number} review ({review_date})

**Original Feedback:**
> {quoted feedback}

## Problem

{what needs to be done}

## Proposed Solution

{approach if known}

## Acceptance Criteria

- [ ] {criterion 1}
- [ ] {criterion 2}

## Related

- PR #{number}
- Review: .claude/reviews/pr-{number}/latest.md

## Labels

{labels}" \
  --label "{label1}" \
  --label "{label2}"
```

**Track in response:**
```markdown
### 📋 Deferred: {Issue Title}

**Review Feedback:**
> {quoted feedback}

**Reason for Deferral:**
{why not addressed now}

**GitHub Issue Created:**
- **Issue**: #{issue_number} - {title}
- **Link**: https://github.com/{owner}/{repo}/issues/{issue_number}
- **Labels**: {labels}

**Timeline:**
{estimated timeline}
```

### 5. Answer Questions (Category C)

For each question:

```markdown
### ❓ Question: {Question}

**Review Question:**
> {quoted question}

**Answer:**
{thorough answer}
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

**Use this format:**
```markdown
# Response to PR #{number} Review

**PR**: #{number} - {title}
**Review Date**: {review_date}
**Review Status**: {status}
**Response Date**: {YYYY-MM-DD HH:MM}

**Review File**: [.claude/reviews/pr-{number}/latest.md](.claude/reviews/pr-{number}/latest.md)

---

## Summary

{1-2 paragraph summary}

**Actions Taken:**
- ✅ {count} issues addressed with code changes
- 📋 {count} issues deferred (GitHub issues created)
- ❓ {count} questions answered

**Files Modified:** {count}
**Issues Created:** {count}

---

## Feedback Addressed

{all feedback items categorized and addressed}

---

## Files Modified

{list files with changes}

---

## GitHub Issues Created

{list issues with links}

---

## Next Steps

**Before Merge:**
- [ ] {action}

**After Merge:**
- [ ] {action}

---

**Responded by**: {author}
**Response Date**: {YYYY-MM-DD}
**All Feedback Addressed**: {Yes/No/Partially}
```

**Create symlink:**
```bash
cd .claude/responses/pr-{number}
ln -sf response-${timestamp}.md latest.md
```

### 7. Update Response Index

**Create or read** `.claude/responses/index.md`

**Add entry:**
```markdown
| #{number} | {date} | {addressed_count} | {deferred_count} | [View](pr-{number}/latest.md) |
```

**Update statistics**

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
{list with links}

**Files Modified:**
{list files}

**Quick access:**
- Response: cat .claude/responses/pr-{number}/latest.md
- Review: cat .claude/reviews/pr-{number}/latest.md
```

---

## Important Notes

- Read .claude/skills/pr-response.md for detailed guidelines
- Use decision framework to categorize feedback appropriately
- Create clear, actionable GitHub issues for deferred items
- Test code changes before marking as addressed
- Handle errors gracefully (issue creation failures, etc.)

Work through all steps autonomously and report when complete.
```

**Subagent Type**: general-purpose

**Model**: sonnet (for thorough analysis and code changes)

---

## What Happens

When you invoke this command:

1. ✅ Main Claude parses PR number
2. ✅ Launches autonomous agent via Task tool
3. ✅ You see: "Respond to PR review feedback is running..."
4. ✅ Agent works independently:
   - Reads latest review
   - Makes code changes for quick fixes
   - Creates GitHub issues for deferred items
   - Answers questions
   - Saves response document
   - Updates index
5. ✅ Agent reports back with summary
6. ✅ You get file paths and issue links

The agent has access to all necessary tools:
- Bash (gh commands for issue creation)
- Read (review files, code files)
- Edit/Write (make code changes)
- Grep/Glob (code searching)

This allows you to systematically address all review feedback in one autonomous workflow!
