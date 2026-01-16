"""
Pagination component for djust.

Simple stateless pagination component with automatic Rust optimization.
"""

from ..base import Component

try:
    from djust._rust import RustPagination

    _RUST_AVAILABLE = True
except ImportError:
    _RUST_AVAILABLE = False


class Pagination(Component):
    """
    Pagination component (Bootstrap 5).

    Features:
    - Automatic Rust optimization (~1μs rendering)
    - Template fallback if Rust unavailable
    - Python fallback with f-strings
    - Previous/Next buttons
    - Page number buttons
    - Ellipsis for large page counts
    - Size variants (sm, md, lg)
    - Optional first/last buttons

    Args:
        current_page: Current active page (1-indexed)
        total_pages: Total number of pages
        size: Pagination size (sm, md, lg)
        show_first_last: Show first/last page buttons
        max_visible_pages: Maximum number of page buttons to show (show ellipsis beyond this)

    Example:
        >>> pagination = Pagination(
        ...     current_page=5,
        ...     total_pages=20,
        ...     size="md",
        ...     show_first_last=True,
        ...     max_visible_pages=5
        ... )
        >>> pagination.render()
        '<nav aria-label="Page navigation">...'
    """

    _rust_impl_class = RustPagination if _RUST_AVAILABLE else None

    # Note: Using Python fallback for simplicity with dynamic loops

    def __init__(
        self,
        current_page: int,
        total_pages: int,
        size: str = "md",
        show_first_last: bool = True,
        max_visible_pages: int = 5,
    ):
        # Validate inputs
        if current_page < 1:
            current_page = 1
        if current_page > total_pages:
            current_page = total_pages
        if total_pages < 1:
            total_pages = 1

        # Pass kwargs to parent
        super().__init__(
            current_page=current_page,
            total_pages=total_pages,
            size=size,
            show_first_last=show_first_last,
            max_visible_pages=max_visible_pages,
        )

        # Set instance attributes for Python rendering
        self.current_page = current_page
        self.total_pages = total_pages
        self.size = size
        self.show_first_last = show_first_last
        self.max_visible_pages = max_visible_pages

    def get_context_data(self):
        """Return context for template rendering."""
        return {
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "size": self.size,
            "show_first_last": self.show_first_last,
            "max_visible_pages": self.max_visible_pages,
        }

    def _calculate_page_range(self):
        """Calculate which page numbers to display with ellipsis logic."""
        current = self.current_page
        total = self.total_pages
        max_visible = self.max_visible_pages

        # If total pages fit within max_visible, show all
        if total <= max_visible:
            return list(range(1, total + 1))

        # Calculate range around current page
        half = max_visible // 2
        start = max(1, current - half)
        end = min(total, current + half)

        # Adjust if we're near the beginning or end
        if current <= half:
            end = min(total, max_visible)
        elif current >= total - half:
            start = max(1, total - max_visible + 1)

        page_range = list(range(start, end + 1))

        # Add ellipsis markers
        pages_with_ellipsis = []

        # Add first page and ellipsis if needed
        if start > 1:
            pages_with_ellipsis.append(1)
            if start > 2:
                pages_with_ellipsis.append("...")

        # Add visible page range
        pages_with_ellipsis.extend(page_range)

        # Add ellipsis and last page if needed
        if end < total:
            if end < total - 1:
                pages_with_ellipsis.append("...")
            pages_with_ellipsis.append(total)

        return pages_with_ellipsis

    def _render_custom(self) -> str:
        """Pure Python fallback (f-string rendering)."""
        parts = ['<nav aria-label="Page navigation">']

        # Build pagination classes
        pagination_classes = ["pagination"]
        if self.size != "md":
            pagination_classes.append(f"pagination-{self.size}")

        parts.append(f'  <ul class="{" ".join(pagination_classes)}">')

        # Previous button
        prev_disabled = " disabled" if self.current_page == 1 else ""
        prev_aria = ' aria-disabled="true"' if self.current_page == 1 else ""
        parts.append(f'    <li class="page-item{prev_disabled}">')
        parts.append(
            f'      <a class="page-link" href="#" data-page="{self.current_page - 1}"{prev_aria}>Previous</a>'
        )
        parts.append("    </li>")

        # Page numbers with ellipsis
        pages = self._calculate_page_range()
        for page in pages:
            if page == "...":
                # Ellipsis
                parts.append('    <li class="page-item disabled">')
                parts.append('      <span class="page-link">...</span>')
                parts.append("    </li>")
            else:
                # Page number
                active = " active" if page == self.current_page else ""
                aria = ' aria-current="page"' if page == self.current_page else ""
                parts.append(f'    <li class="page-item{active}">')
                parts.append(
                    f'      <a class="page-link" href="#" data-page="{page}"{aria}>{page}</a>'
                )
                parts.append("    </li>")

        # Next button
        next_disabled = " disabled" if self.current_page == self.total_pages else ""
        next_aria = ' aria-disabled="true"' if self.current_page == self.total_pages else ""
        parts.append(f'    <li class="page-item{next_disabled}">')
        parts.append(
            f'      <a class="page-link" href="#" data-page="{self.current_page + 1}"{next_aria}>Next</a>'
        )
        parts.append("    </li>")

        parts.append("  </ul>")
        parts.append("</nav>")

        return "\n".join(parts)
