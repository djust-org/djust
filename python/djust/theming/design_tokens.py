"""
Design tokens for spacing, typography, and structural styling.

These tokens provide a foundation for consistent design systems and integrate
seamlessly with djust-theming's color system. All tokens are theme-agnostic
(work with any color palette) and are designed to be composable.
"""


def get_spacing_tokens() -> str:
    """
    Generate spacing scale CSS custom properties.

    Uses a consistent 4px base unit with both linear and geometric progressions
    for fine-grained control at small sizes and exponential growth at larger sizes.

    Returns:
        CSS string with spacing scale variables
    """
    return """  /* Spacing scale (4px base unit) */
  --space-0: 0;
  --space-1: 0.25rem;    /* 4px */
  --space-2: 0.5rem;     /* 8px */
  --space-3: 0.75rem;    /* 12px */
  --space-4: 1rem;       /* 16px */
  --space-5: 1.25rem;    /* 20px */
  --space-6: 1.5rem;     /* 24px */
  --space-8: 2rem;       /* 32px */
  --space-10: 2.5rem;    /* 40px */
  --space-12: 3rem;      /* 48px */
  --space-16: 4rem;      /* 64px */
  --space-20: 5rem;      /* 80px */
  --space-24: 6rem;      /* 96px */"""


def get_typography_tokens() -> str:
    """
    Generate typography CSS custom properties.

    Includes font sizes, line heights, and font weights for consistent text styling.
    Uses rem units for accessibility (respects user's browser font size preference).

    Returns:
        CSS string with typography variables
    """
    return """  /* Typography scale */
  --text-xs: 0.75rem;     /* 12px */
  --text-sm: 0.875rem;    /* 14px */
  --text-base: 1rem;      /* 16px */
  --text-lg: 1.125rem;    /* 18px */
  --text-xl: 1.25rem;     /* 20px */
  --text-2xl: 1.5rem;     /* 24px */
  --text-3xl: 1.875rem;   /* 30px */
  --text-4xl: 2.25rem;    /* 36px */
  --text-5xl: 3rem;       /* 48px */
  --text-6xl: 3.75rem;    /* 60px */

  /* Line heights */
  --leading-none: 1;
  --leading-tight: 1.25;
  --leading-snug: 1.375;
  --leading-normal: 1.5;
  --leading-relaxed: 1.625;
  --leading-loose: 2;

  /* Font weights */
  --font-thin: 100;
  --font-extralight: 200;
  --font-light: 300;
  --font-normal: 400;
  --font-medium: 500;
  --font-semibold: 600;
  --font-bold: 700;
  --font-extrabold: 800;
  --font-black: 900;"""


def get_radius_extensions() -> str:
    """
    Generate extended border radius tokens.

    Extends the base --radius variable from theme tokens with additional sizes.
    These values are relative to --radius so they adapt to the theme's preference.

    Returns:
        CSS string with radius extensions
    """
    return """  /* Extended border radius (based on theme's --radius) */
  --radius-sm: calc(var(--radius) - 0.125rem);
  --radius-md: var(--radius);
  --radius-lg: calc(var(--radius) + 0.125rem);
  --radius-xl: calc(var(--radius) + 0.25rem);
  --radius-2xl: calc(var(--radius) + 0.5rem);
  --radius-full: 9999px;"""


def get_transition_tokens() -> str:
    """
    Generate animation and transition timing tokens.

    Provides consistent durations and easing curves for UI animations.

    Returns:
        CSS string with transition timing variables
    """
    return """  /* Transitions and animations */
  --duration-fast: 150ms;
  --duration-normal: 200ms;
  --duration-slow: 300ms;
  --duration-slower: 500ms;

  --ease-in: cubic-bezier(0.4, 0, 1, 1);
  --ease-out: cubic-bezier(0, 0, 0.2, 1);
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);"""


def get_shadow_tokens() -> str:
    """
    Generate theme-aware shadow tokens.

    Shadows adapt to light/dark themes by using different opacity values
    and adjusting shadow colors based on the theme mode.

    Returns:
        CSS string with shadow definitions for both light and dark themes
    """
    return """/* Theme-aware shadows */
:root,
[data-theme="light"] {
  --shadow-sm: 0 1px 2px 0 hsl(var(--foreground) / 0.05);
  --shadow-md: 0 4px 12px -4px hsl(var(--foreground) / 0.1);
  --shadow-lg: 0 8px 16px -6px hsl(var(--foreground) / 0.15);
  --shadow-xl: 0 20px 25px -5px hsl(var(--foreground) / 0.1), 0 10px 10px -5px hsl(var(--foreground) / 0.04);
}

.dark,
[data-theme="dark"] {
  --shadow-sm: 0 1px 3px 0 hsl(0 0% 0% / 0.3);
  --shadow-md: 0 4px 12px -4px hsl(0 0% 0% / 0.4);
  --shadow-lg: 0 8px 16px -6px hsl(0 0% 0% / 0.5);
  --shadow-xl: 0 20px 25px -5px hsl(0 0% 0% / 0.25), 0 10px 10px -5px hsl(0 0% 0% / 0.1);
}"""


