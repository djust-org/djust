# djust Documentation

This directory contains comprehensive documentation for djust organized by topic.

## Quick Navigation

### 📚 Getting Started
- **[Quick Start Guide](guides/QUICKSTART.md)** - Get up and running in 5 minutes
- **[Authentication & Authorization](guides/authentication.md)** - View-level and handler-level auth with Django permissions
- **[PWA Guide](guides/pwa.md)** - Build offline-first Progressive Web Apps
- **[Multi-Tenant Guide](guides/multi-tenant.md)** - SaaS architecture with tenant isolation
- **[Deployment Guide](guides/DEPLOYMENT.md)** - Deploy djust to production
- **[HTTP Mode Example](guides/HTTP_MODE_EXAMPLE.md)** - Use djust without WebSockets
- **[MCP Server](guides/mcp-server.md)** - AI assistant integration via Model Context Protocol
- **[Template Requirements](guides/template-requirements.md)** - Required template attributes (dj-view, dj-root)
- **[Working with External Services](guides/services.md)** - AWS, REST APIs, Redis integration patterns
- **[Error Code Reference](guides/error-codes.md)** - Complete error code guide with fixes
- **[System Checks Reference](system-checks.md)** - All 37 check IDs (C/V/S/T/Q), severities, suppression patterns, and false positives
- **[Scaffolding Generator](guides/scaffolding.md)** - Generate CRUD LiveView scaffolds from model definitions

### 🔌 Real-Time Features
- **[Navigation](guides/navigation.md)** - URL state management with live_patch and live_redirect
- **[Flash Messages](website/guides/flash-messages.md)** - Transient notifications with put_flash (Phoenix-style)
- **[Presence](guides/presence.md)** - Real-time user presence tracking and live cursors
- **[Streaming](guides/streaming.md)** - Real-time partial DOM updates for LLM chat and live feeds
- **[Uploads](guides/uploads.md)** - Chunked binary file uploads via WebSocket
- **[Hooks](guides/hooks.md)** - Client-side JavaScript lifecycle hooks
- **[on_mount Hooks](website/guides/on-mount-hooks.md)** - Cross-cutting server-side mount hooks (auth, telemetry, tenant resolution)
- **[Model Binding](guides/model-binding.md)** - Two-way data binding for form inputs

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
- **[Forms Implementation](website/guides/forms.md)** - Django Forms integration

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
- **Marketing** - Marketing strategy
- **Marketing Next Steps** - Action items
- **Marketing README Section** - README marketing content
- **Technical Pitch** - Technical value proposition
- **Framework Comparison** - vs alternatives
- **Why Not Alternatives** - Why djust over X

### 🌐 Example Site
- **[Phase 5 Showcase Plan](example-site/EXAMPLE_SITE_PHASE5_PLAN.md)** - Plan to showcase Phase 5 features on example site
- **[Current Site Structure](example-site/CURRENT_SITE_STRUCTURE.md)** - Comprehensive inventory of existing site
- **[Progress Tracker](example-site/PROGRESS_TRACKER.md)** - Implementation progress tracking

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
├── example-site/     # Example site updates & tracking
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
