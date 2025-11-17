"""
djust Query Optimization

Automatically optimizes Django QuerySets based on template variable access patterns.
"""

from .query_optimizer import analyze_queryset_optimization, optimize_queryset

__all__ = ["analyze_queryset_optimization", "optimize_queryset"]
