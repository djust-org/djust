"""
URL configuration for marketing app.
"""
from django.urls import path
from marketing import views

app_name = 'marketing'

urlpatterns = [
    # Homepage
    path('', views.HomeView.as_view(), name='home'),

    # Main pages
    path('features/', views.FeaturesView.as_view(), name='features'),
    path('security/', views.SecurityView.as_view(), name='security'),
    path('examples/', views.ExamplesView.as_view(), name='examples'),
    path('playground/', views.PlaygroundView.as_view(), name='playground'),
    path('comparison/', views.ComparisonView.as_view(), name='comparison'),
    path('benchmarks/', views.BenchmarksView.as_view(), name='benchmarks'),
    path('use-cases/', views.UseCasesView.as_view(), name='use_cases'),
    path('pricing/', views.PricingView.as_view(), name='pricing'),
    path('quickstart/', views.QuickStartView.as_view(), name='quickstart'),
    path('faq/', views.FAQView.as_view(), name='faq'),
]
