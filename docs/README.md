# djust Documentation

This directory contains comprehensive documentation for djust organized by topic.

## Quick Navigation

### 📚 Getting Started
- **[Quick Start Guide](guides/QUICKSTART.md)** - Get up and running in 5 minutes
- **[Deployment Guide](guides/DEPLOYMENT.md)** - Deploy djust to production
- **[HTTP Mode Example](guides/HTTP_MODE_EXAMPLE.md)** - Use djust without WebSockets

### 🎨 Components
- **[Component System Overview](components/COMPONENTS.md)** - shadcn-style component philosophy
- **[API Reference](components/API_REFERENCE_COMPONENTS.md)** - Complete component API
- **[LiveComponent Architecture](components/LIVECOMPONENT_ARCHITECTURE.md)** - Two-tier component system
- **[Component Best Practices](components/COMPONENT_BEST_PRACTICES.md)** - Patterns and anti-patterns
- **[Component Examples](components/COMPONENT_EXAMPLES.md)** - Working examples
- **[Migration Guide](components/COMPONENT_MIGRATION_GUIDE.md)** - Upgrade existing components
- **[Performance Optimization](components/COMPONENT_PERFORMANCE_OPTIMIZATION.md)** - Make components faster
- **[Unified Design](components/COMPONENT_UNIFIED_DESIGN.md)** - Automatic Rust optimization
- **[Rust Components](components/RUST_COMPONENTS.md)** - Rust-powered component implementation

**Component Library**:
- [Breadcrumb](components/BREADCRUMB_COMPONENT_SUMMARY.md)
- [Divider](components/DIVIDER_COMPONENT_SUMMARY.md)
- [Icon](components/ICON_COMPONENT_IMPLEMENTATION.md)
- [List Group](components/LIST_GROUP_COMPONENT.md) | [Implementation](components/LISTGROUP_IMPLEMENTATION_SUMMARY.md)
- [Navbar](components/NAVBAR_COMPONENT_REPORT.md) | [Quick Start](components/NAVBAR_QUICK_START.md)
- [Pagination](components/PAGINATION_COMPONENT_SUMMARY.md)
- [Radio](components/RADIO_COMPONENT_SUMMARY.md) | [Visual Examples](components/RADIO_VISUAL_EXAMPLES.md)
- [Range](components/RANGE_COMPONENT_SUMMARY.md)
- [Textarea](components/TEXTAREA_COMPONENT_SUMMARY.md)
- [Toast](components/TOAST_COMPONENT_REPORT.md)
- [Tooltip](components/TOOLTIP_COMPONENT_REPORT.md)

### 📝 Forms
- **[Forms Implementation](forms/PYTHONIC_FORMS_IMPLEMENTATION.md)** - Django Forms integration

### 🔄 State Management
- **[State Management API](state-management/STATE_MANAGEMENT_API.md)** - Complete decorator reference (@debounce, @throttle, @loading, @cache, @client_state, @optimistic, DraftModeMixin)
- **[Quick Start](state-management/STATE_MANAGEMENT_QUICKSTART.md)** - 5-minute guide to state management
- **[Tutorial](state-management/STATE_MANAGEMENT_TUTORIAL.md)** - Step-by-step product search example
- **[Patterns & Best Practices](state-management/STATE_MANAGEMENT_PATTERNS.md)** - Common patterns and anti-patterns
- **[Examples](state-management/STATE_MANAGEMENT_EXAMPLES.md)** - Copy-paste ready examples
- **[Migration Guide](state-management/STATE_MANAGEMENT_MIGRATION.md)** - From manual JavaScript to decorators
- **[Architecture](state-management/STATE_MANAGEMENT_ARCHITECTURE.md)** - Implementation details
- **[Framework Comparison](state-management/STATE_MANAGEMENT_COMPARISON.md)** - vs Phoenix LiveView & Laravel Livewire

**Implementation History**:
- [Phase 1: Debounce & Throttle](state-management/IMPLEMENTATION_PHASE1.md)
- [Phase 2: @loading Attribute](state-management/IMPLEMENTATION_PHASE2.md)
- [Phase 3: @client_state](state-management/IMPLEMENTATION_PHASE3.md)
- [Phase 4: @cache Decorator](state-management/IMPLEMENTATION_PHASE4.md)
- [Phase 5: @optimistic & DraftMode](state-management/IMPLEMENTATION_PHASE5.md)
- [Loading Attribute Improvements](state-management/LOADING_ATTRIBUTE_IMPROVEMENTS.md)

### 🎭 Actors
- **[Actor Limitations](actors/ACTOR_LIMITATIONS.md)** - Known limitations
- **[Actor State Management](actors/ACTOR_STATE_MANAGEMENT.md)** - Managing state in actors

