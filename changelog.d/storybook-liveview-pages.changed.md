- **The storybook index and category pages are LiveViews, and none of the
  storybook's interactivity is bespoke JavaScript any more.** The index, the
  category pages and the shared sidebar carried ~100 lines of inline `<script>`
  that filtered components by writing `style.display` — on a page whose entire
  purpose is demonstrating djust. The sidebar search is now `dj-input` with
  `dj-debounce`, the category chips and sidebar headers are `dj-click`, and the
  filtering happens in Python (`StorybookSidebarMixin`). Grouping the *filtered*
  list with `{% regroup %}` also retires the empty-category cleanup the script
  hand-rolled, and an empty result is now a rendered state rather than a grid of
  hidden cards. The index and category pages keep their existing access gate
  (`DJUST_THEMING_GALLERY_PUBLIC` / `DEBUG` / `is_staff`), now enforced on every
  transport via `check_permissions` rather than only on the initial HTTP GET.
- **Gallery templates no longer name their view class.** They emit the mount
  root and nothing else; `mixins/request.py` derives the path from the class
  doing the render and stamps it. Naming it by hand is how all ten
  components-gallery views came to declare a module that has never existed
  (#2893) — a path the server computes cannot drift from the class that
  rendered the page.
