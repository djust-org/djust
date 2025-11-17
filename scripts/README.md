# djust Automation Scripts

This directory contains automation scripts for systematic development workflows.

## Overview

We provide two automation scripts:

1. **`auto_phase_dev.sh`** - djust-specific phase automation (simple, quick start)
2. **`auto_feature_dev.sh`** - Generic multi-phase feature automation (flexible, any project)

### Which Script Should I Use?

| Use Case | Script | Reason |
|----------|--------|--------|
| Implementing djust Phase 2 | `auto_phase_dev.sh` | Pre-configured for djust |
| Quick single-phase work | `auto_phase_dev.sh` | Less setup required |
| Multi-phase feature with dependencies | `auto_feature_dev.sh` | Handles phase orchestration |
| Different project (not djust) | `auto_feature_dev.sh` | Fully configurable |
| Complex feature with custom phases | `auto_feature_dev.sh` | Define your own phases |

---

## Project-Specific: Phase Development Automator

**Script**: `auto_phase_dev.sh`

Automates the complete 9-step development process documented in `DEVELOPMENT_PROCESS.md` using Claude Code CLI.

### What It Does

The script provides end-to-end automation for implementing djust phases:

1. **Pre-flight Checks**: Validates git state, Rust/Python toolchains, GitHub CLI
2. **Branch Creation**: Creates feature branch automatically
3. **Context Loading**: Loads phase specs, development process, project context
4. **Claude Execution**: Launches Claude Code with comprehensive instructions
5. **Quality Gates**: Enforces pre-commit hooks, tests, linting, formatting
6. **Documentation**: Ensures docs are updated during implementation
7. **PR Creation**: Creates comprehensive pull request
8. **Session Logging**: Tracks all decisions and work in `.claude/sessions/`

### Usage

```bash
# Basic usage - implement a phase
./scripts/auto_phase_dev.sh phase-2

# Short form
./scripts/auto_phase_dev.sh p2

# Dry run (preview only, don't execute)
./scripts/auto_phase_dev.sh --dry-run phase-2

# Custom branch name
./scripts/auto_phase_dev.sh --branch my-custom-branch phase-2

# Show help
./scripts/auto_phase_dev.sh --help
```

### Available Phases

| Phase Name | Aliases | Description |
|------------|---------|-------------|
| phase-2 | p2, orm-jit | ORM JIT Query Optimizer |
| phase-3 | p3 | (Add as implemented) |

### Prerequisites

Before running the automation:

1. **GitHub CLI** - Installed and authenticated
   ```bash
   brew install gh
   gh auth login
   ```

2. **Claude Code CLI** - Installed globally
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **Clean Git State** - On main branch with no uncommitted changes
   ```bash
   git checkout main
   git pull
   git status  # Should be clean
   ```

4. **Rust Toolchain** - Version 1.70+
   ```bash
   rustc --version
   ```

5. **Python Environment** - Virtual environment activated
   ```bash
   source .venv/bin/activate
   ```

6. **Pre-commit Hooks** - Configured
   ```bash
   pre-commit install
   ```

### What Claude Does Autonomously

When you run the script, Claude Code will:

1. **Plan** (5-10 min)
   - Review phase specification
   - Identify files to change
   - Create comprehensive TODO list
   - Document approach in session log

2. **Implement** (20-40 min)
   - Write Rust code with tests
   - Write Python code with tests
   - Run tests continuously during development
   - Fix failures immediately

3. **Quality Check** (5-10 min)
   - Run `cargo fmt`, `cargo clippy`
   - Run `ruff check`, `ruff format`
   - Execute full test suite
   - Check for unwanted files (__pycache__, etc.)

4. **Document** (5-10 min)
   - Write inline documentation
   - Update API documentation
   - Add usage examples
   - Document known limitations

5. **Validate** (5 min)
   - Run `make test`
   - Run `make build`
   - Run `make lint`
   - Verify all checks pass

6. **Create PR** (2 min)
   - Commit all changes
   - Create comprehensive PR description
   - Self-review in browser
   - Add appropriate labels

### Session Logs

Every run creates a session log at `.claude/sessions/phase-dev-YYYYMMDD-HHMMSS.md`

The log contains:
- Planning decisions and reasoning
- Implementation approach
- Challenges faced and solutions
- Test results
- Documentation updates
- Time spent on each step
- Quality checklist completion status

### Quality Standards

The automation enforces all quality standards from `DEVELOPMENT_PROCESS.md`:

