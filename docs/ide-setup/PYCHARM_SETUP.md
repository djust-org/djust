# PyCharm/IntelliJ Setup Guide for djust

This guide provides optimized PyCharm/IntelliJ IDEA configuration for developing djust, a Django + Rust hybrid framework.

## ✅ Configuration Already Applied

The following optimizations have been applied to `.idea/djust.iml`:

### Source Folders
- ✅ `python/` - Main Python source code
- ✅ `tests/` - Test source (marked as test root)
- ✅ `examples/demo_project/` - Demo Django project

### Excluded Folders (Performance Boost)
- ✅ `.venv/` - Virtual environment
- ✅ `target/` - Rust build artifacts (can be 100s of MB)
- ✅ `node_modules/` - JavaScript dependencies
- ✅ `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/` - Python caches
- ✅ `__pycache__/` - Bytecode cache
- ✅ `coverage/` - Coverage reports
- ✅ `.serena/`, `.claude/` - MCP server caches
- ✅ `.git/` - Git internals

### Language Settings
- ✅ Django templates recognized in `examples/demo_project/demo_app/templates/`
- ✅ JavaScript dialect for `python/djust/static/`
- ✅ Google-style docstrings configured

## 🔌 Essential Plugins

### 1. **Rust** (MUST HAVE)
**Plugin ID**: `org.rust.lang`

Since djust is a Rust + Python hybrid, this is essential for:
- Rust syntax highlighting
- Cargo integration
- Code completion for `.rs` files
- Inline error checking
- Quick fixes and refactoring

**Installation**:
```
Settings → Plugins → Marketplace → Search "Rust" → Install
```

**Post-install**:
- Restart PyCharm
- Open `Cargo.toml` - should show Rust project detected
- Right-click on `crates/` → Mark Directory As → Sources Root (if not auto-detected)

### 2. **TOML** (Strongly Recommended)
**Plugin ID**: `org.toml.lang`

For editing `Cargo.toml`, `pyproject.toml`:
- Syntax highlighting
- Structure view
- Validation

**Installation**:
```
Settings → Plugins → Marketplace → Search "TOML" → Install
```

### 3. **Django Support** (PyCharm Professional Only)
**Built-in for PyCharm Professional**

If you have PyCharm Professional, enable Django support:

**Configuration**:
```
Settings → Languages & Frameworks → Django
  ☑ Enable Django Support
  Django project root: /path/to/djust/examples/demo_project
  Settings: demo_project/settings.py
  Manage script: manage.py
```

**Benefits**:
- Template debugging
- URL resolution
- Model autocomplete
- Management command completion

### 4. **.ignore** (Recommended)
**Plugin ID**: `mobi.hsz.idea.gitignore`

Helps manage `.gitignore`, `.dockerignore`, etc.:
- Syntax highlighting
- Duplicate detection
- Template generation

### 5. **Markdown Navigator Enhanced** (Optional)
**Plugin ID**: `com.vladsch.idea.multimarkdown`

For working with the extensive documentation:
- Preview with GitHub flavoring
- Table of contents
- Diagram support
- Better editing experience

### 6. **Rainbow Brackets** (Optional but Nice)
**Plugin ID**: `izhangzhihao.rainbow.brackets`

Color-codes matching brackets/parentheses:
- Especially helpful for deeply nested code
- Works with Python, Rust, JavaScript

### 7. **GitToolBox** (Recommended)
**Plugin ID**: `zielu.gittoolbox`

Enhanced Git integration:
- Inline blame
- Status display
- Auto-fetch
- Ahead/behind tracking

## ⚙️ PyCharm Settings Optimizations

### 1. Memory Settings (Performance)

**For large projects like djust**, increase PyCharm memory:

```
Help → Change Memory Settings
  Heap Size: 4096 MB (or 8192 MB if you have 16GB+ RAM)
```

### 2. Code Inspection Settings

**Enable Rust-specific inspections**:
```
Settings → Editor → Inspections
  ☑ Rust
    ☑ Borrow checker
    ☑ Name resolution
    ☑ Type checking
```

**Optimize Python inspections**:
```
Settings → Editor → Inspections → Python
  ☑ PEP 8 coding style violation
  ☑ Type checker (if using mypy)
  ☑ Unresolved references
  ☐ Disable noisy inspections (e.g., "Function name doesn't conform to naming conventions" if you prefer)
```

### 3. File Type Associations

