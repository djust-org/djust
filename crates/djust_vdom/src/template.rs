//! Template inheritance system for Django-like template syntax
//!
//! This module provides support for `{% extends %}` and `{% block %}` tags,
//! allowing templates to inherit from base templates and override specific blocks.

use djust_core::{DjangoRustError, Result};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

/// Template tag types
#[derive(Debug, Clone, PartialEq)]
pub enum TemplateTag {
    /// {% extends "base.html" %}
    Extends(String),
    /// {% block name %}content{% endblock %}
    Block { name: String, content: String },
}

/// Parsed template with inheritance information
#[derive(Debug, Clone)]
pub struct Template {
    /// The original template content
    pub content: String,
    /// Optional extends declaration
    pub extends: Option<String>,
    /// Blocks defined in this template
    pub blocks: HashMap<String, String>,
}

impl Template {
    /// Create a new empty template
    pub fn new(content: String) -> Self {
        Self {
            content,
            extends: None,
            blocks: HashMap::new(),
        }
    }

    /// Parse template content to extract extends and blocks
    pub fn parse(&mut self) -> Result<()> {
        self.extends = Self::parse_extends(&self.content)?;
        self.blocks = Self::parse_blocks(&self.content)?;
        Ok(())
    }

    /// Parse {% extends "template_path" %} from template
    fn parse_extends(content: &str) -> Result<Option<String>> {
        // Look for {% extends "..." %} or {% extends '...' %}
        let extends_pattern = r#"\{%\s*extends\s+["']([^"']+)["']\s*%\}"#;
        let re = regex::Regex::new(extends_pattern)
            .map_err(|e| DjangoRustError::TemplateError(format!("Regex error: {}", e)))?;

        if let Some(captures) = re.captures(content) {
            let template_path = captures
                .get(1)
                .ok_or_else(|| {
                    DjangoRustError::TemplateError("Invalid extends syntax".to_string())
                })?
                .as_str()
                .to_string();
            Ok(Some(template_path))
        } else {
            Ok(None)
        }
    }

    /// Parse {% block name %}...{% endblock %} from template
    fn parse_blocks(content: &str) -> Result<HashMap<String, String>> {
        let mut blocks = HashMap::new();

        // Look for {% block name %}...{% endblock %}
        // This regex handles nested content but not nested blocks (which Django doesn't support either)
        let block_pattern = r#"\{%\s*block\s+(\w+)\s*%\}([\s\S]*?)\{%\s*endblock\s*%\}"#;
        let re = regex::Regex::new(block_pattern)
            .map_err(|e| DjangoRustError::TemplateError(format!("Regex error: {}", e)))?;

        for captures in re.captures_iter(content) {
            let block_name = captures
                .get(1)
                .ok_or_else(|| DjangoRustError::TemplateError("Invalid block syntax".to_string()))?
                .as_str()
                .to_string();

            let block_content = captures
                .get(2)
                .ok_or_else(|| DjangoRustError::TemplateError("Invalid block content".to_string()))?
                .as_str()
                .to_string();

            blocks.insert(block_name, block_content);
        }

        Ok(blocks)
    }

    /// Replace blocks in template content with merged blocks
    fn replace_blocks(content: &str, blocks: &HashMap<String, String>) -> String {
        let block_pattern = r#"\{%\s*block\s+(\w+)\s*%\}([\s\S]*?)\{%\s*endblock\s*%\}"#;
        let re = regex::Regex::new(block_pattern).unwrap();

        re.replace_all(content, |captures: &regex::Captures| {
            let block_name = captures.get(1).unwrap().as_str();

            // If we have a replacement for this block, use it
            // Otherwise, keep the original content
            if let Some(replacement) = blocks.get(block_name) {
                replacement.to_string()
            } else {
                // Keep original block content (from base template)
                captures.get(2).unwrap().as_str().to_string()
            }
        })
        .to_string()
    }
}

/// Template loader with caching and directory search
pub struct TemplateLoader {
    /// Template directories to search
    template_dirs: Vec<PathBuf>,
    /// Cached templates (path -> content)
    cache: HashMap<String, String>,
    /// Cache enabled flag
    cache_enabled: bool,
}

impl TemplateLoader {
    /// Create a new template loader
    pub fn new(template_dirs: Vec<PathBuf>) -> Self {
        Self {
            template_dirs,
            cache: HashMap::new(),
            cache_enabled: true,
        }
    }

    /// Disable caching (useful for development)
    pub fn disable_cache(&mut self) {
        self.cache_enabled = false;
        self.cache.clear();
    }

    /// Load a template by path
    pub fn load(&mut self, path: &str) -> Result<String> {
        // Check cache first
        if self.cache_enabled {
            if let Some(cached) = self.cache.get(path) {
                return Ok(cached.clone());
            }
        }

        // Search template directories
        for dir in &self.template_dirs {
            let full_path = dir.join(path);
            if full_path.exists() {
                let content = fs::read_to_string(&full_path).map_err(|e| {
                    DjangoRustError::TemplateError(format!(
                        "Failed to read template {}: {}",
                        path, e
                    ))
                })?;

                // Cache the content
                if self.cache_enabled {
                    self.cache.insert(path.to_string(), content.clone());
                }

                return Ok(content);
            }
        }

        Err(DjangoRustError::TemplateError(format!(
            "Template not found: {}",
            path
        )))
    }

    /// Clear the template cache
    pub fn clear_cache(&mut self) {
        self.cache.clear();
    }
}

/// Merge blocks from child template into base template blocks
pub fn merge_blocks(
    base_blocks: &HashMap<String, String>,
    child_blocks: &HashMap<String, String>,
) -> HashMap<String, String> {
    let mut merged = base_blocks.clone();

    // Child blocks override base blocks
    for (name, content) in child_blocks {
        merged.insert(name.clone(), content.clone());
    }

    merged
}

