"""
Views package for demo_app

This package organizes views into logical modules for better maintainability.

"""

# Import all views from the old views file for backwards compatibility
from ..views_old import (
    IndexView,
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

# Import demos index view
from .demos import DemosIndexView

# Import homepage demo views
from .homepage import (
    HomeCounterDemo,
    HomeSearchDemo,
    HomeLiveDataDemo,
    HomeTodoDemo,
)

__all__ = [
    'IndexView',
    'CounterView',
    'TodoView',
    'ChatView',
    'ReactDemoView',
    'PerformanceTestView',
    'ProductDataTableView',
    'DemosIndexView',
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
    'HomeCounterDemo',
    'HomeSearchDemo',
    'HomeLiveDataDemo',
    'HomeTodoDemo',
]
