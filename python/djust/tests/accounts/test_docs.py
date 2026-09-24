"""The accounts guide may only name things that exist (djust-docs' docs-verify contract)."""

import importlib
import pathlib
import re

ROOT = pathlib.Path(__file__).parents[4]
GUIDE = ROOT / "docs/website/guides/accounts.md"
SECTIONS = (
    "## Quick start",
    "## Choosing a backend",
    "## The `auth` context",
    "## Components",
    "## Overriding templates",
    "## Writing a backend",
    "## Hooks and signals",
    "## Security defaults",
    "## System checks",
    "## Migrating",
)


def test_guide_exists_with_the_required_sections():
    text = GUIDE.read_text()
    for heading in SECTIONS:
        assert heading in text, heading


def _resolves(dotted: str) -> bool:
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def test_every_dotted_djust_name_in_the_guide_exists():
    text = GUIDE.read_text()
    names = set(re.findall(r"`(djust(?:\.[A-Za-z_][A-Za-z0-9_]*)+)`", text))
    assert names, "the guide should reference real djust names"
    missing = sorted(n for n in names if not _resolves(n))
    assert missing == []


def test_every_tag_in_the_guide_is_registered():
    from djust.auth.templatetags.djust_auth import register

    text = GUIDE.read_text()
    tags = set(re.findall(r"\{% (auth_[a-z_]+)", text))
    assert tags
    assert sorted(t for t in tags if t not in register.tags) == []


def test_every_setting_in_the_security_table_is_a_real_default():
    from djust.auth.accounts.backends.allauth import SECURE_DEFAULTS

    text = GUIDE.read_text().split("## Security defaults", 1)[1].split("\n## ", 1)[0]
    for name in re.findall(r"`(ACCOUNT_[A-Z_]+)`", text):
        assert name in SECURE_DEFAULTS, name


def test_every_check_id_is_documented_in_both_places():
    codes = (ROOT / "docs/website/guides/error-codes.md").read_text()
    guide = GUIDE.read_text()
    for n in range(100, 107):
        assert f"A{n}" in codes and f"A{n}" in guide


def test_authentication_guide_links_to_accounts():
    assert "accounts.md" in (ROOT / "docs/website/guides/authentication.md").read_text()


def test_quick_start_sets_where_users_land():
    quick = GUIDE.read_text().split("## Quick start", 1)[1].split("\n## ", 1)[0]
    assert "LOGIN_REDIRECT_URL" in quick
