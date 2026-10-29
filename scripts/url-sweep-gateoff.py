#!/usr/bin/env python3
"""Revert each #3665 fix, and break the sweep; each must turn a test red.

A sweep that survives the reversion of the fix it exists to guard is a decoration.
Each mutation below undoes ONE fix (or one guarantee of the sweep); the test file
named beside it is run against the mutated tree and must FAIL. A mutation that
leaves it green is reported and fails this script.

Same machinery and rules as ``template-library-parity-gateoff.py`` (which it
imports): the mutations are applied to a THROWAWAY COPY of the tree, never to this
checkout; the mutation text must be found and must change the source; HUNG and
ERROR are reported as themselves and are not a pass; the copy starts without
``__pycache__`` and runs with ``PYTHONDONTWRITEBYTECODE`` so a stale ``.pyc`` cannot
certify a mutation that never ran; pytest's own summary decides (a collection error
is ``ERROR``, not ``RED``).

Run: ``python scripts/url-sweep-gateoff.py [--list] [--only TEXT] [--no-control]``.
The fast mutations take seconds; the ones aimed at the sweep (marked ``sweep``)
take about three minutes each, because the sweep is the thing under test.
``python/tests/test_url_scheme_sweep_3665.py`` pins that every mutation text below
still exists, so the list cannot rot.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "parity_gateoff", ROOT / "scripts" / "template-library-parity-gateoff.py"
)
base = importlib.util.module_from_spec(_spec)
sys.modules["parity_gateoff"] = base
_spec.loader.exec_module(base)

TAGS = ROOT / "python/djust/components/templatetags/djust_components.py"
HANDLERS = ROOT / "python/djust/components/rust_handlers.py"
UTILS = ROOT / "python/djust/components/utils.py"
COMP = ROOT / "python/djust/components/components"
SWEEP = ROOT / "scripts/lib/template_library_parity/url_sweep.py"

AVATAR = "python/tests/test_avatar_src_scheme_3665.py"
SINKS = "python/tests/test_image_url_sinks_3665.py"
SWEEPTEST = "python/tests/test_url_scheme_sweep_3665.py"
SWEEP_K = "no_tag_emits or no_class_component or planted_hole or Scanner"

#: (label, file, text to find, replacement, test file, -k expression or "")
MUTATIONS: list[tuple[str, pathlib.Path, str, str, str, str]] = [
    # --- the fixes: each reverted alone, caught by the fast tests
    (
        "avatar tag: src printed as given (the issue)",
        TAGS,
        '        "src": mark_safe(url_attr(src, image=True)),',
        '        "src": src,',
        AVATAR,
        "",
    ),
    (
        "responsive_image tag: srcset only escaped",
        TAGS,
        "        img_attrs.append(f'srcset=\"{srcset_attr(srcset)}\"')",
        "        img_attrs.append(f'srcset=\"{conditional_escape(srcset)}\"')",
        SINKS,
        "",
    ),
    (
        "responsive_image handler: srcset only escaped",
        HANDLERS,
        '        srcset = srcset_attr(kw.get("srcset", ""))',
        '        srcset = conditional_escape(str(kw.get("srcset", "")))',
        SINKS,
        "",
    ),
    (
        "srcset_attr: candidates are not checked",
        UTILS,
        "        candidate = url_attr(url, image=True)\n",
        "        candidate = str(conditional_escape(url))\n",
        SINKS,
        "",
    ),
    (
        "srcset_attr: a marked-safe value is no longer returned unchanged",
        UTILS,
        "    if isinstance(value, SafeData):\n        return str(value)\n    parts = []",
        "    parts = []",
        SINKS,
        "",
    ),
    (
        "ImageCropper: src only escaped",
        COMP / "image_cropper.py",
        "e_src = url_attr(self.src, image=True)",
        "e_src = html.escape(self.src)",
        SINKS,
        "",
    ),
    (
        "ImageUploadPreview: preview url only escaped",
        COMP / "image_upload_preview.py",
        "e_url = url_attr(url, image=True)",
        "e_url = html.escape(str(url))",
        SINKS,
        "",
    ),
    (
        "LiveIndicator: avatar only escaped",
        COMP / "live_indicator.py",
        "e_avatar = url_attr(avatar, image=True)",
        "e_avatar = html.escape(str(avatar))",
        SINKS,
        "",
    ),
    (
        "MentionsInput: avatar only escaped",
        COMP / "mentions_input.py",
        'avatar_src = url_attr(user.get("avatar", ""), image=True)',
        'avatar_src = html.escape(str(user.get("avatar", "")))',
        SINKS,
        "",
    ),
    (
        "OrgChart: avatar only escaped",
        COMP / "org_chart.py",
        'src="{url_attr(avatar, image=True)}"',
        'src="{html.escape(str(avatar))}"',
        SINKS,
        "",
    ),
    (
        "ResponsiveImage: srcset only escaped",
        COMP / "responsive_image.py",
        'srcset="{srcset_attr(self.srcset)}"',
        'srcset="{html.escape(self.srcset)}"',
        SINKS,
        "",
    ),
    # --- the sweep must itself notice a reverted fix, through its own path
    (
        "sweep (tags): the avatar fix reverted, the SWEEP must go red",
        TAGS,
        '        "src": mark_safe(url_attr(src, image=True)),',
        '        "src": src,',
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (tags): the srcset fix reverted in the Python tag, the SWEEP must go red",
        TAGS,
        "        img_attrs.append(f'srcset=\"{srcset_attr(srcset)}\"')",
        "        img_attrs.append(f'srcset=\"{conditional_escape(srcset)}\"')",
        SWEEPTEST,
        SWEEP_K,
    ),
    (
        "sweep (components): OrgChart's fix reverted (a LIST-of-dicts parameter), the SWEEP must go red",
        COMP / "org_chart.py",
        'src="{url_attr(avatar, image=True)}"',
        'src="{html.escape(str(avatar))}"',
        SWEEPTEST,
        SWEEP_K,
    ),
    # --- the sweep's own guarantees
    (
        "scanner: nothing is ever dangerous",
        SWEEP,
        "    scheme = scheme_of(attr.value)\n    if not scheme or not is_url_attr(attr.name):\n        return False",
        "    scheme = scheme_of(attr.value)\n    if True:\n        return False",
        SWEEPTEST,
        "Scanner or planted_hole",
    ),
    (
        "scanner: control characters and whitespace are not stripped before the scheme probe",
        SWEEP,
        '_STRIP = re.compile(r"[\\x00-\\x20]+")',
        '_STRIP = re.compile(r"^$")',
        SWEEPTEST,
        "Scanner",
    ),
    (
        "scanner: attribute values are not entity-decoded",
        SWEEP,
        "            yield Attr(element, m.group(1).lower(), html.unescape(raw))",
        "            yield Attr(element, m.group(1).lower(), raw)",
        SWEEPTEST,
        "Scanner",
    ),
]


def run(work: pathlib.Path, target: str, expr: str) -> tuple[str, str]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(work / "python"), str(work)]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("DJANGO_SETTINGS_MODULE", None)
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", target]
    if expr:
        cmd += ["-k", expr]
    try:
        done = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return "HUNG", ""
    out = done.stdout + done.stderr
    first = next((ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR "))), "")
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)), ""
    )
    if done.returncode == 0 and "passed" in summary and "failed" not in summary:
        return "GREEN", ""
    if "failed" in summary and not re.search(r"\d+ error", summary):
        return "RED", first
    return "ERROR", first


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for label, path, *_ in MUTATIONS:
            print("%-100s %s" % (label, path.relative_to(ROOT)))
        return 0
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    chosen = [m for m in MUTATIONS if only is None or only in m[0]]
    if not chosen:
        print("no mutation matches %r" % only)
        return 2
    base._COPY_FILES = base._COPY_FILES + (
        AVATAR,
        SINKS,
        SWEEPTEST,
        "scripts/template-library-url-sweep.py",
        "scripts/url-sweep-gateoff.py",
    )
    real = [m[1] for m in MUTATIONS]
    before = base.tree_digest(real)
    survivors = 0
    with base.workdir() as work:
        print("workdir: %s" % work, flush=True)
        if "--no-control" not in argv:
            for target in (AVATAR, SINKS):
                control, _ = run(work, target, "")
                print("control (unmutated) %s: %s" % (target, control), flush=True)
                if control != "GREEN":
                    return 2
        for label, path, old, new, target, expr in chosen:
            file = work / path.relative_to(ROOT)
            original = file.read_text(encoding="utf-8")
            assert old in original, "MUTATION TEXT NOT FOUND for %s" % label
            mutated = original.replace(old, new, 1)
            assert mutated != original, "NO-OP MUTATION for %s" % label
            file.write_text(mutated, encoding="utf-8")
            for cache in work.rglob("__pycache__"):
                base.shutil.rmtree(cache, ignore_errors=True)
            print("mutating: %s" % label, flush=True)
            outcome, first = run(work, target, expr)
            file.write_text(original, encoding="utf-8")
            survivors += outcome != "RED"
            print("%-6s %s\n         %s" % (outcome, label, first[:170]), flush=True)
    assert base.tree_digest(real) == before, "the real tree changed during the run"
    print("%d of %d mutations turned the test red" % (len(chosen) - survivors, len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
