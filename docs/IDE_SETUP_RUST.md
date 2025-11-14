# Rust + Python IDE Setup

## TL;DR - Best Setup for djust

You have two options for working with this Rust + Python hybrid project:

### Option 1: IntelliJ IDEA (Recommended) ⭐
Use **IntelliJ IDEA Ultimate** for everything - it has the best Rust support + full Python/Django support.

### Option 2: Dual IDE Setup
- **IntelliJ IDEA** - For Rust code (`crates/`)
- **PyCharm** - For Python code (`python/`, `examples/`)

## Option 1: IntelliJ IDEA Ultimate (Recommended)

IntelliJ IDEA Ultimate has **both** Python and Rust support built-in.

### 1. Open Project in IntelliJ IDEA

```bash
# Open the project
open -a "IntelliJ IDEA" /Users/tip/Dropbox/online_projects/ai/djust
```

### 2. Install Rust Plugin

```
IntelliJ IDEA → Settings (Cmd+,) → Plugins → Marketplace
Search: "Rust"
Plugin: "Rust" by JetBrains
Click: Install → Restart
```

**Note**: The Rust plugin is well-supported in IntelliJ IDEA and actively maintained.

### 3. Enable Python Plugin

```
Settings → Plugins → Installed
Ensure "Python" plugin is enabled (should be by default in Ultimate)
```

### 4. Configure Python Interpreter

```
Settings → Project: djust → Python Interpreter
Click: Add Interpreter → Add Local Interpreter
Select: Virtualenv Environment
Location: /Users/tip/Dropbox/online_projects/ai/djust/.venv
Python version: 3.11
Click: OK
```

### 5. Enable Django Support

```
Settings → Languages & Frameworks → Django
☑ Enable Django Support
Django project root: /Users/tip/Dropbox/online_projects/ai/djust/examples/demo_project
Settings: demo_project/settings.py
Manage script: manage.py
```

### 6. Verify Rust + Python Both Work

**Test Rust**:
1. Open `crates/djust_vdom/src/lib.rs`
2. Should see: Syntax highlighting, documentation on hover
3. Right-click → Run Cargo command → Test

**Test Python**:
1. Open `python/djust/live_view.py`
2. Should see: Autocomplete, type hints
3. Run → Edit Configurations → Add Django Server

### Benefits of IntelliJ IDEA Ultimate

✅ **Best Rust support** - Full IDE features for Rust
✅ **Full Python support** - Same as PyCharm Professional
✅ **Django support** - Template debugging, model autocomplete
✅ **Unified workflow** - No switching between IDEs
✅ **Better project integration** - Single project view for hybrid codebase
✅ **Cargo + Make integration** - Built-in

## Option 2: Dual IDE Setup (PyCharm + IntelliJ IDEA)

If you prefer PyCharm for Python, use this setup:

### When to Use Each IDE

**PyCharm**:
- Python development (`python/djust/`)
- Django demo project (`examples/demo_project/`)
- Python tests (`tests/unit/`)
- Documentation (`.md` files)

**IntelliJ IDEA**:
- Rust development (`crates/`)
- Cargo operations (build, test, clippy)
- Rust tests (`crates/*/tests/`)

### Setup IntelliJ IDEA for Rust

1. **Open as Rust Project**:
   ```bash
   open -a "IntelliJ IDEA" /Users/tip/Dropbox/online_projects/ai/djust
   ```

2. **Install Rust Plugin**:
   ```
   Settings → Plugins → Marketplace → "Rust" → Install → Restart
   ```

3. **Verify Cargo Project**:
   - Should auto-detect `Cargo.toml`
   - Tool window: View → Tool Windows → Cargo
   - See all crates in workspace

4. **Configure Rust Toolchain**:
   ```
   Settings → Languages & Frameworks → Rust
   Toolchain location: (should auto-detect from ~/.cargo)
   Standard library: (should auto-detect)
   ```

### Setup PyCharm for Python

Use the existing PyCharm configuration (already set up from previous steps).

### Workflow

**Editing Rust code**:
1. Open IntelliJ IDEA
2. Edit files in `crates/`
3. Run cargo commands: `cargo build`, `cargo test`
4. Commit changes

**Editing Python code**:
1. Open PyCharm
2. Edit files in `python/` or `examples/`
3. Run Django server, pytest
4. Commit changes

