"""
Template strings for ``manage.py djust_gen_live`` scaffolding generator.

All templates use ``%(variable)s`` Python string substitution to avoid
conflicts with Django template syntax (``{%%}`` / ``{{ }}``).

Django template tags inside these strings are escaped as ``{%% ... %%}``
so that ``%`` formatting does not consume them.
"""

# ---------------------------------------------------------------------------
# views.py — HTML mode (standard LiveView with CRUD)
# ---------------------------------------------------------------------------

VIEWS_PY_TEMPLATE = """\
\"\"\"LiveView for %(app_name)s %(model_name)s CRUD.\"\"\"

%(q_import)s\
from djust import LiveView
from djust.decorators import event_handler

from .models import %(model_name)s


class %(view_class)s(LiveView):
    template_name = "%(app_name)s/%(model_slug)s_list.html"

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.selected = None
        self._compute()

    def _compute(self):
        \"\"\"Recompute item list from database.\"\"\"
        qs = %(model_name)s.objects.all()
        if self.search_query:
%(search_filter)s\
        self.items = list(qs)
        self.item_count = len(self.items)

    @event_handler()
    def search(self, value: str = "", **kwargs):
        \"\"\"Filter items by search query.\"\"\"
        self.search_query = value
        self._compute()

    @event_handler()
    def show(self, item_id: int = 0, **kwargs):
        \"\"\"Show detail panel for a single item.\"\"\"
        try:
            self.selected = %(model_name)s.objects.get(pk=item_id)
        except %(model_name)s.DoesNotExist:
            self.selected = None

    @event_handler()
    def close_panel(self, **kwargs):
        \"\"\"Close the detail panel.\"\"\"
        self.selected = None

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        \"\"\"Delete an item.\"\"\"
        %(model_name)s.objects.filter(pk=item_id).delete()
        self.selected = None
        self._compute()

    @event_handler()
    def create(self, %(create_params)s**kwargs):
        \"\"\"Create a new %(model_name)s.\"\"\"
%(create_body)s\
        self._compute()

    @event_handler()
    def update(self, item_id: int = 0, %(update_params)s**kwargs):
        \"\"\"Update an existing %(model_name)s.\"\"\"
%(update_body)s\
        self._compute()

    def get_context_data(self, **kwargs):
        self._compute()
        return {
            "items": self.items,
            "selected": self.selected,
            "search_query": self.search_query,
            "item_count": self.item_count,
        }
"""

# ---------------------------------------------------------------------------
# views.py — API mode (render_json instead of HTML template)
# ---------------------------------------------------------------------------

VIEWS_PY_API_TEMPLATE = """\
\"\"\"LiveView JSON API for %(app_name)s %(model_name)s.\"\"\"

%(q_import)s\
from djust import LiveView
from djust.decorators import event_handler

from .models import %(model_name)s


class %(view_class)s(LiveView):
    \"\"\"JSON API for %(model_name)s CRUD.\"\"\"

    def mount(self, request, **kwargs):
        self.search_query = ""
        self._compute()

    def _compute(self):
        \"\"\"Recompute item list from database.\"\"\"
        qs = %(model_name)s.objects.all()
        if self.search_query:
%(search_filter)s\
        self.items = list(qs.values())
        self.item_count = len(self.items)

    @event_handler()
    def search(self, value: str = "", **kwargs):
        \"\"\"Filter items by search query.\"\"\"
        self.search_query = value
        self._compute()

    @event_handler()
    def create(self, %(create_params)s**kwargs):
        \"\"\"Create a new %(model_name)s.\"\"\"
%(create_body)s\
        self._compute()

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        \"\"\"Delete an item.\"\"\"
        %(model_name)s.objects.filter(pk=item_id).delete()
        self._compute()

    def render_json(self):
        \"\"\"Return JSON representation of the view state.\"\"\"
        return {
            "items": self.items,
            "search_query": self.search_query,
            "item_count": self.item_count,
        }
"""

# ---------------------------------------------------------------------------
# urls.py — uses live_session() (Bug #8 fix)
# ---------------------------------------------------------------------------

