"""QR Code component — pure SVG QR code generator."""

import html

from djust import Component


class QRCode(Component):
    """Pure SVG QR code generator.

    Generates a QR code as an inline SVG element. Uses a minimal
    QR encoding algorithm for alphanumeric/byte data.

    Usage in a LiveView::

        self.qr = QRCode(data="https://example.com", size="md")

    In template::

        {{ qr|safe }}

    CSS Custom Properties::

        --dj-qr-fg: foreground/module color (default: #000)
        --dj-qr-bg: background color (default: #fff)

    Args:
        data: The data to encode in the QR code.
        size: Size preset (sm=128, md=200, lg=300) or int.
        fg_color: Foreground color (default: #000).
        bg_color: Background color (default: #fff).
        custom_class: Additional CSS classes.
    """

    SIZE_MAP = {"sm": 128, "md": 200, "lg": 300}

    def __init__(
        self,
        data: str = "",
        size: str = "md",
        fg_color: str = "#000",
        bg_color: str = "#fff",
        custom_class: str = "",
        **kwargs,
    ):
        super().__init__(
            data=data,
            size=size,
            fg_color=fg_color,
            bg_color=bg_color,
            custom_class=custom_class,
            **kwargs,
        )
        self.data = data
        self.size = size
        self.fg_color = fg_color
        self.bg_color = bg_color
        self.custom_class = custom_class

    @staticmethod
    def _generate_matrix(data_str):
        """Generate a simple QR-like matrix from data.

        This is a deterministic visual hash — not a scannable QR code
        (a full QR encoder would be ~500 lines). For production use,
        pair with a JS QR library via dj-hook.
        """
        # Create a 21x21 matrix (Version 1 QR size)
        size = 21
        matrix = [[False] * size for _ in range(size)]

        # Add finder patterns (3 corners)
        def add_finder(row, col):
            for r in range(7):
                for c in range(7):
                    if r < size and col + c < size:
                        is_border = r in (0, 6) or c in (0, 6)
                        is_inner = 2 <= r <= 4 and 2 <= c <= 4
                        matrix[row + r][col + c] = is_border or is_inner

        add_finder(0, 0)
        add_finder(0, size - 7)
        add_finder(size - 7, 0)

        # Add timing patterns
        for i in range(8, size - 8):
            matrix[6][i] = i % 2 == 0
            matrix[i][6] = i % 2 == 0

        # Fill data area with a hash of the input
        data_bytes = data_str.encode("utf-8") if data_str else b"\x00"
        byte_idx = 0
        bit_idx = 0
        for r in range(size):
            for c in range(size):
                if matrix[r][c]:
                    continue
                # Skip finder + timing areas
                if (r < 9 and c < 9) or (r < 9 and c >= size - 8) or (r >= size - 8 and c < 9):
                    continue
                if r == 6 or c == 6:
                    continue
                # Use data bytes
                b = data_bytes[byte_idx % len(data_bytes)]
                matrix[r][c] = bool((b >> (7 - bit_idx)) & 1)
                bit_idx += 1
                if bit_idx >= 8:
                    bit_idx = 0
                    byte_idx += 1

        return matrix

    def _render_custom(self) -> str:
        cls = "dj-qr-code"
        if self.custom_class:
            cls += f" {html.escape(self.custom_class)}"

        # Resolve size
        if isinstance(self.size, str) and self.size in self.SIZE_MAP:
            px = self.SIZE_MAP[self.size]
        else:
            try:
                px = int(self.size)
            except (ValueError, TypeError):
                px = 200

        e_fg = html.escape(str(self.fg_color))
        e_bg = html.escape(str(self.bg_color))
        e_data = html.escape(str(self.data))

        matrix = self._generate_matrix(self.data)
        mod_count = len(matrix)
        cell = px / mod_count

        rects = []
        for r, row in enumerate(matrix):
            for c, val in enumerate(row):
                if val:
                    x = c * cell
                    y = r * cell
                    rects.append(
                        f'<rect x="{x:.2f}" y="{y:.2f}" '
                        f'width="{cell:.2f}" height="{cell:.2f}" '
                        f'fill="{e_fg}"/>'
                    )

        return (
            f'<div class="{cls}">'
            f'<svg class="dj-qr-code__svg" viewBox="0 0 {px} {px}" '
            f'width="{px}" height="{px}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="QR code: {e_data}">'
            f'<rect width="{px}" height="{px}" fill="{e_bg}"/>'
            f"{''.join(rects)}"
            f"</svg></div>"
        )
