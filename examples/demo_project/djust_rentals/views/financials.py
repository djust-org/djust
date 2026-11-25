"""
Financial Reports Views

Income, expense, and profit/loss reporting.
"""

from djust import state
from djust_shared.views import BaseViewWithNavbar
from djust.decorators import event_handler
from djust.decorators import cache
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal
from ..models import Payment, Expense, Lease, Property
from ..components import StatCard, PageHeader


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

    # Date range for analysis; persist="server" keeps it across the HTTP
    # fallback and a reconnect. The figures are computed per render from it.
    period = state("month", persist="server")  # month, quarter, year

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

        self._start_date = start_date
        self._end_date = today

        # Calculate income
        income_data = Payment.objects.filter(
            payment_date__gte=start_date.date(),
            status='completed'
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        self._total_income = income_data['total'] or Decimal('0.00')
        self._payment_count = income_data['count'] or 0

        # Calculate expenses
        expense_data = Expense.objects.filter(
            date__gte=start_date.date()
        ).aggregate(
            total=Sum('amount'),
            count=Count('id')
        )

        self._total_expenses = expense_data['total'] or Decimal('0.00')
        self._expense_count = expense_data['count'] or 0

        # Calculate profit
        self._net_profit = self._total_income - self._total_expenses
        if self._total_income > 0:
            self._profit_margin = (self._net_profit / self._total_income) * 100
        else:
            self._profit_margin = Decimal('0.00')

        # Expected vs actual income
        active_leases = Lease.objects.filter(status='active')
        self._expected_monthly_income = active_leases.aggregate(
            total=Sum('monthly_rent')
        )['total'] or Decimal('0.00')

        # Income by property
        self._income_by_property = []
        for prop in Property.objects.all()[:10]:  # Top 10
            prop_income = Payment.objects.filter(
                lease__property=prop,
                payment_date__gte=start_date.date(),
                status='completed'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            if prop_income > 0:
                self._income_by_property.append({
                    'property': prop,
                    'income': prop_income,
                })

        # Sort by income
        self._income_by_property.sort(key=lambda x: x['income'], reverse=True)

        # Expenses by category
        self._expenses_by_category = []
        for choice in Expense.CATEGORY_CHOICES:
            category_key, category_name = choice
            category_total = Expense.objects.filter(
                category=category_key,
                date__gte=start_date.date()
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

            if category_total > 0:
                self._expenses_by_category.append({
                    'category': category_name,
                    'total': category_total,
                })

        # Sort by total
        self._expenses_by_category.sort(key=lambda x: x['total'], reverse=True)

    @event_handler
    def change_period(self, period: str = "month", **kwargs):
        """Change analysis period"""
        if period in ['month', 'quarter', 'year']:
            self.period = period

    def get_context_data(self, **kwargs):
        """Add financial dashboard context"""
        self._load_financial_data()
        context = super().get_context_data(**kwargs)

        # Create page header (do this in get_context_data so it's available for every render)
        page_header = PageHeader(
            title="Financial Dashboard",
            subtitle="Income, expenses, and profitability analysis",
            icon="dollar-sign"
        )

        # Create StatCard components for key financial metrics (render to HTML)
        stat_cards_html = [
            StatCard(
                label="Total Income",
                value=f"${self._total_income:,.0f}",
                icon="trending-up",
                color="green",
                trend=f"{self._payment_count} payments" if self._payment_count else None
            ).render(),
            StatCard(
                label="Total Expenses",
                value=f"${self._total_expenses:,.0f}",
                icon="trending-down",
                color="red",
                trend=f"{self._expense_count} expenses" if self._expense_count else None
            ).render(),
            StatCard(
                label="Net Profit",
                value=f"${self._net_profit:,.0f}",
                icon="dollar-sign",
                color="green" if self._net_profit >= 0 else "red"
            ).render(),
            StatCard(
                label="Profit Margin",
                value=f"{self._profit_margin:.1f}%",
                icon="percent",
                color="primary"
            ).render(),
        ]

        context.update({
            # Components (rendered to HTML strings)
            'page_header': page_header.render(),
            'stat_cards': stat_cards_html,

            # Financial data
            'period': self.period,
            'start_date': self._start_date,
            'end_date': self._end_date,
            'total_income': self._total_income,
            'total_expenses': self._total_expenses,
            'net_profit': self._net_profit,
            'profit_margin': self._profit_margin,
            'payment_count': self._payment_count,
            'expense_count': self._expense_count,
            'expected_monthly_income': self._expected_monthly_income,
            'income_by_property': self._income_by_property,
            'expenses_by_category': self._expenses_by_category,
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
        # Load income data
        self._recent_payments = Payment.objects.filter(
            status='completed'
        ).order_by('-payment_date')[:50]

        # Calculate monthly income for last 6 months
        self._monthly_income = []
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

            self._monthly_income.append({
                'month': month_start.strftime('%B %Y'),
                'total': month_total,
            })

        self._monthly_income.reverse()

    def get_context_data(self, **kwargs):
        """Add income report context"""
        context = super().get_context_data(**kwargs)

        # Create page header
        page_header = PageHeader(
            title="Income Report",
            subtitle="Payment tracking and analysis",
            icon="dollar-sign"
        )

        context.update({
            'page_header': page_header.render(),
            'recent_payments': self._recent_payments,
            'monthly_income': self._monthly_income,
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
        # Load expense data
        self._recent_expenses = Expense.objects.all().order_by('-date')[:50]

        # Expenses by category
        self._category_totals = []
        for choice in Expense.CATEGORY_CHOICES:
            category_key, category_name = choice
            total = Expense.objects.filter(category=category_key).aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0.00')

            if total > 0:
                self._category_totals.append({
                    'category': category_name,
                    'total': total,
                })

        self._category_totals.sort(key=lambda x: x['total'], reverse=True)

    def get_context_data(self, **kwargs):
        """Add expense report context"""
        context = super().get_context_data(**kwargs)

        # Create page header
        page_header = PageHeader(
            title="Expense Report",
            subtitle="Cost tracking and analysis",
            icon="trending-down"
        )

        context.update({
            'page_header': page_header.render(),
            'recent_expenses': self._recent_expenses,
            'category_totals': self._category_totals,
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
        # Calculate year-to-date P&L
        year_start = date.today().replace(month=1, day=1)

        ytd_income = Payment.objects.filter(
            payment_date__gte=year_start,
            status='completed'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        ytd_expenses = Expense.objects.filter(
            date__gte=year_start
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        self._ytd_income = ytd_income
        self._ytd_expenses = ytd_expenses
        self._ytd_profit = ytd_income - ytd_expenses

        # Monthly P&L for last 6 months
        self._monthly_pl = []
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

            self._monthly_pl.append({
                'month': month_start.strftime('%B %Y'),
                'income': income,
                'expenses': expenses,
                'profit': profit,
            })

        self._monthly_pl.reverse()

    def get_context_data(self, **kwargs):
        """Add P&L context"""
        context = super().get_context_data(**kwargs)

        # Create page header
        page_header = PageHeader(
            title="Profit & Loss Statement",
            subtitle="Profitability analysis",
            icon="bar-chart-2"
        )

        context.update({
            'page_header': page_header.render(),
            'ytd_income': self._ytd_income,
            'ytd_expenses': self._ytd_expenses,
            'ytd_profit': self._ytd_profit,
            'monthly_pl': self._monthly_pl,
        })

        return context
