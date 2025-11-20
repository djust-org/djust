"""
Marketing site views.

All views inherit from BaseMarketingView which provides:
- Common navigation data
- Consistent layout
- GitHub star count
"""

from .base import BaseMarketingView
from .home import HomeView
from .features import FeaturesView
from .security import SecurityView
from .examples import ExamplesView
from .playground import PlaygroundView
from .comparison import ComparisonView
from .benchmarks import BenchmarksView
from .use_cases import UseCasesView
from .pricing import PricingView
from .quickstart import QuickStartView
from .faq import FAQView

__all__ = [
    'BaseMarketingView',
    'HomeView',
    'FeaturesView',
    'SecurityView',
    'ExamplesView',
    'PlaygroundView',
    'ComparisonView',
    'BenchmarksView',
    'UseCasesView',
    'PricingView',
    'QuickStartView',
    'FAQView',
]
