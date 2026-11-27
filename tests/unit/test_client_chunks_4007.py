"""The core client bundle and its lazily loaded chunks (#4007).

The LiveView injects ``client-core[.min].js``, which loads the optional
chunks (``static/djust/chunks/``) only on pages that use them; the chunk URLs
ride on the script tag as ``data-djust-chunks`` and the chunk ``<script>``
takes the tag's nonce. ``LIVEVIEW_CONFIG['client_bundle'] = 'full'`` injects
the single bundle instead. The client half is in
``tests/js/client-chunks-4007.test.js``.
"""

from __future__ import annotations

import html
import importlib.util
import json
import re
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings

import djust
from djust.config import config
from djust._client_chunks import CLIENT_CHUNKS
from djust.mixins.post_processing import PostProcessingMixin

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "python/djust/static/djust"


class _View(PostProcessingMixin):
    def get_debug_info(self) -> dict:
        return {}


#: The cache-busting query on every bundle and chunk URL (#3658).
V = f"?v={djust.__version__}"


def _client_tag(request=None) -> str:
    view = _View()
    if request is not None:
        view.request = request
    out = view._inject_client_script("<html><body></body></html>")
    m = re.search(r'<script src="[^"]*djust/client[^"]*"[^>]*>', out)
    assert m, out
    return m.group(0)


@pytest.fixture
def full_bundle():
    previous = config.get("client_bundle")
    config.set("client_bundle", "full")
    try:
        yield
    finally:
        config.set("client_bundle", previous)


@override_settings(DEBUG=False)
def test_default_injects_core_with_hashed_chunk_urls_and_nonce():
    request = RequestFactory().get("/")
    request.csp_nonce = "n0nce"  # type: ignore[attr-defined]
    tag = _client_tag(request)
    assert f'src="/static/djust/client-core.min.js{V}"' in tag
    assert 'nonce="n0nce"' in tag
    chunks = json.loads(html.unescape(re.search(r'data-djust-chunks="([^"]*)"', tag).group(1)))
    assert chunks == {name: f"/static/djust/chunks/{name}.min.js{V}" for name in CLIENT_CHUNKS}


@override_settings(DEBUG=True)
def test_debug_injects_readable_core_and_chunks():
    tag = _client_tag()
    assert f'src="/static/djust/client-core.js{V}"' in tag
    chunks = json.loads(html.unescape(re.search(r'data-djust-chunks="([^"]*)"', tag).group(1)))
    assert chunks["uploads"] == f"/static/djust/chunks/uploads.js{V}"


@override_settings(DEBUG=False)
def test_full_bundle_setting_injects_client_min_js_without_chunks(full_bundle):
    tag = _client_tag()
    assert f'src="/static/djust/client.min.js{V}"' in tag
    assert "data-djust-chunks" not in tag


def test_chunk_list_matches_the_build_and_the_loader():
    build = (ROOT / "scripts/build-client.sh").read_text()
    spec_block = build[
        build.index("CHUNK_SPECS=(") : build.index(")", build.index("CHUNK_SPECS=("))
    ]
    built = re.findall(r'^\s+"([\w-]+)\|', spec_block, re.MULTILINE)
    assert sorted(built) == sorted(CLIENT_CHUNKS)

    loader = (STATIC / "src/13c-chunk-loader.js").read_text()
    ready = loader[loader.index("const _DJUST_CHUNK_READY = {") :]
    ready = ready[: ready.index("};")]
    assert sorted(re.findall(r"^    '([\w-]+)':", ready, re.MULTILINE)) == sorted(CLIENT_CHUNKS)

    for name in CLIENT_CHUNKS:
        assert (STATIC / "chunks" / f"{name}.js").is_file(), name
        assert (STATIC / "chunks" / f"{name}.min.js").is_file(), name


def _checker():
    spec = importlib.util.spec_from_file_location(
        "check_doc_snippets_4007", ROOT / "scripts/check-doc-snippets.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("line", "ok"),
    [
        ("The core client is ~50 KB gzipped <!-- size-claim: core -->", True),
        ("The core client is ~80 KB gzipped <!-- size-claim: core -->", False),
        ("The full client is ~80 KB gzipped <!-- size-claim: full -->", True),
        ("The full client is ~50 KB gzipped <!-- size-claim: full -->", False),
    ],
)
def test_size_checker_verifies_core_and_full_claims(tmp_path, line, ok):
    static = tmp_path / "static"
    static.mkdir()
    manifest = {
        "shipped": {"artifact": "client.min.js.gz", "kb": 80.0},
        "core": {"artifact": "client-core.min.js.gz", "kb": 50.0},
        "unminified": {"artifact": "client.js", "gz_kb": 280.0},
    }
    (static / "client-sizes.json").write_text(json.dumps(manifest))
    readme = tmp_path / "README.md"
    readme.write_text(line + "\n")
    errors, _warnings = _checker().check_js_size(static / "client.min.js.gz", readme)
    assert (errors == []) is ok, errors


def test_real_manifest_reports_core_full_and_chunks():
    m = json.loads((STATIC / "client-sizes.json").read_text())
    assert m["core"]["artifact"] == "client-core.min.js.gz"
    assert m["core"]["kb"] < m["shipped"]["kb"]
    assert sorted(m["chunks"]) == sorted(CLIENT_CHUNKS)
    core_gz = STATIC / "client-core.min.js.gz"
    if core_gz.is_file():
        assert abs(core_gz.stat().st_size / 1024 - m["core"]["kb"]) < 1.0
