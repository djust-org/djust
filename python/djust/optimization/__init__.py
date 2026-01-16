"""
djust Optimization Modules

- Query Optimization: Automatically optimizes Django QuerySets based on template access patterns
- Fingerprint Optimization: Tracks state changes to minimize re-rendering and data transfer
"""

from .query_optimizer import analyze_queryset_optimization, optimize_queryset
from .codegen import generate_serializer_code, compile_serializer, get_serializer_source
from .cache import SerializerCache
from .fingerprint import (
    StateFingerprint,
    SectionCache,
    IncrementalStateSync,
    FingerprintMixin,
    fingerprint,
)

__all__ = [
    # Query optimization
    "analyze_queryset_optimization",
    "optimize_queryset",
    "generate_serializer_code",
    "compile_serializer",
    "get_serializer_source",
    "SerializerCache",
    # Fingerprint optimization
    "StateFingerprint",
    "SectionCache",
    "IncrementalStateSync",
    "FingerprintMixin",
    "fingerprint",
]
