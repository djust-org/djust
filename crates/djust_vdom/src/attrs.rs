//! A node's attributes, kept as a sorted vector (#4319).
//!
//! The attributes were a `HashMap<String, String>`. Nearly every element has
//! one to three of them (the generated `dj-id`, perhaps an `id` or a
//! `class`), and a hash table for three entries is a 48-byte map header plus
//! a four-bucket table of about 210 bytes: on a 100 KB table page that was
//! 0.78 MB of a view's retained tree, for about 20 KB of names and values.
//! [`Attrs`] holds the same pairs in a `Vec`, sorted by name, so the table is
//! the pairs themselves and the header is a vector's 24 bytes.
//!
//! It keeps the map's contract: one value per name (an insert replaces), and
//! two `Attrs` are equal when they hold the same pairs. Lookups are a binary
//! search, which for a handful of names is as fast as hashing them.
//! Iteration is in name order (byte order, as `str` compares), which is the
//! order the tree has always been serialized in (`serialize_attrs_sorted`,
//! `to_html`), so nothing that reaches the wire changes: before, a caller
//! that needed an order sorted the pairs itself, and a sorted input stays
//! sorted.

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::borrow::Borrow;

/// The attributes of one element: name to value, sorted by name.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Attrs {
    pairs: Vec<(String, String)>,
}

impl Attrs {
    pub fn new() -> Self {
        Self { pairs: Vec::new() }
    }

    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            pairs: Vec::with_capacity(capacity),
        }
    }

    fn position<Q>(&self, key: &Q) -> Result<usize, usize>
    where
        String: Borrow<Q>,
        Q: Ord + ?Sized,
    {
        // `String: Borrow<Q>` lets a `&String` and a `&str` key both reach
        // here, as with `HashMap::get`; `String` and `str` order alike.
        self.pairs
            .binary_search_by(|(name, _)| Borrow::<Q>::borrow(name).cmp(key))
    }

    pub fn get<Q>(&self, key: &Q) -> Option<&String>
    where
        String: Borrow<Q>,
        Q: Ord + ?Sized,
    {
        self.position(key).ok().map(|at| &self.pairs[at].1)
    }

    pub fn get_mut<Q>(&mut self, key: &Q) -> Option<&mut String>
    where
        String: Borrow<Q>,
        Q: Ord + ?Sized,
    {
        match self.position(key) {
            Ok(at) => Some(&mut self.pairs[at].1),
            Err(_) => None,
        }
    }

    pub fn contains_key<Q>(&self, key: &Q) -> bool
    where
        String: Borrow<Q>,
        Q: Ord + ?Sized,
    {
        self.position(key).is_ok()
    }

    /// Set `key` to `value`, returning the value it replaced, as
    /// `HashMap::insert` does.
    pub fn insert(&mut self, key: String, value: String) -> Option<String> {
        match self.position(key.as_str()) {
            Ok(at) => Some(std::mem::replace(&mut self.pairs[at].1, value)),
            Err(at) => {
                self.pairs.insert(at, (key, value));
                None
            }
        }
    }

    pub fn remove<Q>(&mut self, key: &Q) -> Option<String>
    where
        String: Borrow<Q>,
        Q: Ord + ?Sized,
    {
        match self.position(key) {
            Ok(at) => Some(self.pairs.remove(at).1),
            Err(_) => None,
        }
    }

    pub fn len(&self) -> usize {
        self.pairs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.pairs.is_empty()
    }

    /// Slots allocated, for the retained-bytes accounting (#3998).
    pub fn capacity(&self) -> usize {
        self.pairs.capacity()
    }

    /// The pairs in name order.
    pub fn iter(&self) -> impl Iterator<Item = (&String, &String)> + '_ {
        self.pairs.iter().map(|(k, v)| (k, v))
    }

    pub fn keys(&self) -> impl Iterator<Item = &String> + '_ {
        self.pairs.iter().map(|(k, _)| k)
    }

    pub fn values(&self) -> impl Iterator<Item = &String> + '_ {
        self.pairs.iter().map(|(_, v)| v)
    }

    pub fn values_mut(&mut self) -> impl Iterator<Item = &mut String> + '_ {
        self.pairs.iter_mut().map(|(_, v)| v)
    }

    pub fn retain(&mut self, mut keep: impl FnMut(&String, &mut String) -> bool) {
        self.pairs.retain_mut(|(k, v)| keep(k, v));
    }

    pub fn shrink_to_fit(&mut self) {
        self.pairs.shrink_to_fit();
    }
}

