- **Fix the component gallery's index page rendering 18 dead links.** Its
  category cards and the sidebar both link through `{{ cat.url }}`, and the
  index's `_category_cards` list — built in `GalleryIndexView.mount`, unlike
  the category views' which already carried the key — was constructed without
  a `"url"` entry. The attribute therefore resolved to the empty string and
  every link rendered as `href=""`: nine cards and nine sidebar entries
  pointing at the current page while looking like ordinary links. The key is
  now populated from `_category_url`, so the links point at the category
  LiveViews under whatever mount point the gallery is served from.
