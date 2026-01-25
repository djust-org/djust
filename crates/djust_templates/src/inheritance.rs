//! Template inheritance support for Django-style {% extends %} and {% block %}
//!
//! This module handles:
//! - Detecting templates that use {% extends %}
//! - Loading parent templates recursively
//! - Building inheritance chains
//! - Merging blocks from child to parent
//! - Rendering final templates with block overrides

use crate::parser::Node;
use djust_core::{DjangoRustError, Result};
use std::collections::HashMap;

/// Represents a template in the inheritance chain
#[derive(Debug, Clone)]
pub struct TemplateLayer {
    pub nodes: Vec<Node>,
    pub blocks: HashMap<String, Vec<Node>>,
}

/// Represents a complete inheritance chain from child to root
#[derive(Debug)]
pub struct InheritanceChain {
    pub layers: Vec<TemplateLayer>, // Index 0 = child, last = root parent
    pub merged_blocks: HashMap<String, Vec<Node>>,
    pub parent_blocks: HashMap<String, Vec<Node>>, // Parent block content for {{ block.super }}
}

impl InheritanceChain {
    /// Create a new inheritance chain from parsed nodes
    pub fn new(nodes: Vec<Node>) -> Self {
        let layer = TemplateLayer {
            nodes: nodes.clone(),
            blocks: extract_blocks(&nodes),
        };

        InheritanceChain {
            layers: vec![layer],
            merged_blocks: HashMap::new(),
            parent_blocks: HashMap::new(),
        }
    }

    /// Check if this template uses extends
    pub fn uses_extends(&self) -> Option<&str> {
        // Check the most recently added layer (last in the chain)
        // This allows us to follow the inheritance chain upward
        if let Some(last_layer) = self.layers.last() {
            for node in &last_layer.nodes {
                if let Node::Extends(parent) = node {
                    return Some(parent);
                }
            }
        }
        None
    }

    /// Add a parent layer to the chain
    pub fn add_parent(&mut self, parent_nodes: Vec<Node>) {
        let parent_layer = TemplateLayer {
            nodes: parent_nodes.clone(),
            blocks: extract_blocks(&parent_nodes),
        };
        self.layers.push(parent_layer);
    }

    /// Merge blocks from all layers (child overrides parent)
    pub fn merge_blocks(&mut self) {
        let mut merged: HashMap<String, Vec<Node>> = HashMap::new();
        let mut parents: HashMap<String, Vec<Node>> = HashMap::new();

        // Start from root (parent) and work toward child (first layer)
        // Track parent content before it gets overridden by child
        for layer in self.layers.iter().rev() {
            for (name, nodes) in &layer.blocks {
                // If this block already exists, save current content as parent
                if let Some(existing) = merged.get(name) {
                    parents.insert(name.clone(), existing.clone());
                }
                // Insert new content (child overrides parent)
                merged.insert(name.clone(), nodes.clone());
            }
        }

        self.merged_blocks = merged;
        self.parent_blocks = parents;
    }

    /// Get the root template nodes (furthest ancestor)
    pub fn get_root_nodes(&self) -> &[Node] {
        &self.layers.last().unwrap().nodes
    }

    /// Replace blocks in nodes with merged block content
    pub fn apply_block_overrides(&self, nodes: &[Node]) -> Vec<Node> {
        nodes
            .iter()
            .map(|node| self.apply_override_to_node(node))
            .collect()
    }

    fn apply_override_to_node(&self, node: &Node) -> Node {
        match node {
            Node::Block { name, nodes: _ } => {
                // Replace block with merged content
                if let Some(merged_nodes) = self.merged_blocks.get(name) {
                    Node::Block {
                        name: name.clone(),
                        nodes: merged_nodes.clone(),
                    }
                } else {
                    // Keep original if no override
                    node.clone()
                }
            }
            // Recursively process nested structures
            Node::If {
                condition,
                true_nodes,
                false_nodes,
            } => Node::If {
                condition: condition.clone(),
                true_nodes: self.apply_block_overrides(true_nodes),
                false_nodes: self.apply_block_overrides(false_nodes),
            },
            Node::For {
                var_names,
                iterable,
                reversed,
                nodes,
                empty_nodes,
            } => Node::For {
                var_names: var_names.clone(),
                iterable: iterable.clone(),
                reversed: *reversed,
                nodes: self.apply_block_overrides(nodes),
                empty_nodes: self.apply_block_overrides(empty_nodes),
            },
            Node::With { assignments, nodes } => Node::With {
                assignments: assignments.clone(),
                nodes: self.apply_block_overrides(nodes),
            },
            // Skip extends nodes in the output
            Node::Extends(_) => Node::Comment,
            // Everything else passes through unchanged
            _ => node.clone(),
        }
    }
}