impl Extend<(String, String)> for Attrs {
    fn extend<I: IntoIterator<Item = (String, String)>>(&mut self, iter: I) {
        for (k, v) in iter {
            self.insert(k, v);
        }
    }
}

impl FromIterator<(String, String)> for Attrs {
    fn from_iter<I: IntoIterator<Item = (String, String)>>(iter: I) -> Self {
        let iter = iter.into_iter();
        let mut out = Attrs::with_capacity(iter.size_hint().0);
        out.extend(iter);
        out
    }
}

impl<const N: usize> From<[(String, String); N]> for Attrs {
    fn from(pairs: [(String, String); N]) -> Self {
        pairs.into_iter().collect()
    }
}

impl From<std::collections::HashMap<String, String>> for Attrs {
    fn from(map: std::collections::HashMap<String, String>) -> Self {
        map.into_iter().collect()
    }
}

impl IntoIterator for Attrs {
    type Item = (String, String);
    type IntoIter = std::vec::IntoIter<(String, String)>;
    fn into_iter(self) -> Self::IntoIter {
        self.pairs.into_iter()
    }
}

impl<'a> IntoIterator for &'a Attrs {
    type Item = (&'a String, &'a String);
    type IntoIter = std::iter::Map<
        std::slice::Iter<'a, (String, String)>,
        fn(&'a (String, String)) -> (&'a String, &'a String),
    >;
    fn into_iter(self) -> Self::IntoIter {
        fn split(pair: &(String, String)) -> (&String, &String) {
            (&pair.0, &pair.1)
        }
        self.pairs.iter().map(split)
    }
}

impl<Q> std::ops::Index<&Q> for Attrs
where
    String: Borrow<Q>,
    Q: Ord + ?Sized,
{
    type Output = String;
    fn index(&self, key: &Q) -> &String {
        self.get(key).expect("no such attribute")
    }
}

/// Serialized as a map in name order: the bytes `serialize_attrs_sorted`
/// wrote for the hash map (#1970).
impl Serialize for Attrs {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(Some(self.pairs.len()))?;
        for (k, v) in &self.pairs {
            map.serialize_entry(k, v)?;
        }
        map.end()
    }
}

impl<'de> Deserialize<'de> for Attrs {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let map = std::collections::HashMap::<String, String>::deserialize(deserializer)?;
        let mut out: Attrs = map.into_iter().collect();
        out.shrink_to_fit();
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn insert_keeps_names_sorted_and_unique() {
        let mut attrs = Attrs::new();
        assert_eq!(attrs.insert("id".into(), "a".into()), None);
        assert_eq!(attrs.insert("class".into(), "x".into()), None);
        assert_eq!(attrs.insert("dj-id".into(), "1".into()), None);
        assert_eq!(attrs.insert("id".into(), "b".into()), Some("a".into()));
        let names: Vec<&String> = attrs.keys().collect();
        assert_eq!(names, ["class", "dj-id", "id"]);
        assert_eq!(attrs.get("id").map(String::as_str), Some("b"));
        assert_eq!(
            attrs.get(&"class".to_string()).map(String::as_str),
            Some("x")
        );
        assert!(attrs.contains_key("dj-id"));
        assert_eq!(attrs.remove("dj-id"), Some("1".into()));
        assert!(!attrs.contains_key("dj-id"));
        assert_eq!(attrs.len(), 2);
    }

    #[test]
    fn equality_ignores_insertion_order() {
        let a: Attrs = [("b".to_string(), "2".to_string()), ("a".into(), "1".into())].into();
        let b: Attrs = [("a".to_string(), "1".to_string()), ("b".into(), "2".into())].into();
        assert_eq!(a, b);
    }

    #[test]
    fn serializes_as_a_sorted_map() {
        let attrs: Attrs = [("z".to_string(), "1".to_string()), ("a".into(), "2".into())].into();
        assert_eq!(
            serde_json::to_string(&attrs).unwrap(),
            r#"{"a":"2","z":"1"}"#
        );
        let back: Attrs = serde_json::from_str(r#"{"z":"1","a":"2"}"#).unwrap();
        assert_eq!(back, attrs);
    }
}
