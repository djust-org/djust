#!/usr/bin/env bash
#
# Rebrand Script: django_rust / django_rust_live → djust
#
# This script performs a comprehensive rebrand of the entire codebase.
# Run from project root: bash scripts/rebrand_to_djust.sh
#

set -e  # Exit on error

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "===================================="
echo "Djust Rebrand Script"
echo "===================================="
echo "Project root: $PROJECT_ROOT"
echo ""

# Safety check
read -p "This will modify 100+ files. Have you committed your changes? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborting. Please commit your changes first."
    exit 1
fi

echo ""
echo "Phase 1: Text replacements in source files..."
echo "------------------------------------"

# Function to replace text in files
replace_in_files() {
    local pattern=$1
    local replacement=$2
    local file_patterns=$3
    local description=$4

    echo "  → $description"
    find . -type f \( $file_patterns \) \
        -not -path "*/target/*" \
        -not -path "*/.git/*" \
        -not -path "*/\.venv/*" \
        -not -path "*/__pycache__/*" \
        -not -path "*/node_modules/*" \
        -not -path "*/scratch/*" \
        -exec sed -i.bak "s|$pattern|$replacement|g" {} +

    # Remove backup files
    find . -name "*.bak" -delete
}

# Python imports and module references
replace_in_files \
    "from django_rust_live" \
    "from djust" \
    "-name '*.py'" \
    "Python imports: from django_rust_live → from djust"

replace_in_files \
    "import django_rust_live" \
    "import djust" \
    "-name '*.py'" \
    "Python imports: import django_rust_live → import djust"

replace_in_files \
    "'django_rust_live'" \
    "'djust'" \
    "-name '*.py'" \
    "Python strings: 'django_rust_live' → 'djust'"

replace_in_files \
    "\"django_rust_live\"" \
    "\"djust\"" \
    "-name '*.py'" \
    "Python strings: \"django_rust_live\" → \"djust\""

# Rust use statements and crate references
replace_in_files \
    "use django_rust_core::" \
    "use djust_core::" \
    "-name '*.rs'" \
    "Rust: use django_rust_core:: → use djust_core::"

replace_in_files \
    "use django_rust_templates::" \
    "use djust_templates::" \
    "-name '*.rs'" \
    "Rust: use django_rust_templates:: → use djust_templates::"

replace_in_files \
    "use django_rust_vdom::" \
    "use djust_vdom::" \
    "-name '*.rs'" \
    "Rust: use django_rust_vdom:: → use djust_vdom::"

replace_in_files \
    "django_rust_core" \
    "djust_core" \
    "-name 'Cargo.toml'" \
    "Cargo.toml: django_rust_core → djust_core"

replace_in_files \
    "django_rust_templates" \
    "djust_templates" \
    "-name 'Cargo.toml'" \
    "Cargo.toml: django_rust_templates → djust_templates"

replace_in_files \
    "django_rust_vdom" \
    "djust_vdom" \
    "-name 'Cargo.toml'" \
    "Cargo.toml: django_rust_vdom → djust_vdom"

replace_in_files \
    "django_rust_live" \
    "djust_live" \
    "-name 'Cargo.toml'" \
    "Cargo.toml: django_rust_live → djust_live"

# Package name in pyproject.toml
replace_in_files \
    "django-rust-live" \
    "djust" \
    "-name 'pyproject.toml'" \
    "pyproject.toml: django-rust-live → djust"

replace_in_files \
    "django_rust_live\\._rust" \
    "djust._rust" \
    "-name 'pyproject.toml'" \
    "pyproject.toml: module name"

replace_in_files \
    "Django Rust Contributors" \
    "Djust Contributors" \
    "-name 'pyproject.toml'" \
    "pyproject.toml: contributors"

# URLs and domains (generic placeholder URLs)
replace_in_files \
    "https://github.com/django-rust/django-rust" \
    "https://github.com/johnrtipton/djust" \
    "-name '*.toml' -o -name '*.md' -o -name '*.py'" \
    "URLs: GitHub repository"

replace_in_files \
    "https://django-rust.readthedocs.io" \
    "https://djust.readthedocs.io" \
    "-name '*.toml' -o -name '*.md'" \
    "URLs: Documentation"

replace_in_files \
    "security@django-rust.org" \
    "security@djust.org" \
    "-name '*.md'" \
    "Email: security"

replace_in_files \
    "help@django-rust.org" \
    "help@djust.org" \
    "-name '*.md'" \
    "Email: help"

replace_in_files \
    "dev@django-rust.org" \
    "dev@djust.org" \
    "-name '*.md'" \
    "Email: dev"

