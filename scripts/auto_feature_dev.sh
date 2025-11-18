#!/bin/bash
#
# Generic Feature Development Automator - Using Claude Code CLI
# Automates systematic multi-phase feature development for any project
#
# This is a generic version that can be adapted to any project by providing
# a configuration file (.claude/feature_config.yaml)
#
# NOTE: This script automatically unsets ANTHROPIC_API_KEY before invoking
# Claude CLI to prevent authentication conflicts. The Claude CLI uses its own
# authentication mechanism (configured via 'claude auth').
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

# Extract specific phase content from feature specification
extract_phase_content() {
    local feature_file="$1"
    local phase_name="$2"
    local format="$3"

    # Extract phase number from phase_name (e.g., "phase-3" -> "3")
    local phase_num="${phase_name#phase-}"

    if [[ "$format" == "markdown" ]]; then
        # For markdown: extract from "## Phase N:" to next "## Phase" or EOF
        # Use awk with simpler logic that works on BSD awk (macOS)
        awk -v phase="$phase_num" '
            /^## Phase [0-9]+[:\-]/ {
                # Extract phase number from current line
                current_phase = $3
                gsub(/[:\-].*/, "", current_phase)

                if (current_phase == phase) {
                    printing = 1
                } else if (printing) {
                    exit
                }
            }
            printing { print }
        ' "$feature_file"
    else
        # For YAML format, would need different extraction logic
        # For now, fall back to entire file
        cat "$feature_file"
    fi
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

    # Load feature specification - EXTRACT ONLY THIS PHASE
    local feature_context=""
    if [ -f "$feature_spec" ]; then
        feature_context=$(extract_phase_content "$feature_spec" "$phase_name" "$FEATURE_FORMAT")
        if [ $? -ne 0 ] || [ -z "$feature_context" ]; then
            error "Failed to extract phase $phase_name content from $feature_spec"
            info "Falling back to full document"
            feature_context=$(cat "$feature_spec")
        fi
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

    # Generate tech stack specific guidelines
    local tech_guidelines=""
    case "$TECH_STACK" in
        python)
            tech_guidelines="- Follow PEP 8 and Python best practices
- Add type hints where beneficial
- Write docstrings for all public APIs
- Use pytest for testing"
            ;;
        rust)
            tech_guidelines="- Follow Rust best practices
- Use cargo fmt and cargo clippy
- Add comprehensive documentation comments
- Write tests in same file or tests/ directory"
            ;;
        python-rust)
            tech_guidelines="**Python Code**:
- Follow PEP 8 and Python best practices
- Add type hints where beneficial
- Write docstrings for all public APIs

**Rust Code**:
- Follow Rust best practices
- Use cargo fmt and cargo clippy
- Add comprehensive documentation comments
- Ensure PyO3 bindings are well-documented"
            ;;
        nodejs|typescript)
            tech_guidelines="- Follow Node.js/TypeScript best practices
- Use ESLint and Prettier
- Add JSDoc or TypeScript types
- Write tests with Jest or similar"
            ;;
    esac

    # Generate quality checks section (pre-generate to avoid bash 3.2 issues)
    local quality_checks=""
    case "$TECH_STACK" in
        python)
            quality_checks='```bash
# Python checks
ruff check .
ruff format .
pytest
```'
            ;;
        rust)
            quality_checks='```bash
# Rust checks
cargo fmt --all
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all
```'
            ;;
        python-rust)
            quality_checks='```bash
# Rust checks
cargo fmt --all
cargo clippy --all-targets --all-features -- -D warnings
cargo test --all

# Python checks
ruff check python/
ruff format python/
pytest python/
```'
            ;;
        nodejs|typescript)
            quality_checks='```bash
