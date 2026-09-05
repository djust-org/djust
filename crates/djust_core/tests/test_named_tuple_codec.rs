use djust_core::{values_structurally_equal, Context, Value};

fn row() -> Value {
    Value::NamedTuple {
        name: "Row".into(),
        fields: vec!["label".into(), "items".into()],
        items: vec![
            Value::String("label".into()),
            Value::List(vec![Value::Integer(1)]),
        ],
    }
}

#[test]
fn named_tuple_keeps_fields_and_items_through_binary_state() {
    let original = row();
    let bytes = rmp_serde::to_vec_named(&original).unwrap();
    let restored: Value = rmp_serde::from_slice(&bytes).unwrap();
    assert!(values_structurally_equal(&original, &restored));
    let mut context = Context::new();
    context.set("row".into(), restored);
    assert_eq!(context.get("row.label").unwrap().to_string(), "label");
    assert_eq!(context.get("row.1.0").unwrap().to_string(), "1");
}

#[test]
fn named_tuple_json_uses_array_semantics_and_repr_names_fields() {
    assert_eq!(serde_json::to_string(&row()).unwrap(), r#"["label",[1]]"#);
    assert_eq!(row().py_repr(), "Row(label='label', items=[1])");
}
