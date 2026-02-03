"""
Admin Dashboard Widgets

Pre-built LiveView components for common admin dashboard patterns:
- Stats widgets with auto-refresh
- Charts that update in real-time
- Activity feeds
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from django.db.models import QuerySet
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)


@dataclass
class AdminWidget:
    """
    Base class for admin dashboard widgets.

    Widgets are self-contained components that can be rendered in admin templates
    and optionally auto-refresh.
    """

    widget_id: str
    title: str = ""
    refresh_interval: Optional[int] = None  # milliseconds
    css_class: str = ""
    template_name: str = "djust/admin/widgets/base.html"

    def get_context(self) -> Dict[str, Any]:
        """Get context data for widget rendering."""
        return {
            "widget_id": self.widget_id,
            "title": self.title,
            "refresh_interval": self.refresh_interval,
            "css_class": self.css_class,
        }

    def render(self) -> str:
        """Render the widget to HTML."""
        return render_to_string(self.template_name, self.get_context())

    def __str__(self) -> str:
        return self.render()


@dataclass
class AdminStatsWidget(AdminWidget):
    """
    Stats widget showing a numeric value with optional trend indicator.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.revenue_widget = AdminStatsWidget(
                    widget_id='revenue',
                    title='Total Revenue',
                    value=Order.objects.aggregate(Sum('total'))['total__sum'] or 0,
                    prefix='$',
                    trend=12.5,  # Percentage change
                    trend_label='vs last month',
                    color='green',
                    refresh_interval=30000,  # Refresh every 30s
                )

    Template:
        {{ revenue_widget }}
    """

    value: Union[int, float, str] = 0
    prefix: str = ""
    suffix: str = ""
    trend: Optional[float] = None  # Percentage change (+/-)
    trend_label: str = ""
    color: str = "blue"  # blue, green, red, yellow, purple
    icon: str = ""  # Icon class (e.g., 'fas fa-dollar-sign')
    template_name: str = "djust/admin/widgets/stats.html"

    def get_context(self) -> Dict[str, Any]:
        context = super().get_context()
        context.update(
            {
                "value": self.value,
                "prefix": self.prefix,
                "suffix": self.suffix,
                "trend": self.trend,
                "trend_label": self.trend_label,
                "trend_positive": self.trend is not None and self.trend >= 0,
                "color": self.color,
                "icon": self.icon,
            }
        )
        return context


@dataclass
class AdminChartWidget(AdminWidget):
    """
    Chart widget for displaying data visualizations.

    Supports line, bar, pie, and doughnut charts via Chart.js.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.sales_chart = AdminChartWidget(
                    widget_id='sales-chart',
                    title='Sales Over Time',
                    chart_type='line',
                    labels=['Jan', 'Feb', 'Mar', 'Apr', 'May'],
                    datasets=[{
                        'label': 'Sales',
                        'data': [100, 120, 115, 134, 168],
                        'borderColor': '#3b82f6',
                        'fill': False,
                    }],
                    refresh_interval=60000,
                )
    """

    chart_type: str = "line"  # line, bar, pie, doughnut, area
    labels: List[str] = field(default_factory=list)
    datasets: List[Dict[str, Any]] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    height: str = "300px"
    template_name: str = "djust/admin/widgets/chart.html"

    def get_context(self) -> Dict[str, Any]:
        context = super().get_context()

        # Default chart options
        default_options = {
            "responsive": True,
            "maintainAspectRatio": False,
            "plugins": {
                "legend": {"display": len(self.datasets) > 1},
            },
        }
        default_options.update(self.options)

        context.update(
            {
                "chart_type": self.chart_type,
                "labels": self.labels,
                "datasets": self.datasets,
                "options": default_options,
                "height": self.height,
            }
        )
        return context


@dataclass
class AdminActivityWidget(AdminWidget):
    """
    Activity feed widget showing recent actions/events.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.activity_widget = AdminActivityWidget(
                    widget_id='activity',
                    title='Recent Activity',
                    items=[
                        {
                            'icon': 'fas fa-user',
                            'message': 'New user registered',
                            'detail': 'john@example.com',
                            'time': '2 minutes ago',
                            'color': 'blue',
                        },
                        {
                            'icon': 'fas fa-shopping-cart',
                            'message': 'New order placed',
                            'detail': 'Order #1234',
                            'time': '5 minutes ago',
                            'color': 'green',
                        },
                    ],
                    max_items=10,
                    refresh_interval=15000,
                )
    """

    items: List[Dict[str, Any]] = field(default_factory=list)
    max_items: int = 10
    show_timestamps: bool = True
    template_name: str = "djust/admin/widgets/activity.html"

    def get_context(self) -> Dict[str, Any]:
        context = super().get_context()
        context.update(
            {
                "items": self.items[: self.max_items],
                "show_timestamps": self.show_timestamps,
                "has_more": len(self.items) > self.max_items,
            }
        )
        return context


@dataclass
class AdminTableWidget(AdminWidget):
    """
    Table widget for displaying tabular data with optional live updates.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.top_products = AdminTableWidget(
                    widget_id='top-products',
                    title='Top Products',
                    columns=['Product', 'Sales', 'Revenue'],
                    rows=[
                        ['Widget A', 150, '$1,500'],
                        ['Widget B', 120, '$1,200'],
                        ['Widget C', 100, '$1,000'],
                    ],
                    refresh_interval=30000,
                )
    """

    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    sortable: bool = False
    max_rows: int = 10
    template_name: str = "djust/admin/widgets/table.html"

    def get_context(self) -> Dict[str, Any]:
        context = super().get_context()
        context.update(
            {
                "columns": self.columns,
                "rows": self.rows[: self.max_rows],
                "sortable": self.sortable,
                "has_more": len(self.rows) > self.max_rows,
            }
        )
        return context


@dataclass
class AdminProgressWidget(AdminWidget):
    """
    Progress widget for showing completion status.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.order_progress = AdminProgressWidget(
                    widget_id='order-progress',
                    title='Order Fulfillment',
                    stages=[
                        {'label': 'Pending', 'count': 12, 'color': 'yellow'},
                        {'label': 'Processing', 'count': 8, 'color': 'blue'},
                        {'label': 'Shipped', 'count': 45, 'color': 'green'},
                        {'label': 'Delivered', 'count': 120, 'color': 'gray'},
                    ],
                    refresh_interval=30000,
                )
    """

    stages: List[Dict[str, Any]] = field(default_factory=list)
    show_percentages: bool = True
    template_name: str = "djust/admin/widgets/progress.html"

    def get_context(self) -> Dict[str, Any]:
        context = super().get_context()

        total = sum(s.get("count", 0) for s in self.stages)
        stages_with_percent = []
        for stage in self.stages:
            stage_copy = dict(stage)
            if total > 0:
                stage_copy["percent"] = (stage.get("count", 0) / total) * 100
            else:
                stage_copy["percent"] = 0
            stages_with_percent.append(stage_copy)

        context.update(
            {
                "stages": stages_with_percent,
                "total": total,
                "show_percentages": self.show_percentages,
            }
        )
        return context


class AdminDashboard:
    """
    Container for organizing admin widgets into a dashboard layout.

    Usage:
        class DashboardView(LiveAdminView):
            def mount(self, request, **kwargs):
                self.dashboard = AdminDashboard(
                    layout='grid',  # grid or flex
                    columns=4,
                )

                # Add widgets
                self.dashboard.add_widget(revenue_widget, size='small')
                self.dashboard.add_widget(users_widget, size='small')
                self.dashboard.add_widget(orders_widget, size='small')
                self.dashboard.add_widget(conversion_widget, size='small')
                self.dashboard.add_widget(sales_chart, size='large')
                self.dashboard.add_widget(activity_feed, size='medium')
    """

    def __init__(
        self,
        layout: str = "grid",
        columns: int = 4,
        gap: str = "1rem",
    ):
        self.layout = layout
        self.columns = columns
        self.gap = gap
        self.widgets: List[Dict[str, Any]] = []

    def add_widget(
        self,
        widget: AdminWidget,
        size: str = "medium",  # small, medium, large, full
        row: Optional[int] = None,
        col: Optional[int] = None,
    ):
        """
        Add a widget to the dashboard.

        Args:
            widget: The widget instance
            size: Widget size (small=1col, medium=2col, large=3col, full=4col)
            row: Optional explicit row position
            col: Optional explicit column position
        """
        size_map = {"small": 1, "medium": 2, "large": 3, "full": self.columns}
        colspan = size_map.get(size, 2)

        self.widgets.append(
            {
                "widget": widget,
                "size": size,
                "colspan": colspan,
                "row": row,
                "col": col,
            }
        )

    def render(self) -> str:
        """Render the dashboard to HTML."""
        return render_to_string(
            "djust/admin/widgets/dashboard.html",
            {
                "layout": self.layout,
                "columns": self.columns,
                "gap": self.gap,
                "widgets": self.widgets,
            },
        )

    def __str__(self) -> str:
        return self.render()


__all__ = [
    "AdminWidget",
    "AdminStatsWidget",
    "AdminChartWidget",
    "AdminActivityWidget",
    "AdminTableWidget",
    "AdminProgressWidget",
    "AdminDashboard",
]
