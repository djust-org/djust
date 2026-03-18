"""
Template strings for ``djust_gen_live`` scaffolding.

All templates use ``%(variable)s`` substitution to avoid conflicts with
Django template syntax (``{%%}``, ``{{ }}``).

Template types supported:
    - string: CharField(max_length=255)
    - text: TextField()
    - integer: IntegerField()
    - float: FloatField()
    - boolean: BooleanField(default=False)
    - date: DateField()
    - datetime: DateTimeField()
    - email: EmailField()
    - url: URLField()
    - slug: SlugField(unique=True)
    - decimal: DecimalField(max_digits=10, decimal_places=2)
    - fk:ModelName: ForeignKey(ModelName, on_delete=models.CASCADE)
"""

# ---------------------------------------------------------------------------
# Django model field template
# ---------------------------------------------------------------------------

MODEL_FIELD_MAP = {
    "string": "    %(name)s = models.CharField(max_length=255, blank=True)\n",
    "text": "    %(name)s = models.TextField(blank=True)\n",
    "integer": "    %(name)s = models.IntegerField(default=0)\n",
    "float": "    %(name)s = models.FloatField(default=0.0)\n",
    "boolean": "    %(name)s = models.BooleanField(default=False)\n",
    "date": "    %(name)s = models.DateField(null=True, blank=True)\n",
    "datetime": "    %(name)s = models.DateTimeField(null=True, blank=True)\n",
    "email": "    %(name)s = models.EmailField(max_length=254, blank=True)\n",
    "url": "    %(name)s = models.URLField(max_length=200, blank=True)\n",
    "slug": "    %(name)s = models.SlugField(unique=True, blank=True)\n",
    "decimal": "    %(name)s = models.DecimalField(max_digits=10, decimal_places=2, default=0)\n",
}

MODEL_FIELD_FK = '    %(name)s = models.ForeignKey(%(related)s, on_delete=models.CASCADE, related_name="%(name)s")\n'


# ---------------------------------------------------------------------------
# LiveView views.py template (standard HTML mode)
# ---------------------------------------------------------------------------

VIEWS_PY_TEMPLATE = """\
\"\"\"LiveView for %(app_name)s %(model_name)s CRUD.\"\"\"

from djust import LiveView
from djust.decorators import event_handler
%(model_import)s


class %(view_class)s(LiveView):
    template_name = "%(app_name)s/%(model_slug)s_list.html"

    def mount(self, request, **kwargs):
        self.search_query = ""
        self.selected = None
        self._compute()

    def _compute(self):
        \"\"\"Recompute from database.\"\"\"
        qs = %(model_name)s.objects.all()
        if self.search_query:
%(search_filter)s\
        self.items = list(qs)

%(computed_props)s\
    @event_handler()
    def search(self, value: str = "", **kwargs):
        \"\"\"Filter by search query.\"\"\"
        self.search_query = value
        self._compute()

    @event_handler()
    def show(self, item_id: int = 0, **kwargs):
        \"\"\"Show a single record.\"\"\"
        try:
            self.selected = %(model_name)s.objects.get(pk=item_id)
        except %(model_name)s.DoesNotExist:
            self.selected = None

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        \"\"\"Delete a record.\"\"\"
        %(model_name)s.objects.filter(pk=item_id).delete()
        self.selected = None
        self._compute()

    @event_handler()
    def create(%(create_params)s, **kwargs):
        \"\"\"Create a new record.\"\"\"
%(create_body)s\
        self._compute()

    @event_handler()
    def update(self, item_id: int = 0, %(update_params)s, **kwargs):
        \"\"\"Update an existing record.\"\"\"
        try:
            obj = %(model_name)s.objects.get(pk=item_id)
%(update_body)s\
            obj.save()
            self.selected = obj
        except %(model_name)s.DoesNotExist:
            pass
        self._compute()

    def get_context_data(self, **kwargs):
        self._compute()
        return {
            "items": self.items,
            "selected": self.selected,
            "search_query": self.search_query,
%(context_data)s\
        }
"""


# ---------------------------------------------------------------------------
# LiveView views.py template (API mode with render_json)
# ---------------------------------------------------------------------------

