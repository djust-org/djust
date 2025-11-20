# Djust Marketing Site Plan

## Overview
This document outlines the plan for the `djust` marketing site. The goal is to create a high-performance, visually striking site that communicates the "Unibody" architecture and security benefits of `djust`. The design will follow the "Industrial/Cyberpunk" aesthetic defined in the monetization strategy.

## Design System

### Theme
*   **Vibe**: Industrial, Cyberpunk, High-Performance, "Inside Baseball" for developers.
*   **Keywords**: Rust, Speed, Security, Zero-API, Black Box.

### Colors
*   **Background**: `#0B0F19` (Deep Void / Brand Dark)
*   **Primary (Django)**: `#44B78B` (Vibrant Green) - UI, Surface, Success.
*   **Secondary (Rust)**: `#E57324` (Burnt Orange) - Engine, Core, Action.
*   **Panel/Surface**: `#151B2B` (Brand Panel)
*   **Text**: `#E2E8F0` (Brand Text)
*   **Muted**: `#94A3B8` (Brand Muted)
*   **Danger**: `#F43F5E` (Brand Danger)

### Typography
*   **Headings**: `Inter` (sans-serif) - Bold, tight tracking.
*   **Body**: `Inter` (sans-serif) - Clean, readable.
*   **Code/Technical**: `JetBrains Mono` (monospace) - For code snippets and technical accents.

### UI Elements
*   **Glassmorphism**: Used for navbars and overlays (`backdrop-blur-md`, `bg-brand-dark/90`).
*   **Grid Patterns**: Subtle background grids to imply "blueprint" or "technical" nature.
*   **Gradients**: Transitions between Django Green and Rust Orange.
*   **Icons**: Heroicons (outline style) or custom SVG icons.

## Site Structure

### 1. Home Page (`index.html`)
*   **Hero Section**:
    *   Headline: "The Speed of Rust. The Simplicity of Django."
    *   Subheadline: "Build reactive, real-time apps without writing a single line of JavaScript."
    *   CTA: "Get Started" (Primary), "View on GitHub" (Secondary).
    *   Visual: Animated terminal or code comparison (Django vs. React).
*   **Value Props**:
    *   **Zero API**: No REST endpoints to manage.
    *   **Type-Safe**: Python-to-Rust bindings.
    *   **Secure**: Logic stays on the server.
*   **Feature Highlight**: "The Unibody Architecture" diagram.
*   **Social Proof**: "Trusted by..." (placeholder).
*   **Footer**: Links to Docs, GitHub, Twitter.

### 2. Security Page (`security.html`)
*   *Based on `Monetization-gemini.md` content.*
*   **Hero**: "The Most Secure API Is No API."
*   **Problem**: The "Glass House" problem (exposing logic in JS bundles).
*   **Solution**: The "Black Box" Guarantee (logic stays on server).
*   **Comparison Table**: React vs. Djust (Attack Surface, Data Leaks, etc.).
*   **Visuals**: "Browser Inspector" showing exposed JS vs. Djust's opaque HTML.

### 3. Documentation (`docs/index.html` - Placeholder)
*   Simple landing page for documentation.
*   Links to: Getting Started, API Reference, Tutorials.

### 4. Pricing/Pro (`pro.html` - Future)
*   "Commoditize the Engine, Charge for the Scale."
*   Free Tier (Open Core) vs. Pro Tier (Clustering, APM, Enterprise Components).

## Implementation Plan

### Tech Stack
*   **HTML5**: Semantic markup.
*   **Tailwind CSS**: For rapid styling and implementing the design system.
*   **Vanilla JS**: Minimal JS for mobile menu toggles (keeping true to the "No JS" philosophy where possible, though this is a static marketing site).

### Directory Structure
```
marketing-site/
├── index.html
├── security.html
├── css/
│   └── style.css (Tailwind output or custom overrides)
├── assets/
│   ├── images/
│   └── svg/
└── js/
    └── main.js (if needed)
```

### Next Steps
1.  Set up the `marketing-site` directory.
2.  Create `tailwind.config.js` with the brand colors and fonts.
3.  Implement `security.html` using the code provided in `Monetization-gemini.md`.
4.  Design and implement `index.html` following the same aesthetic.
5.  Create the Logo SVG file.
