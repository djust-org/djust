use djust_core::{Context, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

#[test]
fn changing_only_safety_invalidates_cached_html() {
    let template = Template::new("{% for row in rows %}{{ row.text }}{% endfor %}").unwrap();
    let mut cache = LoopRenderCache::default();
    for safe in [true, false, true] {
        let mut row = IndexMap::new();
        row.insert("id".into(), Value::Integer(1));
        row.insert(
            "text".into(),
            if safe {
                Value::SafeString("<b>&</b>".into())
            } else {
                Value::String("<b>&</b>".into())
            },
        );
        let mut context = Context::new();
        context.set("rows".into(), Value::List(vec![Value::Object(row)]));
        cache.begin_render();
        let actual = {
            let _guard = LoopCacheGuard::install(&mut cache);
            template.render(&context).unwrap()
        };
        cache.prune();
        assert_eq!(
            actual,
            if safe {
                "<b>&</b>"
            } else {
                "&lt;b&gt;&amp;&lt;/b&gt;"
            }
        );
    }
}