/// Extract all {% block %} tags from nodes and map them by name
fn extract_blocks(nodes: &[Node]) -> HashMap<String, Vec<Node>> {
    let mut blocks = HashMap::new();

    for node in nodes {
        extract_blocks_recursive(node, &mut blocks);
    }

    blocks
}

fn extract_blocks_recursive(node: &Node, blocks: &mut HashMap<String, Vec<Node>>) {
    match node {
        Node::Block { name, nodes } => {
            blocks.insert(name.clone(), nodes.clone());
            // Also extract nested blocks
            for child in nodes {
                extract_blocks_recursive(child, blocks);
            }
        }
        Node::If {
            true_nodes,
            false_nodes,
            ..
        } => {
            for child in true_nodes {
                extract_blocks_recursive(child, blocks);
            }
            for child in false_nodes {
                extract_blocks_recursive(child, blocks);
            }
        }
        Node::For { nodes, .. } | Node::With { nodes, .. } => {
            for child in nodes {
                extract_blocks_recursive(child, blocks);
            }
        }
        _ => {}
    }
}

/// Trait for loading parent templates
/// This will be implemented by the Python integration layer
pub trait TemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>>;
}

/// Build complete inheritance chain by recursively loading parents
pub fn build_inheritance_chain<L: TemplateLoader>(
    nodes: Vec<Node>,
    loader: &L,
    max_depth: usize,
) -> Result<InheritanceChain> {
    let mut chain = InheritanceChain::new(nodes);
    let mut depth = 0;

    // Follow extends chain up to max_depth
    while depth < max_depth {
        if let Some(parent_name) = chain.uses_extends() {
            let parent_name = parent_name.to_string(); // Clone to avoid borrow issues
            let parent_nodes = loader.load_template(&parent_name)?;
            chain.add_parent(parent_nodes);
            depth += 1;
        } else {
            // No more parents
            break;
        }
    }

    if depth >= max_depth {
        return Err(DjangoRustError::TemplateError(format!(
            "Template inheritance depth limit ({max_depth}) exceeded - possible circular inheritance"
        )));
    }

    // Merge all blocks
    chain.merge_blocks();

    Ok(chain)
}

/// Filesystem-based template loader for production use
pub struct FilesystemTemplateLoader {
    template_dirs: Vec<std::path::PathBuf>,
}

impl FilesystemTemplateLoader {
    /// Create a new filesystem template loader with search directories
    pub fn new(template_dirs: Vec<std::path::PathBuf>) -> Self {
        Self { template_dirs }
    }

    /// Find a template file by searching through template directories
    fn find_template(&self, name: &str) -> Result<std::path::PathBuf> {
        for dir in &self.template_dirs {
            let path = dir.join(name);
            if path.exists() {
                return Ok(path);
            }
        }

        // Build list of searched directories for error message
        let searched_paths: Vec<String> = self
            .template_dirs
            .iter()
            .map(|dir| format!("  - {}", dir.display()))
            .collect();

        Err(DjangoRustError::TemplateError(format!(
            "Template not found: {}\nSearched in:\n{}",
            name,
            searched_paths.join("\n")
        )))
    }
}

impl TemplateLoader for FilesystemTemplateLoader {
    fn load_template(&self, name: &str) -> Result<Vec<Node>> {
        use crate::lexer;
        use crate::parser;

        // Find template file
        let path = self.find_template(name)?;

        // Read template source
        let source = std::fs::read_to_string(&path).map_err(|e| {
            DjangoRustError::TemplateError(format!(
                "Failed to read template {}: {}",
                path.display(),
                e
            ))
        })?;

        // Parse template
        let tokens = lexer::tokenize(&source)?;
        parser::parse(&tokens)
    }
}