**Code Quality**:
- ✅ No clippy warnings
- ✅ Code formatted (cargo fmt, ruff format)
- ✅ Type hints added (Python)
- ✅ Comprehensive error handling
- ✅ No unsafe code without justification

**Testing**:
- ✅ All tests pass (Rust + Python)
- ✅ New tests for new functionality
- ✅ Edge cases covered
- ✅ No test regressions
- ✅ Performance benchmarks (when applicable)

**Documentation**:
- ✅ Inline documentation complete
- ✅ API docs updated
- ✅ Usage examples added
- ✅ Known limitations documented
- ✅ CLAUDE.md updated (if needed)

**Git Hygiene**:
- ✅ No __pycache__ or build artifacts
- ✅ Descriptive commit messages
- ✅ No WIP commits in final PR
- ✅ Clean git history

### Troubleshooting

#### "Git state is not clean"
**Problem**: Uncommitted changes or not on main branch

**Solution**:
```bash
# Stash changes
git stash

# Or commit them
git add .
git commit -m "WIP"

# Return to main
git checkout main
```

#### "GitHub CLI not authenticated"
**Problem**: gh CLI not logged in

**Solution**:
```bash
gh auth login
# Follow prompts
```

#### "Claude Code not found"
**Problem**: Claude CLI not installed

**Solution**:
```bash
npm install -g @anthropic-ai/claude-code
```

#### "Pre-commit hooks failing"
**Problem**: Code doesn't meet quality standards

**Solution**: Claude will auto-fix most issues. If it persists:
```bash
# Rust fixes
cargo fmt --all
cargo clippy --fix --allow-dirty

# Python fixes
ruff check --fix python/
ruff format python/
```

#### "Phase documentation not found"
**Problem**: Phase specification doesn't exist yet

**Solution**: Create the phase specification first in `docs/templates/ORM_JIT_API.md` or the appropriate documentation file.

### Customization

To add a new phase:

1. Add phase specification to documentation (e.g., `docs/templates/ORM_JIT_API.md`)
2. Update `load_phase_info()` function in `auto_phase_dev.sh`:
   ```bash
   case "$phase_name" in
       "phase-4"|"p4"|"your-phase-name")
           phase_doc="$DOCS_DIR/path/to/your/phase/doc.md"
           ;;
   ```
3. Update this README with the new phase

### Performance

Typical execution times:

| Phase Complexity | Time |
|------------------|------|
| Simple (bug fix, small feature) | 30-45 min |
| Medium (new component, API) | 45-90 min |
| Complex (architecture change) | 90-120 min |

Total time includes:
- Planning
- Implementation
- Testing
- Documentation
- PR creation

### Comparison with Manual Process

| Task | Manual | Automated | Savings |
|------|--------|-----------|---------|
| Setup (branch, context) | 5 min | 1 min | 80% |
| Implementation | 30-60 min | 30-60 min | 0% |
| Quality checks | 10 min | 5 min | 50% |
| Documentation | 15 min | 10 min | 33% |
| PR creation | 5 min | 2 min | 60% |
| **Total** | **65-95 min** | **48-78 min** | **26-29%** |

Additional benefits:
- **100% compliance** with development process
- **Zero missed steps** (quality gates, docs, tests)
- **Consistent quality** across all phases
- **Session logs** for knowledge retention
- **Reproducible** process for all contributors

### Integration with CI/CD

The automation script creates PRs that:
- Trigger GitHub Actions CI
- Run all tests automatically
- Check code coverage
- Validate documentation
- Can auto-merge if all checks pass (optional)

### Best Practices

1. **Start Small**: Use `--dry-run` first to preview
2. **Review Session Logs**: Learn from Claude's decisions
3. **Iterate**: If PR needs changes, make them and re-run validation
4. **Document Learnings**: Update `DEVELOPMENT_PROCESS.md` with new insights
5. **Customize**: Adapt the script to your team's workflow

### Future Enhancements

Planned improvements:
- [ ] Multi-phase batch execution
- [ ] Automatic PR review categorization (blocking/important/nice-to-have)
- [ ] Integration with issue tracking
- [ ] Knowledge base integration (like notewizard)
- [ ] Automatic benchmark regression detection
- [ ] Performance trend tracking
- [ ] Test coverage tracking and enforcement

### Related Documentation

- **Development Process**: `../DEVELOPMENT_PROCESS.md`
- **Project Context**: `../CLAUDE.md`
- **Phase Specifications**: `../docs/templates/ORM_JIT_API.md`
- **API Reference**: `../docs/API_REFERENCE_*.md`

---

## Generic: Feature Development Automator

**Script**: `auto_feature_dev.sh`

