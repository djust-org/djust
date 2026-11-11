"""A fixture for tests that DEFINE a Django model inside the test body (#3888).

A model class registers itself in the process-global app registry at class creation, and it stays
there: other tests in the same xdist worker that enumerate ``apps.get_models()`` (or an app's
models) then meet a model whose table never existed (``no such table: auth_owned3888``). The fixture
removes every model a test registered, and the registry's caches, when the test ends.

A test-defined model that points at a real model must use ``related_name="+"``: a reverse accessor
is added to the real class and is not removed with the registration.
"""

from __future__ import annotations

import pytest
from django.apps import apps

LABEL = "auth"


@pytest.fixture
def scratch_models():
    before = set(apps.all_models[LABEL])
    try:
        yield
    finally:
        for name in set(apps.all_models[LABEL]) - before:
            del apps.all_models[LABEL][name]
        apps.clear_cache()