# Node.js checks
npm run lint
npm run format
npm test
```'
            ;;
    esac

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

$tech_guidelines

### Step 3: Implementation
- Implement the phase functionality
- Write tests DURING implementation, not after
- Run tests frequently to catch issues early
- Fix failures immediately
- Document as you code (inline comments, docstrings)

### Step 4: Quality Assurance

Run these checks BEFORE staging any files:

$quality_checks

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

    # Unset ANTHROPIC_API_KEY to prevent conflicts with Claude CLI auth
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        info "Unsetting ANTHROPIC_API_KEY to avoid auth conflicts with Claude CLI"
        unset ANTHROPIC_API_KEY
    fi

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

# Execute all phases in sequence with per-phase PRs
execute_all_phases() {
    local feature_name="$1"
    local base_branch_name="$2"
    local feature_spec="$3"

    local total_phases=${#PHASES[@]}
    local completed_phases=0

    section "Multi-Phase Execution: $total_phases phase(s)"
    log "Workflow: Implement → PR → Review → Merge → Next Phase"
    echo ""

    for i in "${!PHASES[@]}"; do
        local phase_name="${PHASES[$i]}"
        local phase_num=$((i + 1))
        local phase_branch="phase-${phase_num}/${base_branch_name}"

        highlight "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        highlight " Phase $phase_num/$total_phases: $phase_name"
        highlight "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""

        # Create branch for this phase (from main)
        log "Creating branch for phase $phase_num: $phase_branch"
        git checkout -b "$phase_branch" main

        # Create context for this phase
        create_claude_context "$feature_name" "$phase_branch" "$phase_name" "$i" "$total_phases" "$feature_spec"

        # Execute phase implementation
        if ! run_phase_implementation "$phase_name" "$i" "$total_phases"; then
            error "❌ Phase implementation failed at phase $phase_num/$total_phases: $phase_name"
            echo ""
            info "Completed phases: $completed_phases/$total_phases"
            info "Failed phase: $phase_name"
            info "Session log: .claude/sessions/${SESSION_ID}.md"
            return 1
        fi

        # Create PR for this phase
        local pr_number=$(create_phase_pr "$phase_name" "$phase_num" "$total_phases")
        if [ -z "$pr_number" ]; then
            error "Failed to create PR for phase $phase_num"
            return 1
        fi

        # Self-review the PR
        self_review_pr "$pr_number" "$phase_name"

        # Resolve any comments from self-review
        resolve_pr_comments "$pr_number" "$phase_name"

        # Merge the PR
        if ! merge_phase_pr "$pr_number" "$phase_num"; then
            error "Failed to merge PR #$pr_number for phase $phase_num"
            return 1
        fi

        completed_phases=$((completed_phases + 1))

        # Brief pause before next phase
        if [ $((i + 1)) -lt $total_phases ]; then
            log ""
            log "Phase $phase_num/$total_phases complete and merged!"
            log "Moving to next phase in 5 seconds..."
            sleep 5
        fi
    done

    log ""
    highlight "✓ All $total_phases phase(s) completed and merged!"

    # Final documentation update after all phases
    log ""
    log "All phases merged - now updating comprehensive documentation..."
    if ! update_final_documentation "$feature_name" "$base_branch_name" "$total_phases"; then
        warn "Documentation update failed - continuing anyway"
    fi

    return 0
}

# Update comprehensive project documentation after all phases
update_final_documentation() {
    local feature_name="$1"
    local base_branch_name="$2"
    local total_phases="$3"

    section "Comprehensive Documentation Update"

    log "Creating documentation branch..."
    local docs_branch="docs/${base_branch_name}"
    git checkout -b "$docs_branch" main

    log "Claude is updating all project documentation..."

    # Create comprehensive documentation update context
    local docs_context=$(cat << 'EOF'
# Comprehensive Documentation Update Task

All phases of the feature have been implemented and merged. Now update ALL project documentation to reflect the new feature.

## Your Task

Update comprehensive project documentation in this order:

### 1. Main README.md
- Add feature to feature list (if not already there)
- Update installation steps if changed
- Add usage examples for new feature
- Update table of contents if needed

### 2. Architecture Documentation
Check and update these files (create if missing):
- `docs/ARCHITECTURE.md` - Add new components/modules
- `docs/templates/ORM_JIT_ARCHITECTURE.md` - Update if this is ORM JIT feature
- Any phase-specific architecture docs

### 3. API Documentation
- Update API reference with new public APIs
- Add code examples
- Document parameters, return values, exceptions
- `docs/API_REFERENCE.md` or similar

### 4. Tutorial/Guide
- Add tutorials showing how to use new feature
- Step-by-step guides
- Common patterns and best practices

### 5. CHANGELOG.md
Add entry for this feature:
```markdown
## [Unreleased]

### Added
- [Feature Name]: Brief description
  - Phase 1: Template variable extraction
  - Phase 2: Query optimizer
  - Phase 3: Serializer code generation
  - Phase 4: LiveView integration
  - Phase 5: Caching infrastructure
  - Phase 6: Testing & documentation
```

### 6. Examples/Demos
- Add example code in `examples/` directory
- Update existing examples if they should use new feature
- Ensure examples are tested and working

### 7. Migration Guide (if breaking changes)
Create `docs/MIGRATION_GUIDE.md` if feature requires migration:
- What changed
- How to migrate existing code
- Before/after examples

## Quality Standards

Before creating commit:
- [ ] All documentation is clear and accurate
- [ ] Code examples are tested and working
- [ ] No typos or formatting issues
- [ ] Links work (internal and external)
- [ ] Table of contents updated
- [ ] Screenshots/diagrams added if helpful

## Commit Message

Create descriptive commit:
```bash
git commit -m "docs: Comprehensive documentation for [feature name]

