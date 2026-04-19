"""
djust Component class implementations.

Alternative to template tags for programmatic use in LiveViews.

Usage::

    from djust.components.components import Badge, Button, Card, StatusDot
    from djust.components.components import Alert, StatCard, Tag, Toast, Progress, Spinner, Switch

    class MyView(LiveView):
        def mount(self, **kwargs):
            self.status_badge = Badge.status("running")
            self.priority_badge = Badge.priority("P0")
            self.agent_status = StatusDot("completed")
            self.submit_btn = Button("Save", variant="primary", action="save")
            self.info_card = Card(content="<p>Info</p>", variant="elevated")
            self.notice = Alert.success("Saved!")
            self.revenue = StatCard(label="Revenue", value="$12,345", trend="up")
            self.tag = Tag("Python", variant="info")
            self.toast = Toast.success("Done!")
            self.progress = Progress(value=75)
            self.loading = Spinner()
            self.toggle = Switch(name="dark_mode", label="Dark mode")

In template::

    {{ status_badge|safe }}
    {{ priority_badge|safe }}
    {{ agent_status|safe }}
    {{ submit_btn|safe }}
    {{ info_card|safe }}
    {{ notice|safe }}
"""

from .alert import Alert
from .avatar_group import AvatarGroup
from .hover_card import HoverCard
from .notification_popover import NotificationPopover
from .badge import Badge
from .button import Button
from .card import Card
from .data_grid import DataGrid
from .markdown import Markdown
from .notification_badge import NotificationBadge
from .progress import Progress
from .progress_circle import ProgressCircle
from .rich_select import RichSelect
from .segmented_progress import SegmentedProgress
from .spinner import Spinner
from .stat_card import StatCard
from .status_dot import StatusDot
from .status_indicator import StatusIndicator
from .switch import Switch
from .tag import Tag
from .toast import Toast
from .streaming_text import StreamingText
from .code_snippet import CodeSnippet
from .connection_status import ConnectionStatus
from .copyable_text import CopyableText
from .live_counter import LiveCounter
from .relative_time import RelativeTime
from .responsive_image import ResponsiveImage
from .scroll_to_top import ScrollToTop
from .server_event_toast import ServerEventToastMixin
from .dependent_select import DependentSelect
from .currency_input import CurrencyInput
from .conversation_thread import ConversationThread
from .thinking_indicator import ThinkingIndicator
from .multimodal_input import MultimodalInput
from .feedback_widget import FeedbackWidget
from .form_validation import FormErrors, FieldError
from .approval_gate import ApprovalGate
from .source_citation import SourceCitation
from .model_selector import ModelSelector
from .token_counter import TokenCounter
from .chat_bubble import ChatBubble
from .presence_avatars import PresenceAvatars
from .mentions_input import MentionsInput
from .expandable_text import ExpandableText
from .truncated_list import TruncatedList
from .markdown_textarea import MarkdownTextarea
from .skeleton_factory import SkeletonFactory
from .content_loader import ContentLoader
from .time_picker import TimePicker
from .wizard import Wizard
from .bottom_sheet import BottomSheet
from .infinite_scroll import InfiniteScroll
from .countdown import Countdown
from .cookie_consent import CookieConsent
from .form_array import FormArray
from .scroll_spy import ScrollSpy
from .page_alert import PageAlert
from .dropdown_menu import DropdownMenu
from .meter import Meter
from .export_dialog import ExportDialog
from .import_wizard import ImportWizard
from .audit_log import AuditLog
from .error_boundary import ErrorBoundary
from .sortable_list import SortableList
from .sortable_grid import SortableGrid
from .image_cropper import ImageCropper
from .signature_pad import SignaturePad
from .resizable_panel import ResizablePanel
from .image_lightbox import ImageLightbox
from .dashboard_grid import DashboardGrid
from .bar_chart import BarChart
from .line_chart import LineChart
from .pie_chart import PieChart
from .sparkline import Sparkline
from .heatmap import Heatmap
from .treemap import Treemap
from .calendar_heatmap import CalendarHeatmap
from .terminal import Terminal
from .markdown_editor import MarkdownEditor
from .json_viewer import JsonViewer
from .log_viewer import LogViewer
from .file_tree import FileTree
from .tour import Tour
from .calendar_view import CalendarView
from .gantt_chart import GanttChart
from .diff_viewer import DiffViewer
from .pivot_table import PivotTable
from .org_chart import OrgChart
from .comparison_table import ComparisonTable
from .masonry_grid import MasonryGrid
from .cursors_overlay import CursorsOverlay
from .live_indicator import LiveIndicator
from .collab_selection import CollabSelection
from .activity_feed import ActivityFeed
from .reactions import Reactions
from .map_picker import MapPicker
from .prompt_editor import PromptEditor
from .voice_input import VoiceInput
from .cron_input import CronInput
from .error_page import ErrorPage
from .image_upload_preview import ImageUploadPreview
from .animated_number import AnimatedNumber
from .ribbon import Ribbon
from .breadcrumb_dropdown import BreadcrumbDropdown
from .data_card_grid import DataCardGrid
from .agent_step import AgentStep
from .qr_code import QRCode
from .accordion import Accordion
from .announcement_bar import AnnouncementBar
from .app_shell import AppShell
from .aspect_ratio import AspectRatio
from .avatar import Avatar
from .breadcrumb import Breadcrumb
from .callout import Callout
from .carousel import Carousel
from .code_block import CodeBlock
from .collapsible import Collapsible
from .color_picker import ColorPicker
from .combobox import Combobox
from .command_palette import CommandPalette
from .context_menu import ContextMenu
from .copy_button import CopyButton
from .data_table import DataTable
from .date_picker import DatePicker
from .description_list import DescriptionList
from .dropdown import Dropdown
from .empty_state import EmptyState
from .fab import Fab
from .fieldset import Fieldset
from .file_dropzone import FileDropzone
from .filter_bar import FilterBar
from .form_group import FormGroup
from .gauge import Gauge
from .icon import Icon
from .inline_edit import InlineEdit
from .input_group import InputGroup
from .kanban_board import KanbanBoard
from .kbd import Kbd
from .loading_overlay import LoadingOverlay
from .modal import Modal
from .multi_select import MultiSelect
from .nav_menu import NavMenu
from .notification_center import NotificationCenter
from .number_stepper import NumberStepper
from .otp_input import OtpInput
from .page_header import PageHeader
from .pagination import Pagination
from .popover import Popover
from .rating import Rating
from .rich_text_editor import RichTextEditor
from .scroll_area import ScrollArea
from .sheet import Sheet
from .sidebar import Sidebar
from .skeleton import Skeleton
from .split_button import SplitButton
from .split_pane import SplitPane
from .stepper import Stepper
from .sticky_header import StickyHeader
from .table_of_contents import TableOfContents
from .tabs import Tabs
from .tag_input import TagInput
from .theme_toggle import ThemeToggle
from .timeline import Timeline
from .toggle_group import ToggleGroup
from .toolbar import Toolbar
from .tooltip import Tooltip
from .tree_view import TreeView
from .virtual_list import VirtualList

