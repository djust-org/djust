"""
Observability URL patterns. Include via:

    urlpatterns = [
        path("_djust/observability/", include("djust.observability.urls")),
        ...
    ]

Each Phase 7.x PR adds more routes below `health`. Routes are
additionally DEBUG-gated at the view level so a stray include in a
production config still refuses to serve data.
"""

from django.urls import path

from djust.observability.views import (
    eval_handler,
    handler_timings,
    health,
    last_traceback,
    log_tail,
    reset_view_state,
    sql_queries,
    view_assigns,
)

app_name = "djust_observability"

urlpatterns = [
    path("health/", health, name="health"),
    path("view_assigns/", view_assigns, name="view_assigns"),
    path("last_traceback/", last_traceback, name="last_traceback"),
    path("log/", log_tail, name="log"),
    path("handler_timings/", handler_timings, name="handler_timings"),
    path("sql_queries/", sql_queries, name="sql_queries"),
    path("reset_view_state/", reset_view_state, name="reset_view_state"),
    path("eval_handler/", eval_handler, name="eval_handler"),
]
