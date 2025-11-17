#!/bin/bash
#
# djust Phase Development Automator - Using Claude Code CLI
# Automates the systematic 9-step feature development process
#
# Based on DEVELOPMENT_PROCESS.md - implements comprehensive quality gates,
# testing, documentation, and PR review workflow for djust phases.
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Configuration
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
TEMP_DIR=$(mktemp -d)
SESSION_ID="phase-dev-$(date +%Y%m%d-%H%M%S)"
SESSION_START=$(date +%s)
trap "rm -rf $TEMP_DIR" EXIT

# Paths
SCRIPTS_DIR="$REPO_ROOT/scripts"
SESSIONS_DIR="$REPO_ROOT/.claude/sessions"
DOCS_DIR="$REPO_ROOT/docs"

# Create sessions directory if it doesn't exist
mkdir -p "$SESSIONS_DIR"

# Function to display colored output
log() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%H:%M:%S')] ERROR:${NC} $1" >&2
}

warn() {
    echo -e "${YELLOW}[$(date +'%H:%M:%S')] WARNING:${NC} $1"
}

info() {
    echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1"
}

highlight() {
    echo -e "${CYAN}$1${NC}"
}

section() {
    echo ""
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${MAGENTA}  $1${NC}"
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# Check if gh CLI is installed
check_gh_cli() {
    if ! command -v gh &> /dev/null; then
        error "GitHub CLI (gh) not found"
        echo ""
        echo "Please install GitHub CLI first:"
        echo "  brew install gh  (macOS)"
        echo "  or visit: https://cli.github.com/"
        echo ""
        exit 1
    fi

    # Check if authenticated
    if ! gh auth status &> /dev/null; then
        error "Not authenticated with GitHub CLI"
        echo ""
        echo "Please authenticate first:"
        echo "  gh auth login"
        exit 1
    fi
}

# Check if Claude Code is installed
check_claude_setup() {
    if ! command -v claude &> /dev/null; then
        error "Claude Code CLI not found"
        echo ""
        echo "Please install Claude Code first:"
        echo "  npm install -g @anthropic-ai/claude-code"
        exit 1
    fi
}

# Check if we're on a clean main branch
check_git_state() {
    local current_branch=$(git branch --show-current)

    if [ "$current_branch" != "main" ]; then
        error "Not on main branch (currently on: $current_branch)"
        echo ""
        echo "Please return to main branch first:"
        echo "  git checkout main"
        exit 1
    fi

    if ! git diff --quiet; then
        error "Working directory has unstaged changes"
        echo ""
        echo "Please commit or stash your changes first:"
        echo "  git stash"
        echo "  # or"
        echo "  git commit -am 'WIP'"
        exit 1
    fi

    if ! git diff --cached --quiet; then
        error "Staging area has uncommitted changes"
        echo ""
        echo "Please commit your changes first:"
        echo "  git commit"
        exit 1
    fi

    log "✓ Git state is clean (on main branch with no uncommitted changes)"
}

# Check Rust toolchain
check_rust() {
    if ! command -v cargo &> /dev/null; then
        error "Rust toolchain not found"
        echo ""
        echo "Please install Rust first:"
        echo "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
        exit 1
    fi

    local rust_version=$(rustc --version | awk '{print $2}')
    log "✓ Rust $rust_version installed"
}

# Check Python environment
check_python() {
    if ! command -v python3 &> /dev/null; then
        error "Python 3 not found"
        exit 1
    fi

    # Check for virtual environment
    if [ -z "$VIRTUAL_ENV" ]; then
        warn "No virtual environment activated"
        echo ""
        echo "It's recommended to activate a virtual environment:"
        echo "  source .venv/bin/activate"
        echo ""
        read -p "$(echo -e ${CYAN})Continue anyway? (y/N)$(echo -e ${NC}) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            info "Aborted by user"
            exit 0
        fi
    else
        log "✓ Virtual environment active: $VIRTUAL_ENV"
    fi
}

# Load phase information from docs
load_phase_info() {
    local phase_name="$1"

    log "Loading phase information for: $phase_name"

    # Check if phase documentation exists
    local phase_doc=""
    case "$phase_name" in
        "phase-2"|"p2"|"orm-jit")
            phase_doc="$DOCS_DIR/templates/ORM_JIT_API.md"
            ;;
        "phase-3"|"p3"|"query-optimizer")
            phase_doc="$DOCS_DIR/templates/ORM_JIT_API.md"
            ;;
        *)
            warn "Unknown phase: $phase_name"
            echo ""
            echo "Available phases:"
            echo "  phase-2, p2, orm-jit        - ORM JIT Query Optimizer"
            echo "  phase-3, p3, ...            - (Add more phases as needed)"
            echo ""
            return 1
            ;;
    esac

    if [ ! -f "$phase_doc" ]; then
        warn "Phase documentation not found: $phase_doc"
        return 1
    fi

    # Extract relevant section from documentation
    cat "$phase_doc" > "$TEMP_DIR/phase_context.md"

    log "✓ Loaded phase documentation"
    return 0
}

