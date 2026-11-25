#!/bin/bash
# Concatenate source modules into built JS files, then emit minified +
# pre-compressed siblings for production serving.
#
# Output files (under python/djust/static/djust/):
#   client.js       — FULL bundle: readable concat of every source module;
#                     auditable, used as source-map target
#   client.min.js   — the full bundle, terser-minified
#   client-core.js / client-core.min.js — CORE bundle (#4007): every module
#                     except the chunks; the default the LiveView injects
#                     (readable in DEBUG, minified in production)
#   chunks/<name>.js / .min.js — optional feature modules the core loads when
#                     the page uses them (see CHUNK_SPECS below)
#   client.min.js.gz  — gzip-precompressed sibling (whitenoise picks automatically)
#   client.min.js.br  — brotli-precompressed sibling (when `brotli` command available)
#   client.min.js.map — terser source map (dev aid; DevTools only). NOT
#                     referenced from client.min.js: the .map is gitignored
#                     (absolute checkout paths in `sources`, see below) and
#                     so never in the wheel, while a `sourceMappingURL`
#                     comment pointing at it made Django's
#                     ManifestStaticFilesStorage / whitenoise fail
#                     `collectstatic` with MissingFileError (#2662). Load the
#                     map manually in DevTools ("Add source map…") when needed.
#
# debug-panel.js is concatenated only: it is served solely under DEBUG, and
# unminified. Minification is skipped gracefully if terser isn't installed,
# so contributors can still iterate on the raw source modules without an npm
# install.
#
# #2054: client.js / client.min.js (and debug-panel.js) are
# committed to git — terser is npm-pinned via package-lock.json, so their
# output is byte-identical across every contributor's machine. The .gz / .br
# / .map siblings are gitignored, build-time-only artifacts: they are NOT
# committed. gzip/brotli are unpinned system CLI tools, so their compressed
# output can differ byte-for-byte across contributor toolchains even when the
# input (.min.js) is unchanged — every regeneration by any contributor showed
# up as diff noise on every JS-touching PR. .map files have a second, worse
# problem: terser's `sources` field embeds the absolute filesystem path of
# the checkout, which is guaranteed to differ across every clone/worktree.
# Downstream apps that serve static files via WhiteNoise's
# `CompressedManifestStaticFilesStorage` (djust's own documented pattern —
# see djust.org/marketing_project/settings.py) regenerate their own .gz/.br
# at `collectstatic` time regardless, so nothing at runtime relies on these
# files being pre-built or committed. Still generated here (uncommitted) so
# local dev / `make start` / whitenoise-in-dev-mode has them on disk.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/python/djust/static/djust/src"
STATIC_DIR="$PROJECT_DIR/python/djust/static/djust"

