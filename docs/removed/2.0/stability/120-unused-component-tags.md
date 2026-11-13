### The unused component tags, their classes and assets (#4002)

33 `djust_components` template tags that no app in the 2026-10 usage scan of
owner-run apps uses are gone, with the component classes, client scripts and
vendored bundles that existed only for them:

- all of `_advanced.py`: `activity_feed`, `agent_step`, `animated_number`,
  `breadcrumb_dropdown`, `calendar`, `collab_selection`, `comparison_table`,
  `cron_input`, `cursors`, `data_card_grid`, `diff_viewer`, `error_page`,
  `gantt_chart`, `image_upload_preview`, `live_indicator`, `map_picker`,
  `masonry_grid`, `org_chart`, `pivot_table`, `prompt_editor`, `qr_code`,
  `reactions`, `ribbon`, `tour`, `voice_input`;
- from `_dev_tools.py`: `file_tree`, `log_viewer`, `markdown_editor`,
  `terminal` (`json_viewer` stays: security pins render through it);
- the AI-chat widgets: `conversation_thread`, `thinking_indicator`,
  `multimodal_input`, `feedback`.

A template that still uses one fails to compile, under Django's engine and the
Rust engine alike, with a `TemplateSyntaxError` that names the tag and what to
use instead. Importing one of the classes (`from djust.components import Tour`)
raises `ImportError` naming it. An application library that defines its own tag under one of these
names (`{% calendar %}`, `{% tour %}`, ...) and `{% load %}`s it gets its own tag under both
engines: the refusal only answers when nothing else claims the name. The Visual/Markdown editor bundle
(`markdown-visual.js`, Tiptap) and the vendored Leaflet are no longer shipped.