Implements multi-phase features for **any project** using YAML feature specifications.

### 💡 How Does It Know What to Implement?

**You provide a specification file - either YAML or Markdown!**

**Option 1: Use Markdown Documentation** (Recommended if you have docs)

```markdown
## Phase 1: Template Variable Extraction

**Duration**: 2 days

### Deliverables
- [ ] Rust: extract_template_variables() in djust_templates/src/parser.rs
- [ ] Tests: 50+ comprehensive tests

### Implementation
\`\`\`rust
pub fn extract_template_variables(template: &str) -> HashMap<String, Vec<String>> {
    // Implementation here...
}
\`\`\`
```

**Option 2: Use YAML Format**

```yaml
feature: "my-feature"
phases:
  - name: phase-1
    deliverables:
      - "Rust: my_function() in src/lib.rs"
      - "Tests: 50+ comprehensive tests"
```

The script reads the file and passes it to Claude Code, which implements exactly what you specified.

**📖 Documentation**:
- `.claude/MARKDOWN_SPECS.md` - Using markdown docs as specs
- `.claude/HOW_IT_WORKS.md` - Detailed explanation with diagrams
- `.claude/YAML_TO_CODE.md` - YAML format examples

### What Makes It Generic?

- ✅ **Tech Stack Agnostic**: Supports Python, Rust, Node.js, TypeScript, Go, etc.
- ✅ **Configurable Quality Gates**: Define your own linting/testing commands
- ✅ **Multi-Phase Orchestration**: Execute multiple phases with dependency tracking
- ✅ **Project-Agnostic**: Works with any git repository
- ✅ **Customizable**: Configure via `.claude/feature_config.yaml`

### Quick Start

```bash
# 1. Create feature specification
cat > .claude/features/my-feature.yaml << 'EOF'
feature: "my-feature"
description: "Feature description"
phases:
  - name: phase-1
    title: "Phase 1"
    description: "Implement core functionality"
    dependencies: []
  - name: phase-2
    title: "Phase 2"
    dependencies: [phase-1]
EOF

# 2. Run automation
./scripts/auto_feature_dev.sh .claude/features/my-feature.yaml

# 3. All phases execute automatically, commits created, PR opened
```

### Feature Specification Format

Feature specs are YAML files that define multi-phase work:

```yaml
feature: "jit-auto-serialization"
title: "JIT Auto-Serialization for Django ORM"
description: |
  Multi-line description of the feature

phases:
  - name: phase-1-extraction
    title: "Variable Extraction"
    description: "Extract template variables"
    docs:
      - "docs/api.md#section"
    estimated_time: 60  # minutes
    dependencies: []
    deliverables:
      - "Rust implementation"
      - "Tests"
    success_criteria:
      - "All tests pass"
    quality_gates:
      - "cargo test"

  - name: phase-2-optimizer
    title: "Query Optimizer"
    description: "Implement optimizer"
    estimated_time: 90
    dependencies: [phase-1-extraction]  # Runs after phase-1
    deliverables:
      - "Optimizer implementation"
    quality_gates:
      - "cargo test"
      - "pytest"

labels:
  - "enhancement"
  - "performance"
```

See `.claude/features/example-jit-serialization.yaml` for a complete example.

### Configuration File

Create `.claude/feature_config.yaml` to customize behavior:

```yaml
# Project Configuration
project_name: "MyProject"
tech_stack: "python-rust"  # python, rust, nodejs, typescript, go, ruby

# Quality Gates
quality_gates:
  rust:
    - "cargo fmt --all"
    - "cargo clippy -- -D warnings"
  python:
    - "ruff check ."
    - "ruff format ."

# Test Commands
test_commands:
  rust:
    - "cargo test --all"
  python:
    - "pytest"

# Documentation Paths
doc_paths:
  - "README.md"
  - "docs/"

# Validation
validation:
  excluded_files:
    - "__pycache__"
    - "*.pyc"
    - "node_modules"
```

See `.claude/feature_config.yaml` for complete configuration options.

### Usage Examples

```bash
# Execute all phases
./scripts/auto_feature_dev.sh .claude/features/my-feature.yaml

# Preview only (dry run)
./scripts/auto_feature_dev.sh --dry-run .claude/features/my-feature.yaml

# Execute single phase
./scripts/auto_feature_dev.sh --single phase-2 .claude/features/my-feature.yaml

# Custom branch name
./scripts/auto_feature_dev.sh --branch custom-name .claude/features/my-feature.yaml
```

### How Multi-Phase Execution Works

