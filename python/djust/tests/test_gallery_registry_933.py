"""Regression test for bug #933 — gallery registry discover_* helpers were unused.

Before the fix: ``discover_template_tags()`` and ``discover_component_classes()``
were public helpers exported from ``djust.components.gallery.__init__`` but
``get_gallery_data()`` never called them. A developer adding a new
``@register.tag`` or ``Component`` subclass without updating the curated
``EXAMPLES`` / ``CLASS_EXAMPLES`` dicts would have that new thing silently
missing from the rendered gallery.

After the fix: ``get_gallery_data()`` calls both discover helpers and emits a
``logger.debug`` warning listing every registered tag / component class that
is missing an EXAMPLES / CLASS_EXAMPLES entry. The gallery still renders the
curated data as its source of truth (discover output can't reproduce
human-authored variant examples), but the drift is now observable.
"""

import logging

import tests.conftest  # noqa: F401  -- configure Django settings


def test_get_gallery_data_invokes_discover_helpers(monkeypatch):
    """Bug #933: ``get_gallery_data()`` must exercise both discover helpers."""
    from djust.components.gallery import registry

    called = {"tags": 0, "classes": 0}

    real_discover_tags = registry.discover_template_tags
    real_discover_classes = registry.discover_component_classes

    def tracking_discover_tags():
        called["tags"] += 1
        return real_discover_tags()

    def tracking_discover_classes():
        called["classes"] += 1
        return real_discover_classes()

    monkeypatch.setattr(registry, "discover_template_tags", tracking_discover_tags)
    monkeypatch.setattr(registry, "discover_component_classes", tracking_discover_classes)

    data = registry.get_gallery_data()

    assert called["tags"] == 1, "discover_template_tags must be called exactly once"
    assert called["classes"] == 1, "discover_component_classes must be called exactly once"
    # The curated data is still the source of truth for the gallery output
    assert "categories" in data
    assert isinstance(data["categories"], dict)


def test_get_gallery_data_logs_missing_tag_examples(monkeypatch, caplog):
    """A registered tag missing from EXAMPLES should be logged at DEBUG."""
    from djust.components.gallery import registry

    # Simulate a tag registered in the codebase but absent from EXAMPLES
    monkeypatch.setattr(
        registry,
        "discover_template_tags",
        lambda: {"brand_new_tag": object(), "another_new_tag": object()},
    )
    # Keep class discovery identical so we isolate the tag warning
    monkeypatch.setattr(registry, "discover_component_classes", lambda: {})

    with caplog.at_level(logging.DEBUG, logger="djust.components.gallery.registry"):
        registry.get_gallery_data()

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "missing EXAMPLES entries" in messages
    assert "brand_new_tag" in messages
    assert "another_new_tag" in messages


def test_get_gallery_data_logs_missing_class_examples(monkeypatch, caplog):
    """A registered component class missing from CLASS_EXAMPLES should be logged at DEBUG."""
    from djust.components.gallery import registry

    monkeypatch.setattr(registry, "discover_template_tags", lambda: {})
    monkeypatch.setattr(
        registry,
        "discover_component_classes",
        lambda: {"NewlyAddedComponent": object()},
    )

    with caplog.at_level(logging.DEBUG, logger="djust.components.gallery.registry"):
        registry.get_gallery_data()

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "missing CLASS_EXAMPLES entries" in messages
    assert "NewlyAddedComponent" in messages


def test_get_gallery_data_discover_failure_does_not_break_gallery(monkeypatch):
    """If discover_* raises, the gallery must still render (degrades gracefully)."""
    from djust.components.gallery import registry

    def boom():
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(registry, "discover_template_tags", boom)
    monkeypatch.setattr(registry, "discover_component_classes", boom)

    # Must not propagate
    data = registry.get_gallery_data()
    assert "categories" in data
    assert isinstance(data["categories"], dict)
