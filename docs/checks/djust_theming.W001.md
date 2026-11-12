---
id: djust_theming.W001
---

<!-- doc: guide -->
### djust_theming.W001: Preset fails WCAG AA contrast

**Severity**: Warning

**What causes it**: The active theme preset has a colour pair below the WCAG AA minimum. Message: 'Preset "<name>" <mode> mode: <label> contrast ratio X:1 < Y:1 (WCAG AA)'. The pairs are every `*_foreground` label on its fill (4.5:1), `link` used as text on the background and on a card (4.5:1), the derived `primary_text`, `brand_text`, `info_text`, `success_text`, `warning_text` and `destructive_text` (`--primary-text` ..., a fill read as text) on the background and on a card, and for the status colours on the 10% tint of an alert or toast (4.5:1; each is solved from its fill by lightness, so ordinary palettes cannot trip them), and the `input` border on the background (3:1, WCAG 1.4.11).

**Fix**: Adjust the named foreground or background colour to reach at least the minimum ratio. For the derived `*_text` pairs there is no such token to set: change `background`, `card` or the fill's hue or saturation instead; they only miss when the page and card are mid-tone.

---