# --- minification helper ---------------------------------------------------
#
# Runs terser on the concatenated output if the binary is available.
# Produces <input>.min.js alongside the raw file. Also emits .gz and .br
# pre-compressed siblings if gzip / brotli are available.
minify_and_compress() {
    local input="$1"
    local output_min="${input%.js}.min.js"

    local terser_bin=""
    if [ -x "$PROJECT_DIR/node_modules/.bin/terser" ]; then
        terser_bin="$PROJECT_DIR/node_modules/.bin/terser"
    elif command -v terser >/dev/null 2>&1; then
        terser_bin=$(command -v terser)
    fi

    if [ -z "$terser_bin" ]; then
        echo "  terser not found — skipping minification (install via 'npm install')"
        return 0
    fi

    # --keep-fnames (#1676): after the #1635 single-IIFE wrap, terser's
    # name-mangling renamed cross-module function declarations (e.g.
    # applyPatches -> ie) inconsistently across the IIFE scope, so a call
    # site / export referencing the mangled name threw
    # `ReferenceError: <name> is not defined` in the MINIFIED bundle only
    # (the unminified client.js resolves via the shared outer-IIFE scope).
    # Preserving function names keeps every cross-module reference resolvable.
    # Costs ~2 KB gzipped; correctness > size for a prod-breaking class of bug.
    #
    # --source-map WITHOUT `url=` (#2662): still writes <output>.map next to
    # the bundle for local DevTools use, but appends NO `//# sourceMappingURL`
    # comment. The map is gitignored and therefore absent from the wheel; a
    # reference to it broke `collectstatic` under ManifestStaticFilesStorage.
    # tests/unit/test_client_minified.py pins "no reference to an unshipped
    # file" so this cannot silently regress.
    "$terser_bin" "$input" \
        --compress \
        --mangle \
        --keep-fnames \
        --comments=false \
        --source-map "includeSources,root='./'" \
        --output "$output_min" 2>/dev/null || {
        echo "  terser failed on $(basename "$input"); leaving raw file"
        return 0
    }
    echo "  minified $(basename "$input") → $(basename "$output_min") ($(wc -c < "$output_min" | tr -d ' ') bytes)"

    # Pre-compressed siblings for whitenoise / nginx to serve without
    # runtime compression cost. -k keeps the original; -f overwrites a
    # stale sibling from a previous build.
    if command -v gzip >/dev/null 2>&1; then
        # -n: do NOT embed original name + mtime in the gzip header.
        # Without this, each build produces different bytes even when
        # the input is unchanged, which trips the pre-commit build-js
        # hook into an infinite "files were modified by this hook"
        # loop on re-commits.
        gzip -n -k -f -9 "$output_min"
        echo "  gzipped → $(basename "$output_min").gz ($(wc -c < "$output_min.gz" | tr -d ' ') bytes)"
    fi
    if command -v brotli >/dev/null 2>&1; then
        brotli -k -f -q 11 "$output_min"
        echo "  brotlied → $(basename "$output_min").br ($(wc -c < "$output_min.br" | tr -d ' ') bytes)"
    fi
}

# Build client.js
#
# The concatenated modules are wrapped in a single IIFE so EVERY top-level
# `const`/`let`/`class` is function-scoped, not bound in the shared global
# lexical environment. Without the wrapper, re-executing the bundle in the
# same document — morphdom re-attaching the <script> after live_redirect, or
# a bfcache `pageshow` re-init — re-declared those identifiers at PARSE time
# and threw `Identifier '_TEXT_INPUT_TYPES' has already been declared` before
# the runtime `window._djustClientLoaded` guard could run (#1635). Inside the
# IIFE a second execution just builds a fresh function scope and the runtime
# guard short-circuits the re-init. Modules export their public API via
# explicit `window.*` / `globalThis.djust.*` assignments, which still run
# inside the IIFE, so nothing observable changes.
# --- client chunks (#4007) ----------------------------------------------
#
# The full bundle (client.js / client.min.js) holds every module; it is what a
# hand-written <script src="djust/client.js"> loads and what
# LIVEVIEW_CONFIG['client_bundle'] = 'full' injects. The CORE bundle
# (client-core.js / client-core.min.js) is the default the LiveView
# post-processing injects: every module EXCEPT those listed here, which are
# built as chunks/<name>.js and loaded by src/13c-chunk-loader.js only when the
# page uses them.
#
# Spec: "<chunk name>|<src modules>|<core bindings the chunk reads>".
# A chunk runs outside the core IIFE, so every core-scope name it uses must be
# listed in the third field (and exposed by window.djust._chunkScope in
# 13c-chunk-loader.js). The build rewrites each such identifier to a read
# through that object; tests/js/client-chunks-4007.test.js fails the build's
# output if a chunk is left with any other free identifier. The source
# modules themselves are not edited.
CHUNK_SPECS=(
    "uploads|15-uploads.js|liveViewWS isWSConnected slotIdFor slotFrameFields"
    "virtual-list|29-virtual-list.js|"
    "cursor-overlay|16-cursor-overlay.js|"
    "tutorial|28-tutorial-bubble.js|"
    "service-worker|33-sw-registration.js|"
)
CHUNK_DIR="$STATIC_DIR/chunks"

is_chunk_module() {
    local mod="$1" spec mods
    for spec in "${CHUNK_SPECS[@]}"; do
        mods=$(echo "$spec" | cut -d'|' -f2)
        for m in $mods; do
            [ "$m" = "$mod" ] && return 0
        done
    done
    return 1
}

