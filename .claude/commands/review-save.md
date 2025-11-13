# Review Pull Request and Save

Launch an autonomous agent to review a PR and save it to markdown.

**Arguments**:
`{pr-number}` or `{pr-url}` [additional requirements]

**Examples**:
```bash
/review-save 40
/review-save 40 Focus on security and memory leaks
/review-save https://github.com/owner/repo/pull/123 Check error handling
```

---

## Instructions

Launch a general-purpose agent to perform the PR review autonomously.

**Parse arguments first:**
- Extract PR number/URL from {ARG}
- Extract any additional requirements (everything after PR number)

**Then use the Task tool to launch an agent:**

**Description**: "Review PR and save to markdown"

**Prompt**:
```
You are reviewing Pull Request {number/url}.

{if additional requirements}
Additional focus areas: {requirements}
{endif}

Your task is to autonomously complete a comprehensive PR review and save it to markdown.

## Steps to Complete

### 1. Fetch PR Details

Use Bash tool:
```bash
gh pr view {number} --json title,body,additions,deletions,files,commits,headRefName,baseRefName,state,author
gh pr diff {number}
```

### 2. Perform Comprehensive Review

Review the PR following the same quality standards as the built-in /review command:

**Analyze:**
- Code quality and correctness
- Architecture and design patterns
- Test coverage and quality
- Security considerations
- Performance implications
- Documentation completeness
- Breaking changes
- Merge recommendation

{if additional requirements}
**Extra focus on**: {requirements}
{endif}

**Provide:**
- Summary (what the PR does)
- Code quality analysis (strengths ⭐⭐⭐⭐⭐ and issues ❌ ⚠️ 💡)
- File-by-file review (for significant files)
- Testing status
- Performance impact
- Security assessment
- Documentation quality
- Clear merge recommendation (APPROVED/APPROVED_WITH_CONDITIONS/CHANGES_REQUESTED)

### 3. Save Review to Markdown

**Create directory:**
```bash
mkdir -p .claude/reviews/pr-{number}
```

**Generate timestamp:**
```bash
timestamp=$(date +"%Y-%m-%d-%H%M%S")
```

**Save review to file:**
`.claude/reviews/pr-{number}/review-${timestamp}.md`

**Use this format:**
```markdown
# PR #{number} Review: {title}

**Reviewer**: Claude Code
**Date**: {YYYY-MM-DD HH:MM}
**Status**: {APPROVED | APPROVED_WITH_CONDITIONS | CHANGES_REQUESTED | COMMENTED}
**Repository**: {owner/repo}
**Branch**: {head} → {base}

{if additional requirements}
**Additional Focus Areas**: {requirements}
{endif}

---

## Summary

{1-2 paragraph overview}

**Type**: {Feature | Bugfix | Refactor | Documentation | Test | Chore}
**Impact**: {High | Medium | Low}
**Changes**: {additions} additions, {deletions} deletions across {file_count} files

---

## What This PR Does

{detailed description}

---

## Code Quality Analysis

### Strengths ⭐⭐⭐⭐⭐

{list strengths}

### Issues & Recommendations

#### Critical Issues ❌
{blocking issues or NONE}

#### Minor Issues ⚠️
{non-blocking issues}

#### Nice-to-Have Improvements 💡
{optional suggestions}

---

## File-by-File Review

{review significant files}

---

## Testing Status
{test coverage analysis}

---

## Performance Impact
{performance analysis}

---

## Security Considerations
{security assessment}

---

## Documentation Quality
{documentation review}

---

## Merge Recommendation

**Status**: {final status}

{justification}

---

**Reviewed by**: Claude Code
**Review Date**: {YYYY-MM-DD}
**Review Status**: {final status}
```

**Create symlink:**
```bash
cd .claude/reviews/pr-{number}
ln -sf review-${timestamp}.md latest.md
```

### 4. Update Review Index

**Read** `.claude/reviews/index.md`

**Update these sections:**

1. **Recent Reviews table** - Add to top (keep top 10):
```markdown
| #{number} | {title} | {date} | {status} | Claude Code | [View](pr-{number}/latest.md) |
```

2. **Reviews by Status** - Add under appropriate heading

3. **Reviews by PR** - Add or update PR section:
```markdown
### PR #{number} - {title}

**Reviews**: {count} total

- [{timestamp}](pr-{number}/review-{timestamp}.md) - {status}
  - **Summary**: {one-line}
  - **Recommendation**: {merge rec}
```

4. **Update statistics** - Increment counts, update percentages

**Write** updated content back to `.claude/reviews/index.md`

### 5. Report Completion

Return this message to the user:

```
✅ Review complete and saved!

**PR**: #{number} - {title}
**Status**: {APPROVED/APPROVED_WITH_CONDITIONS/CHANGES_REQUESTED}
**File**: .claude/reviews/pr-{number}/review-{timestamp}.md

**Quick access:**
- Latest: cat .claude/reviews/pr-{number}/latest.md
- Index: cat .claude/reviews/index.md

**Recommendation**: {one-line merge recommendation}
```

---

## Important Notes

- Follow the same review quality as built-in /review command
- Read .claude/skills/pr-reviewer.md for detailed guidelines
- Handle errors gracefully (PR not found, gh auth issues, etc.)
- For large PRs (>1000 lines), warn user but still complete review
- If additional focus areas were specified, emphasize those in review

Work through all steps autonomously and report when complete.
```

**Subagent Type**: general-purpose

**Model**: sonnet (for thorough analysis)

---

## What Happens

When you invoke this command:

1. ✅ Main Claude parses your arguments
2. ✅ Launches autonomous agent via Task tool
3. ✅ You see: "Review PR and save to markdown is running..."
4. ✅ Agent works independently (fetches, reviews, saves, updates index)
5. ✅ Agent reports back with summary
6. ✅ You get file path and quick access commands

The agent has access to all necessary tools:
- Bash (gh commands)
- Read (templates and existing files)
- Write (save review and update index)
- Grep/Glob (code searching if needed)

This allows the review to run completely autonomously while you work on other tasks!
