from __future__ import annotations

from django.test import override_settings

from djust.assets.registry import build_registry, get_registry
from djust.tests._asset_fixtures import write_asset


def test_first_declaration_wins_and_the_other_is_recorded(tmp_path):
    _, first = write_asset(tmp_path / "a", version="2.0.0")
    _, second = write_asset(tmp_path / "b", version="1.0.0")
    reg = build_registry([first, second])
    assert reg.assets["test-lib"].packages[0].version == "2.0.0"
    (shadow,) = reg.shadows
    assert shadow.winner.source == str(first)
    assert shadow.loser.packages[0].version == "1.0.0"


def test_problems_are_collected_not_raised(tmp_path):
    reg = build_registry([tmp_path / "missing.json"])
    assert reg.assets == {}
    assert [p.check_id for p in reg.problems] == ["djust.B001"]


def test_digest_is_stable_and_ignores_source_path(tmp_path):
    _, a = write_asset(tmp_path / "a")
    _, b = write_asset(tmp_path / "b")
    assert build_registry([a]).digest() == build_registry([b]).digest()
    _, c = write_asset(tmp_path / "c", version="9.9.9")
    assert build_registry([a]).digest() != build_registry([c]).digest()
    assert len(build_registry([a]).digest()) == 64


def test_project_manifests_come_first_and_reset_on_setting_change(tmp_path):
    _, manifest = write_asset(tmp_path)
    with override_settings(DJUST_ASSET_MANIFESTS=[str(manifest)]):
        reg = get_registry()
        assert reg.assets["test-lib"].source == str(manifest)
    assert "test-lib" not in get_registry().assets
