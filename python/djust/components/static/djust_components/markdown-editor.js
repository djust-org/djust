/* MarkdownEditor: progressive textarea controls using djust's native hook lifecycle.
 * The textarea remains the form value. Input events use FormMixin's existing
 * transport; preview HTML is rendered and sanitized by the server.
 */
(function () {
  "use strict";
  const actions = [
    ["bold", "Bold", "B", "**", "**"],
    ["italic", "Italic", "I", "_", "_"],
    ["heading", "Heading", "H2", "## ", "", true],
    ["quote", "Quote", "❞", "> ", "", true],
    ["list", "Bullet list", "• List", "- ", "", true],
    ["ordered", "Numbered list", "1. List", "1. ", "", true],
    ["task", "Task list", "☑", "- [ ] ", "", true],
    ["code", "Inline code", "</>", "`", "`"],
    ["block", "Code block", "```", "```\n", "\n```"],
    ["link", "Link", "Link", "[", "](https://)"],
  ];

  function replacement(action, value, start, end) {
    const item = actions.find((row) => row[0] === action);
    if (!item) return null;
    let [, , , before, after, lines] = item;
    if (lines) {
      start = start === 0 ? 0 : value.lastIndexOf("\n", start - 1) + 1;
      if (end > start && value[end - 1] === "\n") end -= 1;
      const next = value.indexOf("\n", end);
      end = next < 0 ? value.length : next;
      const selected = value.slice(start, end);
      const text = selected
        .split("\n")
        .map(
          (line, index) =>
            (action === "ordered" ? `${index + 1}. ` : before) + line,
        )
        .join("\n");
      return { start, end, text, from: start, to: start + text.length };
    }
    const selected = value.slice(start, end);
    if (action === "block") {
      if (start && value[start - 1] !== "\n") before = "\n" + before;
      if (end < value.length && value[end] !== "\n") after += "\n";
    }
    return {
      start,
      end,
      text: before + selected + after,
      from: start + before.length,
      to: start + before.length + selected.length,
    };
  }

  window.djust = window.djust || {};
  window.djust.hooks = window.djust.hooks || {};
  window.djust.hooks.MarkdownEditor = {
    mounted() {
      // Controls live only inside server-declared markup. Inserting siblings
      // next to a native field changes the server's VDOM index paths.
      this.textarea =
        this.el.querySelector("textarea") ||
        this.el
          .closest("form")
          ?.elements.namedItem(this.el.dataset.field || "body");
      this.ui =
        this.el.querySelector("[data-markdown-ui]") ||
        (this.el.hasAttribute("dj-update") ? this.el : null);
      if (!this.textarea || !this.ui || this.ui === this.textarea) {
        this.textarea = null;
        return;
      }
      this.ui.replaceChildren();
      this.mode = "markdown";
      this.originalClass = this.textarea.classList.contains(
        "dj-markdown-source-hidden",
      );
      this.originalTabIndex = this.textarea.getAttribute("tabindex");
      this.originalAriaHidden = this.textarea.getAttribute("aria-hidden");
      this.toolbar = document.createElement("div");
      this.toolbar.className = "dj-markdown-toolbar";
      this.toolbar.setAttribute("role", "group");
      this.toolbar.setAttribute("aria-label", "Editor controls");
      this.ui.appendChild(this.toolbar);
      this.status = document.createElement("p");
      this.status.className = "dj-markdown-status";
      this.status.setAttribute("role", "status");
      this.ui.appendChild(this.status);
      this.surface = document.createElement("div");
      this.surface.className = "dj-markdown-visual";
      this.surface.hidden = true;
      this.ui.appendChild(this.surface);
      this.surface.addEventListener("keydown", (event) => {
        if (
          !event.isComposing &&
          !event.altKey &&
          (event.metaKey || event.ctrlKey) &&
          event.key.toLowerCase() === "k"
        ) {
          event.preventDefault();
          this.editLink();
        }
      });
      const button = (label, text, callback) => {
        const item = document.createElement("button");
        item.type = "button";
        item.textContent = text;
        item.title = label;
        item.setAttribute("aria-label", label);
        item.addEventListener("mousedown", (event) => event.preventDefault());
        item.addEventListener("click", callback);
        this.toolbar.appendChild(item);
        return item;
      };
      if (window.djust.createMarkdownVisual) {
        this.visualButton = button("Visual mode", "Visual", () =>
          this.setMode("visual", true),
        );
        this.markdownButton = button("Markdown mode", "Markdown", () =>
          this.setMode("markdown", true),
        );
      }
      this.actionButtons = [];
      for (const [action, label, text] of actions) {
        if (
          this.el.dataset.actions &&
          !this.el.dataset.actions.split(" ").includes(action)
        )
          continue;
        const item = button(label, text, () =>
          action === "link" ? this.editLink() : this.format(action),
        );
        item.dataset.markdownAction = action;
        this.actionButtons.push(item);
      }
      if (this.visualButton) {
        for (const action of ["undo", "redo"]) {
          if (
            this.el.dataset.actions &&
            !this.el.dataset.actions.split(" ").includes(action)
          )
            continue;
          const item = button(
            action === "undo" ? "Undo" : "Redo",
            action === "undo" ? "↶" : "↷",
            () => this.format(action),
          );
          item.dataset.markdownAction = action;
          this.actionButtons.push(item);
        }
      }
      this.onKey = (event) => {
        if (
          event.isComposing ||
          event.altKey ||
          !(event.metaKey || event.ctrlKey)
        )
          return;
        const action = { b: "bold", i: "italic", k: "link" }[
          event.key.toLowerCase()
        ];
        if (action) {
          event.preventDefault();
          action === "link" ? this.editLink() : this.format(action);
        }
      };
      this.onInvalid = () => this.setMode("markdown", true);
      this.textarea.addEventListener("keydown", this.onKey);
      this.textarea.addEventListener("invalid", this.onInvalid);
      this.onReset = () =>
        queueMicrotask(() => {
          this.lastValue = this.textarea.value;
          if (this.visual) this.visual.load(this.lastValue);
        });
      this.form = this.textarea.form;
      this.form?.addEventListener("reset", this.onReset);
      this.setMode(
        this.textarea.dataset.markdownEditor ||
          this.el.dataset.mode ||
          "markdown",
      );
      this.updated();
    },
    setMode(mode, focus = false) {
      if (!this.textarea) return;
      this.status.textContent = "";
      if (mode === "visual" && window.djust.createMarkdownVisual) {
        if (!this.visual) {
          const label =
            this.textarea.labels?.[0]?.textContent.trim() || "Post body";
          this.visual = window.djust.createMarkdownVisual(
            this.surface,
            this.textarea.value,
            (value) => {
              this.lastValue = value;
              this.textarea.value = value;
              this.textarea.dispatchEvent(
                new Event("input", { bubbles: true }),
              );
              // Apps can declaratively bind either input or change.
              if (!this.textarea.hasAttribute("dj-input"))
                this.textarea.dispatchEvent(
                  new Event("change", { bubbles: true }),
                );
            },
            label + " (visual editor)",
            () => this.updateButtons(),
          );
        }
        if (this.mode !== "visual" && this.lastValue !== this.textarea.value) {
          const reason = this.visual.load(this.textarea.value);
          if (reason) {
            this.status.textContent = reason;
            mode = "markdown";
          } else this.lastValue = this.textarea.value;
        }
      } else mode = "markdown";
      this.mode = mode;
      this.el.classList.toggle("dj-markdown-is-visual", mode === "visual");
      this.surface.hidden = mode !== "visual";
      this.textarea.classList.toggle(
        "dj-markdown-source-hidden",
        mode === "visual",
      );
      this.textarea.setAttribute("aria-hidden", String(mode === "visual"));
      if (mode === "visual") this.textarea.setAttribute("tabindex", "-1");
      else if (this.originalTabIndex === null)
        this.textarea.removeAttribute("tabindex");
      else this.textarea.setAttribute("tabindex", this.originalTabIndex);
      this.visualButton?.setAttribute(
        "aria-pressed",
        String(mode === "visual"),
      );
      this.markdownButton?.setAttribute(
        "aria-pressed",
        String(mode === "markdown"),
      );
      this.updateButtons();
      if (focus)
        mode === "visual"
          ? this.visual.focus()
          : this.textarea.focus({ preventScroll: true });
    },
    updateButtons() {
      const disabled = this.textarea.disabled || this.textarea.readOnly;
      this.visual?.editable(!disabled);
      for (const button of this.actionButtons || []) {
        const action = button.dataset.markdownAction;
        button.disabled =
          disabled ||
          (this.mode !== "visual" && ["undo", "redo"].includes(action));
        if (this.mode === "visual" && this.visual) {
          const names = {
            bold: "bold",
            italic: "italic",
            heading: "heading",
            quote: "blockquote",
            list: "bulletList",
            ordered: "orderedList",
            task: "taskList",
            code: "code",
            block: "codeBlock",
            link: "link",
          };
          if (names[action])
            button.setAttribute(
              "aria-pressed",
              String(this.visual.editor.isActive(names[action])),
            );
          if (action === "undo")
            button.disabled = disabled || !this.visual.editor.can().undo();
          if (action === "redo")
            button.disabled = disabled || !this.visual.editor.can().redo();
        } else button.removeAttribute("aria-pressed");
      }
    },
    updated() {
      if (!this.textarea) return;
      const current =
        this.el.querySelector("textarea") ||
        this.el
          .closest("form")
          ?.elements.namedItem(this.el.dataset.field || "body");
      if (current && current !== this.textarea) {
        this.textarea.removeEventListener("keydown", this.onKey);
        this.textarea.removeEventListener("invalid", this.onInvalid);
        this.textarea = current;
        current.addEventListener("keydown", this.onKey);
        current.addEventListener("invalid", this.onInvalid);
      }
      if (this.mode === "visual") {
        // Match native focused-textarea semantics while typing. The
        // visual editor owns the active draft; server state wins on blur.
        if (this.surface.contains(document.activeElement))
          this.textarea.value = this.lastValue;
        else if (this.textarea.value !== this.lastValue) {
          const reason = this.visual.load(this.textarea.value);
          if (reason) {
            this.setMode("markdown");
            this.status.textContent = reason;
          }
          this.lastValue = this.textarea.value;
        }
      }
      this.textarea.classList.toggle(
        "dj-markdown-source-hidden",
        this.mode === "visual",
      );
      this.updateButtons();
      this.el.classList.toggle("dj-markdown-is-visual", this.mode === "visual");
      this.textarea.setAttribute("aria-hidden", String(this.mode === "visual"));
      if (this.mode === "visual") this.textarea.setAttribute("tabindex", "-1");
    },
    editLink() {
      if (this.textarea.disabled || this.textarea.readOnly) return;
      if (this.mode !== "visual") {
        this.format("link");
        return;
      }
      if (this.linkPanel) {
        this.linkPanel.remove();
        this.linkPanel = null;
        return;
      }
      const panel = document.createElement("div");
      panel.className = "dj-markdown-link";
      const label = document.createElement("label");
      label.textContent = "Link URL";
      const input = document.createElement("input");
      input.type = "url";
      input.placeholder = "https://example.com";
      input.value = this.visual.editor.getAttributes("link").href || "";
      label.appendChild(input);
      panel.appendChild(label);
      for (const [text, apply] of [
        ["Apply link", true],
        ["Cancel", false],
      ]) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = text;
        button.addEventListener("click", () => {
          if (apply) {
            const href = input.value.trim();
            if (href && !/^(https?:|mailto:|\/|#)/i.test(href)) {
              input.setCustomValidity(
                "Use an HTTP, HTTPS, mailto or relative URL.",
              );
              input.reportValidity();
              return;
            }
            this.visual.format("link", href);
          }
          panel.remove();
          this.linkPanel = null;
          this.visual.focus();
        });
        panel.appendChild(button);
      }
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          panel.querySelector("button").click();
        }
      });
      this.ui.insertBefore(panel, this.surface);
      this.linkPanel = panel;
      input.focus();
    },
    format(action) {
      const field = this.textarea;
      if (field.disabled || field.readOnly) return;
      if (this.mode === "visual") {
        this.visual.format(action);
        return;
      }
      const edit = replacement(
        action,
        field.value,
        field.selectionStart,
        field.selectionEnd,
      );
      if (!edit) return;
      field.focus();
      field.setSelectionRange(edit.start, edit.end);
      const previous = field.value;
      let inserted = false;
      try {
        inserted = document.execCommand("insertText", false, edit.text);
      } catch (_) {
        /* standards fallback */
      }
      if (!inserted && field.value === previous) {
        field.setRangeText(edit.text, edit.start, edit.end, "end");
        field.dispatchEvent(new Event("input", { bubbles: true }));
      }
      field.setSelectionRange(edit.from, edit.to);
    },
    destroyed() {
      this.textarea?.removeEventListener("keydown", this.onKey);
      this.textarea?.removeEventListener("invalid", this.onInvalid);
      this.form?.removeEventListener("reset", this.onReset);
      this.visual?.destroy();
      this.toolbar?.remove();
      this.status?.remove();
      this.surface?.remove();
      this.linkPanel?.remove();
      if (!this.originalClass)
        this.textarea?.classList.remove("dj-markdown-source-hidden");
      for (const [name, value] of [
        ["tabindex", this.originalTabIndex],
        ["aria-hidden", this.originalAriaHidden],
      ]) {
        if (value == null) this.textarea?.removeAttribute(name);
        else this.textarea?.setAttribute(name, value);
      }
    },
  };
})();