**Both IDEs can be open simultaneously** - they share the same project files.

## IntelliJ IDEA Rust Plugin Features

Once installed, you get:

### Code Intelligence
- ✅ Syntax highlighting with semantic colors
- ✅ Code completion (structs, functions, macros)
- ✅ Error highlighting (borrow checker, type errors)
- ✅ Quick documentation (Cmd+Q)
- ✅ Go to definition (Cmd+B)
- ✅ Find usages (Cmd+F7)

### Refactoring
- ✅ Rename (Shift+F6)
- ✅ Extract function
- ✅ Inline variable
- ✅ Change signature

### Cargo Integration
- ✅ Run cargo commands from IDE
- ✅ Build project (Cmd+F9)
- ✅ Run tests with coverage
- ✅ Clippy integration

### Debugging
- ✅ Set breakpoints in Rust code
- ✅ Step through execution
- ✅ Inspect variables
- ✅ Evaluate expressions

### Macro Expansion
- ✅ Expand macros recursively
- ✅ See generated code from `#[derive(...)]`
- ✅ Useful for PyO3 macros like `#[pyfunction]`

## Troubleshooting

### "Rust plugin not found in PyCharm"

**Cause**: PyCharm Community Edition doesn't support Rust plugin well.

**Solution**: Use IntelliJ IDEA instead (you have it installed).

### "Rust plugin installed but not working"

1. **Check Rust toolchain**:
   ```bash
   rustc --version
   cargo --version
   ```

2. **Reinstall plugin**:
   ```
   Settings → Plugins → Installed → Rust → Uninstall → Restart
   Settings → Plugins → Marketplace → Rust → Install → Restart
   ```

3. **Invalidate caches**:
   ```
   File → Invalidate Caches → Invalidate and Restart
   ```

### "Python not recognized in IntelliJ IDEA"

**Solution**: Install Python plugin:
```
Settings → Plugins → Marketplace → "Python" → Install → Restart
```

### "Cargo commands not showing"

**Solution**:
1. Ensure `Cargo.toml` exists in project root
2. View → Tool Windows → Cargo (should show workspace)
3. Settings → Languages & Frameworks → Rust → Verify toolchain path

## Recommended Workflow

### Daily Development

**Use IntelliJ IDEA Ultimate for everything**:
1. Open IntelliJ IDEA
2. Work on both Rust and Python seamlessly
3. Run cargo commands via Cargo tool window
4. Run Django server via Run configurations
5. One IDE, one workflow

### Alternative: Project-Based

**Use the right tool for the task**:
- Rust refactoring? → IntelliJ IDEA
- Django template editing? → PyCharm
- Quick Python fix? → Either IDE
- Complex Rust debugging? → IntelliJ IDEA

## Performance Comparison

| Feature | IntelliJ IDEA | PyCharm |
|---------|---------------|---------|
| Rust support | ⭐⭐⭐⭐⭐ Excellent | ⭐ Limited |
| Python support | ⭐⭐⭐⭐⭐ Excellent (Ultimate) | ⭐⭐⭐⭐⭐ Excellent |
| Django support | ⭐⭐⭐⭐⭐ Excellent (Ultimate) | ⭐⭐⭐⭐⭐ Excellent (Pro) |
| Cargo integration | ⭐⭐⭐⭐⭐ Native | ⭐⭐ Via terminal |
| Memory usage | ~2GB | ~1.5GB |
| Indexing speed | Fast (optimized for Rust) | Fast (optimized for Python) |

## Conclusion

**For djust development, use IntelliJ IDEA Ultimate.** It provides the best experience for this Rust + Python hybrid project.

### Why?

1. **Best Rust support** - The Rust plugin works perfectly
2. **Full Python support** - Same capabilities as PyCharm Professional
3. **Unified workflow** - No IDE switching
4. **Better integration** - Sees the project as one cohesive whole
5. **You already have it installed!**

### Next Steps

1. Open IntelliJ IDEA
2. Install Rust plugin (Settings → Plugins → "Rust")
3. Open this project: `/Users/tip/Dropbox/online_projects/ai/djust`
4. Configure Python interpreter (Settings → Project → Python Interpreter)
5. Enable Django support
6. Start coding! 🚀

---

**Questions?** Check the Rust plugin documentation: https://plugins.jetbrains.com/plugin/8182-rust
