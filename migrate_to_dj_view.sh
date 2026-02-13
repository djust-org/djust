#!/bin/bash
# Migrate from data-djust-* to dj-* naming convention

set -e

echo "🔄 Migrating djust to new naming convention (dj-view, dj-root)"
echo "================================================================"
echo ""

# Backup important files first
echo "📦 Creating backups..."
find . -name "*.py" -o -name "*.html" -o -name "*.md" -o -name "*.js" | \
  grep -v ".git\|node_modules\|.venv\|backup" | \
  while read file; do
    if grep -q "data-djust-view\|data-djust-root" "$file" 2>/dev/null; then
      cp "$file" "$file.backup-naming" 2>/dev/null || true
    fi
  done

echo "✅ Backups created"
echo ""

# Function to replace in files
replace_in_files() {
  local pattern=$1
  local replacement=$2
  local file_types=$3

  echo "Replacing '$pattern' with '$replacement' in $file_types..."

  find . \( -name "*.py" -o -name "*.html" -o -name "*.md" -o -name "*.js" \) \
    -not -path "./.git/*" \
    -not -path "./node_modules/*" \
    -not -path "./.venv/*" \
    -not -name "*.backup*" \
    -exec sed -i '' "s/$pattern/$replacement/g" {} \;
}

# Replace attributes
echo "🔧 Replacing attributes..."
replace_in_files "data-djust-view" "dj-view" "all"
replace_in_files "data-djust-root" "dj-root" "all"
replace_in_files "data-djust-lazy" "dj-lazy" "all"

# Also update Python code references (in strings, docs, etc.)
echo ""
echo "🔧 Updating Python attribute names in code..."
replace_in_files "data-liveview-root" "dj-liveview-root" "Python/JS"

# Update dataset references in JS
echo ""
echo "🔧 Updating JavaScript dataset references..."
find . -name "*.js" \
  -not -path "./.git/*" \
  -not -path "./node_modules/*" \
  -not -name "*.backup*" \
  -exec sed -i '' 's/dataset\.djustView/dataset.djView/g' {} \;

echo ""
echo "✅ Migration complete!"
echo ""
echo "Summary:"
echo "  - data-djust-view → dj-view"
echo "  - data-djust-root → dj-root"
echo "  - data-djust-lazy → dj-lazy"
echo ""
echo "Next steps:"
echo "  1. Run tests: make test"
echo "  2. Review changes: git diff"
echo "  3. Remove backups: find . -name '*.backup-naming' -delete"
echo ""
