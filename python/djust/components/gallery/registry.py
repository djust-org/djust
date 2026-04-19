"""Auto-discovery of template tags and component classes for the gallery."""


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
    from djust.components.components import __all__ as class_names
    import djust.components.components as comp_module

    return {name: getattr(comp_module, name) for name in class_names}


def get_gallery_data():
    """Build the full gallery data structure grouped by category.

    Returns:
        dict with 'categories' key mapping to {category_name: [component_info, ...]}
    """
    from .examples import EXAMPLES, CLASS_EXAMPLES, CATEGORIES

    tags = discover_template_tags()
    classes = discover_component_classes()

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
