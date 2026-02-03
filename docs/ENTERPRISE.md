# djust Enterprise

This document outlines the strategy, features, and licensing for a commercial enterprise tier of djust.

---

## Executive Summary

**djust** is an open-source Phoenix LiveView-style framework for Django. **djust Enterprise** extends the core with features required by large organizations: compliance, security, scale, and support.

**Model:** Open Core — generous MIT-licensed open source tier, commercial license for enterprise-specific features.

**Target customers:** Companies with >50 developers, regulated industries (finance, healthcare, government), SaaS platforms serving enterprise clients.

---

## Feature Matrix

### Core (MIT Licensed — Always Free)

| Category | Features |
|----------|----------|
| **LiveView Engine** | Components, VDOM diffing, WebSocket, SSE fallback |
| **Developer Experience** | Debug toolbar, hot reload, CLI tools, test helpers |
| **Forms** | LiveForm, validation, `visible_if` conditionals |
| **Data** | Async assigns, streams, real-time updates |
| **Navigation** | SPA mode, morphing refreshes, persistent elements |
| **Uploads** | File uploads with progress, drag-drop |
| **Mobile** | Touch gestures, viewport-aware rendering |
| **i18n** | Multi-language, RTL support |
| **Admin** | Basic LiveView admin integration |
| **State** | Memory and Redis backends |
| **Scaling** | Single-node, Redis pub/sub |

### Enterprise (Commercial License)

| Category | Features | Why Enterprise? |
|----------|----------|-----------------|
| **Audit & Compliance** | LiveAudit streaming, session replay, SIEM integration, compliance reports, PII masking | Requires dedicated support, regulated industry focus |
| **Security** | Component-level RBAC, OPA policy integration, crypto tenant isolation | Complex setup, security review needed |
| **Identity** | SAML 2.0, OIDC, LDAP/AD, multi-IdP, JIT provisioning | Enterprise-specific protocols |
| **Scale** | Horizontal WebSocket clustering, NATS backplane, connection draining, geo-routing | Infrastructure complexity |
| **Observability** | OpenTelemetry deep integration, pre-built Datadog/Grafana dashboards | Vendor integrations |
| **Resilience** | Session continuity, graceful degradation, automatic failover | Mission-critical deployments |
| **Support** | Priority support, SLAs, dedicated Slack channel, architecture review | Human time |

---

## Enterprise Features — Detailed

### 1. LiveAudit: Real-Time Audit Trail

**Problem:** Compliance frameworks (SOC2, HIPAA, GDPR, PCI-DSS) require comprehensive audit trails. Bolting this onto LiveView is error-prone.

**Solution:** Automatic audit event generation at the rendering layer.

```python
# settings.py
DJUST_ENTERPRISE = {
    "audit": {
        "enabled": True,
        "sinks": ["database", "s3://compliance-bucket/audit/"],
        "siem": {
            "type": "splunk",
            "endpoint": "https://splunk.company.com/services/collector",
            "token": env("SPLUNK_HEC_TOKEN"),
        },
        "pii_fields": ["ssn", "email", "phone"],  # Auto-masked in logs
    }
}
```

**Audit event structure:**
```json
{
    "timestamp": "2026-02-03T15:00:00Z",
    "event_id": "uuid",
    "session_id": "ws_session_123",
    "user_id": 456,
    "user_email": "j***@company.com",  // Masked
    "ip_address": "192.168.1.100",
    "component": "EmployeeProfile",
    "action": "update_salary",
    "params": {"employee_id": 789, "new_salary": "[REDACTED]"},
    "result": "success",
    "render_time_ms": 12,
    "geo": {"country": "US", "region": "VA"}
}
```

**Session replay:**
```python
from djust.enterprise import replay_session

# Reconstruct exact UI state at any point
replay = replay_session("ws_session_123", at="2026-02-03T14:55:00Z")
replay.render_html()  # Returns HTML as user saw it
```

---

### 2. Component-Level RBAC

**Problem:** Django permissions are model-level. Enterprises need UI-level control (this user can view but not edit, this field is PII-restricted).

**Solution:** Declarative policies at the component level.

