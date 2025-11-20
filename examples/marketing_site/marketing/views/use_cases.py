"""
Use cases page view.
"""
from .base import StaticMarketingView


class UseCasesView(StaticMarketingView):
    """
    Use cases page showing industry applications.

    Showcases real-world scenarios where djust excels.
    """

    template_name = 'marketing/use_cases.html'
    page_slug = 'use_cases'

    def mount(self, request, **kwargs):
        """Initialize use cases page state."""
        super().mount(request, **kwargs)

        # Industry use cases with code examples
        self.use_cases = [
            {
                'id': 'fintech',
                'industry': 'FinTech & Banking',
                'gradient': 'from-green-500 to-emerald-600',
                'icon_path': 'M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
                'description': 'Build secure, real-time financial dashboards without exposing business logic to the browser. Zero API attack surface.',
                'perfect_for': [
                    'Trading platforms with real-time market data',
                    'Banking dashboards with live transaction feeds',
                    'Fraud detection systems with instant alerts',
                    'Portfolio management tools',
                ],
                'key_benefits': [
                    'Keep pricing algorithms on the server',
                    'Real-time updates via WebSocket',
                    'Sub-millisecond rendering for live data',
                ],
                'has_code': True,
                'code_title': 'Example: Trading Dashboard',
                'code': '''<span class="text-purple-400">from</span> djust <span class="text-purple-400">import</span> LiveView
<span class="text-purple-400">from</span> decimal <span class="text-purple-400">import</span> Decimal

<span class="text-purple-400">class</span> <span class="text-yellow-400">TradingDashboard</span>(LiveView):
    template_name = <span class="text-green-400">'trading.html'</span>

    <span class="text-purple-400">def</span> <span class="text-blue-400">mount</span>(self, request):
        self.portfolio = self.load_portfolio()
        self.live_prices = &#123;&#125;

        <span class="text-gray-500"># Subscribe to price feed</span>
        self.subscribe(<span class="text-green-400">'market:prices'</span>)

    <span class="text-purple-400">def</span> <span class="text-blue-400">handle_info</span>(self, event, data):
        <span class="text-gray-500"># Real-time price updates</span>
        <span class="text-purple-400">if</span> event == <span class="text-green-400">'price_update'</span>:
            self.live_prices[data[<span class="text-green-400">'symbol'</span>]] = data[<span class="text-green-400">'price'</span>]
            self.update_portfolio_value()

    <span class="text-purple-400">def</span> <span class="text-blue-400">execute_trade</span>(self, symbol, quantity):
        <span class="text-gray-500"># Business logic stays on server</span>
        <span class="text-purple-400">if</span> self.check_compliance(symbol, quantity):
            self.broker.execute(symbol, quantity)
            self.portfolio = self.load_portfolio()''',
            },
            {
                'id': 'saas',
                'industry': 'SaaS Dashboards',
                'gradient': 'from-blue-500 to-cyan-600',
                'icon_path': 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
                'description': 'Build data-intensive dashboards with complex aggregations, real-time updates, and responsive filtering.',
                'perfect_for': [
                    'Analytics platforms with complex queries',
                    'Admin panels with live data tables',
                    'Monitoring dashboards with metrics',
                    'CRM tools with contact management',
                ],
                'key_benefits': [
                    'Automatic N+1 query elimination',
                    'Built-in debouncing and caching',
                    'Server-side aggregations (no client processing)',
                ],
                'has_code': True,
                'code_title': 'Example: Analytics Dashboard',
                'code': '''<span class="text-purple-400">from</span> djust <span class="text-purple-400">import</span> LiveView
<span class="text-purple-400">from</span> djust.decorators <span class="text-purple-400">import</span> debounce, cache

<span class="text-purple-400">class</span> <span class="text-yellow-400">AnalyticsDashboard</span>(LiveView):
    template_name = <span class="text-green-400">'analytics.html'</span>

    <span class="text-purple-400">def</span> <span class="text-blue-400">mount</span>(self, request):
        self._metrics = Metric.objects.all()
        self.time_range = <span class="text-green-400">'7d'</span>

    <span class="text-dec">@debounce</span>(wait=<span class="text-orange-400">0.5</span>)
    <span class="text-dec">@cache</span>(ttl=<span class="text-orange-400">300</span>, key_params=[<span class="text-green-400">'time_range'</span>])
    <span class="text-purple-400">def</span> <span class="text-blue-400">filter_metrics</span>(self, time_range=<span class="text-green-400">'7d'</span>):
        self.time_range = time_range
        self._refresh_metrics()

    <span class="text-purple-400">def</span> <span class="text-blue-400">_refresh_metrics</span>(self):
        <span class="text-gray-500"># Complex aggregation on server</span>
        self._metrics = (
            Metric.objects
            .filter(timestamp__gte=self.get_start_date())
            .aggregate_by_hour()
        )

    <span class="text-purple-400">def</span> <span class="text-blue-400">get_context_data</span>(self, **kwargs):
        self.metrics = self._metrics  <span class="text-gray-500"># JIT serialization</span>
        <span class="text-purple-400">return</span> super().get_context_data(**kwargs)''',
            },
            {
                'id': 'ecommerce',
                'industry': 'E-commerce',
                'gradient': 'from-purple-500 to-pink-600',
                'icon_path': 'M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z',
                'description': 'Create interactive shopping experiences with real-time inventory, dynamic pricing, and instant cart updates.',
                'perfect_for': [
                    'Product catalogs with live search/filtering',
                    'Shopping carts with instant updates',
                    'Inventory management systems',
                    'Checkout flows with validation',
                ],
                'key_benefits': [
                    'Real-time inventory updates',
                    'Django Forms integration for checkout',
                    'Keep pricing logic server-side',
                ],
                'has_code': True,
                'code_title': 'Example: Product Search',
                'code': '''<span class="text-purple-400">from</span> djust <span class="text-purple-400">import</span> LiveView
<span class="text-purple-400">from</span> djust.decorators <span class="text-purple-400">import</span> debounce

<span class="text-purple-400">class</span> <span class="text-yellow-400">ProductSearch</span>(LiveView):
    template_name = <span class="text-green-400">'products.html'</span>

    <span class="text-purple-400">def</span> <span class="text-blue-400">mount</span>(self, request):
        self._products = Product.objects.filter(active=<span class="text-orange-400">True</span>)
        self.search_query = <span class="text-green-400">''</span>
        self.category = <span class="text-green-400">'all'</span>

    <span class="text-dec">@debounce</span>(wait=<span class="text-orange-400">0.3</span>)
    <span class="text-purple-400">def</span> <span class="text-blue-400">search</span>(self, value=<span class="text-green-400">''</span>, **kwargs):
        self.search_query = value
        self._refresh_products()

    <span class="text-purple-400">def</span> <span class="text-blue-400">filter_category</span>(self, category=<span class="text-green-400">'all'</span>):
        self.category = category
        self._refresh_products()

    <span class="text-purple-400">def</span> <span class="text-blue-400">add_to_cart</span>(self, product_id: int):
        product = Product.objects.get(id=product_id)

        <span class="text-gray-500"># Check inventory server-side</span>
        <span class="text-purple-400">if</span> product.inventory > <span class="text-orange-400">0</span>:
            self.cart.add(product)
            self.success = <span class="text-green-400">f'Added &#123;product.name&#125;'</span>

    <span class="text-purple-400">def</span> <span class="text-blue-400">_refresh_products</span>(self):
        queryset = Product.objects.filter(active=<span class="text-orange-400">True</span>)

        <span class="text-purple-400">if</span> self.search_query:
            queryset = queryset.filter(
                name__icontains=self.search_query
            )

        <span class="text-purple-400">if</span> self.category != <span class="text-green-400">'all'</span>:
            queryset = queryset.filter(category=self.category)

        self._products = queryset''',
            },
            {
                'id': 'healthcare',
                'industry': 'Healthcare',
                'gradient': 'from-red-500 to-rose-600',
                'icon_path': 'M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z',
                'description': 'HIPAA-compliant patient portals, EHR systems, and medical device dashboards with maximum security.',
                'perfect_for': [
                    'Patient portals with medical records',
                    'Real-time vital signs monitoring',
                    'Appointment scheduling systems',
                    'Lab results dashboards',
                ],
                'key_benefits': [
                    'Zero API attack surface (HIPAA compliance)',
                    'Server-side access control',
                    'Audit logs for all data access',
                    'Real-time vital signs updates',
                ],
                'has_code': False,
            },
            {
                'id': 'enterprise',
                'industry': 'Enterprise Internal Tools',
                'gradient': 'from-orange-500 to-amber-600',
                'icon_path': 'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4',
                'description': 'Rapidly build internal tools, admin panels, and workflow automation with minimal JavaScript.',
                'perfect_for': [
                    'Admin dashboards for operations teams',
                    'Data entry and approval workflows',
                    'Internal reporting tools',
                    'Configuration management interfaces',
                ],
                'key_benefits': [
                    '10x faster development than React SPAs',
                    'Leverage existing Django models/forms',
                    'Zero build step complexity',
                    'Easy to maintain and extend',
                ],
                'has_code': False,
            },
            {
                'id': 'collaboration',
                'industry': 'Real-Time Collaboration',
                'gradient': 'from-cyan-500 to-teal-600',
                'icon_path': 'M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z',
                'description': 'Multi-user applications with presence indicators, live cursors, and instant synchronization.',
                'perfect_for': [
                    'Chat applications and messaging',
                    'Collaborative document editing',
                    'Project management tools',
                    'Live polling and surveys',
                ],
                'key_benefits': [
                    'Built-in WebSocket pub/sub',
                    'Presence tracking out of the box',
                    '10,000+ concurrent connections per server',
                    'Redis backend for horizontal scaling',
                ],
                'has_code': False,
            },
        ]

    def get_context_data(self, **kwargs):
        """Add use cases page context."""
        context = super().get_context_data(**kwargs)
        context.update({
            'use_cases': self.use_cases,
        })
        return context
