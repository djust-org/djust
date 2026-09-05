use djust_core::{Context, Encoded, Value};
use djust_templates::loop_cache::{LoopCacheGuard, LoopRenderCache};
use djust_templates::Template;
use indexmap::IndexMap;

#[test]
fn changing_only_display_safety_invalidates_cached_html() {
    pyo3::Python::initialize();
    let template = Template::new("{% for row in rows %}{{ row.text }}{% endfor %}").unwrap();
    let mut cache = LoopRenderCache::default();
    for safe in [true, false, true] {
        let mut row = IndexMap::new();
        row.insert("id".into(), Value::Integer(1));
        row.insert(
            "text".into(),
            Value::Encoded(Box::new(Encoded {
                type_name: "TextObject".into(),
                display: "<b>&</b>".into(),
                display_safe: safe,
                json: "<b>&</b>".into(),
                truthy: true,
                len: None,
                iterable: false,
                repr: "TextObject()".into(),
                cmp_key: None,
                attrs: Default::default(),
                items: None,
                eq_class: None,
                live: None,
            })),
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
