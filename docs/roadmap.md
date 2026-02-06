# Djust Roadmap

## Vision
To provide a "Unibody" architecture that combines Django's simplicity with Rust's performance, offering a secure, zero-API alternative to modern SPAs. The goal is to make standard Django Forms feel like high-end React forms without writing JavaScript, while ensuring maximum security by keeping business logic on the server.

## Phase 1: The Foundation (Open Core)
**License**: MIT (Open Source)
**Goal**: Build trust and adoption by releasing the core runtime.

*   **Core VDOM Engine**:
    *   Open-source Rust implementation of the Virtual DOM diffing algorithm.
    *   Ensures developers can debug rendering issues and verify the "engine" logic.
*   **JIT Template Scanner**:
    *   Rust-based static analysis of templates.
    *   Automatically detects used fields to prevent N+1 queries and ensure zero data leaks (if it's not in the HTML, it's not fetched).
*   **Basic LiveView**:
    *   WebSocket integration for server-side state management.
    *   Python-to-Rust bindings for seamless state updates.

## Phase 2: Developer Experience Revolution
**Focus**: Solving the "Glass House" problem and making Forms powerful.

*   **LiveModelForm**:
    *   **Real-Time Validation**: "The Red Squiggly Killer". Validate fields as users type via Rust-debounced events.
    *   **Dynamic Dependent Fields**: Handle logic like "Show State if Country is US" entirely in Python.
    *   **Auto-Save**: Google Docs-style auto-saving hooks.
*   **Service Worker Enhancements**:
    *   Prefetch on hover for near-instant navigation.
    *   App Shell pattern for instant page loads.
    *   WebSocket reconnection bridge with event buffering.
    *   See [SW Enhancements Guide](guides/sw-enhancements.md) for full details.
*   **Zero-JS File Uploads**:
    *   Chunked uploads over WebSockets.
    *   Server-driven progress bars without client-side `FormData` complexity.
*   **Stateful Wizards**:
    *   Multi-step form support with state held in memory/Redis.
    *   Instant Back/Forward navigation without database persistence until final submission.

## Phase 3: Brand & Security Positioning
**Theme**: "Industrial," "Cyberpunk," "High-Performance."

*   **"Security by Architecture"**:
    *   Marketing materials explaining the "No API" benefit.
    *   Focus on IP Protection (logic stays on server) and Attack Surface Reduction (1 WebSocket vs. 50 REST endpoints).
*   **Visual Identity**:
    *   **Logo**: The "Turbocharged D" (Django 'd' with a Rust gear).
    *   **Palette**: Django Green (`#44B78B`), Rust Orange (`#E57324`), and Deep Void (`#0B0F19`).
*   **Merchandise Strategy**:
    *   "Inside Baseball" gear for developers (e.g., "Rendering in 0.5ms" mugs).

## Phase 4: Enterprise & Monetization (The "Pro" Tier)
**Strategy**: "Commoditize the Engine, Charge for the Scale."
**Distribution**: Compiled Rust binaries (Shared Libraries) inside Python Wheels to protect IP.

*   **Djust Pro (Commercial License)**:
    *   **Clustering Engine**:
        *   Distributed state synchronization across multiple servers.
        *   Rust-to-Rust networking (bypassing Redis for extreme performance).
    *   **Advanced APM**:
        *   Deep tracing of render cycles, patch sizes, and latency.
    *   **Enterprise Components**:
        *   **PDF Generator**: High-performance server-side PDF rendering.
        *   **Super Table**: Excel-like grid with "Infinite Formset" capabilities.

*   **SaaS Offering**:
    *   Managed Sidecar Proxy.
    *   Multi-tenant Router for high-scale deployments.

## Technical Implementation Strategy
*   **Open Source**: Python logic, PyO3 bindings, Standard Template Renderer.
*   **Closed Source**: Complex clustering algorithms and enterprise features compiled to `.so` / `.pyd` binaries.
