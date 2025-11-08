"""
Data components for Django Rust Live.

Components for displaying and managing data.
"""

from .table import TableComponent
from .pagination import PaginationComponent

__all__ = [
    'TableComponent',
    'PaginationComponent',
]