**Add custom file types**:
```
Settings → Editor → File Types
  HTML Files: Add pattern *.html.jinja2 (if used)
  Python: Ensure *.pyi (type stubs) are recognized
```

### 4. External Tools Configuration

#### Cargo Commands

**Add Cargo test**:
```
Settings → Tools → External Tools → Add
  Name: Cargo Test
  Program: cargo
  Arguments: test
  Working directory: $ProjectFileDir$
```

**Add Cargo Clippy**:
```
Settings → Tools → External Tools → Add
  Name: Cargo Clippy
  Program: cargo
  Arguments: clippy --all-targets --all-features
  Working directory: $ProjectFileDir$
```

#### Make Commands

**Add Make targets**:
```
Settings → Tools → External Tools → Add
  Name: Make Build
  Program: make
  Arguments: build
  Working directory: $ProjectFileDir$
```

### 5. Django Run Configuration

**Create Django server run configuration**:
```
Run → Edit Configurations → Add New → Django Server
  Name: Demo Project
  Host: 127.0.0.1
  Port: 8002
  Working directory: examples/demo_project
  Environment variables: (none needed)
  Python interpreter: Python 3.11 virtualenv at ~/Dropbox/online_projects/ai/djust/.venv
```

**Benefits**:
- One-click server start/stop
- Integrated console
- Debugger support
- Automatic reloading

### 6. Test Runner Configuration

**Configure pytest**:
```
Settings → Tools → Python Integrated Tools
  Default test runner: pytest
  Pytest additional arguments: --cov=python/djust --cov-report=html
```

### 7. Code Style Settings

**Import Django/Rust code styles**:
```
Settings → Editor → Code Style
  Python:
    ☑ Use PEP 8 style guide
    Tab size: 4
    Indent: 4
    Continuation indent: 4

  Rust:
    ☑ Use rustfmt
    Tab size: 4
    Indent: 4
```

### 8. Version Control Settings

**Optimize Git performance**:
```
Settings → Version Control → Git
  ☑ Use credential helper
  ☑ Auto-update if push rejected

Settings → Version Control → Commit
  ☐ Use non-modal commit interface (unchecked for better UX on large projects)
  ☑ Analyze code
  ☑ Check TODO
  ☑ Perform code analysis
  ☑ Scan with Rust Clippy (if available)
```

## 🚀 Keyboard Shortcuts for This Project

### Navigation
- **Cmd+Shift+O** (Mac) / **Ctrl+Shift+N** (Win/Linux) - Find file
- **Cmd+O** / **Ctrl+N** - Find class
- **Cmd+Shift+F** / **Ctrl+Shift+F** - Find in path (great for searching across Python + Rust)

### Rust-Specific
- **Cmd+B** / **Ctrl+B** - Go to definition (works in Rust!)
- **Cmd+Shift+T** / **Ctrl+Shift+T** - Go to test
- **Cmd+F12** / **Ctrl+F12** - File structure (for Rust modules)

### Python/Django
- **Cmd+Shift+A** / **Ctrl+Shift+A** - Find action → "manage.py"
- **Alt+Enter** - Quick fix (works for both Python and Rust)

### Multi-Language
- **Cmd+E** / **Ctrl+E** - Recent files (fast switching between Python and Rust files)

## 🎯 Workflow Optimizations

### 1. Split Editor for Hybrid Development

When working on Python + Rust integration:

```
Right-click editor tab → Split Right
  Left: Python file (e.g., live_view.py)
  Right: Rust file (e.g., crates/djust_vdom/src/lib.rs)
```

### 2. Tool Window Layout

**Optimal layout for djust development**:
```
Left: Project view
Bottom: Terminal (for cargo commands)
Right: Rust Cargo tool window
Floating: Run (for Django server)
```

**Save layout**:
```
Window → Store Current Layout as Default
```

### 3. File Templates

**Create custom file templates**:

**LiveView template**:
```
Settings → Editor → File and Code Templates → Python Script
```

```python
from djust import LiveView

class ${NAME}(LiveView):
    """
    ${DESCRIPTION}
    """
    template_name = '${TEMPLATE_PATH}'

    def mount(self, request):
        """Initialize view state"""
        pass

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context
```

### 4. Live Templates (Code Snippets)

**Add custom live templates**:
```
Settings → Editor → Live Templates → Python → Add
```

**Example: `@cache` decorator**:
```
Abbreviation: dcache
Template text:
@cache(ttl=$TTL$, key_params=[$PARAMS$])
def $METHOD$(self, $ARGS$):
    """$DESCRIPTION$"""
    $END$
```

