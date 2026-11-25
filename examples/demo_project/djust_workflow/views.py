"""List, detail and edit pages for ``Ticket``, added to a Django app one piece at a time.

Guide: docs/website/guides/incremental-django-workflow.md
"""

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from djust import LiveView, state
from djust.decorators import event_handler
from djust.decorators import permission_required as require_permission
from djust.forms import FormMixin, ModelFormMixin

from .forms import TicketForm, TicketListParams
from .models import Ticket

PAGE_SIZE = 5


class NotificationError(Exception):
    """The requester could not be told that their ticket was closed."""


def notify_requester(ticket):
    """Stand-in for a slow or flaky call (mail, chat, webhook). Tests make it fail."""


class TicketListView(LiveView):
    template_name = "djust_workflow/ticket_list.html"
    login_required = True
    permission_required = "djust_workflow.view_ticket"

    # The URL is the source of truth: these three come from the query string.
    params_form = TicketListParams
    params_form_fallback = "fields"
    status = state("all")
    q = state("")
    page = state(1)
    # Declared so the messages reach the template: an undeclared attribute is not rendered.
    params_errors = state(default_factory=dict)

    def handle_params(self, params, uri):
        self.status = params["status"]
        self.q = params["q"]
        self.page = params["page"]

    @require_permission("djust_workflow.view_ticket")
    @event_handler()
    def apply_filter(self, status: str = "all", q: str = ""):
        # Only the URL changes here. The browser then reports the new URL and
        # ``handle_params`` validates it, so a typed ``?page=abc`` and a form
        # submit go through the same declaration.
        self.live_patch(params={"status": status, "q": q.strip(), "page": 1})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tickets = Ticket.objects.visible_to(self.request.user).select_related("created_by")
        if self.status != "all":
            tickets = tickets.filter(status=self.status)
        if self.q:
            tickets = tickets.filter(Q(title__icontains=self.q) | Q(body__icontains=self.q))
        page = Paginator(tickets, PAGE_SIZE).get_page(self.page)
        context.update(
            tickets=list(page.object_list),
            page_number=page.number,
            num_pages=page.paginator.num_pages,
            total=page.paginator.count,
            prev_page=page.number - 1 if page.has_previous() else None,
            next_page=page.number + 1 if page.has_next() else None,
            can_add=self.request.user.has_perm("djust_workflow.add_ticket"),
        )
        return context


class TicketDetailView(LiveView):
    template_name = "djust_workflow/ticket_detail.html"
    login_required = True
    permission_required = "djust_workflow.view_ticket"

    error_message = state("")
    success_message = state("")

    def mount(self, request, pk=None, **kwargs):
        self.ticket_pk = pk  # the URL's id; the object itself is read fresh each time

    def get_object(self):
        return Ticket.objects.select_related("created_by").get(pk=self.ticket_pk)

    def has_object_permission(self, request, obj):
        return Ticket.objects.visible_to(request.user).filter(pk=obj.pk).exists()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ticket"] = self.get_object()
        context["can_close"] = self.request.user.has_perm("djust_workflow.change_ticket")
        return context

    @require_permission("djust_workflow.change_ticket")
    @event_handler()
    def close_ticket(self):
        self.error_message = ""
        self.success_message = ""
        try:
            with transaction.atomic():
                ticket = Ticket.objects.select_for_update().get(pk=self.ticket_pk)
                if ticket.status == Ticket.Status.CLOSED:
                    self.error_message = "This ticket was already closed."
                    return
                ticket.status = Ticket.Status.CLOSED
                ticket.save(update_fields=["status"])
                notify_requester(ticket)
        except NotificationError:
            # The transaction rolled back, so the ticket is still open.
            self.error_message = "The requester could not be notified, so the ticket stays open."
            return
        self.success_message = "Ticket closed."


class TicketCreateView(FormMixin, LiveView):
    template_name = "djust_workflow/ticket_form.html"
    login_required = True
    permission_required = "djust_workflow.add_ticket"
    form_class = TicketForm

    def form_valid(self, form):
        ticket = form.save(commit=False)
        ticket.created_by = self.request.user
        ticket.save()
        self.live_redirect(reverse("djust_workflow:detail", kwargs={"pk": ticket.pk}))

    def form_invalid(self, form):
        self.error_message = "Please correct the errors below."


class TicketEditView(ModelFormMixin[Ticket], LiveView):
    template_name = "djust_workflow/ticket_form.html"
    login_required = True
    permission_required = "djust_workflow.change_ticket"
    model = Ticket
    form_class = TicketForm

    def get_queryset(self):
        return Ticket.objects.visible_to(self.request.user)

    def form_valid(self, form):
        self.object = form.save()
        self.live_redirect(reverse("djust_workflow:detail", kwargs={"pk": self.object.pk}))

    def form_invalid(self, form):
        self.error_message = "Please correct the errors below."
