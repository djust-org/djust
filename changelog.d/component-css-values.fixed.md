- **Components check the CSS values they write into `style` attributes.** A
  colour, length, ratio or number that a component takes from its caller and
  writes into an inline `style` attribute is now checked against the shapes the
  component documents, and anything else is replaced by the component's
  default. Colours accept `#rgb`/`#rgba`/`#rrggbb`/`#rrggbbaa`, a CSS named
  colour (with `transparent` and `currentcolor`), `rgb()`/`rgba()`/`hsl()`/
  `hsla()` with plain numeric arguments, and `var(--name)` references; lengths
  accept numbers with a unit, the usual keywords, `var()`, and `calc()`/`min()`/
  `max()`/`clamp()`. Valid values render exactly as before. Covers
  `CursorsOverlay` and `CollabSelection` (the user colour), `CalendarView`
  event colours, `Meter` segment colours, `ColorPicker`, kanban column colours,
  the data table colour scale, and the heights, widths, gaps, offsets and
  ratios of `MapPicker`, `RichTextEditor`, `ScrollArea`, `StickyHeader`,
  `DataGrid`, `DataTable`, `ResizablePanel`, `SortableGrid`, `DashboardGrid`,
  `AspectRatio`, `ResponsiveImage`, `SplitPane`, the progress components and
  `{% theme_skeleton %}`, on every render path (component class, template tag
  and Rust template engine). The helpers are
  `djust.components.utils.safe_css_color`, `safe_css_length`, `safe_css_ratio`
  and `safe_css_number`.
