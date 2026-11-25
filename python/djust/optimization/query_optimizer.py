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


#: What a template reads off a to-many relation that needs at most one row of it, or
#: none: the manager's and the QuerySet's own methods, an index into ``all``, and a
#: length read (``djust_templates::parser::LENGTH_PATH``, #3862). See
#: :func:`_is_light_relation_read`.
_LIGHT_HEADS = ("count", "exists", "first", "last")
_LENGTH_PATH = "__len__"


def _is_light_relation_read(remaining_path: str) -> bool:
    """Does ``remaining_path`` (what follows a to-many relation) read at most one row
    of it (#4326)? ``count`` / ``exists`` / ``first.x`` / ``last.x``, on the manager or
    on ``all``; ``all.N.x``; ``all.__len__``. A plain ``all`` (a loop, the rows printed)
    and ``all.<field>`` (a field of every row) read every row."""
    parts = remaining_path.split(".")
    if parts[0] == "all":
        parts = parts[1:]
        if not parts:
            return False
        if parts[0].isdigit() or parts == [_LENGTH_PATH]:
            return True
    return parts[0] in _LIGHT_HEADS


def _light_read_key(remaining_path: str) -> str:
    """The one query a light read (:func:`_is_light_relation_read`) runs per parent row:
    ``count``, ``all.count``, ``all.1``, ``all.__len__``, ``first`` ... Two paths with one
    key (``all.1.name`` / ``all.1.id``) share it; two keys are two queries."""
    parts = remaining_path.split(".")
    return ".".join(parts[:2]) if parts[0] == "all" else parts[0]


def _light_query_budget() -> int:
    from ..config import config

    budget = config.get("queryset_light_relation_max_parents", 1)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        return 0
    return int(budget)


def light_relation_parent_bound(queryset: Any) -> int:
    """How many parent rows ``queryset`` is sliced to, when that is at most
    ``queryset_light_relation_max_parents`` (#4326); ``0`` otherwise (unsliced, sliced
    past it, the setting ``0``). A relation it reads lightly may then be read per parent
    row instead of prefetched: :func:`analyze_queryset_optimization` decides, by counting
    the queries that would run."""
    budget = _light_query_budget()
    if not budget:
        return 0
    query = getattr(queryset, "query", None)
    high = getattr(query, "high_mark", None)
    low = getattr(query, "low_mark", 0) or 0
    if isinstance(high, bool) or not isinstance(high, int) or not isinstance(low, int):
        return 0
    parents = max(high - low, 0)
    return parents if parents <= budget else 0


class QueryOptimization:
    """Result of query analysis."""

    def __init__(self) -> None:
        self.select_related: Set[str] = set()
        self.prefetch_related: Set[str] = set()
        self.annotations: Dict[str, Any] = {}
        # Relations read only lightly (#4326), with the distinct per-row queries
        # (:func:`_light_read_key`) those reads run, and those read row by row.
        self._light: Dict[str, Set[str]] = {}
        self._heavy: Set[str] = set()

    def to_dict(self) -> Dict[str, List[str]]:
        """Convert to dictionary format."""
        return {
            "select_related": sorted(self.select_related),
            "prefetch_related": sorted(self.prefetch_related),
            "annotations": list(self.annotations.keys()),
        }


def analyze_queryset_optimization(
    model_class: type[models.Model],
    variable_paths: List[str],
    per_parent_light_reads: int = 0,
) -> QueryOptimization:
    """
    Analyze variable paths and determine optimal Django ORM optimization.

    Args:
        model_class: Django model class (e.g., Lease)
        variable_paths: List of dot-separated paths (e.g., ["property.name", "tenant.user.email"])
        per_parent_light_reads: The number of parent rows (a QuerySet sliced to a
            few, :func:`light_relation_parent_bound`; ``0`` off). A to-many relation
            every path reads only lightly (a method, an index, a length; #4326) is NOT
            prefetched when its per-row queries -- parents times the distinct light
            reads -- stay within ``queryset_light_relation_max_parents``. That setting
            caps the queries that replace the one prefetch query, so its default ``1``
            never runs more queries than the prefetch would.

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

    budget = _light_query_budget() if per_parent_light_reads else 0
    if budget:
        parents = int(per_parent_light_reads)
        for relation, reads in optimization._light.items():
            if relation in optimization._heavy or parents * len(reads) > budget:
                continue
            if not any(
                other.startswith(relation + "__") for other in optimization.prefetch_related
            ):
                optimization.prefetch_related.discard(relation)
    return optimization


def _note_to_many(optimization: QueryOptimization, django_path: str, remaining: str) -> None:
    if remaining and _is_light_relation_read(remaining):
        optimization._light.setdefault(django_path, set()).add(_light_read_key(remaining))
    else:
        optimization._heavy.add(django_path)


def _analyze_path(
    model_class: type[models.Model],
    path: str,
    optimization: QueryOptimization,
    prefix: str = "",
) -> None:
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

    # get_field() also accepts a ForeignKey's scalar attname (owner_id).
    # Only the relation name is legal in select_related; its ID is already
    # present on the row, including nullable and to_field relationships.
    if isinstance(field, (ForeignKey, OneToOneField)) and field_name != field.name:
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
        _note_to_many(optimization, django_path, remaining_path)

        # Continue analyzing nested path
        if remaining_path:
            related_model = field.related_model
            _analyze_path(related_model, remaining_path, optimization, django_path)

    elif field.is_relation and field.one_to_many:
        # Reverse ForeignKey (e.g., property.leases)
        # Use prefetch_related
        optimization.prefetch_related.add(django_path)
        _note_to_many(optimization, django_path, remaining_path)

        # Continue analyzing nested path
        if remaining_path:
            related_model = field.related_model
            _analyze_path(related_model, remaining_path, optimization, django_path)


def optimize_queryset(queryset: Any, optimization: QueryOptimization) -> Any:
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
