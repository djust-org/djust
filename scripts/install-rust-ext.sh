#!/usr/bin/env bash
# Install a built djust Rust extension (.so) into a checkout WITHOUT
# overwriting a file that running processes may have mapped.
#
# Why this exists (2026-09-18, macOS 27): `cp new.so python/djust/_rust*.so`
# rewrites the EXISTING inode. When another process — a dev server, a
# background daemon — has that inode mapped, the kernel's cached code
# signature for the vnode no longer matches the new pages and the next
# `import djust._rust` is SIGKILLed (exit 137, no dyld message, nothing in
# `log show`). Re-signing the copy (`codesign -s - -f`) makes it load again,
# and replacing through a NEW inode (`mv`) never fights a mapped file:
# every mapped process keeps the old inode, exactly as cargo/maturin's own
# rename-into-place does. This script does both.
#
# Usage:
#   scripts/install-rust-ext.sh <built.so> [<dest checkout dir>]
#   scripts/install-rust-ext.sh path/to/wheel.whl [<dest checkout dir>]
# The dest defaults to the repo containing this script. A wheel is unzipped
# for its `djust/_rust*.so` first (the `maturin build -i <venv python>`
# output — `maturin develop` needs a venv and repoints the editable install,
# so from a worktree build a wheel and install it with this script).
set -euo pipefail

src="${1:?usage: $0 <built.so | wheel.whl> [dest checkout dir]}"
dest_root="${2:-$(cd "$(dirname "$0")/.." && pwd)}"
dest_dir="$dest_root/python/djust"

tmp_dir=""
cleanup() {
  if [ -n "$tmp_dir" ]; then rm -rf "$tmp_dir"; fi
}
trap cleanup EXIT

case "$src" in
  *.whl)
    tmp_dir="$(mktemp -d)"
    unzip -o -q "$src" 'djust/_rust*.so' -d "$tmp_dir"
    src="$(find "$tmp_dir/djust" -name '_rust*.so' -print -quit)"
    [ -n "$src" ] || { echo "no djust/_rust*.so inside $1" >&2; exit 1; }
    ;;
esac

name="$(basename "$src")"
target="$dest_dir/$name"
staged="$dest_dir/.$name.installing.$$"

cp "$src" "$staged"
# A fresh ad-hoc signature on the COPY, before it is ever mapped.
codesign -s - -f "$staged" 2>/dev/null
# New inode into place: processes mapping the old file keep the old inode.
mv -f "$staged" "$target"
rm -f "$dest_dir"/.__rust*.installing.* 2>/dev/null || true

echo "installed $name -> $target (inode $(stat -f %i "$target"))"
