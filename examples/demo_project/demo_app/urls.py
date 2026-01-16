"""
URL configuration for demo app.
"""

from django.urls import path
from . import views

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),

    # Homepage Embedded Demos
    path('home-demos/counter/', views.HomeCounterDemo.as_view(), name='home-counter'),
    path('home-demos/search/', views.HomeSearchDemo.as_view(), name='home-search'),
    path('home-demos/data/', views.HomeLiveDataDemo.as_view(), name='home-data'),
    path('home-demos/todo/', views.HomeTodoDemo.as_view(), name='home-todo'),
    path('home-demos/navbar-badge/', views.NavbarBadgeDemo.as_view(), name='home-navbar-badge'),

    # Live Demos
    path('demos/', views.DemosIndexView.as_view(), name='demos-index'),
    path('demos/counter/', views.CounterView.as_view(), name='demos-counter'),
    path('demos/todo/', views.TodoView.as_view(), name='demos-todo'),
    path('demos/chat/', views.ChatView.as_view(), name='demos-chat'),
    path('demos/react/', views.ReactDemoView.as_view(), name='demos-react'),
    path('demos/performance/', views.PerformanceTestView.as_view(), name='demos-performance'),
    path('demos/datatable/', views.ProductDataTableView.as_view(), name='demos-datatable'),
    path('demos/rust-components/', views.RustComponentsDemo.as_view(), name='demos-rust-components'),
    path('demos/components-only/', views.ComponentsOnlyDemo.as_view(), name='demos-components-only'),
    path('demos/no-template/', views.NoTemplateDemo.as_view(), name='demos-no-template'),
    path('demos/unified-components/', views.ComponentsDemoView.as_view(), name='demos-unified-components'),
    path('demos/toast/', views.toast_demo, name='demos-toast'),
    path('demos/dropdown/', views.DropdownDemo.as_view(), name='demos-dropdown'),
    path('demos/navbar/', views.NavBarDemoView.as_view(), name='demos-navbar'),
    path('demos/offcanvas/', views.OffcanvasDemoView.as_view(), name='demos-offcanvas'),
    path('demos/component-showcase/', views.ComponentShowcaseView.as_view(), name='demos-component-showcase'),

    # State Management Demos (Phase 2)
    path('demos/debounce/', views.DebounceSearchView.as_view(), name='demos-debounce'),
    path('demos/throttle/', views.ThrottleScrollView.as_view(), name='demos-throttle'),

    # State Management Demos (Phase 3)
    path('demos/optimistic-todo/', views.OptimisticTodoView.as_view(), name='demos-optimistic-todo'),
    path('demos/optimistic-counter/', views.OptimisticCounterView.as_view(), name='demos-optimistic-counter'),

    # Component System Demo (Phase 4)
    path('demos/components/', views.ComponentDemoView.as_view(), name='demos-components'),

    # State Management Demos (Phase 5)
    path('demos/cache/', views.CacheDemoView.as_view(), name='demos-cache'),
    path('demos/cache-test/', views.CacheTestView.as_view(), name='demos-cache-test'),
    path('tests/draft-mode/', views.DraftModeTestView.as_view(), name='tests-draft-mode'),
    path('tests/loading/', views.LoadingTestView.as_view(), name='tests-loading'),

    # Test Suite
    path('tests/', views.TestIndexView.as_view(), name='tests-index'),

    # Component Library Kitchen Sink
    path('kitchen-sink/', views.KitchenSinkView.as_view(), name='kitchen-sink'),

    # Documentation
    path('docs/', views.DocsView.as_view(), name='docs'),
    path('docs/components/', views.ComponentsGuideView.as_view(), name='components-guide'),

    # Django Forms Integration
    path('forms/', views.FormsIndexView.as_view(), name='forms-index'),
    path('forms/registration/', views.RegistrationFormView.as_view(), name='forms-registration'),
    path('forms/contact/', views.ContactFormView.as_view(), name='forms-contact'),
    path('forms/profile/', views.ProfileFormView.as_view(), name='forms-profile'),
    path('forms/simple/', views.SimpleContactFormView.as_view(), name='forms-simple'),

    # Auto-rendered forms (different CSS frameworks)
    path('forms/auto/', views.AutoContactFormView.as_view(), name='forms-auto'),
    path('forms/auto/compare/', views.AutoFormComparisonView.as_view(), name='forms-auto-compare'),
    path('forms/auto/tailwind/', views.AutoContactFormTailwindView.as_view(), name='forms-auto-tailwind'),
    path('forms/auto/plain/', views.AutoContactFormPlainView.as_view(), name='forms-auto-plain'),
]
