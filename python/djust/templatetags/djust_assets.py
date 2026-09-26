"""``{% load djust_assets %}``: tags for declared third-party assets (ADR-040)."""

from __future__ import annotations

from django import template
from django.utils.safestring import SafeString

from djust.assets.tags import asset_tags, asset_url

register = template.Library()


@register.simple_tag
def djust_asset(name: str, variant: str | None = None) -> SafeString:
    """Script/stylesheet/modulepreload tags, with integrity, for asset ``name``."""
    return asset_tags(name, variant)


@register.simple_tag
def djust_asset_url(name: str, variant: str | None = None, file_type: str = "module") -> str:
    """The URL of the asset's first ``file_type`` file, for code that imports it."""
    return asset_url(name, variant, file_type)
