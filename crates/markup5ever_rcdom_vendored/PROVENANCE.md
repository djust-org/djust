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

**One formatting-only change. This file previously claimed the source was
byte-identical to upstream; that was false and was corrected after review.**

`cargo fmt --all` (which this repository enforces) runs over every workspace member,
including this crate, and rustfmt removes the trailing commas after four match-block
arms — at lines 328, 469, 651 and 669 of the file as vendored. That is the **entire**
difference:

```
diff <(curl -sSL raw.githubusercontent.com/servo/html5ever/d7232d74.../rcdom/lib.rs) \
     crates/markup5ever_rcdom_vendored/src/lib.rs
  -> 328c328  },  ->  }
  -> 469c469  },  ->  }
  -> 651c651  },  ->  }
  -> 669c669  },  ->  }
```

Verified, not assumed: `rustfmt --edition 2021` applied to the upstream file produces
output that is **byte-identical** to the vendored file, so rustfmt alone accounts for
every byte of the delta and no other edit was made. Both licence files ARE
byte-identical to upstream.

The alternative — preserving upstream bytes exactly — was rejected because this
repository enforces rustfmt over the workspace, so the vendored file would fail the
format gate on every run. Formatting-only, no semantic change, and the upstream
copyright header is still the unmodified first line.

The rest is packaging: the crate lives under `crates/`, its `Cargo.toml` is adapted
to this workspace (workspace-inherited dependency versions, `src/lib.rs` layout,
`publish = false`), and upstream's `[dev-dependencies]`, benches and examples are not
vendored — this is a library copy for djust's use, not a maintained fork.

## Maintenance consequence, stated plainly

This is now ours to keep current. Upstream explicitly does not support the crate, so
there is no release to track and no upstream fix to inherit; when `markup5ever_rcdom`
does gain a 0.40+ release on crates.io, the right move is to delete this crate and
return to the registry dependency.
