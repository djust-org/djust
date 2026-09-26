"""Build a throwaway vendored asset: a static file plus a manifest declaring it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from djust.assets.manifest import sri


def write_asset(
    tmp_path: Path,
    name: str = "test-lib",
    rel: str = "testlib/lib.js",
    content: bytes = b"console.log(1);\n",
    version: str = "1.0.0",
    manifest_name: str = "djust_assets.json",
    **file_extra: Any,
) -> tuple[Path, Path]:
    static_dir = tmp_path / "static"
    target = static_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    entry = {"path": rel, "integrity": sri(content), **file_extra}
    manifest = tmp_path / manifest_name
    manifest.write_text(
        json.dumps(
            {
                "schema": 1,
                "assets": {
                    name: {
                        "files": [entry],
                        "packages": [{"purl": f"pkg:npm/{name}@{version}", "license": "MIT"}],
                    }
                },
            }
        )
    )
    return static_dir, manifest
