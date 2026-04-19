"""Diff Viewer component — side-by-side or unified text diff."""

import html

from djust import Component


class DiffViewer(Component):
    """Side-by-side or unified text diff viewer.

    Usage in a LiveView::

        self.diff = DiffViewer(
            old="Hello world\\nFoo bar",
            new="Hello world\\nFoo baz\\nNew line",
        )

    In template::

        {{ diff|safe }}

    Args:
        old: Original text
        new: Modified text
        mode: "split" or "unified" (default: "split")
        title_old: Label for old pane (default: "Original")
        title_new: Label for new pane (default: "Modified")
        show_line_numbers: Show line numbers (default: True)
        custom_class: Additional CSS classes
    """

    def __init__(
        self,
        old: str = "",
        new: str = "",
        mode: str = "split",
        title_old: str = "Original",
        title_new: str = "Modified",
        show_line_numbers: bool = True,
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            old=old,
            new=new,
            mode=mode,
            title_old=title_old,
            title_new=title_new,
            show_line_numbers=show_line_numbers,
            custom_class=custom_class,
            **kwargs,
        )
        self.old = str(old) if old else ""
        self.new = str(new) if new else ""
        self.mode = mode if mode in ("split", "unified") else "split"
        self.title_old = title_old
        self.title_new = title_new
        self.show_line_numbers = show_line_numbers
        self.custom_class = custom_class

    @staticmethod
    def _compute_diff(old_lines, new_lines):
        """Simple LCS-based diff producing (tag, old_line, new_line) tuples."""
        m, n = len(old_lines), len(new_lines)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if old_lines[i - 1] == new_lines[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

        result = []
        i, j = m, n
        while i > 0 or j > 0:
            if i > 0 and j > 0 and old_lines[i - 1] == new_lines[j - 1]:
                result.append(("equal", old_lines[i - 1], new_lines[j - 1]))
                i -= 1
                j -= 1
            elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
                result.append(("insert", None, new_lines[j - 1]))
                j -= 1
            else:
                result.append(("delete", old_lines[i - 1], None))
                i -= 1
        result.reverse()
        return result

    def _render_custom(self) -> str:
        classes = ["dj-diff"]
        if self.mode == "unified":
            classes.append("dj-diff--unified")
        else:
            classes.append("dj-diff--split")
        if self.custom_class:
            classes.append(html.escape(self.custom_class))
        class_str = " ".join(classes)

        old_lines = self.old.split("\n") if self.old else []
        new_lines = self.new.split("\n") if self.new else []
        ops = self._compute_diff(old_lines, new_lines)

        if self.mode == "unified":
            return self._render_unified(class_str, ops)
        return self._render_split(class_str, ops)

    def _render_split(self, class_str, ops):
        e_title_old = html.escape(str(self.title_old))
        e_title_new = html.escape(str(self.title_new))

        old_rows = []
        new_rows = []
        old_num = 0
        new_num = 0

        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_o = (
                    f'<span class="dj-diff__num">{old_num}</span>' if self.show_line_numbers else ""
                )
                num_n = (
                    f'<span class="dj-diff__num">{new_num}</span>' if self.show_line_numbers else ""
                )
                old_rows.append(
                    f'<div class="dj-diff__line">{num_o}'
                    f'<span class="dj-diff__text">{html.escape(old_line)}</span></div>'
                )
                new_rows.append(
                    f'<div class="dj-diff__line">{num_n}'
                    f'<span class="dj-diff__text">{html.escape(new_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = (
                    f'<span class="dj-diff__num">{old_num}</span>' if self.show_line_numbers else ""
                )
                old_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}'
                    f'<span class="dj-diff__text">{html.escape(old_line)}</span></div>'
                )
                new_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
            elif tag == "insert":
                new_num += 1
                num_html = (
                    f'<span class="dj-diff__num">{new_num}</span>' if self.show_line_numbers else ""
                )
                old_rows.append('<div class="dj-diff__line dj-diff__line--empty"></div>')
                new_rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}'
                    f'<span class="dj-diff__text">{html.escape(new_line)}</span></div>'
                )

        return (
            f'<div class="{class_str}">'
            f'<div class="dj-diff__pane dj-diff__pane--old">'
            f'<div class="dj-diff__pane-header">{e_title_old}</div>'
            f"{''.join(old_rows)}</div>"
            f'<div class="dj-diff__pane dj-diff__pane--new">'
            f'<div class="dj-diff__pane-header">{e_title_new}</div>'
            f"{''.join(new_rows)}</div></div>"
        )

    def _render_unified(self, class_str, ops):
        rows = []
        old_num = 0
        new_num = 0

        for tag, old_line, new_line in ops:
            if tag == "equal":
                old_num += 1
                new_num += 1
                num_html = ""
                if self.show_line_numbers:
                    num_html = (
                        f'<span class="dj-diff__num">{old_num}</span>'
                        f'<span class="dj-diff__num">{new_num}</span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line">{num_html}'
                    f'<span class="dj-diff__marker"> </span>'
                    f'<span class="dj-diff__text">{html.escape(old_line)}</span></div>'
                )
            elif tag == "delete":
                old_num += 1
                num_html = ""
                if self.show_line_numbers:
                    num_html = (
                        f'<span class="dj-diff__num">{old_num}</span>'
                        f'<span class="dj-diff__num"></span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--del">{num_html}'
                    f'<span class="dj-diff__marker">-</span>'
                    f'<span class="dj-diff__text">{html.escape(old_line)}</span></div>'
                )
            elif tag == "insert":
                new_num += 1
                num_html = ""
                if self.show_line_numbers:
                    num_html = (
                        f'<span class="dj-diff__num"></span>'
                        f'<span class="dj-diff__num">{new_num}</span>'
                    )
                rows.append(
                    f'<div class="dj-diff__line dj-diff__line--add">{num_html}'
                    f'<span class="dj-diff__marker">+</span>'
                    f'<span class="dj-diff__text">{html.escape(new_line)}</span></div>'
                )

        return f'<div class="{class_str}"><div class="dj-diff__unified">{"".join(rows)}</div></div>'
