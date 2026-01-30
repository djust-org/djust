#!/bin/bash
# Concatenate source modules into built JS files
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/python/djust/static/djust/src"

# Build client.js
CLIENT_OUT="$PROJECT_DIR/python/djust/static/djust/client.js"
if ls "$SRC_DIR"/[0-9]*.js 1>/dev/null 2>&1; then
    cat "$SRC_DIR"/[0-9]*.js > "$CLIENT_OUT"
    echo "Built client.js from $(ls "$SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules"
fi

# Build debug-panel.js
DEBUG_SRC_DIR="$SRC_DIR/debug"
DEBUG_OUT="$PROJECT_DIR/python/djust/static/djust/debug-panel.js"
if [ -d "$DEBUG_SRC_DIR" ]; then
    cat "$DEBUG_SRC_DIR"/[0-9]*.js > "$DEBUG_OUT"
    echo "Built debug-panel.js from $(ls "$DEBUG_SRC_DIR"/[0-9]*.js | wc -l | tr -d ' ') modules"
fi
