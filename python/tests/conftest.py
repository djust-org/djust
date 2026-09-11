"""Test-isolation wiring for the ``python/tests`` root (#2234).

This root had **no conftest at all**, so the autouse `reset_djust_globals`
fixture that `tests/` and `python/djust/tests/` have wired since #1883 never
ran for its 133 files. That is a hole in what the module calls a "systemic
cure": two of three roots were covered, and the third was covered by nothing.

Surfaced by the Stage 11 review of PR #2236, which noticed the hygiene guards
*scan* this root while the reset does not *protect* it — the guards would
report a leak here that nothing prevented.

Deliberately a copy of the same three-line fixture rather than a shared helper:
`conftest.py` files are not importable from each other, and pytest discovers
them by location. The duplication is pytest's, not a choice.
"""

import pytest
from differential_corpus_2723 import CorpusCache


@pytest.fixture(autouse=True)
def _reset_djust_globals():
    """Reset djust process-global mutable state BEFORE each test.

    Pre-yield (resets before the test runs) so tests that set up their own
    global state in their body still work. See ``djust.test_isolation`` for the
    full inventory and the conservative-inclusion rationale.
    """
    from djust.test_isolation import reset_djust_globals

    reset_djust_globals()
    yield


@pytest.fixture(scope="session")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> CorpusCache:
    base = tmp_path_factory.getbasetemp()
    # An xdist worker's basetemp is `<controller basetemp>/popen-gwN`
    # (xdist/workermanage.py). The parent is the one directory every worker
    # of THIS session can see, and a later session gets a fresh numbered dir.
    if base.name.startswith("popen-"):
        base = base.parent
    return CorpusCache(base / "corpus-2345")


@pytest.fixture(scope="session")
def corpus_payload(corpus: CorpusCache) -> dict:
    """The unchanged full sweep, shared by all readers in one CI shard.

    tests.corpus_shards recognizes this fixture in the collected dependency
    closure. Mutated scripts still use CorpusCache directly and get their own
    content-keyed runs; this fixture represents only the unmodified sweep.
    """
    return corpus.sweep()