/// Convert AST nodes back to template string format (preserves Django syntax)
fn nodes_to_template_string(nodes: &[Node]) -> String {
    let mut output = String::new();
    for node in nodes {
        output.push_str(&node_to_template_string(node));
    }
    output
}

/// Convert a single node back to template string format
fn node_to_template_string(node: &Node) -> String {
    match node {
        Node::Text(text) => text.clone(),
        Node::Variable(var_name, filters) => {
            let mut result = format!("{{{{ {var_name} ");
            for (filter_name, arg) in filters {
                if let Some(arg) = arg {
                    result.push_str(&format!("|{filter_name}:\"{arg}\" "));
                } else {
                    result.push_str(&format!("|{filter_name} "));
                }
            }
            result.push_str("}}");
            result
        }
        Node::Block { name, nodes } => {
            let mut result = format!("{{% block {name} %}}");
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endblock %}");
            result
        }
        Node::If {
            condition,
            true_nodes,
            false_nodes,
        } => {
            let mut result = format!("{{% if {condition} %}}");
            result.push_str(&nodes_to_template_string(true_nodes));
            if !false_nodes.is_empty() {
                result.push_str("{% else %}");
                result.push_str(&nodes_to_template_string(false_nodes));
            }
            result.push_str("{% endif %}");
            result
        }
        Node::For {
            var_names,
            iterable,
            reversed,
            nodes,
            empty_nodes,
        } => {
            let var_names_str = var_names.join(", ");
            let mut result = format!("{{% for {var_names_str} in {iterable}");
            if *reversed {
                result.push_str(" reversed");
            }
            result.push_str(" %}");
            result.push_str(&nodes_to_template_string(nodes));
            if !empty_nodes.is_empty() {
                result.push_str("{% empty %}");
                result.push_str(&nodes_to_template_string(empty_nodes));
            }
            result.push_str("{% endfor %}");
            result
        }
        Node::With { assignments, nodes } => {
            let mut result = String::from("{% with ");
            for (i, (key, value)) in assignments.iter().enumerate() {
                if i > 0 {
                    result.push(' ');
                }
                result.push_str(&format!("{key}={value}"));
            }
            result.push_str(" %}");
            result.push_str(&nodes_to_template_string(nodes));
            result.push_str("{% endwith %}");
            result
        }
        Node::Comment => String::new(),    // Comments are stripped
        Node::Extends(_) => String::new(), // Extends is already processed
        Node::Include {
            template,
            with_vars,
            only,
        } => {
            let mut result = format!("{{% include \"{template}\"");
            if !with_vars.is_empty() {
                result.push_str(" with");
                for (key, value) in with_vars {
                    result.push_str(&format!(" {key}={value}"));
                }
            }
            if *only {
                result.push_str(" only");
            }
            result.push_str(" %}");
            result
        }
        Node::CsrfToken => "{% csrf_token %}".to_string(),
        Node::Static(path) => format!("{{% static \"{path}\" %}}"),
        Node::ReactComponent { .. } => {
            // React components should be preserved as-is if possible
            // For now, skip them as they're handled separately
            String::new()
        }
        Node::RustComponent { .. } => {
            // Rust components should be preserved as-is if possible
            // For now, skip them as they're handled separately
            String::new()
        }
        Node::CustomTag { name, args } => {
            // Reconstruct custom tag: {% tagname arg1 arg2 %}
            let mut result = format!("{{% {name}");
            for arg in args {
                result.push(' ');
                result.push_str(arg);
            }
            result.push_str(" %}");
            result
        }
    }
}

