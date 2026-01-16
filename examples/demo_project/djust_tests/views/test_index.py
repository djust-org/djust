"""
Test Index View

Displays a catalog of all available automated test pages with descriptions,
status, and links to run each test.
"""

from django.views.generic import TemplateView


class TestIndexView(TemplateView):
    """
    Index page for all automated test pages.

    This view provides a central hub for discovering and accessing all
    available test pages in the project. Each test includes:
    - Name and description
    - Feature being tested
    - Phase/version information
    - Link to run the test
    """

    template_name = 'tests/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Define all available test pages
        # TODO: Consider auto-discovery of test views in future
        context['tests'] = [
            {
                'name': '@cache Decorator',
                'description': 'Automated testing for client-side response caching with TTL and LRU eviction',
                'url_name': 'tests:cache',
                'feature': 'Cache Decorator',
                'phase': 'Phase 5',
                'status': 'passing',
                'tests': [
                    'Cache decorator is invoked',
                    'Cache hits return instantly without server calls',
                    'Cache misses call server and cache result',
                    'TTL expiration works correctly',
                    'Cache key generation works with key_params',
                ],
            },
            {
                'name': 'DraftModeMixin',
                'description': 'Automated testing for localStorage-based draft auto-save with 500ms debounce',
                'url_name': 'tests:draft-mode',
                'feature': 'Draft Mode',
                'phase': 'Phase 5',
                'status': 'passing',
                'tests': [
                    'localStorage API available',
                    'Draft key configuration',
                    'Draft persistence check',
                    'Auto-save functionality (500ms debounce)',
                ],
            },
            {
                'name': '@loading Attribute',
                'description': 'Automated testing for @loading.disable, @loading.class, @loading.show, @loading.hide HTML attributes with scoped loading state',
                'url_name': 'tests:loading',
                'feature': 'Loading Attributes',
                'phase': 'Phase 5',
                'status': 'passing',
                'tests': [
                    'LoadingManager available',
                    '@loading attributes present in DOM',
                    'Button disabled during loading',
                    'Independent button behavior (no cross-contamination)',
                    'Grouped elements (button + spinner)',
                    'Multiple modifiers work together',
                ],
            },
            # Add more tests here as they're created
            # Example:
            # {
            #     'name': '@debounce Decorator',
            #     'description': 'Automated testing for debounced event handlers',
            #     'url_name': 'tests-debounce',
            #     'feature': 'Debounce Decorator',
            #     'phase': 'Phase 2',
            #     'status': 'passing',
            #     'tests': [
            #         'Debounce delays handler execution',
            #         'Multiple rapid calls trigger only once',
            #         'Wait time is configurable',
            #     ],
            # },
        ]

        # Summary statistics
        context['total_tests'] = len(context['tests'])
        context['passing_tests'] = sum(
            1 for t in context['tests'] if t['status'] == 'passing'
        )
        context['failing_tests'] = sum(
            1 for t in context['tests'] if t['status'] == 'failing'
        )
        context['pending_tests'] = sum(
            1 for t in context['tests'] if t['status'] == 'pending'
        )

        return context
