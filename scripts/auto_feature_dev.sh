#!/bin/bash
#
# Generic Feature Development Automator - Using Claude Code CLI
# Automates systematic multi-phase feature development for any project
#
# This is a generic version that can be adapted to any project by providing
# a configuration file (.claude/feature_config.yaml)
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
SESSION_ID="feature-dev-$(date +%Y%m%d-%H%M%S)"
SESSION_START=$(date +%s)
trap "rm -rf $TEMP_DIR" EXIT

# Paths
CLAUDE_DIR="$REPO_ROOT/.claude"
SCRIPTS_DIR="$CLAUDE_DIR/scripts"
SESSIONS_DIR="$CLAUDE_DIR/sessions"
CONFIG_FILE="$CLAUDE_DIR/feature_config.yaml"

# Create necessary directories
mkdir -p "$SESSIONS_DIR"

# Default configuration (can be overridden by config file)
PROJECT_NAME="project"
TECH_STACK="python"  # Options: python, rust, python-rust, nodejs, etc.
QUALITY_GATES=()
TEST_COMMANDS=()
BUILD_COMMANDS=()
DOC_PATHS=()

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

# Load configuration from YAML file (simple parser)
load_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        warn "Configuration file not found: $CONFIG_FILE"
        warn "Using default configuration. Create $CONFIG_FILE for customization."
        return 0
    fi

    log "Loading configuration from $CONFIG_FILE"

    # Simple YAML parsing (supports basic key-value and arrays)
    while IFS= read -r line; do
        # Skip comments and empty lines
        [[ "$line" =~ ^#.*$ ]] && continue
        [[ -z "$line" ]] && continue

        # Parse key-value pairs
        if [[ "$line" =~ ^([a-zA-Z_][a-zA-Z0-9_]*):\ *(.+)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local value="${BASH_REMATCH[2]}"

            # Strip quotes from value if present
            value="${value#\"}"  # Remove leading quote
            value="${value%\"}"  # Remove trailing quote

            case "$key" in
                project_name) PROJECT_NAME="$value" ;;
                tech_stack) TECH_STACK="$value" ;;
                main_branch) MAIN_BRANCH="$value" ;;
            esac
        fi

        # Parse arrays (simple format: - item)
        if [[ "$line" =~ ^-\ +(.+)$ ]]; then
            local item="${BASH_REMATCH[1]}"
            # Store in appropriate array based on context
            # (This is simplified - real YAML parser would be better)
        fi
    done < "$CONFIG_FILE"

    log "✓ Configuration loaded: project=$PROJECT_NAME, stack=$TECH_STACK"
}

# Check prerequisites based on tech stack
check_prerequisites() {
    section "Checking Prerequisites"

    # Always need git and gh
    if ! command -v git &> /dev/null; then
        error "Git not found"
        exit 1
    fi

    if ! command -v gh &> /dev/null; then
        error "GitHub CLI (gh) not found. Install: brew install gh"
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        error "Not authenticated with GitHub CLI. Run: gh auth login"
        exit 1
    fi

    # Check Claude Code CLI
    if ! command -v claude &> /dev/null; then
        error "Claude Code CLI not found"
        echo "Install: npm install -g @anthropic-ai/claude-code"
        exit 1
    fi

    # Tech stack specific checks
    case "$TECH_STACK" in
        python|python-rust)
            if ! command -v python3 &> /dev/null; then
                error "Python 3 not found"
                exit 1
            fi
            log "✓ Python $(python3 --version | awk '{print $2}')"
            ;;
    esac

    case "$TECH_STACK" in
        rust|python-rust)
            if ! command -v cargo &> /dev/null; then
                error "Rust toolchain not found"
                echo "Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
                exit 1
            fi
            log "✓ Rust $(rustc --version | awk '{print $2}')"
            ;;
    esac

    case "$TECH_STACK" in
        nodejs|typescript)
            if ! command -v node &> /dev/null; then
                error "Node.js not found"
                exit 1
            fi
            log "✓ Node.js $(node --version)"
            ;;
    esac

    log "✓ All prerequisites met"
}