/// High-level function to resolve template inheritance from a file
/// Returns the merged template as a string (with Django template syntax preserved)
pub fn resolve_template_inheritance(
    template_path: &str,
    template_dirs: &[std::path::PathBuf],
) -> Result<String> {
    // Create loader
    let loader = FilesystemTemplateLoader::new(template_dirs.to_vec());

    // Load initial template
    let initial_path = loader.find_template(template_path)?;
    let source = std::fs::read_to_string(&initial_path).map_err(|e| {
        DjangoRustError::TemplateError(format!(
            "Failed to read template {}: {}",
            initial_path.display(),
            e
        ))
    })?;

    // Parse initial template
    let tokens = crate::lexer::tokenize(&source)?;
    let nodes = crate::parser::parse(&tokens)?;

    // Check if template uses inheritance
    let uses_extends = nodes.iter().any(|node| matches!(node, Node::Extends(_)));

    if !uses_extends {
        // No inheritance, return source as-is
        return Ok(source);
    }

    // Build inheritance chain
    let chain = build_inheritance_chain(nodes, &loader, 10)?;

    // Get merged template nodes
    let root_nodes = chain.get_root_nodes();
    let final_nodes = chain.apply_block_overrides(root_nodes);

    // Convert AST back to template string (preserves {{ var }} syntax)
    Ok(nodes_to_template_string(&final_nodes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_blocks() {
        let nodes = vec![
            Node::Text("Before".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![Node::Text("Hello".to_string())],
            },
            Node::Text("After".to_string()),
        ];

        let blocks = extract_blocks(&nodes);
        assert_eq!(blocks.len(), 1);
        assert!(blocks.contains_key("content"));
    }

    #[test]
    fn test_uses_extends() {
        let nodes = vec![
            Node::Extends("base.html".to_string()),
            Node::Block {
                name: "content".to_string(),
                nodes: vec![],
            },
        ];

        let chain = InheritanceChain::new(nodes);
        assert_eq!(chain.uses_extends(), Some("base.html"));
    }

    #[test]
    fn test_no_extends() {
        let nodes = vec![Node::Text("Hello".to_string())];
        let chain = InheritanceChain::new(nodes);
        assert_eq!(chain.uses_extends(), None);
    }

    #[test]
    fn test_nodes_to_template_string_preserves_variables() {
        // Test that variables are preserved as {{ var }} not rendered
        let nodes = vec![
            Node::Variable("name".to_string(), vec![]),
            Node::Text(" is here".to_string()),
        ];

        let result = nodes_to_template_string(&nodes);

        // Should preserve Django template syntax
        assert!(result.contains("{{ name }}"));
        assert!(!result.contains("{{name}}")); // Should have spaces
    }

    #[test]
    fn test_nodes_to_template_string_preserves_filters() {
        let nodes = vec![Node::Variable(
            "price".to_string(),
            vec![
                ("floatformat".to_string(), Some("2".to_string())),
                ("default".to_string(), Some("0.00".to_string())),
            ],
        )];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{{ price"));
        assert!(result.contains("|floatformat:\"2\""));
        assert!(result.contains("|default:\"0.00\""));
        assert!(result.contains("}}"));
    }

    #[test]
    fn test_nodes_to_template_string_block_syntax() {
        let nodes = vec![Node::Block {
            name: "content".to_string(),
            nodes: vec![
                Node::Text("<p>".to_string()),
                Node::Variable("message".to_string(), vec![]),
                Node::Text("</p>".to_string()),
            ],
        }];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% block content %}"));
        assert!(result.contains("{{ message }}"));
        assert!(result.contains("{% endblock %}"));
    }

    #[test]
    fn test_nodes_to_template_string_if_else() {
        let nodes = vec![Node::If {
            condition: "user.is_authenticated".to_string(),
            true_nodes: vec![Node::Text("Welcome!".to_string())],
            false_nodes: vec![Node::Text("Please login".to_string())],
        }];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% if user.is_authenticated %}"));
        assert!(result.contains("Welcome!"));
        assert!(result.contains("{% else %}"));
        assert!(result.contains("Please login"));
        assert!(result.contains("{% endif %}"));
    }

    #[test]
    fn test_nodes_to_template_string_for_loop() {
        let nodes = vec![Node::For {
            var_names: vec!["item".to_string()],
            iterable: "items".to_string(),
            reversed: false,
            nodes: vec![Node::Variable("item.name".to_string(), vec![])],
            empty_nodes: vec![],
        }];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% for item in items %}"));
        assert!(result.contains("{{ item.name }}"));
        assert!(result.contains("{% endfor %}"));
    }

    #[test]
    fn test_nodes_to_template_string_for_loop_reversed() {
        let nodes = vec![Node::For {
            var_names: vec!["item".to_string()],
            iterable: "items".to_string(),
            reversed: true,
            nodes: vec![Node::Text("Item".to_string())],
            empty_nodes: vec![],
        }];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% for item in items reversed %}"));
    }

    #[test]
    fn test_nodes_to_template_string_with_tag() {
        let nodes = vec![Node::With {
            assignments: vec![
                ("total".to_string(), "price|add:tax".to_string()),
                ("discount".to_string(), "0.1".to_string()),
            ],
            nodes: vec![Node::Variable("total".to_string(), vec![])],
        }];

        let result = nodes_to_template_string(&nodes);

        assert!(result.contains("{% with total=price|add:tax discount=0.1 %}"));
        assert!(result.contains("{{ total }}"));
        assert!(result.contains("{% endwith %}"));
    }

    #[test]
    fn test_nodes_to_template_string_csrf_token() {
        let nodes = vec![Node::CsrfToken];
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% csrf_token %}");
    }

    #[test]
    fn test_nodes_to_template_string_static() {
        let nodes = vec![Node::Static("css/style.css".to_string())];
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% static \"css/style.css\" %}");
    }

    #[test]
    fn test_nodes_to_template_string_include() {
        let nodes = vec![Node::Include {
            template: "partials/header.html".to_string(),
            with_vars: vec![],
            only: false,
        }];
        let result = nodes_to_template_string(&nodes);
        assert_eq!(result, "{% include \"partials/header.html\" %}");
    }

    #[test]
    fn test_nodes_to_template_string_complex_nested() {
        // Test a complex nested structure
        let nodes = vec![Node::Block {
            name: "content".to_string(),
            nodes: vec![Node::If {
                condition: "items".to_string(),
                true_nodes: vec![Node::For {
                    var_names: vec!["item".to_string()],
                    iterable: "items".to_string(),
                    reversed: false,
                    nodes: vec![
                        Node::Text("<li>".to_string()),
                        Node::Variable("item.name".to_string(), vec![("upper".to_string(), None)]),
                        Node::Text("</li>".to_string()),
                    ],
                    empty_nodes: vec![],
                }],
                false_nodes: vec![Node::Text("<p>No items</p>".to_string())],
            }],
        }];

        let result = nodes_to_template_string(&nodes);

        // Should preserve all nested structures
        assert!(result.contains("{% block content %}"));
        assert!(result.contains("{% if items %}"));
        assert!(result.contains("{% for item in items %}"));
        assert!(result.contains("{{ item.name |upper }}"));
        assert!(result.contains("{% else %}"));
        assert!(result.contains("<p>No items</p>"));
        assert!(result.contains("{% endfor %}"));
        assert!(result.contains("{% endif %}"));
        assert!(result.contains("{% endblock %}"));
    }

    #[test]
    fn test_template_not_found_error_lists_directories() {
        use tempfile::TempDir;

        // Create temporary directories
        let temp_dir1 = TempDir::new().unwrap();
        let temp_dir2 = TempDir::new().unwrap();
        let temp_dir3 = TempDir::new().unwrap();

        let dirs = vec![
            temp_dir1.path().to_path_buf(),
            temp_dir2.path().to_path_buf(),
            temp_dir3.path().to_path_buf(),
        ];

        let loader = FilesystemTemplateLoader::new(dirs.clone());

        // Try to find a template that doesn't exist
        let result = loader.find_template("nonexistent.html");

        // Should be an error
        assert!(result.is_err());

        // Extract error message
        let error_message = result.unwrap_err().to_string();

        // Should contain the template name
        assert!(error_message.contains("nonexistent.html"));

        // Should contain "Searched in:" header
        assert!(error_message.contains("Searched in:"));

        // Should list all three directories
        for dir in &dirs {
            let dir_str = dir.display().to_string();
            assert!(
                error_message.contains(&dir_str),
                "Error message should contain directory: {dir_str}\nActual message: {error_message}"
            );
        }

        // Should have proper formatting with bullet points
        assert!(error_message.contains("  - "));
    }
}
