"""
Views package for demo_app

This package organizes views into logical modules for better maintainability.

"""

# Import all views from the old views file for backwards compatibility
from ..views_old import (
    IndexView,
    NavbarBadgeDemo,
    CounterView,
    TodoView,
    ChatView,
    ReactDemoView,
    PerformanceTestView,
    ProductDataTableView,
)

# Import the refactored view from the new package
from .forms_demo import (
    FormsIndexView,
    RegistrationFormView,
    ContactFormView,
    ProfileFormView,
    SimpleContactFormView,
    AutoContactFormView,
    AutoContactFormTailwindView,
    AutoContactFormPlainView,
    AutoFormComparisonView,
)

# Import kitchen sink view
from .kitchen_sink import KitchenSinkView

# Import docs view
from .docs import DocsView
from .components_guide import ComponentsGuideView

# Import demos index view
from .demos import DemosIndexView

# Import Rust components demo
from .rust_components_demo import RustComponentsDemo

# Import components-only demo
from .components_only_demo import ComponentsOnlyDemo

# Import no-template demo
from .no_template_demo import NoTemplateDemo

# Import unified components demo
from .components_demo import ComponentsDemoView

# Import toast demo
from .toast_demo import toast_demo

# Import dropdown demo
from .dropdown_demo import DropdownDemo

# Import navbar demo
from .navbar_demo import NavBarDemoView

# Import offcanvas demo
from .offcanvas_demo import OffcanvasDemoView

# Import component showcase
from .component_showcase import ComponentShowcaseView

# Import state management demos (Phase 2)
from .debounce_demo import DebounceSearchView
from .throttle_demo import ThrottleScrollView

# Import state management demos (Phase 3)
from .optimistic_todo_demo import OptimisticTodoView
from .optimistic_counter_demo import OptimisticCounterView

# Import component system demo (Phase 4)
from .component_demo import ComponentDemoView

# Import state management demos (Phase 5)
from .cache_demo import CacheDemoView
from .cache_test import CacheTestView
from .draft_mode_test import DraftModeTestView
from .loading_test import LoadingTestView

# Import test index
from .test_index import TestIndexView

# Import homepage demo views
from .homepage import (
    HomeCounterDemo,
    HomeSearchDemo,
    HomeLiveDataDemo,
    HomeTodoDemo,
)

__all__ = [
    'IndexView',
    'NavbarBadgeDemo',
    'CounterView',
    'TodoView',
    'ChatView',
    'ReactDemoView',
    'PerformanceTestView',
    'ProductDataTableView',
    'DemosIndexView',
    'RustComponentsDemo',
    'ComponentsOnlyDemo',
    'NoTemplateDemo',
    'ComponentsDemoView',
    'toast_demo',
    'DropdownDemo',
    'NavBarDemoView',
    'OffcanvasDemoView',
    'ComponentShowcaseView',
    'FormsIndexView',
    'RegistrationFormView',
    'ContactFormView',
    'ProfileFormView',
    'SimpleContactFormView',
    'AutoContactFormView',
    'AutoContactFormTailwindView',
    'AutoContactFormPlainView',
    'AutoFormComparisonView',
    'KitchenSinkView',
    'DocsView',
    'ComponentsGuideView',
    'HomeCounterDemo',
    'HomeSearchDemo',
    'HomeLiveDataDemo',
    'HomeTodoDemo',
    'DebounceSearchView',
    'ThrottleScrollView',
    'OptimisticTodoView',
    'OptimisticCounterView',
    'ComponentDemoView',
    'CacheDemoView',
    'CacheTestView',
    'DraftModeTestView',
    'LoadingTestView',
    'TestIndexView',
]