# Guardrail (#953): tests/js/_helpers.js is a TEST-ONLY shim. If any of its
# signatures ever leak into a production bundle, terser still happily ships
# the code — but it bloats the client + exposes the `./_helpers.js` import
# path in the minified map. Fail the build loudly if that happens.
assert_no_test_helpers() {
    if grep -q "Shared JSDOM test helpers" "$1" \
       || grep -q "from '\./_helpers\.js'" "$1"; then
        echo "ERROR: tests/js/_helpers.js content found in $(basename "$1")" >&2
        echo "       (a source module under src/ is importing test-only helpers)" >&2
        exit 1
    fi
}

# Build client.js (full) and client-core.js (core)
#
# The concatenated modules are wrapped in a single IIFE so EVERY top-level
# `const`/`let`/`class` is function-scoped, not bound in the shared global
# lexical environment. Without the wrapper, re-executing the bundle in the
# same document — morphdom re-attaching the <script> after live_redirect, or
# a bfcache `pageshow` re-init — re-declared those identifiers at PARSE time
# and threw `Identifier '_TEXT_INPUT_TYPES' has already been declared` before
# the runtime `window._djustClientLoaded` guard could run (#1635). Inside the
# IIFE a second execution just builds a fresh function scope and the runtime
# guard short-circuits the re-init. Modules export their public API via
# explicit `window.*` / `globalThis.djust.*` assignments, which still run
# inside the IIFE, so nothing observable changes.
#
# Each bundle ends by naming itself on window.djust._clientBundle (first
# bundle on the page wins), which 13c-chunk-loader.js reads at init.
CLIENT_OUT="$STATIC_DIR/client.js"
CORE_OUT="$STATIC_DIR/client-core.js"
if ls "$SRC_DIR"/[0-9]*.js 1>/dev/null 2>&1; then
    {
        printf ';(function () {\n'
        cat "$SRC_DIR"/[0-9]*.js
        printf "\nwindow.djust._clientBundle = window.djust._clientBundle || 'full';\n"
        printf '})();\n'
    } > "$CLIENT_OUT"
    echo "Built client.js from $(ls "$SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules ($(wc -c < "$CLIENT_OUT" | tr -d ' ') bytes, IIFE-wrapped)"
    assert_no_test_helpers "$CLIENT_OUT"
    minify_and_compress "$CLIENT_OUT"

    core_count=0
    {
        printf ';(function () {\n'
        for f in "$SRC_DIR"/[0-9]*.js; do
            if ! is_chunk_module "$(basename "$f")"; then
                cat "$f"
                core_count=$((core_count + 1))
            fi
        done
        printf "\nwindow.djust._clientBundle = window.djust._clientBundle || 'core';\n"
        # The private bridge chunks read core bindings through (13c-chunk-loader.js).
        printf "if (window.djust._clientBundle === 'core') _djustExposeChunkScope();\n"
        printf '})();\n'
    } > "$CORE_OUT"
    echo "Built client-core.js ($(wc -c < "$CORE_OUT" | tr -d ' ') bytes, IIFE-wrapped)"
    assert_no_test_helpers "$CORE_OUT"
    minify_and_compress "$CORE_OUT"

    rm -f "$CHUNK_DIR"/*.js "$CHUNK_DIR"/*.gz "$CHUNK_DIR"/*.br "$CHUNK_DIR"/*.map 2>/dev/null || true
    mkdir -p "$CHUNK_DIR"
    for spec in "${CHUNK_SPECS[@]}"; do
        name=$(echo "$spec" | cut -d'|' -f1)
        mods=$(echo "$spec" | cut -d'|' -f2)
        bridged=$(echo "$spec" | cut -d'|' -f3)
        out="$CHUNK_DIR/$name.js"
        body=$(for m in $mods; do
            if [ ! -f "$SRC_DIR/$m" ]; then
                echo "ERROR: chunk $name names missing module $m" >&2
                exit 1
            fi
            cat "$SRC_DIR/$m"
        done) || exit 1
        if [ -n "$bridged" ]; then
            # Identifier boundaries only: never a property (`.name`), a longer
            # identifier, or an object key (`name:`).
            alt=$(echo "$bridged" | tr ' ' '|')
            body=$(printf '%s\n' "$body" | DJ_ALT="$alt" perl -pe 's/(?<![\w\$.])($ENV{DJ_ALT})(?![\w\$])(?!\s*:(?!:))/__djScope.$1/g')
        fi
        {
            printf '// djust client chunk "%s" (#4007): GENERATED by scripts/build-client.sh from %s.\n' "$name" "$mods"
            printf '// Loaded by the core bundle (client-core.js) when the page needs it.\n'
            printf ';(function () {\n'
            printf "if (!window.djust || typeof window.djust._chunkShouldRun !== 'function' || !window.djust._chunkShouldRun('%s')) return;\n" "$name"
            if [ -n "$bridged" ]; then
                printf 'const __djScope = window.djust._chunkScope;\n'
            fi
            printf '%s\n' "$body"
            printf "window.djust._chunkReady('%s');\n" "$name"
            printf '})();\n'
        } > "$out"
        assert_no_test_helpers "$out"
        echo "Built chunks/$name.js ($(wc -c < "$out" | tr -d ' ') bytes)"
        minify_and_compress "$out"
    done
fi

# Build debug-panel.js
DEBUG_SRC_DIR="$SRC_DIR/debug"
DEBUG_OUT="$STATIC_DIR/debug-panel.js"
if [ -d "$DEBUG_SRC_DIR" ]; then
    cat "$DEBUG_SRC_DIR"/[0-9]*.js > "$DEBUG_OUT"
    echo "Built debug-panel.js from $(ls "$DEBUG_SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules ($(wc -c < "$DEBUG_OUT" | tr -d ' ') bytes)"
    # Not minified: the panel is injected only under DEBUG, and always as the
    # readable debug-panel.js (mixins/post_processing.py), so a .min.js would
    # be shipped in every wheel and never served.
fi

# ---------------------------------------------------------------------------
# Size manifest (#2138)
#
# Three places in prose quoted a client size, describing TWO different
# artifacts, and only the README pair was checked — so the unchecked pair
# (CLAUDE.md) drifted more than 2x before anyone noticed, and the README pair
# drifted across three PRs that each added a few hundred bytes.
#
# This is the single measured source. It is COMMITTED, deliberately: the
# gzipped bundles are gitignored (.gitignore:70), so without a checked-in
# manifest the doc check measures a locally-built artifact and warns-and-skips
# on a fresh clone — meaning CI and a contributor could disagree about whether
# a claim is in band.
# ---------------------------------------------------------------------------
write_size_manifest() {
    local manifest="$STATIC_DIR/client-sizes.json"
    local min_gz min_raw raw gz modules

    # NEVER write a number we could not measure. The first version emitted 0
    # for a missing .gz and called `gzip` unguarded, so running this script
    # WITHOUT terser (which it explicitly supports — see the skip message
    # above) or without gzip produced a valid JSON manifest full of zeros.
    # The doc check then read it, computed a band of [-3, +3] KB, and rejected
    # every correct claim in README and CLAUDE.md — a strictly worse
    # reproduction of the #2133 wall this manifest exists to remove, reachable
    # by running `make build-js`.
    if [ ! -f "$STATIC_DIR/client.min.js.gz" ]; then
        echo "  sizes → SKIPPED (no client.min.js.gz; needs terser + gzip)" >&2
        echo "          keeping the existing manifest rather than zeroing it" >&2
        return 0
    fi
    if ! command -v gzip >/dev/null 2>&1; then
        echo "  sizes → SKIPPED (gzip not available)" >&2
        return 0
    fi

    min_raw=$(wc -c < "$STATIC_DIR/client.min.js" | tr -d ' ')
    raw=$(wc -c < "$STATIC_DIR/client.js" | tr -d ' ')
    modules=$(ls "$SRC_DIR"/[0-9]*.js 2>/dev/null | wc -l | tr -d ' ')
    min_gz=$(wc -c < "$STATIC_DIR/client.min.js.gz" | tr -d ' ')
    gz=$(gzip -n -9 -c "$STATIC_DIR/client.js" | wc -c | tr -d ' ')

    # Belt and braces: a zero here means a measurement silently failed, and a
    # zeroed manifest is worse than none because the checker trusts it.
    for v in "$min_raw" "$raw" "$modules" "$min_gz" "$gz"; do
        if [ -z "$v" ] || [ "$v" -eq 0 ] 2>/dev/null; then
            echo "ERROR: refusing to write a size manifest with a zero measurement" >&2
            echo "       (min_raw=$min_raw raw=$raw modules=$modules min_gz=$min_gz gz=$gz)" >&2
            return 1
        fi
    done

    if [ ! -f "$STATIC_DIR/client-core.min.js.gz" ]; then
        echo "ERROR: client-core.min.js.gz missing although client.min.js.gz was built" >&2
        return 1
    fi
    local core_gz core_raw chunks_json chunk_gz_total
    core_gz=$(wc -c < "$STATIC_DIR/client-core.min.js.gz" | tr -d ' ')
    core_raw=$(wc -c < "$STATIC_DIR/client-core.min.js" | tr -d ' ')
    chunks_json=""
    chunk_gz_total=0
    for spec in "${CHUNK_SPECS[@]}"; do
        local cname cgz cmods
        cname=$(echo "$spec" | cut -d'|' -f1)
        cmods=$(echo "$spec" | cut -d'|' -f2)
        if [ ! -f "$CHUNK_DIR/$cname.min.js.gz" ]; then
            echo "ERROR: chunks/$cname.min.js.gz missing" >&2
            return 1
        fi
        cgz=$(wc -c < "$CHUNK_DIR/$cname.min.js.gz" | tr -d ' ')
        chunk_gz_total=$((chunk_gz_total + cgz))
        [ -n "$chunks_json" ] && chunks_json="$chunks_json,"
        chunks_json="$chunks_json
    \"$cname\": { \"artifact\": \"chunks/$cname.min.js.gz\", \"modules\": \"$cmods\", \"bytes\": $cgz, \"kb\": $(python3 -c "print(round($cgz/1024, 1))") }"
    done

    cat > "$manifest" <<JSON
{
  "_comment": "GENERATED by scripts/build-client.sh — do not edit. The single measured source for every client-size claim in prose (#2138). Committed because the .gz artifacts are gitignored, so a fresh clone would otherwise have nothing to check against.",
  "shipped": {
    "artifact": "client.min.js.gz",
    "bytes": $min_gz,
    "kb": $(python3 -c "print(round($min_gz/1024, 1))"),
    "note": "The FULL bundle: every module, no chunk loading. What client.min.js serves and what LIVEVIEW_CONFIG['client_bundle'] = 'full' injects. A claim about it may carry <!-- size-claim: full --> (the default marker)."
  },
  "core": {
    "artifact": "client-core.min.js.gz",
    "bytes": $core_gz,
    "kb": $(python3 -c "print(round($core_gz/1024, 1))"),
    "minified_raw_bytes": $core_raw,
    "note": "The default injected bundle (#4007): every module except the chunks below. A page downloads this plus only the chunks it uses. Claims about it need <!-- size-claim: core --> on the line."
  },
  "chunks": {$chunks_json
  },
  "chunks_total_kb": $(python3 -c "print(round($chunk_gz_total/1024, 1))"),
  "minified_raw": { "artifact": "client.min.js", "bytes": $min_raw },
  "unminified": {
    "artifact": "client.js",
    "bytes": $raw,
    "gz_bytes": $gz,
    "gz_kb": $(python3 -c "print(round($gz/1024, 1))"),
    "modules": $modules,
    "note": "A build INPUT, not a deliverable. Claims about it need <!-- size-claim: unminified --> on the line."
  }
}
JSON
    echo "  sizes → client-sizes.json (full $(python3 -c "print(round($min_gz/1024, 1))") KB gz, core $(python3 -c "print(round($core_gz/1024, 1))") KB gz)"
}

if [ -f "$STATIC_DIR/client.min.js" ]; then
    write_size_manifest
fi
