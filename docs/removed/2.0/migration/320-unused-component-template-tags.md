---
table: templates
removed: The 33 component template tags no app was found using: all of `_advanced.py` (`{% tour %}`, `{% map_picker %}`, `{% pivot_table %}`, `{% cursors %}`, ...), `{% terminal %}`, `{% markdown_editor %}`, `{% log_viewer %}` and `{% file_tree %}` from `_dev_tools.py` and the AI-chat widgets (`{% conversation_thread %}`, `{% thinking_indicator %}`, `{% multimodal_input %}`, `{% feedback %}`)
use: The error names the tag and its replacement where there is one (`{% breadcrumb %}` for `{% breadcrumb_dropdown %}`, `{% tree_view %}` for `{% file_tree %}`, `{% markdown_textarea %}` for `{% markdown_editor %}`, ...); otherwise copy the tag from djust 1.3 into a template library of your own
covers: activity_feed, agent_step, animated_number, breadcrumb_dropdown, calendar, collab_selection, comparison_table, cron_input, cursors, data_card_grid, diff_viewer, error_page, gantt_chart, image_upload_preview, live_indicator, map_picker, masonry_grid, org_chart, pivot_table, prompt_editor, qr_code, reactions, ribbon, tour, voice_input, file_tree, log_viewer, markdown_editor, terminal, conversation_thread, thinking_indicator, multimodal_input, feedback
---
