"""Standalone Django setup for the concrete binding identity assertions."""

from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="typing-proof-only", INSTALLED_APPS=[], LIVEVIEW_CONFIG={"hot_reload": False}
    )

from positive import verify  # noqa: E402

verify()
