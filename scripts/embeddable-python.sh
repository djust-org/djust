#!/usr/bin/env bash
# Resolve a Python interpreter that is SAFE TO EMBED — i.e. safe to export as
# PYO3_PYTHON for a `cargo test` binary that statically embeds CPython via
# `Python::initialize` (closes #2072).
#
# WHY: the pre-push `cargo-test` hook used to export
# `PYO3_PYTHON="$(bash scripts/run-with-venv-python.sh --print)"` — the
# project venv's interpreter. On machines where the venv's BASE interpreter
# is uv's python-build-standalone (`~/.local/share/uv/python/cpython-*`),
# any PyO3 test binary that embeds CPython (e.g.
# crates/djust_templates/tests/test_block_custom_tag_arg_json_2042.rs) fails
# bootstrap DETERMINISTICALLY:
#   Fatal Python error: init_fs_encoding
#   ...
#   sys.prefix = '/install'
# python-build-standalone bakes a relocatable build whose install prefix
# ('/install') was fixed at build time; CPython's embedded bootstrap can't
# always re-derive the real prefix from the calling binary's location the
# way it can for a normal (non-standalone) build. This is a known
# python-build-standalone limitation, not a djust bug — the SAME test in the
# SAME profile passes when pyo3 is left to do its own interpreter discovery,
# or when PYO3_PYTHON points at a homebrew/framework build instead.
#
# RESOLUTION ORDER (first hit wins):
#   (a) the project venv's own interpreter (resolved the same way
#       scripts/run-with-venv-python.sh does), IF its `sys.base_prefix` is
#       NOT a python-build-standalone install.
#   (b) a framework/homebrew `python3.X` (X = the venv's major.minor —
#       pyo3 needs interpreter-version consistency with what built the
#       cached artifacts; a mismatched minor version forces a rebuild of
#       cached crates) found on PATH or at the well-known homebrew keg path.
#   (c) fall back to the venv's own interpreter anyway, with a LOUD warning
#       on stderr that embedded tests may fail with the init_fs_encoding
#       class of error (#2072). This keeps the script always producing SOME
#       interpreter rather than hard-failing a caller that doesn't check.
#
# Usage:
#   PYO3_PYTHON="$(bash scripts/embeddable-python.sh)" cargo test ...
# Diagnostics (which branch was taken, and why) always go to STDERR; stdout
# is exactly one line — the resolved interpreter's absolute path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_note() {
    echo "embeddable-python.sh: $*" >&2
}

# Detect a python-build-standalone install via its `sys.base_prefix`. uv
# delivers these under `$UV_PYTHON_INSTALL_DIR` (default
# `~/.local/share/uv/python/`) — checked first if the env var is set, then
# the default path pattern `*/uv/python/*` as a portable fallback. This is
# the SINGLE detection used both here and by scripts/doctor.sh's
# embedded-pyo3 check (shelling out to this script) — do not duplicate the
# heuristic elsewhere (#1646 parallel-path-drift).
_is_uv_standalone_base() {
    local base_prefix="$1"
    [ -z "$base_prefix" ] && return 1
    if [ -n "${UV_PYTHON_INSTALL_DIR:-}" ]; then
        case "$base_prefix" in
            "$UV_PYTHON_INSTALL_DIR"*)
                return 0
                ;;
        esac
    fi
    case "$base_prefix" in
        */uv/python/*)
            return 0
            ;;
    esac
    return 1
}

_base_prefix_of() {
    local py="$1"
    "$py" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || true
}

_major_minor_of() {
    local py="$1"
    "$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# (a) the venv's own interpreter, if embeddable.
# ---------------------------------------------------------------------------
venv_python=""
if [ -x "$SCRIPT_DIR/run-with-venv-python.sh" ]; then
    venv_python="$(bash "$SCRIPT_DIR/run-with-venv-python.sh" --print 2>/dev/null || true)"
fi

venv_base_prefix=""
venv_major_minor=""
if [ -n "$venv_python" ] && [ -x "$venv_python" ]; then
    venv_base_prefix="$(_base_prefix_of "$venv_python")"
    venv_major_minor="$(_major_minor_of "$venv_python")"

    if ! _is_uv_standalone_base "$venv_base_prefix"; then
        printf '%s\n' "$venv_python"
        exit 0
    fi
    _note "venv interpreter $venv_python has a python-build-standalone (uv-managed) base_prefix ($venv_base_prefix) — NOT safe for PYO3_PYTHON embedding (#2072: init_fs_encoding / sys.prefix='/install'); looking for a framework/homebrew python$venv_major_minor instead"
fi

# ---------------------------------------------------------------------------
# (b) a framework/homebrew python3.X on PATH or at the well-known homebrew
# keg path, matching the venv's major.minor when known.
# ---------------------------------------------------------------------------
candidates=()
if [ -n "$venv_major_minor" ]; then
    candidates+=(
        "/opt/homebrew/opt/python@$venv_major_minor/bin/python$venv_major_minor"
        "/usr/local/opt/python@$venv_major_minor/bin/python$venv_major_minor"
    )
    if command -v "python$venv_major_minor" >/dev/null 2>&1; then
        candidates+=("$(command -v "python$venv_major_minor")")
    fi
fi
# Unversioned fallbacks — used when there's no venv at all, or as a last
# resort if no matching-version candidate above panned out.
candidates+=("/opt/homebrew/bin/python3" "/usr/local/bin/python3")
if command -v python3 >/dev/null 2>&1; then
    candidates+=("$(command -v python3)")
fi

for c in "${candidates[@]}"; do
    [ -x "$c" ] || continue
    c_base_prefix="$(_base_prefix_of "$c")"
    if _is_uv_standalone_base "$c_base_prefix"; then
        continue
    fi
    c_major_minor="$(_major_minor_of "$c")"
    if [ -n "$venv_major_minor" ] && [ -n "$c_major_minor" ] && [ "$c_major_minor" != "$venv_major_minor" ]; then
        _note "candidate $c is Python $c_major_minor but the venv is $venv_major_minor — a mismatched minor version forces pyo3 to rebuild cached artifacts; still usable, but install python@$venv_major_minor for a cache-friendly match"
    fi
    _note "resolved embeddable interpreter: $c (base_prefix=$c_base_prefix)"
    printf '%s\n' "$c"
    exit 0
done

# ---------------------------------------------------------------------------
# (c) fall back to the venv's own interpreter anyway, loudly.
# ---------------------------------------------------------------------------
if [ -n "$venv_python" ] && [ -x "$venv_python" ]; then
    _note "*** WARNING: no non-uv-standalone Python interpreter found on this machine; falling back to $venv_python for PYO3_PYTHON. Embedded-PyO3 test binaries MAY FAIL with 'Fatal Python error: init_fs_encoding' / sys.prefix='/install' (#2072). Install a matching homebrew/framework python to fix, e.g.: brew install python@${venv_major_minor:-3.12} ***"
    printf '%s\n' "$venv_python"
    exit 0
fi

echo "embeddable-python.sh: ERROR: no Python interpreter found for PYO3_PYTHON (looked at the project venv, homebrew/framework python3.X, and python3 on PATH)." >&2
exit 1
