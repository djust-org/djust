"""Third-party browser assets: manifests, registry, tags and SBOMs (ADR-040)."""

RESET_ON = frozenset(
    {
        "DEBUG",
        "DJUST_ASSET_MANIFESTS",
        "INSTALLED_APPS",
        "STATICFILES_DIRS",
        "STATIC_ROOT",
        "STATIC_URL",
        "STORAGES",
        "STATICFILES_STORAGE",
    }
)


def on_setting_changed(*, setting: str, **kwargs: object) -> None:
    """``setting_changed`` receiver: drop cached state derived from settings."""
    if setting not in RESET_ON:
        return
    from .registry import reset_registry
    from .tags import clear_integrity_cache

    reset_registry()
    clear_integrity_cache()
