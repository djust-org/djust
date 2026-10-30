#!/usr/bin/env bash
# Install a djust wheel built by another CI job into this checkout's venv,
# laid out exactly as `maturin develop` leaves it — so a job can skip the
# Rust build without changing what its tests import.
#
# Why not just `pip install` the wheel: the tests import djust from the
# CHECKOUT. pytest puts python/ on sys.path when it collects
# python/djust/tests/, and `pythonpath = ["."]` covers the rest, so a
# site-packages copy would be shadowed by a python/djust/ with no `_rust`
# module in it. `maturin develop` solves that with a `djust.pth` pointing at
# python/ plus the compiled `_rust*.so` dropped into python/djust/. This
# does the same from a prebuilt wheel:
#
#   1. install the wheel (--no-deps): dist-info, version metadata and the
#      `djust` / `djust-deploy` console scripts, as maturin develop writes;
#   2. swap the installed package copy for a `djust.pth` -> <checkout>/python;
#   3. drop the wheel's `_rust*.so` into python/djust/ (install-rust-ext.sh);
#   4. prove `djust` and `djust._rust` now import from the checkout — an
#      ABI mismatch (a cp312 wheel in a 3.13 venv) fails here, loudly, not as
#      thousands of collection errors later.
#
# Usage: scripts/ci-install-prebuilt-djust.sh <dir holding one wheel | wheel.whl> [python]
#   python defaults to .venv/bin/python in the current directory (the checkout).
set -euo pipefail

src="${1:?usage: $0 <wheel dir | wheel.whl> [python]}"
py="${2:-.venv/bin/python}"
checkout="$PWD"

if [ -d "$src" ]; then
  shopt -s nullglob
  wheels=("$src"/djust-*.whl)
  shopt -u nullglob
  if [ "${#wheels[@]}" -ne 1 ]; then
    echo "expected exactly one djust wheel in $src, found ${#wheels[@]}:" >&2
    ls -la "$src" >&2 || true
    exit 1
  fi
  wheel="${wheels[0]}"
else
  wheel="$src"
fi

uv pip install --python "$py" --no-deps --reinstall "$wheel"

site="$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
rm -rf "$site/djust"
printf '%s\n' "$checkout/python" > "$site/djust.pth"

"$(dirname "$0")/install-rust-ext.sh" "$wheel" "$checkout"

CHECKOUT="$checkout" "$py" - <<'EOF'
import os
import sys

import djust
import djust._rust

root = os.path.join(os.environ["CHECKOUT"], "python", "djust")
for mod in (djust, djust._rust):
    if not os.path.realpath(mod.__file__).startswith(os.path.realpath(root) + os.sep):
        sys.exit(f"{mod.__name__} imported from {mod.__file__}, not from {root}")
print(f"djust {djust.__version__} + {os.path.basename(djust._rust.__file__)} import from {root}")
EOF