def get_typography_classes() -> str:
    """
    Generate typography utility classes.

    Provides semantic heading and body text classes that use the typography tokens.
    These classes enforce a consistent type hierarchy across the application.

    Returns:
        CSS string with typography classes
    """
    return """/* Typography hierarchy */
.h1 {
  font-size: var(--text-4xl);
  font-weight: var(--font-bold);
  line-height: var(--leading-tight);
  letter-spacing: -0.025em;
}

.h2 {
  font-size: var(--text-3xl);
  font-weight: var(--font-bold);
  line-height: var(--leading-tight);
  letter-spacing: -0.025em;
}

.h3 {
  font-size: var(--text-2xl);
  font-weight: var(--font-semibold);
  line-height: var(--leading-tight);
}

.h4 {
  font-size: var(--text-xl);
  font-weight: var(--font-semibold);
  line-height: var(--leading-snug);
}

.h5 {
  font-size: var(--text-lg);
  font-weight: var(--font-medium);
  line-height: var(--leading-normal);
}

.h6 {
  font-size: var(--text-base);
  font-weight: var(--font-medium);
  line-height: var(--leading-normal);
}

/* Body text variants */
.text-body {
  font-size: var(--text-base);
  line-height: var(--leading-normal);
}

.text-small {
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
}

.text-tiny {
  font-size: var(--text-xs);
  line-height: var(--leading-normal);
}

/* Text style utilities */
.text-uppercase {
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.text-balance {
  text-wrap: balance;
}

.text-pretty {
  text-wrap: pretty;
}"""


def get_interactive_utilities() -> str:
    """
    Generate interactive state utility classes.

    Provides consistent patterns for hover, focus, active, and disabled states
    that work across different components and integrate with theme colors.

    Returns:
        CSS string with interactive state classes
    """
    return """/* Interactive state patterns */
.interactive {
  cursor: pointer;
  transition: all var(--duration-fast) ease;
  -webkit-tap-highlight-color: transparent;
}

.interactive:hover {
  background-color: hsl(var(--accent));
}

.interactive:active {
  transform: scale(0.98);
}

.interactive:focus-visible {
  outline: 2px solid hsl(var(--ring));
  outline-offset: 2px;
}

.interactive:disabled,
.interactive[aria-disabled="true"] {
  opacity: 0.5;
  cursor: not-allowed;
  pointer-events: none;
}

/* Link patterns */
.link {
  color: hsl(var(--primary));
  text-decoration: underline;
  text-decoration-color: transparent;
  transition: text-decoration-color var(--duration-fast) ease;
}

.link:hover {
  text-decoration-color: currentColor;
}

.link:focus-visible {
  outline: 2px solid hsl(var(--ring));
  outline-offset: 2px;
  border-radius: 2px;
}

/* Focus ring utilities */
.focus-ring:focus-visible {
  outline: 2px solid hsl(var(--ring));
  outline-offset: 2px;
}

.focus-ring-inset:focus-visible {
  outline: 2px solid hsl(var(--ring));
  outline-offset: -2px;
}"""


def get_layout_utilities() -> str:
    """
    Generate layout utility classes.

    Common layout patterns like truncation, line clamping, and container queries
    that complement the existing Tailwind CSS utilities.

    Returns:
        CSS string with layout utilities
    """
    return """/* Layout utilities */
.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.line-clamp-4 {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Scrollbar styling */
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: hsl(var(--muted));
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: hsl(var(--muted-foreground) / 0.3);
  border-radius: 3px;
}

.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: hsl(var(--muted-foreground) / 0.5);
}"""


def get_layout_tokens() -> str:
    """
    Generate responsive layout CSS custom properties.

    Includes breakpoints for responsive design and structural dimensions for
    common layout patterns (sidebar, topbar, etc.).

    Returns:
        CSS string with layout/responsive variables
    """
    return """  /* Responsive breakpoints */
  --breakpoint-sm: 640px;
  --breakpoint-md: 768px;
  --breakpoint-lg: 1024px;
  --breakpoint-xl: 1280px;

  /* Layout dimensions */
  --sidebar-width: 280px;
  --sidebar-collapsed-width: 64px;
  --topbar-height: 56px;"""


def get_animation_keyframes() -> str:
    """
    Generate common animation keyframes.

    Provides reusable animations for fades, slides, pulses, and other common UI effects.

    Returns:
        CSS string with @keyframes and animation utilities
    """
    return """/* Animation keyframes */
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeOut {
  from {
    opacity: 1;
  }
  to {
    opacity: 0;
  }
}

@keyframes slideInFromRight {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

@keyframes slideOutToRight {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(100%);
  }
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* Animation utility classes */
.fade-in {
  animation: fadeIn var(--duration-normal) ease;
}

.fade-out {
  animation: fadeOut var(--duration-normal) ease;
}

.slide-in-right {
  animation: slideInFromRight var(--duration-normal) ease;
}

.slide-out-right {
  animation: slideOutToRight var(--duration-normal) ease;
}

.pulse {
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

.spin {
  animation: spin 1s linear infinite;
}"""


