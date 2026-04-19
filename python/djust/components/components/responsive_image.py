"""Responsive Image component with srcset, lazy loading, and blur-up."""

import html

from djust import Component


class ResponsiveImage(Component):
    """Picture element with srcset, lazy loading, and blur-up placeholder.

    Usage in a LiveView::

        self.hero = ResponsiveImage(
            src="/static/hero.jpg",
            alt="Hero image",
            aspect_ratio="16/9",
            lazy=True,
        )

    In template::

        {{ hero|safe }}

    CSS Custom Properties::

        --dj-responsive-image-radius: border radius
        --dj-responsive-image-bg: placeholder background

    Args:
        src: Image URL (required)
        alt: Alt text (required for accessibility)
        aspect_ratio: CSS aspect-ratio value (e.g. "16/9", "4/3", "1/1")
        lazy: Enable native lazy loading (default True)
        srcset: Optional srcset attribute value
        sizes: Optional sizes attribute value
        placeholder: URL for blur-up placeholder image
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        src: str = "",
        alt: str = "",
        aspect_ratio: str = "",
        lazy: bool = True,
        srcset: str = "",
        sizes: str = "",
        placeholder: str = "",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            src=src,
            alt=alt,
            aspect_ratio=aspect_ratio,
            lazy=lazy,
            srcset=srcset,
            sizes=sizes,
            placeholder=placeholder,
            custom_class=custom_class,
            **kwargs,
        )
        self.src = src
        self.alt = alt
        self.aspect_ratio = aspect_ratio
        self.lazy = lazy
        self.srcset = srcset
        self.sizes = sizes
        self.placeholder = placeholder
        self.custom_class = custom_class

    def _render_custom(self) -> str:
        """Render the responsive image HTML."""
        classes = ["dj-responsive-image"]
        if self.placeholder:
            classes.append("dj-responsive-image--blur-up")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        e_src = html.escape(self.src)
        e_alt = html.escape(self.alt)

        style = ""
        if self.aspect_ratio:
            e_ratio = html.escape(self.aspect_ratio)
            style = f' style="aspect-ratio:{e_ratio}"'

        img_attrs = [f'src="{e_src}"', f'alt="{e_alt}"']

        if self.lazy:
            img_attrs.append('loading="lazy"')

        if self.srcset:
            img_attrs.append(f'srcset="{html.escape(self.srcset)}"')

        if self.sizes:
            img_attrs.append(f'sizes="{html.escape(self.sizes)}"')

        img_tag = f'<img {" ".join(img_attrs)} class="dj-responsive-image__img">'

        placeholder_html = ""
        if self.placeholder:
            e_ph = html.escape(self.placeholder)
            placeholder_html = (
                f'<img src="{e_ph}" alt="" class="dj-responsive-image__placeholder" '
                f'aria-hidden="true">'
            )

        return f'<div class="{class_str}"{style}>{placeholder_html}{img_tag}</div>'
