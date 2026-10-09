"""Repository-wide test isolation, including in-process git calls (#3179)."""

import pytest


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """Keep git commands aimed at fixture repositories under hooks (#3179)."""
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)