VIEWS_API_TEMPLATE = """\
# LiveView API for %(app_name)s %(model_name)s CRUD - JSON responses.
from djust import LiveView
from djust.decorators import event_handler
%(model_import)s


class %(view_class)s(LiveView):
    '''JSON API LiveView for %(model_name)s.'''

    def mount(self, request, **kwargs):
        self.search_query = ""
        self._compute()

    def _compute(self):
        '''Recompute from database.'''
        qs = %(model_name)s.objects.all()
        if self.search_query:
%(search_filter)s\
        self.items = list(qs)

%(computed_props)s\
    @event_handler()
    def search(self, value: str = "", **kwargs):
        '''Filter by search query.'''
        self.search_query = value
        self._compute()
        return self.render_json({"items": self.items})

    @event_handler()
    def index(self, **kwargs):
        '''List all records.'''
        self._compute()
        return self.render_json({"items": self.items})

    @event_handler()
    def show(self, item_id: int = 0, **kwargs):
        '''Show a single record.'''
        try:
            item = %(model_name)s.objects.get(pk=item_id)
        except %(model_name)s.DoesNotExist:
            return self.render_json({"error": "Not found"}, status=404)
        return self.render_json({"item": item})

    @event_handler()
    def delete(self, item_id: int = 0, **kwargs):
        '''Delete a record.'''
        %(model_name)s.objects.filter(pk=item_id).delete()
        return self.render_json({"deleted": True})

    @event_handler()
    def create(%(create_params)s, **kwargs):
        '''Create a new record.'''
%(create_body)s\
        self._compute()
        return self.render_json({"created": True, "items": self.items})

    @event_handler()
    def update(self, item_id: int = 0, %(update_params)s, **kwargs):
        '''Update an existing record.'''
        try:
            obj = %(model_name)s.objects.get(pk=item_id)
%(update_body)s\
            obj.save()
            return self.render_json({"updated": True, "item": obj})
        except %(model_name)s.DoesNotExist:
            return self.render_json({"error": "Not found"}, status=404)
"""


LIST_HTML_TEMPLATE = """\
{%% extends "%(app_name)s/base.html" %%}

{%% block title %%}%(model_display)s - %(display_name)s{%% endblock %%}

{%% block content %%}
{%% csrf_token %%}
<div dj-root dj-view="%(app_name)s.views.%(view_class)s">

    <h1 class="text-2xl font-bold text-white mb-6">%(model_display)s</h1>

    <!-- Search -->
    <div class="mb-6">
        <input type="text"
               dj-input="search"
               name="value"
               value="{{ search_query }}"
               placeholder="Search %(model_display_lower)s..."
               class="w-full px-4 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">
    </div>

    <!-- Create form -->
    <form dj-submit="create" class="glass rounded-lg p-4 mb-6 grid grid-cols-1 sm:grid-cols-2 gap-3">
%(form_fields_create)s\
        <div class="sm:col-span-2">
            <button type="submit"
                    class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 transition"
                    dj-loading.class="opacity-50" dj-loading.disable>
                Add %(model_display_singular)s
            </button>
        </div>
    </form>

    <!-- List -->
    <div class="space-y-2">
        {%% for item in items %%}
        <div class="glass rounded-lg px-4 py-3 flex items-center justify-between group">
            <div class="flex items-center gap-4 cursor-pointer"
                 dj-click="show"
                 data-item_id:int="{{ item.pk }}">
%(list_item_fields)s\
            </div>
            <div class="flex items-center gap-2">
                <button dj-click="delete"
                        data-item_id:int="{{ item.pk }}"
                        dj-confirm="Delete this %(model_display_singular_lower)s?"
                        class="text-red-400 opacity-0 group-hover:opacity-100 hover:text-red-300 transition text-sm">
                    Delete
                </button>
            </div>
        </div>
        {%% empty %%}
        <div class="text-center text-gray-500 py-12">
            {%% if search_query %%}
            No %(model_display_lower)s match "{{ search_query }}"
            {%% else %%}
            No %(model_display_lower)s yet. Add one above!
            {%% endif %%}
        </div>
        {%% endfor %%}
    </div>

%(show_panel)s\
</div>
{%% endblock %%}
"""


# ---------------------------------------------------------------------------
# Show/detail template (embedded in list via conditional)
# ---------------------------------------------------------------------------