Update all project documentation after completing all phases:

- README: Add feature overview and usage examples
- Architecture: Document new components and design
- API: Complete API reference for new functionality
- Guides: Add tutorials and best practices
- CHANGELOG: Add feature entry with all phases
- Examples: Add working examples demonstrating feature

All [total_phases] phases now fully documented."
```

Begin documentation update now.
EOF
)

    # Run Claude to update documentation
    # Write context to temp file and invoke claude with /dev/tty for stdin
    local temp_file=$(mktemp)
    echo "$docs_context" > "$temp_file"
    claude "$temp_file" --permission-mode acceptEdits < /dev/tty
    rm -f "$temp_file"

    # Check if any changes were made
    if git diff --quiet && git diff --cached --quiet; then
        log "No documentation changes needed"
        git checkout main
        git branch -D "$docs_branch"
        return 0
    fi

    log "✓ Documentation updated"

    # Create PR for documentation
    section "Creating Documentation PR"

    local docs_pr_title="docs: Comprehensive documentation for $feature_name"
    local docs_pr_body="## Documentation Update

This PR updates all project documentation after completing all $total_phases phases of the $feature_name feature.

### Updated Documentation
- ✅ README.md - Feature overview and examples
- ✅ Architecture docs - New components and design
- ✅ API reference - Complete API documentation
- ✅ Guides/tutorials - How to use the feature
- ✅ CHANGELOG - Feature entry
- ✅ Examples - Working examples

### Review Checklist
- [ ] All code examples work
- [ ] No typos or broken links
- [ ] Documentation is clear and complete

🤖 Generated with [Claude Code](https://claude.com/claude-code)"

    gh pr create --title "$docs_pr_title" --body "$docs_pr_body" --label "documentation"

    local docs_pr_number=$(gh pr view --json number -q .number 2>/dev/null || echo "")
    local docs_pr_url=$(gh pr view --json url -q .url 2>/dev/null || echo "")

    if [ -n "$docs_pr_url" ]; then
        highlight "✓ Documentation PR Created: $docs_pr_url (PR #$docs_pr_number)"

        # Self-review docs PR
        log "Self-reviewing documentation PR..."
        self_review_pr "$docs_pr_number" "documentation"

        # Resolve any comments
        resolve_pr_comments "$docs_pr_number" "documentation"

        # Auto-merge docs PR (less critical than code PRs)
        log "Merging documentation PR..."
        if merge_phase_pr "$docs_pr_number" "docs"; then
            highlight "✓ Documentation merged to main"
            return 0
        else
            warn "Documentation PR needs manual review: $docs_pr_url"
            return 1
        fi
    else
        error "Failed to create documentation PR"
        return 1
    fi
}

# Create PR for current phase
create_phase_pr() {
    local phase_name="$1"
    local phase_num="$2"
    local total_phases="$3"

    section "Creating Pull Request for Phase $phase_num"

    # Check if there are commits to PR
    local commit_count=$(git log --oneline origin/main..HEAD 2>/dev/null | wc -l || echo 0)

    if [ "$commit_count" -eq 0 ]; then
        warn "No commits found on this branch - nothing to PR"
        return 1
    fi

    log "Creating PR for phase $phase_num/$total_phases with $commit_count commit(s)..."

    # Push branch to remote first
    log "Pushing branch to remote..."
    git push -u origin HEAD || {
        error "Failed to push branch"
        return 1
    }

    # Create PR with detailed title and body
    local pr_title="feat(phase-$phase_num): $phase_name"
    local pr_body="## Phase $phase_num/$total_phases: $phase_name

This PR implements Phase $phase_num of the multi-phase feature.

### Implementation
- Completed phase $phase_num as specified
- All tests passing
- Quality gates passed

### Next Steps
- Review this phase
- Merge when approved
- Phase $((phase_num + 1))/$total_phases will follow

🤖 Generated with [Claude Code](https://claude.com/claude-code)"

    # Create PR and capture URL
    local pr_url=$(gh pr create --title "$pr_title" --body "$pr_body" --label "enhancement" 2>&1 | tail -1)

    # Get current branch to query PR info
    local current_branch=$(git branch --show-current)
    local pr_number=$(gh pr list --head "$current_branch" --json number -q '.[0].number' 2>/dev/null || echo "")

    if [ -n "$pr_url" ]; then
        highlight "✓ PR Created: $pr_url (PR #$pr_number)"
        echo "$pr_number"
        return 0
    else
        error "Failed to create PR"
        return 1
    fi
}

# Claude self-reviews the PR
self_review_pr() {
    local pr_number="$1"
    local phase_name="$2"

    section "Self-Reviewing PR #$pr_number"

    log "Claude is reviewing the PR..."

    # Create self-review context
    local review_context=$(cat << 'EOF'
# PR Self-Review Task

You are reviewing your own Pull Request to ensure quality before requesting team review.

## Your Task

1. **Read the PR diff**: Use `gh pr diff` to see all changes
2. **Review systematically**:
   - Code quality: Are there any obvious bugs, security issues, or bad practices?
   - Tests: Are edge cases covered? Any missing tests?
   - Documentation: Are docstrings clear? Is README updated if needed?
   - Performance: Any obvious performance issues?
   - Security: Input validation? SQL injection risks? XSS vulnerabilities?

3. **Create review comments** for any issues found:
   - Use `gh pr review <pr#> --comment --body "message"`
   - Be specific: file, line number, issue, suggested fix
   - Categorize: [CRITICAL], [IMPORTANT], [MINOR]

