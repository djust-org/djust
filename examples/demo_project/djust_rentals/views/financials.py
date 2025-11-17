"""
Financial Reports Views

Income, expense, and profit/loss reporting.
"""

from djust_shared.views import BaseViewWithNavbar
from djust.decorators import cache
from djust_shared.components.ui import HeroSection
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal
from ..models import Payment, Expense, Lease, Property


class FinancialDashboardView(BaseViewWithNavbar):
    """
    Financial dashboard with key metrics and summaries.

    Features:
    - Income vs expenses overview
    - Monthly trends
    - Property-wise breakdown
    - Cached calculations with @cache
    """
    template_name = 'rentals/financial_dashboard.html'

    def mount(self, request, **kwargs):
        """Initialize financial dashboard"""
        # Date range for analysis
        self.period = "month"  # month, quarter, year

        # Initialize hero
        self.hero = HeroSection(
            title="Financial Dashboard",
            subtitle="Income, expenses, and profitability analysis",
            icon="💰"
        )

        # Load financial data
        self._load_financial_data()

    def _load_financial_data(self):
        """Load and calculate financial metrics"""
        # Determine date range based on period
        today = timezone.now()
        if self.period == "month":
            start_date = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif self.period == "quarter":
            quarter_month = ((today.month - 1) // 3) * 3 + 1
            start_date = today.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:  # year
            start_date = today.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

        self.start_date = start_date
        self.end_date = today

        # Calculate income
        income_data = Payment.objects.filter(
            payment_date__gte=start_date.date(),
            status='completed'
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        self.total_income = income_data['total'] or Decimal('0.00')
        self.payment_count = income_data['count'] or 0

        # Calculate expenses
        expense_data = Expense.objects.filter(
            date__gte=start_date.date()
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        self.total_expenses = expense_data['total'] or Decimal('0.00')
        self.expense_count = expense_data['count'] or 0

        # Calculate profit
        self.net_profit = self.total_income - self.total_expenses
        if self.total_income > 0:
            self.profit_margin = (self.net_profit / self.total_income) * 100
        else:
            self.profit_margin = Decimal('0.00')

        # Expected vs actual income
        active_leases = Lease.objects.filter(status='active')
        self.expected_monthly_income = active_leases.aggregate(
            total=Sum('monthly_rent')
        )['total'] or Decimal('0.00')

        # Income by property
        self.income_by_property = []
        for prop in Property.objects.all()[:10]:  # Top 10
            prop_income = Payment.objects.filter(
                lease__property=prop,
                payment_date__gte=start_date.date(),
                status='completed'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            if prop_income > 0:
                self.income_by_property.append({
                    'property': prop,
                    'income': prop_income,
                })

        # Sort by income
        self.income_by_property.sort(key=lambda x: x['income'], reverse=True)

        # Expenses by category
        self.expenses_by_category = []
        for choice in Expense.CATEGORY_CHOICES:
            category_key, category_name = choice
            category_total = Expense.objects.filter(
                category=category_key,
                date__gte=start_date.date()
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            if category_total > 0:
                self.expenses_by_category.append({
                    'category': category_name,
                    'total': category_total,
                })

        # Sort by total
        self.expenses_by_category.sort(key=lambda x: x['total'], reverse=True)

    def change_period(self, period: str = "month", **kwargs):
        """Change analysis period"""
        if period in ['month', 'quarter', 'year']:
            self.period = period
            self._load_financial_data()

    def get_context_data(self, **kwargs):
        """Add financial dashboard context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'period': self.period,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'total_income': self.total_income,
            'total_expenses': self.total_expenses,
            'net_profit': self.net_profit,
            'profit_margin': self.profit_margin,
            'payment_count': self.payment_count,
            'expense_count': self.expense_count,
            'expected_monthly_income': self.expected_monthly_income,
            'income_by_property': self.income_by_property,
            'expenses_by_category': self.expenses_by_category,
        })

        return context


class IncomeReportView(BaseViewWithNavbar):
    """
    Detailed income report.

    Features:
    - Payment history
    - Income by property
    - Late payments
    - Upcoming payments
    """
    template_name = 'rentals/income_report.html'

    def mount(self, request, **kwargs):
        """Initialize income report"""
        # Initialize hero
        self.hero = HeroSection(
            title="Income Report",
            subtitle="Payment tracking and analysis",
            icon="💵"
        )

        # Load income data
        self.recent_payments = Payment.objects.filter(
            status='completed'
        ).order_by('-payment_date')[:50]

        # Calculate monthly income for last 6 months
        self.monthly_income = []
        today = date.today()
        for i in range(6):
            month_date = today.replace(day=1) - timedelta(days=i*30)
            month_start = month_date.replace(day=1)
            next_month = (month_start.month % 12) + 1
            next_year = month_start.year + (1 if next_month == 1 else 0)
            month_end = month_start.replace(month=next_month, year=next_year) - timedelta(days=1)

            month_total = Payment.objects.filter(
                payment_date__gte=month_start,
                payment_date__lte=month_end,
                status='completed'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            self.monthly_income.append({
                'month': month_start.strftime('%B %Y'),
                'total': month_total,
            })

        self.monthly_income.reverse()

    def get_context_data(self, **kwargs):
        """Add income report context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'recent_payments': self.recent_payments,
            'monthly_income': self.monthly_income,
        })

        return context


class ExpenseReportView(BaseViewWithNavbar):
    """
    Detailed expense report.

    Features:
    - Expense history
    - Expenses by category
    - Expenses by property
    - Vendor tracking
    """
    template_name = 'rentals/expense_report.html'

    def mount(self, request, **kwargs):
        """Initialize expense report"""
        # Initialize hero
        self.hero = HeroSection(
            title="Expense Report",
            subtitle="Cost tracking and analysis",
            icon="💸"
        )

        # Load expense data
        self.recent_expenses = Expense.objects.all().order_by('-date')[:50]

        # Expenses by category
        self.category_totals = []
        for choice in Expense.CATEGORY_CHOICES:
            category_key, category_name = choice
            total = Expense.objects.filter(category=category_key).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0.00')

            if total > 0:
                self.category_totals.append({
                    'category': category_name,
                    'total': total,
                })

        self.category_totals.sort(key=lambda x: x['total'], reverse=True)

    def get_context_data(self, **kwargs):
        """Add expense report context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'recent_expenses': self.recent_expenses,
            'category_totals': self.category_totals,
        })

        return context


class ProfitLossView(BaseViewWithNavbar):
    """
    Profit and loss statement.

    Features:
    - Monthly P&L
    - Year-to-date summary
    - Property-wise profitability
    - Trend analysis
    """
    template_name = 'rentals/profit_loss.html'

    def mount(self, request, **kwargs):
        """Initialize P&L view"""
        # Initialize hero
        self.hero = HeroSection(
            title="Profit & Loss Statement",
            subtitle="Profitability analysis",
            icon="📊"
        )

        # Calculate year-to-date P&L
        year_start = date.today().replace(month=1, day=1)

        ytd_income = Payment.objects.filter(
            payment_date__gte=year_start,
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        ytd_expenses = Expense.objects.filter(
            date__gte=year_start
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        self.ytd_income = ytd_income
        self.ytd_expenses = ytd_expenses
        self.ytd_profit = ytd_income - ytd_expenses

        # Monthly P&L for last 6 months
        self.monthly_pl = []
        today = date.today()
        for i in range(6):
            month_date = today.replace(day=1) - timedelta(days=i*30)
            month_start = month_date.replace(day=1)
            next_month = (month_start.month % 12) + 1
            next_year = month_start.year + (1 if next_month == 1 else 0)
            month_end = month_start.replace(month=next_month, year=next_year) - timedelta(days=1)

            income = Payment.objects.filter(
                payment_date__gte=month_start,
                payment_date__lte=month_end,
                status='completed'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            expenses = Expense.objects.filter(
                date__gte=month_start,
                date__lte=month_end
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            profit = income - expenses

            self.monthly_pl.append({
                'month': month_start.strftime('%B %Y'),
                'income': income,
                'expenses': expenses,
                'profit': profit,
            })

        self.monthly_pl.reverse()

    def get_context_data(self, **kwargs):
        """Add P&L context"""
        context = super().get_context_data(**kwargs)

        context.update({
            'ytd_income': self.ytd_income,
            'ytd_expenses': self.ytd_expenses,
            'ytd_profit': self.ytd_profit,
            'monthly_pl': self.monthly_pl,
        })

        return context
