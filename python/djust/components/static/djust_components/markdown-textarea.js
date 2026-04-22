/**
 * Markdown Textarea — client-side preview rendering.
 *
 * When the component enters preview mode (dj-md-textarea--preview class),
 * renders the raw markdown content in the preview pane.  Uses a simple
 * built-in markdown-to-HTML converter for basic formatting (bold, italic,
 * headings, code, links, lists).  No external dependencies.
 * Uses MutationObserver for LiveView compatibility.
 */
(function () {
  "use strict";

  /**
   * Minimal markdown-to-HTML converter.
   * Handles: headings, bold, italic, inline code, code blocks,
   * links, unordered lists, and paragraphs.
   */
  function markdownToHtml(md) {
    if (!md) return "";

    var lines = md.split("\n");
    var html = [];
    var inCodeBlock = false;
    var inList = false;

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      // Fenced code blocks
      if (line.trim().indexOf("```") === 0) {
        if (inCodeBlock) {
          html.push("</code></pre>");
          inCodeBlock = false;
        } else {
          if (inList) {
            html.push("</ul>");
            inList = false;
          }
          html.push("<pre><code>");
          inCodeBlock = true;
        }
        continue;
      }
      if (inCodeBlock) {
        html.push(escapeHtml(line));
        html.push("\n");
        continue;
      }

      // Headings
      var headingMatch = line.match(/^(#{1,6})\s+(.*)/);
      if (headingMatch) {
        if (inList) {
          html.push("</ul>");
          inList = false;
        }
        var level = headingMatch[1].length;
        html.push(
          "<h" + level + ">" + inlineFormat(headingMatch[2]) + "</h" + level + ">"
        );
        continue;
      }

      // Unordered list items
      if (/^\s*[-*+]\s+/.test(line)) {
        if (!inList) {
          html.push("<ul>");
          inList = true;
        }
        html.push("<li>" + inlineFormat(line.replace(/^\s*[-*+]\s+/, "")) + "</li>");
        continue;
      }

      // Close list if we hit a non-list line
      if (inList) {
        html.push("</ul>");
        inList = false;
      }

      // Blank line
      if (line.trim() === "") {
        continue;
      }

      // Paragraph
      html.push("<p>" + inlineFormat(line) + "</p>");
    }

    if (inList) html.push("</ul>");
    if (inCodeBlock) html.push("</code></pre>");

    return html.join("");
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function inlineFormat(text) {
    // Escape HTML FIRST so user input can't inject tags via markdown text.
    // Markdown syntax chars (*, _, `, [, ], (, )) are not in the escape set,
    // so the regex-based transforms below still match as intended.
    text = escapeHtml(text);
    // Inline code
    text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic
    text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // Links — validate URL scheme to reject javascript:/data:/vbscript: etc.
    text = text.replace(
      /\[([^\]]+)\]\(([^)]+)\)/g,
      function (_match, label, url) {
        var safeUrl = _sanitizeUrl(url);
        return '<a href="' + safeUrl + '">' + label + "</a>";
      }
    );
    return text;
  }

  function _sanitizeUrl(url) {
    // Reject dangerous URL schemes (case-insensitive, leading whitespace
    // tolerated). Relative URLs, http(s), mailto, anchors etc. pass through.
    var trimmed = String(url).trim().toLowerCase();
    if (
      trimmed.indexOf("javascript:") === 0 ||
      trimmed.indexOf("data:") === 0 ||
      trimmed.indexOf("vbscript:") === 0
    ) {
      return "#";
    }
    return url;
  }

  function initMarkdownTextarea(el) {
    if (el._djMdTextareaInit) return;
    el._djMdTextareaInit = true;

    renderPreview(el);

    // Watch for class changes (preview toggle via LiveView)
    new MutationObserver(function () {
      renderPreview(el);
    }).observe(el, { attributes: true, attributeFilter: ["class"] });
  }

  function renderPreview(el) {
    if (!el.classList.contains("dj-md-textarea--preview")) return;

    var previewEl = el.querySelector(".dj-md-textarea__preview");
    if (!previewEl) return;

    var raw = previewEl.getAttribute("data-raw") || "";
    previewEl.innerHTML = markdownToHtml(raw);
  }

  function initAll() {
    document
      .querySelectorAll('[dj-hook="MarkdownTextarea"]')
      .forEach(initMarkdownTextarea);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  // Re-initialize after LiveView patches
  new MutationObserver(function (mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType === 1) {
          if (
            node.getAttribute &&
            node.getAttribute("dj-hook") === "MarkdownTextarea"
          ) {
            initMarkdownTextarea(node);
          }
          if (node.querySelectorAll) {
            node
              .querySelectorAll('[dj-hook="MarkdownTextarea"]')
              .forEach(initMarkdownTextarea);
          }
        }
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
