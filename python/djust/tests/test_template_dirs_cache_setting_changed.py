"""``get_template_dirs()`` follows ``override_settings(TEMPLATES=...)`` in and out.

The dirs are memoised (``_get_template_dirs_cached``) and nothing cleared the
cache when a settings override ended, so a test file that pointed ``TEMPLATES``
at a temporary directory left every later ``template_name`` view in the process
searching only that directory — "Template not found" for an unrelated file,
order-dependent. The receiver on ``setting_changed`` clears it.
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(SECRET_KEY="x", INSTALLED_APPS=["djust"], TEMPLATES=[])
    django.setup()

from django.test import override_settings  # noqa: E402

from djust.utils import get_template_dirs  # noqa: E402


def test_override_settings_templates_is_seen_and_undone(tmp_path):
    before = get_template_dirs()
    assert str(tmp_path) not in before
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": False,
            }
        ]
    ):
        assert get_template_dirs() == [str(tmp_path)]
    assert get_template_dirs() == before, "the override's dirs must not outlive it"
