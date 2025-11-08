"""
Tabs component for Django Rust Live.

Provides tabbed navigation with multiple panels.
"""

from typing import Dict, Any, List
from ..base import LiveComponent


class TabsComponent(LiveComponent):
    """
    Tabbed interface component.

    Displays content in switchable tabs with reactive state management.

    Usage:
        from django_rust_live.components import TabsComponent

        # In your LiveView:
        def mount(self, request):
            self.profile_tabs = TabsComponent(
                tabs=[
                    {'id': 'profile', 'label': 'Profile', 'content': 'Profile information...'},
                    {'id': 'settings', 'label': 'Settings', 'content': 'Settings panel...'},
                    {'id': 'security', 'label': 'Security', 'content': 'Security options...', 'badge': '2'},
                ],
                active='profile'
            )

        # In template:
        {{ profile_tabs.render }}

        # Methods:
        def switch_to_settings(self):
            self.profile_tabs.activate_tab('settings')
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize tabs state"""
        self.tabs = kwargs.get('tabs', [])  # List of {id, label, content, badge, disabled}
        self.active = kwargs.get('active', None)
        self.variant = kwargs.get('variant', 'tabs')  # tabs, pills
        self.vertical = kwargs.get('vertical', False)

        # Set first tab as active if none specified
        if not self.active and self.tabs:
            self.active = self.tabs[0]['id']

    def get_context(self) -> Dict[str, Any]:
        """Get tabs context"""
        return {
            'tabs': self.tabs,
            'active': self.active,
            'variant': self.variant,
            'vertical': self.vertical,
        }

    def activate_tab(self, tab_id: str):
        """Switch to a different tab"""
        self.active = tab_id
        self.trigger_update()

    def render(self) -> str:
        """Render tabs with inline HTML"""
        from django.utils.safestring import mark_safe
        from ...config import config

        framework = config.get('css_framework', 'bootstrap5')

        if framework == 'bootstrap5':
            return mark_safe(self._render_bootstrap())
        elif framework == 'tailwind':
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 tabs"""
        nav_class = 'nav-pills' if self.variant == 'pills' else 'nav-tabs'
        flex_class = ' flex-column' if self.vertical else ''

        html = f'<div id="{self.component_id}">'

        # Nav tabs
        html += f'<ul class="nav {nav_class}{flex_class}" role="tablist">'

        for tab in self.tabs:
            tab_id = tab['id']
            label = tab['label']
            badge = tab.get('badge', '')
            disabled = tab.get('disabled', False)

            active_class = ' active' if tab_id == self.active else ''
            disabled_class = ' disabled' if disabled else ''

            badge_html = f' <span class="badge bg-secondary">{badge}</span>' if badge else ''

            html += f'''<li class="nav-item" role="presentation">
                <button class="nav-link{active_class}{disabled_class}" @click="activate_tab" data-tab="{tab_id}"
                        type="button" role="tab">{label}{badge_html}</button>
            </li>'''

        html += '</ul>'

        # Tab content
        html += '<div class="tab-content mt-3">'

        for tab in self.tabs:
            tab_id = tab['id']
            content = tab.get('content', '')
            active_class = ' show active' if tab_id == self.active else ''

            html += f'<div class="tab-pane fade{active_class}" id="tab-{tab_id}">{content}</div>'

        html += '</div></div>'
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS tabs"""
        html = f'<div id="{self.component_id}">'

        # Nav tabs
        border_class = 'border-b border-gray-200' if not self.vertical else 'border-r border-gray-200'
        flex_class = 'flex space-x-8' if not self.vertical else 'flex flex-col space-y-2'

        html += f'<div class="{border_class}">'
        html += f'<nav class="{flex_class}" aria-label="Tabs">'

        for tab in self.tabs:
            tab_id = tab['id']
            label = tab['label']
            badge = tab.get('badge', '')
            disabled = tab.get('disabled', False)

            if tab_id == self.active:
                if self.variant == 'pills':
                    classes = 'bg-blue-100 text-blue-700'
                else:
                    classes = 'border-blue-500 text-blue-600'
            else:
                if self.variant == 'pills':
                    classes = 'text-gray-500 hover:text-gray-700'
                else:
                    classes = 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'

            if disabled:
                classes += ' opacity-50 cursor-not-allowed'

            if self.variant == 'pills':
                base_classes = 'px-3 py-2 font-medium text-sm rounded-md'
            else:
                base_classes = 'border-b-2 py-4 px-1 text-sm font-medium'

            badge_html = f' <span class="ml-2 bg-gray-100 text-gray-900 py-0.5 px-2.5 rounded-full text-xs">{badge}</span>' if badge else ''

            click_attr = f' @click="activate_tab" data-tab="{tab_id}"' if not disabled else ''

            html += f'<button class="{base_classes} {classes}"{click_attr}>{label}{badge_html}</button>'

        html += '</nav></div>'

        # Tab content
        html += '<div class="mt-4">'

        for tab in self.tabs:
            tab_id = tab['id']
            content = tab.get('content', '')
            display = '' if tab_id == self.active else ' hidden'

            html += f'<div id="tab-{tab_id}" class="tab-pane{display}">{content}</div>'

        html += '</div></div>'
        return html

    def _render_plain(self) -> str:
        """Render plain HTML tabs"""
        html = f'<div class="tabs" id="{self.component_id}">'

        # Nav tabs
        html += '<div class="tabs-nav">'

        for tab in self.tabs:
            tab_id = tab['id']
            label = tab['label']
            badge = tab.get('badge', '')
            disabled = tab.get('disabled', False)

            active_class = ' active' if tab_id == self.active else ''
            disabled_class = ' disabled' if disabled else ''

            badge_html = f' <span class="badge">{badge}</span>' if badge else ''

            html += f'<button class="tab{active_class}{disabled_class}" @click="activate_tab" data-tab="{tab_id}">{label}{badge_html}</button>'

        html += '</div>'

        # Tab content
        html += '<div class="tabs-content">'

        for tab in self.tabs:
            tab_id = tab['id']
            content = tab.get('content', '')
            display = '' if tab_id == self.active else ' style="display:none"'

            html += f'<div class="tab-pane" id="tab-{tab_id}"{display}>{content}</div>'

        html += '</div></div>'
        return html
