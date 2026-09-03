- **`@humanfs/node` 0.16.7 → 0.16.8** (GHSA-p498-v437-472g, medium) — recursive
  copy followed symlinked files and copied data from outside the source tree.
  A dev-only transitive of `eslint`; no runtime surface. Lockfile-only, taken
  within the existing `^0.16.6` constraint, so no `overrides` entry was needed.
  `@humanfs/core` 0.19.1 → 0.19.2 and `@humanfs/types` 0.15.0 come along as
  dependencies of the patched release.
