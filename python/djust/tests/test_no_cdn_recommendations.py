"""Task 10 (vendored assets): djust must not generate or recommend loading
third-party code from the Tailwind CDN.

The scaffold's example template (``djust_theme.py``) used to write
``<link href="https://cdn.tailwindcss.com" ...>`` into generated projects,
and the C011 system check's development hint used to suggest the CDN as a
fallback. Both now point users at the Tailwind CLI build instead.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_djust_never_emits_or_recommends_the_tailwind_cdn():
    hits = subprocess.run(
        ["git", "grep", "-n", "cdn.tailwindcss.com", "--", "python/djust", ":!python/djust/tests"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout
    assert hits == ""
