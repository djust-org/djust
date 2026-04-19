"""Map Picker component for click-to-pick location on a map."""

import html

from djust import Component


class MapPicker(Component):
    """Click-to-pick location on a Leaflet/OSM map.

    Renders an interactive map container with a marker at the selected
    coordinates. Clicking the map fires an event with lat/lng.

    Usage in a LiveView::

        self.map = MapPicker(
            lat=40.7128,
            lng=-74.0060,
            pick_event="set_location",
        )

    In template::

        {{ map|safe }}

    CSS Custom Properties::

        --dj-map-picker-height: map height (default: 400px)
        --dj-map-picker-radius: border radius (default: 0.5rem)
        --dj-map-picker-border: border color (default: #e5e7eb)

    Args:
        lat: Latitude of the marker.
        lng: Longitude of the marker.
        pick_event: Event name fired on map click.
        zoom: Map zoom level (default: 13).
        height: Map height CSS value.
        custom_class: Additional CSS classes.
    """

    def __init__(
        self,
        lat: float = 0.0,
        lng: float = 0.0,
        pick_event: str = "set_location",
        zoom: int = 13,
        height: str = "400px",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            lat=lat,
            lng=lng,
            pick_event=pick_event,
            zoom=zoom,
            height=height,
            custom_class=custom_class,
            **kwargs,
        )
        self.lat = lat
        self.lng = lng
        self.pick_event = pick_event
        self.zoom = zoom
        self.height = height
        self.custom_class = custom_class

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

        return (
            f'<div class="{cls}" dj-hook="MapPicker" '
            f'data-lat="{lat}" data-lng="{lng}" '
            f'data-zoom="{zoom}" data-pick-event="{e_event}" '
            f'style="height:{e_height}" '
            f'role="application" aria-label="Map picker">'
            f'<div class="dj-map-picker__map"></div>'
            f"</div>"
        )
