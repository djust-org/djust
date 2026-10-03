"""Keep fixture repos out of the HOST repository's git state.

Fixtures in this suite create throwaway git repos and shell out to `git`
(`add`, `commit`, `checkout`) inside them. Copying the process environment is
the obvious thing to do there — and it silently carries the host repo's git
state along with it:

    GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY,
    GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_COMMON_DIR

`pre-commit` exports `GIT_INDEX_FILE` — the index it stashes your working tree
into — to every hook it runs. A fixture that inherits it does not operate on
its own throwaway repo; it operates on the REAL repository's index. Observed
on the pre-push hook (djust 1.1 line, 2026-09-15):

* `test_run_with_venv_python.py`'s fixture ran `git add -A` against the leaked
  index, writing the FIXTURE's paths into the real index. pre-commit's
  stash/restore then left the fixture's one-line `# djust package stub` in
  `python/djust/__init__.py` in the working tree, so the next import in that
  same pytest run failed with `cannot import name 'LiveView' from 'djust'` —
  which the pre-push hook correctly refused to attribute, blocking the push
  for a reason nothing in the failure text named.
* The same leak had already detached the index wholesale (every real path
  staged as deleted), and made another fixture's `git commit` fail with a
  non-zero exit only under pre-commit.

Scrub the family before handing an environment to a fixture's git.
"""

from __future__ import annotations

from typing import Dict

HOST_GIT_STATE_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
)


def scrub_host_git_state(env: Dict[str, str]) -> Dict[str, str]:
    """Remove the host repo's git-state variables from ``env``, in place."""
    for name in HOST_GIT_STATE_VARS:
        env.pop(name, None)
    return env
