import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.slow
def test_wheel_carries_the_sbom_in_both_places(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "maturin",
            "build",
            "--release",
            "-i",
            sys.executable,
            "-o",
            str(tmp_path),
        ],
        cwd=REPO,
        check=True,
    )
    (wheel,) = tmp_path.glob("djust-*.whl")
    names = zipfile.ZipFile(wheel).namelist()
    assert "djust/djust.cdx.json" in names
    assert any(n.endswith(".dist-info/sboms/djust.cdx.json") for n in names)
    assert not any("/static/" in n and n.endswith(".cdx.json") for n in names)