1. **Parse Feature Spec** → Extracts phases and dependencies
2. **Validate Dependencies** → Ensures phases are in correct order
3. **For Each Phase**:
   - Create context with phase details
   - Run Claude Code autonomously
   - Execute quality gates
   - Create commit for phase
   - Move to next phase
4. **Create Final PR** → After all phases complete

### Phase Dependencies

Phases can depend on other phases:

```yaml
phases:
  - name: core
    dependencies: []  # Runs first

  - name: api
    dependencies: [core]  # Runs after 'core'

  - name: integration
    dependencies: [core, api]  # Runs after both complete
```

The script automatically orders phases based on dependencies.

### Tech Stack Support

| Tech Stack | Quality Gates | Test Commands |
|------------|---------------|---------------|
| **python** | ruff, mypy | pytest |
| **rust** | clippy, fmt | cargo test |
| **python-rust** | Both Rust + Python | cargo test, pytest |
| **nodejs** | eslint, prettier | npm test |
| **typescript** | tsc, eslint | jest |
| **go** | go fmt, go vet | go test |

Configure in `.claude/feature_config.yaml`.

### Adapting to Your Project

To use with a different project:

1. **Copy scripts to your project**:
   ```bash
   cp scripts/auto_feature_dev.sh /path/to/your/project/scripts/
   ```

2. **Create configuration**:
   ```bash
   mkdir -p .claude/features
   cp .claude/feature_config.yaml /path/to/your/project/.claude/
   ```

3. **Customize config** for your tech stack:
   ```yaml
   project_name: "YourProject"
   tech_stack: "nodejs"  # or python, rust, etc.
   quality_gates:
     nodejs:
       - "npm run lint"
   ```

4. **Create feature spec** and run:
   ```bash
   ./scripts/auto_feature_dev.sh .claude/features/your-feature.yaml
   ```

### Comparison: Project-Specific vs Generic

| Feature | auto_phase_dev.sh | auto_feature_dev.sh |
|---------|-------------------|---------------------|
| **Setup** | Zero config | Create feature spec + config |
| **Flexibility** | djust only | Any project |
| **Multi-Phase** | No (single phase) | Yes (with dependencies) |
| **Tech Stack** | Python + Rust | Configurable |
| **Phase Definition** | Hardcoded | YAML specification |
| **Best For** | Quick djust work | Complex multi-phase features |

### Benefits of Generic Approach

1. **Reusability**: Use same script across all your projects
2. **Standardization**: Consistent development process everywhere
3. **Documentation**: Feature specs document the work
4. **Dependency Tracking**: Automatic phase ordering
5. **Team Collaboration**: Share feature specs with team
6. **Knowledge Retention**: YAML specs serve as documentation

### Example: Using for a Different Project

**Scenario**: You have a Node.js/TypeScript API project

1. **Create config**:
   ```yaml
   # .claude/feature_config.yaml
   project_name: "MyAPI"
   tech_stack: "typescript"
   quality_gates:
     nodejs:
       - "npm run lint"
       - "npm run type-check"
   test_commands:
     nodejs:
       - "npm test"
   ```

2. **Create feature spec**:
   ```yaml
   # .claude/features/auth-system.yaml
   feature: "authentication-system"
   phases:
     - name: jwt-auth
       title: "JWT Authentication"
       dependencies: []
     - name: oauth
       title: "OAuth Integration"
       dependencies: [jwt-auth]
   ```

3. **Run**:
   ```bash
   ./scripts/auto_feature_dev.sh .claude/features/auth-system.yaml
   ```

Done! The script adapts to TypeScript, runs your quality gates, and completes all phases.

---

## Other Scripts

### open_intellij.sh

Opens the project in IntelliJ IDEA with proper Rust + Python configuration.

```bash
./scripts/open_intellij.sh
```

See `docs/IDE_SETUP_RUST.md` for IDE setup details.

### rebrand_to_djust.sh

Historical script used for rebranding the project. Not used in regular development.

## Contributing

When adding new automation scripts:

1. Add comprehensive help text (`--help`)
2. Include error checking and validation
3. Use colored output for clarity
4. Create session logs for traceability
5. Document in this README
6. Make executable: `chmod +x scripts/your_script.sh`
7. Test with `--dry-run` mode (if applicable)

## Support

If you encounter issues with automation:

1. Check session log: `.claude/sessions/phase-dev-*.md`
2. Review error messages carefully
3. Try `--dry-run` to preview context
4. Check prerequisites are met
5. Report issues to the team

---

**Last Updated**: 2025-11-16
**Maintained By**: djust core team