# Create comprehensive context for Claude
create_claude_context() {
    local phase_name="$1"
    local branch_name="$2"

    log "Creating comprehensive context file..."

    # Load development process
    local dev_process=""
    if [ -f "$REPO_ROOT/DEVELOPMENT_PROCESS.md" ]; then
        dev_process=$(cat "$REPO_ROOT/DEVELOPMENT_PROCESS.md")
    fi

    # Load phase-specific context
    local phase_context=""
    if [ -f "$TEMP_DIR/phase_context.md" ]; then
        phase_context=$(cat "$TEMP_DIR/phase_context.md")
    fi

    # Load CLAUDE.md (project instructions)
    local claude_md=""
    if [ -f "$REPO_ROOT/CLAUDE.md" ]; then
        claude_md=$(cat "$REPO_ROOT/CLAUDE.md")
    fi

    # Create comprehensive context file
    cat > "$TEMP_DIR/context.md" << EOF
# djust Phase Development - Autonomous Implementation

## Mission: Complete $phase_name Implementation

You are autonomously implementing **$phase_name** for the djust framework following the systematic 9-step development process documented in DEVELOPMENT_PROCESS.md.

## Session Information
- **Session ID**: $SESSION_ID
- **Branch**: $branch_name
- **Repository**: $REPO_ROOT
- **Start Time**: $(date)

## Your Autonomous Workflow

You will complete the following 9 steps systematically:

### Step 1: Pre-Implementation Planning (5-10 minutes)
- Review the phase specification below
- Identify all files that need changes (Rust + Python)
- Plan the implementation approach
- Create a comprehensive TODO list using TodoWrite tool
- Document key decisions in session log

### Step 2: Create Feature Branch
- Already created: \`$branch_name\`
- All work should be on this branch

### Step 3: Implement Core Functionality
- **Rust Implementation** (if needed):
  - Follow Rust best practices
  - Add comprehensive inline documentation
  - Use appropriate error handling
  - Consider performance implications

- **Python Implementation** (if needed):
  - Follow Python/Django conventions
  - Add type hints where beneficial
  - Maintain API consistency with existing code

- **Test as You Go**:
  - Write tests DURING implementation, not after
  - Run tests frequently to catch issues early
  - Fix failures immediately

### Step 4: Quality Assurance - Pre-commit Checks
**CRITICAL**: Run these BEFORE staging any files:

\`\`\`bash
# Rust checks (if Rust code changed)
cargo fmt --all
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all

# Python checks (if Python code changed)
ruff check python/
ruff format python/
pytest python/

# Full pre-commit suite
pre-commit run --all-files
\`\`\`

**If any check fails**: Fix immediately before proceeding.

### Step 5: Self-Review Before Committing
\`\`\`bash
# Review all changes
git diff

# Check for unwanted files
git status --short | grep -E "(__pycache__|\.pyc|\.DS_Store|target/debug)"

# If found, remove from tracking:
# git rm -r --cached <path>

# Stage only intended files
git add <specific-files>

# Verify staging area
git diff --cached
\`\`\`

### Step 6: Comprehensive Testing
- **Unit Tests**: Ensure all new code has tests
- **Integration Tests**: Test interactions between components
- **Edge Cases**: Document known limitations
- **Performance**: Add benchmarks if performance-critical

**Testing Checklist**:
- [ ] Rust tests pass: \`cargo test --all\`
- [ ] Python tests pass: \`pytest python/\`
- [ ] Benchmarks added (if applicable): \`cargo bench\`
- [ ] Edge cases documented
- [ ] No regression in existing tests

### Step 7: Documentation
**CRITICAL**: Update documentation DURING implementation, not after.

**Update These Files**:
- [ ] Inline code documentation (docstrings, comments)
- [ ] API documentation in \`docs/\`
- [ ] CLAUDE.md (if new patterns/features)
- [ ] README.md (if user-facing changes)
- [ ] Usage examples
- [ ] Known limitations section

**Documentation Standards**:
- Explain WHY, not just WHAT
- Include usage examples
- Document error conditions
- Note performance characteristics
- Describe edge cases and limitations

### Step 8: Final Validation
\`\`\`bash
# Run complete test suite
make test

# Build release version (Rust)
make build

# Run linters
make lint

# Verify no unwanted files
git status
\`\`\`

### Step 9: Create Pull Request
\`\`\`bash
# Create comprehensive PR with:
gh pr create --fill

# PR description should include:
# - What was implemented
# - Testing performed
# - Documentation updated
# - Known limitations
# - Performance characteristics (if applicable)
# - Screenshots/examples (if UI changes)

# Add label based on phase
gh pr edit --add-label "enhancement"

# Self-review the PR in browser
gh pr view --web
\`\`\`

## Phase Specification

$phase_context

## Development Process Reference

$dev_process

## Project Context

$claude_md

## Quality Standards - Checklist

Before marking this phase as complete, ensure:

**Code Quality**:
- [ ] No clippy warnings
- [ ] Code formatted (cargo fmt, ruff format)
- [ ] Type hints added (Python)
- [ ] Error handling comprehensive
- [ ] No unsafe code without justification

**Testing**:
- [ ] All tests pass (Rust + Python)
- [ ] New tests added for new functionality
- [ ] Edge cases covered
- [ ] No test regressions
- [ ] Performance benchmarks (if applicable)

**Documentation**:
- [ ] Inline documentation complete
- [ ] API docs updated
- [ ] Usage examples added
- [ ] Known limitations documented
- [ ] CLAUDE.md updated (if needed)

**Git Hygiene**:
- [ ] No __pycache__ or build artifacts committed
- [ ] Commit messages descriptive
- [ ] No WIP commits in final PR
- [ ] Clean git history

**PR Quality**:
- [ ] PR description comprehensive
- [ ] Self-reviewed code changes
- [ ] All CI checks passing
- [ ] Ready for review

## Session Logging

Create and maintain a session log at:
\`.claude/sessions/$SESSION_ID.md\`

Include:
- Planning decisions and reasoning
- Implementation approach
- Challenges faced and solutions
- Test results
- Documentation updates
- Time spent on each step
- Final checklist completion status

## Important Guidelines

- **Be Thorough**: Follow all 9 steps systematically
- **Test Early, Test Often**: Don't leave testing until the end
- **Document as You Code**: Write docs during implementation
- **Quality Over Speed**: Better to do it right than do it fast
- **Be Transparent**: Document all decisions and limitations
- **Follow Patterns**: Use existing code patterns as reference
- **Ask Questions**: If specification is unclear, note it in session log

## Available Tools

- File operations: Read, Write, Edit, Grep, Glob
- Shell commands: Bash (including make, cargo, pytest, git, gh)
- Todo tracking: TodoWrite
- Task delegation: Task (for complex searches)

## Exit Conditions

Phase is complete when:
- All 9 steps finished
- All quality checks pass
- PR created and self-reviewed
- Session log updated
- TodoWrite checklist shows all items complete

## Begin Implementation

Start with Step 1: Pre-Implementation Planning.
Create a comprehensive TODO list and begin systematic implementation!
EOF

    log "✓ Context file created: $TEMP_DIR/context.md"
}

# Run Claude Code with comprehensive context
run_claude_implementation() {
    section "Launching Claude Code for Phase Implementation"

    info "This will take 30-90 minutes depending on phase complexity..."
    info "Claude will work autonomously through all 9 steps"
    echo ""

    cd "$REPO_ROOT"

    # Run Claude with the context file
    claude -p "$(cat $TEMP_DIR/context.md)" \
        --allowedTools "Read,Write,Edit,Bash,Grep,Glob,Task,TodoWrite" \
        --permission-mode acceptEdits \
        --verbose

    local exit_code=$?

    if [ $exit_code -ne 0 ]; then
        error "Claude Code execution failed with exit code $exit_code"
        return $exit_code
    fi

    log "✓ Claude Code execution completed"
    return 0
}

# Show summary of changes made
show_summary() {
    section "Implementation Summary"

    # Show git changes
    if ! git diff --quiet || ! git diff --cached --quiet; then
        highlight "Changed files:"
        git status --short
        echo ""

        highlight "Diff summary:"
        git diff --stat
        git diff --cached --stat
        echo ""
    else
        info "No uncommitted changes"
    fi

    # Show commits on feature branch
    local current_branch=$(git branch --show-current)
    local commit_count=$(git log --oneline origin/main..HEAD 2>/dev/null | wc -l || echo 0)

    if [ "$commit_count" -gt 0 ]; then
        highlight "Commits on $current_branch:"
        git log --oneline origin/main..HEAD
        echo ""
    fi

    # Calculate session duration
    local session_end=$(date +%s)
    local session_duration=$((session_end - SESSION_START))
    local session_minutes=$((session_duration / 60))

    info "Session duration: ${session_minutes} minutes"
    info "Session log: .claude/sessions/${SESSION_ID}.md"

    # Check if PR was created
    if gh pr view &> /dev/null; then
        local pr_url=$(gh pr view --json url -q .url)
        highlight "PR Created: $pr_url"
    else
        warn "No PR found - implementation may be incomplete"
    fi
}

# Show usage information
show_usage() {
    cat << EOF
djust Phase Development Automator - Using Claude Code CLI

Usage: $0 [OPTIONS] <phase-name>

This script automates the systematic 9-step development process for djust phases
as documented in DEVELOPMENT_PROCESS.md.

Process Overview:
  1. Pre-Implementation Planning - Review specs, create TODO list
  2. Create Feature Branch - Automated branch creation
  3. Implement Core Functionality - Rust + Python implementation
  4. Quality Assurance - Pre-commit checks (clippy, ruff, tests)
  5. Self-Review - Check for unwanted files, review changes
  6. Comprehensive Testing - Unit, integration, edge cases, benchmarks
  7. Documentation - Inline docs, API docs, usage examples
  8. Final Validation - Full test suite, build, lint
  9. Create Pull Request - Comprehensive PR with self-review

Quality Gates:
  - cargo clippy (Rust linting)
  - cargo fmt (Rust formatting)
  - cargo test (Rust tests)
  - ruff check/format (Python linting/formatting)
  - pytest (Python tests)
  - pre-commit hooks
  - Documentation completeness
  - No __pycache__ or build artifacts
  - Performance benchmarks (when applicable)

Phase Names:
  phase-2, p2, orm-jit        - ORM JIT Query Optimizer
  phase-3, p3, ...            - (Add more as implemented)

Options:
  -h, --help        Show this help message
  -n, --dry-run     Only show planning, don't run Claude
  -b, --branch      Custom branch name (default: auto-generated)

Examples:
  $0 phase-2                              # Implement Phase 2
  $0 p2                                   # Short form
  $0 --dry-run phase-2                    # Preview only
  $0 --branch my-feature phase-2          # Custom branch name

Prerequisites:
  - GitHub CLI (gh) installed and authenticated
  - Claude Code CLI installed
  - Git repository on main branch (clean state)
  - Rust toolchain installed
  - Python 3 with virtual environment
  - Pre-commit hooks configured

Session Logs:
  All sessions are logged to: .claude/sessions/phase-dev-*.md

Documentation:
  Development process: DEVELOPMENT_PROCESS.md
  Project context: CLAUDE.md
  Phase specifications: docs/templates/ORM_JIT_API.md

EOF
}

# Parse command line arguments
DRY_RUN=false
CUSTOM_BRANCH=""
PHASE_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_usage
            exit 0
            ;;
        -n|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -b|--branch)
            CUSTOM_BRANCH="$2"
            shift 2
            ;;
        *)
            if [ -z "$PHASE_NAME" ]; then
                PHASE_NAME="$1"
                shift
            else
                error "Unknown option: $1"
                show_usage
                exit 1
            fi
            ;;
    esac
done

# Validate phase name provided
if [ -z "$PHASE_NAME" ]; then
    error "Phase name required"
    echo ""
    show_usage
    exit 1
fi

# Main execution
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║       djust Phase Development Automator (Claude Code)     ║"
    echo "║    Systematic 9-step process for high-quality features    ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    info "Session ID: $SESSION_ID"
    info "Phase: $PHASE_NAME"
    echo ""

    # Pre-flight checks
    section "Pre-flight Checks"
    check_gh_cli
    check_claude_setup
    check_git_state
    check_rust
    check_python

    # Load phase information
    section "Loading Phase Information"
    if ! load_phase_info "$PHASE_NAME"; then
        exit 1
    fi

    # Generate branch name
    local branch_name
    if [ -n "$CUSTOM_BRANCH" ]; then
        branch_name="$CUSTOM_BRANCH"
    else
        # Auto-generate branch name from phase
        branch_name="feature/${PHASE_NAME}-$(date +%Y%m%d)"
    fi

    info "Branch name: $branch_name"

    # Create feature branch
    section "Creating Feature Branch"

    if git show-ref --verify --quiet "refs/heads/$branch_name"; then
        error "Branch already exists: $branch_name"
        echo ""
        read -p "$(echo -e ${CYAN})Switch to existing branch? (y/N)$(echo -e ${NC}) " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            git checkout "$branch_name"
            log "✓ Switched to existing branch"
        else
            exit 1
        fi
    else
        git checkout -b "$branch_name"
        log "✓ Created and switched to branch: $branch_name"
    fi

    # Create Claude context
    section "Preparing Claude Context"
    create_claude_context "$PHASE_NAME" "$branch_name"

    if [ "$DRY_RUN" = true ]; then
        info "Dry run mode - context file created at: $TEMP_DIR/context.md"
        info "Review the context and run without --dry-run to execute"
        exit 0
    fi

    # Confirmation prompt
    echo ""
    read -p "$(echo -e ${CYAN})Start autonomous phase implementation? (y/N)$(echo -e ${NC}) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Aborted by user"
        exit 0
    fi

    # Run Claude implementation
    run_claude_implementation

    local result=$?

    # Show summary
    show_summary

    if [ $result -ne 0 ]; then
        error "❌ Phase implementation failed"
        echo ""
        info "Review session log: .claude/sessions/${SESSION_ID}.md"
        exit $result
    fi

    # Success
    section "✅ Phase Implementation Complete"

    echo ""
    log "Next steps:"
    echo "  1. Review the PR: gh pr view --web"
    echo "  2. Address any CI failures"
    echo "  3. Request review from team"
    echo "  4. Address PR feedback"
    echo "  5. Merge when approved"
    echo ""
    info "Session log: .claude/sessions/${SESSION_ID}.md"
    echo ""
}

# Run main function
main