URLS_PY_TEMPLATE = """\
\"\"\"URL configuration for %(app_name)s %(model_name)s.\"\"\"

from django.urls import path

from djust.routing import live_session

from .views import %(view_class)s

urlpatterns = [
    *live_session("/%(app_name)s", [
        path("%(model_slug)s/", %(view_class)s.as_view(), name="%(url_name)s"),
    ]),
]
"""

# ---------------------------------------------------------------------------
# HTML template — list + detail panel with dj-* directives
# ---------------------------------------------------------------------------

LIST_TEMPLATE = """\
{%% extends "%(app_name)s/base.html" %%}

{%% block title %%}%(model_display_plural)s — %(app_display)s{%% endblock %%}

{%% block content %%}
{%% csrf_token %%}
<div dj-root dj-view="%(app_name)s.views.%(view_class)s">

    <!-- Header -->
    <div class="flex items-center justify-between mb-6">
        <h1 class="text-2xl font-bold text-white">%(model_display_plural)s</h1>
        <span class="text-sm text-gray-400">{{ item_count }} total</span>
    </div>

    <!-- Search -->
    <div class="mb-6">
        <input type="text"
               dj-input="search"
               name="value"
               value="{{ search_query }}"
               placeholder="Search %(model_display_plural_lower)s..."
               class="w-full px-4 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">
    </div>

    <!-- Create form -->
    <form dj-submit="create" class="glass rounded-lg p-4 mb-6 grid grid-cols-1 sm:grid-cols-2 gap-3">
%(form_fields_html)s\
        <div class="sm:col-span-2">
            <button type="submit"
                    class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 transition"
                    dj-loading.class="opacity-50" dj-loading.disable>
                Add %(model_display_singular)s
            </button>
        </div>
    </form>

    <div class="flex gap-6">
        <!-- List panel -->
        <div class="flex-1 space-y-2">
            {%% for item in items %%}
            <div class="glass rounded-lg px-4 py-3 flex items-center justify-between group cursor-pointer"
                 dj-click="show"
                 dj-value-item_id:int="{{ item.pk }}">
                <div class="flex items-center gap-4">
%(list_item_fields)s\
                </div>
                <button dj-click="delete"
                        dj-value-item_id:int="{{ item.pk }}"
                        dj-confirm="Delete this %(model_display_singular_lower)s?"
                        class="text-red-400 opacity-0 group-hover:opacity-100 hover:text-red-300 transition text-sm">
                    Delete
                </button>
            </div>
            {%% empty %%}
            <div class="text-center text-gray-500 py-12">
                {%% if search_query %%}
                    No %(model_display_plural_lower)s match "{{ search_query }}"
                {%% else %%}
                    No %(model_display_plural_lower)s yet. Add one above!
                {%% endif %%}
            </div>
            {%% endfor %%}
        </div>

        <!-- Detail / edit panel -->
        {%% if selected %%}
        <div class="w-80 glass rounded-lg p-4">
            <div class="flex items-center justify-between mb-4">
                <h2 class="text-lg font-semibold text-white">Edit %(model_display_singular)s</h2>
                <button dj-click="close_panel" class="text-gray-400 hover:text-white text-sm">Close</button>
            </div>
            <form dj-submit="update" class="space-y-3">
                <input type="hidden" name="item_id" value="{{ selected.pk }}">
%(show_panel_fields)s\
                <button type="submit"
                        class="w-full px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 transition"
                        dj-loading.class="opacity-50" dj-loading.disable>
                    Save
                </button>
            </form>
        </div>
        {%% endif %%}
    </div>

</div>
{%% endblock %%}
"""

# ---------------------------------------------------------------------------
# tests.py — generated test file using LiveViewTestClient pattern
# ---------------------------------------------------------------------------

TESTS_PY_TEMPLATE = """\
\"\"\"Tests for %(app_name)s %(model_name)s LiveView.\"\"\"

from django.test import TestCase

from .models import %(model_name)s
from .views import %(view_class)s


class %(view_class)sTest(TestCase):
    \"\"\"Tests for %(view_class)s.\"\"\"

    def test_mount(self):
        \"\"\"View mounts with empty state.\"\"\"
        view = %(view_class)s()
        # Verify class exists and can be instantiated
        self.assertIsNotNone(view)

    def test_model_exists(self):
        \"\"\"%(model_name)s model is importable.\"\"\"
        self.assertTrue(hasattr(%(model_name)s, "objects"))
"""

