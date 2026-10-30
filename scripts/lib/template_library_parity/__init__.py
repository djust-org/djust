"""Parity ratchet between djust's own template libraries and their native handlers (#3663).

For ``djust_components`` and ``djust_theming``'s ``theme_tags``, a tag can be
rendered two ways: by Django's engine (the Python tag) or by djust's Rust
engine (``DjustTemplateBackend``, where a registered native handler wins). The
two are independent implementations, and for most tags they do not render the
same bytes. This package measures that, per tag, and holds the measurement in
a checked-in baseline that can only move toward parity.

``classify``
    Pure functions, no Django: structural scan of two HTML strings, the
    per-variant classification, the per-tag aggregate, the baseline file and
    the ratchet comparison.

``variants``
    Pure functions, no Django: argument generation from parameter names and
    the table of hand-written composites.

``harness``
    The Django-facing half: a deterministic process (frozen clock, counting
    uuids), the inventory of native tags, parameter discovery, the differential
    render. It configures Django settings and replaces ``uuid`` and
    ``datetime`` for the whole process, so it only ever runs in a FRESH
    process; ``scripts/template-library-parity.py`` is that process, and the
    tests drive it as a subprocess.
"""