# Check git state
check_git_state() {
    local main_branch="${MAIN_BRANCH:-main}"
    local current_branch=$(git branch --show-current)

    if [ "$current_branch" != "$main_branch" ]; then
        error "Not on $main_branch branch (currently on: $current_branch)"
        echo ""
        echo "Please return to $main_branch branch first:"
        echo "  git checkout $main_branch"
        exit 1
    fi

    if ! git diff --quiet || ! git diff --cached --quiet; then
        error "Working directory has uncommitted changes"
        echo ""
        echo "Please commit or stash your changes first:"
        echo "  git stash"
        exit 1
    fi

    log "✓ Git state is clean (on $main_branch branch)"
}

# Parse feature specification file (YAML or Markdown)
parse_feature_spec() {
    local feature_file="$1"

    if [ ! -f "$feature_file" ]; then
        error "Feature specification file not found: $feature_file"
        echo ""
        echo "Create a feature spec file (YAML or Markdown):"
        echo ""
        echo "YAML format:"
        cat << 'EOF'
feature: "jit-auto-serialization"
phases:
  - name: phase-1
    title: Template Variable Extraction
    ...
EOF
        echo ""
        echo "OR Markdown format:"
        cat << 'EOF'
## Phase 1: Template Variable Extraction
**Duration**: 2 days
**Dependencies**: None

### Deliverables
- [ ] Item 1
...
EOF
        exit 1
    fi

    log "Parsing feature specification: $feature_file"

    # Detect format (YAML vs Markdown)
    local file_ext="${feature_file##*.}"

    if [[ "$file_ext" == "md" || "$file_ext" == "markdown" ]]; then
        # Markdown format - extract phases from headers
        PHASES=()
        while IFS= read -r line; do
            # Match: ## Phase 1: Title  or  ## Phase 1 - Title
            if [[ "$line" =~ ^##\ +Phase\ +([0-9]+)[:\-]\ +(.+)$ ]]; then
                local phase_num="${BASH_REMATCH[1]}"
                local phase_title="${BASH_REMATCH[2]}"
                PHASES+=("phase-${phase_num}")
                log "  Found Phase $phase_num: $phase_title"
            fi
        done < "$feature_file"

        FEATURE_FORMAT="markdown"
    else
        # YAML format - extract phase names
        PHASES=()
        while IFS= read -r line; do
            if [[ "$line" =~ ^\ *-\ +name:\ +(.+)$ ]]; then
                PHASES+=("${BASH_REMATCH[1]}")
            fi
        done < "$feature_file"

        FEATURE_FORMAT="yaml"
    fi

    if [ ${#PHASES[@]} -eq 0 ]; then
        error "No phases found in feature specification"
        echo ""
        if [[ "$FEATURE_FORMAT" == "markdown" ]]; then
            echo "Expected markdown format:"
            echo "  ## Phase 1: Title"
            echo "  ## Phase 2: Title"
        else
            echo "Expected YAML format:"
            echo "  phases:"
            echo "    - name: phase-1"
        fi
        exit 1
    fi

    log "✓ Found ${#PHASES[@]} phase(s): ${PHASES[*]} (format: $FEATURE_FORMAT)"
}

# Create comprehensive context for Claude (multi-phase aware)
create_claude_context() {
    local feature_name="$1"
    local branch_name="$2"
    local phase_name="$3"
    local phase_index="$4"
    local total_phases="$5"
    local feature_spec="$6"

    log "Creating context for $phase_name ($((phase_index + 1))/$total_phases)..."

    # Load feature specification
    local feature_context=""
    if [ -f "$feature_spec" ]; then
        feature_context=$(cat "$feature_spec")
    fi

    # Load development process (if exists)
    local dev_process=""
    if [ -f "$REPO_ROOT/DEVELOPMENT_PROCESS.md" ]; then
        dev_process=$(cat "$REPO_ROOT/DEVELOPMENT_PROCESS.md")
    fi

    # Load project context (if exists)
    local project_context=""
    if [ -f "$REPO_ROOT/CLAUDE.md" ]; then
        project_context=$(cat "$REPO_ROOT/CLAUDE.md")
    fi

    # Create comprehensive context file
    cat > "$TEMP_DIR/context.md" << EOF
# Feature Development - Multi-Phase Implementation

## Mission: Complete $feature_name - $phase_name

You are autonomously implementing **$phase_name** (phase $((phase_index + 1)) of $total_phases) for the **$feature_name** feature.

## Multi-Phase Context

This is part of a multi-phase feature implementation:
- **Current Phase**: $phase_name ($((phase_index + 1))/$total_phases)
- **Feature**: $feature_name
- **Branch**: $branch_name
- **Session ID**: $SESSION_ID

## Session Information
- **Repository**: $REPO_ROOT
- **Project**: $PROJECT_NAME
- **Tech Stack**: $TECH_STACK
- **Start Time**: $(date)

## Your Autonomous Workflow

Complete the following steps systematically:

### Step 1: Pre-Implementation Planning (5-10 minutes)
- Review the phase specification below
- Review previous phases (if any) to understand context
- Identify all files that need changes
- Plan the implementation approach
- Create a comprehensive TODO list using TodoWrite tool
- Document key decisions in session log

### Step 2: Implementation Strategy

**Multi-Phase Awareness**:
- This is phase $((phase_index + 1)) of $total_phases
- Previous phases may have established patterns - follow them
- Consider how this phase integrates with previous/future phases
- Maintain consistency with existing code style

**Tech Stack Specific ($TECH_STACK)**:

$(case "$TECH_STACK" in
    python)
        echo "- Follow PEP 8 and Python best practices"
        echo "- Add type hints where beneficial"
        echo "- Write docstrings for all public APIs"
        echo "- Use pytest for testing"
        ;;
    rust)
        echo "- Follow Rust best practices"
        echo "- Use cargo fmt and cargo clippy"
        echo "- Add comprehensive documentation comments"
        echo "- Write tests in same file or tests/ directory"
        ;;
    python-rust)
        echo "**Python Code**:"
        echo "- Follow PEP 8 and Python best practices"
        echo "- Add type hints where beneficial"
        echo "- Write docstrings for all public APIs"
        echo ""
        echo "**Rust Code**:"
        echo "- Follow Rust best practices"
        echo "- Use cargo fmt and cargo clippy"
        echo "- Add comprehensive documentation comments"
        echo "- Ensure PyO3 bindings are well-documented"
        ;;
    nodejs|typescript)
        echo "- Follow Node.js/TypeScript best practices"
        echo "- Use ESLint and Prettier"
        echo "- Add JSDoc or TypeScript types"
        echo "- Write tests with Jest or similar"
        ;;
esac)

### Step 3: Implementation
- Implement the phase functionality
- Write tests DURING implementation, not after
- Run tests frequently to catch issues early
- Fix failures immediately
- Document as you code (inline comments, docstrings)

### Step 4: Quality Assurance

Run these checks BEFORE staging any files:

$(case "$TECH_STACK" in
    python)
        echo "\`\`\`bash"
        echo "# Python checks"
        echo "ruff check ."
        echo "ruff format ."
        echo "pytest"
        echo "\`\`\`"
        ;;
    rust)
        echo "\`\`\`bash"
        echo "# Rust checks"
        echo "cargo fmt --all"
        echo "cargo clippy --all-targets --all-features -- -D warnings"
        echo "cargo test --all"
        echo "\`\`\`"
        ;;
    python-rust)
        echo "\`\`\`bash"
        echo "# Rust checks"
        echo "cargo fmt --all"
        echo "cargo clippy --all-targets --all-features -- -D warnings"
        echo "cargo test --all"
        echo ""
        echo "# Python checks"
        echo "ruff check python/"
        echo "ruff format python/"
        echo "pytest python/"
        echo "\`\`\`"
        ;;
    nodejs|typescript)
        echo "\`\`\`bash"
        echo "# Node.js checks"
        echo "npm run lint"
        echo "npm run format"
        echo "npm test"
        echo "\`\`\`"
        ;;