# ---------------------------------------------------------------------------
# Field-type to form input mapping
# ---------------------------------------------------------------------------

FORM_INPUT_MAP = {
    "string": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="text" name="%(name)s" placeholder="%(label)s"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "text": (
        '        <div class="sm:col-span-2">\n'
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <textarea name="%(name)s" rows="3" placeholder="%(label)s"\n'
        '                      class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500"></textarea>\n'
        "        </div>\n"
    ),
    "integer": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="number" name="%(name)s" placeholder="0"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "float": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="number" step="any" name="%(name)s" placeholder="0.0"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "decimal": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="number" step="0.01" name="%(name)s" placeholder="0.00"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "boolean": (
        '        <div class="flex items-center gap-2">\n'
        '            <input type="checkbox" name="%(name)s" value="1"\n'
        '                   class="rounded border-gray-500 bg-surface-800'
        ' text-indigo-500 focus:ring-indigo-500">\n'
        '            <label class="text-sm text-gray-400">%(label)s</label>\n'
        "        </div>\n"
    ),
    "date": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="date" name="%(name)s"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "datetime": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="datetime-local" name="%(name)s"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "email": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="email" name="%(name)s" placeholder="%(label)s"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "url": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="url" name="%(name)s" placeholder="https://"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "slug": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="text" name="%(name)s" placeholder="%(label)s"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
    "fk": (
        "        <div>\n"
        '            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '            <input type="number" name="%(name)s_id" placeholder="%(label)s ID"\n'
        '                   class="w-full px-3 py-2 rounded-lg bg-surface-800 border'
        " border-white/10 text-gray-200 placeholder-gray-500"
        ' focus:outline-none focus:border-indigo-500">\n'
        "        </div>\n"
    ),
}

# ---------------------------------------------------------------------------
# Show panel field mapping (detail/edit view)
# ---------------------------------------------------------------------------

SHOW_FIELD_MAP = {
    "string": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="text" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "text": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <textarea name="%(name)s" rows="3"\n'
        '                              class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">'
        "{{ selected.%(name)s }}</textarea>\n"
        "                </div>\n"
    ),
    "integer": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="number" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "float": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="number" step="any" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "decimal": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="number" step="0.01" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "boolean": (
        '                <div class="flex items-center gap-2">\n'
        '                    <input type="checkbox" name="%(name)s" value="1"'
        " {%% if selected.%(name)s %%}checked{%% endif %%}\n"
        '                           class="rounded border-gray-500 bg-surface-800'
        ' text-indigo-500 focus:ring-indigo-500">\n'
        '                    <label class="text-sm text-gray-400">%(label)s</label>\n'
        "                </div>\n"
    ),
    "date": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="date" name="%(name)s"'
        " value=\"{{ selected.%(name)s|date:'Y-m-d' }}\"\n"
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "datetime": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="datetime-local" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "email": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="email" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "url": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="url" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "slug": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="text" name="%(name)s"'
        ' value="{{ selected.%(name)s }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
    "fk": (
        "                <div>\n"
        '                    <label class="block text-sm text-gray-400 mb-1">%(label)s</label>\n'
        '                    <input type="number" name="%(name)s_id"'
        ' value="{{ selected.%(name)s_id }}"\n'
        '                           class="w-full px-3 py-2 rounded-lg bg-surface-800'
        " border border-white/10 text-gray-200"
        ' focus:outline-none focus:border-indigo-500">\n'
        "                </div>\n"
    ),
}

# ---------------------------------------------------------------------------
# List item field display (first N fields in the list row)
# ---------------------------------------------------------------------------

LIST_FIELD_DISPLAY = {
    "boolean": (
        '                    <span class="text-gray-400 text-sm">'
        "{%% if item.%(name)s %%}"
        '<span class="text-emerald-400">Yes</span>'
        "{%% else %%}"
        '<span class="text-gray-500">No</span>'
        "{%% endif %%}"
        "</span>\n"
    ),
    "default": ('                    <span class="text-gray-200">{{ item.%(name)s }}</span>\n'),
}