SHOW_PANEL_TEMPLATE = """\
    <!-- Show/Edit panel -->
    {%% if selected %%}
    <div class="glass rounded-lg p-6 mt-8 border border-indigo-500/30">
        <div class="flex items-center justify-between mb-4">
            <h2 class="text-xl font-bold text-white">%(model_display_singular)s Details</h2>
            <button dj-click="show"
                    data-item_id:int="0"
                    class="text-gray-400 hover:text-white transition">
                <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
        </div>
%(show_item_fields)s\
        <!-- Edit form -->
        <form dj-submit="update"
              class="mt-6 pt-6 border-t border-white/10 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input type="hidden" name="item_id" value="{{ selected.pk }}">
%(form_fields_edit)s\
            <div class="sm:col-span-2 flex gap-3">
                <button type="submit"
                        class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 transition"
                        dj-loading.class="opacity-50" dj-loading.disable>
                    Save Changes
                </button>
                <button type="button"
                        dj-click="show"
                        data-item_id:int="0"
                        class="px-4 py-2 rounded-lg bg-surface-700 text-gray-300 hover:bg-surface-600 transition">
                    Cancel
                </button>
            </div>
        </form>
    </div>
    {%% endif %%}
"""


# ---------------------------------------------------------------------------
# Form partial (for show/edit)
# ---------------------------------------------------------------------------

FORM_FIELDS_CREATE_TEMPLATE = """\
        <div>
            <label class="block text-sm text-gray-400 mb-1">%(label)s</label>
            %(input_html)s
        </div>
"""

FORM_FIELDS_EDIT_TEMPLATE = """\
            <div>
                <label class="block text-sm text-gray-400 mb-1">%(label)s</label>
                %(input_html)s
            </div>
"""


# ---------------------------------------------------------------------------
# URL patterns template
# ---------------------------------------------------------------------------

URLS_PY_TEMPLATE = """\
\"\"\"URL configuration for %(app_name)s.\"\"\"

from django.urls import path

from .views import %(view_class)s

urlpatterns = [
    path("%(url_prefix)s", %(view_class)s.as_view(), name="%(url_name)s"),
]
"""


# ---------------------------------------------------------------------------
# Test file template
# ---------------------------------------------------------------------------

TEST_TEMPLATE = """\
\"\"\"Smoke tests for %(app_name)s %(model_name)s LiveView.\"\"\"

import pytest
from django.test import TestCase

from %(app_name)s.views import %(view_class)s
from djust.testing import LiveViewTestClient


class Test%(model_name)sLiveView(TestCase):
    \"\"\"Smoke tests for %(model_name)s CRUD LiveView.\"\"\"

    def setUp(self):
        self.client = LiveViewTestClient(%(view_class)s)

    def test_mount(self):
        \"\"\"View mounts without error.\"\"\"
        self.client.mount()
        self.client.assert_state(items=[], selected=None, search_query="")

    def test_search(self):
        \"\"\"Search event updates search_query.\"\"\"
        self.client.mount()
        self.client.send_event("search", value="test")
        self.client.assert_state(search_query="test")

    def test_show_sets_selected(self):
        \"\"\"Show event sets selected item.\"\"\"
        self.client.mount()
        # Initially no item selected
        self.client.assert_state(selected=None)

    def test_context_data_includes_items(self):
        \"\"\"Context includes items list.\"\"\"
        self.client.mount()
        ctx = self.client.get_context_data()
        assert "items" in ctx
        assert "selected" in ctx
        assert "search_query" in ctx
"""


# ---------------------------------------------------------------------------
# Helper: build HTML input for a field type
# ---------------------------------------------------------------------------

