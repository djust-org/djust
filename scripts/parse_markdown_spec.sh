#!/bin/bash
#
# Parse Markdown Feature Specification
#
# Extracts phases from markdown documentation and converts to format
# that auto_feature_dev.sh can understand.
#

set -e

show_usage() {
    cat << EOF
Parse Markdown Feature Specification

Usage: $0 <markdown-file>

Extracts phases from markdown headers and creates a format usable
by auto_feature_dev.sh.

Expected Markdown Format:
  ## Phase 1: Title
  **Duration**: X days
  **Dependencies**: Phase N, Phase M

  ### Goals
  ...

  ### Deliverables
  - [ ] Item 1
  - [ ] Item 2

  ### Acceptance Criteria
  - [x] Criterion 1
  - [x] Criterion 2

Example:
  $0 docs/templates/ORM_JIT_IMPLEMENTATION.md

Output:
  Prints phase information that can be passed to automation script.

EOF
}

if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    show_usage
    exit 0
fi

if [ -z "$1" ]; then
    echo "Error: Markdown file required"
    show_usage
    exit 1
fi

MARKDOWN_FILE="$1"

if [ ! -f "$MARKDOWN_FILE" ]; then
    echo "Error: File not found: $MARKDOWN_FILE"
    exit 1
fi

# Extract phase information
# This is a simple parser - for production, use Python or a proper parser

echo "Extracting phases from: $MARKDOWN_FILE"
echo ""

# Find all phase headers
grep -E "^##\s+Phase\s+[0-9]+" "$MARKDOWN_FILE" | while read -r line; do
    echo "Found: $line"
done

echo ""
echo "To use with automation, the markdown file will be passed directly as context."
echo "No YAML conversion needed - Claude reads the markdown specification!"
