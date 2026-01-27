"""
Pagination component for djust.

Provides pagination controls for navigating through data sets.
"""

from typing import Dict, Any
from ..base import LiveComponent


class PaginationComponent(LiveComponent):
    """
    Pagination component for data navigation.

    Displays page controls with previous/next buttons, page numbers,
    and optional jump-to-page functionality.

    Usage:
        from djust.components import PaginationComponent

        # In your LiveView:
        def mount(self, request):
            self.pagination = PaginationComponent(
                current_page=1,
                total_pages=10,
                on_page_change="handle_page_change"
            )

        def handle_page_change(self, page: int):
            self.pagination.go_to_page(page)
            # Load data for new page...

        # In template:
        {{ pagination.render }}
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize pagination state"""
        self.current_page = kwargs.get("current_page", 1)
        self.total_pages = kwargs.get("total_pages", 1)
        self.total_items = kwargs.get("total_items", None)
        self.items_per_page = kwargs.get("items_per_page", 10)
        self.max_visible_pages = kwargs.get("max_visible_pages", 5)  # Max page numbers to show
        self.show_first_last = kwargs.get("show_first_last", True)
        self.show_prev_next = kwargs.get("show_prev_next", True)
        self.show_page_info = kwargs.get("show_page_info", True)  # "Showing X-Y of Z items"
        self.size = kwargs.get("size", "md")  # sm, md, lg
        self.alignment = kwargs.get("alignment", "center")  # left, center, right
        self.on_page_change = kwargs.get("on_page_change", None)

        # Calculate total_pages from total_items if not provided
        if self.total_items and not kwargs.get("total_pages"):
            self.total_pages = (self.total_items + self.items_per_page - 1) // self.items_per_page

    def get_context(self) -> Dict[str, Any]:
        """Get pagination context"""
        return {
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "total_items": self.total_items,
            "items_per_page": self.items_per_page,
            "max_visible_pages": self.max_visible_pages,
        }

    def go_to_page(self, page: int):
        """Navigate to a specific page"""
        if 1 <= page <= self.total_pages:
            self.current_page = page
            self.trigger_update()

    def next_page(self):
        """Go to next page"""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self.trigger_update()

    def previous_page(self):
        """Go to previous page"""
        if self.current_page > 1:
            self.current_page -= 1
            self.trigger_update()

    def first_page(self):
        """Go to first page"""
        if self.current_page != 1:
            self.current_page = 1
            self.trigger_update()

    def last_page(self):
        """Go to last page"""
        if self.current_page != self.total_pages:
            self.current_page = self.total_pages
            self.trigger_update()

    def _get_visible_pages(self):
        """Calculate which page numbers to display"""
        if self.total_pages <= self.max_visible_pages:
            return list(range(1, self.total_pages + 1))

        # Calculate range around current page
        half = self.max_visible_pages // 2
        start = max(1, self.current_page - half)
        end = min(self.total_pages, start + self.max_visible_pages - 1)

        # Adjust start if we're near the end
        if end - start < self.max_visible_pages - 1:
            start = max(1, end - self.max_visible_pages + 1)

        return list(range(start, end + 1))

    def _get_page_info(self) -> str:
        """Get page info text (e.g., 'Showing 1-10 of 100 items')"""
        if not self.total_items:
            return f"Page {self.current_page} of {self.total_pages}"

        start = (self.current_page - 1) * self.items_per_page + 1
        end = min(self.current_page * self.items_per_page, self.total_items)
        return f"Showing {start}-{end} of {self.total_items} items"

    def render(self) -> str:
        """Render pagination with inline HTML"""
        from django.utils.safestring import mark_safe
        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 pagination"""
        size_map = {"sm": "pagination-sm", "md": "", "lg": "pagination-lg"}
        size_class = size_map.get(self.size, "")

        align_map = {"left": "", "center": "justify-content-center", "right": "justify-content-end"}
        align_class = align_map.get(self.alignment, "justify-content-center")

        html = f'<div id="{self.component_id}">'

        # Page info
        if self.show_page_info:
            html += (
                f'<div class="text-muted mb-2 text-{self.alignment}">{self._get_page_info()}</div>'
            )

        # Pagination controls
        pagination_class = f"pagination {size_class} {align_class}".strip()
        html += f'<nav aria-label="Page navigation"><ul class="{pagination_class}">'

        # First page button
        if self.show_first_last:
            disabled = " disabled" if self.current_page == 1 else ""
            click_attr = ' dj-click="first_page"' if self.current_page != 1 else ""
            html += f'<li class="page-item{disabled}"><a class="page-link" href="#"{click_attr}>&laquo;&laquo;</a></li>'

        # Previous page button
        if self.show_prev_next:
            disabled = " disabled" if self.current_page == 1 else ""
            click_attr = ' dj-click="previous_page"' if self.current_page != 1 else ""
            html += f'<li class="page-item{disabled}"><a class="page-link" href="#"{click_attr}>&laquo;</a></li>'

        # Page numbers
        visible_pages = self._get_visible_pages()

        # Show ellipsis before if needed
        if visible_pages[0] > 1:
            html += '<li class="page-item disabled"><a class="page-link" href="#">...</a></li>'

        for page in visible_pages:
            active = " active" if page == self.current_page else ""
            click_attr = (
                f' dj-click="go_to_page" data-page="{page}"' if page != self.current_page else ""
            )
            html += f'<li class="page-item{active}"><a class="page-link" href="#"{click_attr}>{page}</a></li>'

        # Show ellipsis after if needed
        if visible_pages[-1] < self.total_pages:
            html += '<li class="page-item disabled"><a class="page-link" href="#">...</a></li>'

        # Next page button
        if self.show_prev_next:
            disabled = " disabled" if self.current_page == self.total_pages else ""
            click_attr = ' dj-click="next_page"' if self.current_page != self.total_pages else ""
            html += f'<li class="page-item{disabled}"><a class="page-link" href="#"{click_attr}>&raquo;</a></li>'

        # Last page button
        if self.show_first_last:
            disabled = " disabled" if self.current_page == self.total_pages else ""
            click_attr = ' dj-click="last_page"' if self.current_page != self.total_pages else ""
            html += f'<li class="page-item{disabled}"><a class="page-link" href="#"{click_attr}>&raquo;&raquo;</a></li>'

        html += "</ul></nav></div>"
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS pagination"""
        align_map = {"left": "justify-start", "center": "justify-center", "right": "justify-end"}
        align_class = align_map.get(self.alignment, "justify-center")

        size_map = {
            "sm": "px-2 py-1 text-sm",
            "md": "px-3 py-2 text-base",
            "lg": "px-4 py-3 text-lg",
        }
        size_class = size_map.get(self.size, size_map["md"])

        html = f'<div id="{self.component_id}">'

        # Page info
        if self.show_page_info:
            info_align = (
                "text-left"
                if self.alignment == "left"
                else "text-right"
                if self.alignment == "right"
                else "text-center"
            )
            html += f'<div class="text-gray-600 mb-2 {info_align}">{self._get_page_info()}</div>'

        # Pagination controls
        html += f'<nav class="flex {align_class}" aria-label="Pagination">'
        html += '<ul class="inline-flex items-center -space-x-px">'

        # First page button
        if self.show_first_last:
            disabled_class = (
                "opacity-50 cursor-not-allowed" if self.current_page == 1 else "hover:bg-gray-100"
            )
            click_attr = ' dj-click="first_page"' if self.current_page != 1 else ""
            html += f"""<li>
                <a href="#" class="{size_class} ml-0 leading-tight text-gray-500 bg-white border border-gray-300 rounded-l-lg {disabled_class}"{click_attr}>
                    &laquo;&laquo;
                </a>
            </li>"""

        # Previous page button
        if self.show_prev_next:
            disabled_class = (
                "opacity-50 cursor-not-allowed" if self.current_page == 1 else "hover:bg-gray-100"
            )
            click_attr = ' dj-click="previous_page"' if self.current_page != 1 else ""
            first_class = "rounded-l-lg" if not self.show_first_last else ""
            html += f"""<li>
                <a href="#" class="{size_class} leading-tight text-gray-500 bg-white border border-gray-300 {first_class} {disabled_class}"{click_attr}>
                    &laquo;
                </a>
            </li>"""

        # Page numbers
        visible_pages = self._get_visible_pages()

        # Show ellipsis before if needed
        if visible_pages[0] > 1:
            html += f'<li><span class="{size_class} leading-tight text-gray-500 bg-white border border-gray-300">...</span></li>'

        for page in visible_pages:
            if page == self.current_page:
                active_class = "z-10 bg-blue-50 border-blue-300 text-blue-600"
            else:
                active_class = (
                    "bg-white border-gray-300 text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                )

            click_attr = (
                f' dj-click="go_to_page" data-page="{page}"' if page != self.current_page else ""
            )
            html += f'<li><a href="#" class="{size_class} leading-tight border {active_class}"{click_attr}>{page}</a></li>'

        # Show ellipsis after if needed
        if visible_pages[-1] < self.total_pages:
            html += f'<li><span class="{size_class} leading-tight text-gray-500 bg-white border border-gray-300">...</span></li>'

        # Next page button
        if self.show_prev_next:
            disabled_class = (
                "opacity-50 cursor-not-allowed"
                if self.current_page == self.total_pages
                else "hover:bg-gray-100"
            )
            click_attr = ' dj-click="next_page"' if self.current_page != self.total_pages else ""
            last_class = "rounded-r-lg" if not self.show_first_last else ""
            html += f"""<li>
                <a href="#" class="{size_class} leading-tight text-gray-500 bg-white border border-gray-300 {last_class} {disabled_class}"{click_attr}>
                    &raquo;
                </a>
            </li>"""

        # Last page button
        if self.show_first_last:
            disabled_class = (
                "opacity-50 cursor-not-allowed"
                if self.current_page == self.total_pages
                else "hover:bg-gray-100"
            )
            click_attr = ' dj-click="last_page"' if self.current_page != self.total_pages else ""
            html += f"""<li>
                <a href="#" class="{size_class} leading-tight text-gray-500 bg-white border border-gray-300 rounded-r-lg {disabled_class}"{click_attr}>
                    &raquo;&raquo;
                </a>
            </li>"""

        html += "</ul></nav></div>"
        return html

    def _render_plain(self) -> str:
        """Render plain HTML pagination"""
        html = f'<div class="pagination-container text-{self.alignment}" id="{self.component_id}">'

        # Page info
        if self.show_page_info:
            html += f'<div class="pagination-info">{self._get_page_info()}</div>'

        # Pagination controls
        html += '<div class="pagination">'

        # First page button
        if self.show_first_last:
            disabled = " disabled" if self.current_page == 1 else ""
            click_attr = ' dj-click="first_page"' if self.current_page != 1 else ""
            html += f'<a href="#" class="pagination-link{disabled}"{click_attr}>&laquo;&laquo;</a>'

        # Previous page button
        if self.show_prev_next:
            disabled = " disabled" if self.current_page == 1 else ""
            click_attr = ' dj-click="previous_page"' if self.current_page != 1 else ""
            html += f'<a href="#" class="pagination-link{disabled}"{click_attr}>&laquo;</a>'

        # Page numbers
        visible_pages = self._get_visible_pages()

        # Show ellipsis before if needed
        if visible_pages[0] > 1:
            html += '<span class="pagination-ellipsis">...</span>'

        for page in visible_pages:
            active = " active" if page == self.current_page else ""
            click_attr = (
                f' dj-click="go_to_page" data-page="{page}"' if page != self.current_page else ""
            )
            html += f'<a href="#" class="pagination-link{active}"{click_attr}>{page}</a>'

        # Show ellipsis after if needed
        if visible_pages[-1] < self.total_pages:
            html += '<span class="pagination-ellipsis">...</span>'

        # Next page button
        if self.show_prev_next:
            disabled = " disabled" if self.current_page == self.total_pages else ""
            click_attr = ' dj-click="next_page"' if self.current_page != self.total_pages else ""
            html += f'<a href="#" class="pagination-link{disabled}"{click_attr}>&raquo;</a>'

        # Last page button
        if self.show_first_last:
            disabled = " disabled" if self.current_page == self.total_pages else ""
            click_attr = ' dj-click="last_page"' if self.current_page != self.total_pages else ""
            html += f'<a href="#" class="pagination-link{disabled}"{click_attr}>&raquo;&raquo;</a>'

        html += "</div></div>"
        return html
