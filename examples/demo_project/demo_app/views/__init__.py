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
    FormsIndexView,
    RegistrationFormView,
    ContactFormView,
    ProfileFormView,
)

# Import the refactored view from the new package
from .forms_demo import (
    SimpleContactFormView,
    AutoContactFormView,
    AutoContactFormTailwindView,
    AutoContactFormPlainView,
    AutoFormComparisonView,
)

# Import kitchen sink view
from .kitchen_sink import KitchenSinkView

__all__ = [
    'IndexView',
    'CounterView',
    'TodoView',
    'ChatView',
    'ReactDemoView',
    'PerformanceTestView',
    'ProductDataTableView',
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
]
