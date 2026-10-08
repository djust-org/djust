# Provenance — vendored `markup5ever_rcdom`

**Why this exists.** `djust` needs `html5ever` 0.40 for the parser upgrade (#3011),
but `markup5ever_rcdom` has **no 0.40 release on crates.io** — upstream marks it
`publish = false` ("Basic, unsupported DOM structure for use by tests"), and its
crates.io releases are cut outside the html5ever project. So the three crates cannot
move together from the registry, and this copy unblocks the upgrade. The vendoring
choice was made explicitly in the issue's own comment (option 2), with its
consequence recorded there: djust now owns a DOM implementation upstream calls
unsupported.

## Exact source

| | |
|---|---|
| upstream repo | https://github.com/servo/html5ever |
| tag | `html5ever-v0.40.1` |
| resolved commit | `d7232d746d3112f08926d169591576bd12fe2fdf` |
| path taken | `rcdom/lib.rs` → `crates/markup5ever_rcdom_vendored/src/lib.rs` |
| fetched | 2026-10-08, `raw.githubusercontent.com` at the commit above (HTTP 200, 22124 bytes) |
| licence (upstream `rcdom/Cargo.toml`) | workspace licence, i.e. **MIT OR Apache-2.0** |
| licence files | `LICENSE-MIT` and `LICENSE-APACHE`, copied verbatim from the same commit |
| file header retained | yes — the upstream copyright header is the first line of `src/lib.rs` and is unmodified |

## Modifications from upstream

**None to the source.** `src/lib.rs` is byte-identical to `rcdom/lib.rs` at the
commit above. The only delta is packaging: this crate is placed under `crates/`, and
its `Cargo.toml` is adapted to this workspace (workspace-inherited dependency
versions, `src/lib.rs` layout, `publish = false`). Upstream's `[dev-dependencies]`,
benches and examples are not vendored — this is a library copy for djust's use, not
a maintained fork.

## Maintenance consequence, stated plainly

This is now ours to keep current. Upstream explicitly does not support the crate, so
there is no release to track and no upstream fix to inherit; when `markup5ever_rcdom`
does gain a 0.40+ release on crates.io, the right move is to delete this crate and
return to the registry dependency.
