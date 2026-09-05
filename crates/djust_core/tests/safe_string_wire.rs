use djust_core::{values_structurally_equal, Value};

#[test]
fn safety_is_runtime_only_in_json_and_messagepack() {
    for text in ["", "<b>&é</b>"] {
        let safe = Value::SafeString(text.into());
        let plain = Value::String(text.into());
        assert!(values_structurally_equal(&safe, &safe));
        assert!(!values_structurally_equal(&safe, &plain));
        assert_eq!(
            serde_json::to_vec(&safe).unwrap(),
            serde_json::to_vec(&plain).unwrap()
        );
        assert_eq!(
            rmp_serde::to_vec(&safe).unwrap(),
            rmp_serde::to_vec(&plain).unwrap()
        );
        let nested = Value::List(vec![safe]);
        let json: Value = serde_json::from_slice(&serde_json::to_vec(&nested).unwrap()).unwrap();
        let binary: Value = rmp_serde::from_slice(&rmp_serde::to_vec(&nested).unwrap()).unwrap();
        let expected = Value::List(vec![plain]);
        assert!(values_structurally_equal(&json, &expected));
        assert!(values_structurally_equal(&binary, &expected));
    }
}
