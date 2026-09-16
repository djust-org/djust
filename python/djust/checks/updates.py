"""djust.U001 — a newer release or a published advisory for the installed version.

Reads only the cache written by :mod:`djust.updates`; ``manage.py check``
never touches the network. The dev server and the CLI refresh the cache.
"""

from typing import Any

from django.core.checks import CheckMessage, register

from djust.checks.utils import DjustInfo, DjustWarning, _is_check_suppressed

INSTALL_HINT = "uv pip install -U djust"


@register("djust")
def check_updates(app_configs: Any, **kwargs: Any) -> list[CheckMessage]:
    from django.conf import settings

    from djust import updates

    if _is_check_suppressed("djust.U001"):
        return []
    config = getattr(settings, "DJUST_CONFIG", None) or {}
    if not updates.should_check(debug=bool(settings.DEBUG), config=config):
        return []
    status = updates.check(fetch=False)
    if status is None:
        return []
    message = status.message(INSTALL_HINT)
    if message is None:
        return []
    cls = DjustWarning if status.advisories else DjustInfo
    return [
        cls(
            message,
            hint="Disable with DJUST_CONFIG = {'update_check': False} or DJUST_NO_UPDATE_CHECK=1.",
            id="djust.U001",
        )
    ]
