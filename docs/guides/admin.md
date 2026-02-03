# Django Admin Integration

djust provides seamless integration with Django admin for interactive admin pages with real-time updates.

## Quick Start

```python
from django.contrib import admin
from djust.admin import LiveViewAdminMixin, live_action

class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    list_display = ['name', 'price', 'stock']
    
    # Enable LiveView features
    live_filters = True
    live_inline_editing = True
    live_editable_fields = ['price', 'stock']
    
    @live_action(description="Export selected items")
    def export_items(self, request, queryset):
        for i, item in enumerate(queryset):
            yield self.update_progress(i + 1, queryset.count(), f"Exporting {item.name}...")
            item.export()

admin.site.register(Product, ProductAdmin)
```

## LiveViewAdminMixin

Add `LiveViewAdminMixin` to your ModelAdmin to enable LiveView features:

```python
from djust.admin import LiveViewAdminMixin

class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    # Standard Django admin options
    list_display = ['name', 'price', 'stock', 'active']
    list_filter = ['active', 'category']
    search_fields = ['name']
    
    # LiveView options
    live_filters = True           # Real-time filtering
    live_inline_editing = True    # Edit fields directly in list view
    live_editable_fields = ['price', 'stock']  # Which fields can be edited inline
    live_refresh_interval = 30000  # Auto-refresh every 30 seconds
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `live_filters` | bool | `False` | Enable real-time search/filter |
| `live_inline_editing` | bool | `False` | Enable inline field editing |
| `live_editable_fields` | list | `[]` | Fields that can be edited inline |
| `live_refresh_interval` | int | `None` | Auto-refresh interval in milliseconds |

### Live Filtering

When `live_filters = True`, a search box appears above the list that filters results in real-time without page reload:

```python
class OrderAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    list_display = ['id', 'customer', 'total', 'status']
    live_filters = True
```

The filter searches across all fields in `search_fields` with debounced input.

### Inline Editing

Enable inline editing to modify fields directly in the list view:

```python
class InventoryAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    list_display = ['sku', 'name', 'editable_quantity', 'editable_price']
    live_inline_editing = True
    live_editable_fields = ['quantity', 'price']
    
    def editable_quantity(self, obj):
        return self.get_inline_edit_field(obj, 'quantity')
    
    def editable_price(self, obj):
        return self.get_inline_edit_field(obj, 'price')
```

Clicking an editable field turns it into an input. Press Enter to save, Escape to cancel.

## Live Actions

Convert slow admin actions into streaming operations with progress feedback using `@live_action`:

```python
from djust.admin import LiveViewAdminMixin, live_action

class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    actions = ['export_csv', 'bulk_update_prices', 'sync_inventory']
    
    @live_action(description="Export to CSV")
    def export_csv(self, request, queryset):
        total = queryset.count()
        
        for i, product in enumerate(queryset):
            yield self.update_progress(
                current=i + 1,
                total=total,
                message=f"Exporting {product.name}..."
            )
            # Export logic here
            export_product_to_csv(product)
        
        yield self.update_progress(total, total, "Export complete!", "complete")
    
    @live_action(description="Bulk update prices", permissions=['change_product'])
    def bulk_update_prices(self, request, queryset):
        total = queryset.count()
        
        for i, product in enumerate(queryset):
            yield {
                'current': i + 1,
                'total': total,
                'percent': int((i + 1) / total * 100),
                'message': f"Updating {product.name}..."
            }
            product.price *= 1.1  # 10% increase
            product.save()
```

### update_progress Helper

The mixin provides `update_progress()` for consistent progress updates:

```python
yield self.update_progress(
    current=5,      # Current item (1-indexed)
    total=100,      # Total items
    message="Processing item 5...",  # Status message
    status="running"  # Status: 'running', 'complete', 'error'
)
```

### Permissions

Restrict actions to users with specific permissions:

```python
@live_action(
    description="Delete all logs",
    permissions=['delete_log']  # Requires myapp.delete_log permission
)
def delete_logs(self, request, queryset):
    ...
```

## LiveAdminView

Create custom admin pages with full LiveView functionality:

```python
from djust.admin import LiveAdminView
from djust import event_handler

class DashboardView(LiveAdminView):
    template_name = "myapp/admin/dashboard.html"
    admin_title = "Analytics Dashboard"
    admin_subtitle = "Real-time metrics"
    permission_required = "myapp.view_dashboard"
    
    def mount(self, request, **kwargs):
        self.stats = self.load_stats()
        self.recent_orders = Order.objects.order_by('-created')[:10]
    
    def load_stats(self):
        return {
            'total_revenue': Order.objects.aggregate(Sum('total'))['total__sum'],
            'order_count': Order.objects.count(),
            'active_users': User.objects.filter(is_active=True).count(),
        }
    
    @event_handler
    def refresh_stats(self):
        self.stats = self.load_stats()
