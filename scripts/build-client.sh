#!/bin/bash
# Concatenate source modules into built JS files, then emit minified +
# pre-compressed siblings for production serving.
#
# Output files (under python/djust/static/djust/):
#   client.js       — readable concat of 35+ source modules; auditable,
#                     served in DEBUG mode, used as source-map target
#   client.min.js   — terser-minified; default served in production
#   client.min.js.gz  — gzip-precompressed sibling (whitenoise picks automatically)
#   client.min.js.br  — brotli-precompressed sibling (when `brotli` command available)
#
# Same layout for debug-panel.js. Minification is skipped gracefully if
# terser isn't installed, so contributors can still iterate on the raw
# source modules without an npm install.
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

    "$terser_bin" "$input" \
        --compress \
        --mangle \
        --comments=false \
        --source-map "url='$(basename "$output_min").map',includeSources,root='./'" \
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
CLIENT_OUT="$STATIC_DIR/client.js"
if ls "$SRC_DIR"/[0-9]*.js 1>/dev/null 2>&1; then
    cat "$SRC_DIR"/[0-9]*.js > "$CLIENT_OUT"
    echo "Built client.js from $(ls "$SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules ($(wc -c < "$CLIENT_OUT" | tr -d ' ') bytes)"
    minify_and_compress "$CLIENT_OUT"
fi

# Build debug-panel.js
DEBUG_SRC_DIR="$SRC_DIR/debug"
DEBUG_OUT="$STATIC_DIR/debug-panel.js"
if [ -d "$DEBUG_SRC_DIR" ]; then
    cat "$DEBUG_SRC_DIR"/[0-9]*.js > "$DEBUG_OUT"
    echo "Built debug-panel.js from $(ls "$DEBUG_SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules ($(wc -c < "$DEBUG_OUT" | tr -d ' ') bytes)"
    minify_and_compress "$DEBUG_OUT"
fi
