"""Marked-safe arguments in attribute slots of the component tags (#4009): a ratchet.

``conditional_escape`` leaves a marked-safe value unescaped, which is right in a text slot and lets
the value out of an attribute. #4008 fixed ``toggle_group``; a sweep of every component tag with a
marked-safe value that breaks out of a quoted attribute (``marked_safe_sweep_4009``) finds the
tags below still doing it. They are the Python tags, and since 2.0 the Python tag is the only
render path (the Rust engine renders its node), so this list is the whole of the defect: the native
twins that also had it are gone, and the four kept layout handlers escape their attributes with
``attr_text`` (``test_component_single_render_path.py``).

The fix is a structural pass over the tags (``attr_text`` in every attribute slot), tracked on #4009;
it is not part of the single-render-path change. Until then this pins the set EXACTLY, in both
directions: a tag that starts leaking fails, and a tag that is fixed must be removed from the list.
Exploiting it needs the application to pass a marked-safe hostile value into a tag argument.
"""

from __future__ import annotations

import pytest

import marked_safe_sweep_4009 as safe_sweep

pytestmark = pytest.mark.components

KNOWN_BREAKOUTS = frozenset(
    {
        "_advanced:ActivityFeedNode",
        "_advanced:AgentStepNode",
        "_advanced:AnimatedNumberNode",
        "_advanced:BreadcrumbDropdownNode",
        "_advanced:CalendarViewNode",
        "_advanced:CollabSelectionNode",
        "_advanced:ComparisonTableNode",
        "_advanced:CronInputNode",
        "_advanced:CursorsOverlayNode",
        "_advanced:DataCardGridNode",
        "_advanced:DiffViewerNode",
        "_advanced:ErrorPageNode",
        "_advanced:GanttChartNode",
        "_advanced:ImageUploadPreviewNode",
        "_advanced:LiveIndicatorNode",
        "_advanced:MapPickerNode",
        "_advanced:MasonryGridNode",
        "_advanced:OrgChartNode",
        "_advanced:PivotTableNode",
        "_advanced:PromptEditorNode",
        "_advanced:QRCodeNode",
        "_advanced:ReactionsNode",
        "_advanced:RibbonNode",
        "_advanced:VoiceInputNode",
        "_charts:BarChartNode",
        "_charts:CalendarHeatmapNode",
        "_charts:HeatmapNode",
        "_charts:LineChartNode",
        "_charts:PieChartNode",
        "_charts:SparklineNode",
        "_charts:TreemapNode",
        "_dev_tools:FileTreeNode",
        "_dev_tools:JsonViewerNode",
        "_dev_tools:LogViewerNode",
        "_dev_tools:MarkdownEditorNode",
        "_dev_tools:TerminalNode",
        "_forms:ConfirmDialogNode",
        "_forms:PopconfirmNode",
        "_forms:autocomplete",
        "_forms:currency_input",
        "_forms:dependent_select",
        "_forms:dj_form",
        "_forms:password_input",
        "_forms:search_input",
        "_forms:slider",
        "djust_components:AccordionNode",
        "djust_components:AlertNode",
        "djust_components:AnnouncementBarNode",
        "djust_components:ApprovalGateNode",
        "djust_components:AspectRatioNode",
        "djust_components:AuditLogNode",
        "djust_components:AvatarGroupNode",
        "djust_components:AwaitNode",
        "djust_components:BottomSheetNode",
        "djust_components:CalloutNode",
        "djust_components:CardNode",
        "djust_components:ChatBubbleNode",
        "djust_components:CollapsibleNode",
        "djust_components:CommandPaletteNode",
        "djust_components:ConnectionStatusNode",
        "djust_components:ContextMenuNode",
        "djust_components:ConversationThreadNode",
        "djust_components:CookieConsentNode",
        "djust_components:CopyableTextNode",
        "djust_components:CountdownNode",
        "djust_components:DashboardGridNode",
        "djust_components:DescriptionListNode",
        "djust_components:DjLabelNode",
        "djust_components:DropdownMenuNode",
        "djust_components:DropdownNode",
        "djust_components:ErrorBoundaryNode",
        "djust_components:ExpandableTextNode",
        "djust_components:ExportDialogNode",
        "djust_components:FeedbackWidgetNode",
        "djust_components:FieldsetNode",
        "djust_components:FilterBarNode",
        "djust_components:FormArrayNode",
        "djust_components:FormGroupNode",
        "djust_components:ImageCropperNode",
        "djust_components:ImportWizardNode",
        "djust_components:InfiniteScrollNode",
        "djust_components:InlineEditNode",
        "djust_components:InputAddonNode",
        "djust_components:InputGroupNode",
        "djust_components:LightboxNode",
        "djust_components:LiveCounterNode",
        "djust_components:LoadingOverlayNode",
        "djust_components:MarkdownTextareaNode",
        "djust_components:MentionsInputNode",
        "djust_components:MenuItemNode",
        "djust_components:MeterNode",
        "djust_components:ModalNode",
        "djust_components:ModelSelectorNode",
        "djust_components:MultimodalInputNode",
        "djust_components:NavMenuNode",
        "djust_components:NotificationBadgeNode",
        "djust_components:NotificationPopoverNode",
        "djust_components:PageAlertNode",
        "djust_components:PageHeaderNode",
        "djust_components:PopoverNode",
        "djust_components:PresenceAvatarsNode",
        "djust_components:ProgressCircleNode",
        "djust_components:ResizablePanelNode",
        "djust_components:ScrollAreaNode",
        "djust_components:ScrollSpyNode",
        "djust_components:SegmentedProgressNode",
        "djust_components:SheetNode",
        "djust_components:SidebarNode",
        "djust_components:SignaturePadNode",
        "djust_components:SkeletonForNode",
        "djust_components:SortableGridNode",
        "djust_components:SortableListNode",
        "djust_components:SourceCitationNode",
        "djust_components:StatusIndicatorNode",
        "djust_components:StickyHeaderNode",
        "djust_components:StreamingTextNode",
        "djust_components:TabsNode",
        "djust_components:ThemeToggleNode",
        "djust_components:ThinkingIndicatorNode",
        "djust_components:TimePickerNode",
        "djust_components:ToastContainerNode",
        "djust_components:TokenCounterNode",
        "djust_components:ToolbarNode",
        "djust_components:TooltipNode",
        "djust_components:TruncatedListNode",
        "djust_components:WizardNode",
        "djust_components:carousel",
        "djust_components:code_block",
        "djust_components:code_snippet",
        "djust_components:color_picker",
        "djust_components:combobox",
        "djust_components:context_menu_item",
        "djust_components:copy_button",
        "djust_components:data_grid",
        "djust_components:date_picker",
        "djust_components:dj_button",
        "djust_components:dj_checkbox",
        "djust_components:dj_input",
        "djust_components:dj_radio",
        "djust_components:dj_select",
        "djust_components:dj_tag",
        "djust_components:dj_textarea",
        "djust_components:empty_state",
        "djust_components:fab",
        "djust_components:file_dropzone",
        "djust_components:gauge",
        "djust_components:kanban_board",
        "djust_components:multi_select",
        "djust_components:notification_center",
        "djust_components:number_stepper",
        "djust_components:otp_input",
        "djust_components:palette_item",
        "djust_components:rating",
        "djust_components:relative_time",
        "djust_components:render_theme_toggle",
        "djust_components:responsive_image",
        "djust_components:rich_select",
        "djust_components:rich_text_editor",
        "djust_components:scroll_to_top",
        "djust_components:spinner",
        "djust_components:split_button",
        "djust_components:stat_card",
        "djust_components:switch",
        "djust_components:table_of_contents",
        "djust_components:tag_input",
        "djust_components:tree_view",
    }
)


