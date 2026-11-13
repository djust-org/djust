### The `_legacy` theming module

The `djust.theming.themes._legacy` module — its `THEMES` dict and the
`get_theme()` / `list_themes()` functions, and the `Theme`, `Typography`,
`Spacing`, `BorderRadius`, `Shadows`, `Animations` and `ComponentStyles` classes
that `djust.theming.themes` re-exported from it — is gone. Migrate to the
design-system API:

```python
# Before
from djust.theming.themes._legacy import get_theme

theme = get_theme("dark")

# After
from djust.theming.theme_packs import get_design_system

theme = get_design_system("dark")
```
