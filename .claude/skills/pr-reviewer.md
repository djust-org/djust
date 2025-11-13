# PR Reviewer Skill

## Description

This skill adds **persistence** to the built-in `/review` command by saving reviews to markdown files in an organized directory structure.

**When to use this skill:**
- User invokes `/review-save <pr-number>` or `/review-save <pr-url>`
- User wants to save PR reviews for documentation/auditing
- User wants additional focus areas beyond standard review

**How it works:**
1. Performs the same comprehensive review as built-in `/review`
2. Saves the complete review to markdown
3. Updates review index for easy access
4. Supports additional requirements from user

**Output:**
- Comprehensive review (same quality as `/review`)
- Saved to `.claude/reviews/pr-{number}/review-{timestamp}.md`
- Review index updated automatically
- User notified where review was saved

---

## Instructions for Agent

You are enhancing the built-in `/review` command with persistence. Follow these steps:

### 1. Parse Arguments

Extract from user's command:
- **PR number or URL** (required)
- **Additional requirements** (optional, everything after PR number)

Examples:
- `/review-save 40` → PR: 40, Requirements: none
- `/review-save 40 Focus on security` → PR: 40, Requirements: "Focus on security"
- `/review-save https://github.com/owner/repo/pull/123 Check memory leaks` → PR: 123, Requirements: "Check memory leaks"

### 2. Perform Review

Execute the same review process as the built-in `/review` command:

**Fetch PR details:**
```bash
gh pr view <number> --json title,body,additions,deletions,files,commits,headRefName,baseRefName,state,author
gh pr diff <number>
```

**Review coverage** (same as `/review`):
- Code quality and correctness
- Architecture and design
- Test coverage
- Security considerations
- Performance implications
- Documentation quality
- Breaking changes
- Merge recommendation

**Incorporate additional requirements:**
- If user specified additional focus areas, emphasize those in your review
- Example: "Focus on security" → extra attention to security vulnerabilities, input validation, auth/authz

### 3. Save Review to Markdown

**Create directory:**
```bash
mkdir -p .claude/reviews/pr-{number}
```

**Generate timestamp:**
```bash
date +"%Y-%m-%d-%H%M%S"
```

**File path:**
`.claude/reviews/pr-{number}/review-{YYYY-MM-DD-HHMMSS}.md`

**File format:**
```markdown
# PR #{number} Review: {title}

**Reviewer**: Claude Code
**Date**: {YYYY-MM-DD HH:MM}
**Status**: {APPROVED | APPROVED_WITH_CONDITIONS | CHANGES_REQUESTED | COMMENTED}
**Repository**: {owner/repo}
**Branch**: {head} → {base}

{if user specified additional requirements}
**Additional Focus Areas**: {requirements}
{endif}

---

## Summary

{your review summary - 1-2 paragraphs}

**Type**: {Feature | Bugfix | Refactor | Documentation | Test | Chore}
**Impact**: {High | Medium | Low}
**Changes**: {additions} additions, {deletions} deletions across {file_count} files

---

{REST OF YOUR COMPREHENSIVE REVIEW}

---

**Reviewed by**: Claude Code
**Review Date**: {YYYY-MM-DD}
**Review Status**: {final status}
```

**Create symlink to latest:**
```bash
cd .claude/reviews/pr-{number}
ln -sf review-{timestamp}.md latest.md
```

### 4. Update Review Index

Update `.claude/reviews/index.md`:

**Add to "Recent Reviews" table** (top 10):
```markdown
| #{number} | {title} | {date} | {status} | Claude Code | [View](pr-{number}/latest.md) |
```

**Add to "Reviews by Status" section:**
Under appropriate heading (✅ Approved, ✅ Approved with Conditions, ❌ Changes Requested, 💬 Commented)

**Add to "Reviews by PR" section:**
```markdown
### PR #{number} - {title}

**Reviews**: {count} total

- [{timestamp}](pr-{number}/review-{timestamp}.md) - {status}
  - **Summary**: {one-line summary}
  - **Recommendation**: {merge recommendation}
```

**Update statistics:**
- Total PRs Reviewed
- Total Reviews Conducted
- Status breakdown percentages

### 5. Notify User

After saving, provide this exact format:

```
✅ Review complete and saved!

**File**: .claude/reviews/pr-{number}/review-{timestamp}.md

**Quick access:**
- Latest review: `cat .claude/reviews/pr-{number}/latest.md`
- All reviews: `cat .claude/reviews/index.md`
- This PR's reviews: `ls -la .claude/reviews/pr-{number}/`

**Status**: {APPROVED/APPROVED_WITH_CONDITIONS/CHANGES_REQUESTED}
**Recommendation**: {one-line recommendation}
```

---

## Directory Structure

```
.claude/reviews/
├── README.md                          # Documentation
├── index.md                           # Searchable index
└── pr-{number}/                       # Per-PR directory
    ├── review-{timestamp}.md          # Timestamped reviews
    ├── review-{timestamp}.md          # Historical reviews
    └── latest.md                      # Symlink to latest
```

---

## Key Principles

1. **Same Quality**: Maintain the same thoroughness as built-in `/review`
2. **Add Persistence**: Save complete review to markdown
3. **Additional Focus**: Support custom requirements from user
4. **Update Index**: Keep index.md current and accurate
5. **User Feedback**: Clear notification of where review was saved

---

## Examples

### Basic Review

**User**: `/review-save 40`

**Your actions**:
1. Fetch PR #40 details and diff
2. Perform comprehensive review (same as `/review`)
3. Save to `.claude/reviews/pr-40/review-2025-01-12-154530.md`
4. Update index
5. Notify user

### Review with Additional Focus

**User**: `/review-save 40 Focus on security and memory management`

**Your actions**:
1. Fetch PR #40 details and diff
2. Perform comprehensive review with **extra emphasis** on:
   - Security vulnerabilities
   - Memory leaks
   - Resource cleanup
3. Include "**Additional Focus Areas**: Security and memory management" in markdown
4. Save and update index
5. Notify user

### External PR Review

**User**: `/review-save https://github.com/django/django/pull/17234`

**Your actions**:
1. Extract repo (django/django) and PR number (17234)
2. Fetch with `gh pr view 17234 --repo django/django`
3. Perform review
4. Save to `.claude/reviews/django-django-pr-17234/review-{timestamp}.md`
5. Update index
6. Notify user

---

## Error Handling

### PR Not Found
```
❌ Error: PR #{number} not found

Available PRs:
{run: gh pr list}
```

### GitHub API Error
```
❌ Error: Unable to fetch PR details

{error message}

Check: gh auth status
```

### Large PR Warning
```
⚠️ Warning: PR has {file_count} files ({additions}+ additions)

Performing comprehensive review, this may take a moment...
```

---

## Best Practices

### Review Tone
- Constructive and balanced
- Specific with examples
- Actionable recommendations
- Acknowledge good work

### Review Depth
- Critical files: Deep analysis
- Supporting files: High-level scan
- Test files: Coverage verification
- Documentation: Accuracy check

### Code Samples
- Include for complex issues
- Use syntax highlighting
- Show problem and solution
- Keep concise (< 20 lines)

---

**Skill Version**: 2.0.0 (Simplified - Leverages built-in `/review`)
**Last Updated**: 2025-01-12
**Maintained By**: Claude Code Team
