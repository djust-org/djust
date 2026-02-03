# Future Considerations & Potential Add-on Packages

This document tracks ideas that may benefit djust but don't belong in the core framework — either because they're specialized use cases, add complexity, or would be better served as separate packages.

---

## Potential Add-on Packages

### djust-admin

**Idea:** Enhanced Django admin with real-time LiveView capabilities.

**Features under consideration:**
- Real-time model editing without page refresh
- Live dashboard widgets (user activity, system stats)
- Inline list editing with instant save
- Real-time search and filtering
- Bulk action progress tracking
- Admin activity audit trail
- Customizable admin themes

**Status:** Planned

**Why separate:** Not everyone needs enhanced admin. Core djust already has `LiveViewAdminMixin` for basic integration; this package goes further with opinionated admin UX.

---

### djust-forms-extended

**Idea:** Advanced form features beyond core LiveForm.

**Features under consideration:**
- Multi-context rendering (render same form as card, email, PDF)
- Form wizards / multi-step flows
- Conditional field groups (show/hide entire sections)
- Form versioning for audit trails

**Status:** Under evaluation

**Why separate:** Core LiveForm handles 90% of use cases. These features add complexity and dependencies that not everyone needs.

---

### djust-workflows

**Idea:** Declarative workflow engine with LiveView UI.

**Inspired by:** NextDJ "Oracle pattern" — define workflow once, render anywhere.

**Features under consideration:**
- JSON schema workflow definitions
- State machine integration
- Approval chains with real-time status
- Workflow templates library

**Status:** Concept only

**Why separate:** This is a different paradigm (schema-driven) vs djust's code-first approach. Better as opt-in package for enterprise users who need it.

---

### djust-charts

**Idea:** Real-time chart components with WebSocket updates.

**Features under consideration:**
- Wrapper for Chart.js / ECharts / Plotly
- `dj-chart` directive for declarative binding
- Streaming data support
- Dashboard layout helpers

**Status:** Community interest noted

**Why separate:** Chart libraries are large dependencies. Keep core lightweight.

---

### djust-maps

**Idea:** Interactive map components (Leaflet/Mapbox) with live updates.

**Features under consideration:**
- Real-time marker updates
- Geofencing with LiveView events
- Heatmap overlays
- Clustering for large datasets

**Status:** Concept only

**Why separate:** Maps are specialized; most apps don't need them.

---

### djust-templating

**Idea:** Reusable template components and layout primitives for djust apps.

**Features under consideration:**
- Pre-built component library (modals, cards, tables, alerts)
- Layout primitives (sidebar, header, responsive grid)
- Slot-based composition patterns
- Template inheritance helpers
- Integration with djust-theming for consistent styling

**Status:** Planned

**Why separate:** Keeps core framework unopinionated about UI. Users can choose this package or bring their own component library.

**Related:** `djust-theming` (already exists at `djust_project/djust-theming/`) — handles color schemes, dark mode, 7 presets, shadcn/ui compatible.

---

### djust-storybook

**Idea:** Component development and documentation in isolation.

**Features under consideration:**
- Render components with mock assigns
- Hot-reload on save
- Side-by-side state comparison (empty, loading, error, success)
- Visual regression snapshot testing
- Auto-generated component documentation
- Embed live examples in docs via `{% djust_playground %}`

**Status:** Under evaluation

**Why separate:** Not needed for production apps. Development/documentation tooling should be optional.

---

### djust-audit

**Idea:** Comprehensive audit trail and compliance logging.

**Features under consideration:**
- Automatic event logging (who, what, when, IP, session)
- Immutable audit trail with tamper detection
- SIEM integration (Splunk, Datadog, ELK)
- Session replay (reconstruct exact UI state)
- GDPR/HIPAA/SOC2 compliance reports
- PII field masking in logs

**Status:** Planned (potential enterprise feature)

**Why separate:** Compliance features add overhead. Most apps don't need SIEM integration.

---

### djust-sso

**Idea:** Enterprise single sign-on integration.

**Features under consideration:**
- SAML 2.0 support
- OIDC/OAuth2 integration
- LDAP/Active Directory sync
- Just-in-time user provisioning
- Group-to-role mapping
- Multi-IdP configurations

**Status:** Planned (potential enterprise feature)

**Why separate:** Most Django apps use django-allauth. Enterprise SSO is specialized.

---

## Ideas Under Evaluation

These may be added to core or become packages — still deciding.

### Server Components Pattern

Allow rendering expensive components server-side with streaming.

**Pros:** Better SEO, faster initial paint
**Cons:** Complexity, different mental model
**Decision:** Watch React Server Components adoption, revisit in v0.9

### LiveView Islands

Embed LiveView components in otherwise static pages without full page WebSocket.

**Pros:** Gradual adoption, smaller footprint
**Cons:** Multiple connections, state coordination
**Decision:** Interesting for migration paths, needs design work

### Form Builder UI

Visual drag-and-drop form builder that generates LiveForm code.

**Pros:** Non-technical users, rapid prototyping
**Cons:** Generated code quality, maintenance burden
**Decision:** Better as third-party tool, not core

---

## Rejected Ideas

Ideas evaluated and decided against.

| Idea | Reason for rejection |
|------|---------------------|
| Built-in state machine | Django-fsm exists, don't reinvent |
| GraphQL subscriptions in core | Too specialized, document as pattern instead |
| Native mobile SDK | Out of scope, use responsive web or Capacitor |

---

## How to Propose Additions

1. Open a GitHub Discussion with the idea
2. Include: use case, proposed API, why it belongs in djust
3. Community feedback period (2 weeks)
4. Core team decision: roadmap, package, or reject

---

## djust Enterprise

For comprehensive enterprise strategy, features, and licensing details, see:

**📄 [ENTERPRISE.md](./ENTERPRISE.md)**

Covers:
- Feature matrix (open source vs enterprise)
- Detailed enterprise feature specs (LiveAudit, RBAC, SSO, scaling, compliance)
- Licensing options (BSL 1.1 recommended)
- Pricing tiers
- Go-to-market strategy
- Competitive analysis

---

*Last updated: 2026-02-03*
