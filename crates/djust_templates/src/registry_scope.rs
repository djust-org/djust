//! Engine-local registry storage. Namespace zero retains the low-level API.
use pyo3::prelude::*;
use std::cell::Cell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{RwLock, RwLockReadGuard, RwLockWriteGuard};

thread_local! { static CURRENT: Cell<u64> = const { Cell::new(0) }; }
static NEXT: AtomicU64 = AtomicU64::new(1);

#[pyfunction]
pub fn new_registry_namespace() -> u64 {
    NEXT.fetch_add(1, Ordering::Relaxed)
}
#[pyfunction]
pub fn set_registry_namespace(namespace: u64) -> u64 {
    CURRENT.with(|v| v.replace(namespace))
}
#[pyfunction(name = "current_registry_namespace")]
pub fn current() -> u64 {
    CURRENT.with(Cell::get)
}

type Maps<V> = HashMap<u64, HashMap<String, V>>;
pub(crate) struct ScopedMap<V>(RwLock<Maps<V>>);
impl<V> ScopedMap<V> {
    pub fn new() -> Self {
        Self(RwLock::new(HashMap::new()))
    }
    pub fn release(&self, namespace: u64) -> Result<(), String> {
        let removed = self
            .0
            .write()
            .map_err(|e| e.to_string())?
            .remove(&namespace);
        // Python objects may run destructors; release them after unlocking.
        drop(removed);
        Ok(())
    }
    pub fn read(&self) -> Result<Read<'_, V>, String> {
        self.0
            .read()
            .map(|maps| Read {
                maps,
                namespace: current(),
            })
            .map_err(|e| e.to_string())
    }
    pub fn write(&self) -> Result<Write<'_, V>, String> {
        self.0
            .write()
            .map(|maps| Write {
                maps,
                namespace: current(),
            })
            .map_err(|e| e.to_string())
    }
}
pub(crate) struct Read<'a, V> {
    maps: RwLockReadGuard<'a, Maps<V>>,
    namespace: u64,
}
impl<V> Read<'_, V> {
    pub fn get(&self, name: &str) -> Option<&V> {
        self.maps
            .get(&self.namespace)
            .and_then(|m| m.get(name))
            .or_else(|| self.maps.get(&0).and_then(|m| m.get(name)))
    }
    pub fn contains_key(&self, name: &str) -> bool {
        self.get(name).is_some()
    }
    pub fn keys(&self) -> impl Iterator<Item = &String> {
        let mut keys = std::collections::HashSet::new();
        for ns in [0, self.namespace] {
            if let Some(map) = self.maps.get(&ns) {
                keys.extend(map.keys());
            }
        }
        keys.into_iter()
    }
}
pub(crate) struct Write<'a, V> {
    maps: RwLockWriteGuard<'a, Maps<V>>,
    namespace: u64,
}
impl<V> Write<'_, V> {
    pub fn insert(&mut self, name: String, value: V) -> Option<V> {
        self.maps
            .entry(self.namespace)
            .or_default()
            .insert(name, value)
    }
    pub fn remove(&mut self, name: &str) -> Option<V> {
        self.maps
            .get_mut(&self.namespace)
            .and_then(|m| m.remove(name))
    }
    pub fn clear(&mut self) {
        self.maps.remove(&self.namespace);
    }
}

/// Restore the enclosing engine even when rendering returns early or unwinds.
pub(crate) struct NamespaceGuard(u64);
impl NamespaceGuard {
    pub fn enter(namespace: u64) -> Self {
        Self(set_registry_namespace(namespace))
    }
}
impl Drop for NamespaceGuard {
    fn drop(&mut self) {
        set_registry_namespace(self.0);
    }
}
