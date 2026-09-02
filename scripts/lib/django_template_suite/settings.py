"""Settings for running the checkout's ``tests/runtests.py`` under the recorder.

Django's own ``tests/test_sqlite.py`` (in-memory sqlite for ``default`` and
``other``, ``USE_TZ=False``) plus ``TEST_RUNNER`` pointing at the recording
runner. ``runtests.py`` only sets a ``TEST_RUNNER`` default when the settings
module has none, so ours is honoured.

Importable only with the checkout's ``tests/`` directory on ``sys.path`` —
``child.py`` arranges that; nothing else should import this module.
"""

from test_sqlite import *  # noqa: F401,F403 — Django's own test settings, re-exported

TEST_RUNNER = "scripts.lib.django_template_suite.recorder.DjustSuiteRunner"
