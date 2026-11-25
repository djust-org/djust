from django.urls import path

from .views import TicketCreateView, TicketDetailView, TicketEditView, TicketListView

app_name = "djust_workflow"

urlpatterns = [
    path("", TicketListView.as_view(), name="list"),
    path("new/", TicketCreateView.as_view(), name="create"),
    path("<int:pk>/", TicketDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", TicketEditView.as_view(), name="edit"),
]