FIELD_TYPE_TO_HTML = {
    "string": '<input type="text" name="%(name)s" value="{{ selected.%(name)s }}" placeholder="%(label)s" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">',
    "text": '<textarea name="%(name)s" rows="3" placeholder="%(label)s" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500 resize-none">{{ selected.%(name)s }}</textarea>',
    "integer": '<input type="number" name="%(name)s" value="{{ selected.%(name)s|default:\'0\' }}" step="1" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 focus:outline-none focus:border-indigo-500">',
    "float": '<input type="number" name="%(name)s" value="{{ selected.%(name)s|default:\'0\' }}" step="0.01" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 focus:outline-none focus:border-indigo-500">',
    "boolean": '<div class="flex items-center"><input type="checkbox" name="%(name)s" value="1" {{#if selected.%(name)s}}checked{{/if}} class="w-5 h-5 rounded bg-surface-800 border border-white/10 text-indigo-500 focus:outline-none focus:ring-indigo-500"><span class="ml-2 text-gray-300">%(label)s</span></div>',
    "date": '<input type="date" name="%(name)s" value="{{ selected.%(name)s|date:"Y-m-d" }}" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 focus:outline-none focus:border-indigo-500">',
    "datetime": '<input type="datetime-local" name="%(name)s" value="{{ selected.%(name)s|date:"Y-m-d\\TH:i" }}" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 focus:outline-none focus:border-indigo-500">',
    "email": '<input type="email" name="%(name)s" value="{{ selected.%(name)s }}" placeholder="%(label)s" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">',
    "url": '<input type="url" name="%(name)s" value="{{ selected.%(name)s }}" placeholder="https://..." class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">',
    "slug": '<input type="text" name="%(name)s" value="{{ selected.%(name)s }}" placeholder="%(label)s" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500">',
    "decimal": '<input type="number" name="%(name)s" value="{{ selected.%(name)s|default:\'0\' }}" step="0.01" class="w-full px-3 py-2 rounded-lg bg-surface-800 border border-white/10 text-gray-200 focus:outline-none focus:border-indigo-500">',
}


def get_field_html(field_name: str, field_type: str, label: str) -> str:
    """Return HTML input for a field based on its type."""
    template = FIELD_TYPE_TO_HTML.get(field_type, FIELD_TYPE_TO_HTML["string"])
    return template % {"name": field_name, "label": label}


def get_search_filter(fields):
    """Build the search filter lines for _compute()."""
    text_fields = [f for f in fields if f["type"] in ("string", "text", "email", "url", "slug")]
    if not text_fields:
        return "        pass  # No text fields to search\n"

    lines = []
    for f in text_fields:
        lines.append("            qs = qs.filter(%s__icontains=self.search_query)" % f["name"])
    return "".join("        %s\n" % line for line in lines)


def build_model_import(model_name, fields):
    """Build the model import line for views.py."""
    return "from .models import %s\n" % model_name


def build_create_params(fields):
    """Build parameter list for create handler."""
    params = []
    for f in fields:
        if f["type"] == "boolean":
            params.append("%s: bool = False" % f["name"])
        elif f["type"] in ("integer", "float", "decimal"):
            params.append("%s: float = 0" % f["name"])
        elif f["type"].startswith("fk:"):
            params.append("%s: int = 0" % f["name"])
        else:
            params.append('%s: str = ""' % f["name"])
    return ", ".join(params)


def build_create_body(fields):
    """Build the body of the create handler."""
    text_fields = [
        f
        for f in fields
        if f["type"] not in ("boolean", "integer", "float", "decimal", "date", "datetime")
    ]
    lines = []
    if text_fields:
        first = text_fields[0]["name"]
        lines.append("        if %s and %s.strip():\n" % (first, first))
        lines.append("            %s.objects.create(\n" % fields[0].get("model_name", "%s"))
        for f in fields:
            if f["type"] == "boolean":
                lines.append("                %s=%s,\n" % (f["name"], f["name"]))
            elif f["type"] in ("integer", "float", "decimal"):
                lines.append("                %s=%s,\n" % (f["name"], f["name"]))
            elif f["type"].startswith("fk:"):
                lines.append("                %s_id=%s,\n" % (f["name"], f["name"]))
            else:
                lines.append("                %s=%s.strip(),\n" % (f["name"], f["name"]))
        lines.append("            )\n")
    else:
        lines.append("        %s.objects.create()\n" % fields[0].get("model_name", "%s"))
    return "".join(lines)


def build_update_params(fields):
    """Build parameter list for update handler."""
    params = []
    for f in fields:
        if f["type"] == "boolean":
            params.append("%s: bool = False" % f["name"])
        elif f["type"] in ("integer", "float", "decimal"):
            params.append("%s: float = 0" % f["name"])
        elif f["type"].startswith("fk:"):
            params.append("%s: int = 0" % f["name"])
        else:
            params.append('%s: str = ""' % f["name"])
    return ", ".join(params)