/// Resolve template inheritance and return merged template content
pub fn resolve_inheritance(template_path: &str, loader: &mut TemplateLoader) -> Result<String> {
    // Load and parse the child template
    let content = loader.load(template_path)?;
    let mut template = Template::new(content);
    template.parse()?;

    // If no extends, return the template as-is
    if template.extends.is_none() {
        return Ok(template.content);
    }

    // Load and parse the base template
    let base_path = template.extends.unwrap();
    let base_content = loader.load(&base_path)?;
    let mut base_template = Template::new(base_content);
    base_template.parse()?;

    // Recursively resolve base template (supports multi-level inheritance)
    let resolved_base = if base_template.extends.is_some() {
        resolve_inheritance(&base_path, loader)?
    } else {
        base_template.content.clone()
    };

    // Re-parse the resolved base to get its blocks
    let mut resolved_base_template = Template::new(resolved_base);
    resolved_base_template.parse()?;

    // Merge blocks: child blocks override base blocks
    let merged_blocks = merge_blocks(&resolved_base_template.blocks, &template.blocks);

    // Replace blocks in the resolved base template
    let final_content = Template::replace_blocks(&resolved_base_template.content, &merged_blocks);

    Ok(final_content)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn test_parse_extends() {
        let content = r#"{% extends "base.html" %}"#;
        let result = Template::parse_extends(content).unwrap();
        assert_eq!(result, Some("base.html".to_string()));

        let content_single = r#"{% extends 'base.html' %}"#;
        let result_single = Template::parse_extends(content_single).unwrap();
        assert_eq!(result_single, Some("base.html".to_string()));

        let no_extends = "<div>No extends</div>";
        let result_none = Template::parse_extends(no_extends).unwrap();
        assert_eq!(result_none, None);
    }

    #[test]
    fn test_parse_blocks() {
        let content = r#"
            {% block title %}My Title{% endblock %}
            <div>
                {% block content %}
                    <p>Default content</p>
                {% endblock %}
            </div>
        "#;

        let blocks = Template::parse_blocks(content).unwrap();
        assert_eq!(blocks.len(), 2);
        assert!(blocks.contains_key("title"));
        assert!(blocks.contains_key("content"));
        assert_eq!(blocks.get("title").unwrap().trim(), "My Title");
    }

    #[test]
    fn test_replace_blocks() {
        let content = r#"
            <html>
                <title>{% block title %}Default{% endblock %}</title>
                <body>{% block content %}Default Content{% endblock %}</body>
            </html>
        "#;

        let mut blocks = HashMap::new();
        blocks.insert("title".to_string(), "New Title".to_string());
        blocks.insert("content".to_string(), "<p>New Content</p>".to_string());

        let result = Template::replace_blocks(content, &blocks);
        assert!(result.contains("New Title"));
        assert!(result.contains("<p>New Content</p>"));
        assert!(!result.contains("Default"));
    }

    #[test]
    fn test_merge_blocks() {
        let mut base_blocks = HashMap::new();
        base_blocks.insert("title".to_string(), "Base Title".to_string());
        base_blocks.insert("content".to_string(), "Base Content".to_string());

        let mut child_blocks = HashMap::new();
        child_blocks.insert("content".to_string(), "Child Content".to_string());

        let merged = merge_blocks(&base_blocks, &child_blocks);

        assert_eq!(merged.get("title").unwrap(), "Base Title");
        assert_eq!(merged.get("content").unwrap(), "Child Content");
    }

    #[test]
    fn test_template_loader() -> Result<()> {
        // Create a temporary directory for test templates
        let temp_dir = TempDir::new().unwrap();
        let template_path = temp_dir.path().join("test.html");
        fs::write(&template_path, "<div>Test Template</div>").unwrap();

        // Create loader
        let mut loader = TemplateLoader::new(vec![temp_dir.path().to_path_buf()]);

        // Load template
        let content = loader.load("test.html")?;
        assert_eq!(content, "<div>Test Template</div>");

        // Load again (should hit cache)
        let content2 = loader.load("test.html")?;
        assert_eq!(content2, "<div>Test Template</div>");

        Ok(())
    }

    #[test]
    fn test_resolve_inheritance() -> Result<()> {
        // Create temporary directory for test templates
        let temp_dir = TempDir::new().unwrap();

        // Create base template
        let base_content = r#"
            <html>
                <head>
                    <title>{% block title %}Default Title{% endblock %}</title>
                </head>
                <body>
                    {% block content %}
                        <p>Default content</p>
                    {% endblock %}
                </body>
            </html>
        "#;
        fs::write(temp_dir.path().join("base.html"), base_content).unwrap();

        // Create child template
        let child_content = r#"
            {% extends "base.html" %}

            {% block title %}Child Title{% endblock %}

            {% block content %}
                <h1>Child Content</h1>
                <p>This is the child template content</p>
            {% endblock %}
        "#;
        fs::write(temp_dir.path().join("child.html"), child_content).unwrap();

        // Resolve inheritance
        let mut loader = TemplateLoader::new(vec![temp_dir.path().to_path_buf()]);
        let resolved = resolve_inheritance("child.html", &mut loader)?;

        // Verify merged template
        assert!(resolved.contains("Child Title"));
        assert!(resolved.contains("<h1>Child Content</h1>"));
        assert!(!resolved.contains("Default Title"));
        assert!(!resolved.contains("Default content"));
        assert!(!resolved.contains("{% extends"));
        assert!(!resolved.contains("{% block"));

        Ok(())
    }
}
