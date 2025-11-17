"""
Dependency management system for components.

DEPRECATED: This module has been moved to djust.components.dependencies.
Importing from here is for backwards compatibility only.

New code should use:
    from djust.components.dependencies import DependencyManager, Dependency, DEPENDENCY_REGISTRY
"""

# Re-export from djust core for backwards compatibility
from djust.components.dependencies import (
    DependencyManager,
    Dependency,
    DEPENDENCY_REGISTRY,
)

__all__ = ['DependencyManager', 'Dependency', 'DEPENDENCY_REGISTRY']
