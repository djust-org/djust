"""{% djust_asset %} renders through the Rust engine's library bridge (#2547)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from django.test import override_settings  # noqa: E402

from djust.tests._asset_fixtures import write_asset  # noqa: E402
from test_load_imports_django_libraries_2547 import liveview_render  # noqa: E402


def test_djust_asset_in_a_liveview_template(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    with override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)], STATICFILES_DIRS=[str(static_dir)]
    ):
        out = liveview_render('<div>{% load djust_assets %}{% djust_asset "test-lib" %}</div>', {})
    assert '<script src="/static/testlib/lib.js" integrity="sha384-' in out
