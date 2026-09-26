"""The djust_admin templates must not load the Tailwind runtime CDN compiler.

Task 9 (vendored assets): base.html, login.html and logout.html used to load
``https://cdn.tailwindcss.com`` directly. They now render the static,
vendored ``admin-css`` asset (Task 7) via ``{% djust_asset "admin-css" %}``.
"""

from pathlib import Path

ADMIN_TEMPLATES = Path(__file__).resolve().parents[1] / "admin_ext" / "templates" / "djust_admin"


def test_admin_templates_load_no_cdn():
    offenders = [
        p.name for p in ADMIN_TEMPLATES.glob("*.html") if "cdn.tailwindcss.com" in p.read_text()
    ]
    assert offenders == []


def test_admin_base_uses_the_declared_stylesheet():
    text = (ADMIN_TEMPLATES / "base.html").read_text()
    assert '{% djust_asset "admin-css" %}' in text and "djust_assets" in text
