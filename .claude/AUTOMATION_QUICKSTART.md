# Automation Quick Start Guide

## 💡 How It Works

**The YAML feature specification file contains all the implementation details.**

When you run:
```bash
./scripts/auto_feature_dev.sh .claude/features/my-feature.yaml
```

The script:
1. Reads your YAML file (the specification)
2. Passes it to Claude Code as context
3. Claude reads the spec and implements what you described
4. Creates commits, runs tests, makes PR

**The YAML file IS the specification** - it tells Claude exactly what to implement.

See `.claude/HOW_IT_WORKS.md` for detailed explanation.

---

Choose your workflow based on your needs:

## 🚀 Quick Start: djust Phase Implementation

**Use when**: Implementing a specific djust phase (Phase 2, Phase 3, etc.)

```bash
# Implement Phase 2 (ORM JIT Query Optimizer)
./scripts/auto_phase_dev.sh phase-2

# Preview what will happen
./scripts/auto_phase_dev.sh --dry-run phase-2
```

**What it does**:
- Creates feature branch
- Implements entire phase (Rust + Python)
- Runs all quality gates
- Creates tests and benchmarks
- Updates documentation
- Creates PR

**Time**: 30-90 minutes (autonomous)

---

## 🎯 Advanced: Multi-Phase Feature

**Use when**: Implementing complex feature with multiple interdependent phases

### Option A: Use Existing Markdown Documentation

**If you already have markdown docs** (like `docs/templates/ORM_JIT_IMPLEMENTATION.md`):

```bash
# Use markdown docs directly - no YAML conversion needed!
./scripts/auto_feature_dev.sh docs/templates/ORM_JIT_IMPLEMENTATION.md
```

**Requirements**: Markdown must have phase headers:
```markdown
## Phase 1: Title Here
## Phase 2: Another Title
```

See `.claude/MARKDOWN_SPECS.md` for complete documentation.

### Option B: Create YAML Specification

**For new features** or if you prefer structured format:

```bash
cat > .claude/features/my-feature.yaml << 'EOF'
feature: "my-feature"
description: "Feature description"

phases:
  - name: phase-1-core
    title: "Core Implementation"
    description: "Implement core functionality"
    estimated_time: 60
    dependencies: []

  - name: phase-2-api
    title: "API Layer"
    description: "Add public API"
    estimated_time: 45
    dependencies: [phase-1-core]

  - name: phase-3-tests
    title: "Integration Tests"
    description: "End-to-end testing"
    estimated_time: 30
    dependencies: [phase-1-core, phase-2-api]
EOF
```

### Run Automation (Both Formats)

```bash
# YAML format
./scripts/auto_feature_dev.sh .claude/features/my-feature.yaml

# Markdown format
./scripts/auto_feature_dev.sh docs/my-feature.md

# Execute single phase (both formats)
./scripts/auto_feature_dev.sh --single phase-2 <spec-file>
```

**What it does**:
- Executes all phases in dependency order
- Creates commit per phase
- Validates after each phase
- Creates final PR with all phases

**Time**: Sum of all phase times (autonomous)

---

## 📋 Comparison

| Aspect | auto_phase_dev.sh | auto_feature_dev.sh |
|--------|-------------------|---------------------|
| **Setup** | Zero | Create YAML spec |
| **Best For** | Single djust phase | Multi-phase feature |
| **Config** | None needed | Optional YAML config |
| **Phases** | Single | Multiple with dependencies |
| **Projects** | djust only | Any project |

---

## 🛠️ For Other Projects

Using automation in a different project:

### Step 1: Copy Scripts

```bash
# Copy to your project
cp scripts/auto_feature_dev.sh /path/to/your/project/scripts/
chmod +x /path/to/your/project/scripts/auto_feature_dev.sh
```

### Step 2: Create Configuration

```bash
# Create config directory
mkdir -p /path/to/your/project/.claude/features

# Copy example config
cp .claude/feature_config.yaml /path/to/your/project/.claude/
```

### Step 3: Customize Config

Edit `.claude/feature_config.yaml`:

```yaml
project_name: "YourProject"
tech_stack: "nodejs"  # or python, rust, typescript, go

quality_gates:
  nodejs:
    - "npm run lint"
    - "npm run type-check"

test_commands:
  nodejs:
    - "npm test"
```

### Step 4: Create Feature Spec & Run

```bash
# Create feature spec
cat > .claude/features/auth.yaml << 'EOF'
feature: "authentication"
phases:
  - name: jwt
    title: "JWT Auth"
    dependencies: []
EOF

# Run
./scripts/auto_feature_dev.sh .claude/features/auth.yaml
```

---

## 📚 Full Documentation

- **Scripts README**: `scripts/README.md`
- **Development Process**: `DEVELOPMENT_PROCESS.md`
- **Example Feature Spec**: `.claude/features/example-jit-serialization.yaml`
- **Configuration Reference**: `.claude/feature_config.yaml`

---

## 🔧 Troubleshooting

### "Git state is not clean"
```bash
git stash  # Stash changes
git checkout main  # Return to main
```

### "GitHub CLI not authenticated"
```bash
gh auth login  # Authenticate
```

### "Pre-commit hooks failing"
Claude will auto-fix most issues. If persists:
```bash
# Rust
cargo fmt --all
cargo clippy --fix

# Python
ruff check --fix .
ruff format .
```

### "Phase failed"
Check session log:
```bash
cat .claude/sessions/feature-dev-*.md
```

Review Claude's decisions and error messages.

---

## 💡 Tips

1. **Start with dry-run** to preview context
2. **Review session logs** to learn from Claude's decisions
3. **Customize config** for your workflow
4. **Break complex features into phases** for better control
5. **Use dependencies** to ensure correct execution order

---

## 🎓 Learning More

The automation implements the 9-step development process:

1. Pre-Implementation Planning
2. Create Feature Branch
3. Implement Core Functionality
4. Quality Assurance (Pre-commit)
5. Self-Review
6. Comprehensive Testing
7. Documentation
8. Final Validation
9. Create Pull Request

See `DEVELOPMENT_PROCESS.md` for details on each step.

---

**Questions?** Check `scripts/README.md` for comprehensive documentation.
