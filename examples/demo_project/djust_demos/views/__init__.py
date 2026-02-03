"""
Demo views for djust_demos app
"""

from .counter_demo import CounterView
from .demos import (
    TodoView,
    ChatView,
    ReactDemoView,
    PerformanceTestView,
    ProductDataTableView,
    DemosIndexView,
    DemosIndexDesign1View,
    DemosIndexDesign2View,
    DemosIndexDesign3View,
    DemosIndexHybridView,
    DemosIndexShadcnView,
)
from .dropdown_demo import DropdownDemo
from .toast_demo import toast_demo
from .navbar_demo import NavBarDemoView
from .offcanvas_demo import OffcanvasDemoView
from .component_showcase import ComponentShowcaseView
from .debounce_demo import DebounceSearchView
from .throttle_demo import ThrottleScrollView
from .optimistic_todo_demo import OptimisticTodoView
from .optimistic_counter_demo import OptimisticCounterView
from .component_demo import ComponentDemoView
from .cache_demo import CacheDemoView
from .smart_dashboard import SmartDashboardView
from .rust_components_demo import RustComponentsDemo
from .components_only_demo import ComponentsOnlyDemo
from .no_template_demo import NoTemplateDemo
from .components_demo import ComponentsDemoView
from .kitchen_sink import KitchenSinkView
from .streaming_demo import StreamingDemoView

__all__ = [
    'CounterView',
    'TodoView',
    'ChatView',
    'ReactDemoView',
    'PerformanceTestView',
    'ProductDataTableView',
    'DemosIndexView',
    'DemosIndexDesign1View',
    'DemosIndexDesign2View',
    'DemosIndexDesign3View',
    'DemosIndexHybridView',
    'DemosIndexShadcnView',
    'DropdownDemo',
    'toast_demo',
    'NavBarDemoView',
    'OffcanvasDemoView',
    'ComponentShowcaseView',
    'DebounceSearchView',
    'ThrottleScrollView',
    'OptimisticTodoView',
    'OptimisticCounterView',
    'ComponentDemoView',
    'ComponentsDemoView',
    'CacheDemoView',
    'SmartDashboardView',
    'RustComponentsDemo',
    'ComponentsOnlyDemo',
    'NoTemplateDemo',
    'KitchenSinkView',
    'StreamingDemoView',
]
