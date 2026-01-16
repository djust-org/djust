//! Message types for actor communication
//!
//! This module defines the message types used for communication between actors
//! and their handles. Messages use oneshot channels for request-response patterns.

use super::error::Result;
use djust_core::Value;
use djust_vdom::Patch;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use tokio::sync::oneshot;

// ============================================================================
// Session-level messages
// ============================================================================

/// Messages sent to SessionActor
#[derive(Debug)]
pub enum SessionMsg {
    /// Mount a new view (Phase 5: Now includes Python view instance)
    Mount {
        view_path: String,
        params: HashMap<String, Value>,
        python_view: Option<Py<PyAny>>,
        reply: oneshot::Sender<Result<MountResponse>>,
    },

    /// Handle an event from the client
    Event {
        event_name: String,
        params: HashMap<String, Value>,
        view_id: Option<String>, // Phase 6: Route to specific view by UUID
        reply: oneshot::Sender<Result<PatchResponse>>,
    },

    /// Unmount a specific view (Phase 6)
    Unmount {
        view_id: String,
        reply: oneshot::Sender<Result<()>>,
    },

    // Phase 8: Component management messages
    /// Create a component in a specific view (Phase 8.2: Added python_component)
    CreateComponent {
        view_id: String,
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<Py<PyAny>>, // Phase 8.2: Python component for event handlers
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Send event to a component in a specific view
    ComponentEvent {
        view_id: String,
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Update props for a component in a specific view
    UpdateComponentProps {
        view_id: String,
        component_id: String,
        props: HashMap<String, Value>,
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Remove a component from a specific view
    RemoveComponent {
        view_id: String,
        component_id: String,
        reply: oneshot::Sender<Result<()>>,
    },

    /// Health check ping
    Ping { reply: oneshot::Sender<()> },

    /// Graceful shutdown
    Shutdown,
}

/// Response from mounting a view
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MountResponse {
    /// Rendered HTML
    pub html: String,
    /// Session ID
    pub session_id: String,
    /// View ID (UUID) for routing events to specific views (Phase 6)
    pub view_id: String,
}

/// Response from handling an event
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatchResponse {
    /// VDOM patches (if available)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub patches: Option<Vec<Patch>>,
    /// Full HTML (fallback if no VDOM)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub html: Option<String>,
    /// Version number for ordering
    pub version: u64,
}

// ============================================================================
// View-level messages
// ============================================================================

/// Messages sent to ViewActor
#[derive(Debug)]
pub enum ViewMsg {
    /// Update state
    UpdateState {
        updates: HashMap<String, Value>,
        reply: oneshot::Sender<Result<()>>,
    },

    /// Render to HTML
    Render {
        reply: oneshot::Sender<Result<String>>,
    },

    /// Render and compute VDOM diff
    RenderWithDiff {
        reply: oneshot::Sender<Result<RenderResult>>,
    },

    /// Set Python view instance for event handler callbacks (Phase 5)
    SetPythonView {
        view: Py<PyAny>,
        reply: oneshot::Sender<Result<()>>,
    },

    /// Handle an event by calling Python event handler (Phase 5)
    Event {
        event_name: String,
        params: HashMap<String, Value>,
        reply: oneshot::Sender<Result<RenderResult>>,
    },

    /// Create a child ComponentActor (Phase 8.2: Added python_component)
    CreateComponent {
        component_id: String,
        template_string: String,
        initial_props: HashMap<String, Value>,
        python_component: Option<Py<PyAny>>, // Phase 8.2: Python component for event handlers
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Route event to a specific child component (Phase 8)
    ComponentEvent {
        component_id: String,
        event_name: String,
        params: HashMap<String, Value>,
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Update props for a specific child component (Phase 8)
    UpdateComponentProps {
        component_id: String,
        props: HashMap<String, Value>,
        reply: oneshot::Sender<Result<String>>, // Returns rendered HTML
    },

    /// Remove a child component (Phase 8)
    RemoveComponent {
        component_id: String,
        reply: oneshot::Sender<Result<()>>,
    },

    /// Receive event from child component (Phase 8.2)
    ComponentEventFromChild {
        component_id: String,
        event_name: String,
        data: HashMap<String, Value>,
    },

    /// Reset state
    Reset,

    /// Shutdown this view
    Shutdown,
}

/// Result from rendering with VDOM diff
#[derive(Debug, Clone)]
pub struct RenderResult {
    /// Rendered HTML
    pub html: String,
    /// VDOM patches (if available)
    pub patches: Option<Vec<Patch>>,
    /// Version number
    pub version: u64,
}

// ============================================================================
// Component-level messages (future)
// ============================================================================

/// Messages sent to ComponentActor
#[derive(Debug)]
pub enum ComponentMsg {
    /// Update props from parent
    UpdateProps {
        props: HashMap<String, Value>,
        reply: oneshot::Sender<Result<()>>,
    },

    /// Send event to parent
    SendToParent {
        event: String,
        data: HashMap<String, Value>,
    },

    /// Shutdown this component
    Shutdown,
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mount_response_serialization() {
        let response = MountResponse {
            html: "<div>Test</div>".to_string(),
            session_id: "session-123".to_string(),
            view_id: "view-456".to_string(),
        };

        let json = serde_json::to_string(&response).unwrap();
        assert!(json.contains("session-123"));
        assert!(json.contains("view-456"));
        assert!(json.contains("<div>Test</div>"));

        let deserialized: MountResponse = serde_json::from_str(&json).unwrap();
        assert_eq!(deserialized.html, response.html);
        assert_eq!(deserialized.session_id, response.session_id);
        assert_eq!(deserialized.view_id, response.view_id);
    }

    #[test]
    fn test_patch_response_serialization() {
        let response = PatchResponse {
            patches: None,
            html: Some("<div>Updated</div>".to_string()),
            version: 2,
        };

        let json = serde_json::to_string(&response).unwrap();
        let deserialized: PatchResponse = serde_json::from_str(&json).unwrap();

        assert_eq!(deserialized.version, 2);
        assert_eq!(deserialized.html, Some("<div>Updated</div>".to_string()));
        assert!(deserialized.patches.is_none());
    }

    #[test]
    fn test_patch_response_with_patches() {
        let patches = vec![
            // Would need actual Patch instances from djust_vdom
        ];

        let response = PatchResponse {
            patches: Some(patches),
            html: None,
            version: 3,
        };

        // Patches should be present, html should be omitted in JSON
        let json = serde_json::to_string(&response).unwrap();
        assert!(!json.contains("\"html\""));
    }

    #[test]
    fn test_render_result_clone() {
        let result = RenderResult {
            html: "<div>Test</div>".to_string(),
            patches: None,
            version: 1,
        };

        let cloned = result.clone();
        assert_eq!(cloned.html, result.html);
        assert_eq!(cloned.version, result.version);
    }
}