```python
from djust.enterprise import LiveViewRBAC, policy

class SalaryDashboard(LiveViewRBAC):
    
    @policy(view="hr_staff", edit="hr_manager")
    def salary_table(self):
        return self.render_component("salary_table")
    
    @policy(view="hr_manager", pii=True)
    def ssn_field(self, employee):
        return employee.ssn
```

**OPA (Open Policy Agent) integration:**
```python
DJUST_ENTERPRISE = {
    "rbac": {
        "backend": "opa",
        "endpoint": "http://opa:8181/v1/data/djust/allow",
    }
}
```

```rego
# policy.rego
package djust

allow {
    input.component == "SalaryDashboard"
    input.action == "view"
    "hr_staff" in input.user.roles
}

allow {
    input.component == "SalaryDashboard"
    input.action == "edit"
    "hr_manager" in input.user.roles
    input.user.department == input.resource.department
}
```

---

### 3. Horizontal WebSocket Scaling

**Problem:** Single-node WebSocket servers are bottlenecks and single points of failure.

**Solution:** Distributed WebSocket handling with intelligent routing.

```python
DJUST_ENTERPRISE = {
    "scaling": {
        "backend": "nats",  # or "redis_cluster"
        "servers": ["nats://nats1:4222", "nats://nats2:4222"],
        "sticky_sessions": True,  # Keep user on same node when possible
        "connection_draining": {
            "enabled": True,
            "grace_period_seconds": 30,
        },
    }
}
```

**Features:**
- Automatic connection migration on node failure
- Zero-downtime deploys with connection draining
- Geographic routing (EU users → EU nodes)
- Per-tenant connection isolation

**Kubernetes integration:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: djust-app
  annotations:
    djust.io/websocket-scaling: "enabled"
    djust.io/drain-on-shutdown: "true"
```

---

### 4. Enterprise SSO Gateway

**Problem:** Enterprises require integration with existing identity infrastructure.

**Solution:** Drop-in SSO with automatic WebSocket session binding.

```python
DJUST_ENTERPRISE = {
    "sso": {
        "providers": [
            {
                "type": "saml2",
                "name": "Corporate AD",
                "metadata_url": "https://idp.company.com/metadata.xml",
                "attribute_mapping": {
                    "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                    "groups": "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups",
                },
            },
            {
                "type": "oidc",
                "name": "Okta",
                "client_id": env("OKTA_CLIENT_ID"),
                "client_secret": env("OKTA_CLIENT_SECRET"),
                "discovery_url": "https://company.okta.com/.well-known/openid-configuration",
            },
        ],
        "jit_provisioning": True,  # Auto-create users on first login
        "group_role_mapping": {
            "CN=HR,OU=Groups,DC=company,DC=com": ["hr_staff"],
            "CN=HR-Managers,OU=Groups,DC=company,DC=com": ["hr_staff", "hr_manager"],
        },
    }
}
```

---

### 5. Compliance Mode

**Problem:** Security reviews are time-consuming. Every enterprise asks the same questions.

**Solution:** One toggle enables secure defaults and generates compliance documentation.

```python
# settings.py
DJUST_COMPLIANCE_MODE = "strict"  # or "moderate" or "off"
```

**What "strict" enables:**
- ✅ Audit logging (all events)
- ✅ TLS-only WebSockets (reject ws://)
- ✅ Session timeout (30 min idle)
- ✅ PII field auto-masking in logs
- ✅ Rate limiting (strict defaults)
- ✅ CSRF protection on all events
- ✅ Content Security Policy headers
- ✅ Secure cookie flags

**Compliance report generation:**
```bash
djust compliance-report --format pdf --framework soc2
djust compliance-report --format json --framework hipaa
```

Outputs a checklist showing which controls are active, evidence of configuration, and gaps to address.

---

### 6. Deep Observability (APM)

**Problem:** "The dashboard feels slow" is impossible to debug without tracing.

**Solution:** OpenTelemetry instrumentation across the full LiveView lifecycle.

```python
DJUST_ENTERPRISE = {
    "observability": {
        "opentelemetry": {
            "endpoint": "http://otel-collector:4317",
            "service_name": "djust-app",
        },
        "metrics": {
            "render_time_histogram": True,
            "patch_size_histogram": True,
            "websocket_latency": True,
            "active_connections_gauge": True,
        },
        "tracing": {
            "sample_rate": 0.1,  # 10% of requests
            "trace_db_queries": True,
        },
    }
}
```

**Pre-built dashboards:**
- Grafana: WebSocket connections, render times, error rates
- Datadog: APM integration with LiveView-specific spans
- New Relic: Custom instrumentation

**Trace spans:**
```
[djust.websocket.connect] 2ms
  └─[djust.mount] 15ms
      └─[django.db.query] 3ms (SELECT * FROM employees...)
      └─[djust.render] 8ms
          └─[djust.vdom.diff] 2ms
