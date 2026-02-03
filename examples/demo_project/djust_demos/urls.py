"""
URL configuration for djust_demos app.
"""

from django.urls import path
from .views import *

app_name = 'demos'

urlpatterns = [
    # Demos Index
    path('', DemosIndexView.as_view(), name='index'),

    # Design Proposals
    path('design1/', DemosIndexDesign1View.as_view(), name='design1'),
    path('design2/', DemosIndexDesign2View.as_view(), name='design2'),
    path('design3/', DemosIndexDesign3View.as_view(), name='design3'),
    path('hybrid/', DemosIndexHybridView.as_view(), name='hybrid'),
    path('shadcn/', DemosIndexShadcnView.as_view(), name='shadcn'),

    # Basic Demos
    path('counter/', CounterView.as_view(), name='counter'),
    path('todo/', TodoView.as_view(), name='todo'),
    path('chat/', ChatView.as_view(), name='chat'),
    path('react/', ReactDemoView.as_view(), name='react'),
    path('performance/', PerformanceTestView.as_view(), name='performance'),
    path('datatable/', ProductDataTableView.as_view(), name='datatable'),

    # Component Demos
    path('rust-components/', RustComponentsDemo.as_view(), name='rust-components'),
    path('components-only/', ComponentsOnlyDemo.as_view(), name='components-only'),
    path('no-template/', NoTemplateDemo.as_view(), name='no-template'),
    path('unified-components/', ComponentsDemoView.as_view(), name='unified-components'),
    path('toast/', toast_demo, name='toast'),
    path('dropdown/', DropdownDemo.as_view(), name='dropdown'),
    path('navbar/', NavBarDemoView.as_view(), name='navbar'),
    path('offcanvas/', OffcanvasDemoView.as_view(), name='offcanvas'),
    path('component-showcase/', ComponentShowcaseView.as_view(), name='component-showcase'),

    # State Management Demos (Phase 2)
    path('debounce/', DebounceSearchView.as_view(), name='debounce'),
    path('throttle/', ThrottleScrollView.as_view(), name='throttle'),

    # State Management Demos (Phase 3)
    path('optimistic-todo/', OptimisticTodoView.as_view(), name='optimistic-todo'),
    path('optimistic-counter/', OptimisticCounterView.as_view(), name='optimistic-counter'),

    # Component System Demo (Phase 4)
    path('components/', ComponentDemoView.as_view(), name='components'),

    # State Management Demos (Phase 5)
    path('cache/', CacheDemoView.as_view(), name='cache'),
    path('smart-dashboard/', SmartDashboardView.as_view(), name='smart-dashboard'),

    # Kitchen Sink
    path('kitchen-sink/', KitchenSinkView.as_view(), name='kitchen-sink'),

    # Streaming Demo
    path('streaming/', StreamingDemoView.as_view(), name='streaming'),

    # Navigation Demo
    path('navigation/', NavigationDemoView.as_view(), name='navigation'),

    # LiveForm Demo
    path('live-form/', LiveFormDemoView.as_view(), name='live-form'),

    # Uploads Demo
    path('uploads/', UploadsDemoView.as_view(), name='uploads'),
]