4. **Approve if no critical issues**:
   - If only minor/optional improvements: `gh pr review <pr#> --approve --body "LGTM with minor suggestions"`
   - If critical issues: `gh pr review <pr#> --request-changes --body "Critical issues found"`

## Standards Checklist

Before approving, verify:
- [ ] No clippy/ruff warnings
- [ ] All tests pass
- [ ] Edge cases tested
- [ ] Documentation complete
- [ ] No security vulnerabilities
- [ ] Code follows project style
- [ ] No debugging code left in (println!, dbg!, console.log)
- [ ] Error messages are helpful

Begin your review now.
EOF
)

    # Run Claude to self-review
    # Write context to temp file and invoke claude with /dev/tty for stdin
    local temp_file=$(mktemp)
    echo "$review_context" > "$temp_file"
    claude "$temp_file" --permission-mode acceptEdits < /dev/tty
    rm -f "$temp_file"

    log "✓ Self-review complete"
}

# Resolve PR review comments
resolve_pr_comments() {
    local pr_number="$1"
    local phase_name="$2"

    section "Resolving PR #$pr_number Review Comments"

    # Check if there are unresolved review comments
    local review_decision=$(gh pr view "$pr_number" --json reviewDecision -q .reviewDecision)

    if [ "$review_decision" == "APPROVED" ]; then
        highlight "✓ PR already approved - no comments to resolve"
        return 0
    fi

    if [ "$review_decision" != "CHANGES_REQUESTED" ]; then
        log "No review decision yet (status: $review_decision)"
        return 0
    fi

    log "PR has requested changes - Claude will address them..."

    # Create comment resolution context
    local resolve_context=$(cat << EOF
# PR Comment Resolution Task

Your PR #$pr_number has review comments requesting changes.

## Your Task

1. **Read all review comments**:
   \`\`\`bash
   gh pr view $pr_number --comments
   \`\`\`

2. **Address each comment**:
   - Fix the code based on feedback
   - Run tests to verify fix
   - Run quality gates (cargo fmt, clippy, ruff, etc.)
   - Commit with descriptive message referencing the comment

3. **Respond to comments**:
   - After fixing, respond to each comment explaining your fix
   - Link to the commit that addresses it

4. **Push updates**:
   \`\`\`bash
   git push origin $(git branch --show-current)
   \`\`\`

5. **Request re-review**:
   \`\`\`bash
   gh pr review $pr_number --approve --body "All comments addressed. Ready for re-review."
   \`\`\`

Begin addressing review comments now.
EOF
)

    # Run Claude to resolve comments
    # Write context to temp file and invoke claude with /dev/tty for stdin
    local temp_file=$(mktemp)
    echo "$resolve_context" > "$temp_file"
    claude "$temp_file" --permission-mode acceptEdits < /dev/tty
    rm -f "$temp_file"

    log "✓ Review comments addressed"
}

# Merge phase PR
merge_phase_pr() {
    local pr_number="$1"
    local phase_num="$2"

    section "Merging PR #$pr_number (Phase $phase_num)"

    # Wait for approval
    local max_attempts=10
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        local review_decision=$(gh pr view "$pr_number" --json reviewDecision -q .reviewDecision)

        if [ "$review_decision" == "APPROVED" ]; then
            log "✓ PR approved - merging..."

            # Merge PR
            gh pr merge "$pr_number" --squash --delete-branch

            # Update local main
            git checkout main
            git pull origin main

            highlight "✓ Phase $phase_num merged to main"
            return 0
        else
            log "Waiting for approval (status: $review_decision) - attempt $((attempt + 1))/$max_attempts"
            sleep 10
            attempt=$((attempt + 1))
        fi
    done

    error "PR not approved after $max_attempts attempts"
    return 1
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
  -h, --help           Show this help message
  -n, --dry-run        Only show planning, don't execute
  -b, --branch NAME    Custom branch name (default: auto-generated)
  -s, --single PHASE   Execute single phase only (specify phase name)
  -r, --resume N       Resume from phase N through end (e.g., --resume 2)
  --start-phase PHASE  Start from specific phase and run to end

Examples:
  $0 features/jit-serialization.yaml             # Execute all phases
  $0 --dry-run features/new-feature.yaml         # Preview only
  $0 --single phase-2 features/my-feat.yaml      # Execute one phase only
  $0 --resume 2 features/my-feat.yaml            # Resume from phase 2 through end
  $0 --start-phase phase-2 features/my-feat.yaml # Same as --resume 2
  $0 --branch custom-name features/feat.yaml     # Custom branch

Resume Usage:
  If phase 1 is complete, use --resume 2 to continue from phase 2.
  This is shorthand for --start-phase phase-2.

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
START_PHASE=""
RESUME_PHASE_NUM=""
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
        --start-phase)
            START_PHASE="$2"
            shift 2
            ;;
        -r|--resume)
            RESUME_PHASE_NUM="$2"
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

    # Handle resume by phase number (convert to --start-phase)
    if [ -n "$RESUME_PHASE_NUM" ]; then
        # Validate it's a number
        if ! [[ "$RESUME_PHASE_NUM" =~ ^[0-9]+$ ]]; then
            error "Resume phase must be a number: $RESUME_PHASE_NUM"
            exit 1
        fi

        # Validate it's in valid range
        if [ "$RESUME_PHASE_NUM" -lt 1 ] || [ "$RESUME_PHASE_NUM" -gt "${#PHASES[@]}" ]; then
            error "Resume phase $RESUME_PHASE_NUM out of range (1-${#PHASES[@]})"
            exit 1
        fi

        # Convert to phase name (phase-1, phase-2, etc.)
        START_PHASE="phase-${RESUME_PHASE_NUM}"
        info "Resume mode: Starting from phase $RESUME_PHASE_NUM ($START_PHASE)"
    fi

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

    # Handle start-phase execution (run from specific phase to end)
    if [ -n "$START_PHASE" ]; then
        # Find the index of START_PHASE
        local start_index=-1
        for i in "${!PHASES[@]}"; do
            if [ "${PHASES[$i]}" = "$START_PHASE" ]; then
                start_index=$i
                break
            fi
        done

        if [ $start_index -eq -1 ]; then
            error "Start phase not found: $START_PHASE"
            error "Available phases: ${PHASES[*]}"
            exit 1
        fi

        # Create new array with phases from start_index onwards
        local remaining_phases=("${PHASES[@]:$start_index}")
        PHASES=("${remaining_phases[@]}")

        info "Starting from phase $((start_index + 1)): $START_PHASE"
        info "Will execute ${#PHASES[@]} phase(s): ${PHASES[*]}"
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

    info "Branch prefix: $branch_name"
    info "Phases to execute: ${#PHASES[@]}"
    info "Workflow: Each phase gets its own branch → PR → Review → Merge"
    echo ""

    if [ "$DRY_RUN" = true ]; then
        # Create context for first phase only (preview)
        local temp_branch="phase-1/${branch_name}"
        create_claude_context "feature" "$temp_branch" "${PHASES[0]}" 0 "${#PHASES[@]}" "$FEATURE_SPEC"
        info "Dry run mode - context created at: $TEMP_DIR/context.md"
        info "Review and run without --dry-run to execute"
        exit 0
    fi

    # Confirmation prompt
    echo ""
    log "This will create ${#PHASES[@]} separate PRs (one per phase)."
    log "Each PR will be reviewed and merged before moving to the next phase."
    echo ""
    read -p "$(echo -e ${CYAN})Start multi-phase feature implementation? (y/N)$(echo -e ${NC}) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Aborted by user"
        exit 0
    fi

    # Execute all phases (each creates its own PR, reviews it, and merges)
    local feature_name=$(basename "$FEATURE_SPEC" .yaml)
    if ! execute_all_phases "$feature_name" "$branch_name" "$FEATURE_SPEC"; then
        error "❌ Feature implementation failed"
        show_summary
        exit 1
    fi

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