```

```html
<!-- myapp/templates/myapp/admin/dashboard.html -->
{% extends "admin/base_site.html" %}
{% load static %}

{% block content %}
<div class="dashboard">
    <div class="stats-grid">
        <div class="stat-card">
            <h3>Total Revenue</h3>
            <p class="stat-value">${{ stats.total_revenue|floatformat:2 }}</p>
        </div>
        <div class="stat-card">
            <h3>Orders</h3>
            <p class="stat-value">{{ stats.order_count }}</p>
        </div>
        <div class="stat-card">
            <h3>Active Users</h3>
            <p class="stat-value">{{ stats.active_users }}</p>
        </div>
    </div>
    
    <button dj-click="refresh_stats">Refresh</button>
</div>
{% endblock %}
```

### URL Configuration

```python
# urls.py
from django.urls import path
from myapp.admin_views import DashboardView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('admin/dashboard/', DashboardView.as_view(), name='admin-dashboard'),
]
```

## Dashboard Widgets

Pre-built widgets for common admin dashboard patterns:

### Stats Widget

```python
from djust.admin import LiveAdminView
from djust.admin.widgets import AdminStatsWidget

class DashboardView(LiveAdminView):
    def mount(self, request, **kwargs):
        self.revenue_widget = AdminStatsWidget(
            widget_id='revenue',
            title='Total Revenue',
            value=125000,
            prefix='$',
            trend=12.5,  # +12.5% change
            trend_label='vs last month',
            color='green',
            refresh_interval=30000,
        )
```

```html
{{ revenue_widget }}
```

### Chart Widget

```python
from djust.admin.widgets import AdminChartWidget

self.sales_chart = AdminChartWidget(
    widget_id='sales-chart',
    title='Sales Over Time',
    chart_type='line',  # line, bar, pie, doughnut
    labels=['Jan', 'Feb', 'Mar', 'Apr', 'May'],
    datasets=[{
        'label': 'Revenue',
        'data': [12000, 15000, 18000, 14000, 22000],
        'borderColor': '#3b82f6',
        'fill': False,
    }],
    height='300px',
    refresh_interval=60000,
)
```

**Note:** Charts require Chart.js. Add to your admin template:
```html
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
```

### Activity Feed Widget

```python
from djust.admin.widgets import AdminActivityWidget

self.activity = AdminActivityWidget(
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
            'detail': 'Order #1234 - $150.00',
            'time': '5 minutes ago',
            'color': 'green',
        },
    ],
    max_items=10,
    refresh_interval=15000,
)
```

### Table Widget

```python
from djust.admin.widgets import AdminTableWidget

self.top_products = AdminTableWidget(
    widget_id='top-products',
    title='Top Selling Products',
    columns=['Product', 'Sales', 'Revenue'],
    rows=[
        ['Widget Pro', 150, '$4,500'],
        ['Gadget Plus', 120, '$3,600'],
        ['Tool Basic', 100, '$2,000'],
    ],
    max_rows=5,
)
```

### Progress Widget

```python
from djust.admin.widgets import AdminProgressWidget

self.order_status = AdminProgressWidget(
    widget_id='order-status',
    title='Order Pipeline',
    stages=[
        {'label': 'Pending', 'count': 12, 'color': 'yellow'},
        {'label': 'Processing', 'count': 8, 'color': 'blue'},
        {'label': 'Shipped', 'count': 45, 'color': 'green'},
        {'label': 'Delivered', 'count': 120, 'color': 'gray'},
    ],
    show_percentages=True,
    refresh_interval=30000,
)
```

### Dashboard Layout

Organize widgets in a grid layout:

```python
from djust.admin.widgets import AdminDashboard, AdminStatsWidget

class DashboardView(LiveAdminView):
    def mount(self, request, **kwargs):
        self.dashboard = AdminDashboard(
            layout='grid',
            columns=4,
            gap='1rem',
        )
        
        # Add widgets with sizes
        self.dashboard.add_widget(revenue_widget, size='small')     # 1 column
        self.dashboard.add_widget(orders_widget, size='small')      # 1 column
        self.dashboard.add_widget(users_widget, size='small')       # 1 column
        self.dashboard.add_widget(conversion_widget, size='small')  # 1 column
        self.dashboard.add_widget(sales_chart, size='large')        # 3 columns
        self.dashboard.add_widget(activity_feed, size='medium')     # 2 columns
```

```html
{{ dashboard }}
```

## Registering Admin Views

Use the registry for automatic URL generation and sidebar integration:

```python
from djust.admin.views import admin_views, LiveAdminView