def build_update_body(fields):
    """Build the body of the update handler."""
    lines = []
    for f in fields:
        if f["type"] == "boolean":
            lines.append("            obj.%s = %s\n" % (f["name"], f["name"]))
        elif f["type"] in ("integer", "float", "decimal"):
            lines.append("            obj.%s = %s\n" % (f["name"], f["name"]))
        elif f["type"].startswith("fk:"):
            lines.append("            obj.%s_id = %s\n" % (f["name"], f["name"]))
        else:
            lines.append("            obj.%s = %s\n" % (f["name"], f["name"]))
    return "".join(lines)


def build_context_data(fields):
    """Build context data dict entries."""
    lines = [
        '            "items": self.items,\n',
        '            "selected": self.selected,\n',
        '            "search_query": self.search_query,\n',
    ]
    return "".join(lines)


def build_computed_props(fields):
    """Build additional computed property lines."""
    # Count for list
    lines = ["        self.item_count = len(self.items)\n"]
    return "".join(lines)


def build_list_item_fields(fields, max_fields=4):
    """Build list item display lines."""
    lines = []
    for f in fields[:max_fields]:
        if f["type"] == "boolean":
            lines.append(
                '                <span class="text-sm px-2 py-0.5 rounded %s">{{ item.%s }}</span>\n'
                % (
                    "{% if item.%s %}bg-emerald-500/20 text-emerald-400{% else %}bg-surface-700 text-gray-400{% endif %}",
                    f["name"],
                )
            )
        elif f["type"] in ("integer", "float", "decimal"):
            lines.append(
                '                <span class="text-gray-300">{{ item.%s }}</span>\n' % f["name"]
            )
        elif f["type"] in ("date", "datetime"):
            lines.append(
                '                <span class="text-gray-400 text-sm">{{ item.%s|date:"Y-m-d" }}</span>\n'
                % f["name"]
            )
        else:
            lines.append(
                '                <span class="text-gray-200">{{ item.%s }}</span>\n' % f["name"]
            )
    return (
        "".join(lines)
        if lines
        else '                <span class="text-gray-200">{{ item.pk }}</span>\n'
    )


def build_show_item_fields(fields):
    """Build show panel item field display lines."""
    lines = []
    for f in fields:
        label = f["name"].replace("_", " ").title()
        if f["type"] == "boolean":
            lines.append(
                '        <div class="mb-2">\n'
                '            <span class="text-gray-400 text-sm">%s:</span>\n'
                '            <span class="ml-2 text-white">{{#if selected.%s}}Yes{{else}}No{{/if}}</span>\n'
                "        </div>\n" % (label, f["name"])
            )
        elif f["type"] in ("date", "datetime"):
            lines.append(
                '        <div class="mb-2">\n'
                '            <span class="text-gray-400 text-sm">%s:</span>\n'
                '            <span class="ml-2 text-white">{{ selected.%s|date:"Y-m-d" }}</span>\n'
                "        </div>\n" % (label, f["name"])
            )
        elif f["type"].startswith("fk:"):
            lines.append(
                '        <div class="mb-2">\n'
                '            <span class="text-gray-400 text-sm">%s:</span>\n'
                '            <span class="ml-2 text-white">{{ selected.%s_id }}</span>\n'
                "        </div>\n" % (label, f["name"])
            )
        else:
            lines.append(
                '        <div class="mb-2">\n'
                '            <span class="text-gray-400 text-sm">%s:</span>\n'
                '            <span class="ml-2 text-white">{{ selected.%s }}</span>\n'
                "        </div>\n" % (label, f["name"])
            )
    return "".join(lines)


def build_form_fields_create(fields):
    """Build form fields for create form."""
    lines = []
    for f in fields:
        label = f["name"].replace("_", " ").title()
        html = get_field_html(f["name"], f["type"], label)
        indent = "        "
        lines.append(indent + "<div>")
        lines.append(
            indent + '    <label class="block text-sm text-gray-400 mb-1">%s</label>' % label
        )
        lines.append(indent + "    " + html)
        lines.append(indent + "</div>")
    return "\n".join(lines)


def build_form_fields_edit(fields):
    """Build form fields for edit form."""
    lines = []
    for f in fields:
        label = f["name"].replace("_", " ").title()
        html = get_field_html(f["name"], f["type"], label)
        indent = "            "
        lines.append(indent + "<div>")
        lines.append(
            indent + '    <label class="block text-sm text-gray-400 mb-1">%s</label>' % label
        )
        lines.append(indent + "    " + html)
        lines.append(indent + "</div>")
    return "\n".join(lines)