[djust.websocket.event] 12ms
  └─[djust.handle_event: update_status] 10ms
      └─[django.db.query] 4ms
      └─[djust.render] 5ms
```

---

## Licensing

### Open Source License (Core)

**License:** MIT

```
MIT License

Copyright (c) 2024-2026 djust contributors

Permission is hereby granted, free of charge, to any person obtaining a copy...
```

### Enterprise License

**License:** Commercial (proprietary) or BSL 1.1 (source-available)

**Option A: Proprietary**
- Source code not visible
- Standard commercial EULA
- Simpler legally, less community trust

**Option B: Business Source License (BSL 1.1)**
- Source code visible (builds trust)
- Free for non-production use
- Production use requires paid license
- Converts to open source after 3 years (delayed open source)
- Used by: MariaDB, CockroachDB, Sentry

**Recommendation:** BSL 1.1 — balances revenue with transparency.

---

## Pricing

### Proposed Tiers

| Tier | Price | Includes |
|------|-------|----------|
| **Community** | Free | Core framework (MIT) |
| **Team** | $500/month | Enterprise features, 5 developers, email support |
| **Business** | $2,000/month | Enterprise features, 25 developers, priority support, Slack |
| **Enterprise** | Custom | Unlimited developers, SLA, dedicated support, architecture review |

### Pricing Models Considered

| Model | Pros | Cons |
|-------|------|------|
| **Per-seat** | Predictable, scales with team | Discourages adoption |
| **Per-server** | Fair for infrastructure cost | Hard to track in k8s |
| **Flat annual** | Simple, predictable | Doesn't scale with usage |
| **Usage-based** | Fair, aligns with value | Unpredictable bills |

**Recommendation:** Per-seat with generous free tier (up to 3 developers free for startups).

---

## Go-to-Market

### Phase 1: Build Credibility (Now)
- Keep all current roadmap MIT licensed
- Ship v0.7, v0.8, v0.9 with excellent DX
- Build case studies (JBM, internal projects)
- Grow community (Discord, Twitter, blog)

### Phase 2: Enterprise Beta (Q3 2026)
- Build LiveAudit as first enterprise feature
- Private beta with 3-5 design partners
- Iterate on pricing and packaging
- Legal review of BSL license

### Phase 3: GA Launch (Q4 2026)
- Public launch of djust Enterprise
- Pricing page on djust.org
- Sales process for Enterprise tier
- Partner program for consultants

---

## Competitive Landscape

| Framework | License | Enterprise Offering |
|-----------|---------|---------------------|
| Phoenix LiveView | MIT | None (Elixir ecosystem) |
| Laravel Livewire | MIT | None (Laravel ecosystem) |
| Hotwire/Turbo | MIT | None |
| HTMX | BSD | None |
| **djust** | MIT | **djust Enterprise (planned)** |

**Opportunity:** No LiveView-style framework has an enterprise tier. djust can be first.

---

## Open Questions

1. **Feature boundary:** Should horizontal scaling be enterprise-only, or just the NATS/advanced backends?
2. **Delayed open source:** If using BSL, what's the right conversion period? (2 years? 3 years?)
3. **Startup program:** Free enterprise tier for YC/Techstars companies?
4. **Consulting:** Offer paid implementation services alongside licenses?
5. **Certification:** djust Certified Developer program?

---

## Next Steps

- [ ] Legal review of BSL 1.1 license
- [ ] Build LiveAudit proof-of-concept
- [ ] Identify 3-5 enterprise beta partners
- [ ] Design pricing page for djust.org
- [ ] Write enterprise feature documentation
- [ ] Set up license key infrastructure

---

*Last updated: 2026-02-03*
