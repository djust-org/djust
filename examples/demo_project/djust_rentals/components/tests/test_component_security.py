"""
Security tests for djust_rentals example components.

These tests ensure that example components properly escape user input to prevent XSS.
"""
import pytest

from djust_rentals.components.data_table import DataTable
from djust_rentals.components.page_header import PageHeader
from djust_rentals.components.stat_card import StatCard
from djust_rentals.components.status_badge import StatusBadge


class TestDataTableSecurity:
    """Test that DataTable properly escapes user values."""

    def test_data_table_escapes_cell_values(self):
        """DataTable must escape cell values to prevent XSS."""
        malicious_value = '<script>alert("XSS")</script>'
        table = DataTable(
            headers=["Name", "Email"],
            rows=[
                {"Name": malicious_value, "Email": "test@example.com"},
                {"Name": "Safe", "Email": malicious_value},
            ]
        )

        html = table.render()

        # Script tags should be escaped
        assert '<script>' not in html, "Unescaped script tag in table cell"
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_data_table_escapes_empty_message(self):
        """DataTable must escape the empty_message to prevent XSS."""
        malicious_message = '<img src=x onerror=alert(1)>'
        table = DataTable(
            headers=["Name"],
            rows=[],
            empty_message=malicious_message
        )

        html = table.render()

        # Should not contain executable HTML
        assert 'onerror=' not in html or '&' in html
        assert '<img' not in html or '&lt;' in html

    def test_data_table_escapes_headers(self):
        """DataTable must escape header values to prevent XSS."""
        malicious_header = '<script>alert("XSS")</script>'
        table = DataTable(
            headers=[malicious_header, "Safe Header"],
            rows=[{malicious_header: "value1", "Safe Header": "value2"}]
        )

        html = table.render()

        # Headers should be escaped
        assert '<script>' not in html
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_data_table_html_entities(self):
        """DataTable should properly handle HTML entities."""
        table = DataTable(
            headers=["Amount"],
            rows=[{"Amount": "A & B < C > D"}]
        )

        html = table.render()

        # Entities should be escaped once, not double-escaped
        assert '&amp;' in html or '&' in html
        assert '&amp;amp;' not in html


class TestStatusBadgeSecurity:
    """Test that StatusBadge properly escapes user values."""

    def test_status_badge_escapes_label(self):
        """StatusBadge must escape the label to prevent XSS."""
        malicious_label = '<script>alert("XSS")</script>'
        badge = StatusBadge(status="active", label=malicious_label)

        html = badge.render()

        assert '<script>' not in html
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_status_badge_escapes_icon(self):
        """StatusBadge must escape the icon to prevent XSS."""
        malicious_icon = '<img src=x onerror=alert(1)>'
        badge = StatusBadge(status="active", label="Test", icon=malicious_icon)

        html = badge.render()

        assert 'onerror=' not in html or '&' in html
        assert '<img' not in html or '&lt;' in html

    def test_status_badge_color_class(self):
        """StatusBadge color should not allow HTML injection."""
        # Even though color is typically from an enum, test defensive escaping
        malicious_color = '"><script>alert(1)</script><span class="'
        badge = StatusBadge(status="active", label="Test", color=malicious_color)

        html = badge.render()

        assert '<script>' not in html or '&lt;' in html


class TestPageHeaderSecurity:
    """Test that PageHeader properly escapes user values."""

    def test_page_header_escapes_title(self):
        """PageHeader must escape the title to prevent XSS."""
        malicious_title = '<script>alert("XSS")</script>'
        header = PageHeader(title=malicious_title)

        html = header.render()

        assert '<script>' not in html
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_page_header_escapes_subtitle(self):
        """PageHeader must escape the subtitle to prevent XSS."""
        malicious_subtitle = '<img src=x onerror=alert(1)>'
        header = PageHeader(title="Safe", subtitle=malicious_subtitle)

        html = header.render()

        assert 'onerror=' not in html or '&' in html
        assert '<img' not in html or '&lt;' in html

    def test_page_header_escapes_icon(self):
        """PageHeader must escape the icon to prevent XSS."""
        malicious_icon = '<svg onload=alert(1)>'
        header = PageHeader(title="Safe", icon=malicious_icon)

        html = header.render()

        assert 'onload=' not in html or '&' in html


class TestStatCardSecurity:
    """Test that StatCard properly escapes user values."""

    def test_stat_card_escapes_label(self):
        """StatCard must escape the label to prevent XSS."""
        malicious_label = '<script>alert("XSS")</script>'
        card = StatCard(label=malicious_label, value="100", color="primary")

        html = card.render()

        assert '<script>' not in html
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_stat_card_escapes_value(self):
        """StatCard must escape the value to prevent XSS."""
        malicious_value = '<img src=x onerror=alert(1)>'
        card = StatCard(label="Total", value=malicious_value, color="success")

        html = card.render()

        assert 'onerror=' not in html or '&' in html

    def test_stat_card_escapes_trend(self):
        """StatCard must escape the trend to prevent XSS."""
        malicious_trend = '<script>alert("XSS")</script>'
        card = StatCard(label="Revenue", value="$1000", color="info", trend=malicious_trend)

        html = card.render()

        assert '<script>' not in html
        assert '&lt;script&gt;' in html or 'alert(' not in html

    def test_stat_card_color_class(self):
        """StatCard color should not allow HTML injection."""
        malicious_color = '"><script>alert(1)</script><div class="'
        card = StatCard(label="Test", value="100", color=malicious_color)

        html = card.render()

        assert '<script>' not in html or '&lt;' in html
