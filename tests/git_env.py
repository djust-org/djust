"""Environment for a test fixture that runs `git` in a temporary repository.

A fixture that shells out to `git` must never inherit git's own execution
variables. Under a git hook (`pre-push`, `pre-commit`, …) git exports
``GIT_DIR`` and ``GIT_INDEX_FILE`` pointing at the REAL repository, so a
fixture's ``git init`` in a temp directory silently re-initialises the real
repo — rewriting its shared config (this is where a stray ``core.bare = true``
comes from) — and the fixture's ``git add`` / ``git commit`` write into the
real index, which then shows thousands of staged deletions (#2608).

Reproduced::

    $ cd "$(mktemp -d)" && GIT_DIR=/path/to/repo/.git git init -q -b main
    warning: re-init: ignored --initial-branch=main

So: strip every ``GIT_*`` execution variable, keep the caller's overrides.
"""

from __future__ import annotations

import os

#: Variables git exports to hooks (and that a user may set) which redirect a
#: subsequent `git` invocation away from its own working directory.
GIT_EXECUTION_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_INDEX_VERSION",
    "GIT_NAMESPACE",
)


def isolated_git_env(**overrides: str) -> dict[str, str]:
    """`os.environ` with every git execution variable removed.

    `overrides` are applied last, so a caller can still pin identity or config
    variables (``GIT_AUTHOR_NAME``, ``GIT_CONFIG_GLOBAL``, …).
    """
    env = {k: v for k, v in os.environ.items() if k not in GIT_EXECUTION_VARS}
    env.update(overrides)
    return env