### 🌳 Virtual DOM
- **[VDOM Patching Issue](vdom/VDOM_PATCHING_ISSUE.md)** - Form value preservation
- **[VDOM Root Cause](vdom/VDOM_ROOT_CAUSE.md)** - Deep dive into VDOM issues

### 📄 Templates
- **[Template Inheritance Design](templates/TEMPLATE_INHERITANCE_DESIGN.md)** - Inheritance system design
- **[Template Inheritance Integration](templates/TEMPLATE_INHERITANCE_INTEGRATION.md)** - Integration guide

### 🧪 Testing
- **[JavaScript Testing](testing/TESTING_JAVASCRIPT.md)** - Test JavaScript code
- **[Testing Pages](testing/TESTING_PAGES.md)** - Test LiveView pages

### 💻 IDE Setup
- **[Rust IDE Setup](ide-setup/IDE_SETUP_RUST.md)** - Configure IntelliJ IDEA for Rust
- **[PyCharm Setup](ide-setup/PYCHARM_SETUP.md)** - Complete PyCharm configuration
- **[PyCharm Quick Start](ide-setup/PYCHARM_QUICK_START.md)** - 10-minute essential setup

### 🚀 Development & CI/CD
- **[AI Workflow Process](development/AI_WORKFLOW_PROCESS.md)** - AI-assisted development workflow
- **[Definition of Done](development/DEFINITION_OF_DONE.md)** - Quality standards
- **[CI Optimization](development/CI_OPTIMIZATION.md)** - Parallel test execution guide

### 🎯 Marketing & Strategy
- **[Marketing](marketing/MARKETING.md)** - Marketing strategy
- **[Marketing Next Steps](marketing/MARKETING_NEXT_STEPS.md)** - Action items
- **[Marketing README Section](marketing/README_MARKETING_SECTION.md)** - README marketing content
- **[Technical Pitch](marketing/TECHNICAL_PITCH.md)** - Technical value proposition
- **[Framework Comparison](marketing/FRAMEWORK_COMPARISON.md)** - vs alternatives
- **[Why Not Alternatives](marketing/WHY_NOT_ALTERNATIVES.md)** - Why djust over X

### 📦 Archive
Historical documents preserved for reference:
- [Phase 3 Complete](archive/PHASE3_COMPLETE.md)
- [Phase 5 Design](archive/PHASE5_DESIGN.md)
- [Phase 8.2 Enhancements](archive/PHASE8.2_ENHANCEMENTS.md)
- [Code Review Response](archive/CODE_REVIEW_RESPONSE.md)
- [PR Description](archive/PR_DESCRIPTION.md)
- [PR Summary](archive/PR_SUMMARY.md)
- [Next Steps](archive/NEXT_STEPS.md)
- [JavaScript Tests Complete](archive/JAVASCRIPT_TESTS_COMPLETE.md)
- [Test Suite Summary](archive/TEST_SUITE_SUMMARY.md)
- [TODO Template Reversed Filter](archive/TODO_TEMPLATE_REVERSED_FILTER.md)

---

## Project Root Files

Essential files kept in the project root:
- **[README.md](../README.md)** - Project overview
- **[CHANGELOG.md](../CHANGELOG.md)** - Version history
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Contribution guidelines
- **[ROADMAP.md](../ROADMAP.md)** - Future development plans
- **[CLAUDE.md](../CLAUDE.md)** - Claude Code instructions

---

## Documentation Structure

```
docs/
├── README.md (this file)
├── actors/           # Actor system documentation
├── archive/          # Historical documents
├── components/       # Component system & library
├── development/      # Development process & CI/CD
├── forms/           # Django Forms integration
├── guides/          # User guides
├── ide-setup/       # IDE configuration
├── marketing/       # Marketing & strategy
├── state-management/ # State management API & guides (includes implementation phases)
├── templates/       # Template system
├── testing/         # Testing guides
└── vdom/           # Virtual DOM internals
```

---

## Contributing to Documentation

When adding new documentation:

1. **Choose the right directory**:
   - User-facing guides → `guides/`
   - Component docs → `components/`
   - Implementation details → relevant subsystem folder
   - Historical/completed phases → `archive/`

2. **Link from this README**: Add your document to the appropriate section above

3. **Follow existing patterns**:
   - Clear, descriptive titles
   - Code examples with syntax highlighting
   - Real-world use cases
   - Performance considerations

4. **Keep root clean**: Only README, CHANGELOG, CONTRIBUTING, ROADMAP, and CLAUDE.md belong in project root

---

**Last Updated**: November 14, 2024