@admin_views.register(
    app_label='analytics',
    title='Dashboard',
    icon='fas fa-chart-line',
    order=0
)
class AnalyticsDashboard(LiveAdminView):
    template_name = "analytics/admin/dashboard.html"
    ...

@admin_views.register(
    app_label='analytics',
    title='Reports',
    icon='fas fa-file-alt',
    order=1
)
class ReportsView(LiveAdminView):
    template_name = "analytics/admin/reports.html"
    ...
```

Add registered URLs to your admin:

```python
# urls.py
from djust.admin.views import admin_views

urlpatterns = [
    path('admin/', admin.site.urls),
] + admin_views.get_urls()
```

## Styling

The admin integration includes CSS that matches Django admin styling. You can customize using CSS variables:

```css
:root {
    --djust-widget-bg: #fff;
    --djust-widget-border: #e8e8e8;
    --djust-primary: #417690;
    --djust-success: #10b981;
    --djust-warning: #f59e0b;
    --djust-danger: #ef4444;
}
```

## Complete Example

```python
# admin.py
from django.contrib import admin
from django.db.models import Sum, Count
from djust.admin import LiveViewAdminMixin, LiveAdminView, live_action
from djust.admin.widgets import (
    AdminDashboard,
    AdminStatsWidget,
    AdminChartWidget,
    AdminActivityWidget,
)
from djust import event_handler
from .models import Product, Order, Customer

class ProductAdmin(LiveViewAdminMixin, admin.ModelAdmin):
    list_display = ['name', 'editable_price', 'editable_stock', 'active']
    live_filters = True
    live_inline_editing = True
    live_editable_fields = ['price', 'stock']
    
    def editable_price(self, obj):
        return self.get_inline_edit_field(obj, 'price')
    
    def editable_stock(self, obj):
        return self.get_inline_edit_field(obj, 'stock')
    
    @live_action(description="Generate inventory report")
    def generate_report(self, request, queryset):
        total = queryset.count()
        for i, product in enumerate(queryset):
            yield self.update_progress(i + 1, total, f"Processing {product.name}...")
            # Report generation logic
        yield self.update_progress(total, total, "Report ready!", "complete")

admin.site.register(Product, ProductAdmin)


class StoreDashboard(LiveAdminView):
    template_name = "store/admin/dashboard.html"
    admin_title = "Store Dashboard"
    
    def mount(self, request, **kwargs):
        self.setup_widgets()
    
    def setup_widgets(self):
        stats = self.get_stats()
        
        self.dashboard = AdminDashboard(columns=4)
        
        self.dashboard.add_widget(
            AdminStatsWidget(
                widget_id='revenue',
                title='Revenue',
                value=stats['revenue'],
                prefix='$',
                trend=stats['revenue_trend'],
                color='green',
            ),
            size='small'
        )
        
        self.dashboard.add_widget(
            AdminStatsWidget(
                widget_id='orders',
                title='Orders',
                value=stats['orders'],
                trend=stats['orders_trend'],
                color='blue',
            ),
            size='small'
        )
        
        self.dashboard.add_widget(
            AdminChartWidget(
                widget_id='sales-chart',
                title='Sales Trend',
                chart_type='line',
                labels=stats['chart_labels'],
                datasets=[{
                    'label': 'Sales',
                    'data': stats['chart_data'],
                    'borderColor': '#3b82f6',
                }],
            ),
            size='large'
        )
        
        self.dashboard.add_widget(
            AdminActivityWidget(
                widget_id='recent-orders',
                title='Recent Orders',
                items=self.get_recent_activity(),
                refresh_interval=15000,
            ),
            size='medium'
        )
    
    def get_stats(self):
        return {
            'revenue': Order.objects.aggregate(Sum('total'))['total__sum'] or 0,
            'revenue_trend': 12.5,
            'orders': Order.objects.count(),
            'orders_trend': 8.3,
            'chart_labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
            'chart_data': [120, 150, 180, 140, 200],
        }
    
    def get_recent_activity(self):
        return [
            {
                'message': f'Order #{order.id}',
                'detail': f'{order.customer} - ${order.total}',
                'time': order.created.strftime('%H:%M'),
                'color': 'green',
            }
            for order in Order.objects.order_by('-created')[:5]
        ]
    
    @event_handler
    def refresh(self):
        self.setup_widgets()
```

```html
<!-- store/templates/store/admin/dashboard.html -->
{% extends "admin/base_site.html" %}

{% block content %}
<div id="dashboard-container">
    {{ dashboard }}
    
    <button dj-click="refresh" class="button">
        Refresh Dashboard
    </button>
</div>
{% endblock %}
```
