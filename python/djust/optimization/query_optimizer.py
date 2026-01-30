"""
Django ORM Query Optimizer

Analyzes variable access paths and generates optimal select_related/prefetch_related calls.
"""

from typing import Any, Dict, List, Set
from django.db import models
from django.core.exceptions import FieldDoesNotExist
from django.db.models.fields.related import (
    ForeignKey,
    OneToOneField,
    ManyToManyField,
)


class QueryOptimization:
    """Result of query analysis."""

    def __init__(self):
        self.select_related: Set[str] = set()
        self.prefetch_related: Set[str] = set()
        self.annotations: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, List[str]]:
        """Convert to dictionary format."""
        return {
            "select_related": sorted(self.select_related),
            "prefetch_related": sorted(self.prefetch_related),
            "annotations": list(self.annotations.keys()),
        }


def analyze_queryset_optimization(
    model_class: type[models.Model], variable_paths: List[str]
) -> QueryOptimization:
    """
    Analyze variable paths and determine optimal Django ORM optimization.

    Args:
        model_class: Django model class (e.g., Lease)
        variable_paths: List of dot-separated paths (e.g., ["property.name", "tenant.user.email"])

    Returns:
        QueryOptimization with select_related and prefetch_related sets

    Example:
        >>> optimization = analyze_queryset_optimization(Lease, ["property.name", "tenant.user.email"])
        >>> optimization.to_dict()
        {"select_related": ["property", "tenant__user"], "prefetch_related": []}
    """
    optimization = QueryOptimization()

    for path in variable_paths:
        _analyze_path(model_class, path, optimization)

    return optimization


def _analyze_path(
    model_class: type[models.Model],
    path: str,
    optimization: QueryOptimization,
    prefix: str = "",
):
    """
    Recursively analyze a single path and update optimization.

    Args:
        model_class: Current model class being analyzed
        path: Remaining path to analyze (e.g., "tenant.user.email")
        optimization: QueryOptimization to update
        prefix: Django ORM path prefix (e.g., "lease__tenant")
    """
    if not path:
        return

    parts = path.split(".", 1)
    field_name = parts[0]
    remaining_path = parts[1] if len(parts) > 1 else ""

    # Try to get the field from the model
    try:
        field = model_class._meta.get_field(field_name)
    except FieldDoesNotExist:
        # Check for @property with annotation hint via _djust_annotations
        # Use _annotated_ prefix to avoid conflict with @property descriptors
        annotations = getattr(model_class, "_djust_annotations", {})
        if field_name in annotations:
            annotation_key = f"_annotated_{field_name}"
            optimization.annotations[annotation_key] = annotations[field_name]
        return

    # Build Django ORM path
    django_path = f"{prefix}__{field_name}" if prefix else field_name

    if isinstance(field, (ForeignKey, OneToOneField)):
        # Use select_related for ForeignKey/OneToOne
        optimization.select_related.add(django_path)

        # Continue analyzing nested path
        if remaining_path:
            related_model = field.related_model
            _analyze_path(related_model, remaining_path, optimization, django_path)

    elif isinstance(field, ManyToManyField):
        # Use prefetch_related for ManyToMany
        optimization.prefetch_related.add(django_path)

        # Continue analyzing nested path
        if remaining_path:
            related_model = field.related_model
            _analyze_path(related_model, remaining_path, optimization, django_path)

    elif field.is_relation and field.one_to_many:
        # Reverse ForeignKey (e.g., property.leases)
        # Use prefetch_related
        optimization.prefetch_related.add(django_path)

        # Continue analyzing nested path
        if remaining_path:
            related_model = field.related_model
            _analyze_path(related_model, remaining_path, optimization, django_path)


def optimize_queryset(queryset, optimization: QueryOptimization):
    """
    Apply select_related/prefetch_related to a QuerySet.

    Args:
        queryset: Django QuerySet
        optimization: QueryOptimization from analyze_queryset_optimization()

    Returns:
        Optimized QuerySet

    Example:
        >>> qs = Lease.objects.all()
        >>> optimization = analyze_queryset_optimization(Lease, ["property.name"])
        >>> qs = optimize_queryset(qs, optimization)
        >>> # qs is now: Lease.objects.all().select_related("property")
    """
    if optimization.annotations:
        queryset = queryset.annotate(**optimization.annotations)

    if optimization.select_related:
        queryset = queryset.select_related(*optimization.select_related)

    if optimization.prefetch_related:
        queryset = queryset.prefetch_related(*optimization.prefetch_related)

    return queryset