**Example: LiveView test**:
```
Abbreviation: testlv
Template text:
def test_$NAME$(self):
    """Test $DESCRIPTION$"""
    view = $VIEW_CLASS$()
    view.mount(self.request)
    $END$
```

## 🔍 Debugging Setup

### 1. Remote Python Debugging

**For debugging Django server**:
```
Run → Edit Configurations → Add → Python Debug Server
  Name: djust Debug Server
  Port: 5678
  Path mappings: (none needed for local)
```

### 2. Rust Debugging (with lldb)

**Prerequisites**:
- Install lldb: `brew install lldb` (Mac)
- Configure in PyCharm/IntelliJ

**Create Rust debug configuration**:
```
Run → Edit Configurations → Add → Cargo Command
  Name: Test VDOM (Debug)
  Command: test
  Cargo project: djust (workspace)
  Additional arguments: -p djust_vdom -- --nocapture
```

## 📊 Performance Monitoring

### Indexing Performance

**Monitor indexing**:
```
Help → Diagnostic Tools → Activity Monitor
```

**If indexing is slow**, try:
```
File → Invalidate Caches → Invalidate and Restart
```

### Exclude Patterns

**Add more exclusions if needed**:
```
Settings → Editor → File Types → Ignore files and folders
  Add: *.pyc;*.pyo;*.so;*.dylib;
```

## 🛠️ Project-Specific Tools

### 1. Database Tools (PyCharm Professional)

**Connect to demo project database**:
```
View → Tool Windows → Database
  + → Data Source → SQLite
  File: examples/demo_project/db.sqlite3
```

**Benefits**:
- Browse tables
- Run SQL queries
- Inspect migrations

### 2. HTTP Client

**Test LiveView endpoints**:

Create `scratch/http-tests.http`:
```http
### Test cache decorator
POST http://localhost:8002/demos/cache/
Content-Type: application/json

{
  "event": "search",
  "query": "laptop"
}

### Test WebSocket (using Python script)
# Use tests in examples/demo_project/
```

## 🎨 UI/UX Customizations

### Theme Recommendation

**For Rust + Python hybrid**:
- Light theme: IntelliJ Light
- Dark theme: Darcula (high contrast for Rust types)

### Font Recommendation

**For mixed Rust/Python code**:
```
Settings → Editor → Font
  Font: JetBrains Mono (best for coding)
  Size: 13-14
  Line spacing: 1.2
  ☑ Enable ligatures (for Rust operators like -> and =>)
```

## 📝 Additional Tips

### 1. Rust Macro Expansion

When working with PyO3 macros:
```
Right-click in Rust file → Rust → Expand Macro Recursively
```

### 2. Django Template Language

**Enable template debugging**:
```
Settings → Build, Execution, Deployment → Template Languages
  Template language: Django
  Directories: examples/demo_project/demo_app/templates
```

### 3. Quick Documentation

**View docstrings/docs**:
- **Cmd+Q** (Mac) / **Ctrl+Q** (Win/Linux) - Quick documentation
- Works for both Python docstrings and Rust `///` docs

### 4. Find Usages Across Languages

**Find where Python calls Rust**:
- Right-click on Rust function
- Find Usages (Cmd+F7 / Alt+F7)
- Shows both Rust and Python usages

## 🔄 Keep Updated

### Plugin Updates
```
Settings → Plugins → Updates
  ☑ Check for plugin updates
```

### Rust Plugin Updates
The Rust plugin is actively developed. Update regularly for:
- New language features
- Performance improvements
- Bug fixes

## 📚 Resources

- **Rust Plugin**: https://plugins.jetbrains.com/plugin/8182-rust
- **Django Support**: https://www.jetbrains.com/help/pycharm/django-support7.html
- **PyCharm Tips**: https://www.jetbrains.com/pycharm/guide/

## 🚨 Troubleshooting

### Rust plugin not working?
1. Ensure Rust toolchain installed: `rustc --version`
2. Restart PyCharm
3. File → Invalidate Caches → Restart

### Django server won't start?
1. Check Python interpreter: Settings → Project → Python Interpreter
2. Ensure dependencies installed: `make install`
3. Check port 8002 not in use: `lsof -i :8002`

### Templates not recognized?
1. Settings → Languages & Frameworks → Django
2. Enable Django support
3. Point to correct template directories

---

**Ready to code!** With these optimizations, PyCharm should be blazing fast for djust development. 🚀
