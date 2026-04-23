"""Auto-discovery of template tags and component classes for the gallery."""

import logging

logger = logging.getLogger(__name__)


def discover_template_tags():
    """Discover all registered template tags from the djust_components library.

    Returns a dict of {tag_name: tag_function_or_node} for all tags registered
    in the djust_components template tag library.
    """
    from djust.components.templatetags import djust_components as taglib

    lib = taglib.register
    # Library.tags contains both block tags and simple/inclusion tags
    return dict(lib.tags)


def discover_component_classes():
    """Discover all component classes exported from djust.components.components.

    Returns a dict of {class_name: class_object}.
    """
    import djust.components.components as comp_module

    class_names = comp_module.__all__
    return {name: getattr(comp_module, name) for name in class_names}


def get_gallery_data():
    """Build the full gallery data structure grouped by category.

    Returns:
        dict with 'categories' key mapping to {category_name: [component_info, ...]}

    The gallery's curated EXAMPLES / CLASS_EXAMPLES dicts in ``examples.py`` are
    the source of truth for what gets rendered — each entry carries human-authored
    variant data that auto-discovery can't reproduce. We still call
    ``discover_template_tags()`` and ``discover_component_classes()`` here to
    cross-check the two sources: any tag or component class that is registered
    in the codebase but missing an entry in the curated EXAMPLES emits a
    ``logger.debug`` warning. This catches the common drift where a developer
    adds a new ``@register.tag`` or ``Component`` subclass but forgets to add
    a gallery example — without this check the new thing silently disappears
    from the gallery instead of the author noticing at dev time.
    """
    from .examples import EXAMPLES, CLASS_EXAMPLES, CATEGORIES

    # Cross-check discovered registrations against the curated example lists.
    # Missing entries are debug-logged (not raised) so this never breaks the
    # gallery at runtime — it just surfaces the gap for anyone running with
    # ``logging.getLogger("djust.components.gallery.registry").setLevel(DEBUG)``.
    try:
        registered_tags = discover_template_tags()
        missing_tags = sorted(set(registered_tags) - set(EXAMPLES))
        if missing_tags:
            logger.debug(
                "gallery: %d registered template tag(s) missing EXAMPLES entries: %s",
                len(missing_tags),
                ", ".join(missing_tags),
            )
    except Exception as exc:  # pragma: no cover - defensive: never break the gallery
        logger.debug("gallery: discover_template_tags() failed: %s", exc)

    try:
        registered_classes = discover_component_classes()
        missing_classes = sorted(set(registered_classes) - set(CLASS_EXAMPLES))
        if missing_classes:
            logger.debug(
                "gallery: %d registered component class(es) missing CLASS_EXAMPLES entries: %s",
                len(missing_classes),
                ", ".join(missing_classes),
            )
    except Exception as exc:  # pragma: no cover - defensive: never break the gallery
        logger.debug("gallery: discover_component_classes() failed: %s", exc)

    # Group template tag examples by category
    categories = {}
    for tag_name, info in EXAMPLES.items():
        cat = info.get("category", "misc")
        cat_label = CATEGORIES.get(cat, cat.title())
        if cat_label not in categories:
            categories[cat_label] = []
        categories[cat_label].append(
            {
                "name": tag_name,
                "label": info["label"],
                "type": "tag",
                "variants": info["variants"],
            }
        )

    # Group class examples by category
    for class_name, info in CLASS_EXAMPLES.items():
        cat = info.get("category", "misc")
        cat_label = CATEGORIES.get(cat, cat.title())
        if cat_label not in categories:
            categories[cat_label] = []
        categories[cat_label].append(
            {
                "name": class_name,
                "label": info["label"],
                "type": "class",
                "variants": info["variants"],
            }
        )

    return {"categories": categories}