# Kubernetes
replace_in_files \
    "django-rust-live" \
    "djust" \
    "-path '*/k8s/*.yaml' -o -path '*/k8s/*.sh' -o -name 'Makefile'" \
    "K8s: service/deployment names"

replace_in_files \
    "django-rust" \
    "djust" \
    "-path '*/k8s/*.yaml' -o -path '*/k8s/*.sh' -o -name 'Makefile'" \
    "K8s: namespace"

replace_in_files \
    "ghcr.io/johnrtipton/django-rust-live" \
    "ghcr.io/johnrtipton/djust" \
    "-path '*/k8s/*.yaml' -o -path '*/k8s/*.sh' -o -name 'Makefile' -o -name 'Dockerfile'" \
    "Container registry path"

# Documentation titles and headers
replace_in_files \
    "Django Rust Live" \
    "Djust" \
    "-name '*.md' -o -name '*.html'" \
    "Documentation: Django Rust Live → Djust"

replace_in_files \
    "Django-Rust" \
    "Djust" \
    "-name '*.md'" \
    "Documentation: Django-Rust → Djust"

# Template tags
replace_in_files \
    "{% load django_rust_live %}" \
    "{% load djust %}" \
    "-name '*.html'" \
    "Templates: template tag loads"

# .gitignore
replace_in_files \
    "django_rust\\.png" \
    "djust.png" \
    "-name '.gitignore'" \
    ".gitignore: logo file"

echo ""
echo "Phase 2: Renaming directories..."
echo "------------------------------------"

# Rename directories (deepest first to avoid path issues)
if [ -d "python/django_rust_live/static/django_rust_live" ]; then
    echo "  → python/django_rust_live/static/django_rust_live → python/django_rust_live/static/djust"
    mv python/django_rust_live/static/django_rust_live python/django_rust_live/static/djust
fi

if [ -d "python/django_rust_live" ]; then
    echo "  → python/django_rust_live → python/djust"
    mv python/django_rust_live python/djust
fi

if [ -d "crates/django_rust_core" ]; then
    echo "  → crates/django_rust_core → crates/djust_core"
    mv crates/django_rust_core crates/djust_core
fi

if [ -d "crates/django_rust_templates" ]; then
    echo "  → crates/django_rust_templates → crates/djust_templates"
    mv crates/django_rust_templates crates/djust_templates
fi

if [ -d "crates/django_rust_vdom" ]; then
    echo "  → crates/django_rust_vdom → crates/djust_vdom"
    mv crates/django_rust_vdom crates/djust_vdom
fi

if [ -d "crates/django_rust_live" ]; then
    echo "  → crates/django_rust_live → crates/djust_live"
    mv crates/django_rust_live crates/djust_live
fi

# Update paths in pyproject.toml and Cargo.toml after directory renames
sed -i.bak 's|crates/django_rust_live/Cargo.toml|crates/djust_live/Cargo.toml|g' pyproject.toml
sed -i.bak 's|crates/django_rust_core|crates/djust_core|g' Cargo.toml
sed -i.bak 's|crates/django_rust_templates|crates/djust_templates|g' Cargo.toml
sed -i.bak 's|crates/django_rust_vdom|crates/djust_vdom|g' Cargo.toml
sed -i.bak 's|crates/django_rust_live|crates/djust_live|g' Cargo.toml
find . -name "*.bak" -delete

echo ""
echo "Phase 3: Updating crate dependencies..."
echo "------------------------------------"

# Update Cargo.toml dependencies to use new crate names
for cargo_file in crates/*/Cargo.toml; do
    if [ -f "$cargo_file" ]; then
        echo "  → Updating dependencies in $cargo_file"
        sed -i.bak 's|path = "../django_rust_core"|path = "../djust_core"|g' "$cargo_file"
        sed -i.bak 's|path = "../django_rust_templates"|path = "../djust_templates"|g' "$cargo_file"
        sed -i.bak 's|path = "../django_rust_vdom"|path = "../djust_vdom"|g' "$cargo_file"
        find . -name "*.bak" -delete
    fi
done

echo ""
echo "===================================="
echo "Rebrand Complete!"
echo "===================================="
echo ""
echo "Summary:"
echo "  ✓ Text replacements in 100+ files"
echo "  ✓ 6 directories renamed"
echo "  ✓ Cargo.toml dependencies updated"
echo "  ✓ pyproject.toml configuration updated"
echo ""
echo "Next steps:"
echo "  1. Review changes: git status"
echo "  2. Rebuild Rust: cargo build"
echo "  3. Rebuild Python: maturin develop"
echo "  4. Run tests: pytest"
echo "  5. Commit changes: git add -A && git commit -m 'Rebrand to djust'"
echo ""
echo "Note: Root directory rename must be done manually:"
echo "  cd .."
echo "  mv django_rust djust"
echo ""