@pytest.mark.slow
def test_the_marked_safe_breakouts_are_exactly_the_known_ones():
    found = safe_sweep.breakouts()
    assert sorted(found - KNOWN_BREAKOUTS) == [], (
        "a tag now leaks a marked-safe value out of an attribute"
    )
    assert sorted(KNOWN_BREAKOUTS - found) == [], (
        "fixed (good): remove these from KNOWN_BREAKOUTS so they cannot regress"
    )


def test_the_sweep_sees_a_breakout_and_not_a_text_slot():
    """Non-vacuity (#1468): the detector flags a marked-safe value in an attribute and not the same
    value rendered raw as text, which is what a marked-safe value is for."""
    from payload_sweep_3774 import injections

    payload = safe_sweep.SAFE_PAYLOAD
    assert any("onmouseover" in hit for hit in injections(f'<b title="{payload}">x</b>'))
    assert any("onmouseover" in hit for hit in injections(f"<b title='{payload}'>x</b>"))
    assert injections(f"<b>{payload}</b>") == []


def test_the_single_path_is_the_python_tag_for_a_known_breakout():
    """The Rust engine renders the Python tag's node, so it has exactly the Python tag's defect,
    no more and no less: ``rating`` (the tag #4009 confirmed) through both engines."""
    from django.template import engines
    from django.utils.safestring import mark_safe

    from djust import _rust
    from djust.components.rust_handlers import register_with_rust_engine

    register_with_rust_engine()
    ctx = {"e": mark_safe('x" onmouseover="alert(1)')}
    src = "{% rating value=2 event=e %}"
    django = engines["django"].from_string("{% load djust_components %}" + src).render(ctx)
    assert str(_rust.render_template(src, ctx)) == django
    assert "djust_components:rating" in KNOWN_BREAKOUTS
