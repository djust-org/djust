# djust AI Reference Docs

Focused, standalone reference sections for AI code generation. Each file is self-contained and under 500 tokens.

Start with [Application engineering conventions](conventions.md). It explains
ownership, native-first decisions, verification and how application agents find
these references. The topic sections below are concise API examples.

## Sections

- [lifecycle.md](lifecycle.md) -- LiveView lifecycle: mount, refresh, render
- [events.md](events.md) -- Event handlers, decorators, parameter conventions
- [jit.md](jit.md) -- JIT serialization: private/public variable pattern
- [templates.md](templates.md) -- Template directives (dj-click, dj-model, etc.)
- [security.md](security.md) -- Authorization and input validation patterns
- [forms.md](forms.md) -- FormMixin and form handling

## Usage

Load individual sections into LLM context as needed, or use `../llms-full.txt` for a single-file reference.
