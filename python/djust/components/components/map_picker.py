"""Map Picker component for click-to-pick location on a map."""

import html
import json
import logging
import re

from djust import Component
from typing import Any, ClassVar, Optional, Tuple

logger = logging.getLogger(__name__)

#: OpenStreetMap's standard tile layer. Its usage policy
#: (https://operations.osmfoundation.org/policies/tiles/) is for light use:
#: a production app should point ``tile_url`` at its own tile server or a
#: provider it has an agreement with.
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_ATTRIBUTION = "© OpenStreetMap contributors"
DEFAULT_ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"

_TILE_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_TILE_VARIABLES = frozenset({"z", "x", "y", "s", "r"})
_LEAFLET_ICONS = {
    "icon": "djust_components/vendor/leaflet/images/marker-icon.png",
    "icon2x": "djust_components/vendor/leaflet/images/marker-icon-2x.png",
    "shadow": "djust_components/vendor/leaflet/images/marker-shadow.png",
}


def _plain_url(value: Any) -> str:
    """``value`` when it is an absolute http(s) URL or a root-relative path
    with no whitespace or control characters, else ``""``."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in text):
        return ""
    if text.startswith(("https://", "http://")) or (
        text.startswith("/") and not text.startswith("//")
    ):
        return text
    return ""


def tile_url_or_blank(value: Any) -> str:
    """A usable Leaflet tile URL template, or ``""`` (no tiles).

    The template must be an absolute http(s) URL or a root-relative path and
    carry ``{z}``, ``{x}`` and ``{y}``; ``{s}`` (subdomain) and ``{r}``
    (retina) are the only other placeholders Leaflet fills in here. Anything
    else (a ``javascript:`` or ``data:`` URL, a stray placeholder that makes
    Leaflet throw on the first tile request) renders a map with no tiles.
    """
    url = _plain_url(value)
    if not url:
        return ""
    names = _TILE_PLACEHOLDER.findall(url)
    if not {"z", "x", "y"} <= set(names) or not set(names) <= _TILE_VARIABLES:
        return ""
    return url


def _leaflet_assets_json() -> str:
    """The vendored Leaflet's URLs and integrity values, as JSON for the hook.

    Computed by ``djust.assets`` from the manifest (ADR-040), so they carry
    Subresource Integrity and the app's own (hashed) static names. The hook
    loads them on demand, once per page, only when a map is on the page.
    ``""`` when the asset cannot be resolved; the hook then says so.
    """
    from django.core.exceptions import ImproperlyConfigured
    from django.templatetags.static import static

    from djust.assets.tags import asset_source

    try:
        js_url, js_integrity, js_cors = asset_source("leaflet", file_type="script")
        css_url, css_integrity, css_cors = asset_source("leaflet", file_type="style")
        # Explicit icon URLs: Leaflet's own image-path detection reads the
        # name out of its stylesheet and breaks on hashed static file names.
        icons = {key: static(path) for key, path in _LEAFLET_ICONS.items()}
    except (ImproperlyConfigured, ValueError):
        logger.warning("MapPicker: the vendored 'leaflet' asset could not be resolved")
        return ""
    return json.dumps(
        {
            "js": js_url,
            "jsIntegrity": js_integrity,
            "css": css_url,
            "cssIntegrity": css_integrity,
            "crossOrigin": "anonymous" if (js_cors or css_cors) else "",
            **icons,
        },
        separators=(",", ":"),
    )


def map_picker_attrs(
    tile_url: Any = None,
    attribution: Any = None,
    attribution_url: Any = None,
    max_zoom: Any = 19,
) -> str:
    """The tile and Leaflet attributes of the hook host (additive to the 4 the
    markup always had).

    Shared by the component class, the Django tag and the Rust tag handler so
    the three cannot drift. Every value is escaped here. ``tile_url=None`` is
    OpenStreetMap's standard layer with its required attribution;
    ``tile_url=""`` means no tiles at all (a map that never touches the
    network); an unusable template means no tiles too (see
    :func:`tile_url_or_blank`) and is logged once per render, without the
    value.
    """
    default_tiles = tile_url is None
    tiles = DEFAULT_TILE_URL if default_tiles else tile_url_or_blank(tile_url)
    if not default_tiles and tile_url not in ("", None) and not tiles:
        logger.warning(
            "MapPicker: tile_url must be an http(s) or root-relative URL template "
            "with {z}, {x} and {y}; rendering the map without tiles"
        )
    text = "" if attribution is None else str(attribution).strip()[:300]
    if default_tiles and not text:
        # OpenStreetMap's licence requires its attribution: a blank one cannot remove it.
        text = DEFAULT_ATTRIBUTION
    link = _plain_url(attribution_url) if attribution_url is not None else ""
    if default_tiles and not link:
        link = DEFAULT_ATTRIBUTION_URL
    try:
        zoom_max = max(1, min(int(max_zoom), 22))
    except (ValueError, TypeError, OverflowError):
        zoom_max = 19
    out = (
        f' data-tile-url="{html.escape(tiles)}"'
        f' data-attribution="{html.escape(text)}"'
        f' data-attribution-url="{html.escape(link)}"'
        f' data-max-zoom="{zoom_max}"'
    )
    assets = _leaflet_assets_json()
    if assets:
        out += f' data-leaflet="{html.escape(assets)}"'
    return out


class MapPicker(Component):
    """Click-to-pick location on a Leaflet map.

    Renders an interactive map with a marker at the selected coordinates.
    Clicking the map (or moving the marker with the arrow keys and pressing
    Enter) sends ``pick_event`` with ``{lat, lng}``. Load
    ``djust_components/map-picker.js`` after the djust client; the script
    loads the vendored Leaflet on demand, only on a page that has a map.

    Usage in a LiveView::

        self.map = MapPicker(
            lat=40.7128,
            lng=-74.0060,
            pick_event="set_location",
        )

    In template::

        {{ map|safe }}

    Handle the pick. ``lat`` and ``lng`` come from the browser: treat them as
    untrusted input and validate them (the hook already sends only finite
    numbers, latitude within -90..90 and longitude within -180..180, but a
    crafted client can send anything)::

        import math

        @event_handler()
        def set_location(self, lat=None, lng=None, **kwargs):
            try:
                lat, lng = float(lat), float(lng)
            except (TypeError, ValueError):
                return
            if not (math.isfinite(lat) and math.isfinite(lng)):
                return
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                return
            self.lat, self.lng = lat, lng
            self.map = MapPicker(lat=lat, lng=lng, pick_event="set_location")

    Tiles and privacy: by default the map shows OpenStreetMap's standard
    tiles, with the attribution they require. Every viewer's browser then
    requests tiles from ``tile.openstreetmap.org``, so the tile provider sees
    each viewer's IP address (and the area they look at). OpenStreetMap's tile
    usage policy allows light use only: a production app should set
    ``tile_url`` to its own tile server or a provider it has an agreement
    with. No API key is stored or shipped by djust; a provider that needs a
    key must be reached through your own server or a URL you build.
    ``tile_url=""`` shows no tiles at all and makes no tile request. Allow
    the tile host in your Content-Security-Policy ``img-src``.

    The marker and the zoom buttons work without any tiles (a blocked or
    offline tile server leaves a grey map and a notice, not a broken page).
    ``tile_url``, ``attribution`` and ``attribution_url`` are developer
    settings, not user input: a ``tile_url`` taken from a user would make every
    viewer's browser contact a host that user chose.

    Keyboard: focus the map, then the arrow keys move the marker (Shift for
    larger steps), ``+`` and ``-`` zoom, Enter or Space choose the location
    (the only thing that sends the event) and Escape goes back to the chosen
    one. Moves and choices are announced and shown in a readout.

    Leaflet: the library is vendored (ADR-040, ``leaflet`` in the components
    ``djust_assets.json``, with its license and Subresource Integrity values)
    and served from your own origin; nothing is fetched from a CDN. The hook
    loads it on demand, once per page. The names come from the asset manifest,
    so hashed static file names (``ManifestStaticFilesStorage``, WhiteNoise)
    work, and the marker images are passed to Leaflet as explicit URLs because
    its own image-path detection cannot read a hashed name. To load it with
    the page instead, render ``{% djust_asset "leaflet" %}`` in a template (or
    ``{{ map.asset_tags }}``); an already loaded Leaflet is used as it is.

    Content-Security-Policy: scripts and the stylesheet come from your own
    origin and nothing uses ``eval`` or ``<style>`` elements; allow the tile
    host in ``img-src`` (a tile server on another origin needs nothing else).
    The map's height is rendered as an inline ``style`` attribute, which a
    policy without ``style-src-attr 'unsafe-inline'`` blocks.

    CSS Custom Properties::

        --dj-map-picker-height: map height (default: 400px)
        --dj-map-picker-radius: border radius (default: 0.5rem)
        --dj-map-picker-border: border color (default: #e5e7eb)

    Args:
        lat: Latitude of the marker.
        lng: Longitude of the marker.
        pick_event: Event name fired on a pick (``{lat, lng}``).
        zoom: Map zoom level (default: 13).
        height: Map height CSS value.
        custom_class: Additional CSS classes.
        tile_url: Leaflet tile URL template (http(s) or root-relative, with
            ``{z}``, ``{x}``, ``{y}``; ``{s}`` and ``{r}`` allowed). ``None``
            (default) is OpenStreetMap's standard layer, ``""`` is no tiles.
        attribution: Attribution text shown on the map (text only, never
            HTML). Defaults to OpenStreetMap's for the default tiles; set it
            to your provider's required text with a custom ``tile_url``.
        attribution_url: Optional http(s) link for the attribution text.
        max_zoom: Highest zoom level (default: 19, OpenStreetMap's).
        label: Accessible name of the map (default: "Map picker").
    """

    #: The vendored Leaflet (ADR-040). ``djust.B007`` checks it is declared;
    #: the hook loads it on demand, so ``asset_tags`` is only needed to load
    #: it eagerly or under a strict script policy.
    requires_assets: ClassVar[Tuple[str, ...]] = ("leaflet",)

    def __init__(
        self,
        lat: float = 0.0,
        lng: float = 0.0,
        pick_event: str = "set_location",
        zoom: int = 13,
        height: str = "400px",
        custom_class: str = "",
        tile_url: Optional[str] = None,
        attribution: Optional[str] = None,
        attribution_url: Optional[str] = None,
        max_zoom: int = 19,
        label: str = "Map picker",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            lat=lat,
            lng=lng,
            pick_event=pick_event,
            zoom=zoom,
            height=height,
            custom_class=custom_class,
            tile_url=tile_url,
            attribution=attribution,
            attribution_url=attribution_url,
            max_zoom=max_zoom,
            label=label,
            **kwargs,
        )
        self.lat = lat
        self.lng = lng
        self.pick_event = pick_event
        self.zoom = zoom
        self.height = height
        self.custom_class = custom_class
        self.tile_url = tile_url
        self.attribution = attribution
        self.attribution_url = attribution_url
        self.max_zoom = max_zoom
        self.label = label

    def _render_custom(self) -> str:
        cls = "dj-map-picker"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        e_event = html.escape(self.pick_event)
        try:
            lat = float(self.lat)
        except (ValueError, TypeError):
            lat = 0.0
        try:
            lng = float(self.lng)
        except (ValueError, TypeError):
            lng = 0.0
        try:
            zoom = int(self.zoom)
        except (ValueError, TypeError):
            zoom = 13

        e_height = html.escape(str(self.height))
        e_label = html.escape(str(self.label))
        extra = map_picker_attrs(
            self.tile_url, self.attribution, self.attribution_url, self.max_zoom
        )

        return (
            f'<div class="{cls}" dj-hook="MapPicker" '
            f'data-lat="{lat}" data-lng="{lng}" '
            f'data-zoom="{zoom}" data-pick-event="{e_event}"{extra} '
            f'style="height:{e_height}" '
            f'role="application" aria-label="{e_label}">'
            f'<div class="dj-map-picker__map"></div>'
            f"</div>"
        )
