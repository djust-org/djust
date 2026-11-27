//! Deterministic accounting of the heap a value owns (#3998).
//!
//! The numbers are computed from capacities and `size_of`, not read from the
//! allocator: the same value reports the same bytes on every machine, so a
//! test can assert on them and a growth law can be fitted to them. They are a
//! lower bound on what the allocator holds (it rounds each block up and adds
//! its own bookkeeping).

use crate::object_key::ObjectKey;
use crate::Value;
use std::mem::size_of;

/// Heap bytes of a hashbrown table (`HashMap`, `HashSet`, `AHashMap`) with
/// `capacity` usable slots of `entry` bytes each: the bucket array plus one
/// control byte per bucket and one trailing control group.
pub fn table_bytes(capacity: usize, entry: usize) -> usize {
    if capacity == 0 {
        return 0;
    }
    let buckets = (capacity * 8 / 7 + 1).next_power_of_two();
    buckets * (entry + 1) + 16
}

fn key_bytes(key: &ObjectKey) -> usize {
    match key {
        ObjectKey::Str(s) | ObjectKey::Method(s) | ObjectKey::Decimal(s) | ObjectKey::BigInt(s) => {
            s.capacity()
        }
        ObjectKey::Tuple(items) => {
            items.capacity() * size_of::<ObjectKey>() + items.iter().map(key_bytes).sum::<usize>()
        }
        ObjectKey::Other(other) => {
            size_of::<crate::object_key::OtherKey>()
                + other.display.capacity()
                + other.repr.capacity()
        }
        _ => 0,
    }
}

fn items_bytes(items: &Vec<Value>) -> usize {
    items.capacity() * size_of::<Value>() + items.iter().map(Value::retained_bytes).sum::<usize>()
}

impl Value {
    /// Heap bytes this value owns, NOT counting the `Value` itself (its
    /// container counts that). An [`crate::Encoded`] carrier is counted as its
    /// box only: the strings inside it are not walked.
    pub fn retained_bytes(&self) -> usize {
        match self {
            Value::Missing | Value::None | Value::Bool(_) | Value::Integer(_) | Value::Float(_) => {
                0
            }
            Value::String(s)
            | Value::SafeString(s)
            | Value::ModelName(s)
            | Value::Decimal(s)
            | Value::BigInt(s) => s.capacity(),
            Value::List(items) | Value::Tuple(items) | Value::DictView { items, .. } => {
                items_bytes(items)
            }
            Value::NamedTuple { meta, items } => {
                size_of::<crate::NamedTupleMeta>()
                    + meta.name.capacity()
                    + meta.fields.capacity() * size_of::<String>()
                    + meta.fields.iter().map(String::capacity).sum::<usize>()
                    + items_bytes(items)
            }
            Value::Object(map) => {
                // The box (#4319), then the IndexMap's own heap: an entries
                // Vec of (hash, key, value) plus a table of indices.
                size_of::<indexmap::IndexMap<ObjectKey, Value>>()
                    + map.capacity()
                        * (size_of::<usize>() + size_of::<ObjectKey>() + size_of::<Value>())
                    + table_bytes(map.capacity(), size_of::<usize>())
                    + map
                        .iter()
                        .map(|(k, v)| key_bytes(k) + v.retained_bytes())
                        .sum::<usize>()
            }
            Value::Encoded(_) => size_of::<crate::Encoded>(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scalars_own_no_heap() {
        assert_eq!(Value::Integer(7).retained_bytes(), 0);
        assert_eq!(Value::None.retained_bytes(), 0);
    }

    #[test]
    fn a_string_owns_its_capacity() {
        let mut s = String::with_capacity(64);
        s.push('a');
        assert_eq!(Value::String(s).retained_bytes(), 64);
    }

    #[test]
    fn a_list_owns_its_slots_and_its_items() {
        let items = vec![Value::String("abcd".to_string()), Value::Integer(1)];
        let slots = items.capacity() * size_of::<Value>();
        assert_eq!(Value::List(items).retained_bytes(), slots + 4);
    }

    #[test]
    fn a_larger_list_reports_more() {
        let small = Value::List((0..10).map(Value::Integer).collect());
        let large = Value::List((0..1000).map(Value::Integer).collect());
        assert!(large.retained_bytes() >= 100 * small.retained_bytes());
    }

    /// #4319: a view holds one `Value` per list item and per dict entry of
    /// its state, and one `ObjectKey` per dict entry, so their sizes are most
    /// of what its Rust copy of the state costs. The large, rare variants are
    /// boxed to keep them here.
    #[test]
    fn a_value_and_a_key_stay_small() {
        assert!(size_of::<Value>() <= 40, "{}", size_of::<Value>());
        assert!(size_of::<ObjectKey>() <= 32, "{}", size_of::<ObjectKey>());
    }
}