def get_reduced_motion_css() -> str:
    """
    Generate reduced-motion media query CSS.

    Overrides all duration tokens to 0ms and kills animations/transitions
    for users who prefer reduced motion. Complements the partial rules in
    ``performance.css`` and ``css_generator._generate_base_styles()``.

    Returns:
        CSS string with @media (prefers-reduced-motion: reduce) block.
    """
    return """/* Reduced motion accessibility */
@media (prefers-reduced-motion: reduce) {
  :root {
    --duration-fast: 0ms;
    --duration-normal: 0ms;
    --duration-slow: 0ms;
    --duration-slower: 0ms;
  }

  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}"""


def get_high_contrast_css() -> str:
    """
    Generate high-contrast media query CSS.

    Increases border widths, focus ring visibility, and forces solid borders
    for users who prefer more contrast. Works alongside the high-contrast
    theme presets in ``high_contrast.py``.

    Returns:
        CSS string with @media (prefers-contrast: more) block.
    """
    return """/* High contrast accessibility */
@media (prefers-contrast: more) {
  :root {
    --border-width: 2px;
    --ring-width: 3px;
    --ring-offset: 3px;
  }

  *,
  *::before,
  *::after {
    border-style: solid;
  }

  *:focus-visible {
    outline: var(--ring-width, 3px) solid hsl(var(--ring));
    outline-offset: var(--ring-offset, 3px);
  }

  .btn,
  .card-theme,
  .input-theme,
  .badge-primary,
  .badge-secondary,
  .badge-destructive,
  .badge-success,
  .badge-warning,
  .badge-info {
    border-width: var(--border-width, 2px);
  }
}"""


def generate_design_tokens_root_css() -> str:
    """
    Generate only the :root custom property declarations for design tokens.

    This includes spacing, typography scale, radius extensions, transitions,
    and shadow tokens — all the CSS custom properties that other rules depend on.
    Suitable for critical CSS (needed for first paint).

    Returns:
        CSS string with :root block and shadow definitions only.
    """
    sections = [
        "/* djust-theming - Design Tokens */",
        "",
        ":root {",
        get_spacing_tokens(),
        "",
        get_typography_tokens(),
        "",
        get_radius_extensions(),
        "",
        get_transition_tokens(),
        "",
        get_layout_tokens(),
        "}",
        "",
        get_shadow_tokens(),
    ]

    return "\n".join(sections)


def generate_design_tokens_classes_css(
    include_typography: bool = True,
    include_interactive: bool = True,
    include_layout: bool = True,
    include_animations: bool = True,
) -> str:
    """
    Generate design token class-based CSS (typography, interactive, layout, animations).

    These are utility classes that depend on the :root custom properties from
    ``generate_design_tokens_root_css()``. Suitable for deferred CSS (not needed
    for first paint).

    Args:
        include_typography: Include typography classes (.h1-.h6, etc.)
        include_interactive: Include interactive state patterns
        include_layout: Include layout utilities (truncate, line-clamp, etc.)
        include_animations: Include animation keyframes and utilities

    Returns:
        CSS string with class-based design token utilities.
    """
    sections = []

    if include_typography:
        sections.extend([get_typography_classes()])

    if include_interactive:
        if sections:
            sections.append("")
        sections.extend([get_interactive_utilities()])

    if include_layout:
        if sections:
            sections.append("")
        sections.extend([get_layout_utilities()])

    if include_animations:
        if sections:
            sections.append("")
        sections.extend([get_animation_keyframes()])

    # Accessibility media queries (always included)
    if sections:
        sections.append("")
    sections.append(get_reduced_motion_css())
    sections.append("")
    sections.append(get_high_contrast_css())

    return "\n".join(sections)


def generate_design_tokens_css(
    include_typography: bool = True,
    include_interactive: bool = True,
    include_layout: bool = True,
    include_animations: bool = True,
) -> str:
    """
    Generate complete design tokens CSS.

    Args:
        include_typography: Include typography classes (.h1-.h6, etc.)
        include_interactive: Include interactive state patterns
        include_layout: Include layout utilities (truncate, line-clamp, etc.)
        include_animations: Include animation keyframes and utilities

    Returns:
        Complete CSS string with all requested design tokens
    """
    root_css = generate_design_tokens_root_css()
    classes_css = generate_design_tokens_classes_css(
        include_typography=include_typography,
        include_interactive=include_interactive,
        include_layout=include_layout,
        include_animations=include_animations,
    )

    if classes_css:
        return root_css + "\n\n" + classes_css
    return root_css
