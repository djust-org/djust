"""Write an SBOM with a known-vulnerable package through djust's own generator.

CI scans it and requires the scanner to FAIL: that proves the scanner reads
our SBOM shape (nested components, purls) instead of silently seeing nothing.
lodash 4.17.20 has CVE-2021-23337 (fixed in 4.17.21).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from djust.assets.manifest import Asset, AssetFile, Package, sri  # noqa: E402
from djust.assets.sbom import dumps, to_cyclonedx  # noqa: E402

asset = Asset(
    name="canary",
    source="canary",
    files=(AssetFile(integrity=sri(b"canary"), type="script", path="canary.js"),),
    packages=(Package("pkg:npm/lodash@4.17.20", "MIT"),),
)
out = Path(sys.argv[1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(dumps(to_cyclonedx([asset], root_name="canary", root_version=None, digest="0" * 64)))
