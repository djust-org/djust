use djust_core::{values_structurally_equal, Encoded, Value};

#[test]
fn encoded_display_safety_is_runtime_only() {
    let encoded = Encoded {
        type_name: "TextObject".into(),
        display: "<b>&</b>".into(),
        display_safe: true,
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
    };
    let mut plain = encoded.clone();
    plain.display_safe = false;
    let marked = Value::Encoded(Box::new(encoded));
    let plain = Value::Encoded(Box::new(plain));
    assert!(!marked.is_safe_string());
    assert!(marked.string_conversion_is_safe());
    assert!(!values_structurally_equal(&marked, &plain));
    assert_eq!(
        serde_json::to_vec(&marked).unwrap(),
        serde_json::to_vec(&plain).unwrap()
    );
    assert_eq!(
        rmp_serde::to_vec(&marked).unwrap(),
        rmp_serde::to_vec(&plain).unwrap()
    );
    let restored: Value = rmp_serde::from_slice(&rmp_serde::to_vec(&marked).unwrap()).unwrap();
    assert!(values_structurally_equal(&restored, &plain));
    assert!(!restored.string_conversion_is_safe());
}