esac)

**If any check fails**: Fix immediately before proceeding.

### Step 5: Self-Review
\`\`\`bash
# Review all changes
git diff

# Check for unwanted files
git status --short | grep -E "(__pycache__|\.pyc|\.DS_Store|node_modules|target/debug|\.log)"

# If found, add to .gitignore or remove from tracking

# Stage only intended files
git add <specific-files>

# Verify staging area
git diff --cached
\`\`\`

### Step 6: Testing
- **Unit Tests**: Test individual components
- **Integration Tests**: Test component interactions
- **Edge Cases**: Test boundary conditions
- **Performance**: Add benchmarks if performance-critical

### Step 7: Documentation
Update documentation DURING implementation:
- [ ] Inline code documentation (comments, docstrings)
- [ ] API documentation (if public API)
- [ ] Usage examples
- [ ] Known limitations
- [ ] Update README.md (if user-facing changes)

### Step 8: Phase Commit

Create a commit for this phase:

\`\`\`bash
# Create descriptive commit
git commit -m "feat($phase_name): <brief description>

<Detailed description of what was implemented>

- List key changes
- Mention any important decisions
- Note any limitations

Phase $((phase_index + 1))/$total_phases of $feature_name"
\`\`\`

### Step 9: Phase Completion Check

Before marking this phase complete, ensure:

- [ ] All tests pass
- [ ] Code formatted and linted
- [ ] Documentation updated
- [ ] No unwanted files committed
- [ ] Commit created with descriptive message
- [ ] Session log updated

## Feature Specification

$feature_context

## Development Process Reference

$dev_process

## Project Context

$project_context

## Multi-Phase Execution Notes

**Important**:
- After completing this phase, the script will automatically proceed to the next phase
- Each phase gets its own commit
- Final PR will be created after ALL phases complete
- If this phase fails validation, the entire feature stops
- Session log tracks progress across all phases

## Session Logging

Update the session log at:
\`.claude/sessions/$SESSION_ID.md\`

Include:
- Phase $((phase_index + 1))/$total_phases: $phase_name
- Planning decisions and reasoning
- Implementation approach
- Challenges faced and solutions
- Test results
- Time spent on this phase

## Available Tools

- File operations: Read, Write, Edit, Grep, Glob
- Shell commands: Bash (git, testing, building)
- Todo tracking: TodoWrite
- Task delegation: Task (for complex searches)

## Exit Conditions

Phase is complete when:
- All quality checks pass
- Tests pass
- Documentation updated
- Commit created
- Session log updated
- TodoWrite checklist complete

## Begin Phase $((phase_index + 1))/$total_phases Implementation

Start with Step 1: Pre-Implementation Planning.
Create TODO list and begin systematic implementation!
EOF

    log "✓ Context file created for phase $((phase_index + 1))/$total_phases"
}

# Run Claude Code for a single phase
run_phase_implementation() {
    local phase_name="$1"
    local phase_index="$2"
    local total_phases="$3"

    section "Phase $((phase_index + 1))/$total_phases: $phase_name"

    info "Claude is now working on this phase..."
    info "This may take 30-90 minutes depending on complexity..."
    echo ""

    cd "$REPO_ROOT"

    # Run Claude with the context file
    claude -p "$(cat $TEMP_DIR/context.md)" \
        --allowedTools "Read,Write,Edit,Bash,Grep,Glob,Task,TodoWrite" \
        --permission-mode acceptEdits \
        --verbose

    local exit_code=$?

    if [ $exit_code -ne 0 ]; then
        error "Claude Code execution failed for $phase_name (exit code $exit_code)"
        return $exit_code
    fi

    log "✓ Phase $phase_name completed successfully"
    return 0
}

# Execute all phases in sequence
execute_all_phases() {
    local feature_name="$1"
    local branch_name="$2"
    local feature_spec="$3"

    local total_phases=${#PHASES[@]}
    local completed_phases=0

    section "Multi-Phase Execution: $total_phases phase(s)"

    for i in "${!PHASES[@]}"; do
        local phase_name="${PHASES[$i]}"

        # Create context for this phase
        create_claude_context "$feature_name" "$branch_name" "$phase_name" "$i" "$total_phases" "$feature_spec"

        # Execute phase
        if ! run_phase_implementation "$phase_name" "$i" "$total_phases"; then
            error "❌ Feature implementation failed at phase $((i + 1))/$total_phases: $phase_name"
            echo ""
            info "Completed phases: $completed_phases/$total_phases"
            info "Failed phase: $phase_name"
            info "Session log: .claude/sessions/${SESSION_ID}.md"
            return 1
        fi

        completed_phases=$((completed_phases + 1))

        # Brief pause between phases
        if [ $((i + 1)) -lt $total_phases ]; then
            log "Phase $((i + 1))/$total_phases complete. Moving to next phase in 3 seconds..."
            sleep 3
        fi
    done

    log "✓ All $total_phases phase(s) completed successfully!"
    return 0
}

# Create final PR after all phases
create_final_pr() {
    local feature_name="$1"
    local branch_name="$2"

    section "Creating Pull Request"

    # Check if there are commits to PR
    local commit_count=$(git log --oneline origin/$(git rev-parse --abbrev-ref HEAD@{upstream} 2>/dev/null || echo "main")..HEAD 2>/dev/null | wc -l || echo 0)

    if [ "$commit_count" -eq 0 ]; then
        warn "No commits found on this branch - nothing to PR"
        return 1
    fi

    log "Creating PR with $commit_count commit(s)..."

    # Create PR
    gh pr create --fill --label "enhancement"

    local pr_url=$(gh pr view --json url -q .url 2>/dev/null || echo "")

    if [ -n "$pr_url" ]; then
        highlight "✓ PR Created: $pr_url"

        # Open in browser
        if command -v open &> /dev/null; then
            open "$pr_url"
        fi
    else
        warn "PR created but URL not available"
    fi
}

# Show summary
show_summary() {
    section "Feature Implementation Summary"

    local session_end=$(date +%s)
    local session_duration=$((session_end - SESSION_START))
    local session_minutes=$((session_duration / 60))

    # Show commits
    local current_branch=$(git branch --show-current)
    local commit_count=$(git log --oneline origin/main..HEAD 2>/dev/null | wc -l || echo 0)

    if [ "$commit_count" -gt 0 ]; then
        highlight "Commits on $current_branch:"
        git log --oneline origin/main..HEAD
        echo ""
    fi

    info "Total phases completed: ${#PHASES[@]}"
    info "Total time: ${session_minutes} minutes"
    info "Session log: .claude/sessions/${SESSION_ID}.md"

    # Check if PR exists
    if gh pr view &> /dev/null; then
        local pr_url=$(gh pr view --json url -q .url)
        highlight "PR: $pr_url"
    fi
}

# Show usage
show_usage() {
    cat << EOF
Generic Feature Development Automator - Using Claude Code CLI

Usage: $0 [OPTIONS] <feature-spec-file>

This script automates multi-phase feature development for any project.

How It Works:
  1. Reads feature specification with multiple phases
  2. Executes each phase in sequence
  3. Creates a commit per phase
  4. Validates quality gates between phases
  5. Creates final PR after all phases complete

Feature Spec Format (YAML):
  feature: "My Feature"
  description: "Feature description"
  phases:
    - name: phase-1
      title: "Phase 1 Title"
      description: "Phase 1 description"
      docs: "path/to/docs.md#section"
      dependencies: []
    - name: phase-2
      title: "Phase 2 Title"
      dependencies: [phase-1]

Options:
  -h, --help        Show this help message
  -n, --dry-run     Only show planning, don't execute
  -b, --branch      Custom branch name (default: auto-generated)
  -s, --single      Execute single phase only (specify phase name)

Examples:
  $0 features/jit-serialization.yaml          # Execute all phases
  $0 --dry-run features/new-feature.yaml      # Preview only
  $0 --single phase-2 features/my-feat.yaml   # Execute one phase
  $0 --branch custom-name features/feat.yaml  # Custom branch

Configuration:
  Create .claude/feature_config.yaml to customize:
    project_name: "My Project"
    tech_stack: "python-rust"  # python, rust, nodejs, typescript, etc.
    main_branch: "main"
    quality_gates:
      - "cargo clippy"
      - "pytest"

Prerequisites:
  - GitHub CLI (gh) installed and authenticated
  - Claude Code CLI installed
  - Git repository on main branch (clean state)
  - Tech stack tools installed (based on config)

Session Logs:
  All sessions logged to: .claude/sessions/feature-dev-*.md

EOF
}

# Parse command line arguments
DRY_RUN=false
CUSTOM_BRANCH=""
SINGLE_PHASE=""
FEATURE_SPEC=""

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
        -s|--single)
            SINGLE_PHASE="$2"
            shift 2
            ;;
        *)
            if [ -z "$FEATURE_SPEC" ]; then
                FEATURE_SPEC="$1"
                shift
            else
                error "Unknown option: $1"
                show_usage
                exit 1
            fi
            ;;
    esac
done

# Validate feature spec provided
if [ -z "$FEATURE_SPEC" ]; then
    error "Feature specification file required"
    echo ""
    show_usage
    exit 1
fi

# Main execution
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║    Generic Feature Development Automator (Claude Code)    ║"
    echo "║      Multi-phase systematic feature implementation        ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    info "Session ID: $SESSION_ID"
    info "Feature Spec: $FEATURE_SPEC"
    echo ""

    # Load configuration
    load_config

    # Pre-flight checks
    check_prerequisites
    check_git_state

    # Parse feature specification
    parse_feature_spec "$FEATURE_SPEC"

    # Handle single phase execution
    if [ -n "$SINGLE_PHASE" ]; then
        # Check if phase exists
        if [[ ! " ${PHASES[@]} " =~ " ${SINGLE_PHASE} " ]]; then
            error "Phase not found: $SINGLE_PHASE"
            error "Available phases: ${PHASES[*]}"
            exit 1
        fi
        PHASES=("$SINGLE_PHASE")
        info "Single phase mode: $SINGLE_PHASE"
    fi

    # Generate branch name
    local branch_name
    if [ -n "$CUSTOM_BRANCH" ]; then
        branch_name="$CUSTOM_BRANCH"
    else
        # Auto-generate from feature spec filename
        local feature_name=$(basename "$FEATURE_SPEC" .yaml)
        branch_name="feature/${feature_name}-$(date +%Y%m%d)"
    fi

    info "Branch: $branch_name"
    info "Phases to execute: ${#PHASES[@]}"
    echo ""

    # Create feature branch
    section "Creating Feature Branch"

    if git show-ref --verify --quiet "refs/heads/$branch_name"; then
        warn "Branch already exists: $branch_name"
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
        log "✓ Created and switched to: $branch_name"
    fi

    if [ "$DRY_RUN" = true ]; then
        # Create context for first phase only (preview)
        create_claude_context "feature" "$branch_name" "${PHASES[0]}" 0 "${#PHASES[@]}" "$FEATURE_SPEC"
        info "Dry run mode - context created at: $TEMP_DIR/context.md"
        info "Review and run without --dry-run to execute"
        exit 0
    fi

    # Confirmation prompt
    echo ""
    read -p "$(echo -e ${CYAN})Start multi-phase feature implementation? (y/N)$(echo -e ${NC}) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Aborted by user"
        exit 0
    fi

    # Execute all phases
    local feature_name=$(basename "$FEATURE_SPEC" .yaml)
    if ! execute_all_phases "$feature_name" "$branch_name" "$FEATURE_SPEC"; then
        error "❌ Feature implementation failed"
        show_summary
        exit 1
    fi

    # Create final PR
    create_final_pr "$feature_name" "$branch_name"

    # Show summary
    show_summary

    section "✅ Feature Implementation Complete"

    echo ""
    log "Next steps:"
    echo "  1. Review the PR: gh pr view --web"
    echo "  2. Address any CI failures"
    echo "  3. Request review from team"
    echo "  4. Merge when approved"
    echo ""
    info "Session log: .claude/sessions/${SESSION_ID}.md"
    echo ""
}

# Run main
main
