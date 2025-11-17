"""
djust Query Optimization

Automatically optimizes Django QuerySets based on template variable access patterns.
"""

from .query_optimizer import analyze_queryset_optimization, optimize_queryset
from .codegen import generate_serializer_code, compile_serializer, get_serializer_source

__all__ = [
    "analyze_queryset_optimization",
    "optimize_queryset",
    "generate_serializer_code",
    "compile_serializer",
    "get_serializer_source",
]
