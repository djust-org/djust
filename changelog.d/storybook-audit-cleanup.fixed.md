- **Answer every `dj-click` the storybook's own previews emit.** Twenty events
  across seventeen components had no handler — `rating`'s `set_rating` was the
  one reported, and it errored on click; so would the other nineteen
  (`toggle_group`, `stepper`, `date_picker`, `page_alert`, `split_button`,
  `markdown_textarea`, `notification_center`, `command_palette`, `form_array`, …).
  A component renders `dj-click` because a host is expected to answer it — on a
  storybook page this view *is* the host, and an unanswered event is a server
  error rather than a no-op. They are driven from one table mapping each event to
  the single state kwarg it sets, installed as handlers the way the framework
  installs its own descriptor events.
- **Style the components whose `dj-` class families had no CSS.** `Switch`, `Tag`
  and `StatCard` rendered as raw browser controls or bare text: the theming
  `.switch*` / `.tag*` families were styled, the `dj-`-prefixed ones the python
  components emit were not, and `.dj-stat-card*` existed nowhere. Markdown
  rendered unstyled too — it emits `dj-prose` while `prose.css` styles the
  unprefixed `.prose`.
- **Give eleven data components examples that contain data.** `data_table`,
  `data_grid`, `comparison_table`, `tree_view`, `breadcrumb`, `multi_select`,
  `combobox`, `tag_input`, `otp_input`, `time_picker` and `color_picker` all had
  an example of `[{}]`, so each rendered with every argument at its default —
  a `data_table` preview was an empty `<table>` with no columns and no rows, and
  a `tree_view` preview was nothing to look at. The shapes come from each
  component's own docstring rather than from guessing at the signature, which is
  how `meter` came to be passed `value`/`min`/`max` for a `segments`/`total`
  API.
