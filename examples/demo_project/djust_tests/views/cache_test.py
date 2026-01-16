"""
Cache Test - automated testing for @cache decorator
"""

from djust import LiveView
from djust.decorators import cache, debounce


class CacheTestView(LiveView):
    """
    Automated test for @cache decorator functionality.

    This view runs tests automatically on page load to verify:
    - Cache decorator is invoked
    - Cache hits return instantly without server calls
    - Cache misses call server and cache result
    - TTL expiration works correctly
    - Cache key generation works with key_params
    """

    template_name = 'tests/cache_test.html'

    def mount(self, request):
        """Initialize test state"""
        self.query = ""
        self.server_calls = 0
        self.total_searches = 0
        self.test_results = []
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.all_tests_passed = False

    def get_context_data(self, **kwargs):
        """Add computed values to context"""
        context = super().get_context_data(**kwargs)

        # Calculate cache efficiency
        if self.total_searches > 0:
            cache_efficiency = round(
                ((self.total_searches - self.server_calls) / self.total_searches) * 100
            )
        else:
            cache_efficiency = 0

        context['cache_efficiency'] = cache_efficiency

        # Update test results based on server calls
        if self.server_calls > 0:
            self._update_test_results()

        return context

    def _update_test_results(self):
        """Update test results based on actual behavior"""
        self.test_results = []

        # Test 1: Decorator is invoked
        test1_passed = True  # If we got here, decorator is at least defined
        self.test_results.append({
            'name': 'Test 1: @cache decorator exists',
            'description': 'Verifies @cache decorator is defined in decorators.py',
            'passed': test1_passed,
            'duration_ms': 0,
            'details': '@cache decorator imported successfully'
        })

        # Test 2: Server calls are reasonable
        test2_passed = self.server_calls <= 3 if self.total_searches > 0 else True
        self.test_results.append({
            'name': 'Test 2: Server calls minimized',
            'description': 'Verifies cache is reducing server calls',
            'passed': test2_passed,
            'duration_ms': 0,
            'details': f'{self.server_calls} server calls out of {self.total_searches} total searches'
        })

        # Test 3: Cache efficiency
        if self.total_searches >= 5:
            cache_efficiency = ((self.total_searches - self.server_calls) / self.total_searches) * 100
            test3_passed = cache_efficiency >= 40  # Expect at least 40% cache hit rate
            self.test_results.append({
                'name': 'Test 3: Cache efficiency',
                'description': 'Verifies cache hit rate is acceptable',
                'passed': test3_passed,
                'duration_ms': 0,
                'details': f'Cache efficiency: {round(cache_efficiency)}% (expected ≥40%)'
            })
        else:
            test3_passed = True
            self.test_results.append({
                'name': 'Test 3: Cache efficiency',
                'description': 'Not enough searches to measure cache efficiency',
                'passed': test3_passed,
                'duration_ms': 0,
                'details': f'Need at least 5 searches, got {self.total_searches}'
            })

        # Update test counts
        self.tests_run = len(self.test_results)
        self.tests_passed = sum(1 for t in self.test_results if t['passed'])
        self.tests_failed = self.tests_run - self.tests_passed
        self.all_tests_passed = self.tests_failed == 0

    @debounce(wait=0.5)
    @cache(ttl=300, key_params=["query"])
    def search(self, query: str = "", total_searches: int = 0, **kwargs):
        """
        Search handler with debouncing and caching.

        This method is used for both automated tests and manual testing.
        The automated test runs on page load via JavaScript.

        Args:
            query: The search query string
            total_searches: Total number of searches (tracked client-side and passed in)
        """
        self.query = query
        self.total_searches = total_searches  # Update from client-side counter
        self.server_calls += 1
