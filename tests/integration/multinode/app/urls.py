from django.urls import include, path

from djust.sse import sse_urlpatterns
from tests.integration.multinode.app import views

urlpatterns = [
    path("djust/", include(sse_urlpatterns)),
    path("counter/", views.CounterView.as_view()),
    path("counter2/", views.CounterView.as_view()),
    path("legacy/", views.LegacyCounterView.as_view()),
    path("off/", views.SwitchedOffView.as_view()),
    path("held/", views.HeldView.as_view()),
    path("items/<int:pk>/", views.ItemView.as_view()),
    path("form/", views.FormView.as_view()),
    path("wizard/", views.WizardView.as_view()),
    path("who/", views.WhoView.as_view()),
    path("component/", views.ComponentView.as_view()),
    path("push/", views.PushView.as_view()),
    path("room/", views.RoomView.as_view()),
    path("async/", views.AsyncView.as_view()),
    path("tick/", views.TickView.as_view()),
    path("stream/", views.StreamView.as_view()),
    path("_mn/login/", views.login_view),
    path("_mn/logout/", views.logout_view),
    path("_mn/push/", views.push_view),
    path("_mn/stats/", views.stats_view),
    path("_mn/session-keys/", views.session_keys_view),
    path("_mn/gate/", views.gate_view),
    path("_mn/release/", views.release_view),
]