__all__ = [
    "Alert",
    "AvatarGroup",
    "Badge",
    "Button",
    "Card",
    "CodeSnippet",
    "ConnectionStatus",
    "CopyableText",
    "DataGrid",
    "LiveCounter",
    "Markdown",
    "NotificationBadge",
    "Progress",
    "ProgressCircle",
    "RelativeTime",
    "ResponsiveImage",
    "RichSelect",
    "ScrollToTop",
    "SegmentedProgress",
    "ServerEventToastMixin",
    "Spinner",
    "StatCard",
    "StatusDot",
    "StatusIndicator",
    "StreamingText",
    "Switch",
    "Tag",
    "Toast",
    "CurrencyInput",
    "DependentSelect",
    "FieldError",
    "FormErrors",
    "HoverCard",
    "NotificationPopover",
    "ConversationThread",
    "ThinkingIndicator",
    "MultimodalInput",
    "FeedbackWidget",
    "ApprovalGate",
    "SourceCitation",
    "ModelSelector",
    "TokenCounter",
    "ChatBubble",
    "PresenceAvatars",
    "MentionsInput",
    "ExpandableText",
    "TruncatedList",
    "MarkdownTextarea",
    "SkeletonFactory",
    "ContentLoader",
    "TimePicker",
    "Wizard",
    "BottomSheet",
    "InfiniteScroll",
    "Countdown",
    "CookieConsent",
    "FormArray",
    "ScrollSpy",
    "PageAlert",
    "DropdownMenu",
    "Meter",
    "ExportDialog",
    "ImportWizard",
    "AuditLog",
    "ErrorBoundary",
    "SortableList",
    "SortableGrid",
    "ImageCropper",
    "SignaturePad",
    "ResizablePanel",
    "ImageLightbox",
    "DashboardGrid",
    "BarChart",
    "LineChart",
    "PieChart",
    "Sparkline",
    "Heatmap",
    "Treemap",
    "CalendarHeatmap",
    "Terminal",
    "MarkdownEditor",
    "JsonViewer",
    "LogViewer",
    "FileTree",
    "Tour",
    "CalendarView",
    "GanttChart",
    "DiffViewer",
    "PivotTable",
    "OrgChart",
    "ComparisonTable",
    "MasonryGrid",
    "CursorsOverlay",
    "LiveIndicator",
    "CollabSelection",
    "ActivityFeed",
    "Reactions",
    "MapPicker",
    "PromptEditor",
    "VoiceInput",
    "CronInput",
    "ErrorPage",
    "ImageUploadPreview",
    "AnimatedNumber",
    "Ribbon",
    "BreadcrumbDropdown",
    "DataCardGrid",
    "AgentStep",
    "QRCode",
    "Accordion",
    "AnnouncementBar",
    "AppShell",
    "AspectRatio",
    "Avatar",
    "Breadcrumb",
    "Callout",
    "Carousel",
    "CodeBlock",
    "Collapsible",
    "ColorPicker",
    "Combobox",
    "CommandPalette",
    "ContextMenu",
    "CopyButton",
    "DataTable",
    "DatePicker",
    "DescriptionList",
    "Dropdown",
    "EmptyState",
    "Fab",
    "Fieldset",
    "FileDropzone",
    "FilterBar",
    "FormGroup",
    "Gauge",
    "Icon",
    "InlineEdit",
    "InputGroup",
    "KanbanBoard",
    "Kbd",
    "LoadingOverlay",
    "Modal",
    "MultiSelect",
    "NavMenu",
    "NotificationCenter",
    "NumberStepper",
    "OtpInput",
    "PageHeader",
    "Pagination",
    "Popover",
    "Rating",
    "RichTextEditor",
    "ScrollArea",
    "Sheet",
    "Sidebar",
    "Skeleton",
    "SplitButton",
    "SplitPane",
    "Stepper",
    "StickyHeader",
    "TableOfContents",
    "Tabs",
    "TagInput",
    "ThemeToggle",
    "Timeline",
    "ToggleGroup",
    "Toolbar",
    "Tooltip",
    "TreeView",
    "VirtualList",
]
